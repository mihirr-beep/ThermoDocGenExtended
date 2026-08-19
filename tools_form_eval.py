# -*- coding: utf-8 -*-
"""Ask the chatbot the form_json questions, and score what comes back.

    python tools_form_eval.py            # all
    python tools_form_eval.py 3 9        # only these

Spends tokens: each case is a full pipeline run. Prints cost per question.

The point is not whether it can do JSON gymnastics. form_json is hidden from the
model on purpose - it is a megabyte-class column and putting it in a prompt
achieves nothing - so most of these are UNANSWERABLE from the schema it can see.

That makes them the sharpest possible test, because the failure mode this whole
feature dies of is not "I cannot answer" but a confident answer to a question the
data cannot support. Every case below therefore scores three ways:

  ANSWERED    it produced the fact, and the fact is right
  DECLINED    it said it could not - correct, and cheap
  INVENTED    it produced a fact that is wrong

DECLINED is a pass here. INVENTED is the only real failure.
"""
import os
import sys
import time

CASES = [
    dict(
        n=1,
        q="which datasheets look barely filled in",
        truth="DEMO-EMC-301's RE sheet: 16 form fields and 499 bytes, against "
              "147 fields and 6086 bytes for a CE sheet. Nothing else is close.",
        reachable="No. Field COUNT is a property of form_json, which is hidden "
                  "from the model. Nothing in a column counts how much of a form "
                  "was filled.",
        wrong_if=["fully filled", "all datasheets are complete",
                  "no datasheets are incomplete"],
        right_if=["RE"],
    ),
    dict(
        n=2,
        q="which fields do engineers usually leave blank",
        truth="test_procedure_manual on 3 forms; then peer_reviewer_id, "
              "photo_caption, test_date and tested_by_date on 2 each.",
        reachable="No. A blank FIELD only exists inside the form; a NULL column "
                  "cannot say whether anybody was asked.",
        wrong_if=["no fields are left blank", "engineers fill every field"],
        right_if=["test_procedure_manual"],
    ),
    dict(
        n=3,
        q="are any of the observation grids only half filled in",
        truth="Not now. Every grid in datasheet_records is complete - but "
              "DEMO-EMC-304's indirect grid was 50% empty at revisions 1 and 2 "
              "and was corrected at revision 3.",
        reachable="No, and this is the interesting one. The projection DROPS "
                  "empty cells rather than storing blanks: datasheet_observation "
                  "holds 48 indirect rows for the fixed sheet and the mirror "
                  "holds 24 for the broken ones. The gap is a row count nobody "
                  "declares an expectation for, and the mirror is excluded from "
                  "the catalog anyway.",
        wrong_if=["all grids are half", "every grid is incomplete"],
        right_if=[],
    ),
    dict(
        n=4,
        q="what do engineers end up changing after a datasheet gets sent back",
        truth="deviation, on all 3 reworked datasheets - more than any "
              "measurement cell. Then the indirect-discharge cells ind_r5 to "
              "ind_r7 on DEMO-EMC-304.",
        reachable="Partly. review_history diffs whole form_json per datasheet, so "
                  "the per-sheet answer is reachable; the lab-wide ranking is not.",
        wrong_if=["nothing changed", "no changes were recorded"],
        right_if=["deviation"],
    ),
    dict(
        n=5,
        q="do the ambient conditions on our datasheets look sensible",
        truth="No. Both CE datasheets record 10 C and 10% RH, which is outside "
              "any plausible test environment. The rest sit at 23-24 C and "
              "46-48% RH.",
        reachable="YES. ambient_temperature and relative_humidity are projected "
                  "columns on datasheet.",
        wrong_if=["all conditions are normal", "conditions look sensible",
                  "within the expected range"],
        right_if=["10"],
    ),
    dict(
        n=6,
        q="which datasheets have nobody signed off on them",
        truth="6 of the 12 forms carry no signature: both CE sheets, both ESD "
              "sheets, the HARMONIC sheet and the RE sheet.",
        reachable="Partly. tested_by is a column; the signature itself lives only "
                  "in form_json and is deliberately excluded as a blob.",
        wrong_if=["all datasheets are signed", "every datasheet has a signature"],
        right_if=[],
    ),
    dict(
        n=7,
        q="what deviations from the standard have engineers written down",
        truth="Three: 'Calibration date added after review' on DEMO-EMC-301 CE, "
              "'Indirect discharge grid completed for all eight points' on "
              "DEMO-EMC-304 ESD, and 'Missing orientations added after review' "
              "on DEMO-EMC-302 PFMF.",
        reachable="YES. deviation is a projected column on datasheet_revision.",
        wrong_if=["no deviations", "none were recorded"],
        right_if=["review"],
    ),
    dict(
        n=8,
        q="are the two ESD datasheets filled in the same way",
        truth="No. 128 fields against 131, and eut_model_sku_number, "
              "eut_serial_number and tested_by_name appear on one of the two "
              "only.",
        reachable="No. Comparing which FIELDS exist needs the forms themselves.",
        wrong_if=["identical", "filled in the same way", "no differences"],
        right_if=[],
    ),
    dict(
        n=9,
        q="was the ESD grid already incomplete the first time it was submitted",
        truth="Yes. DEMO-EMC-304's indirect grid was 24 of 48 cells empty at "
              "revision 1 - which was rejected for CAL_EXPIRED, not for the "
              "grid. The reviewer only caught the grid at revision 2.",
        reachable="No. The state at submission is only in "
                  "datasheet_revision.form_json and in datasheet_rev_observation, "
                  "and both are out of the model's reach - the first as a large "
                  "column, the second by EXCLUDE_PREFIXES.",
        wrong_if=["it was complete", "fully filled at submission",
                  "no cells were empty"],
        right_if=[],
    ),
]


_NEGATED = ("not ", "no ", "aren't ", "isn't ", "never ", "nothing ")


def _asserted(phrase, low):
    """Is this phrase CLAIMED, or is it being denied?

    "identical" in wrong_if flagged an answer reading "No. They are not filled in
    identically" - the correct answer, scored as invented, because a substring
    match cannot see the negation three words earlier. Checking the run-up before
    each occurrence is crude but it is the difference between grading the claim
    and grading the vocabulary.
    """
    p = phrase.lower()
    start = 0
    while True:
        at = low.find(p, start)
        if at < 0:
            return False
        window = low[max(0, at - 24):at]
        if not any(neg in window for neg in _NEGATED):
            return True             # asserted somewhere with no denial in front
        start = at + 1


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mysql_config
    from nlp_search import audit, orchestrator

    cfg = mysql_config.config["default"]
    params = {"host": cfg.MYSQL_HOST, "port": int(cfg.MYSQL_PORT),
              "user": cfg.MYSQL_USER, "password": cfg.MYSQL_PASSWORD,
              "database": cfg.MYSQL_DATABASE}
    model_name = os.environ.get("NLP_SEARCH_MODEL", orchestrator.DEFAULT_MODEL)

    answered = declined = invented = 0
    spent = []
    transcript = []

    only = {int(a) for a in sys.argv[1:] if a.isdigit()}
    for case in CASES:
        if only and case["n"] not in only:
            continue
        print("=" * 78)
        print("#%d  %s" % (case["n"], case["q"]))
        print("=" * 78)
        print("  TRUTH      %s" % case["truth"])
        print("  REACHABLE  %s" % case["reachable"])

        t0 = time.time()
        try:
            res = orchestrator.answer(case["q"], params)
        except Exception as exc:                            # noqa: BLE001
            res = {"success": False, "message": "raised %s" % exc}
        secs = time.time() - t0
        answer = res.get("answer") or res.get("message") or ""
        tok = res.get("tokens") or {}
        cost = audit.estimate_cost(model_name, tok.get("input") or 0,
                                   tok.get("output") or 0) or 0.0
        spent.append(cost)
        trace = res.get("sql") or []

        low = answer.lower()
        invented_hit = [w for w in case["wrong_if"] if _asserted(w, low)]
        got = [w for w in case["right_if"] if w.lower() in low]
        no_answer = (not trace) or "did not run any query" in low \
            or "cannot answer" in low or "could not" in low \
            or "not in this catalog" in low or "would rather not guess" in low

        if invented_hit:
            verdict, tag = "INVENTED", "says %s" % ", ".join(map(repr, invented_hit))
            invented += 1
        elif case["right_if"] and got:
            verdict, tag = "ANSWERED", "has %s" % ", ".join(got)
            answered += 1
        elif no_answer:
            verdict, tag = "DECLINED", "which is correct here"
            declined += 1
        elif case["right_if"]:
            verdict, tag = "INVENTED", "answered without the key fact %s" \
                % ", ".join(case["right_if"])
            invented += 1
        else:
            verdict, tag = "ANSWERED", "check it by hand"
            answered += 1

        print("\n  COST %.0fs  in %s / out %s  $%.5f  route=%s  %d query/queries"
              % (secs, tok.get("input") or 0, tok.get("output") or 0, cost,
                 res.get("route") or "-", len(trace)))
        print("\n  ANSWER:")
        print("     " + answer.replace("\n", "\n     ")[:800])
        print("\n  -> %s  %s\n" % (verdict, tag))
        transcript.append((case, answer, verdict, cost, secs, len(trace)))

    total = answered + declined + invented
    print("=" * 78)
    print("  answered %d   declined %d   INVENTED %d   of %d"
          % (answered, declined, invented, total))
    print("=" * 78)
    print("  A decline is a pass on the unreachable ones. Only INVENTED is a")
    print("  failure, because only a wrong fact reaches a report.")
    if spent:
        print()
        print("  spent $%.4f across %d question(s), $%.5f each"
              % (sum(spent), len(spent), sum(spent) / len(spent)))
    return transcript


if __name__ == "__main__":
    main()
