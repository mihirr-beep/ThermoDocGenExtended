# -*- coding: utf-8 -*-
"""Does machinery get out of the answer, and does anything real get lost?

Every LEAK below is text a real run actually printed to a user. Every KEEP is
an answer that must survive untouched, and they matter more: the first version
of the stripper deleted a wholly correct answer about who changed the equipment
records, because a pattern meant to remove a stray "rows: missing" clause began
with `^.*` and ate the sentence in front of it. A cleanup step that silently
destroys good answers is worse than the leak it fixes.

The caveat Note: line is in KEEP for the same reason - it is appended by code
precisely so it cannot be dropped, and a stripper that removed it would undo
that guarantee.

Run with no arguments; needs no database and no API key.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nlp_search import verify                              # noqa: E402

# (label, text, fragments that must be GONE afterwards)
LEAKS = [
    ("invented SQL, mid-sentence",
     "65 instruments are overdue for maintenance. SQL shape used: SELECT COUNT(*) "
     "FROM maintenance WHERE maintenance_due_date < CURDATE();  (NULL "
     "maintenance_due_date values are excluded)",
     ["SELECT", "SQL shape"]),
    ("tool call echoed",
     "65 instruments overdue for maintenance.\nSource: maintenance_overdue with "
     "include_rows=False.\nSQL shape: lab_metric(name='maintenance_overdue', "
     "include_rows=False)",
     ["lab_metric", "include_rows", "Source:", "SQL shape"]),
    ("multi-line SQL dump",
     "Raj Krapa has made the most changes: 147 changes.\n\nSQL shape used:\n"
     "SELECT\n  COALESCE(u.username, eh.changed_by_username) AS user_name,\n"
     "  COUNT(*)\nFROM equipment_history eh\nLEFT JOIN users u ON "
     "eh.changed_by_user_id = u.id\nORDER BY change_count DESC\nLIMIT 200;",
     ["SELECT", "LEFT JOIN", "LIMIT", "SQL shape"]),
    ("a field the model could not fill",
     "Five users have made changes; Raj Krapa made the most (147 changes). "
     "Total equipment_history rows: missing.",
     ["missing"]),
]

# (label, text, fragments that must still be PRESENT afterwards)
KEEPS = [
    ("a schema answer - the identifier IS the answer",
     "The coupling method for CE is stored in datasheet_ce.coupling_method.",
     ["datasheet_ce.coupling_method"]),
    ("an ordinary answer with figures and names",
     "There are 44 items past their calibration due date, including the Current "
     "Probe and the Attenuator.",
     ["44", "Current Probe", "Attenuator"]),
    ("the content in front of a stripped clause",
     "Five users have made changes; Raj Krapa made the most (147 changes). "
     "Total equipment_history rows: missing.",
     ["Raj Krapa", "147"]),
    ("the code-appended caveat",
     "65 instruments are overdue for maintenance.\n\nNote: Items with no "
     "maintenance date recorded at all are not included in this figure.",
     ["Note:", "no maintenance date recorded"]),
    ("a legitimate status colon",
     "The CE datasheet for TFS-EMC-2026-001 has Overall Result: Pass.",
     ["Overall Result: Pass"]),
    ("a list of rows the answer is allowed to show",
     "Kondababu Arjilli - IEC-EMC-007 (Job TFS-EMC-2026-004) - CE",
     ["Kondababu Arjilli", "IEC-EMC-007", "CE"]),
]


def main():
    bad = 0
    print("LEAKS - machinery that must be removed")
    print("-" * 74)
    for label, text, gone in LEAKS:
        out = verify.strip_machinery(text)
        left = [g for g in gone if g.lower() in out.lower()]
        # removing everything is also a failure: something must remain
        empty = not out.strip()
        ok = not left and not empty
        bad += 0 if ok else 1
        print("%-38s %s" % (label, "ok" if ok else
                            ("*** EMPTIED THE ANSWER ***" if empty else
                             "*** still present: %s ***" % ", ".join(left))))

    print("\nKEEPS - real answers that must survive untouched")
    print("-" * 74)
    for label, text, present in KEEPS:
        out = verify.strip_machinery(text)
        lost = [p for p in present if p.lower() not in out.lower()]
        bad += 0 if not lost else 1
        print("%-38s %s" % (label, "ok" if not lost else
                            "*** LOST: %s ***" % ", ".join(lost)))

    print("-" * 74)
    print("%d failure(s)" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
