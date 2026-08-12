# -*- coding: utf-8 -*-
"""Can a primary key still ground a claim about a quantity? It could.

This replays the exact evidence and the exact answer from a real run. Asked
which engineers have not filled in a single datasheet, the assistant said
"Kondababu Arjilli has 10 tests assigned with no datasheet, and Krishna Gonela
has 3". Its own four queries returned 12 and 2. Both 10 and 3 are
planner_entries.id values that came back in a result set, so the grounding
check accepted them and the UI stamped the answer "Corrected to match the
data" - a wrong answer wearing a badge that says it was verified.

Run it with no arguments; it needs no database and no API key.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nlp_search.ledger import Ledger                      # noqa: E402
from nlp_search import verify                             # noqa: E402

QUESTION = ("Which engineers have been assigned tests but have not filled in "
            "a single datasheet?")

# The answer that shipped. 10 and 3 appear in no query result.
BAD = ("Engineers who have been assigned tests but have not filled in a single "
       "datasheet are Kondababu Arjilli and Krishna Gonela. Kondababu Arjilli "
       "has 10 tests assigned with no datasheet, and Krishna Gonela has 3 "
       "tests assigned with no datasheet.")


def ledger_from_the_real_run():
    """The four queries that run actually issued, with their real columns.

    The column NAMES matter as much as the values - that is what marks 10 and
    3 as identifiers rather than measurements.
    """
    led = Ledger()
    led.record("semantics", "SELECT COUNT(*) FROM iec_emc_request_tests t "
                            "LEFT JOIN `datasheet` d ...",
               columns=["test_unfilled"], rows=[[78]])
    led.record("semantics", "SELECT COUNT(*) FROM planner_entries p "
                            "LEFT JOIN `datasheet` d ...",
               columns=["test_no_datasheet"], rows=[[13]])
    # planner ids 3..14 - this is where 10 and 3 came from
    led.record("datasheets",
               "SELECT p.id AS planner_entry_id, p.test_name, p.test_person_name, "
               "p.tco_id, p.status FROM planner_entries p LEFT JOIN datasheet d "
               "ON d.planner_entry_id = p.id WHERE d.id IS NULL LIMIT 200",
               columns=["planner_entry_id", "test_name", "test_person_name",
                        "tco_id", "status"],
               rows=[[i, "CE", "Kondababu Arjilli", "IEC-EMC-010", "in_progress"]
                     for i in (3, 10, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40)]
                    + [[41, "EFT", "Krishna Gonela", "IEC-EMC-004", "cancelled"],
                       [4, "CE", "Krishna Gonela", "IEC-EMC-004", "in_progress"]])
    led.record("schedule",
               "SELECT id AS planner_entry_id, tco_id, test_name, "
               "test_person_name, status FROM planner_entries WHERE status <> "
               "'cancelled' AND test_person_name IS NOT NULL LIMIT 200",
               columns=["planner_entry_id", "tco_id", "test_name",
                        "test_person_name", "status"],
               rows=[[i, "IEC-EMC-004", "CE", "Krishna Gonela", "report_uploaded"]
                     for i in range(3, 20)])
    return led


def main():
    led = ledger_from_the_real_run()

    id_only = led.id_only_numbers()
    print("integers that appear ONLY in id columns: %s"
          % ", ".join(sorted(id_only, key=lambda x: (len(x), x)) or ["(none)"]))

    # The two figures the answer asserted, and the two the evidence holds.
    for n, why in (("10", "claimed for Kondababu"), ("3", "claimed for Krishna"),
                   ("78", "a real COUNT"), ("13", "a real COUNT")):
        in_all = n in led.values()
        withheld = n in id_only
        print("  %-4s %-24s in evidence=%-5s withheld as an id=%s"
              % (n, why, in_all, withheld))

    # Now the check itself, which is what actually shipped the bad answer.
    res = verify.check(QUESTION, BAD, led, model=None)
    flagged = set(res.get("unsupported") or [])
    print("\nverdict: %s" % res["verdict"])
    print("notes  : %s" % "; ".join(res.get("notes") or ["(none)"]))

    # With no API key the adjudicator cannot run, so assert on the numeric pass
    # rather than the verdict - that is the part this change touches.
    supported = ((led.values() - led.id_only_numbers())
                 | verify._question_tokens(QUESTION)
                 | verify._row_counts(led) | verify._temporal_tokens())
    caught = [n for n in ("10", "3") if n not in supported]
    passed = [n for n in ("78", "13") if n in supported]

    print("\nfabricated figures now refused : %s" % (", ".join(caught) or "NONE"))
    print("genuine counts still accepted  : %s" % (", ".join(passed) or "NONE"))

    ok = caught == ["10", "3"] and passed == ["78", "13"]
    print("\n%s" % ("PASS - a primary key can no longer ground a quantity"
                    if ok else
                    "*** FAIL - the grounding set still accepts a raw id ***"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
