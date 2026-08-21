#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Questions the APPLICATION CANNOT ANSWER. Spends tokens.

    python tools_beyond_ui_eval.py --truth      # FREE: just the ground truth
    python tools_beyond_ui_eval.py              # runs the assistant too
    python tools_beyond_ui_eval.py 3 7          # selected cases

WHY THESE QUESTIONS AND NOT OTHERS
----------------------------------
tools_user_eval.py asks things a user could also find by clicking - it measures
whether the assistant is as good as the screen. That is the wrong bar. The screen
is already there, and anybody who wants one datasheet will open it.

What the application genuinely cannot do is look ACROSS things. Every page in it
is scoped to one record: one request, one datasheet, one planner entry, one piece
of equipment. So the questions worth asking here are the ones that need two or
more records compared, a history walked, or a pattern counted - and none of them
has a screen:

  comparison      three products side by side; the UI shows one at a time
  reasoning       WHY something failed, not THAT it failed
  history         what changed between two attempts, who kept sending it back
  aggregate       which failure mode costs the most rework lab-wide
  correlation     equipment that turns up disproportionately on failures
  negative space  work that was requested and never scheduled - by definition
                  not on any page, because there is no record to open

A question that a user could answer by opening a page is a question this suite
should not contain.

HOW IT IS GRADED
----------------
`truth` is SQL and runs first, free, so the answer is read against rows rather
than against my expectations. Run --truth before spending anything: a broken
truth query wastes an API call and grades nothing. No string matching - that
mis-graded five cases in earlier suites, in both directions.

Cost is printed per question and totalled, because the point of testing is to
find defects, not to discover the bill afterwards.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# (id, category, question, truth_sql, what to look for in the answer)
CASES = [
    (1, "compare",
     "Compare the Vantage Water Purifier, the Kestrel Spectrometer and the "
     "Cascade Chromatograph. Which one gave us the most trouble?",
     """SELECT r.product_name,
               COUNT(*) AS tests,
               SUM(d.result IN ('FAIL','C','D')) AS failed,
               SUM(d.status='Draft') AS unfinished,
               SUM((SELECT COUNT(*) FROM datasheet_status_history h
                     WHERE h.datasheet_id = d.id AND h.to_status='Rejected'))
                 AS sent_back
          FROM `datasheet` d
          JOIN planner_entries p ON p.id = d.planner_entry_id
          JOIN iec_emc_requests r ON r.id = p.test_request_id
         WHERE r.product_name REGEXP 'Vantage|Kestrel|Cascade'
         GROUP BY r.product_name""",
     "three products ranked, with WHY - failures vs rework are different trouble"),

    (2, "aggregate",
     "Which kind of problem causes us the most rework?",
     """SELECT h.reason_code, COUNT(*) AS times_sent_back,
               COUNT(DISTINCT h.datasheet_id) AS sheets
          FROM datasheet_status_history h
         WHERE h.to_status='Rejected' AND h.reason_code IS NOT NULL
         GROUP BY h.reason_code ORDER BY times_sent_back DESC""",
     "CAL_EXPIRED is top at 4; rework is the REVIEW axis, not unit failure"),

    (3, "history",
     "Which reviewer sends the most work back, and what do they send it back for?",
     """SELECT h.actor_name,
               SUM(h.to_status='Rejected') AS sent_back,
               SUM(h.to_status='Approved') AS approved,
               GROUP_CONCAT(DISTINCT h.reason_code) AS reasons
          FROM datasheet_status_history h
         GROUP BY h.actor_name ORDER BY sent_back DESC""",
     "a named person with counts; approved matters too or it reads as an accusation"),

    (4, "correlation",
     "Is any piece of test equipment turning up more often on the tests that "
     "failed than on the ones that passed?",
     """SELECT de.equipment_name,
               SUM(d.result IN ('FAIL','C','D')) AS on_failed,
               SUM(d.result IN ('PASS','A','B')) AS on_passed,
               COUNT(*) AS total
          FROM datasheet_equipment de
          JOIN `datasheet` d ON d.id = de.datasheet_id
         GROUP BY de.equipment_name
        HAVING on_failed > 0 ORDER BY on_failed DESC""",
     "equipment is on nearly every sheet, so a bare count proves nothing - "
     "the honest answer says the rate is indistinguishable, or shows both columns"),

    (5, "history",
     "Have any products failed the same test more than once?",
     """SELECT r.product_name, d.test_code, COUNT(*) AS failures
          FROM `datasheet` d
          JOIN planner_entries p ON p.id = d.planner_entry_id
          JOIN iec_emc_requests r ON r.id = p.test_request_id
         WHERE d.result IN ('FAIL','C','D')
         GROUP BY r.product_name, d.test_code
        HAVING failures > 1""",
     "if the answer lists any, check it is not counting revisions of ONE test twice"),

    (6, "negative space",
     "Are there any tests a customer asked for that we never scheduled?",
     """SELECT r.tco_id, r.product_name, t.test_code
          FROM iec_emc_request_tests t
          JOIN iec_emc_requests r ON r.id = t.request_id
          LEFT JOIN planner_entries p ON p.test_request_id = t.request_id
         WHERE t.is_selected=1 AND p.id IS NULL
         ORDER BY r.tco_id LIMIT 20""",
     "the count matters more than the list; is_selected=1 is the filter that "
     "separates real work from menu items"),

    (7, "status",
     "Which tests are marked as in progress but have not actually been touched "
     "for more than a month?",
     """SELECT p.tco_id, p.test_name, p.end_date,
               DATEDIFF(CURDATE(), p.end_date) AS days_past
          FROM planner_entries p
         WHERE p.status='in_progress' AND p.end_date < CURDATE() - INTERVAL 30 DAY
         ORDER BY days_past DESC""",
     "12 rows; the answer must not present these as active work"),

    (8, "reasoning",
     "Why do our units keep failing radiated susceptibility?",
     """SELECT r.product_name, d.result, d.failure_reason_code, d.deviation
          FROM `datasheet` d
          JOIN planner_entries p ON p.id = d.planner_entry_id
          JOIN iec_emc_requests r ON r.id = p.test_request_id
         WHERE d.test_code='RS_RI' ORDER BY d.result""",
     "RS_MALFUNCTION on the failures; 'keep failing' is a premise to check, "
     "not accept - how many actually failed vs passed"),

    (9, "aggregate",
     "Across all the conducted emission tests, is there a frequency that "
     "breaches the limit more than the others?",
     """SELECT f.value AS frequency, COUNT(*) AS breaches
          FROM datasheet_measurement mg
          JOIN datasheet_measurement f
            ON f.datasheet_id = mg.datasheet_id
           AND f.grid_key = mg.grid_key AND f.row_no = mg.row_no
           AND f.col_key = REPLACE(mg.col_key, '_margin', '_freq')
          JOIN `datasheet` d ON d.id = mg.datasheet_id
         WHERE d.test_code='CE' AND mg.col_key LIKE '%margin%'
           AND mg.value_num > 0
         GROUP BY f.value ORDER BY breaches DESC LIMIT 10""",
     "positive margin IS the breach. NOTE: qp_margin must pair with qp_freq and "
     "avg_margin with avg_freq - a LIKE '%freq%' join matches both and inflates "
     "every count 4x, which is what my own first truth query did"),

    (10, "history",
     "Which datasheet took the most goes to get approved, and what kept changing?",
     """SELECT d.tco_id, d.test_code,
               (SELECT COUNT(*) FROM datasheet_revision v
                 WHERE v.datasheet_id=d.id) AS revisions,
               (SELECT COUNT(*) FROM datasheet_draft_history dh
                 WHERE dh.datasheet_id=d.id) AS saves
          FROM `datasheet` d
         ORDER BY revisions DESC, saves DESC LIMIT 5""",
     "revisions are submissions, saves are keystrokes - they are not the same "
     "measure of 'goes'"),

    (11, "correlation",
     "Did we ever run a test using equipment that was out of calibration?",
     """SELECT d.tco_id, d.test_code, de.equipment_name, de.calibration_due
          FROM datasheet_equipment de
          JOIN `datasheet` d ON d.id = de.datasheet_id
         WHERE de.calibration_due IS NOT NULL AND de.calibration_due <> ''
         LIMIT 15""",
     "calibration_due is TEXT on the datasheet - free-typed. If it cannot be "
     "compared to a date the answer should say that, not guess"),

    (12, "insight",
     "What do the products that passed everything first time have in common "
     "that the ones we had trouble with do not?",
     """SELECT r.product_name,
               SUM(d.result IN ('FAIL','C','D')) AS failed,
               COUNT(*) AS tests,
               COUNT(DISTINCT d.eut_configuration) AS configs,
               GROUP_CONCAT(DISTINCT d.eut_configuration) AS config
          FROM `datasheet` d
          JOIN planner_entries p ON p.id = d.planner_entry_id
          JOIN iec_emc_requests r ON r.id = p.test_request_id
         GROUP BY r.product_name ORDER BY failed""",
     "the honest answer is probably 'nothing distinguishes them in this data' - "
     "a confident causal story here would be the worst outcome of the suite"),
]


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = [a for a in sys.argv[1:]]
    truth_only = "--truth" in args
    only = {int(a) for a in args if a.isdigit()}

    import mysql_config
    import pymysql
    cfg = mysql_config.config["default"]
    params = {"host": cfg.MYSQL_HOST, "port": int(cfg.MYSQL_PORT),
              "user": cfg.MYSQL_USER, "password": cfg.MYSQL_PASSWORD,
              "database": cfg.MYSQL_DATABASE}
    conn = pymysql.connect(autocommit=True, **params)
    orchestrator = None
    if not truth_only:
        from nlp_search import orchestrator as _o
        orchestrator = _o

    spent = 0.0
    tokens = 0
    for cid, cat, question, truth_sql, looking in CASES:
        if only and cid not in only:
            continue
        print("=" * 78)
        print("#%-2d [%s]  %s" % (cid, cat, question))
        print("=" * 78)
        cur = conn.cursor()
        try:
            cur.execute(truth_sql)
            rows = cur.fetchall()
            err = None
        except Exception as exc:            # noqa: BLE001
            rows, err = [], str(exc)
        cur.close()
        if err:
            print("  TRUTH SQL FAILED: %s" % err[:200])
        else:
            print("  TRUTH (%d row(s)) - looking for: %s" % (len(rows), looking))
            for r in rows[:8]:
                print("     %s" % (r,))
            if len(rows) > 8:
                print("     ... %d more" % (len(rows) - 8))
        if truth_only:
            print()
            continue

        t0 = time.time()
        try:
            res = orchestrator.answer(question, params)
        except Exception as exc:            # noqa: BLE001
            res = {"success": False, "message": "raised %s" % exc}
        secs = time.time() - t0
        print()
        print("  ASSISTANT:")
        if not res.get("success"):
            print("     FAILED: %s" % res.get("message"))
        else:
            for line in (res.get("answer") or "").strip().split("\n")[:18]:
                print("     " + line[:150])
            # answer() returns input/output/cached, and the cost has to be
            # computed from that split - a total alone cannot price a request,
            # because output bills at 8x input on this tier and reasoning
            # tokens bill as output. The first run of this suite printed $0 for
            # every question for exactly that reason.
            tok = (res.get("tokens") or {})
            from nlp_search import audit as _audit
            cost = _audit.estimate_cost(res.get("model") or "gpt-5-nano",
                                        tok.get("input"), tok.get("output"),
                                        tok.get("cached"))
            tokens += int(tok.get("total") or 0)
            spent += float(cost or 0)
            print()
            print("     route=%s  grounding=%s  %.0fs  %s tok "
                  "(in %s / out %s / cached %s)  $%.5f  %d quer(y/ies)"
                  % (res.get("route"), (res.get("grounding") or {}).get("verdict"),
                     secs, tok.get("total"), tok.get("input"), tok.get("output"),
                     tok.get("cached"), cost, len(res.get("sql") or [])))
        print()
    conn.close()
    if not truth_only:
        print("=" * 78)
        print("TOTAL  %s tokens   $%.4f reported by the pipeline" % (tokens, spent))
        print("Per-question cost is also in nlp_search_audit.estimated_cost_usd -")
        print("read it from there if the pipeline did not report one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
