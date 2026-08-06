# -*- coding: utf-8 -*-
"""Project a saved datasheet form into the queryable tables.

``datasheet_records.form_json`` stays the source of truth and stays what the
datasheet form reads back (one row, one round trip). This module derives the
normalised copy from it so the data can actually be queried - which tests
failed, what ambient temperature CE ran at, which equipment is in use, who
rejected what and why.

DESIGN NOTES
------------
* **Column names ARE form keys.** The projection tables were generated from the
  11 live schemas, so for most fields the mapping is the identity - the column
  ``pulse_rise_time`` takes ``form["pulse_rise_time"]``. Only the handful of
  genuine renames need an alias (see ``ALIASES``), chiefly the bespoke CE form,
  which calls things ``eut_model`` / ``eut_serial`` / ``eut_voltage_frequency``.

* **The table is introspected, not hard-coded.** Each write asks the database
  which columns exist and fills only those. A schema change therefore degrades
  to "that column stays NULL" instead of raising, and the projection can be
  re-run after the tables are widened.

* **Two tiers.** ``project_header`` is one UPSERT and is cheap enough to run on
  every autosave. ``project`` does the whole thing - spec row, child rows,
  observations - and runs at save/submit, where the user is already waiting.
  On a remote database that difference is the whole point; see
  docs/Datasheet_Plan_FINAL.docx.

* **Idempotent.** Children are deleted and re-inserted for the datasheet being
  projected, so a backfill can be run repeatedly and a partial write self-heals
  on the next save.
"""
import json
import re
from datetime import date, datetime

from sqlalchemy import inspect, text

from . import form_extract as FX
from .registry import REGISTRY, normalize_code

# Form keys that differ from their column name. Everything else maps 1:1.
ALIASES = {
    "CE": {
        "eut_input_voltage_frequency": "eut_voltage_frequency",
        "eut_model_sku_number": "eut_model",
        "eut_serial_number": "eut_serial",
        "test_date": "tested_by_date",
        "tested_by": "tested_by_name",
    },
}
# Columns renamed because the form key was too generic to be a good column.
RENAMED = {"signoff_name": "name", "signoff_date": "date"}

_COLS_CACHE = {}


def _describe(db, table):
    """(all column names, the DATE ones) for a projection table.

    Introspected rather than hard-coded: the column list is generated from the
    11 form schemas, so duplicating it here would just be a second copy to keep
    in step. A column this module does not know about simply stays NULL.
    """
    if table not in _COLS_CACHE:
        try:
            cols = inspect(db.engine).get_columns(table)
            _COLS_CACHE[table] = (
                {c["name"] for c in cols},
                {c["name"] for c in cols
                 if c["type"].__class__.__name__.upper() == "DATE"})
        except Exception:
            _COLS_CACHE[table] = (set(), set())
    return _COLS_CACHE[table]


def _columns(db, table):
    return _describe(db, table)[0]


def _form_value(form, column, code, band_fallback=False):
    """The form value for a column, honouring renames and per-test aliases.

    ``band_fallback`` is for the header only. Several forms split a single
    value across two frequency bands (``ambient_temperature_col_1`` /
    ``_col_2``, or RE's ``_2`` / ``_3``) and never post the plain key, which
    would leave the header column NULL even though the datasheet records it.
    The per-test tables keep both bands as their own columns, so they must not
    do this - there the mapping stays strictly one key, one column.
    """
    for key in (ALIASES.get(code, {}).get(column),
                RENAMED.get(column),
                column):
        if key and key in form:
            return FX.value(form, key) or None
    if band_fallback:
        first, second = FX.band_values(form, column)
        return (first or second) or None
    return None


def _as_date(raw):
    raw = (raw or "").strip() if isinstance(raw, str) else raw
    if isinstance(raw, (date, datetime)):
        return raw
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y"):
        try:
            return datetime.strptime(str(raw).strip()[:11], fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# grid / measurement JSON
# --------------------------------------------------------------------------
# Observation matrices are posted but never declared in the schemas, so they are
# read back through form_extract (the same code the report generator uses).
# Everything is stored self-describing: {label, columns:[{key,label}], rows:[[]]}.

_OBS_COLUMN_HINT = {
    "obs_indirect": "indirect", "obs_direct": "direct", "obs_air": "air",
    "obs_ac": "ac", "obs_dc": "dc", "obs_signal": "signal",
    "obs_power": "power", "obs_dips": "dips", "obs_interruptions": "interrupt",
    "obs_rs": "", "obs_pfmf": "",
}

# Grids that are neither an observation matrix nor a schema-declared table:
# HARMONIC's two upload-driven tables, whose form key is not the column name.
_EXTRA_GRIDS = {
    ("HARMONIC", "harmonic_avgmax_rows"): ("avgmax_row", 10, "Average / Maximum harmonics"),
    ("HARMONIC", "harmonic_rows"): ("harmonic_row", 4, "Harmonic measurement data"),
}

_CE_MEAS_COLS = [("qp_freq", "Frequency (MHz)"), ("qp", "Q-peak"),
                 ("qp_limit", "Limit"), ("qp_margin", "Margin"),
                 ("avg_freq", "Frequency (MHz)"), ("avg", "Average"),
                 ("avg_limit", "Limit"), ("avg_margin", "Margin")]


def _ce_measurements(form, side):
    """CE's Line / Neutral measurement grids, one block per Test.

    The CE form repeats its whole measurement section: ``meas_index[]`` lists
    the live blocks and each one posts ``line<i>_qp_freq[]`` etc. Drafts saved
    before that split posted un-indexed ``line_qp_freq[]``, and those still
    render, so both are read here.
    """
    names = [k for k, _l in _CE_MEAS_COLS]
    order = form.get("meas_index[]") or []
    order = order if isinstance(order, list) else [order]
    order = [str(i).strip() for i in order if str(i).strip()]
    blocks = []
    for i in order or [""]:
        rows = FX._ce_arrays(form, "%s%s_" % (side, i), names)
        if rows:
            blocks.append({"label": FX.value(form, "meas_label_" + i) if i else "",
                           "rows": rows})
    return blocks


def _grid_payload(code, form, base, schema):
    """The JSON for one grid column, or None when the engineer entered nothing.

    Always self-describing - ``{label, columns:[{key,label}], rows:[[...]]}`` -
    so a reader (or the NLP layer) never has to know the form to know what the
    third cell of a row means.
    """
    # 1. CE's repeated Line / Neutral measurement grids
    if code == "CE" and base in ("line_measurements", "neutral_measurements"):
        side = base.split("_")[0]
        blocks = _ce_measurements(form, side)
        if not blocks:
            return None
        payload = {"label": "CE %s: Quasi-peak & Average" % side.title(),
                   "columns": [{"key": k, "label": l} for k, l in _CE_MEAS_COLS]}
        # one unnamed block stays a plain rows list; repeated Tests keep their names
        if len(blocks) == 1 and not blocks[0]["label"]:
            payload["rows"] = blocks[0]["rows"]
        else:
            payload["blocks"] = blocks
        return payload

    # 2. an observation matrix (posted by the form, not declared in the schema)
    if base in _OBS_COLUMN_HINT:
        hint = _OBS_COLUMN_HINT[base]
        for grid in FX.observation_grids(code, form) or []:
            if (grid.get("hint") or "") != hint or not grid.get("rows"):
                continue
            return _grid_json(grid["rows"], grid.get("cols") or [],
                              base.replace("obs_", "").replace("_", " ").title(),
                              grid.get("groups"))
        return None

    # 3. an upload-driven grid whose form key differs from the column name
    if (code, base) in _EXTRA_GRIDS:
        key, ncols, label = _EXTRA_GRIDS[(code, base)]
        rows = FX.table_rows(form, key, ncols)
        return _grid_json(rows, [], label) if rows else None

    # 4. a table declared in the schema - take its column labels from there
    cols = _schema_table_columns(schema, base)
    if cols is None:
        return None
    rows = FX.table_rows(form, base, len(cols))
    if not rows:
        return None
    return {"label": _schema_table_label(schema, base) or base,
            "columns": [{"key": c.get("key") or "c%d" % i,
                         "label": c.get("label") or c.get("key") or ""}
                        for i, c in enumerate(cols)],
            "rows": rows}


def _grid_json(rows, col_labels, label, groups=None):
    width = max(len(r) for r in rows)
    out = {"label": label,
           "columns": [{"key": "c%d" % i,
                        "label": col_labels[i] if i < len(col_labels) else ""}
                       for i in range(width)],
           "rows": [[str(c) for c in r] for r in rows]}
    if groups and any(groups):
        out["groups"] = list(groups)
    return out


def _schema_table_columns(schema, key):
    for sec in (schema or {}).get("sections", []):
        for it in sec.get("items", []):
            if it.get("type") == "table" and it.get("key") == key:
                return it.get("columns") or []
    return None


def _schema_table_label(schema, key):
    for sec in (schema or {}).get("sections", []):
        for it in sec.get("items", []):
            if it.get("type") == "table" and it.get("key") == key:
                return it.get("label")
    return None


_TRAILING_INDEX = re.compile(r"^(.*?)(_\d+)$")


def _caption_of(form, key):
    """A slot's caption. Generic forms post ``<key>_caption``; the CE form posts
    ``plot_line_caption_2`` - the index stays last, so it cannot just be
    appended."""
    cap = FX.value(form, key + "_caption")
    if cap:
        return cap
    m = _TRAILING_INDEX.match(key)
    return FX.value(form, "%s_caption%s" % (m.group(1), m.group(2))) if m else ""


def _images_payload(form, images):
    """{slot: {path, caption, width_mm, height_mm, order}} for every image.

    One JSON column rather than per-slot columns: images are render-only, the
    slot count varies from 2 to 11 per test, captions are user-editable, and RE
    and CE both let the engineer add further slots while filling the form -
    so the set of slots is data, not schema.
    """
    out = {}
    for i, (key, path) in enumerate(sorted((images or {}).items()), 1):
        if not path:
            continue
        entry = {"path": path, "order": i}
        cap = _caption_of(form, key)
        if cap:
            entry["caption"] = cap
        # the image editor posts the Word shape size in cm; store mm like the
        # generator does (generic_service._img_boxes)
        for src, dst in (("__wcm", "width_mm"), ("__hcm", "height_mm")):
            raw = FX.value(form, key + src)
            if raw:
                try:
                    entry[dst] = round(float(raw) * 10.0, 1)
                except ValueError:
                    pass
        out[key] = entry
    return out


# --------------------------------------------------------------------------
# header
# --------------------------------------------------------------------------

def _identity(entry, request):
    """Denormalised identity columns, so most questions need no join."""
    def _u(uid):
        if not uid:
            return None
        try:
            from models import db, User
            u = db.session.get(User, uid)
            return u.username if u else None
        except Exception:
            return None

    return {
        "tco_id": getattr(entry, "tco_id", None),
        "job_number": getattr(request, "job_number", None),
        "product_name": getattr(request, "product_name", None),
        "eut_class": getattr(request, "class_type", None),
        "engineer_name": (_u(getattr(entry, "engineer_user_id", None))
                          or getattr(entry, "test_person_name", None)),
        "peer_reviewer_name": _u(getattr(entry, "peer_reviewer_user_id", None)),
    }


# planner_entries.status is the workflow truth; datasheet.status is DERIVED from
# it plus the record status. Never written independently - see the plan, §3.
_STATUS_FROM_ENTRY = {
    "peer review": "Peer Review",
    "datasheet_uploaded": "Approved",
    "report_uploaded": "Approved",
    "completed": "Approved",
}


def derive_status(entry, record):
    entry_status = str(getattr(entry, "status", "") or "").strip().lower()
    mapped = _STATUS_FROM_ENTRY.get(entry_status)
    if mapped:
        return mapped
    if (record or {}).get("status") == "Submitted":
        return "Peer Review"
    return "Draft"


def _header_values(db, record, entry, request, form, images):
    code = normalize_code(record.get("test_code") or "")
    cols, date_cols = _describe(db, "datasheet")
    vals = {
        "planner_entry_id": record.get("planner_entry_id"),
        "test_request_id": record.get("test_request_id"),
        "test_code": code,
        "status": derive_status(entry, record),
        "created_by_user_id": record.get("created_by_user_id"),
    }
    vals.update(_identity(entry, request))

    reserved = set(vals) | {"id", "revision_no", "submitted_at", "decided_at",
                            "reviewer_user_id", "created_at", "updated_at",
                            "images_json", "result"}
    # every column is written, including the ones the form left blank: this is
    # an UPSERT, so a field the engineer has since cleared must go back to NULL
    # rather than keep the value from the previous save
    for column in cols - reserved:
        v = _form_value(form, column, code, band_fallback=True)
        vals[column] = _as_date(v) if (v and column in date_cols) else v

    if "result" in cols:
        vals["result"] = (FX.value(form, "overall_result")
                          or FX.value(form, "result")
                          or FX.value(form, "met_performance_criteria") or None)
    if "images_json" in cols:
        payload = _images_payload(form, images)
        vals["images_json"] = json.dumps(payload, ensure_ascii=False) if payload else None
    if "reviewer_user_id" in cols:
        vals["reviewer_user_id"] = getattr(entry, "peer_reviewer_user_id", None)
    return {k: v for k, v in vals.items() if k in cols}


def _upsert(db, table, values, key):
    """INSERT ... ON DUPLICATE KEY UPDATE for one row. One round trip."""
    cols = list(values)
    now_cols = [c for c in ("created_at", "updated_at") if c in _columns(db, table)]
    placeholders = ", ".join(":" + c for c in cols)
    sets = ", ".join("`%s`=VALUES(`%s`)" % (c, c) for c in cols if c != key)
    extra_ins = "".join(", `%s`" % c for c in now_cols)
    extra_val = "".join(", NOW()" for _c in now_cols)
    extra_upd = (", `updated_at`=NOW()" if "updated_at" in now_cols else "")
    sql = ("INSERT INTO `%s` (%s%s) VALUES (%s%s) "
           "ON DUPLICATE KEY UPDATE %s%s"
           % (table, ", ".join("`%s`" % c for c in cols), extra_ins,
              placeholders, extra_val, sets or "`%s`=`%s`" % (key, key), extra_upd))
    db.session.execute(text(sql), values)


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def project_header(record, entry, request=None):
    """Cheap tier: upsert ONLY the header row. One round trip.

    Safe to call on every autosave - it carries what people actually query
    (who, which test, which job, status, result, dates) without touching the
    child rows, which only become interesting once a datasheet is submitted.
    Returns the datasheet id, or None.
    """
    from models import db
    if not record or not record.get("planner_entry_id"):
        return None
    form = _parse(record.get("form_json"))
    images = _parse(record.get("images_json"))
    values = _header_values(db, record, entry, request, form, images)
    if not values.get("planner_entry_id"):
        return None
    _upsert(db, "datasheet", values, "planner_entry_id")
    db.session.commit()
    return _datasheet_id(db, record["planner_entry_id"])


def project(record, entry, request=None):
    """Full projection: header + per-test spec + all child rows.

    Runs at save/submit and in the backfill, not on autosave. Idempotent.
    Returns {"datasheet_id": int, "rows": {table: n}} or None.
    """
    from models import db
    if not record or not record.get("planner_entry_id"):
        return None
    code = normalize_code(record.get("test_code") or "")
    if code not in REGISTRY:
        return None
    form = _parse(record.get("form_json"))
    images = _parse(record.get("images_json"))

    values = _header_values(db, record, entry, request, form, images)
    _upsert(db, "datasheet", values, "planner_entry_id")
    # read back inside the same transaction - one commit for the whole
    # projection, so a failure half way through leaves nothing behind
    did = _datasheet_id(db, record["planner_entry_id"])
    if did is None:
        db.session.rollback()
        return None

    schema = _schema(code)
    written = {"spec": _project_spec(db, did, code, form, schema)}
    written.update(_project_children(db, did, code, form, schema))
    db.session.commit()
    return {"datasheet_id": did, "rows": written}


def _project_spec(db, did, code, form, schema):
    """The per-test table: its unique scalar columns + its grid JSON columns."""
    table = "datasheet_" + code.lower()
    cols, date_cols = _describe(db, table)
    if not cols:
        return 0
    vals = {"datasheet_id": did}
    for column in cols - {"datasheet_id"}:
        if column.endswith("_json"):
            payload = _grid_payload(code, form, column[:-5], schema)
            vals[column] = json.dumps(payload, ensure_ascii=False) if payload else None
        else:
            v = _form_value(form, column, code)
            vals[column] = _as_date(v) if (v and column in date_cols) else v
    _upsert(db, table, vals, "datasheet_id")
    return 1


_CHILD_SPECS = (
    ("datasheet_modification", "eut_modification_rec_rows",
     ("mod_state", "description", "fitted_by", "fitted_date"),
     ("mod_", ("state", "description", "fitted_by", "date"))),
    ("datasheet_equipment", "test_equipment_used_rows",
     ("equipment_name", "make", "model_no", "serial_no", "calibration_due"),
     ("eq_", ("name", "make", "model", "serial", "cal_due"))),
    ("datasheet_software", "software_used_rows",
     ("software_name", "software_version"),
     ("sw_", ("name", "version"))),
)


def _project_children(db, did, code, form, schema):
    """The three shared child tables, plus observations and their legend."""
    out = {}
    for table, key, columns, (ce_prefix, ce_names) in _CHILD_SPECS:
        if not _columns(db, table):
            continue
        rows = (FX._ce_arrays(form, ce_prefix, list(ce_names)) if code == "CE"
                else FX.table_rows(form, key, len(columns)))
        db.session.execute(text("DELETE FROM `%s` WHERE datasheet_id=:d" % table),
                           {"d": did})
        n = 0
        payload = []
        for i, row in enumerate(rows, 1):
            values = {"datasheet_id": did, "row_no": i}
            for ci, col in enumerate(columns):
                values[col] = row[ci] if ci < len(row) else None
            payload.append(values)
            n += 1
        if payload:
            cols = ["datasheet_id", "row_no"] + list(columns)
            db.session.execute(
                text("INSERT INTO `%s` (%s) VALUES (%s)"
                     % (table, ", ".join("`%s`" % c for c in cols),
                        ", ".join(":" + c for c in cols))),
                payload)                                    # one round trip
        out[table] = n

    out["datasheet_observation"] = _project_observations(db, did, code, form)
    out["datasheet_observation_legend"] = _project_legend(db, did, code, form)
    return out


def _project_observations(db, did, code, form):
    """Flatten every observation grid to one row per measured cell.

    The same numbers are already in the ``obs_*_json`` columns, but in eleven
    different shapes. Flattened to (grid, row_label, col_label, value) they
    answer "every observation that was not criterion A, across all tests" in
    one query - which is the whole reason for normalising in the first place.

    ``label_cols`` says how many leading cells name the row rather than measure
    anything: for ESD that is the S.No and the test point, for RS the band, the
    level and the dwell time. They are joined into ``row_label`` and only the
    remaining cells become value rows.
    """
    if not _columns(db, "datasheet_observation"):
        return 0
    db.session.execute(text("DELETE FROM datasheet_observation WHERE datasheet_id=:d"),
                       {"d": did})
    payload = []
    for grid in FX.observation_grids(code, form) or []:
        hint = grid.get("hint") or code.lower()
        cols = grid.get("cols") or []
        nlabel = max(1, int(grid.get("label_cols") or 1))
        groups = grid.get("groups") or []
        for ri, row in enumerate(grid.get("rows") or [], 1):
            if not row:
                continue
            parts = [str(c).strip() for c in row[:nlabel] if str(c).strip()]
            if ri - 1 < len(groups) and groups[ri - 1]:
                parts.insert(0, str(groups[ri - 1]).strip())
            label = " / ".join(parts)
            for ci, value in enumerate(row[nlabel:], nlabel):
                value = str(value or "").strip()
                if not value:
                    continue
                payload.append({
                    "d": did, "tc": code, "g": hint, "rn": ri, "rl": label[:200],
                    "ck": "c%d" % ci,
                    "cl": (cols[ci] if ci < len(cols) else "")[:120],
                    "v": value[:20]})
    if payload:
        db.session.execute(text(
            "INSERT INTO datasheet_observation (datasheet_id, test_code, grid_key, "
            "row_no, row_label, col_key, col_label, value) "
            "VALUES (:d, :tc, :g, :rn, :rl, :ck, :cl, :v)"), payload)
    return len(payload)


def _project_legend(db, did, code, form):
    """What each observation code (A, B2, C1 ...) means on THIS datasheet."""
    if not _columns(db, "datasheet_observation_legend"):
        return 0
    db.session.execute(
        text("DELETE FROM datasheet_observation_legend WHERE datasheet_id=:d"),
        {"d": did})
    payload = []
    for i, (obs_code, desc) in enumerate(FX.observation_legend(code, form) or []):
        payload.append({"d": did, "s": "obs_legend", "c": obs_code[:20],
                        "x": desc, "o": i})
    if payload:
        db.session.execute(text(
            "INSERT INTO datasheet_observation_legend "
            "(datasheet_id, grid_scope, code, description, sort_order) "
            "VALUES (:d, :s, :c, :x, :o)"), payload)
    return len(payload)


def delete_projection(planner_entry_id):
    """Remove a datasheet's projected rows. The FK cascade clears the children.

    Called when a draft is discarded - otherwise the projected rows would
    survive as orphans, since nothing links datasheet_records to these tables.
    """
    from models import db
    try:
        db.session.execute(
            text("DELETE FROM `datasheet` WHERE planner_entry_id=:p"),
            {"p": planner_entry_id})
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _parse(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw) or {}
    except (TypeError, ValueError):
        return {}


def _schema(code):
    if code == "CE":
        return None
    try:
        from .registry import load_schema
        return load_schema(code)
    except Exception:
        return None


def _datasheet_id(db, planner_entry_id):
    row = db.session.execute(
        text("SELECT id FROM `datasheet` WHERE planner_entry_id=:p"),
        {"p": planner_entry_id}).first()
    return row[0] if row else None


# --------------------------------------------------------------------------
# backfill CLI:  python -m datasheet_gen.projection [--all]
# --------------------------------------------------------------------------

def backfill(app, only_entry=None, verbose=True):
    """(Re)project every saved datasheet. Idempotent and re-runnable."""
    from models import db, PlannerEntry, EMCRequest
    from . import records as R

    done, failed = 0, []
    with app.app_context():
        q = "SELECT planner_entry_id FROM datasheet_records WHERE planner_entry_id IS NOT NULL"
        params = {}
        if only_entry:
            q += " AND planner_entry_id = :p"
            params["p"] = only_entry
        ids = [r[0] for r in db.session.execute(text(q + " ORDER BY planner_entry_id"),
                                                params).all()]
        for pid in ids:
            rec = R.get_record_for_assignment(pid)
            entry = db.session.get(PlannerEntry, pid)
            req = (db.session.get(EMCRequest, entry.test_request_id)
                   if entry is not None and entry.test_request_id else None)
            try:
                res = project(rec, entry, req)
                done += 1
                if verbose:
                    n = sum((res or {}).get("rows", {}).values())
                    print("  entry %-4s %-15s -> datasheet id %-4s (%d child rows)"
                          % (pid, rec.get("test_code"),
                             (res or {}).get("datasheet_id"), n))
            except Exception as exc:  # noqa: BLE001 - report and continue
                db.session.rollback()
                failed.append((pid, str(exc)[:120]))
                if verbose:
                    print("  entry %-4s FAILED: %s" % (pid, str(exc)[:100]))
    if verbose:
        print("\nprojected %d datasheet(s), %d failed" % (done, len(failed)))
    return done, failed


def _main():  # pragma: no cover - CLI
    import argparse
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap = argparse.ArgumentParser(description="Backfill the datasheet projection")
    ap.add_argument("--entry", type=int, help="only this planner entry id")
    args = ap.parse_args()

    from app import create_app
    app = create_app()
    backfill(app, only_entry=args.entry)


if __name__ == "__main__":  # pragma: no cover
    _main()
