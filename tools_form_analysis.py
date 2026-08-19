# -*- coding: utf-8 -*-
"""Analyses over datasheet form_json, and a log of every query they run.

    python tools_form_analysis.py                 # run, print, write the log
    python tools_form_analysis.py --log-only      # rewrite the log, no DB

Writes docs/form_json_analysis.md: for each analysis, what it answers, the exact
SQL, the result it returned, and what that result meant. Read-only throughout.

WHY form_json AND NOT THE COLUMNS. The projection pulls the substance of a form
into columns and does it well - measurements, equipment, modifications, the
per-test scalars. What it cannot carry is the shape of the form itself: a field
that was ASKED and left empty looks identical to a field that does not exist once
it is a NULL column, and "how much of this datasheet is actually filled in" is a
question about the form, not about any row.

That distinction is not academic here. Two of the six peer-review rejections in
this database are INCOMPLETE_OBS - a reviewer sending work back because a grid
was half empty - and no projected column can find the next one before a reviewer
does.

The queries needing JSON_TABLE are marked. sql_guard blocks it, so the assistant
cannot run those itself; they are for a person with a SQL client. Everything else
uses JSON_EXTRACT, which the guard allows.
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "docs", "form_json_analysis.md")

# Dynamic path: JSON_KEYS gives the field names, then JSON_EXTRACT takes the path
# as an expression, so one query can walk every field of every form without
# knowing any field name in advance.
_EVERY_FIELD = """
    FROM datasheet_records r
    JOIN JSON_TABLE(JSON_KEYS(r.form_json), '$[*]'
                    COLUMNS (k VARCHAR(200) PATH '$')) AS ks
    JOIN LATERAL (SELECT JSON_UNQUOTE(JSON_EXTRACT(
                    r.form_json, CONCAT('$."', ks.k, '"'))) AS v) AS x
    WHERE JSON_VALID(r.form_json)
"""

ANALYSES = [
    dict(
        id="A1",
        title="How much of each datasheet is actually filled in",
        asks="Which datasheets are thin enough that a reviewer will send them "
             "back, before a reviewer has to?",
        needs_json_table=False,
        sql="""
SELECT r.tco_id, r.test_code, r.status,
       JSON_LENGTH(JSON_KEYS(r.form_json))                          AS keys_total,
       CHAR_LENGTH(r.form_json)                                     AS form_bytes
FROM datasheet_records r
WHERE JSON_VALID(r.form_json)
ORDER BY keys_total
""",
        note="NO PERCENTAGE, deliberately. The obvious denominator is the field "
             "count in datasheet_gen/schemas/<CODE>.json, and it does not work: a "
             "schema defines a grid ONCE and the form expands it into one key per "
             "cell, so a correctly filled ESD sheet came out at 242% complete, and "
             "CE has no schema file at all because its form is hand-built HTML. A "
             "ratio nobody can defend is worse than a raw count.\n\n"
             "What the raw count is good for: comparing two forms of the SAME test "
             "code, where the shape is identical. The two ESD sheets at 128 and "
             "131 keys are comparable and close. DEMO-EMC-301's RE sheet at 16 "
             "keys is not comparable to anything here - but see the grid-cell "
             "count, which is zero for it and dozens for every other test. A "
             "datasheet with no grid cells at all has had nothing measured "
             "recorded on it.",
    ),
    dict(
        id="A2",
        title="Fields that were asked and left empty",
        asks="Which specific fields do engineers skip?",
        needs_json_table=True,
        sql="""
SELECT ks.k AS field, COUNT(*) AS forms_leaving_it_blank
""" + _EVERY_FIELD + """
  AND (x.v IS NULL OR x.v = '' OR x.v = '[]')
GROUP BY ks.k
ORDER BY forms_leaving_it_blank DESC, field
LIMIT 25
""",
        note="A blank here is a field the form PUT IN FRONT of the engineer and "
             "they moved past. That is different information from a NULL column, "
             "which cannot tell you whether anyone was ever asked.",
    ),
    dict(
        id="A3",
        title="Grid fill rate - the INCOMPLETE_OBS signal",
        asks="Which measurement or observation grids are half empty?",
        needs_json_table=True,
        sql="""
SELECT r.tco_id, r.test_code,
       REGEXP_REPLACE(ks.k, '_r[0-9]+_c[0-9]+$', '')    AS grid,
       COUNT(*)                                         AS cells,
       SUM(x.v IS NULL OR x.v = '')                     AS empty_cells,
       ROUND(100 * SUM(x.v IS NULL OR x.v = '') / COUNT(*)) AS pct_empty
""" + _EVERY_FIELD + """
  AND ks.k REGEXP '_r[0-9]+_c[0-9]+$'
GROUP BY r.id, grid
ORDER BY pct_empty DESC, cells DESC
LIMIT 25
""",
        note="Two of the six rejections in this database are INCOMPLETE_OBS, and "
             "the reviewer comment on one reads \"indirect discharge grid is only "
             "filled for the first four points. HCP 180 and 270 and both VCP rows "
             "are empty.\" That is this query, run by a human eye.\n\n"
             "The grid name is recovered by stripping the row/column suffix: an "
             "ESD observation grid is stored as one key per cell - `ind_r5_c1`, "
             "`air_r2_c3` - not as a list, which is why a LIKE on `__c` finds "
             "nothing. `ind`, `air` and `dir` are indirect, air and direct "
             "discharge. The other convention, `base__cN[]`, is a list per column "
             "and is what A2 sees.",
    ),
    dict(
        id="A4",
        title="What engineers change after a rejection",
        asks="Across every rejection, which fields get corrected most often?",
        needs_json_table=True,
        sql="""
SELECT ks.k AS field, COUNT(DISTINCT v.datasheet_id) AS datasheets_where_it_changed
FROM datasheet_revision v
JOIN datasheet_revision prev
  ON prev.datasheet_id = v.datasheet_id
 AND prev.revision_no = v.revision_no - 1
JOIN JSON_TABLE(JSON_KEYS(v.form_json), '$[*]'
                COLUMNS (k VARCHAR(200) PATH '$')) AS ks
WHERE JSON_VALID(v.form_json) AND JSON_VALID(prev.form_json)
  AND NOT (JSON_EXTRACT(v.form_json,    CONCAT('$."', ks.k, '"'))
       <=> JSON_EXTRACT(prev.form_json, CONCAT('$."', ks.k, '"')))
GROUP BY ks.k
ORDER BY datasheets_where_it_changed DESC, field
LIMIT 25
""",
        note="review_history answers this for ONE datasheet. Aggregated across "
             "all of them it stops being a story about one sheet and becomes the "
             "list of what this lab's forms get wrong - which is the list worth "
             "fixing in the form itself, not one datasheet at a time. <=> is "
             "MySQL's NULL-safe compare: without it a field appearing for the "
             "first time reads as unchanged.",
    ),
    dict(
        id="A5",
        title="Test conditions as they were actually typed",
        asks="Are the recorded ambient conditions plausible?",
        needs_json_table=False,
        sql="""
SELECT r.tco_id, r.test_code,
       JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.ambient_temperature')) AS temp_c,
       JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.relative_humidity'))   AS rh_pct,
       JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.test_date'))           AS test_date
FROM datasheet_records r
WHERE JSON_VALID(r.form_json)
ORDER BY r.test_code
""",
        note="These are strings, not numbers, so nothing has ever validated "
             "them. A standard expects roughly 15-35 C and 25-75% RH; anything "
             "outside that was either a real excursion worth noting on the "
             "report or a keystroke, and the two look identical in a column.",
    ),
    dict(
        id="A6",
        title="Sign-off completeness",
        asks="Which submitted datasheets have no name, date or signature on them?",
        needs_json_table=False,
        sql="""
SELECT r.tco_id, r.test_code, r.status,
       COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.tested_by_name')), ''),
                NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.tested_by')),      ''),
                '(nobody)')                                        AS tested_by,
       CASE WHEN JSON_EXTRACT(r.form_json, '$.signature') IS NULL
             OR JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.signature')) = ''
            THEN 'MISSING' ELSE 'present' END                      AS signature,
       COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.test_date')), ''),
                '(none)')                                          AS test_date
FROM datasheet_records r
WHERE JSON_VALID(r.form_json)
ORDER BY signature DESC, r.test_code
""",
        note="MISSING_SIGNATURE is one of the sixteen rejection codes, so this "
             "is a rejection somebody can avoid rather than receive.",
    ),
    dict(
        id="A7",
        title="Deviations engineers actually wrote down",
        asks="Where did the test depart from the standard, in their own words?",
        needs_json_table=False,
        sql="""
SELECT r.tco_id, r.test_code,
       JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.deviation')) AS deviation
FROM datasheet_records r
WHERE JSON_VALID(r.form_json)
  AND JSON_UNQUOTE(JSON_EXTRACT(r.form_json, '$.deviation')) NOT IN ('', 'NA', 'N/A', 'None', '-')
ORDER BY r.test_code
""",
        note="DEVIATION_UNDOC is another of the sixteen codes. Free text, so no "
             "aggregate is honest here - it is a read-through, not a metric.",
    ),
    dict(
        id="A8",
        title="Fields one form of a test type has and another does not",
        asks="Are two engineers filling the same form differently?",
        needs_json_table=True,
        sql="""
SELECT r.test_code, ks.k AS field,
       COUNT(*)                                   AS forms_with_it,
       (SELECT COUNT(*) FROM datasheet_records r2
         WHERE r2.test_code = r.test_code
           AND JSON_VALID(r2.form_json))          AS forms_of_this_test
""" + _EVERY_FIELD + """
GROUP BY r.test_code, ks.k
HAVING forms_with_it < forms_of_this_test
ORDER BY r.test_code, field
LIMIT 30
""",
        note="Only meaningful where a test code has more than one datasheet. On "
             "this database that is CE and ESD; everything else has a single "
             "form and cannot disagree with itself.",
    ),
    dict(
        id="A9",
        title="Grid fill rate AT SUBMISSION, per revision",
        asks="Which grids were half empty at the moment the engineer submitted - "
             "the state a reviewer was looking at?",
        needs_json_table=True,
        sql="""
SELECT d.tco_id, d.test_code, v.revision_no,
       REGEXP_REPLACE(ks.k, '_r[0-9]+_c[0-9]+$', '')    AS grid,
       COUNT(*)                                         AS cells,
       SUM(x.v IS NULL OR x.v = '')                     AS empty_cells,
       ROUND(100 * SUM(x.v IS NULL OR x.v = '') / COUNT(*)) AS pct_empty,
       (SELECT h.reason_code FROM datasheet_status_history h
         WHERE h.datasheet_id = v.datasheet_id
           AND h.revision_no  = v.revision_no
           AND h.to_status    = 'Rejected' LIMIT 1)     AS sent_back_for
FROM datasheet_revision v
JOIN `datasheet` d ON d.id = v.datasheet_id
JOIN JSON_TABLE(JSON_KEYS(v.form_json), '$[*]'
                COLUMNS (k VARCHAR(200) PATH '$')) AS ks
JOIN LATERAL (SELECT JSON_UNQUOTE(JSON_EXTRACT(
                v.form_json, CONCAT('$."', ks.k, '"'))) AS v) AS x
WHERE JSON_VALID(v.form_json)
  AND ks.k REGEXP '_r[0-9]+_c[0-9]+$'
GROUP BY v.id, grid
ORDER BY pct_empty DESC, d.tco_id, v.revision_no
LIMIT 25
""",
        note="A3 runs on datasheet_records, which holds the CURRENT form - so a "
             "grid that was half empty when it was rejected reads 0% there, "
             "because the engineer has since filled it in. This runs on the "
             "frozen revisions instead, which is the state the reviewer actually "
             "saw, and puts the rejection code beside it.\n\n"
             "This is the one to run before submitting rather than after. A grid "
             "at 40% with no reason_code yet is the next INCOMPLETE_OBS.",
    ),
]


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    log_only = "--log-only" in sys.argv
    sys.path.insert(0, HERE)

    results = {}
    dbname = "(not queried)"
    if not log_only:
        import mysql_config
        import pymysql
        cfg = mysql_config.config["default"]
        dbname = cfg.MYSQL_DATABASE
        conn = pymysql.connect(host=cfg.MYSQL_HOST, port=int(cfg.MYSQL_PORT),
                               user=cfg.MYSQL_USER, password=cfg.MYSQL_PASSWORD,
                               database=dbname, autocommit=True, charset="utf8mb4")
        cur = conn.cursor()
        for a in ANALYSES:
            print("=" * 78)
            print("%s  %s" % (a["id"], a["title"]))
            print("=" * 78)
            try:
                cur.execute(a["sql"])
                cols = [d[0] for d in cur.description]
                rows = [tuple("" if v is None else str(v) for v in r)
                        for r in cur.fetchall()]
                results[a["id"]] = (cols, rows, None)
                widths = [max(len(cols[i]),
                              max((len(r[i]) for r in rows), default=0))
                          for i in range(len(cols))]
                widths = [min(w, 42) for w in widths]
                print("  " + " | ".join(c[:w].ljust(w) for c, w in zip(cols, widths)))
                print("  " + "-|-".join("-" * w for w in widths))
                for r in rows[:18]:
                    print("  " + " | ".join(v[:w].ljust(w) for v, w in zip(r, widths)))
                if len(rows) > 18:
                    print("  ... %d more rows" % (len(rows) - 18))
            except Exception as exc:  # noqa: BLE001
                results[a["id"]] = ([], [], str(exc)[:300])
                print("  QUERY FAILED: %s" % str(exc)[:300])
            print()

    _write_log(dbname, results)
    print("log written: %s" % LOG)


def _write_log(dbname, results):
    out = []
    add = out.append
    add("# form_json analyses")
    add("")
    add("Every query below was run against `%s` and the result it returned is "
        "printed with it. Regenerate with `python tools_form_analysis.py`; the "
        "results move as the data does, the queries do not." % dbname)
    add("")
    add("`form_json` is the datasheet exactly as the engineer submitted it. The "
        "projection pulls its substance into columns and does that well - what "
        "it cannot carry is the shape of the form. A field that was **asked and "
        "left empty** is indistinguishable from a field that does not exist once "
        "it is a NULL column, and *how much of this datasheet is filled in* is a "
        "question about the form rather than about any row.")
    add("")
    add("That matters here because two of the six peer-review rejections in this "
        "database are `INCOMPLETE_OBS` - a reviewer sending work back over a "
        "half-filled grid - and no projected column finds the next one before a "
        "reviewer does.")
    add("")
    add("## Where it lives")
    add("")
    add("| table | what its form_json is |")
    add("|---|---|")
    add("| `datasheet_records.form_json` | the CURRENT form, overwritten on every save |")
    add("| `datasheet_revision.form_json` | the form FROZEN at each submission - the history |")
    add("| `datasheet_draft_history.form_json` | every autosave, including drafts never submitted |")
    add("")
    add("Use `datasheet_records` for \"how are things now\", `datasheet_revision` "
        "for anything comparing attempts. `JSON_VALID(form_json)` first, always: "
        "the column is `longtext`, so nothing in the schema guarantees it parses.")
    add("")
    add("## A note on JSON_TABLE")
    add("")
    add("Several queries walk every field without naming any, which needs "
        "`JSON_TABLE` plus a dynamic path built with `CONCAT`. **`sql_guard` "
        "blocks `JSON_TABLE`**, so the assistant cannot run those itself - they "
        "are for a SQL client. The ones using only `JSON_EXTRACT` it can run, and "
        "those are marked.")
    add("")
    for a in ANALYSES:
        cols, rows, err = results.get(a["id"], ([], [], None))
        add("---")
        add("")
        add("## %s. %s" % (a["id"], a["title"]))
        add("")
        add("**Answers:** %s" % a["asks"])
        add("")
        add("**The assistant can run this:** %s"
            % ("no - needs JSON_TABLE, which sql_guard blocks"
               if a["needs_json_table"] else "yes - JSON_EXTRACT only"))
        add("")
        add("```sql")
        add(a["sql"].strip())
        add("```")
        add("")
        if err:
            add("Result: **query failed** - `%s`" % err)
        elif cols:
            add("Returned %d row(s):" % len(rows))
            add("")
            add("| " + " | ".join(cols) + " |")
            add("|" + "|".join("---" for _ in cols) + "|")
            for r in rows[:20]:
                add("| " + " | ".join(str(v).replace("|", "\\|")[:70] for v in r) + " |")
            if len(rows) > 20:
                add("")
                add("*... %d more rows.*" % (len(rows) - 20))
        else:
            add("*Not run - use `python tools_form_analysis.py` against a database.*")
        add("")
        add("**Why it is worth running:** %s" % a["note"])
        add("")
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with io.open(LOG, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out) + "\n")
    except (IOError, OSError) as exc:
        print("could not write the log: %s" % exc)


if __name__ == "__main__":
    main()
