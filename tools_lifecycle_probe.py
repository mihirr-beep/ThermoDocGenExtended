#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Drive one request through the whole datasheet review lifecycle and OBSERVE it.

    python tools_lifecycle_probe.py           # build and walk the lifecycle
    python tools_lifecycle_probe.py --clean   # remove everything it created

WHY
---
The NLP layer's answers about revisions rest on a model of which table holds
what, and that model was read off the code rather than seen. Reading is not
enough here: the two axes (a product failing a standard, a record being sent
back in review) are stored separately, `datasheet_revision.status` does not mean
what its name suggests, and the child tables use TWO different versioning
mechanisms. Any of those being wrong makes every revision answer wrong.

So this builds the data and prints what actually landed after each step. The
table at the end is an observation, not a claim.

WHAT IT BUILDS
--------------
One request, DEMO-EMC-301, carrying three tests assigned to one lab engineer,
and deliberately leaving them in three DIFFERENT lifecycle states - because the
states are the thing under test:

    RE   filled, never submitted            -> Draft, no revision exists
    ESD  submitted once, approved           -> one frozen revision
    CE   submitted, REJECTED, fixed, approved -> two frozen revisions

Everything is written through project() and record_transition(), the functions
the app itself calls. A fixture that writes the analytical tables directly would
prove the fixture works and nothing else.

The CE form is COPIED from the real CE datasheet on this database with the
identifying fields changed, because a hand-written form_json of subtly the wrong
shape does not error - it projects to nothing, and the empty result looks like a
mapping bug. ESD's observation grids are built from the keys form_extract
actually reads (ind_r1_c1.., dir_r1_name..). RE is left as scalars only and
never submitted, which is what most datasheets in this database really are.

SYNTHETIC AND SAYS SO: is_synthetic=1, product name starts with DEMO, TCO in the
DEMO-EMC-3xx block. --clean removes it.
"""
import json
import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text  # noqa: E402

TCO = "DEMO-EMC-301"
JOB = "DEMO-JOB-301"
PRODUCT = "DEMO Lifecycle Probe Analyser"
MODEL = "DEMO-50199001"
SERIAL = "DEMOSN0000301"
ENGINEER_ROLE = "lab_engineer"

# (request test_code, planner test_name, datasheet code, lifecycle)
PLAN = [
    ("CE", "CE", "CE", "reject_then_approve"),
    ("ESD", "ESD", "ESD", "approve"),
    ("RE", "RE", "RE", "draft_only"),
]

# Tables to watch. datasheet_rev_* are the sixteen mirrors; measurement is
# listed separately on purpose because it versions itself instead.
WATCH_LIVE = ("datasheet", "datasheet_ce", "datasheet_esd", "datasheet_re",
              "datasheet_equipment", "datasheet_software",
              "datasheet_modification", "datasheet_observation",
              "datasheet_observation_legend", "datasheet_measurement")
WATCH_FROZEN = ("datasheet_revision", "datasheet_rev_ce", "datasheet_rev_esd",
                "datasheet_rev_equipment", "datasheet_rev_software",
                "datasheet_rev_modification", "datasheet_rev_observation")
WATCH_AUDIT = ("datasheet_status_history", "datasheet_draft_history")


# --------------------------------------------------------------------------
# observation
# --------------------------------------------------------------------------
def counts(db, did):
    """Rows each watched table holds FOR THIS DATASHEET."""
    out = {}
    for t in WATCH_LIVE + WATCH_FROZEN + WATCH_AUDIT:
        try:
            if t == "datasheet":
                n = db.session.execute(text(
                    "SELECT COUNT(*) FROM `datasheet` WHERE id=:d"), {"d": did}).scalar()
            elif t == "datasheet_draft_history":
                n = db.session.execute(text(
                    "SELECT COUNT(*) FROM datasheet_draft_history WHERE datasheet_id=:d"),
                    {"d": did}).scalar()
            else:
                n = db.session.execute(text(
                    "SELECT COUNT(*) FROM `%s` WHERE datasheet_id=:d" % t),
                    {"d": did}).scalar()
        except Exception:  # noqa: BLE001
            n = "-"
        out[t] = n
    return out


def live_state(db, did):
    row = db.session.execute(text(
        "SELECT status, revision_no, result, met_performance_criteria "
        "FROM `datasheet` WHERE id=:d"), {"d": did}).first()
    return dict(zip(("status", "revision_no", "result", "criteria"), row)) if row else {}


def report(db, did, label):
    c = counts(db, did)
    s = live_state(db, did)
    print("\n  ---- after: %s" % label)
    print("       datasheet.status=%-12s revision_no=%-3s result=%s"
          % (s.get("status"), s.get("revision_no"), s.get("result")))
    live = " ".join("%s=%s" % (t.replace("datasheet_", "") or "datasheet", c[t])
                    for t in WATCH_LIVE if c[t] not in (0, "-"))
    frozen = " ".join("%s=%s" % (t.replace("datasheet_rev_", "rev_")
                                 .replace("datasheet_revision", "revision"), c[t])
                      for t in WATCH_FROZEN if c[t] not in (0, "-"))
    audit = " ".join("%s=%s" % (t.replace("datasheet_", ""), c[t])
                     for t in WATCH_AUDIT if c[t] not in (0, "-"))
    print("       live   : %s" % (live or "(none)"))
    print("       frozen : %s" % (frozen or "(none)"))
    print("       audit  : %s" % (audit or "(none)"))
    return c


# --------------------------------------------------------------------------
# forms
# --------------------------------------------------------------------------
def ce_form(db):
    """The real CE form on this database, re-identified. Shape correct by
    construction - see the module docstring for why this is not hand-written."""
    raw = db.session.execute(text(
        "SELECT form_json FROM datasheet_records WHERE test_code='CE' "
        "AND form_json IS NOT NULL ORDER BY id LIMIT 1")).scalar()
    if not raw:
        return None
    form = json.loads(raw)
    form.update({"tco_id": TCO, "job_number": JOB, "eut_name": PRODUCT,
                 "eut_model": MODEL, "eut_serial": SERIAL,
                 "test_date": date.today().strftime("%d/%m/%Y"),
                 # FAIL, not PASS: this row also carries CE_LIMIT_EXCEEDED, and
                 # a datasheet that says the unit passed AND exceeded the limit
                 # is data that contradicts itself.
                 "overall_result": "FAIL", "result_class": "B",
                 "failure_reason_code": "CE_LIMIT_EXCEEDED"})
    form.pop("assignment_id", None)
    return form


def esd_form():
    """ESD, with observation grids on the keys form_extract really reads:
    indirect is 8 auto-named points, direct and air are 3 named rows each."""
    form = {
        "tco_id": TCO, "job_number": JOB, "eut_name": PRODUCT,
        "eut_model": MODEL, "eut_serial": SERIAL,
        "sop_reference": "IEC-SOP-511",
        "product_standard": "IEC 61326-1 : 2020",
        "basic_standard": "IEC 61000-4-2:2008",
        "test_mode": "Mode A", "eut_configuration": "Tabletop",
        "eut_modification_state": "0",
        "eut_input_voltage_frequency": "230 V, 50 Hz",
        "ambient_temperature": "23.4", "relative_humidity": "48",
        "test_date": date.today().strftime("%d/%m/%Y"),
        "tested_by": "DEMO Engineer",
        "deviation": "NA",
        "required_performance_criteria": "B",
        "met_performance_criteria": "B",
        "monitoring_parameters": "Display readout and alarm state monitored throughout.",
        "test_procedure": "Discharges applied per IEC 61000-4-2 at each point.",
        "rc_network": "330 ohm / 150 pF",
        "direct_contact_discharge": "+/-4 kV",
        "indirect_hcp": "+/-4 kV", "indirect_vcp": "+/-4 kV",
        "air_discharge": "+/-8 kV",
        "atmospheric_air_pressure": "1008 hPa",
        # equipment / software / modification child tables
        "test_equipment_used_rows__c0[]": ["ESD Simulator", "Climatic Sensor"],
        "test_equipment_used_rows__c1[]": ["Teseq", "Rotronic"],
        "test_equipment_used_rows__c2[]": ["NSG 438", "HP23"],
        "test_equipment_used_rows__c3[]": ["DEMO-ESD-01", "DEMO-HP-02"],
        "test_equipment_used_rows__c4[]": ["2027-01-31", "2027-03-15"],
        "software_used_rows__c0[]": ["ESD Control Suite"],
        "software_used_rows__c1[]": ["4.2.1"],
        "eut_modification_rec_rows__c0[]": ["0"],
        "eut_modification_rec_rows__c1[]": ["Initial state"],
        "eut_modification_rec_rows__c2[]": [""],
        "eut_modification_rec_rows__c3[]": [""],
    }
    # indirect: 8 rows x 6 level cells
    for r in range(1, 9):
        for c in range(1, 7):
            form["ind_r%d_c%d" % (r, c)] = "A"
    # direct + air: 3 named rows each
    for prefix, points in (("dir", ["Enclosure seam", "Front panel", "Connector shell"]),
                           ("air", ["Display bezel", "Vent slot", "USB port"])):
        for i, point in enumerate(points, 1):
            form["%s_r%d_name" % (prefix, i)] = point
            for c in range(1, 7):
                form["%s_r%d_c%d" % (prefix, i, c)] = "A" if c < 6 else "B"
    return form


def re_form():
    """RE scalars only, left as a Draft. Not a shortcut - an unsubmitted draft
    with no revision is the state most datasheets in this database are in, and
    the lifecycle model has to account for it."""
    return {
        "tco_id": TCO, "job_number": JOB, "eut_name": PRODUCT,
        "eut_model": MODEL, "eut_serial": SERIAL,
        "sop_reference": "IEC-SOP-506",
        "product_standard": "IEC 61326-1 : 2020",
        "basic_standard": "CISPR 11:2015",
        "test_mode": "Mode A", "eut_configuration": "Tabletop",
        "ambient_temperature": "23.1", "relative_humidity": "47",
        "tested_by": "DEMO Engineer", "deviation": "NA",
        "name_of_the_test": "Radiated Emission",
        "test_distance": "3 m",
    }


FORMS = {"CE": ce_form, "ESD": lambda _db: esd_form(), "RE": lambda _db: re_form()}


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------
def clean(db):
    rid = db.session.execute(text(
        "SELECT id FROM iec_emc_requests WHERE tco_id=:t"), {"t": TCO}).scalar()
    entries = [r[0] for r in db.session.execute(text(
        "SELECT id FROM planner_entries WHERE tco_id=:t"), {"t": TCO}).fetchall()]
    dids = [r[0] for r in db.session.execute(text(
        "SELECT id FROM `datasheet` WHERE tco_id=:t"), {"t": TCO}).fetchall()]
    for did in dids:
        for t in WATCH_FROZEN + WATCH_LIVE[1:] + ("datasheet_status_history",
                                                  "datasheet_draft_history"):
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
        for t in ("iec_emc_request_test_ce", "iec_emc_request_test_esd",
                  "iec_emc_request_test_re", "iec_emc_request_test_standards"):
            try:
                db.session.execute(text(
                    "DELETE FROM `%s` WHERE request_test_id IN "
                    "(SELECT id FROM iec_emc_request_tests WHERE request_id=:r)" % t),
                    {"r": rid})
            except Exception:  # noqa: BLE001
                db.session.rollback()
        db.session.execute(text("DELETE FROM iec_emc_request_tests WHERE request_id=:r"),
                           {"r": rid})
        db.session.execute(text("DELETE FROM iec_emc_requests WHERE id=:r"), {"r": rid})
    db.session.commit()
    print("removed %s (request=%s entries=%s datasheets=%s)" % (TCO, rid, entries, dids))


def build_request(db, engineer):
    cols = {c[0] for c in db.session.execute(text(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_NAME="
        "'iec_emc_requests' AND TABLE_SCHEMA=DATABASE()")).fetchall()}
    synth = ", is_synthetic" if "is_synthetic" in cols else ""
    synth_v = ", 1" if synth else ""
    # Every requester_* column is NOT NULL with no default on this schema, so
    # they are all supplied rather than discovered one failed INSERT at a time.
    db.session.execute(text(
        "INSERT INTO iec_emc_requests (user_id, tco_id, job_number, status, product_name, "
        "manufacturer, manufacturer_address, model_number, serial_number, test_samples, "
        "samples_available_in_lab, requester_name, requester_department, requester_group, "
        "requester_division, requester_site, requester_email, requester_contact, "
        "requester_designation, requester_date, assigned_engineer_id, "
        "assigned_engineer_name, submitted_at, created_at, updated_at%s) "
        "VALUES (:u, :t, :j, 'Test Plan Approved', :p, 'DEMO Instruments', "
        "'1 Demo Way, Testville', :m, :s, 1, 'yes', 'DEMO Requester', 'DEMO Dept', "
        "'DEMO Group', 'DEMO Division', 'DEMO Site', 'demo.requester@example.invalid', "
        "'0000000000', 'Engineer', CURDATE(), :ei, :en, NOW(), NOW(), NOW()%s)"
        % (synth, synth_v)),
        {"u": engineer.id, "t": TCO, "j": JOB, "p": PRODUCT, "m": MODEL, "s": SERIAL,
         "ei": engineer.id, "en": engineer.username})
    db.session.commit()
    rid = db.session.execute(text(
        "SELECT id FROM iec_emc_requests WHERE tco_id=:t"), {"t": TCO}).scalar()

    start = date.today() - timedelta(days=6)
    entries = {}
    for i, (rcode, pname, dcode, _life) in enumerate(PLAN):
        db.session.execute(text(
            "INSERT INTO iec_emc_request_tests (request_id, test_code, is_selected, "
            "is_developmental, planned_hours, workflow_status, assigned_engineer_id, "
            "assigned_engineer_name, planned_start_date, planned_end_date, created_at, "
            "updated_at) VALUES (:r, :c, 1, 0, 8, 'Assigned Lab Engineer', :ei, :en, "
            ":sd, :ed, NOW(), NOW())"),
            {"r": rid, "c": rcode, "ei": engineer.id, "en": engineer.username,
             "sd": start + timedelta(days=i), "ed": start + timedelta(days=i + 1)})
        db.session.execute(text(
            "INSERT INTO planner_entries (test_person_name, engineer_user_id, "
            "created_by_user_id, test_name, tco_id, test_request_id, start_date, end_date, "
            "total_hours, event_type, status, created_at, updated_at) "
            "VALUES (:tp, :e, :e, :tn, :t, :r, :sd, :ed, 8, 'test', 'in_progress', "
            "NOW(), NOW())"),
            {"tp": engineer.username, "e": engineer.id, "tn": pname, "t": TCO, "r": rid,
             "sd": start + timedelta(days=i), "ed": start + timedelta(days=i + 1)})
        entries[dcode] = db.session.execute(text(
            "SELECT id FROM planner_entries WHERE tco_id=:t AND test_name=:n "
            "ORDER BY id DESC LIMIT 1"), {"t": TCO, "n": pname}).scalar()
    db.session.commit()
    return rid, entries


def fill(db, R, rid, entry_id, code, form, engineer):
    """Save through records.upsert_record - the function the app itself calls.

    An earlier version INSERTed into datasheet_records directly and projected by
    hand. It looked equivalent and was not: upsert_record derives
    ``result`` from the form (overall_result / result), appends to
    datasheet_draft_history, and picks the projection tier. Skipping it left
    datasheet_records.result NULL, which propagated into
    datasheet_revision.result and read exactly like the known defect where no
    frozen revision records whether it passed. The fixture was manufacturing
    the bug it was meant to detect - so it now uses the real entry point.
    """
    from models import PlannerEntry
    assignment = db.session.get(PlannerEntry, entry_id)
    R.upsert_record(assignment, code, form, {}, R.DRAFT,
                    user=engineer, full_projection=True)
    db.session.commit()
    return db.session.execute(text(
        "SELECT id FROM `datasheet` WHERE planner_entry_id=:p"), {"p": entry_id}).scalar()


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    import app as app_module
    from models import db, User
    import datasheet_gen.projection as PJ
    import datasheet_gen.records as R

    a = app_module.create_app("default")
    with a.app_context():
        if "--clean" in sys.argv:
            clean(db)
            return 0

        clean(db)   # idempotent: re-runnable without piling up duplicates

        engineer = db.session.query(User).filter_by(role=ENGINEER_ROLE, is_active=True) \
                             .order_by(User.id).first()
        reviewer = db.session.query(User).filter_by(role="admin", is_active=True) \
                             .order_by(User.id).first()
        if not engineer or not reviewer:
            print("need one active lab_engineer and one active admin")
            return 1
        print("engineer: %s (id %s)   reviewer: %s (id %s)"
              % (engineer.username, engineer.id, reviewer.username, reviewer.id))

        rid, entries = build_request(db, engineer)
        print("request %s id=%s with %d tests, all assigned to %s"
              % (TCO, rid, len(PLAN), engineer.username))

        dids = {}
        for rcode, pname, dcode, life in PLAN:
            builder = FORMS[dcode]
            form = builder(db)
            if form is None:
                print("  %-4s SKIPPED - no donor form on this database" % dcode)
                continue
            did = fill(db, R, rid, entries[dcode], dcode, form, engineer)
            dids[dcode] = did
            print("\n=== %s (planner entry %s, datasheet %s) - %s"
                  % (dcode, entries[dcode], did, life))
            report(db, did, "engineer saved the form")

            if life == "draft_only":
                continue

            PJ.record_transition(entries[dcode], "Peer Review", actor=engineer,
                                 comment="Submitted for peer review.", snapshot=True,
                                 submitted=True, from_status="Draft")
            report(db, did, "SUBMIT #1 (snapshot=True)")

            if life == "reject_then_approve":
                PJ.record_transition(
                    entries[dcode], "Rejected", actor=reviewer, decided=True,
                    comment="LISN calibration date missing from the equipment list.",
                    from_status="Peer Review", reason_code="CAL_EXPIRED")
                report(db, did, "REJECT (reason_code=CAL_EXPIRED)")

                form["measurement_uncertainty"] = "+/- 3.400 dB"
                form["deviation"] = "Calibration date added after review."
                fill(db, R, rid, entries[dcode], dcode, form, engineer)
                report(db, did, "engineer edited and re-saved")

                PJ.record_transition(entries[dcode], "Peer Review", actor=engineer,
                                     comment="Calibration date added. Resubmitted.",
                                     snapshot=True, submitted=True, from_status="Draft")
                report(db, did, "SUBMIT #2 (snapshot=True)")

            PJ.record_transition(entries[dcode], "Approved", actor=reviewer,
                                 comment="Checked against the raw data. Approved.",
                                 decided=True, from_status="Peer Review")
            report(db, did, "APPROVE")
            db.session.execute(text(
                "UPDATE planner_entries SET status='datasheet_uploaded' WHERE id=:e"),
                {"e": entries[dcode]})
            db.session.commit()

        # ---- the questions the NLP layer will actually be asked --------------
        print("\n" + "=" * 74)
        print("WHAT THE TABLES NOW SAY")
        print("=" * 74)
        for label, sql in (
            ("datasheet_revision.status (does it hold the OUTCOME?)",
             "SELECT d.test_code, r.revision_no, r.status FROM datasheet_revision r "
             "JOIN `datasheet` d ON d.id=r.datasheet_id WHERE d.tco_id=:t "
             "ORDER BY d.test_code, r.revision_no"),
            ("datasheet_status_history (where the outcome really is)",
             "SELECT d.test_code, h.revision_no, h.from_status, h.to_status, h.reason_code "
             "FROM datasheet_status_history h JOIN `datasheet` d ON d.id=h.datasheet_id "
             "WHERE d.tco_id=:t ORDER BY d.test_code, h.id"),
            ("live datasheet after it all",
             "SELECT test_code, status, revision_no, result FROM `datasheet` "
             "WHERE tco_id=:t ORDER BY test_code"),
            ("which revisions were REJECTED (the real join)",
             "SELECT d.test_code, h.revision_no, h.reason_code FROM datasheet_status_history h "
             "JOIN `datasheet` d ON d.id=h.datasheet_id WHERE d.tco_id=:t "
             "AND h.to_status='Rejected'"),
            ("measurements: versioned IN PLACE, no mirror",
             "SELECT d.test_code, m.revision_no, COUNT(*) cells FROM datasheet_measurement m "
             "JOIN `datasheet` d ON d.id=m.datasheet_id WHERE d.tco_id=:t "
             "GROUP BY d.test_code, m.revision_no ORDER BY d.test_code, m.revision_no"),
            ("observations: versioned BY COPY - current",
             "SELECT d.test_code, COUNT(*) cells FROM datasheet_observation o "
             "JOIN `datasheet` d ON d.id=o.datasheet_id WHERE d.tco_id=:t GROUP BY d.test_code"),
            ("observations: versioned BY COPY - frozen",
             "SELECT d.test_code, o.revision_no, COUNT(*) cells FROM datasheet_rev_observation o "
             "JOIN `datasheet` d ON d.id=o.datasheet_id WHERE d.tco_id=:t "
             "GROUP BY d.test_code, o.revision_no ORDER BY d.test_code, o.revision_no"),
        ):
            print("\n-- %s" % label)
            try:
                rows = db.session.execute(text(sql), {"t": TCO}).fetchall()
            except Exception as exc:  # noqa: BLE001
                print("   ERROR %s" % exc)
                continue
            if not rows:
                print("   (no rows)")
            for r in rows:
                print("   %s" % (tuple(r),))
    return 0


if __name__ == "__main__":
    sys.exit(main())
