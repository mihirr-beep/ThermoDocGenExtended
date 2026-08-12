# -*- coding: utf-8 -*-
"""How much genuinely useful insight can this answer? Measured by category.

WHY A SECOND SUITE
------------------
tools_insight_eval.py asks the seven questions the primitives were built for. It
scored 7/8, and that number is close to meaningless on its own: I wrote the tool
and then tested the tool against its own design. Any system passes the exam it
set itself.

This suite is the honest version. It mixes:

  supported   the seven shapes, reworded the way someone would actually say them
              - no table names, no codes, no "analyse_history"
  real        the same questions against the REAL products, where result holds an
              IEC performance criterion instead of PASS/FAIL and no failure has
              a classified reason
  aggregate   lab-wide questions no primitive was written for - most common
              failure mode, anything never passing. Can it compose, or does it
              guess?
  review      the paperwork axis, where cohort/resolved_how do not reach
  honest      questions the data CANNOT answer. A refusal is the correct answer
              and scores as a pass; a confident reply scores zero however good it
              reads

That last category is the one that makes the percentage mean anything. A system
answering everything it was built for and inventing the rest is worse than one
answering two thirds and saying so, because the first cannot be trusted without
checking and the second can.

SCORING
-------
Deliberately strict, because loose scoring has flattered this system twice
already - once with a tool-use check that could not fail, once by passing a
verifier evidence-dump as an answer. Facts must appear; forbidden claims must
not; an answer the verifier gave up on is not an answer.

Ground truth was read out of the database before the questions were written, not
after seeing what came back.

    python tools_insight_coverage.py
    python tools_insight_coverage.py -k aggregate
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# A verifier fallback. The rows in it are exact, so it contains every value the
# question was about and passes any keyword check - but the user received a table
# instead of an answer.
_GAVE_UP = ("could not verify", "i have no rows to show you",
            "here is what the database actually returned")

# Ways of saying "the data does not support this". Any one of them, on a question
# whose honest answer is a refusal, is a pass.
_LIMITS = ("not recorded", "no record", "not captured", "not stored", "no field",
           "not available", "cannot", "can not", "can't", "unable", "does not "
           "record", "no data", "nothing in the", "not tracked", "no such",
           "not something", "do not have", "does not exist", "no information",
           "not possible", "would need", "not held")

# (id, category, question, must_have, must_not_have)
# must_have: each entry is a tuple of alternatives - any one satisfies it.
CASES = [
    # ---------------------------------------------------------------- supported
    ("kept-failing", "supported",
     "The DEMO Aurora Centrifuge C5 kept failing. What was actually wrong with it?",
     [("0.72", "0.720"), ("56", "limit"), ("conducted", "CE", "emission")],
     ["radiated emission", "ESD"]),

    # "3 attempts before it passed" and "4 campaigns in total" are both true.
    # The first expectation demanded 4 and failed a correct answer.
    ("how-many-attempts", "supported",
     "How many attempts did the DEMO Aurora Centrifuge C5 need before it passed?",
     [("3", "three", "4", "four")],
     ["never passed", "still failing"]),

    ("came-down", "supported",
     "Show me how the emissions on the DEMO Aurora Centrifuge C5 came down "
     "across its tests.",
     # The trend is the answer. Demanding the passing value too failed an answer
     # that gave 60.8 -> 60.5 -> 57.9 and then reported the pass as criterion A.
     [("60.8",), ("57.9", "52.6")],
     ["no measurements", "not recorded"]),

    ("vega-fix", "supported",
     "What did the DEMO Vega Incubator V2 have fitted to get it through?",
     [("choke",), ("3.3",)],
     ["Schaffner", "watchdog"]),

    ("same-reason", "supported",
     "Did the DEMO Orion Analyzer O9 and the DEMO Aurora Centrifuge C5 fail for "
     "the same reason?",
     [("CE_LIMIT_EXCEEDED", "conducted emission"), ("yes", "same", "both")],
     ["different reasons", "unrelated"]),

    # --------------------------------------------------------------------- real
    ("real-history", "real",
     "Give me the testing history of the Full-Scope EMC Sample Unit.",
     [("IEC-EMC-01",), ("34", "campaign")],
     ["DEMO-EMC", "no history"]),

    ("real-why-failed", "real",
     "Why did the Full-Scope EMC Sample Unit fail on IEC-EMC-010?",
     [("CE", "RE", "HARMONIC", "conducted", "radiated"),
      # no failure_reason_code exists on any real campaign, and no CE breach
      # rows either - the honest answer has to say the reason is not recorded
      ("not recorded", "no reason", "no classified", "not captured",
       "no failure reason", "does not record", "no measurement")],
     ["CE_LIMIT_EXCEEDED", "0.72"]),

    # ---------------------------------------------------------------- aggregate
    ("most-common-mode", "aggregate",
     "What is the most common reason products fail in this lab?",
     [("CE_LIMIT_EXCEEDED", "conducted emission"), ("6", "3")],
     ["EFT_RESET is the most", "no failures"]),

    ("never-passed", "aggregate",
     "Are there any products that have failed and never passed?",
     [("no", "none", "every", "all "),],
     ["Aurora has never passed", "Orion has never passed"]),

    ("how-many-products-ce", "aggregate",
     "How many different products have failed conducted emission?",
     [("3", "three"), ("Aurora", "Orion", "Vega")],
     ["1 product", "only one"]),

    ("mode-most-products", "aggregate",
     "Which failure mode affects the largest number of different products?",
     [("CE_LIMIT_EXCEEDED", "conducted emission"), ("3", "three")],
     ["EFT_RESET affects the most"]),

    # ------------------------------------------------------------------- review
    ("why-sent-back", "review",
     "Why do datasheets get sent back in peer review in this lab?",
     [("CAL_EXPIRED", "calibration"), ("MISSING_PHOTO", "photo")],
     ["CE_LIMIT_EXCEEDED is why", "no rejections"]),

    # 9 rejection EVENTS across 3 DISTINCT datasheets. The question asks how many
    # datasheets, so 3 is right - and the first version of this case demanded 9
    # and failed a correct answer that had even stated its own basis. Ground
    # truth read carelessly is indistinguishable from a model error.
    ("how-many-rejected", "review",
     "How many datasheets have been rejected in peer review?",
     [("3", "three")],
     ["none", "no datasheets have been rejected"]),

    # ------------------------------------------------------------------- honest
    ("which-component", "honest",
     "Which internal component caused the DEMO Aurora Centrifuge C5's emission "
     "failure?",
     [],
     ["the switching supply caused", "the motor driver caused",
      "the power board caused"]),

    ("will-it-pass", "honest",
     "Will the DEMO Nova Sampler N1 pass its next test?",
     [],
     ["yes it will pass", "it will pass", "will likely pass", "should pass"]),

    ("rework-hours", "honest",
     "How many rework hours did the DEMO Aurora Centrifuge C5 failures cost us?",
     [],
     ["hours of rework", "40 hours", "cost us"]),

    ("root-cause", "honest",
     "What was the confirmed root cause of the DEMO Aurora Centrifuge C5's "
     "conducted emission failure?",
     [],
     ["the choke was the root cause", "root cause was the common-mode choke",
      "caused by the lack of a choke", "confirmed root cause was"]),
]


def score(answer, category, must, must_not):
    """(missing, wrong) - empty both ways is a pass."""
    text = (answer or "").lower()
    if any(p in text for p in _GAVE_UP):
        return [("a written answer - got a raw evidence dump",)], []
    wrong = [b for b in must_not if b.lower() in text]
    if category == "honest":
        # The bar is a stated limitation, not silence. An answer that neither
        # refuses nor fabricates - "here are the modifications" to "what caused
        # it" - is evasion, and reads to a user as though it answered.
        if not any(p in text for p in _LIMITS):
            return [("an explicit statement that the data cannot answer this",)], wrong
        return [], wrong
    missing = [alts for alts in must if not any(a.lower() in text for a in alts)]
    return missing, wrong


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", "--filter", default="")
    ap.add_argument("--model", default="gpt-5-nano")
    args = ap.parse_args()

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

    f = args.filter.lower()
    cases = [c for c in CASES if f in c[0].lower() or f in c[1].lower()]
    results = []
    with flask_app.app_context():
        for cid, cat, question, must, must_not in cases:
            print("=" * 78)
            print("[%s / %s]" % (cat, cid))
            print("Q: %s" % question)
            res = orchestrator.answer(question, db_params, verify_answer=True)
            if not res.get("success"):
                print("   NO ANSWER: %s" % res.get("message"))
                results.append((cat, cid, False, "errored"))
                continue
            ans = res.get("answer") or ""
            route = res.get("route") or ""
            print("   route=%-24s primitives ran: %s"
                  % (route, "yes" if "insights" in route else "no"))
            print("   A: " + ans.replace("\n", "\n      ")[:1400])
            missing, wrong = score(ans, cat, must, must_not)
            if missing:
                print("   MISSING : %s" % "; ".join(" / ".join(a) for a in missing))
            if wrong:
                print("   WRONG   : %s" % ", ".join(wrong))
            ok = not missing and not wrong
            results.append((cat, cid, ok, "" if ok else "missing/wrong"))
            print("   => %s" % ("PASS" if ok else "FAIL"))

    print("=" * 78)
    cats = {}
    for cat, cid, ok, _ in results:
        c = cats.setdefault(cat, [0, 0])
        c[1] += 1
        c[0] += bool(ok)
    print("BY CATEGORY")
    for cat in ("supported", "real", "aggregate", "review", "honest"):
        if cat in cats:
            got, tot = cats[cat]
            print("   %-12s %d/%d  %3.0f%%" % (cat, got, tot, 100.0 * got / tot))
    tot_ok = sum(1 for _, _, ok, _ in results if ok)
    print("   %-12s %d/%d  %3.0f%%" % ("OVERALL", tot_ok, len(results),
                                       100.0 * tot_ok / max(1, len(results))))
    print("\nFAILED: %s" % (", ".join(cid for _, cid, ok, _ in results if not ok) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
