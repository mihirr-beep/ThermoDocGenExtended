"""Persistence for filled datasheet forms: drafts + submitted records + viewing.

Design (see datasheet_database_documentation.docx):
  * ``datasheet_records`` is the universal parent table. It carries the common
    metadata columns from the documentation (tco/job/eut/date/result/tester) as
    real, queryable columns, PLUS the COMPLETE filled form as ``form_json`` so
    nothing the engineer typed is ever lost and the datasheet can be rendered
    back exactly. Per-test parameters and observation grids live inside
    ``form_json`` (the current auto-generated form keys don't map 1:1 to the
    doc's per-test columns; JSON keeps the capture lossless and lets a future
    normalization expand from a faithful source).
  * ``status`` is 'Not Submitted' (Save as Draft) or 'Submitted' (Generate).
  * One record per planner entry (each planner entry == one assigned test), so
    a draft and its later submission are the same upserted row.

Kept self-contained: raw idempotent DDL (matches schema.py's style), no edits to
models.py or the main migration path.
"""
import hashlib
import json
import os
from datetime import datetime

from sqlalchemy import inspect, text

DRAFT = "Not Submitted"
SUBMITTED = "Submitted"

_CREATE = """
CREATE TABLE IF NOT EXISTS datasheet_records (
  id INT AUTO_INCREMENT PRIMARY KEY,
  planner_entry_id INT NULL,
  test_request_id INT NULL,
  test_code VARCHAR(20) NULL,
  tco_id VARCHAR(50) NULL,
  job_number VARCHAR(100) NULL,
  eut_name VARCHAR(255) NULL,
  eut_model_sku VARCHAR(100) NULL,
  eut_serial_number VARCHAR(100) NULL,
  test_date DATE NULL,
  result VARCHAR(30) NULL,
  tested_by_name VARCHAR(200) NULL,
  tested_by_user_id INT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'Not Submitted',
  form_json LONGTEXT NULL,
  images_json TEXT NULL,
  generated_file_path VARCHAR(500) NULL,
  created_by_user_id INT NULL,
  created_at DATETIME NULL,
  updated_at DATETIME NULL,
  UNIQUE KEY uq_ds_planner (planner_entry_id),
  KEY idx_ds_tco (tco_id),
  KEY idx_ds_status (status),
  KEY idx_ds_testcode (test_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


# Columns this feature writes that the documented (datasheet_schemas.sql) table
# does NOT have. Added additively so a pre-existing doc table is upgraded in
# place (never dropped) — mirrors schema.py's column-guard pattern.
_REQUIRED_COLS = {
    "test_code": "ADD COLUMN test_code VARCHAR(20) NULL",
    "status": "ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'Not Submitted'",
    "form_json": "ADD COLUMN form_json LONGTEXT NULL",
    "images_json": "ADD COLUMN images_json TEXT NULL",
    "generated_file_path": "ADD COLUMN generated_file_path VARCHAR(500) NULL",
}

# The documented (datasheet_schemas.sql) table marks these NOT NULL, but a
# *draft* legitimately has them empty (no result/date entered yet). Relax them
# to NULL so 'Save as Draft' can persist partial data. (result -> VARCHAR also
# frees its ENUM so any value round-trips.) Applied only when still NOT NULL.
_NULLABLE = {
    "tco_id": "MODIFY COLUMN tco_id VARCHAR(50) NULL",
    "job_number": "MODIFY COLUMN job_number VARCHAR(100) NULL",
    "eut_name": "MODIFY COLUMN eut_name VARCHAR(255) NULL",
    "eut_model_sku": "MODIFY COLUMN eut_model_sku VARCHAR(100) NULL",
    "eut_serial_number": "MODIFY COLUMN eut_serial_number VARCHAR(100) NULL",
    "test_date": "MODIFY COLUMN test_date DATE NULL",
    "result": "MODIFY COLUMN result VARCHAR(30) NULL",
    "tested_by_name": "MODIFY COLUMN tested_by_name VARCHAR(200) NULL",
    "created_by_user_id": "MODIFY COLUMN created_by_user_id INT NULL",
}


def ensure_datasheet_record_tables(app):
    """Ensure datasheet_records exists and has the columns + unique key this
    feature needs. Idempotent, additive, best-effort; never breaks boot."""
    try:
        from models import db
    except Exception:
        return
    with app.app_context():
        try:
            names = inspect(db.engine).get_table_names()
            if "datasheet_records" not in names:
                db.session.execute(text(_CREATE))
                db.session.commit()
                app.logger.info("datasheet_gen: created table datasheet_records")
        except Exception as exc:
            db.session.rollback()
            if "exist" not in str(exc).lower():
                app.logger.error("datasheet_gen: could not create datasheet_records: %s", exc)

        # additively add any missing columns (upgrades a pre-existing doc table)
        try:
            cols = inspect(db.engine).get_columns("datasheet_records")
            existing = {c["name"] for c in cols}
            nullable = {c["name"]: c.get("nullable", True) for c in cols}
        except Exception:
            return
        for name, clause in _REQUIRED_COLS.items():
            if name in existing:
                continue
            try:
                db.session.execute(text(f"ALTER TABLE datasheet_records {clause}"))
                db.session.commit()
                app.logger.info("datasheet_gen: added datasheet_records.%s", name)
            except Exception as exc:
                db.session.rollback()
                if "duplicate" not in str(exc).lower() and "exist" not in str(exc).lower():
                    app.logger.error("datasheet_gen: add %s failed: %s", name, exc)

        # relax NOT NULL on draft-relevant columns (only where still NOT NULL)
        for name, clause in _NULLABLE.items():
            if nullable.get(name, True):
                continue
            try:
                db.session.execute(text(f"ALTER TABLE datasheet_records {clause}"))
                db.session.commit()
                app.logger.info("datasheet_gen: relaxed NOT NULL on datasheet_records.%s", name)
            except Exception as exc:
                db.session.rollback()
                app.logger.warning("datasheet_gen: could not relax %s: %s", name, exc)

        # upsert needs a unique key on planner_entry_id (the doc table lacks it)
        try:
            idx = {i["name"] for i in inspect(db.engine).get_indexes("datasheet_records")}
            if "uq_ds_planner" not in idx:
                db.session.execute(text(
                    "CREATE UNIQUE INDEX uq_ds_planner ON datasheet_records (planner_entry_id)"))
                db.session.commit()
                app.logger.info("datasheet_gen: added unique index uq_ds_planner")
        except Exception as exc:
            db.session.rollback()
            if "duplicate" not in str(exc).lower() and "exist" not in str(exc).lower():
                app.logger.warning("datasheet_gen: could not add uq_ds_planner: %s", exc)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _ist_now():
    try:
        from app import get_ist_now
        return get_ist_now()
    except Exception:
        return datetime.now()


def _first(form_data, *keys):
    """First non-empty value across exact keys, then substring matches."""
    for k in keys:
        v = form_data.get(k)
        if isinstance(v, list):
            v = next((x for x in v if x not in (None, "")), "")
        if v not in (None, ""):
            return str(v).strip()
    for want in keys:
        for k, v in form_data.items():
            if want in k.lower():
                if isinstance(v, list):
                    v = next((x for x in v if x not in (None, "")), "")
                if v not in (None, ""):
                    return str(v).strip()
    return ""


def _parse_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _extract_common(form_data):
    return {
        "job_number": _first(form_data, "job_number"),
        "eut_name": _first(form_data, "eut_name"),
        "eut_model_sku": _first(form_data, "eut_model", "eut_model_sku_number", "eut_model_sku"),
        "eut_serial_number": _first(form_data, "eut_serial", "eut_serial_number"),
        "test_date": _parse_date(_first(form_data, "test_date")),
        "result": _first(form_data, "overall_result", "result"),
        "tested_by_name": _first(form_data, "tested_by_name", "tested_by"),
    }


# --------------------------------------------------------------------------
# read
# --------------------------------------------------------------------------

_SELECT_COLS = ("id, planner_entry_id, test_request_id, test_code, tco_id, job_number, "
                "eut_name, eut_model_sku, eut_serial_number, test_date, result, "
                "tested_by_name, status, form_json, images_json, generated_file_path, "
                "created_by_user_id, created_at, updated_at")


def _row_to_dict(row):
    if row is None:
        return None
    return dict(row._mapping)


def get_record_for_assignment(assignment_id):
    from models import db
    try:
        row = db.session.execute(
            text(f"SELECT {_SELECT_COLS} FROM datasheet_records WHERE planner_entry_id = :pid"),
            {"pid": assignment_id},
        ).first()
    except Exception:
        return None
    return _row_to_dict(row)


def get_record(record_id):
    from models import db
    try:
        row = db.session.execute(
            text(f"SELECT {_SELECT_COLS} FROM datasheet_records WHERE id = :rid"),
            {"rid": record_id},
        ).first()
    except Exception:
        return None
    return _row_to_dict(row)


def list_records(user):
    """Records visible to this user: admin -> all; lab_engineer -> their own
    assignments; others -> none. Newest first."""
    from models import db
    q = (f"SELECT r.{', r.'.join(_SELECT_COLS.split(', '))}, "
         "p.engineer_user_id AS engineer_user_id, p.test_name AS test_name "
         "FROM datasheet_records r "
         "LEFT JOIN planner_entries p ON p.id = r.planner_entry_id ")
    params = {}
    role = getattr(user, "role", None)
    if role == "admin":
        pass
    elif role == "lab_engineer":
        q += "WHERE (p.engineer_user_id = :uid OR r.created_by_user_id = :uid) "
        params["uid"] = user.id
    else:
        return []
    q += "ORDER BY r.updated_at DESC, r.id DESC"
    try:
        rows = db.session.execute(text(q), params).all()
    except Exception:
        return []
    return [dict(r._mapping) for r in rows]


def can_view(record, user):
    role = getattr(user, "role", None)
    if role == "admin":
        return True
    if role != "lab_engineer":
        return False
    if record.get("created_by_user_id") == user.id:
        return True
    from models import db, PlannerEntry
    pe = db.session.get(PlannerEntry, record.get("planner_entry_id")) if record.get("planner_entry_id") else None
    return not (pe and pe.engineer_user_id and pe.engineer_user_id != user.id)


# --------------------------------------------------------------------------
# write (upsert by planner_entry_id)
# --------------------------------------------------------------------------

def upsert_record(assignment, test_code, form_data, images, status,
                  generated_file_path=None, user=None, full_projection=None):
    """Insert or update the single datasheet record for this assignment.

    Draft images accumulate across saves; a submit merges in any prior draft
    images too (so the engineer needn't re-attach on every save). That merge is
    the only reason to read the row first, so it is skipped when no new image
    was posted - which is every autosave after the uploads are done, and the
    row carries a 3-6.5 KB form_json blob over a remote connection.

    After the record is committed the queryable tables are refreshed from it,
    in two tiers: the header alone (one statement) unless ``full_projection``
    asks for everything, which submits and explicit saves do. See projection.py.
    """
    from models import db

    # merge the stored images only when this save actually brought new ones;
    # otherwise images_json is left exactly as it is (see the UPDATE clause)
    posted = {k: v for k, v in (images or {}).items() if v}
    merged_images = {}
    if posted:
        merged_images.update(_stored_images(assignment.id))
        merged_images.update(posted)

    # MERGE onto what is already stored, rather than replacing it.
    #
    # The browser does not post the whole form. buildDraftFormData skips every
    # `el.disabled` input, and the datasheets disable whole sections as a matter
    # of course - the split-row day boxes, conditional blocks, anything greyed
    # out. Writing form_json wholesale therefore DELETED every disabled field on
    # each save, and the engineer saw those boxes empty on the next refresh with
    # nothing to explain it. The generic_form comment about a day "starting
    # empty" after the section count is lowered and raised is the same
    # mechanism, noticed in one place and general everywhere.
    #
    # Merging makes the rule: a save may change a field, and may clear one - an
    # empty box still posts, as "" - but can never delete a field it did not
    # mention. Costs one indexed read of the row about to be written.
    #
    # Arrays are replaced, not appended: a grid that loses a row posts the
    # shorter list and the shorter list wins, which is what deleting a row must
    # mean.
    previous = _stored_form(assignment.id)
    form_data = dict(previous, **(form_data or {}))

    common = _extract_common(form_data)
    uid = getattr(user, "id", None)
    now = _ist_now()
    # read off the assignment BEFORE the commit below: SQLAlchemy expires every
    # loaded object on commit, so the projection would otherwise re-SELECT it
    entry_fields = {
        "id": assignment.id,
        "tco_id": getattr(assignment, "tco_id", None),
        "test_request_id": getattr(assignment, "test_request_id", None),
        "engineer_user_id": getattr(assignment, "engineer_user_id", None),
        "peer_reviewer_user_id": getattr(assignment, "peer_reviewer_user_id", None),
        "test_person_name": getattr(assignment, "test_person_name", None),
        "status": getattr(assignment, "status", None),
    }
    # normalise to the documented result ENUM ('Pass'/'Fail'/'Incomplete');
    # unknown/blank -> NULL (the raw value is preserved in form_json regardless)
    result = {"PASS": "Pass", "FAIL": "Fail", "INCOMPLETE": "Incomplete"}.get(
        (common["result"] or "").strip().upper())
    params = {
        "planner_entry_id": assignment.id,
        "test_request_id": getattr(assignment, "test_request_id", None),
        "test_code": test_code,
        "tco_id": (getattr(assignment, "tco_id", None) or common.get("job_number") or "")[:50] or None,
        "job_number": common["job_number"][:100] or None,
        "eut_name": common["eut_name"][:255] or None,
        "eut_model_sku": common["eut_model_sku"][:100] or None,
        "eut_serial_number": common["eut_serial_number"][:100] or None,
        "test_date": common["test_date"],
        "result": result,
        "tested_by_name": common["tested_by_name"][:200] or None,
        "tested_by_user_id": uid,
        "status": status,
        "form_json": json.dumps(form_data, ensure_ascii=False, default=str),
        "images_json": json.dumps(merged_images, ensure_ascii=False),
        # a blank one must not wipe the path a previous save recorded, hence
        # the COALESCE below rather than a read-then-write here
        "generated_file_path": generated_file_path,
        "created_by_user_id": uid,
        "now": now,
    }
    sql = text("""
        INSERT INTO datasheet_records
          (planner_entry_id, test_request_id, test_code, tco_id, job_number, eut_name,
           eut_model_sku, eut_serial_number, test_date, result, tested_by_name,
           tested_by_user_id, status, form_json, images_json, generated_file_path,
           created_by_user_id, created_at, updated_at)
        VALUES
          (:planner_entry_id, :test_request_id, :test_code, :tco_id, :job_number, :eut_name,
           :eut_model_sku, :eut_serial_number, :test_date, :result, :tested_by_name,
           :tested_by_user_id, :status, :form_json, :images_json, :generated_file_path,
           :created_by_user_id, :now, :now)
        ON DUPLICATE KEY UPDATE
           test_code=VALUES(test_code), tco_id=VALUES(tco_id), job_number=VALUES(job_number),
           eut_name=VALUES(eut_name), eut_model_sku=VALUES(eut_model_sku),
           eut_serial_number=VALUES(eut_serial_number), test_date=VALUES(test_date),
           result=VALUES(result), tested_by_name=VALUES(tested_by_name),
           tested_by_user_id=VALUES(tested_by_user_id), status=VALUES(status),
           form_json=VALUES(form_json), """ + (
           "images_json=VALUES(images_json), " if posted else "") + """
           generated_file_path=COALESCE(VALUES(generated_file_path), generated_file_path),
           updated_at=VALUES(updated_at)
    """)
    db.session.execute(sql, params)
    db.session.commit()

    # Projection FIRST, history second. The other order looks harmless and is
    # not: on the very first save of a datasheet the `datasheet` row does not
    # exist yet, so the history row was written with datasheet_id NULL and any
    # query that joined through `datasheet` silently lost it - losing the first
    # save, which is usually the one somebody is looking for.
    _refresh_projection(entry_fields, params,
                        full=_full_tier(status, full_projection),
                        images_known=bool(posted))
    _append_draft_history(assignment.id, test_code, status, previous,
                          form_data, params.get("form_json"), user)
    return merged_images


def _changed_keys(before, after):
    """Which form keys this save actually altered, added or cleared."""
    out = []
    for k in sorted(set(before) | set(after)):
        if before.get(k) != after.get(k):
            out.append(k)
    return out


def _append_draft_history(entry_id, test_code, status, before, after,
                          form_json, user):
    """Record this save, if it changed anything, and never touch it again.

    Placed here because upsert_record already holds BOTH versions of the form -
    it reads the stored one to merge onto. So the history costs one INSERT and
    no extra read.

    Skipped when nothing changed: the autosave fires on a timer and cannot tell
    whether the engineer edited a box or just tabbed through, and without this
    check the table fills with identical rows.

    Best-effort in its own transaction, AFTER form_json is committed. The
    history must never be the reason a save the engineer was told succeeded did
    not: losing one audit row is a nuisance, losing their work is not.
    """
    from models import db
    changed = _changed_keys(before or {}, after or {})
    if before and not changed:
        return 0
    try:
        blob = form_json if isinstance(form_json, str) else json.dumps(after or {})
        db.session.execute(text("""
            INSERT INTO datasheet_draft_history
              (planner_entry_id, datasheet_id, revision_no, test_code, status,
               form_json, content_hash, changed_fields, changed_count,
               saved_by_user_id, saved_by_name, saved_at)
            SELECT :p, d.id, COALESCE(d.revision_no, 1), :tc, :st, :fj, :h, :cf, :cc,
                   :uid, :un, NOW()
              FROM (SELECT 1) x
              LEFT JOIN `datasheet` d ON d.planner_entry_id = :p
        """), {"p": entry_id, "tc": test_code, "st": status, "fj": blob,
               "h": hashlib.sha1((blob or "").encode("utf-8")).hexdigest(),
               # the full list can be long; keep it readable and bounded
               "cf": ", ".join(changed)[:60000] or None,
               "cc": len(changed),
               "uid": getattr(user, "id", None),
               "un": (getattr(user, "username", None) or "")[:200] or None})
        db.session.commit()
        return 1
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        try:
            from flask import current_app
            current_app.logger.warning(
                "draft history not recorded for entry %s: %s", entry_id, exc)
        except Exception:  # noqa: BLE001
            pass
        return 0


# The autosave used to project only the header, leaving the child tables holding
# the PREVIOUS content of the form. That is worse than leaving them empty: a DBA
# or a report reading datasheet_ce got last-save's coupling method with a fresh
# updated_at next to it and no way to tell.
#
# Measured cost of doing it properly, one CRF datasheet:
#
#     project_header   3 statements, 14 ms local
#     project (full)  24 statements, 21 ms local
#
# 7 ms locally; on the remote production database it is 21 extra round trips,
# so call it a second. The save is debounced 1.5 s after typing stops and runs
# async - nobody is waiting on it - and the projection is best-effort in its own
# transaction, so the cost is DB load, not user-visible latency. Correct tables
# are worth that.
#
# DATASHEET_CHEAP_AUTOSAVE=1 restores the header-only tier if that load ever
# becomes the problem; a submit projects fully either way.
def _full_tier(status, full_projection):
    if full_projection is not None:
        return bool(full_projection)
    if status == SUBMITTED:
        return True
    return os.environ.get("DATASHEET_CHEAP_AUTOSAVE", "") != "1"


def _stored_images(assignment_id):
    """{field: path} already recorded for this assignment. One narrow read."""
    from models import db
    try:
        row = db.session.execute(
            text("SELECT images_json FROM datasheet_records WHERE planner_entry_id = :pid"),
            {"pid": assignment_id}).first()
        return json.loads(row[0]) if row and row[0] else {}
    except Exception:  # noqa: BLE001 - a lost merge must not block the save
        return {}


def _stored_form(assignment_id):
    """The form as last saved, so a partial post can be merged onto it."""
    from models import db
    try:
        row = db.session.execute(
            text("SELECT form_json FROM datasheet_records WHERE planner_entry_id = :pid"),
            {"pid": assignment_id}).first()
        stored = json.loads(row[0]) if row and row[0] else {}
        return stored if isinstance(stored, dict) else {}
    except Exception:  # noqa: BLE001 - a lost merge must not block the save
        return {}


def _refresh_projection(entry_fields, params, full, images_known):
    """Reflect the just-saved record into the queryable tables.

    Best-effort and in its own transaction, after form_json is already
    committed: form_json is the source of truth, so a projection failure must
    never fail a save the engineer has been told succeeded. It can always be
    rebuilt with ``python -m datasheet_gen.projection``.

    Built from the parameters we just wrote rather than by reading the row back,
    so the header tier costs exactly one extra statement. The parent request is
    fetched only for a full projection - it supplies three columns that never
    change, and the autosave tier must not spend a round trip on them.
    """
    from models import db
    try:
        from . import projection as P
        entry = P.EntryFields(**entry_fields)
        request = None
        if full and entry_fields.get("test_request_id"):
            from models import EMCRequest
            request = db.session.get(EMCRequest, entry_fields["test_request_id"])
        record = dict(params)
        if full:
            P.project(record, entry, request, with_images=images_known)
        else:
            P.project_header(record, entry, request, with_images=images_known)
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        try:
            from flask import current_app
            current_app.logger.warning(
                "datasheet projection skipped for entry %s: %s",
                entry_fields.get("id"), exc)
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------
# Reading a draft back
# --------------------------------------------------------------------------
# form_from_record / images_from_record take an ALREADY-FETCHED row. The form
# routes need the record, its form and its images together, and fetching the
# row once instead of three times removes two round trips per page load - which
# matters because the row carries a 3-6.5 KB form_json blob and the database is
# remote. draft_form/draft_images remain for callers that only need one thing.

def form_from_record(record):
    """The saved form_data dict held by an already-fetched record, or {}."""
    if record and record.get("form_json"):
        try:
            return json.loads(record["form_json"])
        except (ValueError, TypeError):
            pass
    return {}


def images_from_record(record):
    """{field: path} of images held by an already-fetched record, or {}."""
    if record and record.get("images_json"):
        try:
            return json.loads(record["images_json"])
        except (ValueError, TypeError):
            pass
    return {}


def draft_images(assignment_id):
    """{field: path} of images previously saved for this assignment (for reuse)."""
    return images_from_record(get_record_for_assignment(assignment_id))


def draft_form(assignment_id):
    """The last-saved form_data dict for this assignment, or {}."""
    return form_from_record(get_record_for_assignment(assignment_id))


def delete_record_for_assignment(assignment_id):
    """Delete the saved draft/record for this assignment and its uploaded image
    files. Returns True if a row was removed, else False."""
    import os
    from models import db
    rec = get_record_for_assignment(assignment_id)
    if not rec:
        return False
    try:                                        # best-effort: remove image files
        for p in (json.loads(rec.get("images_json") or "{}") or {}).values():
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    except (ValueError, TypeError):
        pass
    db.session.execute(
        text("DELETE FROM datasheet_records WHERE planner_entry_id = :pid"),
        {"pid": assignment_id},
    )
    db.session.commit()
    # nothing links the projection back to datasheet_records, so a discarded
    # draft would otherwise leave its projected rows behind as orphans
    try:
        from . import projection as P
        P.delete_projection(assignment_id)
    except Exception:  # noqa: BLE001 - the record itself is already gone
        pass
    return True


# --------------------------------------------------------------------------
# render a saved record read-only (schema-aware for generic; prettified for CE)
# --------------------------------------------------------------------------

def _pretty(key):
    return key.replace("[]", "").replace("_", " ").strip().title()


def _scalar(v):
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if x not in (None, ""))
    return "" if v is None else str(v)


def _table_from_form(form, key_prefix, col_keys):
    """Assemble rows of [values] for parallel form arrays like 'pfx_c[]'."""
    cols = {c: (form.get(f"{key_prefix}{c}[]") or []) for c in col_keys}
    cols = {c: (v if isinstance(v, list) else [v]) for c, v in cols.items()}
    n = max((len(v) for v in cols.values()), default=0)
    rows = []
    for i in range(n):
        row = [_scalar(cols[c][i]) if i < len(cols[c]) else "" for c in col_keys]
        if any(row):
            rows.append(row)
    return rows


# CE (bespoke) view layout: (section title, [(label,key)...], [(table label, prefix, [(col label,col key)])])
_CE_TABLES = [
    ("EUT Modification Record", "mod_", [("State", "state"), ("Description", "description"),
                                         ("Fitted by", "fitted_by"), ("Date", "date")]),
    ("Line measurements", "line_", [("Frequency (MHz)", "qp_freq"), ("Q-peak", "qp"), ("Limit", "qp_limit"),
                                    ("Margin", "qp_margin"), ("Frequency (MHz)", "avg_freq"), ("Average", "avg"),
                                    ("Limit", "avg_limit"), ("Margin", "avg_margin")]),
    ("Neutral measurements", "neutral_", [("Frequency (MHz)", "qp_freq"), ("Q-peak", "qp"), ("Limit", "qp_limit"),
                                          ("Margin", "qp_margin"), ("Frequency (MHz)", "avg_freq"), ("Average", "avg"),
                                          ("Limit", "avg_limit"), ("Margin", "avg_margin")]),
    ("Test Equipment Used", "eq_", [("Equipment", "name"), ("Make", "make"), ("Model", "model"),
                                    ("Serial", "serial"), ("Calibration Due", "cal_due")]),
]


def record_images(record):
    """{key: path} of images saved on this record (parsed from images_json)."""
    try:
        return json.loads(record.get("images_json") or "{}") or {}
    except (ValueError, TypeError):
        return {}


def _img_box(key, code):
    """Document image-slot size in mm — mirrors generic_generator._box so the
    on-screen preview matches the exported .docx exactly."""
    try:
        from .generic_generator import _box
        return _box(key, code)
    except Exception:
        k = (key or "").lower()
        if "sign" in k:
            return (40, 20)
        if "img_fc" in k:
            return (140, 52)
        return (159.2, 95)


def _image_item(key, label, form, images, code):
    """An inline image block for one uploaded image, or None if not uploaded."""
    import os
    path = (images or {}).get(key)
    if not path or not os.path.exists(path):
        return None
    w, h = _img_box(key, code)
    caption = _scalar(form.get(key + "_caption")) or (label or _pretty(key))
    return {"kind": "image", "key": key, "label": label or _pretty(key),
            "caption": caption, "w": w, "h": h}


def _sections_ce(form, images):
    """Document-ordered blocks for the bespoke CE datasheet."""
    detail = []
    for k, v in form.items():
        if k in ("assignment_id", "tco_id") or k.endswith("[]") or k.endswith("_caption"):
            continue
        if k in (images or {}):
            continue
        sv = _scalar(v)
        if sv:
            detail.append({"kind": "field", "label": _pretty(k), "value": sv})
    sections = []
    if detail:
        sections.append({"title": "Details", "items": detail})
    for label, prefix, cols in _CE_TABLES:
        rows = _table_from_form(form, prefix, [c for _, c in cols])
        if rows:
            sections.append({"title": label, "items": [{
                "kind": "table", "label": "",
                "columns": [c for c, _ in cols], "rows": rows}]})
    imgs = [i for i in (_image_item(k, None, form, images, "CE") for k in (images or {})) if i]
    if imgs:
        sections.append({"title": "Images", "items": imgs})
    return sections


def _sections_generic(schema, form, images, code):
    """Document-ordered blocks for a schema-driven datasheet: fields, tables and
    inline images emitted in schema order, mirroring the exported document."""
    sections = []
    seen_imgs = set()
    for sec in schema.get("sections", []):
        if not sec.get("items"):
            continue
        items = []
        for it in sec["items"]:
            t = it.get("type")
            if t == "fields":
                for f in it.get("fields", []):
                    if f.get("input") == "image":
                        im = _image_item(f["key"], f.get("label"), form, images, code)
                        if im:
                            items.append(im); seen_imgs.add(f["key"])
                    else:
                        v = _scalar(form.get(f["key"]))
                        if v:
                            items.append({"kind": "field",
                                          "label": f.get("label") or _pretty(f["key"]), "value": v})
            elif t in ("field", "textarea"):
                if it.get("input") == "image":
                    im = _image_item(it["key"], it.get("label"), form, images, code)
                    if im:
                        items.append(im); seen_imgs.add(it["key"])
                else:
                    v = _scalar(form.get(it["key"]))
                    if v:
                        items.append({"kind": "field", "block": t == "textarea",
                                      "label": it.get("label") or _pretty(it["key"]), "value": v})
            elif t == "image":
                im = _image_item(it["key"], it.get("label"), form, images, code)
                if im:
                    items.append(im); seen_imgs.add(it["key"])
            elif t == "table":
                col_keys = [c["key"] for c in it.get("columns", [])]
                rows = _table_from_form(form, f"{it['key']}__", col_keys)
                if rows:
                    items.append({"kind": "table",
                                  "label": it.get("label") or _pretty(it["key"]),
                                  "columns": [c.get("label") or c["key"] for c in it["columns"]],
                                  "rows": rows})
        if items:
            sections.append({"title": sec.get("title", ""), "items": items})
    # any uploaded images not declared in the schema (e.g. RE meas_img_* plots)
    extra = [i for i in (_image_item(k, None, form, images, code)
                         for k in sorted(images or {}) if k not in seen_imgs) if i]
    if extra:
        sections.append({"title": "Measurement Plots", "items": extra})
    return sections


def record_view_model(record):
    """Read-only view of a saved record, laid out like the document it exports to:
    ordered sections whose items are fields / tables / inline images (in their
    exact document image-slot boxes)."""
    form = {}
    if record.get("form_json"):
        try:
            form = json.loads(record["form_json"])
        except (ValueError, TypeError):
            form = {}
    images = record_images(record)
    code = (record.get("test_code") or "").upper()
    name, form_id = (code or "Datasheet"), ""
    if code and code != "CE":
        try:
            from .registry import load_schema
            schema = load_schema(code)
            name = schema.get("name") or code
            form_id = schema.get("form") or ""
            sections = _sections_generic(schema, form, images, code)
        except Exception:
            name, form_id = code, ""
            sections = _sections_ce(form, images)
    else:
        name, form_id = "CE", "IEC-FRM-504"
        sections = _sections_ce(form, images)
    tco = record.get("tco_id") or ""
    subtitle = " · ".join([x for x in (form_id, ("TCO " + tco) if tco else "") if x])
    return {"title": "%s Test Data Sheet" % name, "subtitle": subtitle, "sections": sections}
