"""Seed sample multi-test EMC requests + per-test planner assignments.

This is a NEW, self-contained script for the datasheet-generation feature. It does
NOT touch the existing seed.py. It creates a few realistic multi-test requests so
the datasheet forms (which are keyed by a planner_entries assignment id) can be
opened and exercised end-to-end for every test code.

Usage (from the project root, using the venv interpreter):
    .\.venv\Scripts\python.exe datasheet_gen/seed_datasheet_demo.py
    .\.venv\Scripts\python.exe datasheet_gen/seed_datasheet_demo.py --reset   # rebuild demo data

Idempotent: matches on the demo TCO ids. --reset deletes the demo requests
(and their planner rows) first, then recreates them.

Depends on the seeded users from seed.py (admin, engineer1, engineer2,
requester1, requester2). Run seed.py first if you haven't.
"""
import sys
from datetime import date, timedelta

from app import create_app
from models import (
    db, User, EMCRequest, PlannerEntry,
    EMCRequestSerialNumber, EMCRequestAdditionalModel, EMCRequestProductStandard,
    EMCRequestSupplyVF, EMCRequestServiceType,
    EMCRequestTest, EMCRequestTestStandard,
    EMCRequestTestCE, EMCRequestTestRE, EMCRequestTestESD, EMCRequestTestHarmonic,
)

# code -> engineer email. Splits the 11 tests across two engineers to demonstrate
# that the per-test assignments of a single request can go to different people.
DEMO_REQUESTS = [
    {
        "tco_id": "TCO-2026-001", "job_id": "JOB-2026-001", "requester": "requester1@local.test",
        "product_name": "PortaScan X100 Spectrum Analyzer", "manufacturer": "Thermo Fisher Scientific",
        "manufacturer_address": "168 Third Avenue, Waltham, MA, USA",
        "model_number": "PS-X100", "additional_models": ["PS-X100A", "PS-X100B"],
        "serials": ["SN-X100-1001", "SN-X100-1002"],
        "standards": ["EN 61326-1:2021", "CISPR 11:2015"],
        "supply": {"voltage": "230", "frequency": "50"},
        "tests": [("CE", "engineer1@local.test"), ("ESD", "engineer1@local.test"),
                  ("RE", "engineer2@local.test"), ("EFT", "engineer2@local.test")],
    },
    {
        "tco_id": "TCO-2026-002", "job_id": "JOB-2026-002", "requester": "requester2@local.test",
        "product_name": "GeneCycler 480 Thermal Cycler", "manufacturer": "Applied BioSystems",
        "manufacturer_address": "Building 7, Bengaluru, KA, India",
        "model_number": "GC-480", "additional_models": ["GC-480X"],
        "serials": ["SN-GC480-2001"],
        "standards": ["IEC 61326-1", "EN 55011"],
        "supply": {"voltage": "115", "frequency": "60"},
        "tests": [("SURGE", "engineer1@local.test"), ("CRF", "engineer2@local.test"),
                  ("HARMONIC", "engineer1@local.test"), ("RS_RI", "engineer2@local.test")],
    },
    {
        "tco_id": "TCO-2026-003", "job_id": "JOB-2026-003", "requester": "requester1@local.test",
        "product_name": "AquaPure 30 Lab Water System", "manufacturer": "Thermo Fisher Scientific",
        "manufacturer_address": "168 Third Avenue, Waltham, MA, USA",
        "model_number": "AP-30", "additional_models": [],
        "serials": ["SN-AP30-3001", "SN-AP30-3002", "SN-AP30-3003"],
        "standards": ["IEC 61010-1", "EN 61000-3-2"],
        "supply": {"voltage": "230", "frequency": "50"},
        "tests": [("VOLTAGEDIPS", "engineer1@local.test"), ("VOLTAGEFLICKER", "engineer2@local.test"),
                  ("PFMF", "engineer1@local.test")],
    },
]

DEMO_TCOS = [r["tco_id"] for r in DEMO_REQUESTS]


def _user(email):
    return User.query.filter_by(email=email).first()


def _attach_test_detail(test, code):
    """Add a per-test detail row (class / range / level) for richer prefill where
    the model supports it. Only the codes with obvious scalar fields are filled."""
    if code == "CE":
        test.ce_detail = EMCRequestTestCE(ce_class="Class A", freq_range="150 kHz - 30 MHz",
                                          voltage_freq="230 V / 50 Hz", cables="Power")
    elif code == "RE":
        test.re_detail = EMCRequestTestRE(re_class="Class A", freq_range="30 MHz - 1 GHz",
                                          voltage_freq="230 V / 50 Hz")
    elif code == "ESD":
        test.esd_detail = EMCRequestTestESD(contact_level="± 4 kV", air_level="± 8 kV",
                                            voltage_freq="230 V / 50 Hz")
    elif code == "HARMONIC":
        test.harmonic_detail = EMCRequestTestHarmonic(harmonic_class="Class A",
                                                      voltage_freq="230 V / 50 Hz")


def _build_request(spec, admin):
    requester = _user(spec["requester"])
    today = date.today()
    req = EMCRequest(
        user_id=requester.id if requester else None,
        tco_id=spec["tco_id"], job_id=spec["job_id"], job_number=spec["job_id"],
        status="Assigned",
        product_name=spec["product_name"], manufacturer=spec["manufacturer"],
        manufacturer_address=spec["manufacturer_address"], model_number=spec["model_number"],
        serial_number=spec["serials"][0] if spec["serials"] else None,
        test_samples=len(spec["serials"]) or 1, samples_available_in_lab="Yes",
        # requester block (all NOT NULL)
        requester_name=(requester.username if requester else "Requester"),
        requester_department="EMC Compliance", requester_group="Product Safety",
        requester_division="Analytical Instruments", requester_site="Bengaluru",
        requester_email=spec["requester"], requester_contact="+91-80-1234-5678",
        requester_designation="Design Engineer", requester_date=today,
        requester_expected_completion_date=today + timedelta(days=21),
        assigned_engineer_id=admin.id if admin else None,
        assigned_engineer_name=(admin.username if admin else None),
    )
    # ordered multi-valued collections (used by prefill)
    req.service_types = [EMCRequestServiceType(service_type="EMC", sort_order=0)]
    req.serial_numbers = [EMCRequestSerialNumber(serial_number=s, sort_order=i)
                          for i, s in enumerate(spec["serials"])]
    req.additional_models = [EMCRequestAdditionalModel(model_number=m, sort_order=i)
                             for i, m in enumerate(spec["additional_models"])]
    req.product_standards = [EMCRequestProductStandard(standard_value=s, sort_order=i)
                             for i, s in enumerate(spec["standards"])]
    import json
    req.supply_vf_values = [EMCRequestSupplyVF(value_text=json.dumps(spec["supply"]), sort_order=0)]

    # one EMCRequestTest per selected test, assigned to its engineer
    for code, eng_email in spec["tests"]:
        eng = _user(eng_email)
        t = EMCRequestTest(
            test_code=code, is_selected=True, is_developmental=False,
            planned_hours=8.0, workflow_status="assigned",
            assigned_engineer_id=eng.id if eng else None,
            assigned_engineer_name=(eng.username if eng else None),
        )
        t.standards = [EMCRequestTestStandard(standard_value=spec["standards"][0], sort_order=0)] \
            if spec["standards"] else []
        _attach_test_detail(t, code)
        req.tests.append(t)

    db.session.add(req)
    db.session.flush()  # assign req.id before creating planner rows
    return req


def _build_planner_entries(req, spec, admin):
    today = date.today()
    rows = []
    for i, (code, eng_email) in enumerate(spec["tests"]):
        eng = _user(eng_email)
        start = today + timedelta(days=i)
        pe = PlannerEntry(
            test_request_id=req.id,
            test_person_name=(eng.username if eng else "engineer"),
            engineer_user_id=eng.id if eng else None,
            created_by_user_id=admin.id if admin else None,
            test_name=code, tco_id=req.tco_id,
            start_date=start, end_date=start,
            total_hours=8.0, event_type="test", status="in_progress",
            event_description=f"{code} test for {req.product_name}",
        )
        db.session.add(pe)
        rows.append((code, eng_email))
    return rows


def reset():
    n_pe = 0
    n_req = 0
    for tco in DEMO_TCOS:
        n_pe += PlannerEntry.query.filter_by(tco_id=tco).delete(synchronize_session=False)
        req = EMCRequest.query.filter_by(tco_id=tco).first()
        if req:
            db.session.delete(req)  # cascades to tests/details/multivalue collections
            n_req += 1
    db.session.commit()
    print(f"Reset: removed {n_req} demo request(s) and {n_pe} planner row(s).")


def main():
    do_reset = "--reset" in sys.argv
    app = create_app()
    with app.app_context():
        admin = _user("admin@local.test")
        if do_reset:
            reset()

        created = []
        for spec in DEMO_REQUESTS:
            if EMCRequest.query.filter_by(tco_id=spec["tco_id"]).first():
                print(f"  {spec['tco_id']}: already present, skipping.")
                continue
            req = _build_request(spec, admin)
            _build_planner_entries(req, spec, admin)
            created.append(spec["tco_id"])
        db.session.commit()

        # Report the assignment distribution (assignment_id == planner_entries.id)
        print("\n=== Datasheet demo data ===")
        print(f"{'assign_id':>9}  {'code':14s} {'TCO':14s} {'engineer':12s} form URL")
        print("-" * 92)
        for pe in (PlannerEntry.query
                   .filter(PlannerEntry.tco_id.in_(DEMO_TCOS))
                   .order_by(PlannerEntry.tco_id, PlannerEntry.id).all()):
            code = (pe.test_name or "").upper()
            url = (f"/datasheet/ce/{pe.id}/form" if code == "CE"
                   else f"/datasheet/g/{code}/{pe.id}/form")
            print(f"{pe.id:>9}  {code:14s} {pe.tco_id:14s} {pe.test_person_name:12s} {url}")
        print("\nLog in as engineer1/engineer2 (Password@123) and open the URLs above,")
        print("or visit the Assigned Tests page to reach each 'Generate Datasheet' button.")
        if created:
            print(f"\nCreated requests: {', '.join(created)}")
        else:
            print("\nNo new requests created (all demo TCOs already existed). Use --reset to rebuild.")


if __name__ == "__main__":
    main()
