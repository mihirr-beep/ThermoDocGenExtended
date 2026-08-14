# -*- coding: utf-8 -*-
"""What the admin types into the report, kept between pages and between days.

WHY THIS EXISTS
---------------
Sections 1 and 3 of the report fill themselves, and sections 4 onward are
spliced from the approved datasheets. Section 2 does not: eleven fields have no
source anywhere in the database, measured on a real generated report -

    blank        CONDITION OF EUT ON RECEIPT, Size (L x W x H), Weight,
                 Operating Frequency, Power Rating, Measured EUT Current
    silently NA  DATE OF RECEIPT OF EUT, SOFTWARE AND FIRMWARE DETAILS,
                 EUT CONFIGURATION DURING TEST, EUT MONITORING PARAMETERS

plus four images with no source (block diagram, two EUT photos, the monitoring
screenshot) and the modes of operation.

The four "silently NA" ones are the reason this is worth building. Today
cleanup_instructions() writes NA over anything it cannot source, so the report
reads as though the lab deliberately marked those not-applicable when in fact
nobody was ever asked. A document that looks finished while hiding that its data
was never collected is the defect here, not the empty cell.

WHY A TABLE RATHER THAN THE SESSION
-----------------------------------
The wizard is several pages long and someone will start it on Tuesday and finish
it on Thursday. Session storage loses that, and losing a half-filled report is
how people go back to editing the .docx by hand.

Shaped after datasheet_records, which already works for exactly this problem:
ONE live row per request, form_json as the source of truth, overwritten on save.
No revision history - a report draft is scaffolding, and the report itself is
the artefact that gets frozen and reviewed.
"""
import json
import logging

from sqlalchemy import text

log = logging.getLogger(__name__)

TABLE = "report_draft"

# One row per request. test_request_id is UNIQUE rather than the primary key so
# the row keeps a stable id if the wizard ever needs to reference it.
_DDL = """
CREATE TABLE IF NOT EXISTS `report_draft` (
  `id`                INT NOT NULL AUTO_INCREMENT,
  `test_request_id`   INT NOT NULL,
  `form_json`         LONGTEXT NULL,
  `images_json`       TEXT NULL,
  `page_reached`      INT NOT NULL DEFAULT 1,
  `updated_by_user_id` INT NULL,
  `created_at`        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_report_draft_request` (`test_request_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""


def ensure_report_draft_table(app):
    """Create the draft table if it is not there. Idempotent, best-effort.

    Best-effort on purpose: a database that cannot take this must still serve
    the existing one-click report, which does not depend on it.
    """
    try:
        from models import db
    except Exception:  # pragma: no cover - models always importable in the app
        return False
    with app.app_context():
        try:
            db.session.execute(text(_DDL))
            db.session.commit()
            return True
        except Exception as exc:
            db.session.rollback()
            app.logger.error("report draft: could not create %s: %s", TABLE, exc)
            return False


def _table_ready(db):
    return bool(db.session.execute(text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema=DATABASE() AND table_name=:t"), {"t": TABLE}).scalar())


def load(request_id):
    """The saved draft as {"form": {...}, "images": {...}, "page": int}.

    Never raises and never returns None - a missing draft is an empty one, so
    every caller can treat "not started" and "started and empty" the same way.
    """
    from models import db
    empty = {"form": {}, "images": {}, "page": 1, "exists": False}
    try:
        if not _table_ready(db):
            return empty
        row = db.session.execute(text(
            "SELECT form_json, images_json, page_reached FROM `report_draft` "
            "WHERE test_request_id=:r"), {"r": int(request_id)}).first()
    except Exception as exc:  # noqa: BLE001
        log.warning("report draft load failed for request %s: %s", request_id, exc)
        return empty
    if row is None:
        return empty

    def _parse(raw):
        if not raw:
            return {}
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {}
        except (TypeError, ValueError):
            return {}

    return {"form": _parse(row[0]), "images": _parse(row[1]),
            "page": int(row[2] or 1), "exists": True}


def save(request_id, form=None, images=None, page=None, user_id=None):
    """Merge into the draft. Returns True when it was written.

    MERGE, not replace. The wizard posts one page at a time, and a page that
    replaced the whole document would wipe the pages before it the moment
    someone used Back. Only the keys present in this post are touched, so an
    absent key means "unchanged" rather than "cleared" - to clear a field the
    page sends it as an empty string, which is what an emptied input does.
    """
    from models import db
    try:
        if not _table_ready(db):
            return False
        cur = load(request_id)
        merged_form = dict(cur["form"])
        merged_form.update(form or {})
        merged_images = dict(cur["images"])
        merged_images.update(images or {})
        # never let the recorded page go backwards: it is "furthest reached",
        # which is what a resume needs, not "where they are right now"
        page_val = max(int(page or 1), int(cur["page"] or 1))
        db.session.execute(text(
            "INSERT INTO `report_draft` (test_request_id, form_json, images_json, "
            "page_reached, updated_by_user_id) VALUES (:r, :f, :i, :p, :u) "
            "ON DUPLICATE KEY UPDATE form_json=VALUES(form_json), "
            "images_json=VALUES(images_json), page_reached=VALUES(page_reached), "
            "updated_by_user_id=VALUES(updated_by_user_id)"),
            {"r": int(request_id),
             "f": json.dumps(merged_form, ensure_ascii=False),
             "i": json.dumps(merged_images, ensure_ascii=False),
             "p": page_val, "u": user_id})
        db.session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        log.error("report draft save failed for request %s: %s", request_id, exc)
        return False


def value(request_id_or_draft, key, default=""):
    """One field, taking either a request id or an already-loaded draft.

    The builder reads a dozen of these while writing one document; making it
    take a loaded draft keeps that to a single query instead of a dozen.
    """
    draft = (request_id_or_draft if isinstance(request_id_or_draft, dict)
             else load(request_id_or_draft))
    v = (draft.get("form") or {}).get(key)
    return default if v is None else v


def clear(request_id):
    """Discard a draft. Used when the admin abandons a report."""
    from models import db
    try:
        if not _table_ready(db):
            return False
        db.session.execute(text(
            "DELETE FROM `report_draft` WHERE test_request_id=:r"),
            {"r": int(request_id)})
        db.session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        log.error("report draft clear failed for request %s: %s", request_id, exc)
        return False
