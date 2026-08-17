#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Seed two more DEMO requests, three tests each, filled through the real flow.

    python tools_seed_demo_requests.py            # build 302 and 303
    python tools_seed_demo_requests.py --clean    # remove them

WHY SCHEMA-DRIVEN AND NOT HAND-WRITTEN
--------------------------------------
A form_json of subtly the wrong shape does not error. It projects to nothing,
and the empty per-test table then looks like a mapping bug - which has already
happened twice in this project, and happened to me once this session when I
INSERTed a record instead of calling records.upsert_record.

So nothing here is guessed. Every scalar and every table is read out of the
test's own schema JSON, which declares its keys, its `options`, its `checkbox`
values and its defaults. A cell with options gets one of ITS options; a table
gets rows on the `key__cN[]` names form_extract actually reads. That makes the
shape correct by construction for any test the lab adds later, instead of
correct for the three I happened to look at.

Five test types post observation grids the schema does NOT declare - the form
builds them at runtime - so those keys are the only hand-written part, taken
from the functions that read them:

    ESD          ind_r<i>_c<n> / dir_r<i>_name  (form_extract._esd_rows)
    EFT          eft_obs_<kind>_cols|_row_<i>|_<i>__c<n>, COMMA-joined cols
    SURGE        surge_obs_<kind>_...           identical but PIPE-joined
    VOLTAGEDIPS  dips_/intr_ rows
    HARMONIC     avgmax_row / harmonic_row      (_EXTRA_GRIDS)

WHAT IT BUILDS
--------------
    DEMO-EMC-302  RS_RI, PFMF, CRF        cells declared in-schema
    DEMO-EMC-303  EFT, SURGE, VOLTAGEFLICKER

Each request gets its own engineer and its own reviewer, and the nine datasheets
across 301-303 deliberately end in different lifecycle states with different
reason codes, because a single state answers no interesting join.

SYNTHETIC AND SAYS SO: is_synthetic=1, product names start with DEMO, TCOs in
the DEMO-EMC-3xx block.
"""
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text  # noqa: E402

# (tco, job, product, model, serial, engineer_offset, [(code, planner_name, lifecycle, fail_code)])
# The two axes are kept DELIBERATELY apart across these six, because collapsing
# them is the mistake the whole reason taxonomy exists to prevent:
#
#   RS_RI   the UNIT failed, and the record was approved first time. An accurate
#           record of a failing unit is an accurate record.
#   PFMF    the unit passed; the RECORD was sent back for an incomplete grid,
#           then fixed and approved. No failure code - nothing failed the standard.
#   SURGE   both at once: the unit was damaged AND the record was sent back.
#
# A failure code is only ever paired with a test that HAS one. There is no PFMF
# code in the taxonomy, and an earlier pass put RS_MALFUNCTION on PFMF - which
# reads as "the power-frequency test failed for a radiated-RF reason" and would
# make any cross-check of the pairing nonsense.
REQUESTS = [
    ("DEMO-EMC-302", "DEMO-JOB-302", "DEMO Spectra Bench Photometer",
     "DEMO-50199002", "DEMOSN0000302", 1, [
         ("RS_RI", "RS_RI", "approve", "RS_MALFUNCTION"),
         ("PFMF", "PFMF", "reject_then_approve", None),
         ("CRF", "CRF", "draft_only", None),
     ]),
    ("DEMO-EMC-303", "DEMO-JOB-303", "DEMO Orion Vacuum Pump Controller",
     "DEMO-50199003", "DEMOSN0000303", 2, [
         ("EFT", "EFT", "approve", None),
         ("SURGE", "Surge", "reject_only", "SURGE_DAMAGE"),
         ("VOLTAGEFLICKER", "VoltageFlicker", "approve", None),
     ]),
]

# The request side spells four tests DIFFERENTLY from the datasheet side, and a
# naive join on test_code silently drops them. Written first with the datasheet
# spelling on both sides, which made the triple join succeed for the wrong
# reason: it matched because the codes happened to be identical, so the corpus
# proved nothing about the bridge it was built to exercise. These are the real
# request-side codes, from iec_emc_request_tests.test_code.
REQUEST_CODE = {
    "RS_RI": "RS",
    "PFMF": "POWER_FREQ",
    "VOLTAGEFLICKER": "FLICKER",
    "VOLTAGEDIPS": "VOLTAGE_DIPS",
}

REJECT_REASONS = {
    "PFMF": ("INCOMPLETE_OBS", "Observation grid is missing the 60 Hz orientations."),
    "SURGE": ("MISSING_PHOTO", "Setup photographs for the DC port are not attached."),
}

# Plausible values by key fragment. Only reached for a field the schema gives no
# options, checkbox or default for - the declared ones always win, so this can
# never contradict the form.
_HINTS = (
    ("frequency_range", "80 MHz to 1 GHz"), ("freq_range", "80 MHz to 1 GHz"),
    ("frequency", "50"), ("bandwidth", "120 kHz"), ("step_size", "1 %"),
    ("dwell", "3 s"), ("distance", "3 m"), ("modulation", "80 % AM (1 kHz)"),
    ("level", "10 V/m"), ("voltage", "230 V"), ("current", "2.0 A"),
    ("power", "120 W"), ("temperature", "23.2"), ("humidity", "46"),
    ("pressure", "1009 hPa"), ("uncertainty", "+/- 3.400 dB"),
    ("sop_reference", "IEC-SOP-520"), ("procedure", "Applied per the basic standard."),
    ("deviation", "NA"), ("monitoring", "Display and alarm state monitored."),
    ("criteria", "B"), ("result", "PASS"), ("class", "Class B"),
    ("group", "Group 1"), ("mode", "Mode A"), ("port", "Enclosure"),
    ("configuration", "Tabletop"), ("time", "1 s"), ("duration", "60 s"),
    ("date", None),          # filled with today by the caller
    ("name", "DEMO Engineer"), ("by", "DEMO Engineer"),
)


import re  # noqa: E402

# A key shaped like a grid cell: f_80_to_1000_col_3, pf_50_col_7, meas__c2.
# These are MEASUREMENTS, and the difference matters more than it looks:
# projection._num() writes datasheet_measurement.value_num only when the cell
# parses as a number, so a text fallback in a measurement cell leaves value_num
# NULL - and value_num is what every "closest to the limit", "worst margin" and
# "did it improve" query sorts on. A first pass filled these with "NA" and left
# RS_RI with 2 numeric cells out of 22.
_GRID_CELL_RE = re.compile(r"(?:_col_\d+|__c\d+|_r\d+_c\d+)$")


def _hint(key, label, today, index=0):
    hay = ("%s %s" % (key, label or "")).lower()
    for frag, value in _HINTS:
        if frag in hay:
            return today if value is None else value
    if _GRID_CELL_RE.search(key):
        # Varied rather than constant, so GROUP BY and ORDER BY on value_num
        # have something to distinguish - a column of identical numbers tests
        # nothing about sorting or margins.
        return "%.2f" % (3.0 + (index % 7) * 1.35)
    return "NA"


def _scalars(schema):
    """Every scalar the form posts: (key, label, options, default).

    A nested descriptor with a key and no type IS a field - that is how the
    schemas express them, and skipping the untyped ones would have left most of
    the form blank.
    """
    out = []

    def walk(items):
        for it in items:
            kind = it.get("type")
            if kind == "fields":
                walk(it.get("fields") or [])
            elif kind in (None, "field", "textarea") and it.get("key"):
                out.append((it["key"], it.get("label"),
                            it.get("options") or it.get("checkbox") or [],
                            it.get("default")))
    for sec in schema.get("sections", []):
        walk(sec.get("items", []))
    return out


def _tables(schema):
    """(key, [column keys]) for every declared repeating table."""
    out = []
    for sec in schema.get("sections", []):
        for it in sec.get("items", []):
            if it.get("type") == "table" and it.get("key"):
                out.append((it["key"], [c.get("key") or "c%d" % i
                                        for i, c in enumerate(it.get("columns") or [])]))
    return out


# --------------------------------------------------------------------------
# the grids the schema does not declare
# --------------------------------------------------------------------------
def _matrix(prefix, kinds, cols, rows, joiner):
    """EFT / SURGE runtime matrices. joiner is ',' for EFT and '|' for SURGE -
    they are read by different splitters and getting it wrong yields one column."""
    form = {}
    for kind in kinds:
        form["%s_%s_cols" % (prefix, kind)] = joiner.join(cols)
        for ri, label in enumerate(rows):
            form["%s_%s_row_%d" % (prefix, kind, ri)] = label
            for ci in range(len(cols)):
                form["%s_%s_%d__c%d" % (prefix, kind, ri, ci)] = "A" if ci % 3 else "B"
    return form


def _obs_extra(code, today):
    if code == "EFT":
        return _matrix("eft_obs", ("power", "signal"),
                       ["+/-0.5 kV", "+/-1 kV", "+/-2 kV"],
                       ["Positive polarity", "Negative polarity"], ",")
    if code == "SURGE":
        return _matrix("surge_obs", ("ac", "dc", "signal"),
                       ["CM L-PE 0deg", "CM L-PE 90deg", "DM L-N 0deg"],
                       ["+/-0.5 kV", "+/-1 kV"], "|")
    if code == "ESD":
        form = {}
        for r in range(1, 9):
            for c in range(1, 7):
                form["ind_r%d_c%d" % (r, c)] = "A"
        for pfx, pts in (("dir", ["Enclosure seam", "Front panel", "Connector shell"]),
                         ("air", ["Display bezel", "Vent slot", "USB port"])):
            for i, pt in enumerate(pts, 1):
                form["%s_r%d_name" % (pfx, i)] = pt
                for c in range(1, 7):
                    form["%s_r%d_c%d" % (pfx, i, c)] = "A" if c < 6 else "B"
        return form
    if code == "HARMONIC":
        form = {}
        for i in range(1, 4):
            for c in range(4):
                form.setdefault("harmonic_row__c%d[]" % c, []).append(str(i * 10 + c))
        return form
    return {}


def build_form(code, schema, ident, today):
    """One complete form_json for this test, correct by construction."""
    form = dict(ident)
    for i, (key, label, options, default) in enumerate(_scalars(schema)):
        if options:
            form[key] = options[i % len(options)]
        elif default:
            form[key] = default
        else:
            form[key] = _hint(key, label, today, i)
    for key, cols in _tables(schema):
        for i in range(len(cols) or 1):
            form["%s__c%d[]" % (key, i)] = ["%s-%d" % (cols[i] if i < len(cols) else "c%d" % i, r)
                                            for r in (1, 2)]
    # CRF splits its observation rows on the port column, so one row must say
    # "signal" or the signal-side grid is empty and looks unrecorded.
    if code == "CRF":
        form["test_observation_rows__c1[]"] = ["Power port", "Signal port"]
    # the three shared child tables, on the names _CHILD_SPECS reads
    form.update({
        "test_equipment_used_rows__c0[]": ["Signal Generator", "Field Probe"],
        "test_equipment_used_rows__c1[]": ["R&S", "Narda"],
        "test_equipment_used_rows__c2[]": ["SMB100A", "NBM-550"],
        "test_equipment_used_rows__c3[]": ["DEMO-SG-01", "DEMO-FP-02"],
        "test_equipment_used_rows__c4[]": ["2027-02-28", "2027-04-30"],
        "software_used_rows__c0[]": ["EMC32 Measurement Suite"],
        "software_used_rows__c1[]": ["10.60.1"],
        "eut_modification_rec_rows__c0[]": ["0", "1"],
        "eut_modification_rec_rows__c1[]": ["Initial state",
                                            "Common-mode choke fitted on the sensor harness"],
        "eut_modification_rec_rows__c2[]": ["", "Engineering"],
        "eut_modification_rec_rows__c3[]": ["", today],
    })
    form.update(_obs_extra(code, today))

    # THE OUTCOME IS ALWAYS SET EXPLICITLY, both ways round.
    #
    # It cannot be left to the generic loop above, which rotates through a
    # field's declared options so that different tests get different values. On
    # an outcome field that rotation is not variety, it is a contradiction: it
    # produced result=FAIL on VOLTAGEFLICKER with no failure code, and criterion
    # D on PFMF whose unit passed. The earlier pass had the opposite defect -
    # result=PASS beside CE_LIMIT_EXCEEDED.
    #
    # A datasheet's outcome and its failure code are the same fact stated twice.
    # Demo data that states it two different ways teaches the model that the two
    # columns are unrelated, which is exactly the inference this corpus exists to
    # prevent.
    failed = bool(ident.get("failure_reason_code"))
    if "overall_result" in form:                      # emission: PASS / FAIL
        form["overall_result"] = "FAIL" if failed else "PASS"
    if "met_performance_criteria" in form:            # immunity: the criterion met
        form["required_performance_criteria"] = "B"
        form["met_performance_criteria"] = "D" if failed else "B"
    return form


# --------------------------------------------------------------------------
def clean(db, tco):
    rid = db.session.execute(text(
        "SELECT id FROM iec_emc_requests WHERE tco_id=:t"), {"t": tco}).scalar()
    entries = [r[0] for r in db.session.execute(text(
        "SELECT id FROM planner_entries WHERE tco_id=:t"), {"t": tco}).fetchall()]
    dids = [r[0] for r in db.session.execute(text(
        "SELECT id FROM `datasheet` WHERE tco_id=:t"), {"t": tco}).fetchall()]
    from datasheet_gen.projection_schema import mirrored_tables
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


def build_request(db, spec, engineer):
    tco, job, product, model, serial, _off, plan = spec
    db.session.execute(text(
        "INSERT INTO iec_emc_requests (user_id, tco_id, job_number, status, product_name, "
        "manufacturer, manufacturer_address, model_number, serial_number, test_samples, "
        "samples_available_in_lab, requester_name, requester_department, requester_group, "
        "requester_division, requester_site, requester_email, requester_contact, "
        "requester_designation, requester_date, assigned_engineer_id, assigned_engineer_name, "
        "submitted_at, created_at, updated_at, is_synthetic) "
        "VALUES (:u, :t, :j, 'Test Plan Approved', :p, 'DEMO Instruments', "
        "'1 Demo Way, Testville', :m, :s, 1, 'yes', 'DEMO Requester', 'DEMO Dept', "
        "'DEMO Group', 'DEMO Division', 'DEMO Site', 'demo.requester@example.invalid', "
        "'0000000000', 'Engineer', CURDATE(), :ei, :en, NOW(), NOW(), NOW(), 1)"),
        {"u": engineer.id, "t": tco, "j": job, "p": product, "m": model, "s": serial,
         "ei": engineer.id, "en": engineer.username})
    db.session.commit()
    rid = db.session.execute(text(
        "SELECT id FROM iec_emc_requests WHERE tco_id=:t"), {"t": tco}).scalar()
    start = date.today() - timedelta(days=10)
    entries = {}
    for i, (code, pname, _life, _fc) in enumerate(plan):
        db.session.execute(text(
            "INSERT INTO iec_emc_request_tests (request_id, test_code, is_selected, "
            "is_developmental, planned_hours, workflow_status, assigned_engineer_id, "
            "assigned_engineer_name, planned_start_date, planned_end_date, created_at, "
            "updated_at) VALUES (:r, :c, 1, 0, 8, 'Assigned Lab Engineer', :ei, :en, "
            ":sd, :ed, NOW(), NOW())"),
            {"r": rid, "c": REQUEST_CODE.get(code, code),
             "ei": engineer.id, "en": engineer.username,
             "sd": start + timedelta(days=i), "ed": start + timedelta(days=i + 1)})
        db.session.execute(text(
            "INSERT INTO planner_entries (test_person_name, engineer_user_id, "
            "created_by_user_id, test_name, tco_id, test_request_id, start_date, end_date, "
            "total_hours, event_type, status, created_at, updated_at) "
            "VALUES (:tp, :e, :e, :tn, :t, :r, :sd, :ed, 8, 'test', 'in_progress', NOW(), NOW())"),
            {"tp": engineer.username, "e": engineer.id, "tn": pname, "t": tco, "r": rid,
             "sd": start + timedelta(days=i), "ed": start + timedelta(days=i + 1)})
        entries[code] = db.session.execute(text(
            "SELECT id FROM planner_entries WHERE tco_id=:t AND test_name=:n "
            "ORDER BY id DESC LIMIT 1"), {"t": tco, "n": pname}).scalar()
    db.session.commit()
    return rid, entries


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
    from datasheet_gen.registry import load_schema

    a = app_module.create_app("default")
    with a.app_context():
        if "--clean" in sys.argv:
            for spec in REQUESTS:
                print("removed %s -> %s" % (spec[0], clean(db, spec[0])))
            return 0

        engineers = db.session.query(User).filter_by(role="lab_engineer", is_active=True) \
                              .order_by(User.id).all()
        reviewers = db.session.query(User).filter_by(role="admin", is_active=True) \
                              .order_by(User.id).all()
        if len(engineers) < 3 or len(reviewers) < 2:
            print("need 3 active lab engineers and 2 admins")
            return 1

        today = date.today().strftime("%d/%m/%Y")
        for spec in REQUESTS:
            tco, job, product, model, serial, off, plan = spec
            clean(db, tco)
            engineer = engineers[off % len(engineers)]
            reviewer = reviewers[off % len(reviewers)]
            rid, entries = build_request(db, spec, engineer)
            print("\n=== %s  id=%s  %s  engineer=%s  reviewer=%s"
                  % (tco, rid, product, engineer.username, reviewer.username))

            for code, pname, life, fail_code in plan:
                schema = load_schema(code)
                ident = {"tco_id": tco, "job_number": job, "eut_name": product,
                         "eut_model": model, "eut_model_sku_number": model,
                         "eut_serial": serial, "eut_serial_number": serial,
                         "test_date": today, "tested_by": engineer.username,
                         "tested_by_name": engineer.username}
                if fail_code:
                    ident["failure_reason_code"] = fail_code
                form = build_form(code, schema, ident, today)
                entry_id = entries[code]
                assignment = db.session.get(PlannerEntry, entry_id)
                R.upsert_record(assignment, code, form, {}, R.DRAFT,
                                user=engineer, full_projection=True)
                db.session.commit()
                did = db.session.execute(text(
                    "SELECT id FROM `datasheet` WHERE planner_entry_id=:p"),
                    {"p": entry_id}).scalar()

                if life != "draft_only":
                    PJ.record_transition(entry_id, "Peer Review", actor=engineer,
                                         comment="Submitted for peer review.",
                                         snapshot=True, submitted=True, from_status="Draft")
                if life in ("reject_then_approve", "reject_only"):
                    rc, note = REJECT_REASONS[code]
                    PJ.record_transition(entry_id, "Rejected", actor=reviewer, decided=True,
                                         comment=note, from_status="Peer Review",
                                         reason_code=rc)
                if life == "reject_then_approve":
                    form["deviation"] = "Missing orientations added after review."
                    R.upsert_record(assignment, code, form, {}, R.DRAFT,
                                    user=engineer, full_projection=True)
                    db.session.commit()
                    PJ.record_transition(entry_id, "Peer Review", actor=engineer,
                                         comment="Grid completed. Resubmitted.",
                                         snapshot=True, submitted=True, from_status="Draft")
                if life in ("approve", "reject_then_approve"):
                    PJ.record_transition(entry_id, "Approved", actor=reviewer,
                                         comment="Checked against the raw data. Approved.",
                                         decided=True, from_status="Peer Review")
                    db.session.execute(text(
                        "UPDATE planner_entries SET status='datasheet_uploaded' WHERE id=:e"),
                        {"e": entry_id})
                    db.session.commit()

                def n(table, col="datasheet_id"):
                    try:
                        return db.session.execute(text(
                            "SELECT COUNT(*) FROM `%s` WHERE %s=:d" % (table, col)),
                            {"d": did}).scalar()
                    except Exception:  # noqa: BLE001
                        db.session.rollback()
                        return "-"
                print("  %-15s entry=%-4s ds=%-4s %-20s spec=%s meas=%-4s obs=%-4s "
                      "equip=%s sw=%s mod=%s rev=%s hist=%s"
                      % (code, entry_id, did, life,
                         n("datasheet_" + code.lower()), n("datasheet_measurement"),
                         n("datasheet_observation"), n("datasheet_equipment"),
                         n("datasheet_software"), n("datasheet_modification"),
                         n("datasheet_revision"), n("datasheet_status_history")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
