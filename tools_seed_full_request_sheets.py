# -*- coding: utf-8 -*-
"""Fill and approve the four datasheets on IEC-EMC-900, so the report has data.

WHY THE FORMS ARE COPIED RATHER THAN WRITTEN
--------------------------------------------
Each test's form_json has its own shape - CE is the bespoke form with parallel
mod_state[] / eq_name[] arrays, everything else uses the generic key__cN[] grid -
and a hand-written payload that is subtly the wrong shape does not error. It
projects to nothing, and the report then shows an empty section that looks like a
mapping bug. That has already happened twice in this project.

So each datasheet is seeded from the equivalent one on IEC-EMC-013, which is real
and complete, with only the identifying fields changed. The shape is therefore
correct by construction and the test exercises the report rather than my guess at
a schema.

WHAT IS DELIBERATELY VARIED
---------------------------
  ESD carries modification state 2; the other three stay at 0. That is the case
  the report has to widen into rows 0, 1 AND 2 - state 1 belongs to no test - and
  proving it on a real generated document is the whole point of this fixture.

  CE and RE are given PASS/FAIL; EFT and ESD a performance criterion. 1.1 must
  show each in its own idiom, and 1.4 must list the two emission uncertainties
  and not the immunity tests.

    python tools_seed_full_request_sheets.py
"""
import json
import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text  # noqa: E402

TCO = "IEC-EMC-900"
DONOR_TCO = "IEC-EMC-013"

# (code, planner test_name, result, criterion). CE/RE are emission tests and
# report PASS/FAIL; EFT/ESD are immunity and report the criterion they met.
PLAN = [
    ("CE",  "CE",  "PASS", "A"),
    ("RE",  "RE",  "PASS", "A"),
    ("EFT", "EFT", None,   "B"),
    ("ESD", "ESD", None,   "A"),
]

# ESD alone reaches state 2, so 2.4 must print 0, 1, 2.
EXTRA_MODIFICATION = {
    "ESD": ("2", "Ferrite sleeve fitted on the sensor harness",
            "Engineering", "14/06/2026"),
}

SOFTWARE = [("EMC32 Measurement Suite", "10.60.1"),
            ("FB900 Monitor", "3.4.0")]


def _donor_form(db, code):
    raw = db.session.execute(text("""
        SELECT d.form_json FROM planner_entries p
        JOIN datasheet_records d ON d.planner_entry_id = p.id
        JOIN iec_emc_requests q ON q.id = p.test_request_id
        WHERE q.tco_id = :donor AND UPPER(p.test_name) = :c
        ORDER BY d.id DESC LIMIT 1"""), {"donor": DONOR_TCO, "c": code}).scalar()
    return json.loads(raw) if raw else {}


def _retarget(form, code, tco, job, engineer, when, result, criterion):
    """The donor's shape with this request's identity and outcome."""
    f = dict(form)
    f.update({
        "tco_id": tco, "job_number": job,
        "test_date": when.isoformat(), "date": when.isoformat(),
        "tested_by": engineer.username,
        "ambient_temperature": "23", "relative_humidity": "47",
        "eut_name": "FULLFILL Test Bench FB-900",
        "eut_model_sku_number": "FB-900-UVUF",
        "eut_serial_number": "FB900-2026-000417",
        "eut_input_voltage_frequency": "230 V / 50 Hz",
        "eut_configuration": "Tabletop, mains powered, sensor harness connected",
        "monitoring_parameters": "No error message; resistivity 18.2 MOhm-cm",
        "test_mode": "Mode A: Normal operating mode",
    })
    # Every immunity form carries BOTH a PASS/FAIL radio and an A/B/C/D select,
    # so a real record has both filled. Leaving `result` alone here inherited the
    # donor's value and produced result='A' on a form whose radio only offers
    # PASS/FAIL - a state the UI cannot create, and one that made the report's
    # 1.1 summary disagree with the test's own RESULT table. Seed what the screen
    # can actually produce, or the fixture tests a fiction.
    f["result"] = result or "PASS"
    f["overall_result"] = f["result"]
    if criterion:
        f["met_performance_criteria"] = criterion
        f["required_performance_criteria"] = criterion

    # SOFTWARE USED, in whichever shape this form posts. 2.3 reads
    # datasheet_software, which is only populated from these keys.
    if code == "CE":
        # the bespoke CE form keeps software as two scalars, not a grid
        f["software_used"] = SOFTWARE[0][0]
        f["software_version"] = SOFTWARE[0][1]
    else:
        f["software_used_rows__c0[]"] = [s[0] for s in SOFTWARE]
        f["software_used_rows__c1[]"] = [s[1] for s in SOFTWARE]

    # EUT MODIFICATION RECORD - state 0 always, plus state 2 on ESD only
    rows = [("0", "Initial state", "", "")]
    if code in EXTRA_MODIFICATION:
        rows.append(EXTRA_MODIFICATION[code])
    if code == "CE":
        f["mod_state[]"] = [r[0] for r in rows]
        f["mod_description[]"] = [r[1] for r in rows]
        f["mod_fitted_by[]"] = [r[2] for r in rows]
        f["mod_date[]"] = [r[3] for r in rows]
    else:
        for i in range(4):
            f["eut_modification_rec_rows__c%d[]" % i] = [r[i] for r in rows]
    return f


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    import app as app_module
    from models import db, User, PlannerEntry
    import datasheet_gen.projection as PJ

    a = app_module.create_app("default")
    with a.app_context():
        rid = db.session.execute(text(
            "SELECT id FROM iec_emc_requests WHERE tco_id=:t"), {"t": TCO}).scalar()
        if not rid:
            print("no %s - run tools_seed_full_request.py first" % TCO)
            return 1
        users = db.session.query(User).order_by(User.id).all()
        engineer = users[0]
        reviewer = next((u for u in users if u.id != engineer.id), engineer)
        start = date.today() - timedelta(days=12)

        for i, (code, name, result, criterion) in enumerate(PLAN):
            when = start + timedelta(days=i)
            entry_id = db.session.execute(text(
                "SELECT id FROM planner_entries WHERE test_request_id=:r AND test_name=:n"),
                {"r": rid, "n": name}).scalar()
            if not entry_id:
                db.session.execute(text(
                    "INSERT INTO planner_entries (test_request_id, tco_id, test_name, "
                    "test_person_name, engineer_user_id, peer_reviewer_user_id, "
                    "start_date, end_date, status, created_at, updated_at) "
                    "VALUES (:r, :t, :n, :pn, :e, :pr, :s, :d, 'in_progress', NOW(), NOW())"),
                    {"r": rid, "t": TCO, "n": name, "pn": engineer.username,
                     "e": engineer.id, "pr": reviewer.id, "s": when,
                     "d": when + timedelta(days=1)})
                entry_id = db.session.execute(text("SELECT LAST_INSERT_ID()")).scalar()
                db.session.commit()

            form = _retarget(_donor_form(db, code), code, TCO,
                             "TFS-EMC-2026-900", engineer, when, result, criterion)
            exists = db.session.execute(text(
                "SELECT id FROM datasheet_records WHERE planner_entry_id=:p"),
                {"p": entry_id}).scalar()
            payload = {"p": entry_id, "c": code,
                       "f": json.dumps(form, ensure_ascii=False),
                       "r": result or criterion}
            if exists:
                db.session.execute(text(
                    "UPDATE datasheet_records SET form_json=:f, result=:r, "
                    "test_code=:c, updated_at=NOW() WHERE planner_entry_id=:p"), payload)
            else:
                db.session.execute(text(
                    "INSERT INTO datasheet_records (planner_entry_id, test_code, "
                    "form_json, result, status, created_at, updated_at) "
                    "VALUES (:p, :c, :f, :r, 'Draft', NOW(), NOW())"), payload)
            db.session.commit()

            rec = dict(db.session.execute(text(
                "SELECT planner_entry_id, test_code, form_json, images_json, result "
                "FROM datasheet_records WHERE planner_entry_id=:p"),
                {"p": entry_id}).mappings().first())
            ent = db.session.execute(text(
                "SELECT * FROM planner_entries WHERE id=:e"), {"e": entry_id}).mappings().first()
            req = db.session.execute(text(
                "SELECT * FROM iec_emc_requests WHERE id=:r"), {"r": rid}).mappings().first()
            # SimpleNamespace, not dict: _header_values reads both with getattr,
            # and plain dicts silently produced NULL tco_id / product_name once
            # already.
            PJ.project(rec, SimpleNamespace(**dict(ent)), SimpleNamespace(**dict(req)))
            db.session.execute(text(
                "UPDATE `datasheet` SET met_performance_criteria=:m, result=:r "
                "WHERE planner_entry_id=:p"),
                {"m": criterion, "r": result or criterion, "p": entry_id})
            db.session.commit()

            PJ.record_transition(entry_id, "Peer Review", actor=engineer,
                                 comment="Submitted for peer review.", snapshot=True,
                                 submitted=True, from_status="Draft")
            PJ.record_transition(entry_id, "Approved", actor=reviewer,
                                 comment="Record checked against the raw data. Approved.",
                                 decided=True, from_status="Peer Review")
            db.session.execute(text(
                "UPDATE planner_entries SET status='datasheet_uploaded' WHERE id=:e"),
                {"e": entry_id})
            db.session.commit()

            mods = db.session.execute(text(
                "SELECT COUNT(*) FROM datasheet_modification mo JOIN `datasheet` d "
                "ON d.id=mo.datasheet_id WHERE d.planner_entry_id=:p"), {"p": entry_id}).scalar()
            sw = db.session.execute(text(
                "SELECT COUNT(*) FROM datasheet_software s JOIN `datasheet` d "
                "ON d.id=s.datasheet_id WHERE d.planner_entry_id=:p"), {"p": entry_id}).scalar()
            print("   %-5s entry %-5s result=%-5s criterion=%-3s modification rows=%s software rows=%s"
                  % (code, entry_id, result or "-", criterion, mods, sw))

        print("\nall four approved on %s" % TCO)
    return 0


if __name__ == "__main__":
    sys.exit(main())
