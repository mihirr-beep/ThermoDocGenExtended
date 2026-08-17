#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Measure intent routing: which worker does a question go to, and is that right?

    python tools_routing_eval.py            # score every case
    python tools_routing_eval.py -v         # ...and show the scores behind each

WHY THIS IS A DETERMINISTIC TEST AND NOT AN LLM EVAL
----------------------------------------------------
Routing is a pure function of the question and the generated catalog. It costs
nothing to run, so it can be run on every change - unlike nlp_search.evals,
which spends real tokens and takes minutes. The two measure different things:
this says "did the question reach a worker that can SEE the answer", evals says
"was the answer right". A question routed to a worker whose allowlist excludes
the tables it needs cannot be answered correctly no matter how good the model
is - it comes back as a confident absence, which is the worst shape of wrong.

READ THIS BEFORE ADDING A CASE
------------------------------
A case is not "the answer I want to this question". It is "the worker that owns
the tables holding this answer". Add cases when you find a question that reaches
the wrong worker, and set `want` from the schema - which table has the data -
not from what would make the number come out right. `None` means no single
worker owns it and the orchestrator should plan; that is a correct outcome, not
a failure, and the cross-domain block below exists to keep it that way.
"""
import sys

sys.path.insert(0, ".")

from nlp_search import intent

# (question, expected domain or None)
CASES = [
    # -- requests: what the customer asked for -----------------------------
    ("Give me cable information for the product Genpure UV xCAD plus WM", "requests"),
    ("How many of our cables are shielded versus unshielded?", "requests"),
    ("What accessories were declared for Smart2pure 6UV?", "requests"),
    ("What is the AC voltage range and rated power for Smart2pure 6UV?", "requests"),
    ("What supply voltage and frequency should be tested for job IEC-EMC-004?", "requests"),
    ("List every job raised in June", "requests"),
    ("Which requests are still waiting for approval?", "requests"),
    ("What product standards were declared on TCO IEC-EMC-007?", "requests"),
    ("How many test samples did the requester send for IEC-EMC-005?", "requests"),
    ("Which decision rules were chosen for job IEC-EMC-004?", "requests"),

    # -- datasheets: what was measured -------------------------------------
    ("How many CE datasheets does Krishna Gonela have?", "datasheets"),
    ("What ambient temperature was recorded on the ESD test?", "datasheets"),
    ("Which datasheets are still in draft?", "datasheets"),
    ("What was the coupling method used for the CE test?", "datasheets"),
    ("Show me the observation grid for the surge test on IEC-EMC-004", "datasheets"),
    ("What result did the harmonic test get?", "datasheets"),

    # -- inventory: the lab's instruments ----------------------------------
    ("Which equipment is due for maintenance next week?", "inventory"),
    ("Which instruments are out of calibration?", "inventory"),
    ("What is the asset id of the LISN?", "inventory"),
    ("Which equipment has an EOU status of EOU?", "inventory"),
    ("When was the spectrum analyser last calibrated?", "inventory"),

    # -- schedule: who and when --------------------------------------------
    ("Who is the peer reviewer on the CE test for IEC-EMC-004?", "schedule"),
    ("Which tests are scheduled for next week?", "schedule"),
    ("What is each engineer's current workload?", "schedule"),
    ("Which planner entries were cancelled?", "schedule"),

    # -- genuinely cross-domain: the orchestrator should plan ---------------
    # These are NOT failures. A worker cannot see outside its allowlist, so a
    # question spanning two of them must go to the orchestrator.
    ("Which tests were requested but never scheduled?", None),
    ("Compare what was requested against what was recorded for IEC-EMC-004", None),
    ("Which jobs are behind, and who is on them?", None),

    # NOT cross-domain, though it reads that way. The inventory slice was given
    # datasheet_equipment specifically so one worker holds both the used-on-a-
    # test list and the calibration dates. Written as None first, from the
    # phrasing rather than from the schema, which is the mistake this file's
    # docstring warns about.
    ("Was any equipment used on a test out of calibration?", "inventory"),

    # -- insight: the datasheets worker owns analyse_history ---------------
    ("Why did the Genpure keep failing its RE test?", "datasheets"),
    ("What changed between the two campaigns for Smart2pure 6UV?", "datasheets"),
    ("What is the most common reason a datasheet gets sent back?", "datasheets"),
]


def main():
    verbose = "-v" in sys.argv
    width = max(len(q) for q, _ in CASES)
    ok = 0
    wrong, declined = [], []
    print("%-*s  %-11s %-11s" % (width, "QUESTION", "EXPECTED", "GOT"))
    print("-" * (width + 26))
    for q, want in CASES:
        got = intent.single_domain(q)
        good = got == want
        ok += good
        flag = "" if good else ("  <- WRONG WORKER" if got else "  <- declined")
        print("%-*s  %-11s %-11s%s" % (width, q, want or "-", got or "-", flag))
        if not good:
            (wrong if got else declined).append((q, want, got))
        if verbose:
            scores = intent.domain_scores(q)
            print("%-*s  %s" % (width, "", {k: round(v, 2) for k, v in
                                            sorted(scores.items(), key=lambda kv: -kv[1])}))

    print()
    print("  %d/%d correct" % (ok, len(CASES)))
    # Separated on purpose. Sending a question to a worker that cannot see the
    # answer produces a confident absence; declining only costs the
    # orchestrator's extra turns. They are not the same defect.
    print("  wrong worker (produces a confident absence): %d" % len(wrong))
    print("  declined to a worker that should have owned it (costs turns): %d" % len(declined))
    for q, want, got in wrong:
        print("     %s -> %s, wanted %s" % (q[:58], got, want))
    return 0 if not wrong else 1


if __name__ == "__main__":
    sys.exit(main())
