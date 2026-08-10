# -*- coding: utf-8 -*-
"""Measured evaluation of the NL search.

    python -m nlp_search.evals              # run everything
    python -m nlp_search.evals --only krishna_count
    python -m nlp_search.evals --repeat 3   # non-determinism shows up here

Every guardrail in this package started as a guess that a live question proved
wrong, and each fix risked breaking something already working - tightening the
grounding check to catch an invented count made it withhold a correct one two
runs later. Tuning against anecdotes oscillates. This gives the numbers to tune
against.

The headline metric is NOT accuracy. It is **hallucination rate**: how often a
confident answer is wrong. An assistant that refuses half the time and is never
wrong is useful; one that answers everything and is wrong occasionally is not,
because nobody can tell which answers to trust. So every case declares what
MUST appear, what must NOT, and whether refusing is an acceptable outcome, and
the summary counts wrong-and-confident separately from declined.

Expectations are written against the seeded database. Re-check them if the
data changes: `must_not` clauses in particular encode facts about the current
rows (that no result is 'Fail', that only two people are called Krishna).
"""
import argparse
import io
import os
import re
import sys
import time

# (id, question, expectations)
#   must      - every one of these must appear in the answer (case-insensitive)
#   must_not  - none of these may appear; each is a specific known-wrong answer
#   any_of    - at least one must appear
#   refusal_ok- True when declining / asking for clarification is a valid result
#   kind      - the intent the router should assign
CASES = [
    # -- straightforward retrieval -------------------------------------------
    dict(id="request_count", kind="data",
         q="How many EMC test requests are in the system?",
         must=["9"], must_not=["21", "12"]),
    dict(id="esd_engineer", kind="data",
         q="Which engineer ran the ESD test, and what result was recorded?",
         must=["krishna gonela"], any_of=["result", "a"], must_not=["fail"]),
    dict(id="ce_coupling_value", kind="data",
         q="What coupling method was used on the CE datasheets?",
         must=["lisn"], must_not=["custom_spec"]),

    # -- aggregation ----------------------------------------------------------
    dict(id="datasheet_breakdown", kind="data",
         q="Break down the datasheets by test code and status.",
         must=["ce", "approved", "draft"], must_not=["rejected"]),
    dict(id="krishna_count", kind="data",
         q="How many CE datasheets has Krishna Gonela filled, and how many of "
           "them are marked as approved?",
         must=["2"], must_not=["11", "3 ce", "0 ce"], refusal_ok=True),

    # -- ambiguity: asking is the right answer -------------------------------
    dict(id="ambiguous_krishna", kind="data",
         q="How many CE datasheets has Krishna filled?",
         must=["gonela", "muthangi"], refusal_ok=True,
         note="two Krishnas - must ask, not pick"),

    # -- absence: the answers that must not be invented -----------------------
    dict(id="no_such_person", kind="data",
         q="What tests has Zaphod Beeblebrox run?",
         any_of=["no person", "not in the database", "no such", "does not exist",
                 "no record"],
         must_not=["0 tests", "has run 0"], refusal_ok=True),
    dict(id="phantom_status", kind="data",
         q="How many CE datasheets are marked as Rejected?",
         any_of=["no rejected", "not a", "no such status", "there is no",
                 "approved", "draft"],
         refusal_ok=True, note="Rejected is not a status - say so, do not report 0"),
    dict(id="failure_reasons", kind="data",
         q="How many tests failed last month, and why did they fail?",
         must_not=["failed because", "due to a fault"], refusal_ok=True,
         note="no failures and no failure-reason field; must not invent one"),

    # -- schema questions: the class that produced the coupling-method bug ----
    dict(id="where_coupling", kind="schema",
         q="In test request object where is the coupling method value in DB for CE",
         must=["datasheet_ce.coupling_method"],
         must_not=["custom_spec", "iec_emc_request_test_ce.cables"],
         note="the original failure: it pointed at custom_spec"),
    dict(id="where_absent_field", kind="schema",
         q="Which column stores the customer satisfaction score for a job?",
         any_of=["not recorded", "no column", "does not exist", "not stored",
                 "not captured"],
         must_not=["custom_spec", "remarks"], refusal_ok=True),
    dict(id="where_rejection_reason", kind="schema",
         q="Is there a field for the rejection reason of a request?",
         must=["rejection_reason"], must_not=["no field", "not recorded"]),

    # -- scope ----------------------------------------------------------------
    dict(id="off_topic", kind="data",
         q="What is the weather in Bangalore today?",
         any_of=["lab", "cannot", "can only", "outside"],
         must_not=["degrees", "sunny", "rain"], refusal_ok=True),
]

# --------------------------------------------------------------------------
# The suite that decides whether this feature is worth having
# --------------------------------------------------------------------------
# Nobody needs an assistant to run "SELECT COUNT(*) FROM datasheet" - anyone
# with database access can do that faster themselves. The feature earns its
# place on questions that take two or three joins, a vocabulary the schema
# does not spell consistently, or a comparison between what was asked for and
# what was recorded. Those are also the questions where a wrong answer does
# real damage, because the asker cannot check it at a glance.
#
# So this suite is deliberately the hard half, and it is graded on the metric
# that matters: WRONG is counted separately from REFUSED. An assistant that
# declines a question it cannot do is usable. One that answers confidently and
# is sometimes wrong is worse than nothing, because a lab cannot tell which
# answers to trust and the wrong ones end up in reports.
#
# Verified against the live database on 2026-08-07. Re-check after data
# changes: `must_not` clauses encode facts about the current rows.
USEFUL_CASES = [
    # -- completeness: the "where do we actually stand" questions -----------
    dict(id="job_completeness", kind="data",
         q="For each job, how many tests were requested and how many actually "
           "have a datasheet recorded? I want to see which jobs are behind.",
         must=["iec-emc-004"], any_of=["10", "11"],
         must_not=["all jobs are complete", "no jobs are behind"],
         refusal_ok=True,
         note="req/rec per job: -001 11/0, -002 12/0, -003 11/0, -004 11/10, "
              "-005 11/1, -006 1/1, -007 11/0, -008 11/0, -009 1/0"),

    dict(id="never_scheduled", kind="data",
         q="Are there tests that were requested on a job but never even got "
           "scheduled in the planner?",
         any_of=["63", "yes", "never"],
         must_not=["all scheduled", "every requested test", "no such tests"],
         refusal_ok=True,
         note="63 requested tests have no planner entry once test codes are "
              "normalised. Getting this wrong means over- or under-counting "
              "by the four codes that are spelled differently."),

    # -- compliance: the questions with consequences ------------------------
    dict(id="overdue_kit_on_approved", kind="data",
         q="Has any equipment that is past its calibration date been used on a "
           "datasheet we have already approved?",
         any_of=["yes", "19", "current probe", "attenuator", "past"],
         must_not=["no equipment", "none of the equipment", "all equipment is "
                   "within calibration"],
         refusal_ok=True,
         note="19 distinct items. This is the single most valuable question in "
              "the set - an approved result produced on out-of-calibration kit "
              "is a finding in an audit."),

    dict(id="self_review", kind="data",
         q="Is there any test where the person who peer reviewed the datasheet "
           "is the same person who ran the test?",
         any_of=["no", "none", "not any"], must_not=["yes", "krishna gonela is"],
         refusal_ok=True,
         note="Zero. A clean compliance check - it must say so positively, and "
              "must have actually looked rather than assuming."),

    dict(id="approved_no_result", kind="data",
         q="Are there any datasheets that were approved but have no result "
           "recorded on them?",
         any_of=["voltagedips", "1", "yes"],
         must_not=["all approved datasheets have", "no such datasheet"],
         refusal_ok=True,
         note="Exactly one: VOLTAGEDIPS on TFS-EMC-2026-002."),

    # -- data the user cannot see any other way -----------------------------
    dict(id="ce_measurements", kind="data",
         q="What are the measurement values recorded on the CE datasheet for "
           "job TFS-EMC-2026-001?",
         must=["0.212", "48.2"], must_not=["not available", "cannot retrieve"],
         refusal_ok=False,
         note="Line and Neutral grids, 3 rows each, only reachable via "
              "read_grid - they live in a JSON column."),

    dict(id="non_a_observations", kind="data",
         q="Show me any observation across all the tests that was not "
           "criterion A.",
         any_of=["none", "no ", "all", "only"], must_not=["criterion b", "criterion c"],
         refusal_ok=True,
         note="All 143 observation cells are 'A'. Must say that rather than "
              "reporting an empty filter result."),

    # -- cross-domain: what the domain split used to make impossible --------
    dict(id="equipment_by_usage", kind="data",
         q="Which instruments do we use most across our tests, and are any of "
           "them due for calibration?",
         any_of=["inrush", "pulse verification", "compact nx"],
         must_not=["no equipment is used"], refusal_ok=True,
         note="Top three tie at 3 uses each. Needs datasheet_equipment joined "
              "to equipment - two different domains."),
]

_REFUSAL_VERDICTS = {"clarify", "no-evidence", "unsupported"}


def _norm(text):
    return re.sub(r"\s+", " ", (text or "")).lower()


def _says(answer, phrase):
    """Does the answer contain this phrase, as words rather than as letters?

    Substring matching graded three correct answers as wrong: must_not=["no"]
    fires on "there is 1 approved datasheet that has no result recorded",
    which is exactly the right answer. Short phrases need word boundaries or
    the suite measures its own matcher instead of the system.
    """
    p = phrase.lower().strip()
    if not p:
        return False
    if len(p) <= 4 or " " not in p:
        return re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(p), answer) is not None
    return p in answer


def _score(case, res):
    """(outcome, reasons) where outcome is pass / wrong / declined / error."""
    if not res.get("success"):
        return "error", [res.get("message", "no message")[:120]]

    answer = _norm(res.get("answer"))
    verdict = (res.get("grounding") or {}).get("verdict", "")
    reasons = []

    # A wrong statement is the failure that matters, so check must_not first -
    # it stays a failure even when the system declined afterwards.
    for bad in case.get("must_not", ()):
        if _says(answer, bad):
            reasons.append("says %r" % bad)
    if reasons:
        return "wrong", reasons

    missing = [m for m in case.get("must", ()) if not _says(answer, m)]
    any_of = case.get("any_of") or []
    has_any = (not any_of) or any(_says(answer, a) for a in any_of)

    if not missing and has_any:
        return "pass", []

    if missing:
        reasons.append("missing %s" % ", ".join(repr(m) for m in missing))
    if not has_any:
        reasons.append("none of %s" % ", ".join(repr(a) for a in any_of))

    # Declining is a legitimate outcome where the case says so - it is not a
    # right answer, but it is not a wrong one either, and the distinction is
    # the whole point of the exercise.
    if case.get("refusal_ok") and verdict in _REFUSAL_VERDICTS:
        return "declined", reasons
    return "wrong", reasons


def run(cases=None, repeat=1, out=None):
    out = out or io.StringIO()
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import app as app_module
    from . import intent, orchestrator

    flask_app = app_module.create_app("default")
    cfg = flask_app.config
    db_params = {"host": cfg.get("MYSQL_HOST"), "port": cfg.get("MYSQL_PORT"),
                 "user": cfg.get("MYSQL_USER"), "password": cfg.get("MYSQL_PASSWORD"),
                 "database": cfg.get("MYSQL_DATABASE")}

    # The whole run happens inside the app context. It used to close right
    # after the config was read, so audit.log_query() - which needs
    # db.session - failed silently on every single eval question. The audit
    # table is where the token and cost record lives, and it had none of the
    # hundred-odd queries the suite had already paid for.
    with flask_app.app_context():
        return _run_cases(cases or CASES, db_params, repeat, out)


def _run_cases(cases, db_params, repeat, out):
    from . import intent, orchestrator
    tally = {"pass": 0, "wrong": 0, "declined": 0, "error": 0}
    routing_misses, tokens, wall = 0, 0, 0.0
    rows = []

    for case in cases:
        for attempt in range(repeat):
            got_kind = intent.classify(case["q"])
            if case.get("kind") and got_kind != case["kind"]:
                routing_misses += 1
            t0 = time.time()
            res = orchestrator.answer(case["q"], db_params, user="evals")
            dt = time.time() - t0
            wall += dt
            tokens += (res.get("tokens") or {}).get("total", 0) or 0
            outcome, reasons = _score(case, res)
            tally[outcome] += 1
            label = case["id"] + ("" if repeat == 1 else "#%d" % (attempt + 1))
            rows.append((label, outcome,
                         (res.get("grounding") or {}).get("verdict", "-"),
                         got_kind, dt, reasons,
                         (res.get("answer") or "")[:160]))
            # Live, unbuffered, on stderr. A full suite takes ten minutes or
            # more, and the whole table used to be written only at the end -
            # so a run that was interrupted lost every result it had already
            # paid for.
            sys.stderr.write("  [%2d/%-2d] %-26s %-5s %5.1fs  %s\n"
                             % (len(rows), len(cases) * repeat, label,
                                outcome.upper(), dt, "; ".join(reasons)[:70]))
            sys.stderr.flush()

    total = sum(tally.values())
    out.write("%-26s %-9s %-12s %-9s %6s  %s\n"
              % ("case", "outcome", "grounding", "routed", "secs", "why"))
    out.write("-" * 110 + "\n")
    for cid, outcome, verdict, kind, dt, reasons, ans in rows:
        flag = {"pass": "ok  ", "wrong": "WRONG", "declined": "decl", "error": "ERR "}[outcome]
        out.write("%-26s %-9s %-12s %-9s %6.1f  %s\n"
                  % (cid, flag, verdict, kind, dt, "; ".join(reasons)))
        if outcome in ("wrong", "error"):
            out.write("%s> %s\n" % (" " * 28, _norm(ans)[:120]))

    out.write("\n" + "=" * 110 + "\n")
    out.write("%d case(s)   correct %d   declined %d   WRONG %d   error %d\n"
              % (total, tally["pass"], tally["declined"], tally["wrong"], tally["error"]))
    answered = tally["pass"] + tally["wrong"]
    out.write("hallucination rate (wrong / answered): %s\n"
              % ("%.0f%% (%d/%d)" % (100.0 * tally["wrong"] / answered,
                                     tally["wrong"], answered) if answered else "n/a"))
    out.write("answer rate (answered / total)       : %.0f%%\n"
              % (100.0 * answered / total if total else 0))
    out.write("routing misses: %d   tokens: %d   wall: %.0fs\n"
              % (routing_misses, tokens, wall))
    return tally, out


def main():  # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="Evaluate the NL search")
    ap.add_argument("--only", help="run one case by id")
    ap.add_argument("--repeat", type=int, default=1,
                    help="run each case N times (surfaces non-determinism)")
    ap.add_argument("--suite", choices=("basic", "useful", "all"), default="all",
                    help="basic = single-fact sanity; useful = the hard "
                         "questions that justify the feature")
    args = ap.parse_args()
    pool = {"basic": CASES, "useful": USEFUL_CASES,
            "all": CASES + USEFUL_CASES}[args.suite]
    cases = [c for c in pool if c["id"] == args.only] if args.only else pool
    if not cases:
        print("no case with id %r. Known: %s"
              % (args.only, ", ".join(c["id"] for c in CASES + USEFUL_CASES)))
        return 2
    tally, out = run(cases, repeat=args.repeat)
    sys.stdout.buffer.write(out.getvalue().encode("utf-8", "replace"))
    return 1 if (tally["wrong"] or tally["error"]) else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
