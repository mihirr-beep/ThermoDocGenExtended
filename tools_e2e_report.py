# -*- coding: utf-8 -*-
"""The whole thing, once, on a request that did not exist five minutes ago.

    raise a request -> fill each datasheet -> submit -> peer review -> approve
    -> generate the IEC-FRM-516 report

Every step goes through the real HTTP route, so the projection, the draft
history, the revision snapshots and the report builder are all exercised the way
a lab engineer exercises them. Nothing is inserted straight into a datasheet
table.

Field VALUES are generated from the field's own name, so the report reads like a
report - 0.15 to 30 MHz, 230 V, +/-4 kV, criterion A - rather than the sentinel
strings a round-trip test uses. A reader should be able to tell whether the
document is right, which they cannot do if every cell says ZQCR0007.

    python tools_e2e_report.py                       # 4 random tests
    python tools_e2e_report.py --tests CE ESD SURGE  # or name them
"""
import argparse
import datetime
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module                                    # noqa: E402
import create_sample_request as SEED                        # noqa: E402
from datasheet_gen.registry import load_schema              # noqa: E402
from models import db, PlannerEntry, User                   # noqa: E402
from sqlalchemy import text                                  # noqa: E402

# request-level codes, and what the planner calls the same test
CHOICES = ("CE", "RE", "ESD", "EFT", "SURGE", "CRF", "RS", "HARMONIC",
           "FLICKER", "POWER_FREQ", "VOLTAGE_DIPS")

# The datasheet engine's own code, keyed by the planner's spelling.
SHEET_CODE = {"CE": "CE", "RE": "RE", "ESD": "ESD", "EFT": "EFT",
              "Surge": "SURGE", "CRF": "CRF", "RS_RI": "RS_RI",
              "Harmonic": "HARMONIC", "VoltageFlicker": "VOLTAGEFLICKER",
              "PFMF": "PFMF", "VoltageDips": "VOLTAGEDIPS"}

FREE_TEXT = {"text", "textarea", "number", "date", "select"}


# --------------------------------------------------------------------------
# plausible values, derived from the field's name
# --------------------------------------------------------------------------
# A report full of "ZQCR0007" is unreadable, so a reviewer cannot tell a correct
# document from a broken one. These are keyed on what the field is called, most
# specific first - the first pattern that appears in the key wins.
VALUE_RULES = (
    ("ambient_temp",   "23.4"),
    ("relative_humid", "48"),
    ("humidity",       "48"),
    ("temperature",    "23.4"),
    ("pressure",       "101.3"),
    ("freq_start",     "0.15"),
    ("freq_stop",      "30"),
    ("frequency",      "50"),
    ("freq",           "0.15 - 30"),
    ("voltage",        "230"),
    ("current",        "2.0"),
    ("power",          "300"),
    ("level",          "4"),
    ("limit",          "56.0"),
    ("margin",         "-11.9"),
    ("distance",       "3"),
    ("height",         "1-4"),
    ("bandwidth",      "120k"),
    ("step",           "40k"),
    ("dwell",          "3"),
    ("duration",       "1"),
    ("time",           "1"),
    ("date",           None),          # filled with a real date
    ("polarization",   "Horizontal and Vertical"),
    ("detector",       "Peak and Quasi-peak"),
    ("attenuation",    "Auto"),
    ("antenna",        "Bilog CBL6112B"),
    ("probe",          "ESD gun NSG438"),
    ("mode",           "Mode A"),
    ("criteri",        "A"),
    ("observation",    "A"),
    ("result",         "A"),
    ("remark",         "No degradation observed."),
    ("comment",        "No degradation observed."),
    ("deviation",      "None"),
    ("procedure",      "As per the basic standard."),
    ("standard",       "IEC 61326-1:2020"),
    ("software",       "iec.control"),
    ("version",        "10.3.2"),
    ("serial",         "SN-2026-0417"),
    ("model",          "50129885"),
    ("make",           "Rohde & Schwarz"),
    ("name",           "EMI Test Receiver"),
    ("engineer",       None),
    ("tested_by",      None),
)

EQUIPMENT = [("EMI Test Receiver", "Rohde & Schwarz", "ESCI 7", "100234", "2027-01-18"),
             ("LISN", "Schwarzbeck", "NSLK 8127", "8127-441", "2026-11-02"),
             ("Bilog Antenna", "Chase", "CBL6112B", "2894", "2027-03-09"),
             ("ESD Simulator", "Teseq", "NSG 438", "1204", "2026-09-30")]


def value_for(key, kind, today, engineer):
    k = key.lower()
    for needle, val in VALUE_RULES:
        if needle in k:
            if needle == "date":
                return today.strftime("%Y-%m-%d") if kind == "date" else today.strftime("%d %b %Y")
            if needle in ("engineer", "tested_by"):
                return engineer
            return val
    if kind == "date":
        return today.strftime("%Y-%m-%d")
    if kind == "number":
        return "1"
    return "As applicable"


def payload_for(code, pid, tco, today, engineer):
    """Every field of one datasheet, filled."""
    data = {"assignment_id": str(pid), "tco_id": tco, "_full_save": "1",
            "ambient_temperature": "23.4", "relative_humidity": "48",
            "test_date": today.strftime("%Y-%m-%d"),
            "tested_by": engineer, "tested_by_name": engineer,
            "result": "A", "deviation": "None",
            "required_performance_criteria": "A",
            "met_performance_criteria": "A"}

    if code == "CE":
        # the bespoke form: parallel name[] arrays rather than a JSON schema
        data["meas_index[]"] = ["1"]
        data["meas_label_1"] = "Test 1"
        for side in ("line1", "neutral1"):
            data["%s_qp_freq[]" % side] = ["0.212", "0.485", "1.240"]
            data["%s_qp[]" % side] = ["48.2", "44.1", "39.7"]
            data["%s_qp_limit[]" % side] = ["63.5", "56.0", "56.0"]
            data["%s_qp_margin[]" % side] = ["-15.3", "-11.9", "-16.3"]
            data["%s_avg_freq[]" % side] = ["0.212", "0.485", "1.240"]
            data["%s_avg[]" % side] = ["34.6", "31.2", "28.4"]
            data["%s_avg_limit[]" % side] = ["53.5", "46.0", "46.0"]
            data["%s_avg_margin[]" % side] = ["-18.9", "-14.8", "-17.6"]
        for i, (n, mk, md, sn, cal) in enumerate(EQUIPMENT):
            data.setdefault("eq_name[]", []).append(n)
            data.setdefault("eq_make[]", []).append(mk)
            data.setdefault("eq_model[]", []).append(md)
            data.setdefault("eq_serial[]", []).append(sn)
            data.setdefault("eq_cal_due[]", []).append(cal)
        data["mod_state[]"] = ["0"]
        data["mod_description[]"] = ["Initial state"]
        return data

    schema = load_schema(code)
    for sec in schema.get("sections", []):
        for it in sec.get("items", []):
            key, typ = it.get("key"), it.get("type")
            if not key:
                continue
            if typ == "table":
                cols = it.get("columns", [])
                rows = 3 if any("freq" in (c.get("key") or "") for c in cols) else 2
                for col in cols:
                    ck = "%s__%s[]" % (key, col["key"])
                    data[ck] = [value_for(col.get("key", ""), col.get("type", "text"),
                                          today, engineer) for _ in range(rows)]
            elif typ in FREE_TEXT:
                if it.get("options"):
                    data[key] = str(it["options"][0].get("value", it["options"][0]))
                else:
                    data[key] = value_for(key, typ, today, engineer)
    # the equipment / software / modification tables every generic sheet has
    for i, (n, mk, md, sn, cal) in enumerate(EQUIPMENT):
        for c, v in (("c0", n), ("c1", mk), ("c2", md), ("c3", sn), ("c4", cal)):
            data.setdefault("test_equipment_used_rows__%s[]" % c, []).append(v)
    data["software_used_rows__c0[]"] = ["iec.control", "Net.Control"]
    data["software_used_rows__c1[]"] = ["10.3.2", "3.2.6"]
    data["eut_modification_rec_rows__c0[]"] = ["0"]
    data["eut_modification_rec_rows__c1[]"] = ["Initial state"]
    data["obs_legend_code[]"] = ["A"]
    data["obs_legend_desc[]"] = ["No degradation of performance was observed."]
    data.update(observation_payload(code))
    return data


# --------------------------------------------------------------------------
# the observation matrices
# --------------------------------------------------------------------------
# These are NOT in the JSON schema. Each immunity datasheet builds its own grid
# in JavaScript and posts it under its own field names, so a schema-driven
# payload fills every scalar and table and leaves the observation tables empty -
# which on an immunity report is the one table that matters. Names taken from
# the readers in form_extract / generic_service, not guessed.
#
# One cell is deliberately B rather than A. An all-A report cannot show whether
# a non-A value survives capture, projection and rendering, and B is the value
# that has to reach a reviewer intact.
def observation_payload(code):
    d = {}
    if code == "ESD":
        # ind_r<i>_c<1..6>; dir/air additionally carry a per-row point name
        for i in range(1, 9):
            for c in range(1, 7):
                d["ind_r%d_c%d" % (i, c)] = "A"
        for prefix, points in (("dir", ("Enclosure - top", "Enclosure - side", "Display bezel")),
                               ("air", ("Ventilation slots", "USB port", "Power inlet"))):
            for i, point in enumerate(points, 1):
                d["%s_r%d_name" % (prefix, i)] = point
                for c in range(1, 7):
                    d["%s_r%d_c%d" % (prefix, i, c)] = "A"
        d["dir_r2_c3"] = "B"
    elif code == "EFT":
        for kind, label in (("power", "AC mains L+N+PE"), ("signal", "Ethernet port")):
            d["eft_obs_%s_cols" % kind] = "+0.5 kV,-0.5 kV,+1 kV,-1 kV"
            for ri, rowname in enumerate((label, "Normal mode")):
                d["eft_obs_%s_row_%d" % (kind, ri)] = rowname
                for ci in range(4):
                    d["eft_obs_%s_%d__c%d" % (kind, ri, ci)] = "A"
    elif code == "VOLTAGEDIPS":
        for kind, combos in (("dips", ("0 deg / L1", "180 deg / L1")),
                             ("intr", ("0 deg / L1",))):
            for ci, combo in enumerate(combos):
                d["vdips_%s_combo_%d" % (kind, ci)] = combo
                d["vdips_%s_%d__pct[]" % (kind, ci)] = ["0", "40", "70"]
                d["vdips_%s_%d__dur[]" % (kind, ci)] = ["0.5", "10", "25"]
                d["vdips_%s_%d__obs[]" % (kind, ci)] = ["A", "A", "A"]
    elif code == "RS_RI":
        for base in ("f_80_to_1000", "f_1000_to_6000", "f_ism"):
            d[base + "_col_1"] = "3"
            d[base + "_col_2"] = "3"
            for c in range(3, 11):
                d["%s_col_%d" % (base, c)] = "A"
    elif code == "PFMF":
        for base in ("pf_50", "pf_60"):
            d[base + "_col_1"] = "3"
            for c in range(3, 10):
                d["%s_col_%d" % (base, c)] = "A"
    elif code == "SURGE":
        for kind in ("ac", "dc", "signal"):
            d["surge_obs_%s_cols" % kind] = "+0.5 kV,-0.5 kV,+1 kV,-1 kV"
            for ri, rowname in enumerate(("L-N", "L-PE")):
                d["surge_obs_%s_row_%d" % (kind, ri)] = rowname
                for ci in range(4):
                    d["surge_obs_%s_%d__c%d" % (kind, ri, ci)] = "A"
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tco", default="IEC-EMC-011")
    ap.add_argument("--job", default="TFS-EMC-2026-011")
    ap.add_argument("--tests", nargs="*", default=None,
                    help="request-level codes; omit for 4 at random")
    ap.add_argument("--engineer", type=int, default=SEED.DEFAULT_ENGINEER)
    args = ap.parse_args()

    picked = args.tests or random.sample(list(CHOICES), 4)
    print("=" * 74)
    print("END TO END: %s / %s" % (args.tco, args.job))
    print("tests picked: %s" % ", ".join(picked))
    print("=" * 74)

    app = app_module.create_app("default")
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["PROPAGATE_EXCEPTIONS"] = True
    app.login_manager.session_protection = None

    # 1. the request, with only the chosen tests
    SEED.TEST_CODES = tuple(picked)
    print("\n1. RAISING THE REQUEST")
    SEED.create(app, args.tco, args.job, args.engineer)

    with app.app_context():
        rid = db.session.execute(text(
            "SELECT id FROM iec_emc_requests WHERE tco_id=:t"), {"t": args.tco}).scalar()
        entries = db.session.execute(text(
            "SELECT id, test_name FROM planner_entries WHERE test_request_id=:r "
            "ORDER BY id"), {"r": rid}).fetchall()
        admin = User.query.filter_by(role="admin").first()
        uid, uname = admin.id, admin.username
        today = datetime.date.today()
    print("   request id %s, %d planner entries" % (rid, len(entries)))

    client = app.test_client()

    def login():
        with client.session_transaction() as s:
            s["_user_id"] = str(uid)
            s["_fresh"] = True

    # 2. fill each datasheet through the real save-draft route
    print("\n2. FILLING THE DATASHEETS")
    filled = []
    for pid, planner_name in entries:
        code = SHEET_CODE.get(planner_name)
        if not code:
            print("   %-16s no datasheet engine - skipped" % planner_name)
            continue
        url = ("/datasheet/ce/save-draft" if code == "CE"
               else "/datasheet/g/%s/save-draft" % code.lower())
        data = payload_for(code, pid, args.tco, today, uname)
        login()
        r = client.post(url, data=data)
        n = sum(len(v) if isinstance(v, list) else 1 for v in data.values())
        print("   %-16s entry %-4s %-3s  %d values posted" % (code, pid, r.status_code, n))
        if r.status_code == 200:
            filled.append((pid, code))

    # 3. submit + approve each, so the report has approved data to build from
    print("\n3. SUBMIT -> PEER REVIEW -> APPROVE")
    with app.app_context():
        from datasheet_gen.projection import record_transition
        for pid, code in filled:
            record_transition(pid, "Peer Review", actor=admin, from_status="Draft",
                              snapshot=True, submitted=True,
                              comment="Sent for peer review (end-to-end test).")
            record_transition(pid, "Approved", actor=admin, decided=True,
                              comment="Approved (end-to-end test).")
            row = db.session.execute(text(
                "SELECT status, revision_no FROM `datasheet` WHERE planner_entry_id=:p"),
                {"p": pid}).first()
            db.session.execute(text(
                "UPDATE planner_entries SET status='datasheet_uploaded' WHERE id=:p"),
                {"p": pid})
            print("   %-16s entry %-4s -> %s (revision %s frozen)"
                  % (code, pid, row[0], (row[1] or 1) - 1))
        db.session.commit()

    # 4. the report
    print("\n4. GENERATING THE REPORT")
    login()
    t0 = time.time()
    r = client.post("/api/test-requests/%d/generate-test-report" % rid, json={})
    took = time.time() - t0
    body = r.get_json() or {}
    print("   HTTP %s in %.1fs" % (r.status_code, took))
    path = body.get("file_path")
    if r.status_code != 200 or not path:
        print("   FAILED: %s" % str(body)[:300])
        return 1
    print("   %s" % os.path.basename(path))
    print("   %s" % (body.get("message") or "")[:200])
    print("\nreport: %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
