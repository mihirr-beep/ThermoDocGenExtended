# -*- coding: utf-8 -*-
"""One request, all eleven IEC-FRM-516 report sections, built through the real
generate endpoint - so the check is whether the wired-up app produces a correct
combined report, not whether the builder function can be called correctly.

    python tools_seed_report_all11.py             # create + fill + generate
    python tools_seed_report_all11.py --purge     # remove everything

WHY A NEW REQUEST RATHER THAN EXTENDING THE EXISTING FOUR-TEST FIXTURE
------------------------------------------------------------------------
tools_seed_full_request.py already builds one request (IEC-EMC-900) with every
column filled, but only four tests: CE, RE, EFT, ESD - enough to prove PASS/FAIL
vs a performance criterion, not enough to prove every one of the eleven report
sections assembles. Extending that TCO in place risks whatever else on this
branch already depends on it staying at exactly four. This uses a distinct TCO
so the existing fixture is untouched.

WHY THE DATASHEETS ARE DONOR-COPIED, ONE DONOR PER TEST
--------------------------------------------------------
A hand-written form_json of subtly the wrong shape does not error - it projects
to nothing, and the report then shows a blank section that looks identical to a
mapping bug. That has already cost this project twice. So each of the eleven
forms is copied from the best REAL filled example of that test anywhere in the
database (measured: which TCO has the largest form_json for that planner
test_name), with only the identifying fields changed. No single existing
request has all eleven filled, so there is one donor per test rather than one
donor for the whole set.

WHY EACH DATASHEET IS ACTUALLY RENDERED, NOT JUST PROJECTED
-------------------------------------------------------------
report_gen prefers to SPLICE each section from that test's own approved .docx
(report_gen/service.py:_datasheet_path, report_gen/builder.py step 3) rather than
refill the template from field values, and falls back to the template fill only
when no .docx exists. Writing straight into datasheet_records and skipping the
real render would make every one of the eleven silently take the fallback path -
which is not what a real submission does, and not what this branch's own recent
commits ("Datasheet fixes: picture labels, Harmonic table, CRF/PFMF/VDIPS
layout, ESD levels") need exercised. So each test is generated through the same
two calls its real route makes - generic_service.build_context +
generic_generator.render, or service.build_ce_context + generator.render_ce_-
datasheet for CE - producing a real .docx on disk and a real generated_file_path,
exactly like a lab engineer clicking Generate.

WHY THE FINAL REPORT IS BUILT THROUGH THE HTTP ROUTE, NOT builder.build_report()
---------------------------------------------------------------------------------
Calling the builder function directly would prove the builder works, not that
the app produces a correct report when someone clicks the button. This uses
Flask's in-process test client against
``POST /api/test-requests/<id>/generate-test-report`` - the exact route the UI
calls - so auth, the datasheet-requirement check, and the report-approval side
effects all run for real.
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text  # noqa: E402

TCO = "IEC-EMC-911"
JOB = "TFS-EMC-2026-911"
PRODUCT = "REPORTCHECK Bench RC-911"

# (request-side test_code, report/registry code, donor tco, kind, criterion)
# kind: "emission" reports PASS/FAIL via overall_result; "immunity" reports a
# performance criterion via met_performance_criteria; "vdips" is left alone -
# Voltage Dips records one criterion PER LEVEL in its own vdips_met_criteria[]
# array (report_gen/builder.py:_vdips_criteria), so touching a single flat key
# would not represent it and the donor's own array is used untouched.
#
# Donor TCOs are the largest real form_json found for that planner test_name,
# measured directly against this database - see the module docstring.
PLAN = [
    ("CE",           "CE",             "IEC-EMC-006",  "emission", None),
    ("RE",           "RE",             "DEMO-EMC-301", "emission", None),
    ("HARMONIC",     "HARMONIC",       "IEC-EMC-004",  "emission", None),
    ("FLICKER",      "VOLTAGEFLICKER", "IEC-EMC-004",  "emission", None),
    ("EFT",          "EFT",            "IEC-EMC-005",  "immunity", "B"),
    ("ESD",          "ESD",            "DEMO-EMC-314", "immunity", "A"),
    ("SURGE",        "SURGE",          "IEC-EMC-004",  "immunity", "B"),
    ("CRF",          "CRF",            "DEMO-EMC-311", "immunity", "A"),
    ("RS",           "RS_RI",          "IEC-EMC-004",  "immunity", "A"),
    ("POWER_FREQ",   "PFMF",           "IEC-EMC-004",  "immunity", "A"),
    ("VOLTAGE_DIPS", "VOLTAGEDIPS",    "IEC-EMC-004",  "vdips",    None),
]

# ESD alone reaches modification state 2, which is the case the report has to
# widen 2.4 into rows 0, 1 AND 2 - state 1 belongs to no test. Every other test
# stays at state 0. Same deliberate design as tools_seed_full_request_sheets.py.
EXTRA_MODIFICATION_CODE = "ESD"
EXTRA_MODIFICATION = ("2", "Ferrite sleeve fitted on the sensor harness",
                      "Engineering", "2026-06-14")

SOFTWARE = [("EMC32 Measurement Suite", "10.60.1"), ("FB900 Monitor", "3.4.0")]

MODES = [
    "Normal operating mode - dispensing at 2 L/min, display active",
    "Standby mode - pump idle, UV lamp off, display dimmed",
    "Service mode - diagnostics running, all valves cycled",
]

# request-side per-test detail table + a plausible fill, modelled on the
# columns actually measured on this database (see module docstring). The
# request's OWN declared standard is the IEC/EN pair each test is derived from
# (_DERIVED_BASIC_STANDARDS / basic_standard_map, same split used lab-wide).
REQUEST_SPEC = {
    "CE":     ("iec_emc_request_test_ce", {"voltage_freq": "230 V / 50 Hz",
              "freq_range": "150 kHz - 30 MHz", "cables": "Mains cordset",
              "ce_class": "Class B"}),
    "RE":     ("iec_emc_request_test_re", {"voltage_freq": "230 V / 50 Hz",
              "freq_range": "30 MHz - 1 GHz", "re_class": "Class B"}),
    "EFT":    ("iec_emc_request_test_eft", {"voltage_freq": "230 V / 50 Hz",
              "cables_power": "Mains cordset", "cables_signal": "Sensor harness",
              "test_level1": "+/-1 kV"}),
    "ESD":    ("iec_emc_request_test_esd", {"voltage_freq": "230 V / 50 Hz",
              "contact_level": "+/-4 kV", "air_level": "+/-8 kV"}),
    "SURGE":  ("iec_emc_request_test_surge", {"voltage_freq": "230 V / 50 Hz",
              "cables_power": "Mains cordset", "cables_signal": "Sensor harness",
              "cm1": "+/-2 kV", "dm1": "+/-1 kV"}),
    "CRF":    ("iec_emc_request_test_crf", {"voltage_freq": "230 V / 50 Hz",
              "freq_range": "150 kHz - 80 MHz", "cables_power": "Mains cordset",
              "cables_signal": "Sensor harness", "test_level1": "3 V"}),
    "RS":     ("iec_emc_request_test_rs", {"voltage_freq": "230 V / 50 Hz",
              "freq_range": "80 MHz - 6 GHz", "field_strength1": "3 V/m"}),
    "HARMONIC": ("iec_emc_request_test_harmonic", {"voltage_freq": "230 V / 50 Hz",
              "harmonic_class": "Class A"}),
    "FLICKER": ("iec_emc_request_test_flicker", {"voltage_freq": "230 V / 50 Hz",
              "custom_specification": "As per the standard"}),
    "POWER_FREQ": ("iec_emc_request_test_power_freq", {"voltage_freq": "230 V / 50 Hz",
              "test_level": "3 A/m"}),
    "VOLTAGE_DIPS": ("iec_emc_request_test_voltage_dips", {"voltage_freq": "230 V / 50 Hz",
              "voltage_dip1": "30%", "voltage_dip2": "60%", "voltage_dip3": "100%",
              "interruption": "100%", "time1": "10 ms", "time2": "100 ms",
              "time3": "1000 ms", "time4": "5000 ms"}),
}
REQUEST_STANDARDS = {
    "CE": ["CISPR 11", "EN 55011"], "RE": ["CISPR 11", "EN 55011"],
    "EFT": ["IEC 61000-4-4", "EN 61000-4-4"], "ESD": ["IEC 61000-4-2", "EN 61000-4-2"],
    "SURGE": ["IEC 61000-4-5", "EN 61000-4-5"], "CRF": ["IEC 61000-4-6", "EN 61000-4-6"],
    "RS": ["IEC 61000-4-3", "EN 61000-4-3"], "HARMONIC": ["IEC 61000-3-2", "EN 61000-3-2"],
    "FLICKER": ["IEC 61000-3-3", "EN 61000-3-3"],
    "POWER_FREQ": ["IEC 61000-4-8", "EN 61000-4-8"],
    "VOLTAGE_DIPS": ["IEC 61000-4-11", "EN 61000-4-11"],
}


def _cols(db, table):
    return {r[0] for r in db.session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=DATABASE() AND table_name=:t"), {"t": table}).fetchall()}


def purge(db):
    """Remove everything this script created, driven by information_schema so
    a table this script doesn't know the name of is never left holding rows."""
    rid = db.session.execute(text(
        "SELECT id FROM iec_emc_requests WHERE tco_id=:t"), {"t": TCO}).scalar()
    if not rid:
        return False
    ent = [r[0] for r in db.session.execute(text(
        "SELECT id FROM planner_entries WHERE test_request_id=:r"), {"r": rid}).fetchall()] or [-1]
    dids = [r[0] for r in db.session.execute(text(
        "SELECT id FROM `datasheet` WHERE planner_entry_id IN :e"), {"e": tuple(ent)}).fetchall()] or [-1]
    child_tables = [r[0] for r in db.session.execute(text(
        "SELECT TABLE_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND COLUMN_NAME='datasheet_id' "
        "AND TABLE_NAME LIKE 'datasheet%'")).fetchall()]
    for t in child_tables:
        try:
            db.session.execute(text("DELETE FROM `%s` WHERE datasheet_id IN :d" % t),
                               {"d": tuple(dids)})
        except Exception:
            db.session.rollback()
    db.session.execute(text("DELETE FROM `datasheet` WHERE id IN :d"), {"d": tuple(dids)})
    db.session.execute(text("DELETE FROM datasheet_draft_history WHERE planner_entry_id IN :e"),
                       {"e": tuple(ent)})
    db.session.execute(text("DELETE FROM datasheet_records WHERE planner_entry_id IN :e"),
                       {"e": tuple(ent)})
    db.session.execute(text("DELETE FROM planner_entries WHERE id IN :e"), {"e": tuple(ent)})
    for child, key in (("iec_emc_request_accessories", "request_id"),
                       ("iec_emc_request_cables", "request_id"),
                       ("iec_emc_request_categories", "request_id"),
                       ("iec_emc_request_decision_rules", "request_id"),
                       ("iec_emc_request_eut_specs", "request_id"),
                       ("iec_emc_request_functional_modes", "request_id"),
                       ("iec_emc_request_product_environments", "request_id"),
                       ("iec_emc_request_product_standards", "request_id"),
                       ("iec_emc_request_serial_numbers", "request_id"),
                       ("iec_emc_request_service_types", "request_id"),
                       ("iec_emc_request_supply_vf", "request_id")):
        try:
            db.session.execute(text("DELETE FROM `%s` WHERE %s=:r" % (child, key)), {"r": rid})
        except Exception:
            db.session.rollback()
    tids = [r[0] for r in db.session.execute(text(
        "SELECT id FROM iec_emc_request_tests WHERE request_id=:r"), {"r": rid}).fetchall()] or [-1]
    test_child_tables = [r[0] for r in db.session.execute(text(
        "SELECT TABLE_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND COLUMN_NAME='request_test_id'")).fetchall()]
    for t in test_child_tables:
        try:
            db.session.execute(text("DELETE FROM `%s` WHERE request_test_id IN :t" % t),
                               {"t": tuple(tids)})
        except Exception:
            db.session.rollback()
    db.session.execute(text("DELETE FROM iec_emc_request_tests WHERE request_id=:r"), {"r": rid})
    db.session.execute(text("DELETE FROM iec_emc_requests WHERE id=:r"), {"r": rid})
    db.session.commit()
    return True


def create_request(db, engineer, manager):
    today = date.today()
    recv = today - timedelta(days=21)
    vals = {
        "user_id": engineer.id, "tco_id": TCO, "job_id": JOB, "job_number": JOB,
        "status": "Assigned Lab Engineer", "product_name": PRODUCT,
        "manufacturer": "Thermo Fisher Scientific",
        "manufacturer_address": "Thermo Fisher Scientific\nRobert-Bosch-Strasse 1\n"
                                "D-63505 Langenselbold, Germany",
        "model_number": "RC-911-UVUF", "serial_number": "RC911-2026-000417",
        "test_samples": 1, "samples_available_in_lab": "yes",
        "has_model_variance": "no",
        "project_details_intent": "EMI/EMC compliance testing for CE marking - "
                                  "full eleven-test scope, one sample",
        "has_wireless_interface": "no",
        "dimension_unit": "mm", "length": 420.0, "width": 315.0, "height": 560.0,
        "weight": 31.5, "operating_frequency": "50/60",
        "product_type": "Tabletop", "type_others": "",
        "product_description":
            "The REPORTCHECK Bench RC-911 is a fixture built to exercise every "
            "section of the combined EMI/EMC test report in one document.",
        "test_configuration":
            "1. Power from a single-phase 230 V / 50 Hz supply through the LISN.\n"
            "2. Run the bench in Normal operating mode for the duration of each test.",
        "operation_modes": "\n".join(MODES),
        "monitoring_parameters":
            "1. No error message on the front display.\n"
            "2. No unintended valve actuation.",
        "product_environment_other": "",
        "product_group": "Group 1", "class_type": "Class B",
        "continue_testing": "YES", "test_report_required": "YES",
        "uncertainty_required": "YES", "test_witness": "NO",
        "conformity_required": "YES", "conformity_statement": "YES",
        "number_of_modes": len(MODES),
        "requester_name": "Athina Ramanathan", "requester_department": "Department - 8",
        "requester_group": "LPD", "requester_division": "WLP",
        "requester_site": "Chelmsford",
        "requester_email": "athina.ramanathan@thermofisher.com",
        "requester_contact": "8524939830",
        "requester_designation": "Senior Design Engineer",
        "requester_date": recv, "requester_expected_completion_date": today + timedelta(days=7),
        "requester_status": "At Review", "requester_signature": "A.Ramanathan",
        "sample_condition": "Received in good condition, no transit damage",
        "capability_available": "yes", "sample_received_date": recv,
        "test_duration": "11.0",
        "test_commencement_date": recv + timedelta(days=2),
        "test_completion_date": recv + timedelta(days=10),
        "lab_manager_name": manager.username, "lab_manager_date": recv,
        "lab_manager_signature": manager.username, "lab_manager_signed_at": datetime.now(),
        "assigned_engineer_id": engineer.id, "assigned_engineer_name": engineer.username,
        "assignment_priority": "normal", "assignment_due_date": today + timedelta(days=5),
        "assignment_notes": "Full eleven-test scope on one sample - report assembly check.",
        "review_comments": "Scope confirmed with the requester on the kick-off call.",
        "reviewed_by": manager.username, "reviewed_at": datetime.now(),
        "submitted_at": datetime.now(), "is_synthetic": 1,
    }
    have = _cols(db, "iec_emc_requests")
    vals = {k: v for k, v in vals.items() if k in have}
    cols = ", ".join("`%s`" % k for k in vals)
    binds = ", ".join(":%s" % k for k in vals)
    db.session.execute(text(
        "INSERT INTO iec_emc_requests (%s, created_at, updated_at) "
        "VALUES (%s, NOW(), NOW())" % (cols, binds)), vals)
    rid = db.session.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    db.session.commit()
    return rid


def create_children(db, rid):
    def ins(table, col, values):
        for i, v in enumerate(values):
            db.session.execute(text(
                "INSERT INTO `%s` (request_id, %s, sort_order) VALUES (:r, :v, :s)"
                % (table, col)), {"r": rid, "v": v, "s": i})

    ins("iec_emc_request_accessories", "accessory_value", [
        json.dumps({"equipmentName": "External pre-treatment cartridge",
                    "make": "Thermo Fisher", "model": "PT-911", "serial": "PT911-0012"})])
    ins("iec_emc_request_cables", "cable_value", [
        json.dumps({"cableName": "Mains cordset", "length": "2", "powerSignal": "Power",
                    "shielded": "Unshielded", "purpose": "Mains supply to the bench"}),
        json.dumps({"cableName": "Sensor harness", "length": "1.5", "powerSignal": "Signal",
                    "shielded": "Shielded", "purpose": "Conductivity and flow sensors"})])
    ins("iec_emc_request_categories", "category_name", ["Laboratory"])
    ins("iec_emc_request_decision_rules", "rule_value", ["standard_measured"])
    ins("iec_emc_request_eut_specs", "spec_value", [json.dumps({
        "acVoltageRange": "100-240", "acFreqRange": "50-60", "acInputCurrent": "2.4",
        "dcVoltageRange": "24", "dcInputCurrent": "5.0", "ratedPower": "150"})])
    ins("iec_emc_request_functional_modes", "mode_value", MODES)
    for i, (key, val) in enumerate((("non_medical", "Basic Electromagnetic"),
                                    ("medical", ""), ("custom", ""))):
        db.session.execute(text(
            "INSERT INTO iec_emc_request_product_environments "
            "(request_id, environment_key, environment_value, sort_order) "
            "VALUES (:r, :k, :v, :s)"), {"r": rid, "k": key, "v": val, "s": i})
    ins("iec_emc_request_product_standards", "standard_value", [
        "IEC 61326-1 : 2020", "EN 61326-1 : 2021", "FCC Subpart 15B : 2024"])
    ins("iec_emc_request_serial_numbers", "serial_number", ["RC911-2026-000417"])
    ins("iec_emc_request_service_types", "service_type", ["Compliance"])
    ins("iec_emc_request_supply_vf", "value_text", [
        json.dumps({"voltage": "230", "frequency": "50", "notes": "primary supply"})])
    db.session.commit()


def create_tests(db, rid, engineer):
    today = date.today()
    ids = {}
    for i, (req_code, _report_code, _donor, _kind, _crit) in enumerate(PLAN):
        db.session.execute(text(
            "INSERT INTO iec_emc_request_tests (request_id, test_code, is_selected, "
            "is_developmental, planned_hours, assigned_engineer_id, assigned_engineer_name, "
            "planned_start_date, planned_end_date, created_at, updated_at) "
            "VALUES (:r, :c, 1, 0, :h, :e, :en, :s, :d, NOW(), NOW())"),
            {"r": rid, "c": req_code, "h": 2.5, "e": engineer.id, "en": engineer.username,
             "s": today - timedelta(days=25 - i), "d": today - timedelta(days=24 - i)})
        ids[req_code] = db.session.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    for req_code, tid in ids.items():
        for i, s in enumerate(REQUEST_STANDARDS[req_code]):
            db.session.execute(text(
                "INSERT INTO iec_emc_request_test_standards (request_test_id, "
                "standard_value, sort_order) VALUES (:t, :v, :s)"), {"t": tid, "v": s, "s": i})
        table, fields = REQUEST_SPEC[req_code]
        have = _cols(db, table)
        use = {k: v for k, v in fields.items() if k in have}
        if not use:
            continue
        cols = ", ".join("`%s`" % k for k in use)
        binds = ", ".join(":%s" % k for k in use)
        payload = dict(use)
        payload["t"] = tid
        db.session.execute(text(
            "INSERT INTO `%s` (request_test_id, %s) VALUES (:t, %s)"
            % (table, cols, binds)), payload)
    db.session.commit()
    return ids


def _donor_form(db, report_code, donor_tco):
    raw = db.session.execute(text("""
        SELECT dr.form_json FROM planner_entries p
        JOIN datasheet_records dr ON dr.planner_entry_id = p.id
        JOIN iec_emc_requests r ON r.id = p.test_request_id
        WHERE r.tco_id = :donor AND UPPER(p.test_name) = :c
        ORDER BY dr.id DESC LIMIT 1"""), {"donor": donor_tco, "c": report_code}).scalar()
    return json.loads(raw) if raw else {}


def _retarget(form, report_code, kind, criterion, engineer, when):
    f = dict(form)
    f.update({
        "tco_id": TCO, "job_number": JOB,
        "test_date": when.isoformat(), "date": when.isoformat(),
        "tested_by": engineer.username,
        "ambient_temperature": "23", "relative_humidity": "47",
        "eut_name": PRODUCT, "eut_model_sku_number": "RC-911-UVUF",
        "eut_serial_number": "RC911-2026-000417",
        "eut_input_voltage_frequency": "230 V / 50 Hz",
        "eut_configuration": "Tabletop, mains powered, sensor harness connected",
        "monitoring_parameters": "No error message; no unintended valve actuation",
        "test_mode": "Mode A: Normal operating mode",
    })
    if kind == "emission":
        f["result"] = "PASS"
        f["overall_result"] = "PASS"
    elif kind == "immunity":
        f["met_performance_criteria"] = criterion
        f["required_performance_criteria"] = criterion
    # kind == "vdips": deliberately untouched - see PLAN comment.

    if report_code == "CE":
        f["software_used"] = SOFTWARE[0][0]
        f["software_version"] = SOFTWARE[0][1]
    else:
        f["software_used_rows__c0[]"] = [s[0] for s in SOFTWARE]
        f["software_used_rows__c1[]"] = [s[1] for s in SOFTWARE]

    rows = [("0", "Initial state", "", "")]
    if report_code == EXTRA_MODIFICATION_CODE:
        rows.append(EXTRA_MODIFICATION)
    if report_code == "CE":
        f["mod_state[]"] = [r[0] for r in rows]
        f["mod_description[]"] = [r[1] for r in rows]
        f["mod_fitted_by[]"] = [r[2] for r in rows]
        f["mod_date[]"] = [r[3] for r in rows]
    else:
        for i in range(4):
            f["eut_modification_rec_rows__c%d[]" % i] = [r[i] for r in rows]
    return f


def _render_one(db, report_code, entry, request_orm, form_data, tag):
    """Build the real .docx through the same two calls the live route makes.
    Returns the output path."""
    from datasheet_gen.routes import _output_dir
    ts = datetime.now().strftime("%Y%m%d_%H%M%S") + ("_%s" % tag)
    out = os.path.join(_output_dir(), "%s_%s_%s.docx" % (TCO, report_code, ts))
    if report_code == "CE":
        from datasheet_gen.service import build_ce_context
        from datasheet_gen.generator import render_ce_datasheet
        ctx = build_ce_context(form_data)
        render_ce_datasheet(ctx, out, images={})
    else:
        from datasheet_gen import generic_service as gs
        from datasheet_gen import generic_generator as gg
        from datasheet_gen.registry import load_schema
        schema = load_schema(report_code)
        ctx = gs.build_context(schema, form_data, request_obj=request_orm)
        ikeys = gs.image_keys(schema)
        gg.render(report_code, ctx, ikeys, {}, out)
    return out


def process_test(db, rid, engineer, reviewer, request_orm, req_code, report_code,
                 donor_tco, kind, criterion, when):
    from models import PlannerEntry
    from datasheet_gen import records as R
    from datasheet_gen.projection import record_transition

    entry_id = db.session.execute(text(
        "SELECT id FROM planner_entries WHERE test_request_id=:r AND test_name=:n"),
        {"r": rid, "n": report_code}).scalar()
    if not entry_id:
        db.session.execute(text(
            "INSERT INTO planner_entries (test_request_id, tco_id, test_name, "
            "test_person_name, engineer_user_id, peer_reviewer_user_id, "
            "start_date, end_date, status, created_at, updated_at) "
            "VALUES (:r, :t, :n, :pn, :e, :pr, :s, :d, 'in_progress', NOW(), NOW())"),
            {"r": rid, "t": TCO, "n": report_code, "pn": engineer.username,
             "e": engineer.id, "pr": reviewer.id, "s": when, "d": when + timedelta(days=1)})
        entry_id = db.session.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        db.session.commit()

    entry = db.session.get(PlannerEntry, entry_id)
    form = _retarget(_donor_form(db, report_code, donor_tco), report_code, kind,
                     criterion, engineer, when)

    # 1. "send to peer review": the real render, then the real route's own writes.
    out1 = _render_one(db, report_code, entry, request_orm, form, "review")
    entry.datasheet_file_path = out1
    entry.datasheet_uploaded_at = when
    entry.datasheet_uploaded_by = engineer.id
    entry.peer_reviewer_user_id = reviewer.id
    entry.peer_review_assigned_at = when
    entry.status = "Peer Review"
    db.session.commit()
    R.upsert_record(entry, report_code, form, {}, R.SUBMITTED,
                    generated_file_path=out1, user=engineer, full_projection=True)
    record_transition(entry.id, "Peer Review", actor=engineer, from_status="Draft",
                      snapshot=True, submitted=True,
                      comment="Sent for peer review (report assembly fixture).")

    # 2. peer review approves.
    record_transition(entry.id, "Approved", actor=reviewer, from_status="Peer Review",
                      decided=True,
                      comment="Record checked against the raw data. Approved.")
    entry.status = "datasheet_uploaded"
    db.session.commit()

    # 3. post-approval "generate final": re-render from the approved saved data,
    # exactly what /generate-final does, so datasheet_file_path points at the
    # document the report will actually splice.
    saved = R.draft_form(entry.id) or form
    out2 = _render_one(db, report_code, entry, request_orm, saved, "final")
    entry.datasheet_file_path = out2
    db.session.commit()

    mods = db.session.execute(text(
        "SELECT COUNT(*) FROM datasheet_modification mo JOIN `datasheet` d "
        "ON d.id=mo.datasheet_id WHERE d.planner_entry_id=:p"), {"p": entry_id}).scalar()
    print("   %-14s entry=%-5s status=%s  file=%s  mod_rows=%s"
          % (report_code, entry_id, entry.status,
             os.path.basename(out2), mods))
    return entry_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--purge", action="store_true")
    ap.add_argument("--no-generate", action="store_true",
                    help="seed and fill only; skip the real generate-test-report call")
    args = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    import app as app_module
    from models import db, User, EMCRequest

    a = app_module.create_app("default")
    with a.app_context():
        if purge(db):
            print("removed the previous %s" % TCO)
        if args.purge:
            return 0

        users = db.session.query(User).order_by(User.id).all()
        admin = next((u for u in users if getattr(u, "role", "") == "admin"
                     and getattr(u, "is_active", 1)), users[0])
        engineer = users[0]
        reviewer = next((u for u in users if u.id != engineer.id), engineer)

        rid = create_request(db, engineer, reviewer)
        create_children(db, rid)
        test_ids = create_tests(db, rid, engineer)
        print("created request %s (id %s) - %s" % (TCO, rid, PRODUCT))
        print("   request-side tests: %s" % ", ".join(test_ids))

        request_orm = db.session.get(EMCRequest, rid)
        start = date.today() - timedelta(days=20)
        print("\nfilling and rendering all eleven datasheets:")
        for i, (req_code, report_code, donor_tco, kind, criterion) in enumerate(PLAN):
            when = start + timedelta(days=i)
            process_test(db, rid, engineer, reviewer, request_orm,
                        req_code, report_code, donor_tco, kind, criterion, when)

        if args.no_generate:
            print("\n--no-generate: stopping before the report call")
            return 0

        print("\ngenerating the combined report through the real endpoint...")
        app_module_app = a
        app_module_app.config["WTF_CSRF_ENABLED"] = False
        app_module_app.config["PROPAGATE_EXCEPTIONS"] = True
        app_module_app.login_manager.session_protection = None
        client = app_module_app.test_client()
        with client.session_transaction() as sess:
            sess["_user_id"] = str(admin.id)
            sess["_fresh"] = True
        r = client.post("/api/test-requests/%d/generate-test-report" % rid,
                        json={"comments": "all-eleven-sections fixture"})
        body = r.get_json() or {}
        print("HTTP %s" % r.status_code)
        print(json.dumps(body, indent=2, ensure_ascii=False)[:2000])
        if r.status_code == 200 and body.get("file_path"):
            path = body["file_path"]
            print("\nreport written to: %s (exists=%s)" % (path, os.path.exists(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
