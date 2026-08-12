# -*- coding: utf-8 -*-
"""Run insight questions through the real chatbot and score what comes back.

These are the SHAPES the manager asked for, not a fixed question list: history,
why-it-failed, what-changed, which-frequencies-improved, what-was-fitted,
what-was-common, and has-this-happened-elsewhere. The phrasing is deliberately
the way a person would say it - no table names, no codes - because the point is
whether the pipeline gets there from ordinary language.

SCORING
-------
Each case lists facts that are TRUE of the seeded corpus and must appear, and
some that are true-sounding and must NOT. A wrong number stated confidently is
the failure this whole design exists to prevent, so a case that invents one
fails outright however well it reads.

The last case is the one worth watching. It asks why a RECORD was rejected, and
the same product also failed the standard for an unrelated reason. An answer
that reports the emission failure has confused the two axes - fluently, and
wrongly.

    python tools_insight_eval.py            # all cases
    python tools_insight_eval.py -k aurora  # just the ones matching
"""
import argparse
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# (id, question, must_contain, must_not_contain)
# must_contain entries are alternatives: any one of a tuple satisfies it.
CASES = [
    ("history",
     "Give me the testing history of the DEMO Aurora Centrifuge C5.",
     [("DEMO-EMC-201", "201"), ("DEMO-EMC-204", "204"),
      ("fail",), ("pass",)],
     ["ESD", "no history", "not found"]),

    ("why-failed",
     "Why did the DEMO Aurora Centrifuge C5 fail its first three tests?",
     [("0.72", "0.720"), ("conducted", "emission", "limit"),
      ("4.8", "60.8")],
     ["radiated emission at 230", "ESD"]),

    ("what-changed",
     "What changed between the last failed test and the first successful test "
     "for the DEMO Aurora Centrifuge C5?",
     [("choke",), ("DEMO-EMC-203", "203"), ("DEMO-EMC-204", "204")],
     ["nothing changed", "no difference"]),

    ("frequencies",
     "Which frequencies showed the greatest improvement between the failed and "
     "the successful test for the DEMO Aurora Centrifuge C5?",
     [("0.72", "0.720"), ("5.3", "5.30"), ("1.15", "1.150")],
     ["230 MHz", "12.6 MHz showed the greatest"]),

    ("modifications",
     "Which modifications were introduced before the DEMO Aurora Centrifuge C5 "
     "first passed?",
     [("choke",), ("Y-capacitor", "Y capacitor", "2.2")],
     ["firmware", "watchdog"]),

    ("cohort",
     "Have any other products experienced the same failure pattern as the "
     "DEMO Aurora Centrifuge C5?",
     [("Orion",), ("Vega",)],
     ["Lyra", "Nova", "Pavo"]),

    ("what-fixed-it",
     "Across all the products that failed conducted emission, what did they "
     "change to make them pass?",
     [("choke",), ("filter", "Schaffner")],
     ["watchdog", "debounce"]),

    # The discrimination case: the RECORD was rejected for a missing
    # photograph; the UNIT separately failed the emission limit. Reporting the
    # emission failure here is the confident wrong answer.
    ("two-axes",
     "Why was the datasheet for the DEMO Orion Analyzer O9 rejected in peer "
     "review?",
     [("photograph", "photo"),],
     ["exceeded the limit as the reason the record was rejected"]),
]


# An answer the verifier gave up on and replaced with a table of rows. It is not
# a wrong answer - the rows are exact - but it is not an ANSWER, and it must not
# score as one: the evidence dump contains every value the question was about,
# so a keyword check passes it every time. why-failed "passed" this way, with
# the user receiving a field/value listing instead of a sentence.
_GAVE_UP = ("could not verify", "i have no rows to show you",
            "here is what the database actually returned")


def score(answer, must, must_not):
    text = (answer or "").lower()
    if any(p in text for p in _GAVE_UP):
        return [("a written answer - got a raw evidence dump",)], []
    missing = [alts for alts in must
               if not any(a.lower() in text for a in alts)]
    present = [bad for bad in must_not if bad.lower() in text]
    return missing, present


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", "--filter", default="")
    ap.add_argument("--model", default="gpt-5-nano")
    args = ap.parse_args()

    # The answers contain whatever the model wrote - arrows, dashes, degree
    # signs - and this prints to a cp1252 console on Windows. An answer with a
    # "->" arrow in it killed the run at case four, losing the three cases
    # behind it and reporting nothing at all.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    os.environ.setdefault("NLP_SEARCH_MODEL", args.model)
    os.environ.setdefault("NLP_WORKER_MODEL", args.model)

    import app as app_module
    from nlp_search import orchestrator

    flask_app = app_module.create_app("default")
    cfg = flask_app.config
    db_params = {"host": cfg.get("MYSQL_HOST"), "port": cfg.get("MYSQL_PORT"),
                 "user": cfg.get("MYSQL_USER"), "password": cfg.get("MYSQL_PASSWORD"),
                 "database": cfg.get("MYSQL_DATABASE")}

    cases = [c for c in CASES if args.filter.lower() in c[0].lower()]
    passed = 0
    with flask_app.app_context():
        for cid, question, must, must_not in cases:
            print("=" * 78)
            print("[%s] %s" % (cid, question))
            res = orchestrator.answer(question, db_params, verify_answer=True)
            if not res.get("success"):
                print("   FAILED TO ANSWER: %s" % res.get("message"))
                continue
            ans = res.get("answer") or ""
            route = res.get("route") or ""
            print("   route=%s" % route)
            # The ROUTE, not the step list. Steps only hold the orchestrator's
            # own calls - a worker's internal tool calls never appear there - so
            # searching them for "analyse_history" matched the sub-question text
            # (which names the tool, because the orchestrator is told to ask for
            # it) and reported "used: yes" for six questions that never called
            # it once. A check that cannot fail is not a check.
            # insights.run is the only thing that writes a ledger entry under
            # the "insights" worker, so the label is proof it actually ran.
            used = "insights" in route
            print("   analyse_history actually ran: %s" % ("yes" if used else "NO"))
            g = res.get("grounding") or {}
            if g:
                print("   grounding: %s" % {k: v for k, v in g.items()
                                            if k in ("verdict", "ok", "stripped",
                                                     "unsupported")})
            print("   ---")
            for line in ans.splitlines():
                print("   " + line)
            print("   ---")
            missing, bad = score(ans, must, must_not)
            if missing:
                print("   MISSING: %s" % "; ".join(" or ".join(a) for a in missing))
            if bad:
                print("   WRONG   : contains %s" % ", ".join(bad))
            ok = not missing and not bad
            passed += bool(ok)
            print("   => %s" % ("PASS" if ok else "FAIL"))
    print("=" * 78)
    print("%d / %d" % (passed, len(cases)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
