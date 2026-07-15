"""Interactively unsubmit selected datasheets.

Lists the datasheets that are currently SUBMITTED (planner status
'datasheet_uploaded') for the CE / RE / Harmonic / Voltage-Flicker tests,
numbers them, and asks which ones to unsubmit (comma-separated numbers, or
'all'). Only the chosen ones are reverted to 'in_progress' and their saved
record set back to 'Not Submitted' — the filled data is KEPT, so the engineer
can re-open and re-submit it (this no longer deletes any records).

Run:  python unsubmit.py
"""
import os
import sys

sys.path.append(os.getcwd())

from sqlalchemy import text
from app import create_app
from models import db, PlannerEntry

# planner.test_name values that belong to the auto-generated datasheets
DATASHEET_TEST_NAMES = [
    "CE", "RE", "Harmonic", "VoltageFlicker",
    "ce", "re", "harmonic", "HARMONIC", "voltageflicker", "VOLTAGEFLICKER",
]


def parse_selection(raw, count):
    """Turn a comma-separated selection ('1,3,5' or 'all') into a sorted list of
    unique 1-based indices within [1, count]. Blank/invalid -> empty list."""
    s = (raw or "").strip().lower()
    if not s:
        return []
    if s in ("all", "*"):
        return list(range(1, count + 1))
    picked = []
    for part in s.replace(" ", "").split(","):
        if part.isdigit():
            n = int(part)
            if 1 <= n <= count and n not in picked:
                picked.append(n)
    return sorted(picked)


def _submitted_entries():
    """The planner entries whose datasheet is currently submitted."""
    return (PlannerEntry.query
            .filter(PlannerEntry.test_name.in_(DATASHEET_TEST_NAMES),
                    PlannerEntry.status == "datasheet_uploaded")
            .order_by(PlannerEntry.id.asc())
            .all())


def _eut_names():
    """planner_entry_id -> EUT name, from the saved datasheet records (best-effort)."""
    out = {}
    try:
        for pid, eut in db.session.execute(
                text("SELECT planner_entry_id, eut_name FROM datasheet_records")):
            if pid is not None and eut:
                out[pid] = eut
    except Exception:
        pass
    return out


def _print_table(entries, eut):
    print("\nSubmitted datasheets:\n")
    print("  %3s  %-18s %-14s %-32s %s" % ("#", "TCO", "Test", "EUT", "Submitted at"))
    print("  " + "-" * 84)
    for i, e in enumerate(entries, 1):
        when = e.datasheet_uploaded_at.strftime("%Y-%m-%d %H:%M") if e.datasheet_uploaded_at else "-"
        print("  %3d  %-18s %-14s %-32s %s" % (
            i, (e.tco_id or "-")[:18], (e.test_name or "-")[:14],
            (eut.get(e.id, "-") or "-")[:32], when))


def _unsubmit(entries):
    count = 0
    for e in entries:
        e.status = "in_progress"
        e.datasheet_file_path = None
        e.datasheet_uploaded_at = None
        e.datasheet_uploaded_by = None
        e.datasheet_comments = None
        # keep the filled form but mark it a draft again so it can be re-opened
        db.session.execute(
            text("UPDATE datasheet_records SET status=:st WHERE planner_entry_id=:pid"),
            {"st": "Not Submitted", "pid": e.id})
        count += 1
    db.session.commit()
    return count


def main():
    app = create_app()
    with app.app_context():
        entries = _submitted_entries()
        if not entries:
            print("No submitted datasheets found — nothing to unsubmit.")
            return
        eut = _eut_names()
        _print_table(entries, eut)

        raw = input("\nEnter the number(s) to unsubmit (comma-separated), 'all', or blank to cancel: ")
        picked = parse_selection(raw, len(entries))
        if not picked:
            print("Nothing selected — cancelled.")
            return

        chosen = [entries[i - 1] for i in picked]
        print("\nWill unsubmit:")
        for i in picked:
            e = entries[i - 1]
            print("  #%d  %s / %s" % (i, e.tco_id or "-", e.test_name or "-"))
        if (input("\nProceed? [y/N]: ").strip().lower() not in ("y", "yes")):
            print("Cancelled.")
            return

        try:
            n = _unsubmit(chosen)
            print("\nUnsubmitted %d datasheet(s) (reverted to 'in_progress'; drafts kept)." % n)
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            print("Error while unsubmitting: %s" % exc)


if __name__ == "__main__":
    main()
