# -*- coding: utf-8 -*-
"""Find every column in the database that holds a CLASSIFICATION.

A classification is a column whose values come from a definite, finite set -
'Draft' / 'Approved' / 'Rejected', 'Yes' / 'No', 'EOU' / 'Non EOU'. They matter
more than their size suggests: they are what users filter on, and a filter
against a value that is not in the set returns zero rows and looks exactly like
a real absence. The calibration question in this lab did precisely that -
`status = 'Out of Calibration'` matched nothing because the classification
lives in `calibration_status_col`, and the honest-looking answer was "none".

Why this is not just ENUM_VALUES from the catalog: build_catalog only harvests
values for columns whose NAME looks enum-ish (status, code, type, family...).
A classification called `result`, `pass_fail` or `eou_status` is invisible to
it, which means invisible to the model. This scan is name-blind - it decides
from the DATA - so it finds those, and reports which are missing from the
catalog.

    python tools_class_columns.py                # report
    python tools_class_columns.py --missing      # only what the catalog lacks
    python tools_class_columns.py --database DB  # another database

Read-only. Nothing here writes.
"""
import os
import sys

MAX_DISTINCT = 25         # above this it is data, not a category
MAX_VALUE_CHARS = 80      # longer than this is prose, whatever its cardinality
THIN_ROWS = 15            # below this the values are definite but the evidence is not
SAMPLE_VALUES = 12        # values printed per column

# THE DISCRIMINATOR. A classification is REUSED - many rows share one value. An
# identifier is not, and in a small table the two are indistinguishable by
# cardinality alone: content_hash has 16 distinct values over 16 rows and looks
# every bit as categorical as a status column until you notice nothing repeats.
# Require each class to average this many rows before calling it a category.
MIN_REPEAT = 3

# Types that can carry a class. Dates, decimals and blobs cannot; JSON is
# handled separately because its classes live inside keys, not in the column.
TEXTISH = ("char", "varchar", "tinytext", "text", "enum", "set")
INTISH = ("tinyint", "smallint", "int", "bit", "bool", "boolean")

# Names that are prose even when a small table makes them look categorical.
# Demoted, never dropped - the point of this scan is to miss nothing.
PROSE_NAMES = ("remark", "comment", "note", "description", "detail", "summary",
               "observation", "justification", "reason_text", "text", "message",
               "address", "email", "phone", "name", "title", "path", "url",
               "filename", "file_name", "json")


def _is_ordinal(col, dtype):
    """Position, not category. `row_no` 1..6 is an index that happens to repeat."""
    if dtype not in INTISH:
        return False
    low = col.lower()
    return low.endswith(("_no", "_order", "_index", "_seq", "_num", "_number",
                         "_count", "_qty", "_sort")) or low in ("no", "seq", "sort_order")


def _looks_dateish(values):
    """Dates kept in a varchar. Categorical-looking, never a category."""
    seen = [str(v) for v in values if v not in (None, "")]
    if not seen:
        return False
    hits = sum(1 for v in seen
               if len(v) >= 8 and v[:4].isdigit() and not v[:5].isdigit()
               and v[4] in "-/")
    return hits > len(seen) / 2


def _looks_hashish(values, ctype):
    """Fixed-width hex, or any wide char(N): a digest, not a class."""
    if "char(" in (ctype or "").lower():
        try:
            width = int((ctype.split("(")[1]).split(")")[0])
        except (IndexError, ValueError):
            width = 0
        if width >= 32:
            return True
    seen = [str(v) for v in values if v not in (None, "")]
    if not seen:
        return False
    hexish = sum(1 for v in seen
                 if len(v) >= 16 and all(c in "0123456789abcdefABCDEF" for c in v))
    return hexish > len(seen) / 2


# Only people, dates and prose. Measurements are excluded by the numeric test
# below instead of by name: blocklisting "power" also kills `powerSignal`, whose
# values are Power / Signal and which is exactly the kind of class we want.
_JSON_NOT_CLASS = ("name", "user", "_by", "date", "_at", "note", "comment",
                   "email", "phone", "make", "model", "serial", "remark", "desc")


def _json_class_like(key, values):
    """A JSON key whose values are categories, not measurements or names.

    The catalog samples values for all 16 JSON columns, and most of what it
    finds is data: acVoltageRange holds '100-240', ratedPower holds '120'. Those
    have few distinct values in a small database and look categorical for
    exactly the wrong reason. Keep only keys whose values are words.
    """
    low = key.lower()
    if any(bad in low for bad in _JSON_NOT_CLASS):
        return False
    vals = [str(v).strip() for v in values if str(v).strip()]
    if not 2 <= len(vals) <= 8:
        return False
    if any(len(v) > 30 for v in vals):
        return False
    numeric = sum(1 for v in vals
                  if v.replace(".", "").replace("-", "").replace(" ", "").isdigit())
    return numeric == 0


def _args():
    db = None
    argv = sys.argv[1:]
    if "--database" in argv:
        db = argv[argv.index("--database") + 1]
    return db, ("--missing" in argv)


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    target_db, only_missing = _args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pymysql
    import mysql_config                                  # loads .env
    cfg = mysql_config.config["default"]
    dbname = target_db or cfg.MYSQL_DATABASE

    conn = pymysql.connect(host=cfg.MYSQL_HOST, port=int(cfg.MYSQL_PORT),
                           user=cfg.MYSQL_USER, password=cfg.MYSQL_PASSWORD,
                           database=dbname, charset="utf8mb4", autocommit=True)
    cur = conn.cursor()

    # What the model can already see, so the report can say what it cannot.
    try:
        from nlp_search import schema_catalog as sc
        known = set(sc.ENUM_VALUES)
        catalog_tables = set(sc.ALLOWED_TABLES)
        json_keys = dict(getattr(sc, "JSON_KEYS", {}) or {})
    except Exception:                                    # noqa: BLE001
        known, catalog_tables, json_keys = set(), set(), {}

    # Identifiers and references are not classes. Exclude both up front.
    cur.execute(
        "SELECT DISTINCT k.table_name, k.column_name FROM "
        "information_schema.key_column_usage k WHERE k.table_schema=%s AND "
        "(k.referenced_table_name IS NOT NULL OR k.constraint_name='PRIMARY')",
        (dbname,))
    skip_cols = {(t, c) for t, c in cur.fetchall()}
    cur.execute(
        "SELECT DISTINCT table_name, column_name FROM information_schema.statistics "
        "WHERE table_schema=%s AND non_unique=0", (dbname,))
    skip_cols |= {(t, c) for t, c in cur.fetchall()}

    cur.execute(
        "SELECT table_name, column_name, data_type, column_type, is_nullable "
        "FROM information_schema.columns WHERE table_schema=%s "
        "ORDER BY table_name, ordinal_position", (dbname,))
    columns = cur.fetchall()

    declared, strong, boolean, thin, constant, vocab = [], [], [], [], [], []

    for table, col, dtype, ctype, nullable in columns:
        dtype = (dtype or "").lower()
        if (table, col) in skip_cols:
            continue
        if col.lower().endswith("_id") or col.lower() == "id":
            continue
        if dtype not in TEXTISH and dtype not in INTISH:
            continue

        q = ("SELECT COUNT(*), COUNT(`%s`), COUNT(DISTINCT `%s`)" % (col, col))
        if dtype in TEXTISH:
            q += ", MAX(CHAR_LENGTH(`%s`))" % col
        else:
            q += ", 0"
        try:
            cur.execute(q + " FROM `%s`" % table)
            total, non_null, distinct, longest = cur.fetchone()
        except Exception:                                # noqa: BLE001
            continue
        if not non_null or not distinct:
            continue
        if distinct > MAX_DISTINCT:
            continue
        if dtype in TEXTISH and (longest or 0) > MAX_VALUE_CHARS:
            continue
        if dtype in INTISH and distinct > 12:
            continue

        try:
            cur.execute("SELECT `%s`, COUNT(*) FROM `%s` WHERE `%s` IS NOT NULL "
                        "GROUP BY `%s` ORDER BY COUNT(*) DESC, `%s` LIMIT %d"
                        % (col, table, col, col, col, SAMPLE_VALUES + 1))
            pairs = cur.fetchall()
        except Exception:                                # noqa: BLE001
            continue

        rec = {"table": table, "col": col, "type": ctype, "distinct": distinct,
               "non_null": non_null, "total": total, "pairs": pairs,
               "in_catalog": ("%s.%s" % (table, col)) in known,
               "in_catalog_table": table in catalog_tables,
               "prose": any(p in col.lower() for p in PROSE_NAMES)}

        vals = [v for v, _n in pairs]
        if dtype in ("enum", "set"):
            declared.append(rec)                          # the schema already said so
        elif _is_ordinal(col, dtype) or _looks_dateish(vals) or _looks_hashish(vals, ctype):
            continue                                      # position, date, digest
        elif distinct == 1:
            constant.append(rec)
        elif non_null < THIN_ROWS:
            thin.append(rec)
        elif distinct * MIN_REPEAT > non_null:
            # Values barely repeat, so this is a list of things - equipment
            # names, serial numbers, standards - not a set of categories. Kept
            # separate rather than dropped: it is still a closed vocabulary a
            # filter can miss, just not a classification.
            vocab.append(rec)
        elif distinct == 2:
            boolean.append(rec)
        else:
            strong.append(rec)

    def show(title, recs, explain):
        recs = [r for r in recs if not (only_missing and r["in_catalog"])]
        print("\n" + "=" * 78)
        print("%s  (%d)" % (title, len(recs)))
        print("=" * 78)
        print(explain)
        if not recs:
            print("  (none)")
            return
        for r in sorted(recs, key=lambda x: (x["table"], x["col"])):
            flags = []
            if not r["in_catalog"]:
                flags.append("NOT IN CATALOG")
            if not r["in_catalog_table"]:
                flags.append("table excluded from catalog")
            if r["prose"]:
                flags.append("name suggests free text - check")
            print("\n  %s.%s   %s" % (r["table"], r["col"], r["type"]))
            print("    %d classes over %d of %d rows%s"
                  % (r["distinct"], r["non_null"], r["total"],
                     "   [" + "; ".join(flags) + "]" if flags else ""))
            shown = r["pairs"][:SAMPLE_VALUES]
            print("    " + ", ".join(
                "%s (%d)" % ("NULL" if v is None else v, n) for v, n in shown)
                + (" ..." if len(r["pairs"]) > SAMPLE_VALUES else ""))
            if r["non_null"] < r["total"]:
                print("    %d rows are NULL - not a class, nobody recorded it"
                      % (r["total"] - r["non_null"]))

    print("Classification columns in `%s`" % dbname)
    print("Scanned %d columns across the whole database, name-blind: what counts "
          "is\nwhether the DATA falls into a definite finite set." % len(columns))

    show("DECLARED - the schema itself says these are a fixed set", declared,
         "MySQL enum/set columns. Definitionally a classification; the database\n"
         "rejects anything outside the list.")

    show("CLASSIFICATIONS - multi-class, measured from the rows", strong,
         "3 to %d distinct values over enough rows to be confident. These are what\n"
         "users filter on, and a filter on a value not in this list returns zero\n"
         "rows and looks identical to a real absence." % MAX_DISTINCT)

    show("TWO-VALUED - yes/no, pass/fail, either/or", boolean,
         "Exactly two classes. Worth separating because a two-valued column is\n"
         "where 'not A' quietly stops meaning 'B' the moment NULL appears.")

    show("ONE VALUE ONLY - classification-shaped, carries no information", constant,
         "Every row holds the same value. These are traps: the column looks like a\n"
         "status and answers nothing. Filtering on it always matches everything,\n"
         "and reading it as an outcome is always wrong.")

    show("THIN EVIDENCE - definite values, too few rows to trust the set", thin,
         "Fewer than %d rows carry a value, so this looks categorical but the full\n"
         "set is probably not present yet. Re-run once there is more data."
         % THIN_ROWS)

    show("CLOSED VOCABULARY - a list of things, not a set of categories", vocab,
         "Values barely repeat (fewer than %d rows per value), so these are names\n"
         "and identifiers drawn from a fixed list - equipment, standards, models.\n"
         "Not classifications, but a filter can still miss them, so worth knowing."
         % MIN_REPEAT)

    if json_keys:
        print("\n" + "=" * 78)
        print("INSIDE JSON - classes that are not columns at all")
        print("=" * 78)
        print("The catalog profiles these by sampling values. A class here cannot be\n"
              "filtered with a plain WHERE - it needs JSON_UNQUOTE(JSON_EXTRACT(...)).")
        found = 0
        for ref, prof in sorted(json_keys.items()):
            for key, meta in sorted((prof.get("keys") or {}).items()):
                vals = list(meta.get("values") or ())
                if _json_class_like(key, vals):
                    found += 1
                    print("\n  %s -> %s" % (ref, key))
                    print("    %s" % ", ".join(str(v) for v in vals))
        if not found:
            print("  (none)")

    total_found = sum(len(x) for x in (declared, strong, boolean, thin, constant))
    missing = [r for x in (declared, strong, boolean, constant) for r in x
               if not r["in_catalog"]]
    print("\n" + "=" * 78)
    print("%d classification columns found. %d are NOT in the catalog's "
          "ENUM_VALUES," % (total_found, len(missing)))
    print("which means the model is never told those values exist and cannot "
          "filter on\nthem correctly. Regenerate after widening _ENUMISH in "
          "build_catalog.py.")
    conn.close()


if __name__ == "__main__":
    main()
