#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Graded questions across single, double, triple and cross-domain joins.

    python tools_join_eval.py                 # everything
    python tools_join_eval.py 5 6 12          # just those cases

Complements tools_routing_eval.py, which is free and checks only that a question
reaches a worker that CAN see the answer. This one spends real tokens and checks
whether the answer is right.

HOW A CASE IS GRADED
--------------------
Every expectation was computed by hand in SQL first, so the grade does not rest
on my reading of the reply:

    must      every one of these must appear in the answer
    must_not  none of these may appear - this is where the marks are
    ok_refuse True when declining is an acceptable outcome

`must_not` carries the weight because the failure that matters here is not a
wrong answer, it is a CONFIDENT wrong answer. Case 6 is the clearest: a naive
join on test_code returns 82 and a normalised one returns 79, both look
plausible, and nothing about the reply tells you which join ran. So 82 is listed
as must_not - the number is the tell.

Scored as three separate outcomes, not one accuracy figure. An assistant that
declines half the time and is never wrong is useful; one that answers everything
and is occasionally wrong is not, because nobody can tell which replies to
trust.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# (id, shape, question, must, must_not, ok_refuse)
CASES = [
    # Expected 12 at first, and marked the run WRONG for answering 9. The reply
    # was right and my expectation was not: it ran `WHERE is_synthetic <> 1` and
    # said "excluding synthetic/demo rows", which is the catalog's own rule about
    # the seeded corpus. 12 counts the three DEMO requests as real jobs. So the
    # question is asked precisely now, and both readings are accepted as long as
    # the answer DISCLOSES which one it used.
    (1, "single", "How many EMC test requests are there in total, including any "
                  "synthetic demo rows?",
     ["12"], [], False),

    (2, "single", "How many pieces of equipment are past their calibration due date?",
     ["44"], [], False),

    (3, "double", "Which datasheets did Krishna Muthangi record, and what result did each get?",
     ["CRF", "PFMF", "RS_RI"], ["CE", "ESD"], False),

    (4, "json", "How many of our cables are shielded versus unshielded?",
     ["1", "28"], ["0 shielded", "no cables"], False),

    (5, "triple", "For job DEMO-EMC-302, which of the requested tests have a datasheet "
                  "recorded against them?",
     ["RS", "POWER_FREQ", "CRF"], ["none", "no datasheet"], False),

    # The headline case. 79 is right, 82 is what a naive join on test_code gives.
    (6, "triple", "How many requested tests across the whole lab have no datasheet "
                  "recorded at all?",
     ["79"], ["82"], False),

    (7, "datasheet", "What ambient temperature and relative humidity were recorded on the "
                     "ESD test for DEMO-EMC-301?",
     ["23.4", "48"], [], False),

    (8, "datasheet", "Which datasheets are still in draft?",
     ["RE", "CRF", "SURGE"], [], False),

    (9, "reason", "Why have datasheets been sent back in peer review?",
     ["calibration", "Observation grid", "photograph"], ["no rejections", "none"], False),

    # Graded on the CODES, not their prose labels - the first version asked for
    # "Conducted emission" and the reply said CE_LIMIT_EXCEEDED, which is the
    # same fact and scored as a miss.
    #
    # The must_not list is the real test here and it is the one that was absent.
    # This question is about family='test_failure' - why a UNIT failed a
    # standard. A run that looked incomplete was in fact wrong: it offered
    # "LISN calibration date missing from the equipment list" and "observation
    # grid is missing the 60 Hz orientations" as reasons products failed. Those
    # are family='review_rejection' - findings about the RECORD - and mixing the
    # two families is the specific error the taxonomy was built to prevent.
    (10, "reason", "Which products failed the standard, and why?",
     ["CE_LIMIT_EXCEEDED", "RS_MALFUNCTION", "SURGE_DAMAGE"],
     ["LISN calibration", "observation grid is missing", "photographs for the DC port",
      "none", "no failures"], False),

    (11, "triple", "A datasheet was rejected because a calibration date was missing. "
                   "What did the engineer change afterwards?",
     ["deviation", "measurement_uncertainty"], [], False),

    # TRAP: datasheet_revision.status is 'Draft' on all nine rows, including the
    # rejected ones. Answering from it yields "none were rejected".
    (12, "trap", "Which datasheet revisions were rejected?",
     ["CE"], ["none were rejected", "no revisions were rejected", "there are no rejected"],
     False),

    # Eight datasheets used an instrument whose inventory copy is overdue, across
    # four distinct instruments. The first run answered "two tests", which my
    # expectation graded merely INCOMPLETE because I had listed names and no
    # count - so the wrong NUMBER slipped through as a missing name.
    #
    # The cause is the fan-out this join is warned about: datasheet_equipment
    # joins `equipment` BY NAME, "BNC Cable" matches two inventory rows AND
    # appears twice on the datasheet, so one real usage becomes four joined rows.
    # Any count off that join is wrong in both directions at once. The wrong
    # counts are named here so the trap cannot pass silently again.
    (13, "cross", "Which instruments were used on a test while out of calibration, "
                  "and on how many datasheets?",
     ["BNC Cable", "Signal Generator", "8"], ["two tests", "2 tests"], False),

    # TRAP: two instruments tie at 6, and only 9 of 10 datasheets record equipment
    # at all - so a bare "most used" ranking overstates its own basis.
    (14, "trap", "Which instrument is used on the most datasheets?",
     ["Field Probe", "Signal Generator"], [], False),

    (15, "double", "How many datasheets has each engineer recorded?",
     ["engineer1", "Kondababu", "Krishna Muthangi", "3"], [], False),
]


def grade(answer, must, must_not):
    low = (answer or "").lower()
    missing = [m for m in must if m.lower() not in low]
    forbidden = [m for m in must_not if m.lower() in low]
    return missing, forbidden


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    import mysql_config  # noqa: F401 - loads .env
    from nlp_search import orchestrator

    cfg = mysql_config.config["default"]
    params = {"host": cfg.MYSQL_HOST, "port": int(cfg.MYSQL_PORT),
              "user": cfg.MYSQL_USER, "password": cfg.MYSQL_PASSWORD,
              "database": cfg.MYSQL_DATABASE}

    only = {int(a) for a in sys.argv[1:] if a.isdigit()}
    right = wrong = declined = 0
    rows = []

    for cid, shape, question, must, must_not, ok_refuse in CASES:
        if only and cid not in only:
            continue
        t0 = time.time()
        try:
            res = orchestrator.answer(question, params)
        except Exception as exc:  # noqa: BLE001
            res = {"success": False, "message": "raised %s" % exc}
        secs = time.time() - t0

        print("=" * 78)
        print("#%-2d [%s] %s" % (cid, shape, question))
        print("=" * 78)
        if not res.get("success"):
            print("  FAILED: %s" % res.get("message"))
            declined += 1
            rows.append((cid, shape, "declined", secs, 0))
            continue

        answer = res.get("answer") or ""
        print(answer.strip()[:1100])
        grounding = (res.get("grounding") or {}).get("verdict")
        tokens = (res.get("tokens") or {}).get("total") or 0
        missing, forbidden = grade(answer, must, must_not)

        if forbidden:
            verdict = "WRONG (said %s)" % ", ".join(repr(f) for f in forbidden)
            wrong += 1
        elif missing:
            # Missing a required fact without asserting a wrong one: the reply is
            # incomplete rather than false. Counted with the declines, because
            # the user still cannot get the fact from it.
            verdict = "INCOMPLETE (no %s)" % ", ".join(repr(m) for m in missing)
            declined += 1
        else:
            verdict = "right"
            right += 1

        print("\n  -> %s" % verdict)
        print("  route=%s grounding=%s %.0fs %s tok"
              % (res.get("route"), grounding, secs, tokens))
        for sql in (res.get("sql") or [])[:3]:
            s = sql.get("sql") if isinstance(sql, dict) else str(sql)
            print("  SQL: %s" % " ".join(str(s).split())[:200])
        rows.append((cid, shape, verdict, secs, tokens))
        print()

    total = right + wrong + declined
    print("=" * 78)
    print("SUMMARY  %d cases" % total)
    print("=" * 78)
    for cid, shape, verdict, secs, tok in rows:
        print("  #%-2d %-10s %-56s %3.0fs" % (cid, shape, verdict[:54], secs))
    print()
    print("  right                        %d/%d" % (right, total))
    print("  WRONG AND CONFIDENT          %d      <- the number that matters" % wrong)
    print("  declined / incomplete        %d" % declined)
    return 0 if not wrong else 1


if __name__ == "__main__":
    sys.exit(main())
