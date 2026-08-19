# -*- coding: utf-8 -*-
"""Score the INSIGHT layer against the rows, in a person's words.

    python tools_insight_eval.py          # all cases
    python tools_insight_eval.py 3 5      # only these

Spends tokens: every case is a full pipeline run.

WHY THE QUESTIONS READ LIKE THIS. tools_join_eval and tools_user_eval score the
same system twenty points apart and the only difference is vocabulary - the first
says "requested tests" and "revisions", the second says "Job Number" and "Data
Sheet". These are written the way somebody asks a colleague: "what went wrong
with", "sent back", "what did they change". No table names, no column names, and
the odd missing apostrophe on purpose.

Each case carries what the DATABASE says, as SQL, plus two lists:

  must     - a fact the answer has to contain to be any use
  must_not - a fact that would make the answer confidently WRONG

must_not is the half that matters. An answer missing a detail wastes a minute; an
answer that names CAL_EXPIRED as the reason a UNIT failed a standard has crossed
the two axes and sounds authoritative doing it. Scoring three buckets - right,
incomplete, wrong - keeps those separate, because they cost differently.
"""
import os
import sys
import time

CASES = [
    dict(
        n=1,
        q="what went wrong with the Lifecycle Probe Analyser",
        truth_sql="SELECT d.test_code, d.result, d.failure_reason_code "
                  "FROM `datasheet` d JOIN planner_entries p ON p.id=d.planner_entry_id "
                  "JOIN iec_emc_requests r ON r.id=p.test_request_id "
                  "WHERE r.product_name LIKE '%Lifecycle Probe%' "
                  "AND d.failure_reason_code IS NOT NULL",
        looking_for="the CE test failed, code CE_LIMIT_EXCEEDED",
        must=["CE_LIMIT_EXCEEDED"],
        # CAL_EXPIRED is why the RECORD was sent back, not why the unit failed.
        # Naming it as the failure crosses the two axes.
        must_not=["failed because of CAL_EXPIRED",
                  "failed due to CAL_EXPIRED",
                  "unit failed for CAL_EXPIRED"],
    ),
    dict(
        n=2,
        q="did any datasheet get sent back more than once, and what for",
        truth_sql="SELECT r.tco_id, d.test_code, COUNT(*) n, "
                  "GROUP_CONCAT(h.reason_code ORDER BY h.revision_no) codes "
                  "FROM datasheet_status_history h JOIN `datasheet` d ON d.id=h.datasheet_id "
                  "JOIN planner_entries p ON p.id=d.planner_entry_id "
                  "JOIN iec_emc_requests r ON r.id=p.test_request_id "
                  "WHERE h.to_status='Rejected' GROUP BY d.id HAVING n>1",
        looking_for="exactly one: DEMO-EMC-304 ESD, CAL_EXPIRED then INCOMPLETE_OBS",
        must=["DEMO-EMC-304", "CAL_EXPIRED", "INCOMPLETE_OBS"],
        must_not=["no datasheet", "none were", "not sent back more than once"],
    ),
    dict(
        n=3,
        q="what do the reviewers keep sending sheets back for",
        truth_sql="SELECT h.reason_code, COUNT(*) events, COUNT(DISTINCT h.datasheet_id) sheets "
                  "FROM datasheet_status_history h WHERE h.to_status='Rejected' "
                  "AND h.reason_code IS NOT NULL GROUP BY h.reason_code "
                  "ORDER BY events DESC, h.reason_code",
        looking_for="CAL_EXPIRED 2 and INCOMPLETE_OBS 2 lead; MISSING_PHOTO 1, UNIT_ERROR 1",
        must=["CAL_EXPIRED", "INCOMPLETE_OBS"],
        # the product-failure axis has no business in this answer
        must_not=["CE_LIMIT_EXCEEDED", "SURGE_DAMAGE", "RS_MALFUNCTION"],
    ),
    dict(
        n=4,
        q="has any other product failed the same way as the Lifecycle Probe Analyser",
        truth_sql="SELECT r.product_name, d.failure_reason_code FROM `datasheet` d "
                  "JOIN planner_entries p ON p.id=d.planner_entry_id "
                  "JOIN iec_emc_requests r ON r.id=p.test_request_id "
                  "WHERE d.failure_reason_code='CE_LIMIT_EXCEEDED'",
        looking_for="no other product - CE_LIMIT_EXCEEDED appears on one product only",
        must=[],
        # inventing a peer is the failure here; so is silently listing the
        # product that was asked about as though it were an answer
        must_not=["Spectra Bench", "Orion Vacuum", "Meridian Rework"],
    ),
    dict(
        n=5,
        q="what did they change on the Spectra Bench Photometer",
        truth_sql="SELECT m.mod_state, m.description FROM datasheet_modification m "
                  "JOIN `datasheet` d ON d.id=m.datasheet_id "
                  "JOIN planner_entries p ON p.id=d.planner_entry_id "
                  "JOIN iec_emc_requests r ON r.id=p.test_request_id "
                  "WHERE r.product_name LIKE '%Spectra Bench%' AND m.mod_state='1'",
        looking_for="a common-mode choke fitted on the sensor harness",
        must=["choke"],
        must_not=["no modification", "nothing was changed", "no changes were recorded"],
    ),
    dict(
        n=6,
        q="how many of our units actually failed their test",
        truth_sql="SELECT COUNT(*) FROM `datasheet` d "
                  "WHERE d.failure_reason_code IS NOT NULL",
        looking_for="3 - CE on 301, RS_RI on 302, SURGE on 303",
        must=["3"],
        # 6 is the number of RECORD rejections; quoting it here is the axis error
        must_not=["6 units", "six units"],
    ),
    dict(
        n=7,
        q="what changed in the readings after the ESD sheet was sent back",
        truth_sql="SELECT DISTINCT m.revision_no FROM datasheet_measurement m "
                  "JOIN `datasheet` d ON d.id=m.datasheet_id "
                  "WHERE d.tco_id='DEMO-EMC-304' AND d.test_code='ESD' "
                  "AND m.grid_key IN ('line_measurements','neutral_measurements')",
        looking_for="ESD has no quasi-peak grid, so a per-frequency comparison "
                    "cannot be made - say that, do not report 'no change'",
        must=[],
        must_not=["no change", "nothing changed", "readings are identical",
                  "improvement of 0"],
    ),
    dict(
        n=8,
        q="who is sending back the most work in peer review",
        truth_sql="SELECT h.actor_name, COUNT(*) FROM datasheet_status_history h "
                  "WHERE h.to_status='Rejected' GROUP BY h.actor_name "
                  "ORDER BY COUNT(*) DESC",
        looking_for="Saimounika Chandavolu, 3 of the 6 rejections",
        must=["Saimounika"],
        # it answered "zero rejections logged in peer review" by querying
        # iec_emc_requests.rejected_at - a THIRD rejection concept, about an
        # admin refusing a request, empty on every row
        must_not=["zero rejections", "no rejections", "no one"],
    ),
]


_FAIL_RE = None


def _states(want, low, codes):
    """Does the answer state this fact, by code OR by the label a person reads?

    The first version demanded the raw code, and marked an answer INCOMPLETE for
    saying "Equipment calibration expired or due date missing - 2" instead of
    "CAL_EXPIRED - 2". The counts were right and the phrasing was better than
    what was asked for. A checker that penalises that is measuring the wrong
    thing, so a code is satisfied by its label out of emc_reason_code.
    """
    import re
    if want.isdigit():
        # A bare number needs word boundaries. must=["3"] passed on the "3"
        # inside "DEMO-EMC-301", so an answer saying "1 unit failed" when three
        # did was scored RIGHT. A digit is the one thing in these lists that
        # occurs by accident inside other tokens.
        return bool(re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(want), low))
    if want.lower() in low:
        return True
    label = codes.get(want.upper(), ("", ""))[0]
    return bool(label) and label.lower() in low


def _axis_warning(low, codes):
    """Flag a review-rejection reason sitting next to a failure claim.

    Case 1 came back with "the issue that went wrong was a CE failure tied to
    expired calibration", and the fixed must_not phrases missed it - they looked
    for "failed because of CAL_EXPIRED" and the answer said it a different way.
    A window search around each review-rejection code catches the shape rather
    than the wording.

    Deliberately a WARNING. Whether a sentence crosses the axes is a judgement
    about prose, and a checker that guesses at it will both miss cases and
    invent them. This narrows what a person has to read.
    """
    global _FAIL_RE
    import re
    if _FAIL_RE is None:
        _FAIL_RE = re.compile(r"\bfail(?:ed|ure|s|ing)?\b")
    out = []
    for code, (label, family) in codes.items():
        if family != "review_rejection":
            continue
        for needle in (code.lower(), (label or "").lower()):
            if not needle:
                continue
            for m in re.finditer(re.escape(needle), low):
                if _FAIL_RE.search(low[max(0, m.start() - 110):m.start()]):
                    out.append("%s appears just after a failure claim" % code)
                    break
    return sorted(set(out))


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mysql_config
    import pymysql
    from nlp_search import audit, orchestrator

    cfg = mysql_config.config["default"]
    params = {"host": cfg.MYSQL_HOST, "port": int(cfg.MYSQL_PORT),
              "user": cfg.MYSQL_USER, "password": cfg.MYSQL_PASSWORD,
              "database": cfg.MYSQL_DATABASE}
    conn = pymysql.connect(autocommit=True, charset="utf8mb4", **params)

    # code -> (label, family). The taxonomy decides which axis a code is on, so
    # the checker reads it rather than carrying a second copy that can drift.
    cur = conn.cursor()
    cur.execute("SELECT code, label, family FROM emc_reason_code")
    codes = {c: (l, f) for c, l, f in cur.fetchall()}

    model_name = os.environ.get("NLP_SEARCH_MODEL",
                                orchestrator.DEFAULT_MODEL)
    spent = []

    only = {int(a) for a in sys.argv[1:] if a.isdigit()}
    right = incomplete = wrong = refusals = 0

    for case in CASES:
        if only and case["n"] not in only:
            continue
        print("=" * 78)
        print("#%d  %s" % (case["n"], case["q"]))
        print("=" * 78)
        cur = conn.cursor()
        try:
            cur.execute(case["truth_sql"])
            rows = cur.fetchall()
        except Exception as exc:                        # noqa: BLE001
            rows = [("TRUTH SQL FAILED", str(exc))]
        print("  DATABASE SAYS (%d row(s)) - %s" % (len(rows), case["looking_for"]))
        for r in rows[:8]:
            print("     %s" % (r,))

        t0 = time.time()
        try:
            res = orchestrator.answer(case["q"], params)
        except Exception as exc:                        # noqa: BLE001
            res = {"success": False, "message": "raised %s" % exc}
        secs = time.time() - t0
        answer = (res.get("answer") or res.get("message") or "")

        # WHAT IT COST AND WHAT IT TOUCHED. A run that only prints verdicts makes
        # you re-run it to find out why, and re-running is the expensive part.
        # Every case shows its price and the primitives and SQL behind it, so a
        # wrong answer can be diagnosed from the transcript instead of a second
        # run. Measured on this database: about $0.003 a question, so a full pass
        # of this file is roughly two and a half cents.
        tok = res.get("tokens") or {}
        cost = audit.estimate_cost(model_name, tok.get("input") or 0,
                                   tok.get("output") or 0)
        spent.append(cost or 0.0)
        print("\n  COST  %.0fs   in %s / out %s tokens   $%.5f   route=%s"
              % (secs, tok.get("input") or 0, tok.get("output") or 0, cost or 0.0,
                 res.get("route") or "-"))
        trace = res.get("sql") or []
        insight_calls = [s for s in trace if "insights" in str(s).lower()
                         or "(" in str(s) and "SELECT" not in str(s).upper()]
        print("  TRACE %d query/queries%s"
              % (len(trace),
                 ("; primitives: " + ", ".join(str(s)[:46] for s in insight_calls[:3]))
                 if insight_calls else "; no insight primitive was called"))
        print("\n  ANSWER:")
        print("     " + answer.replace("\n", "\n     ")[:900])

        low = answer.lower()
        missing = [m for m in case["must"] if not _states(m, low, codes)]
        banned = [m for m in case["must_not"] if m.lower() in low]
        axis = _axis_warning(low, codes)
        # A refusal is not a pass. Case 7 ran zero queries, returned the
        # capability message, and scored RIGHT because it contained none of the
        # banned phrases - which flatters the system exactly where it is weakest.
        # No query behind it means no answer, whatever the prose looks like.
        refused = (not trace) or "did not run any query" in low
        if banned:
            verdict, tag = "WRONG", "says %s" % ", ".join(repr(b) for b in banned)
            wrong += 1
        elif refused:
            verdict, tag = "REFUSED", "ran %d queries - no answer attempted" % len(trace)
            refusals += 1
        elif missing:
            verdict, tag = "INCOMPLETE", "missing %s" % ", ".join(missing)
            incomplete += 1
        else:
            verdict, tag = "RIGHT", ""
            right += 1
        print("\n  -> %s %s" % (verdict, tag))
        if axis:
            print("  !! READ THIS ONE: %s" % "; ".join(axis))
            print("     A review-rejection reason sitting next to a failure claim is")
            print("     how the two axes get crossed. Prose is not reliably gradable,")
            print("     so this is a flag for a human, not a verdict.")
        print()

    total = right + incomplete + wrong + refusals
    print("=" * 78)
    print("  right %d   incomplete %d   refused %d   CONFIDENTLY WRONG %d   of %d"
          % (right, incomplete, refusals, wrong, total))
    print("=" * 78)
    print("  The last column is the one that matters. An incomplete answer costs a")
    print("  minute; a wrong one reaches a report.")
    if spent:
        print()
        print("  spent $%.4f across %d question(s), $%.5f each on average"
              % (sum(spent), len(spent), sum(spent) / len(spent)))


if __name__ == "__main__":
    main()
