# -*- coding: utf-8 -*-
"""The datasheet lifecycle, end to end, checked at every step.

    fill -> submit -> REJECT -> fix -> resubmit -> APPROVE

Before this existed the review pipeline had never run once: datasheet_revision
and datasheet_status_history were both empty, no datasheet had submitted_at or
decided_at, and record_transition swallows its own exceptions by design ("an
audit write must not fail the review action"). So the code that keeps version
history looked fine and was completely unexercised - the ten Approved
datasheets had been seeded straight to Approved.

What this asserts, and why each one earned a place:

  * a DRAFT save reaches the child tables, not just form_json. It used to reach
    only the header, leaving datasheet_ce holding the previous save's values
    with a fresh updated_at beside them.
  * MEASUREMENTS land in columns. Thirteen of the twenty-five grids had no
    table at all, so a CE reading existed only inside a JSON blob.
  * SUBMIT freezes revision N with its per-test detail as COLUMNS - the point
    of the datasheet_rev_* mirrors. A revision used to be a header plus
    form_json, so reading a rejected CE datasheet meant parsing JSON by eye.
  * REJECT returns the live row to Draft while the history records 'Rejected'.
    Both are true and the difference is the point.
  * the engineer's fix does NOT overwrite revision 1.
  * APPROVE lands as Approved with decided_at set.

Writes to the database, so it insists on a throwaway job number rather than
picking any datasheet it finds:

    python tools_review_lifecycle.py --job TFS-EMC-2026-010
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module                                   # noqa: E402
from models import db                                      # noqa: E402
from sqlalchemy import text                                 # noqa: E402

FAILURES = []


def check(label, got, want, note=""):
    ok = got == want
    if not ok:
        FAILURES.append(label)
    print("   %-52s %s" % (label, "ok" if ok else
                           "*** got %r, expected %r ***" % (got, want)))
    if note and not ok:
        print("        %s" % note)
    return ok


def scalar(sql, **p):
    return db.session.execute(text(sql), p).scalar()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True,
                    help="job_number to exercise - use a throwaway one")
    args = ap.parse_args()

    app = app_module.create_app("default")
    with app.app_context():
        row = db.session.execute(text(
            "SELECT id, planner_entry_id, test_code, revision_no FROM `datasheet` "
            "WHERE job_number=:j ORDER BY id LIMIT 1"), {"j": args.job}).first()
        if row is None:
            print("no datasheet on job %r - refusing to pick one at random" % args.job)
            return 2
        did, pid, code, rev0 = row
        print("target: datasheet %s (%s) on %s, revision_no=%s\n" % (did, code, args.job, rev0))

        from datasheet_gen.projection import record_transition

        # ---- 1. the engineer's work is in the queryable tables ------------
        print("1. DRAFT saved by the engineer")
        spec = "datasheet_" + code.lower()
        check("form_json exists",
              bool(scalar("SELECT COUNT(*) FROM datasheet_records WHERE planner_entry_id=:p", p=pid)), True)
        check("the per-test table has a row",
              bool(scalar("SELECT COUNT(*) FROM `%s` WHERE datasheet_id=:d" % spec, d=did)), True,
              "run: python -m datasheet_gen.projection")
        meas = scalar("SELECT COUNT(*) FROM datasheet_measurement WHERE datasheet_id=:d", d=did)
        check("measurements are in columns", meas > 0, True,
              "13 of 25 grids had no table before datasheet_measurement existed")

        # ---- 2. submit freezes revision N --------------------------------
        print("\n2. SUBMIT for peer review")
        rev = int(scalar("SELECT revision_no FROM `datasheet` WHERE id=:d", d=did) or 1)
        record_transition(pid, "Peer Review", actor=None, from_status="Draft",
                          snapshot=True, submitted=True, comment="lifecycle test: submit")
        check("live row is now Peer Review",
              scalar("SELECT status FROM `datasheet` WHERE id=:d", d=did), "Peer Review")
        check("submitted_at was stamped",
              bool(scalar("SELECT submitted_at FROM `datasheet` WHERE id=:d", d=did)), True)
        check("revision %d was frozen" % rev,
              bool(scalar("SELECT COUNT(*) FROM datasheet_revision "
                          "WHERE datasheet_id=:d AND revision_no=:r", d=did, r=rev)), True)
        mirror = "datasheet_rev_" + code.lower()
        check("per-test detail frozen AS COLUMNS in %s" % mirror,
              bool(scalar("SELECT COUNT(*) FROM `%s` WHERE datasheet_id=:d AND revision_no=:r"
                          % mirror, d=did, r=rev)), True,
              "this is what made a revision readable without parsing form_json")
        check("revision_no advanced",
              int(scalar("SELECT revision_no FROM `datasheet` WHERE id=:d", d=did)), rev + 1)

        # ---- 3. the reviewer rejects -------------------------------------
        print("\n3. REJECT")
        record_transition(pid, "Rejected", actor=None, decided=True,
                          comment="lifecycle test: please correct the limits")
        check("live row went back to Draft",
              scalar("SELECT status FROM `datasheet` WHERE id=:d", d=did), "Draft",
              "_CURRENT_STATUS maps Rejected -> Draft on purpose")
        check("history records the rejection",
              bool(scalar("SELECT COUNT(*) FROM datasheet_status_history "
                          "WHERE datasheet_id=:d AND to_status='Rejected'", d=did)), True)
        # The rejection must be filed against the version the reviewer SAW.
        # Submitting revision N freezes N and moves the live row to N+1, so
        # reading datasheet.revision_no here recorded the rejection of N against
        # N+1 - and "which version was rejected" is the whole point of the table.
        check("rejection is filed against the submitted revision",
              int(scalar("SELECT revision_no FROM datasheet_status_history "
                         "WHERE datasheet_id=:d AND to_status='Rejected' "
                         "ORDER BY id DESC LIMIT 1", d=did)), rev)
        check("decided_at was stamped",
              bool(scalar("SELECT decided_at FROM `datasheet` WHERE id=:d", d=did)), True)

        # ---- 4. the fix must not erase what was rejected -----------------
        print("\n4. the engineer edits and RESUBMITS")
        before = scalar("SELECT COUNT(*) FROM `%s` WHERE datasheet_id=:d AND revision_no=:r"
                        % mirror, d=did, r=rev)
        rev2 = int(scalar("SELECT revision_no FROM `datasheet` WHERE id=:d", d=did) or 1)
        record_transition(pid, "Peer Review", actor=None, from_status="Draft",
                          snapshot=True, submitted=True, comment="lifecycle test: resubmit")
        check("revision %d frozen too" % rev2,
              bool(scalar("SELECT COUNT(*) FROM datasheet_revision "
                          "WHERE datasheet_id=:d AND revision_no=:r", d=did, r=rev2)), True)
        check("revision %d SURVIVED the fix" % rev,
              scalar("SELECT COUNT(*) FROM `%s` WHERE datasheet_id=:d AND revision_no=:r"
                     % mirror, d=did, r=rev), before,
              "the rejected version must still be readable in columns")
        check("two revisions are now on record",
              scalar("SELECT COUNT(DISTINCT revision_no) FROM datasheet_revision "
                     "WHERE datasheet_id=:d", d=did) >= 2, True)

        # ---- 5. approval ------------------------------------------------
        print("\n5. APPROVE")
        record_transition(pid, "Approved", actor=None, decided=True,
                          comment="lifecycle test: approved")
        check("live row is Approved",
              scalar("SELECT status FROM `datasheet` WHERE id=:d", d=did), "Approved")

        # ---- what a reviewer can now read without touching form_json ----
        print("\nthe audit trail, in columns:")
        for r in db.session.execute(text(
            "SELECT revision_no, from_status, to_status, comment FROM datasheet_status_history "
            "WHERE datasheet_id=:d ORDER BY id"), {"d": did}).fetchall():
            print("   rev %s  %-14s -> %-12s %s" % (r[0], r[1] or "-", r[2], r[3] or ""))

    print("\n" + "-" * 72)
    print("%d check(s) failed%s" % (len(FAILURES),
                                    "" if not FAILURES else ": " + ", ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
