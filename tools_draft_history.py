# -*- coding: utf-8 -*-
"""Is every edit an engineer makes recoverable, and are no-op saves ignored?

Before datasheet_draft_history existed the answer to the first was no.
datasheet_records holds one row per assignment and upserts it in place, so an
engineer who typed 48.2, saved, spotted the error and saved 48.7 left no trace
of 48.2 anywhere. datasheet_revision only froze SUBMITTED versions, so a
datasheet submitted once had exactly one snapshot and no record of the editing
that produced it.

The second question matters just as much. The autosave fires 1.5 s after typing
stops and cannot tell whether a box was edited or merely tabbed through, so
without a content check the table fills with identical rows and stops being
worth reading.

Drives real saves through the real route. Needs a throwaway job:

    python tools_draft_history.py --job TFS-EMC-2026-010
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module                                    # noqa: E402
from models import db, User                                 # noqa: E402
from sqlalchemy import text                                 # noqa: E402

FAILURES = []


def check(label, got, want, note=""):
    ok = got == want
    if not ok:
        FAILURES.append(label)
    print("   %-54s %s" % (label, "ok" if ok else
                           "*** got %r, expected %r ***" % (got, want)))
    if note and not ok:
        print("        %s" % note)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    args = ap.parse_args()

    app = app_module.create_app("default")
    app.config["WTF_CSRF_ENABLED"] = False
    app.login_manager.session_protection = None

    with app.app_context():
        row = db.session.execute(text(
            "SELECT planner_entry_id, test_code, tco_id FROM datasheet_records r "
            "WHERE r.job_number=:j OR r.tco_id IN "
            "(SELECT tco_id FROM iec_emc_requests WHERE job_number=:j) LIMIT 1"),
            {"j": args.job}).first()
        if row is None:
            print("no draft on job %r - refusing to pick one at random" % args.job)
            return 2
        pid, code, tco = row
        uid = User.query.filter_by(role="admin").first().id

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True

    url = ("/datasheet/ce/save-draft" if code == "CE"
           else "/datasheet/g/%s/save-draft" % code.lower())
    base = {"assignment_id": str(pid), "tco_id": tco or ""}

    def save(**extra):
        d = dict(base)
        d.update(extra)
        return client.post(url, data=d)

    def rows():
        with app.app_context():
            return db.session.execute(text(
                "SELECT changed_count, changed_fields, saved_by_name, content_hash "
                "FROM datasheet_draft_history WHERE planner_entry_id=:p "
                "ORDER BY id"), {"p": pid}).fetchall()

    print("entry %s (%s) on %s\n" % (pid, code, tco))
    start = len(rows())

    print("1. a save that CHANGES a value")
    save(ambient_temperature="21.5")
    after = rows()
    check("a history row was appended", len(after) - start, 1)
    if len(after) > start:
        check("it names the field that changed",
              "ambient_temperature" in (after[-1][1] or ""), True,
              "changed_fields is what makes this table readable")
        check("it records who saved it", bool(after[-1][2]), True)

    print("\n2. the SAME save again - the autosave's no-op case")
    n_before = len(rows())
    save(ambient_temperature="21.5")
    check("nothing was appended", len(rows()) - n_before, 0,
          "identical autosaves would otherwise bury the real edits")

    print("\n3. a second, different value - the edit that used to vanish")
    save(ambient_temperature="23.9")
    hist = rows()
    check("a second row was appended", len(hist) - n_before, 1)
    vals = []
    with app.app_context():
        for (fj,) in db.session.execute(text(
                "SELECT form_json FROM datasheet_draft_history "
                "WHERE planner_entry_id=:p ORDER BY id DESC LIMIT 4"), {"p": pid}).fetchall():
            import json
            try:
                vals.append((json.loads(fj) or {}).get("ambient_temperature"))
            except Exception:
                vals.append(None)
    check("BOTH the old and new value are recoverable",
          "21.5" in vals and "23.9" in vals, True,
          "history holds: %s" % vals)

    print("\n4. what an auditor can now read back")
    with app.app_context():
        for r in db.session.execute(text(
            "SELECT saved_at, status, changed_count, LEFT(changed_fields, 58), saved_by_name "
            "FROM datasheet_draft_history WHERE planner_entry_id=:p "
            "ORDER BY id DESC LIMIT 5"), {"p": pid}).fetchall():
            print("   %s  %-14s %3d field(s)  %-58s %s"
                  % (r[0], r[1], r[2], r[3] or "(first save)", r[4] or "-"))

    print("\n" + "-" * 72)
    print("%d check(s) failed%s" % (len(FAILURES),
                                    "" if not FAILURES else ": " + ", ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
