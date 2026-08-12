# -*- coding: utf-8 -*-
"""Make "why did this fail?" a query instead of a reading exercise.

WHY
---
The lab already records everything an insight question needs EXCEPT the one
thing SQL can group on. A reviewer's finding is stored as free text in
datasheet_status_history.comment; a product's failure is stored nowhere at all
(datasheet_revision.result was blank in all 45 rows when this was written).

Prose cannot be grouped. "Have other products seen a similar failure pattern?"
against free text means reading every comment ever written with an LLM - slow,
expensive, and least reliable exactly at the scale where the question starts to
be worth asking. A code column turns that into GROUP BY.

TWO AXES, DELIBERATELY SEPARATE
-------------------------------
"The test failed" is ambiguous in this building and the ambiguity produces
confidently wrong answers, so the two meanings get two columns:

    datasheet_revision.failure_reason_code   the PRODUCT failed the standard.
                                             Emissions over the limit, EUT reset
                                             under burst. An engineering fact
                                             about the unit.

    datasheet_status_history.reason_code     the PAPERWORK was rejected in peer
                                             review. Calibration expired, photos
                                             missing. A quality-system finding
                                             about the record.

A product can pass the standard on a datasheet that gets rejected three times
for missing calibration dates, and the honest answer to "why did it take four
attempts" names both. Collapsing them into one "failed" column would make that
answer impossible to construct and easy to fake.

WHAT IS NOT HERE
----------------
No column stores WHICH measurement breached the limit. That is derivable -
datasheet_measurement holds value_num and the limit column per revision - and a
derived fact that disagrees with its source is worse than no fact at all.
"""
import logging

from sqlalchemy import text

log = logging.getLogger(__name__)

TAXONOMY_TABLE = "emc_reason_code"

# Grounded in what EMC peer review actually finds. Two families, never mixed:
# a cohort query that matched a product's emission failure against a reviewer's
# missing-signature finding would be noise wearing the costume of an insight.
_TEST_FAILURE = (
    ("RE_LIMIT_EXCEEDED", "Radiated emission above the limit line"),
    ("CE_LIMIT_EXCEEDED", "Conducted emission above the limit line"),
    ("EFT_RESET",         "EUT reset or restarted during fast transient burst"),
    ("SURGE_DAMAGE",      "Permanent damage or loss of function after surge"),
    ("RS_MALFUNCTION",    "Malfunction while exposed to the radiated RF field"),
    ("ESD_LOCKUP",        "Lock-up needing operator intervention after discharge"),
    ("DIPS_NO_RECOVER",   "Did not self-recover after a voltage dip"),
    ("HARMONIC_OVER",     "Harmonic current above the class limit"),
)

_REVIEW_REJECTION = (
    ("CAL_EXPIRED",       "Equipment calibration expired or due date missing"),
    ("MISSING_PHOTO",     "Test setup photographs missing"),
    ("INCOMPLETE_OBS",    "Observation grid incomplete"),
    ("WRONG_LIMIT",       "Wrong limit line or standard edition applied"),
    ("UNIT_ERROR",        "Value recorded in the wrong unit"),
    ("DEVIATION_UNDOC",   "Deviation from the standard not documented"),
    ("SETUP_MISMATCH",    "Setup described does not match the photographs"),
    ("MISSING_SIGNATURE", "Sign-off incomplete"),
)

# A table, not an ENUM or a Python dict: the NLP schema catalog reads the
# database to learn what exists, so a real table makes the vocabulary
# self-describing and lets the lab add a code without a deploy.
_TAXONOMY_DDL = """
CREATE TABLE IF NOT EXISTS `emc_reason_code` (
  `code`        VARCHAR(40)  NOT NULL,
  `family`      VARCHAR(20)  NOT NULL,
  `label`       VARCHAR(200) NOT NULL,
  `is_active`   TINYINT(1)   NOT NULL DEFAULT 1,
  PRIMARY KEY (`code`),
  KEY `idx_reason_family` (`family`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

# (table, column, definition). Nullable with no default everywhere: a row
# written before this existed is genuinely "unclassified", and 0 or '' would be
# a value that later reads as a real answer.
_COLUMNS = (
    # The live header carries the current attempt's code; _snapshot_revision
    # copies it into the frozen revision the same way it copies
    # met_performance_criteria, so each attempt keeps its own answer instead of
    # every attempt reporting whatever the last one said.
    ("datasheet", "failure_reason_code", "VARCHAR(40) NULL"),
    ("datasheet_revision", "failure_reason_code", "VARCHAR(40) NULL"),
    ("datasheet_status_history", "reason_code", "VARCHAR(40) NULL"),
    # The demo corpus has to look real to be useful for insight questions, which
    # is exactly what makes it dangerous sitting unlabelled beside accredited
    # test data. One indexed flag, one WHERE to remove it, and the branded
    # product names and DEMO- prefixed TCOs surface in every answer the chatbot
    # gives, so nobody can mistake it for a genuine result.
    ("iec_emc_requests", "is_synthetic", "TINYINT(1) NOT NULL DEFAULT 0"),
)

_INDEXES = (
    ("datasheet_status_history", "reason_code", "idx_dsh_reason"),
    ("datasheet_revision", "failure_reason_code", "idx_dsrev_reason"),
    ("iec_emc_requests", "is_synthetic", "idx_req_synthetic"),
)


def _table_exists(db, name):
    return bool(db.session.execute(text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema=DATABASE() AND table_name=:t"), {"t": name}).scalar())


def _column_exists(db, table, column):
    return bool(db.session.execute(text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema=DATABASE() AND table_name=:t AND column_name=:c"),
        {"t": table, "c": column}).scalar())


def _index_exists(db, table, name):
    return bool(db.session.execute(text(
        "SELECT COUNT(*) FROM information_schema.statistics "
        "WHERE table_schema=DATABASE() AND table_name=:t AND index_name=:i"),
        {"t": table, "i": name}).scalar())


def ensure_insight_schema(app):
    """Create the reason taxonomy and the columns that classify a failure.

    Idempotent and best-effort, like every other ensure_* at boot: a database
    that cannot take these changes must still serve datasheets, because nothing
    in the capture path depends on them. Returns a list of what it changed.
    """
    try:
        from models import db
    except Exception:  # pragma: no cover - models always importable in the app
        return []

    changed = []
    with app.app_context():
        try:
            db.session.execute(text(_TAXONOMY_DDL))
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            app.logger.warning("insight schema: taxonomy table skipped: %s", exc)
            return []

        # Seed, but never overwrite: is_active is the lab's to change, and a
        # relabelled code is theirs too. Only genuinely new codes are inserted.
        try:
            have = {r[0] for r in db.session.execute(
                text("SELECT code FROM `emc_reason_code`")).fetchall()}
            rows = ([(c, "test_failure", lbl) for c, lbl in _TEST_FAILURE] +
                    [(c, "review_rejection", lbl) for c, lbl in _REVIEW_REJECTION])
            new = [r for r in rows if r[0] not in have]
            for code, family, label in new:
                db.session.execute(text(
                    "INSERT INTO `emc_reason_code` (code, family, label) "
                    "VALUES (:c, :f, :l)"), {"c": code, "f": family, "l": label})
            if new:
                db.session.commit()
                changed.append("seeded %d reason code(s)" % len(new))
        except Exception as exc:
            db.session.rollback()
            app.logger.warning("insight schema: seeding skipped: %s", exc)

        for table, column, ddl in _COLUMNS:
            try:
                if not _table_exists(db, table) or _column_exists(db, table, column):
                    continue
                db.session.execute(text(
                    "ALTER TABLE `%s` ADD COLUMN `%s` %s" % (table, column, ddl)))
                db.session.commit()
                changed.append("%s.%s" % (table, column))
            except Exception as exc:
                db.session.rollback()
                if "duplicate" in str(exc).lower():
                    continue          # added concurrently (reloader race)
                app.logger.error("insight schema: could not add %s.%s: %s",
                                 table, column, exc)

        for table, column, index in _INDEXES:
            try:
                if (not _table_exists(db, table)
                        or not _column_exists(db, table, column)
                        or _index_exists(db, table, index)):
                    continue
                db.session.execute(text(
                    "CREATE INDEX `%s` ON `%s` (`%s`)" % (index, table, column)))
                db.session.commit()
                changed.append(index)
            except Exception as exc:
                db.session.rollback()
                if "duplicate" in str(exc).lower():
                    continue
                app.logger.error("insight schema: could not index %s.%s: %s",
                                 table, column, exc)

        if changed:
            app.logger.info("insight schema: %s", ", ".join(changed))
    return changed
