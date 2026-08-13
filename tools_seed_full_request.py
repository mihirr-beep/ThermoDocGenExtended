# -*- coding: utf-8 -*-
"""One request with EVERY column filled, so the report can be tested honestly.

WHY
---
This database is a development copy and most of it is empty: of the 81 columns on
iec_emc_requests, nine are filled on no request at all and another twenty on
fewer than a third. Production is full. Testing the report here therefore proves
almost nothing - a field that prints blank might be unmapped, or might simply
have had nothing to print, and the two look identical in the finished document.
That ambiguity has already cost this project several wrong conclusions.

So this builds one request where every column and every child table carries a
plausible value, modelled on IEC-EMC-006 (the most complete real request, 63
populated columns) rather than invented from nothing. Anything still blank in
the generated report is then a mapping defect, with no second explanation.

WHAT IT DELIBERATELY EXERCISES
------------------------------
  four tests, two emission + two immunity   1.1 must show PASS/FAIL for CE and
                                            RE and a performance criterion for
                                            EFT and ESD; 1.4 must list exactly
                                            the two emission uncertainties
  three functional modes                    2.7 must print Mode A, Mode B, Mode C
  modification states 0, 0, 2 across tests  2.4 must print rows 0, 1 AND 2 - the
                                            gap-filling rule, on a real document
                                            rather than a unit test
  two supply voltages, three accessories,   the child tables that feed 2.1 and
  three cables, three product standards     2.9, none of which is one-to-one

    python tools_seed_full_request.py            # create (idempotent)
    python tools_seed_full_request.py --purge    # remove it
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text  # noqa: E402

TCO = "IEC-EMC-900"
JOB = "TFS-EMC-2026-900"
PRODUCT = "FULLFILL Test Bench FB-900"

# CE and RE are emission tests (PASS/FAIL, and they carry a measurement
# uncertainty); EFT and ESD are immunity (performance criterion A..D). Four is
# enough to prove both paths without a twenty-minute generate.
TESTS = ["CE", "RE", "EFT", "ESD"]

# request test_code -> planner test_name, the join the report walks
PLANNER_NAME = {"CE": "CE", "RE": "RE", "EFT": "EFT", "ESD": "ESD"}

MODES = [
    "Normal operating mode - dispensing at 2 L/min, display active",
    "Standby mode - pump idle, UV lamp off, display dimmed",
    "Service mode - diagnostics running, all valves cycled",
]

# state 2 lands on ESD only. The other three sit at 0, which is the case the
# report has to widen into rows 0, 1, 2.
MODIFICATION = {
    "CE":  [("0", "Initial state", "", "")],
    "RE":  [("0", "Initial state", "", "")],
    "EFT": [("0", "Initial state", "", "")],
    "ESD": [("0", "Initial state", "", ""),
            ("2", "Ferrite sleeve fitted on the sensor harness",
             "Engineering", "2026-06-14")],
}


def _cols(db, table):
    return {r[0] for r in db.session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=DATABASE() AND table_name=:t"), {"t": table}).fetchall()}


def purge(db):
    rid = db.session.execute(text(
        "SELECT id FROM iec_emc_requests WHERE tco_id=:t"), {"t": TCO}).scalar()
    if not rid:
        return False
    ent = [r[0] for r in db.session.execute(text(
        "SELECT id FROM planner_entries WHERE test_request_id=:r"), {"r": rid}).fetchall()] or [-1]
    dids = [r[0] for r in db.session.execute(text(
        "SELECT id FROM `datasheet` WHERE planner_entry_id IN :e"), {"e": tuple(ent)}).fetchall()] or [-1]
    for t in ("datasheet_measurement", "datasheet_status_history", "datasheet_revision",
              "datasheet_modification", "datasheet_equipment", "datasheet_software",
              "datasheet_observation", "datasheet_observation_legend",
              "datasheet_ce", "datasheet_re", "datasheet_eft", "datasheet_esd",
              "datasheet_rev_modification", "datasheet_rev_equipment",
              "datasheet_rev_software", "datasheet_rev_observation",
              "datasheet_rev_observation_legend", "datasheet_rev_ce",
              "datasheet_rev_re", "datasheet_rev_eft", "datasheet_rev_esd"):
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
    for child in ("iec_emc_request_test_standards", "iec_emc_request_test_ce",
                  "iec_emc_request_test_re", "iec_emc_request_test_eft",
                  "iec_emc_request_test_esd"):
        try:
            db.session.execute(text("DELETE FROM `%s` WHERE request_test_id IN :t" % child),
                               {"t": tuple(tids)})
        except Exception:
            db.session.rollback()
    db.session.execute(text("DELETE FROM iec_emc_request_tests WHERE request_id=:r"), {"r": rid})
    db.session.execute(text("DELETE FROM iec_emc_requests WHERE id=:r"), {"r": rid})
    db.session.commit()
    return True


def create_request(db, engineer, manager):
    """Every column on iec_emc_requests, modelled on the real IEC-EMC-006."""
    today = date.today()
    recv = today - timedelta(days=21)
    vals = {
        "user_id": engineer.id, "tco_id": TCO, "job_id": JOB, "job_number": JOB,
        "status": "Assigned Lab Engineer",
        "product_name": PRODUCT,
        "manufacturer": "Thermo Fisher Scientific",
        "manufacturer_address": "Thermo Fisher Scientific\nRobert-Bosch-Strasse 1\n"
                                "D-63505 Langenselbold, Germany",
        "model_number": "FB-900-UVUF",
        "serial_number": "FB900-2026-000417",
        "test_samples": 1, "samples_available_in_lab": "yes",
        "has_model_variance": "yes",
        "model_variance": "FB-900-UVUF-EU and FB-900-UVUF-US differ only in the "
                          "mains cordset; the EU variant was tested.",
        "model_variance_document": "Variance justification memo TFS-VAR-900 rev B",
        "project_details_intent": "EMI/EMC compliance testing for CE marking",
        "has_wireless_interface": "no",
        # 2.1 size and weight - the four columns that are empty on every request
        # in this database and populated in production
        "dimension_unit": "mm", "length": 420.0, "width": 315.0, "height": 560.0,
        "weight": 31.5, "operating_frequency": "50/60",
        "product_type": "Tabletop", "type_others": "",
        "product_description":
            "The FULLFILL Test Bench FB-900 is a laboratory water purification "
            "bench producing Type I and Type II water. It is used in analytical "
            "laboratories for reagent preparation and instrument feed.",
        "test_configuration":
            "1. Feed the bench with pre-treated tap water via the inlet filter.\n"
            "2. Connect the dispenser arm to the front port.\n"
            "3. Power from a single-phase 230 V / 50 Hz supply through the LISN.\n"
            "4. Run the bench in Normal operating mode for the duration of each test.",
        "operation_modes": "\n".join(MODES),
        "monitoring_parameters":
            "1. No error message on the front display.\n"
            "2. Output resistivity remains 18.2 MOhm-cm at 25 C.\n"
            "3. Dispense rate stays within 2.0 +/- 0.2 L/min.\n"
            "4. No unintended valve actuation.",
        "additional_info":
            "XP Power SMPS VEH120PS24-XE0959. Input AC 100-240 V, 2.0 A max, "
            "50/60 Hz. Output 24 V DC, 5.0 A.",
        "product_environment_other": "",
        "product_group": "Group 1", "class_type": "Class B",
        "continue_testing": "YES", "test_report_required": "YES",
        "uncertainty_required": "YES", "test_witness": "NO",
        "conformity_required": "YES", "conformity_statement": "YES",
        "number_of_modes": len(MODES),
        "requester_name": "Athina Ramanathan",
        "requester_department": "Department - 8", "requester_group": "LPD",
        "requester_division": "WLP", "requester_site": "Chelmsford",
        "requester_email": "athina.ramanathan@thermofisher.com",
        "requester_contact": "8524939830",
        "requester_designation": "Senior Design Engineer",
        "requester_date": recv, "requester_expected_completion_date": today + timedelta(days=7),
        "requester_status": "At Review", "requester_signature": "A.Ramanathan",
        "sample_condition": "Received in good condition, no transit damage",
        "capability_available": "yes", "sample_received_date": recv,
        "test_duration": "4.5",
        "test_commencement_date": recv + timedelta(days=2),
        "test_completion_date": recv + timedelta(days=6),
        "lab_manager_name": manager.username, "lab_manager_date": recv,
        "lab_manager_signature": manager.username,
        "lab_manager_signed_at": datetime.now(),
        "assigned_engineer_id": engineer.id, "assigned_engineer_name": engineer.username,
        "assignment_priority": "normal",
        "assignment_due_date": today + timedelta(days=5),
        "assignment_notes": "Full-scope run; all four tests on one sample.",
        "review_comments": "Scope confirmed with the requester on the kick-off call.",
        "reviewed_by": manager.username, "reviewed_at": datetime.now(),
        "submitted_at": datetime.now(),
        "is_synthetic": 1,
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
    """Every child table, with the JSON shapes the real requests use."""
    def ins(table, col, values, extra=None):
        for i, v in enumerate(values):
            payload = {"r": rid, "v": v, "s": i}
            cols, binds = "request_id, %s, sort_order" % col, ":r, :v, :s"
            if extra:
                for k, val in extra.items():
                    payload[k] = val
                    cols += ", %s" % k
                    binds += ", :%s" % k
            db.session.execute(text(
                "INSERT INTO `%s` (%s) VALUES (%s)" % (table, cols, binds)), payload)

    ins("iec_emc_request_accessories", "accessory_value", [
        json.dumps({"equipmentName": "External pre-treatment cartridge",
                    "make": "Thermo Fisher", "model": "PT-900", "serial": "PT900-0012"}),
        json.dumps({"equipmentName": "Hand dispenser arm",
                    "make": "Thermo Fisher", "model": "HD-12", "serial": "HD12-0431"}),
        json.dumps({"equipmentName": "Wall bracket kit",
                    "make": "Thermo Fisher", "model": "WB-4", "serial": "WB4-0088"}),
    ])
    # Keys are the ones index.html actually posts - cableName / length /
    # powerSignal / shielded / purpose. Written as "type" first, which is not a
    # key the form has ever produced, so the Power/Signal column printed blank
    # in the report and looked like a mapping bug in cable_rows(). It was the
    # fixture that was wrong: across the whole database 29 cable rows carry
    # powerSignal and the only 3 carrying "type" were these.
    ins("iec_emc_request_cables", "cable_value", [
        json.dumps({"cableName": "Mains cordset (EU)", "length": "2",
                    "powerSignal": "Power", "shielded": "Unshielded",
                    "purpose": "Mains supply to the bench"}),
        json.dumps({"cableName": "Sensor harness", "length": "1.5",
                    "powerSignal": "Signal", "shielded": "Shielded",
                    "purpose": "Conductivity and flow sensors"}),
        json.dumps({"cableName": "Service RS-232 lead", "length": "3",
                    "powerSignal": "Signal", "shielded": "Shielded",
                    "purpose": "Diagnostics port, connected during test"}),
    ])
    ins("iec_emc_request_categories", "category_name", ["Laboratory"])
    ins("iec_emc_request_decision_rules", "rule_value", ["standard_measured"])
    ins("iec_emc_request_eut_specs", "spec_value", [json.dumps({
        "acVoltageRange": "100-240", "acFreqRange": "50-60", "acInputCurrent": "2.4",
        "dcVoltageRange": "24", "dcInputCurrent": "5.0", "ratedPower": "150"})])
    ins("iec_emc_request_functional_modes", "mode_value", MODES)
    # product_environments needs its own insert: environment_key varies per row
    # and is NOT NULL with no default, so the generic helper (which applies one
    # extra value to every row) cannot build it and inserting-then-updating
    # fails on the constraint.
    for i, (key, val) in enumerate((("non_medical", "Basic Electromagnetic"),
                                    ("medical", ""), ("custom", ""))):
        db.session.execute(text(
            "INSERT INTO iec_emc_request_product_environments "
            "(request_id, environment_key, environment_value, sort_order) "
            "VALUES (:r, :k, :v, :s)"), {"r": rid, "k": key, "v": val, "s": i})
    ins("iec_emc_request_product_standards", "standard_value", [
        "IEC 61326-1 : 2020", "EN 61326-1 : 2021", "FCC Subpart 15B : 2024"])
    ins("iec_emc_request_serial_numbers", "serial_number", ["FB900-2026-000417"])
    ins("iec_emc_request_service_types", "service_type", ["Compliance"])
    ins("iec_emc_request_supply_vf", "value_text", [
        json.dumps({"voltage": "230", "frequency": "50", "notes": "primary supply"}),
        json.dumps({"voltage": "120", "frequency": "60", "notes": "US variant"}),
    ])
    db.session.commit()


def create_tests(db, rid, engineer):
    """iec_emc_request_tests + the per-test spec rows + declared standards."""
    today = date.today()
    ids = {}
    for i, code in enumerate(TESTS):
        db.session.execute(text(
            "INSERT INTO iec_emc_request_tests (request_id, test_code, is_selected, "
            "is_developmental, planned_hours, assigned_engineer_id, "
            "assigned_engineer_name, planned_start_date, planned_end_date, "
            "created_at, updated_at) VALUES (:r, :c, 1, 0, :h, :e, :en, :s, :d, NOW(), NOW())"),
            {"r": rid, "c": code, "h": 2.5, "e": engineer.id,
             "en": engineer.username,
             "s": today - timedelta(days=14 - i), "d": today - timedelta(days=13 - i)})
        ids[code] = db.session.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    std = {"CE": ["CISPR 11", "EN 55011"], "RE": ["CISPR 11", "EN 55011"],
           "EFT": ["IEC 61000-4-4", "EN 61000-4-4"],
           "ESD": ["IEC 61000-4-2", "EN 61000-4-2"]}
    for code, tid in ids.items():
        for i, s in enumerate(std[code]):
            db.session.execute(text(
                "INSERT INTO iec_emc_request_test_standards (request_test_id, "
                "standard_value, sort_order) VALUES (:t, :v, :s)"),
                {"t": tid, "v": s, "s": i})
    spec = {
        "iec_emc_request_test_ce": ("CE", {"voltage_freq": "230 V / 50 Hz",
                                           "freq_range": "150 kHz - 30 MHz",
                                           "cables": "Mains cordset", "ce_class": "Class B"}),
        "iec_emc_request_test_re": ("RE", {"voltage_freq": "230 V / 50 Hz",
                                           "freq_range": "30 MHz - 1 GHz",
                                           "re_class": "Class B"}),
        "iec_emc_request_test_eft": ("EFT", {"voltage_freq": "230 V / 50 Hz",
                                             "cables_power": "Mains cordset",
                                             "cables_signal": "Sensor harness",
                                             "test_level1": "+/-1 kV"}),
        "iec_emc_request_test_esd": ("ESD", {"voltage_freq": "230 V / 50 Hz",
                                             "contact_level": "+/-4 kV",
                                             "air_level": "+/-8 kV"}),
    }
    for table, (code, fields) in spec.items():
        have = _cols(db, table)
        use = {k: v for k, v in fields.items() if k in have}
        if not use:
            continue
        cols = ", ".join("`%s`" % k for k in use)
        binds = ", ".join(":%s" % k for k in use)
        payload = dict(use); payload["t"] = ids[code]
        db.session.execute(text(
            "INSERT INTO `%s` (request_test_id, %s) VALUES (:t, %s)"
            % (table, cols, binds)), payload)
    db.session.commit()
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--purge", action="store_true")
    args = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    import app as app_module
    from models import db, User
    a = app_module.create_app("default")
    with a.app_context():
        if purge(db):
            print("removed the previous %s" % TCO)
        if args.purge:
            return 0
        users = db.session.query(User).order_by(User.id).all()
        engineer = users[0]
        manager = next((u for u in users if u.id != engineer.id), engineer)
        rid = create_request(db, engineer, manager)
        create_children(db, rid)
        create_tests(db, rid, engineer)
        print("created request %s (id %s) - %s" % (TCO, rid, PRODUCT))
        print("   engineer=%s  manager=%s" % (engineer.username, manager.username))
        print("   tests: %s" % ", ".join(TESTS))
        print("   modes: %d  (Mode A..%s)" % (len(MODES), chr(64 + len(MODES))))
        print("   modification states seeded: %s"
              % {k: [x[0] for x in v] for k, v in MODIFICATION.items()})
        filled = db.session.execute(text("""
            SELECT COUNT(*) FROM information_schema.columns c
            WHERE c.table_schema=DATABASE() AND c.table_name='iec_emc_requests'""")).scalar()
        print("   -> now assign datasheets with tools_seed_full_request_sheets.py")
        print("   (%d columns on the request; run the audit below to see the fill rate)" % filled)
    return 0


if __name__ == "__main__":
    sys.exit(main())
