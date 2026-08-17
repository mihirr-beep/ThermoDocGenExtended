#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Questions in the APP's words, not the schema's. Answer beside ground truth.

    python tools_user_eval.py            # all
    python tools_user_eval.py 3 7        # selected

WHY THIS EXISTS SEPARATELY FROM tools_join_eval.py
--------------------------------------------------
That file scores 11/15 and is not worth much, because I wrote its questions
knowing the schema. It says "requested tests", "revisions", "datasheets",
"test_code" - words that appear in tables and not on screen. A lab engineer
using this application has never seen the database and would not phrase anything
that way, so a suite written in schema vocabulary measures how well the pipeline
answers ME.

Every question below is phrased only in words the UI itself shows the user:
Test Request, Job Number, Assigned To, Test Name, Data Sheet, Equipment List,
Equipment History, Review Comments, Peer Review, Final Report, Start/End Date,
Hours, Condition of samples on receipt, and the status words the app displays
(Test Plan Approved, Assigned Lab Engineer, At Review, in_progress, scheduled,
cancelled, datasheet_uploaded). Dashboard phrasing - Test Queue Status, Equipment
Health, Upcoming Deadlines, Team Performance - is used where a user would
naturally reach for it, because those are headings they read every day.

HOW IT IS GRADED
----------------
`truth` is SQL. It runs first and prints beside the answer, so the reply is
compared against the rows rather than against my expectations. No must/must_not
string matching: that is what mis-graded two cases in the previous suite, once
in each direction. The verdict column is filled in by reading, and the questions
were fixed before the truth was known so they cannot be tuned toward passing.

Some questions here are VAGUE ON PURPOSE - "any tests running late", "who has
the most work on" - because real users are vague. A reasonable answer that
states the reading it used is correct. Asking the user to disambiguate a
question they asked in the app's own words is not.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# (id, question, truth_sql, what_to_look_for)
CASES = [
    (1, "How many test requests are there?",
     "SELECT COUNT(*) AS test_requests FROM iec_emc_requests",
     "a count; may or may not exclude the DEMO rows, but must say which"),

    (2, "Which test requests are still waiting to be approved?",
     "SELECT tco_id, job_number, product_name, status FROM iec_emc_requests "
     "WHERE status <> 'Test Plan Approved' ORDER BY tco_id",
     "the six that are not Test Plan Approved"),

    (3, "Who is assigned to the tests on job TFS-EMC-2026-001?",
     "SELECT p.test_name, p.test_person_name, p.status FROM planner_entries p "
     "JOIN iec_emc_requests r ON r.id = p.test_request_id "
     "WHERE r.job_number = 'TFS-EMC-2026-001' ORDER BY p.test_name",
     "the engineer name(s) on that job's scheduled tests"),

    (4, "What equipment needs calibration soon?",
     "SELECT name, calibration_due_date FROM equipment "
     "WHERE calibration_due_date BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 60 DAY) "
     "ORDER BY calibration_due_date",
     "upcoming ones; 44 are already overdue, which a good answer mentions"),

    (5, "What does the test queue look like at the moment?",
     "SELECT status, COUNT(*) AS tests FROM planner_entries GROUP BY status "
     "ORDER BY tests DESC",
     "a breakdown by status - the Dashboard's own Test Queue Status framing"),

    (6, "Which engineer has the most work on right now?",
     "SELECT p.test_person_name, COUNT(*) AS tests FROM planner_entries p "
     "WHERE p.status IN ('in_progress','scheduled') GROUP BY p.test_person_name "
     "ORDER BY tests DESC",
     "the busiest engineer; ties must be reported as ties"),

    (7, "Are any tests running late?",
     "SELECT test_name, tco_id, end_date, status FROM planner_entries "
     "WHERE end_date < CURDATE() AND status IN ('in_progress','scheduled') "
     "ORDER BY end_date",
     "VAGUE ON PURPOSE - any reasonable rule is fine if it is stated"),

    (8, "What did the reviewer say about the data sheets that were sent back?",
     "SELECT d.tco_id, d.test_code, h.actor_name, h.comment "
     "FROM datasheet_status_history h JOIN `datasheet` d ON d.id = h.datasheet_id "
     "WHERE h.to_status = 'Rejected' ORDER BY d.tco_id",
     "the three rejection comments"),

    (9, "Which equipment is due for maintenance?",
     "SELECT COUNT(*) AS overdue FROM equipment "
     "WHERE maintenance_due_date < CURDATE()",
     "65 overdue; the maintenance table disagrees with equipment on 71 of 82 rows"),

    (10, "What tests are we running for Smart2pure 6UV?",
     "SELECT t.test_code, t.workflow_status FROM iec_emc_request_tests t "
     "JOIN iec_emc_requests r ON r.id = t.request_id "
     "WHERE r.product_name LIKE '%Smart2pure 6UV%' ORDER BY t.test_code",
     "the eleven tests in scope on that product's job"),

    (11, "Has the final report been uploaded for job TFS-EMC-2026-002?",
     "SELECT p.test_name, p.status, "
     "CASE WHEN p.report_file_path IS NULL OR p.report_file_path='' "
     "THEN 'no report' ELSE 'report uploaded' END AS report "
     "FROM planner_entries p JOIN iec_emc_requests r ON r.id = p.test_request_id "
     "WHERE r.job_number = 'TFS-EMC-2026-002' ORDER BY p.test_name",
     "no reports uploaded on that job"),

    (12, "Which data sheets have not been filled in yet?",
     "SELECT p.tco_id, p.test_name, p.status FROM planner_entries p "
     "LEFT JOIN `datasheet` d ON d.planner_entry_id = p.id "
     "WHERE d.id IS NULL AND p.status <> 'cancelled' ORDER BY p.tco_id, p.test_name",
     "scheduled tests with no data sheet recorded"),

    (13, "What ambient temperature did we record on the ESD test?",
     "SELECT tco_id, ambient_temperature, relative_humidity FROM `datasheet` "
     "WHERE test_code = 'ESD'",
     "23.4 C and 48 % on DEMO-EMC-301"),

    (14, "Show me the history for the BNC Cable.",
     "SELECT e.name, eh.action_type, eh.changed_by_username, eh.created_at "
     "FROM equipment_history eh JOIN equipment e ON e.id = eh.equipment_id "
     "WHERE e.name = 'BNC Cable' ORDER BY eh.created_at DESC LIMIT 5",
     "TWO instruments share this name - a good answer notices"),

    (15, "How many samples did we get for the Genpure?",
     "SELECT tco_id, product_name, test_samples, samples_available_in_lab "
     "FROM iec_emc_requests WHERE product_name LIKE '%Genpure%' ORDER BY tco_id",
     "four jobs match 'Genpure'; each declares 1 sample"),
]


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    import mysql_config
    import pymysql
    from nlp_search import orchestrator

    cfg = mysql_config.config["default"]
    params = {"host": cfg.MYSQL_HOST, "port": int(cfg.MYSQL_PORT),
              "user": cfg.MYSQL_USER, "password": cfg.MYSQL_PASSWORD,
              "database": cfg.MYSQL_DATABASE}
    conn = pymysql.connect(autocommit=True, **params)

    only = {int(a) for a in sys.argv[1:] if a.isdigit()}
    for cid, question, truth_sql, looking_for in CASES:
        if only and cid not in only:
            continue
        print("=" * 78)
        print("#%-2d  USER ASKS: %s" % (cid, question))
        print("=" * 78)

        cur = conn.cursor()
        try:
            cur.execute(truth_sql)
            rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            rows = [("TRUTH SQL FAILED", str(exc))]
        print("  GROUND TRUTH (%d row(s)) - %s" % (len(rows), looking_for))
        for r in rows[:10]:
            print("     %s" % (r,))
        if len(rows) > 10:
            print("     ... %d more" % (len(rows) - 10))

        t0 = time.time()
        try:
            res = orchestrator.answer(question, params)
        except Exception as exc:  # noqa: BLE001
            res = {"success": False, "message": "raised %s" % exc}
        secs = time.time() - t0

        print("\n  ASSISTANT SAID:")
        if not res.get("success"):
            print("     FAILED: %s" % res.get("message"))
        else:
            for line in (res.get("answer") or "").strip().split("\n")[:14]:
                print("     " + line[:150])
            print("\n  route=%s  grounding=%s  %.0fs  %s tok  %d quer(y/ies)"
                  % (res.get("route"), (res.get("grounding") or {}).get("verdict"),
                     secs, (res.get("tokens") or {}).get("total"),
                     len(res.get("sql") or [])))
            for sql in (res.get("sql") or [])[:2]:
                s = sql.get("sql") if isinstance(sql, dict) else str(sql)
                print("     SQL: %s" % " ".join(str(s).split())[:190])
        print()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
