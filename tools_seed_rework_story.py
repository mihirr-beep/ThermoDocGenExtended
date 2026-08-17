#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Seed the rework story: filled, sent back, fixed, sent back again, approved.

    python tools_seed_rework_story.py           # build it
    python tools_seed_rework_story.py --clean   # remove it

WHY
---
The commonest review question in the lab - "this datasheet was sent back twice,
why?" - had no instance in the database to answer about. Three rejections
existed, all single, on three different datasheets. NOTHING had ever been
rejected twice, so a question about the second rejection had nothing behind it
and neither did any test of the answer.

This builds one datasheet that goes round three times:

    revision 1  submitted -> REJECTED   CAL_EXPIRED     equipment calibration
    revision 2  submitted -> REJECTED   INCOMPLETE_OBS  observation grid gaps
    revision 3  submitted -> APPROVED

EACH ROUND FIXES DIFFERENT FIELDS ON PURPOSE. review_history compares the whole
form_json between consecutive revisions, so if every round changed the same two
fields the diff would prove nothing about whether the comparison works. Round 2
touches the equipment calibration dates the first reviewer asked about; round 3
fills the observation cells the second reviewer asked about. A reader - or a
model - should be able to see the engineer responding to each finding in turn.

Written through records.upsert_record and record_transition, the functions the
app itself calls, for the reason the other seeders give: a fixture that writes
the analytical tables directly proves the fixture works and nothing else.

SYNTHETIC AND SAYS SO: is_synthetic=1, product starts with DEMO, TCO in the
DEMO-EMC-3xx block.
"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text  # noqa: E402

TCO = "DEMO-EMC-304"
JOB = "DEMO-JOB-304"
PRODUCT = "DEMO Meridian Rework Analyser"
MODEL = "DEMO-50199004"
SERIAL = "DEMOSN0000304"
CODE = "ESD"          # generic form, and its observation grids are keys I know
PLANNER_NAME = "ESD"

ROUNDS = [
    # (decision, reason_code, reviewer comment)
    ("Rejected", "CAL_EXPIRED",
     "ESD simulator calibration due date is blank in the equipment list, and the "
     "climatic sensor's has expired. Cannot accept the record without them."),
    ("Rejected", "INCOMPLETE_OBS",
     "Indirect discharge grid is only filled for the first four points. HCP 180 "
     "and 270 and both VCP rows are empty."),
    ("Approved", None,
     "Calibration dates present and the indirect grid is complete. Approved."),
]


def base_form(today):
    """Round 1: calibration dates missing AND the indirect grid half filled.

    Both faults are present from the start so each reviewer has something real to
    find, and the fix for one does not accidentally fix the other.
    """
    form = {
        "tco_id": TCO, "job_number": JOB, "eut_name": PRODUCT,
        "eut_model": MODEL, "eut_model_sku_number": MODEL,
        "eut_serial": SERIAL, "eut_serial_number": SERIAL,
        "sop_reference": "IEC-SOP-511",
        "product_standard": "IEC 61326-1 : 2020",
        "basic_standard": "IEC 61000-4-2:2008",
        "test_mode": "Mode A", "eut_configuration": "Tabletop",
        "eut_modification_state": "0",
        "eut_input_voltage_frequency": "230 V, 50 Hz",
        "ambient_temperature": "22.8", "relative_humidity": "51",
        "test_date": today, "tested_by": "DEMO Engineer",
        "tested_by_name": "DEMO Engineer",
        "deviation": "NA",
        "required_performance_criteria": "B",
        "met_performance_criteria": "B",
        "monitoring_parameters": "Display readout and alarm state monitored.",
        "test_procedure": "Discharges applied per IEC 61000-4-2 at each point.",
        "rc_network": "330 ohm / 150 pF",
        "direct_contact_discharge": "+/-4 kV",
        "indirect_hcp": "+/-4 kV", "indirect_vcp": "+/-4 kV",
        "air_discharge": "+/-8 kV",
        "atmospheric_air_pressure": "1006 hPa",
        # FAULT 1: the calibration column is blank for both instruments
        "test_equipment_used_rows__c0[]": ["ESD Simulator", "Climatic Sensor"],
        "test_equipment_used_rows__c1[]": ["Teseq", "Rotronic"],
        "test_equipment_used_rows__c2[]": ["NSG 438", "HP23"],
        "test_equipment_used_rows__c3[]": ["DEMO-ESD-04", "DEMO-CS-04"],
        "test_equipment_used_rows__c4[]": ["", ""],
        "software_used_rows__c0[]": ["ESD Control Suite"],
        "software_used_rows__c1[]": ["4.2.1"],
        "eut_modification_rec_rows__c0[]": ["0"],
        "eut_modification_rec_rows__c1[]": ["Initial state"],
        "eut_modification_rec_rows__c2[]": [""],
        "eut_modification_rec_rows__c3[]": [""],
    }
    # FAULT 2: indirect discharge filled for rows 1-4 only, 5-8 left blank
    for r in range(1, 9):
        for c in range(1, 7):
            form["ind_r%d_c%d" % (r, c)] = "A" if r <= 4 else ""
    for prefix, points in (("dir", ["Enclosure seam", "Front panel", "Connector shell"]),
                           ("air", ["Display bezel", "Vent slot", "USB port"])):
        for i, point in enumerate(points, 1):
            form["%s_r%d_name" % (prefix, i)] = point
            for c in range(1, 7):
                form["%s_r%d_c%d" % (prefix, i, c)] = "A"
    return form


def fix_round_2(form):
    """What the engineer changed after CAL_EXPIRED: the calibration dates only."""
    form["test_equipment_used_rows__c4[]"] = ["2027-05-31", "2027-02-28"]
    form["deviation"] = "Calibration certificates attached after review."
    return form


def fix_round_3(form):
    """What the engineer changed after INCOMPLETE_OBS: the missing grid rows only."""
    for r in range(5, 9):
        for c in range(1, 7):
            form["ind_r%d_c%d" % (r, c)] = "A" if c < 6 else "B"
    form["deviation"] = "Indirect discharge grid completed for all eight points."
    return form


def clean(db):
    from datasheet_gen.projection_schema import mirrored_tables
    rid = db.session.execute(text(
        "SELECT id FROM iec_emc_requests WHERE tco_id=:t"), {"t": TCO}).scalar()
    entries = [r[0] for r in db.session.execute(text(
        "SELECT id FROM planner_entries WHERE tco_id=:t"), {"t": TCO}).fetchall()]
    dids = [r[0] for r in db.session.execute(text(
        "SELECT id FROM `datasheet` WHERE tco_id=:t"), {"t": TCO}).fetchall()]
    children = [d for _s, d in mirrored_tables()] + [s for s, _d in mirrored_tables()]
    for did in dids:
        for t in children + ["datasheet_measurement", "datasheet_revision",
                             "datasheet_status_history", "datasheet_draft_history"]:
            try:
                db.session.execute(text("DELETE FROM `%s` WHERE datasheet_id=:d" % t),
                                   {"d": did})
            except Exception:  # noqa: BLE001
                db.session.rollback()
        db.session.execute(text("DELETE FROM `datasheet` WHERE id=:d"), {"d": did})
    for eid in entries:
        db.session.execute(text("DELETE FROM datasheet_records WHERE planner_entry_id=:e"),
                           {"e": eid})
        db.session.execute(text("DELETE FROM datasheet_draft_history WHERE planner_entry_id=:e"),
                           {"e": eid})
        db.session.execute(text("DELETE FROM planner_entries WHERE id=:e"), {"e": eid})
    if rid:
        db.session.execute(text("DELETE FROM iec_emc_request_tests WHERE request_id=:r"),
                           {"r": rid})
        db.session.execute(text("DELETE FROM iec_emc_requests WHERE id=:r"), {"r": rid})
    db.session.commit()
    return rid, entries, dids


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    import app as app_module
    from models import db, User, PlannerEntry
    import datasheet_gen.projection as PJ
    import datasheet_gen.records as R

    a = app_module.create_app("default")
    with a.app_context():
        if "--clean" in sys.argv:
            print("removed %s -> %s" % (TCO, clean(db)))
            return 0
        clean(db)

        engineer = db.session.query(User).filter_by(role="lab_engineer", is_active=True) \
                             .order_by(User.id).first()
        reviewer = db.session.query(User).filter_by(role="admin", is_active=True) \
                             .order_by(User.id).first()
        if not engineer or not reviewer:
            print("need one active lab_engineer and one active admin")
            return 1

        start = date.today() - timedelta(days=20)
        db.session.execute(text(
            "INSERT INTO iec_emc_requests (user_id, tco_id, job_number, status, product_name, "
            "manufacturer, manufacturer_address, model_number, serial_number, test_samples, "
            "samples_available_in_lab, requester_name, requester_department, requester_group, "
            "requester_division, requester_site, requester_email, requester_contact, "
            "requester_designation, requester_date, assigned_engineer_id, "
            "assigned_engineer_name, submitted_at, created_at, updated_at, is_synthetic) "
            "VALUES (:u, :t, :j, 'Test Plan Approved', :p, 'DEMO Instruments', "
            "'1 Demo Way, Testville', :m, :s, 1, 'yes', 'DEMO Requester', 'DEMO Dept', "
            "'DEMO Group', 'DEMO Division', 'DEMO Site', 'demo.requester@example.invalid', "
            "'0000000000', 'Engineer', CURDATE(), :ei, :en, NOW(), NOW(), NOW(), 1)"),
            {"u": engineer.id, "t": TCO, "j": JOB, "p": PRODUCT, "m": MODEL, "s": SERIAL,
             "ei": engineer.id, "en": engineer.username})
        db.session.commit()
        rid = db.session.execute(text(
            "SELECT id FROM iec_emc_requests WHERE tco_id=:t"), {"t": TCO}).scalar()
        db.session.execute(text(
            "INSERT INTO iec_emc_request_tests (request_id, test_code, is_selected, "
            "is_developmental, planned_hours, workflow_status, assigned_engineer_id, "
            "assigned_engineer_name, planned_start_date, planned_end_date, created_at, "
            "updated_at) VALUES (:r, :c, 1, 0, 8, 'At Review', :ei, :en, :sd, :ed, "
            "NOW(), NOW())"),
            {"r": rid, "c": CODE, "ei": engineer.id, "en": engineer.username,
             "sd": start, "ed": start + timedelta(days=1)})
        db.session.execute(text(
            "INSERT INTO planner_entries (test_person_name, engineer_user_id, "
            "created_by_user_id, test_name, tco_id, test_request_id, start_date, end_date, "
            "total_hours, event_type, status, peer_reviewer_user_id, created_at, updated_at) "
            "VALUES (:tp, :e, :e, :tn, :t, :r, :sd, :ed, 8, 'test', 'in_progress', :pr, "
            "NOW(), NOW())"),
            {"tp": engineer.username, "e": engineer.id, "tn": PLANNER_NAME, "t": TCO,
             "r": rid, "sd": start, "ed": start + timedelta(days=1), "pr": reviewer.id})
        db.session.commit()
        entry_id = db.session.execute(text(
            "SELECT id FROM planner_entries WHERE tco_id=:t ORDER BY id DESC LIMIT 1"),
            {"t": TCO}).scalar()
        assignment = db.session.get(PlannerEntry, entry_id)

        print("%s  id=%s  %s" % (TCO, rid, PRODUCT))
        print("engineer=%s   reviewer=%s\n" % (engineer.username, reviewer.username))

        today = date.today().strftime("%d/%m/%Y")
        form = base_form(today)
        fixes = {2: fix_round_2, 3: fix_round_3}

        for n, (decision, code, comment) in enumerate(ROUNDS, start=1):
            if n in fixes:
                form = fixes[n](form)
            R.upsert_record(assignment, CODE, form, {}, R.DRAFT,
                            user=engineer, full_projection=True)
            db.session.commit()
            PJ.record_transition(entry_id, "Peer Review", actor=engineer,
                                 comment="Submitted for peer review.", snapshot=True,
                                 submitted=True, from_status="Draft")
            PJ.record_transition(entry_id, decision, actor=reviewer, decided=True,
                                 comment=comment, from_status="Peer Review",
                                 reason_code=code)
            did = db.session.execute(text(
                "SELECT id FROM `datasheet` WHERE planner_entry_id=:p"),
                {"p": entry_id}).scalar()
            print("  round %d  %-8s %-15s -> datasheet %s"
                  % (n, decision, code or "-", did))

        db.session.execute(text(
            "UPDATE planner_entries SET status='datasheet_uploaded' WHERE id=:e"),
            {"e": entry_id})
        db.session.commit()

        print()
        rows = db.session.execute(text(
            "SELECT h.revision_no, h.to_status, COALESCE(h.reason_code,'-') AS code "
            "FROM datasheet_status_history h JOIN `datasheet` d ON d.id=h.datasheet_id "
            "WHERE d.tco_id=:t AND h.to_status IN ('Approved','Rejected') "
            "ORDER BY h.id"), {"t": TCO}).fetchall()
        print("review rounds recorded:")
        for r in rows:
            print("   revision %s  %-9s %s" % tuple(r))
        revs = db.session.execute(text(
            "SELECT COUNT(*) FROM datasheet_revision v JOIN `datasheet` d "
            "ON d.id=v.datasheet_id WHERE d.tco_id=:t"), {"t": TCO}).scalar()
        print("frozen revisions: %s" % revs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
