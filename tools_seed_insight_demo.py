# -*- coding: utf-8 -*-
"""Seed the failure/rejection history that insight questions need.

WHY THIS EXISTS
---------------
The database records what the lab currently does, and the lab currently
succeeds: when this was written datasheet_revision.result was blank in all 45
rows, only 7 rejections existed (all from test harnesses), and no product had
been tested twice with a different outcome. Every "why did it fail" question is
answerable in principle and unanswerable in practice, because there is nothing
to find.

This builds the missing shape. It is SYNTHETIC and says so everywhere: product
names start with "DEMO", TCOs use the DEMO-EMC-2xx block, and every request
carries is_synthetic=1 so it can be found, filtered, or removed with one WHERE.
The chatbot will quote those names back in its answers, which is the point -
nobody should be able to mistake a seeded failure for a real one.

HOW IT SEEDS
------------
Through project() and record_transition() - the same functions the app calls -
never by INSERTing into the analytical tables directly. Seed data that does not
match the production write path produces primitives that work on the demo and
fail on real data, which is worse than no demo at all.

A CAMPAIGN IS NOT A REVISION
----------------------------
The first cut of this file got the model wrong in exactly the way the feature
is meant to prevent, so the distinction is worth stating plainly.

"Why did Product ABC fail its first three tests" means three separate TESTS.
The unit failed, engineering changed the hardware, and it came back for a new
campaign - a new request, a new datasheet. It does NOT mean three revisions of
one form, and a reviewer does not "reject" a datasheet for recording a failure:
an accurate record of a failing unit is an accurate record, and it gets
approved.

Revisions are the OTHER axis - the paperwork cycle. A datasheet goes back to
the engineer because the LISN calibration had expired or the setup photos are
missing, and revision 2 is the corrected record of the same test.

So each attempt here is its own request, and two campaigns additionally carry a
review rejection so both axes exist in the data and can be told apart.

THE CORPUS
----------
Sized by the hardest question, "have other products seen this failure?", which
is worthless at n=1 and meaningless if everything matches:

    Aurora C5   4 campaigns   CE breach, + a CAL_EXPIRED rejection    the full arc
    Vega V2     2 campaigns   CE breach, same mode                    cohort
    Orion O9    3 campaigns   CE breach, + a MISSING_PHOTO rejection  cohort (n=3)
    Lyra L3     2 campaigns   EFT reset                               DIFFERENT mode
    Nova N1     2 campaigns   EFT reset                               cohort (n=2)
    Pavo P7     1 campaign    clean pass                              control

The second failure mode is not decoration. Without it a cohort query has
nothing to exclude, and a matcher that returns everything looks identical to
one that works.

CONDUCTED EMISSION, NOT RADIATED
--------------------------------
The numbers live in CE's line_measurements grid because it is the only one with
semantic column keys (qp_freq, qp, qp_limit, qp_margin) and a precomputed
margin - RE's grid is positional c0..c6 and its existing rows are placeholder
text. So a breach here is literally qp_margin > 0 and "improvement" is an
unambiguous margin delta. Limits follow CISPR class B quasi-peak, matching the
real rows already in the table.

    python tools_seed_insight_demo.py           # create (idempotent)
    python tools_seed_insight_demo.py --purge   # remove every is_synthetic row
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text  # noqa: E402

TCO_PREFIX = "DEMO-EMC-2"

# CISPR class B quasi-peak / average limits at the frequencies used below.
# Fixed across every revision on purpose: "which frequency improved most" is a
# comparison at the SAME frequency, and a corpus that moved the frequencies
# between attempts would make the question unanswerable by construction.
FREQS = ["0.212", "0.485", "0.720", "1.150", "3.400", "12.600"]
QP_LIMIT = ["63.5", "56.0", "56.0", "56.0", "56.0", "60.0"]
AV_LIMIT = ["53.5", "46.0", "46.0", "46.0", "46.0", "50.0"]


def ce_grid(qp_values):
    """The bespoke CE form's parallel arrays for one attempt.

    _ce_arrays reads line_<name>[] / neutral_<name>[] in _CE_MEAS_COLS order, so
    the margin is computed here rather than typed: a seeded margin that
    disagreed with its own measurement and limit would be the exact kind of
    quietly wrong number this whole feature is supposed to stop producing.
    """
    out = {}
    for side in ("line", "neutral"):
        # neutral runs ~0.6 dB under line, as a real LISN pair does
        qp = [round(float(v) - (0.6 if side == "neutral" else 0.0), 1) for v in qp_values]
        av = [round(v - 6.4, 1) for v in qp]          # average tracks QP below it
        out[side + "_qp_freq[]"] = list(FREQS)
        out[side + "_qp[]"] = ["%.1f" % v for v in qp]
        out[side + "_qp_limit[]"] = list(QP_LIMIT)
        out[side + "_qp_margin[]"] = ["%.1f" % (v - float(l)) for v, l in zip(qp, QP_LIMIT)]
        out[side + "_avg_freq[]"] = list(FREQS)
        out[side + "_avg[]"] = ["%.1f" % v for v in av]
        out[side + "_avg_limit[]"] = list(AV_LIMIT)
        out[side + "_avg_margin[]"] = ["%.1f" % (v - float(l)) for v, l in zip(av, AV_LIMIT)]
    out["meas_index[]"] = []
    return out


# One CAMPAIGN: (result, failure_code, criteria, qp values or None,
#                modifications present on the unit, paperwork rejection or None,
#                what the reviewer wrote on approval)
# The paperwork rejection - (reason_code, comment) - is what makes a campaign
# take two revisions instead of one. It is about the RECORD, never about the
# unit, which is why Aurora campaign 2 fails the standard AND gets bounced for a
# calibration certificate: two independent facts about the same afternoon.
AURORA = [
    ("FAIL", "CE_LIMIT_EXCEEDED", "D",
     ["48.2", "52.1", "60.8", "58.1", "44.3", "41.0"], [], None,
     "Record is accurate: EUT exceeds the class B quasi-peak limit at 0.72 MHz "
     "(+4.8 dB) and 1.15 MHz (+2.1 dB). Approved as a record of failure."),
    ("FAIL", "CE_LIMIT_EXCEEDED", "D",
     ["48.4", "52.3", "60.5", "57.9", "44.1", "41.2"], [],
     ("CAL_EXPIRED",
      "Cannot accept this record: the LISN calibration certificate expired "
      "2026-01-31, before the test date. Re-issue with a calibrated LISN."),
     "Re-issued against the calibrated LISN. EUT still over the limit at "
     "0.72 MHz (+4.5 dB). Approved as a record of failure."),
    ("FAIL", "CE_LIMIT_EXCEEDED", "C",
     ["47.9", "51.2", "57.9", "55.4", "43.8", "40.6"],
     [("Fitted", "Ferrite sleeve Wurth 74271132 on the mains inlet cable")], None,
     "Improved after the ferrite but still failing: 0.72 MHz is +1.9 dB over "
     "the limit. 1.15 MHz now passes at -0.6 dB. Approved as a record."),
    ("PASS", None, "A",
     ["47.1", "49.8", "52.6", "51.2", "42.9", "40.1"],
     [("Fitted", "Ferrite sleeve Wurth 74271132 on the mains inlet cable"),
      ("Fitted", "Common-mode choke 4.7 mH at the mains inlet"),
      ("Fitted", "Y-capacitor 2.2 nF across line-neutral")], None,
     "All frequencies below the class B limit, worst case -3.4 dB at 0.72 MHz. "
     "Compliant. Approved."),
]

VEGA = [
    ("FAIL", "CE_LIMIT_EXCEEDED", "D",
     ["47.5", "51.8", "59.6", "54.2", "43.9", "40.8"], [], None,
     "EUT +3.6 dB over the class B limit at 0.72 MHz. Approved as a record."),
    ("PASS", None, "A",
     ["46.9", "49.4", "52.1", "50.6", "42.7", "40.0"],
     [("Fitted", "Common-mode choke 3.3 mH at the mains inlet")], None,
     "Below the limit at every frequency after the choke. Approved."),
]

ORION = [
    ("FAIL", "CE_LIMIT_EXCEEDED", "D",
     ["48.8", "53.2", "61.4", "57.2", "44.6", "41.5"], [], None,
     "EUT +5.4 dB over the class B limit at 0.72 MHz. Approved as a record."),
    ("FAIL", "CE_LIMIT_EXCEEDED", "D",
     ["48.6", "53.0", "60.9", "56.8", "44.4", "41.3"], [],
     ("MISSING_PHOTO",
      "Test setup photographs are missing for the signal-line configuration, "
      "so the setup cannot be verified against the description."),
     "Photographs supplied. EUT still +4.9 dB over at 0.72 MHz. Approved as a "
     "record of failure."),
    ("PASS", None, "A",
     ["47.4", "50.1", "53.2", "51.6", "43.1", "40.4"],
     [("Fitted", "Mains filter replaced with Schaffner FN2090-6-06"),
      ("Fitted", "Ferrite sleeve on the internal DC harness")], None,
     "Worst case -2.8 dB at 0.72 MHz after the filter change. Approved."),
]

# EFT failures are observed behaviour, not a number - the EUT resets or it does
# not - so these carry no measurement grid. That is deliberate: a system that
# can only explain a failure with a frequency delta cannot explain half of what
# this lab actually sees.
LYRA = [
    ("FAIL", "EFT_RESET", "D", None, [], None,
     "EUT resets at 2 kV on the signal port and needs a power cycle. Criterion "
     "B requires self-recovery. Approved as a record of failure."),
    ("PASS", None, "B", None,
     [("Modified", "Firmware v2.4.1: watchdog reset window widened to 250 ms"),
      ("Fitted", "Ferrite clamp on the signal harness")], None,
     "Self-recovers at 2 kV after the watchdog change. Criterion B met. Approved."),
]

NOVA = [
    ("FAIL", "EFT_RESET", "D", None, [], None,
     "EUT resets at 2 kV on the signal port and does not recover without "
     "operator intervention. Approved as a record of failure."),
    ("PASS", None, "B", None,
     [("Modified", "Firmware v1.8.0: input debounce added on the sensor line")], None,
     "Recovers unaided at 2 kV after the firmware update. Approved."),
]

PAVO = [
    ("PASS", None, "A",
     ["46.2", "48.9", "49.9", "48.7", "42.1", "39.6"], [], None,
     "Compliant at first attempt, worst case -6.1 dB at 0.72 MHz. Approved."),
]

PRODUCTS = [
    ("DEMO Aurora Centrifuge C5", "AUR-C5-230", "CE", AURORA),
    ("DEMO Vega Incubator V2", "VEG-V2-110", "CE", VEGA),
    ("DEMO Orion Analyzer O9", "ORI-O9-400", "CE", ORION),
    ("DEMO Lyra Pump L3", "LYR-L3-015", "EFT", LYRA),
    ("DEMO Nova Sampler N1", "NOV-N1-220", "EFT", NOVA),
    ("DEMO Pavo Chiller P7", "PAV-P7-075", "CE", PAVO),
]


def purge(db):
    """Remove every synthetic request and everything hanging off it.

    Ordered child-first because the structural links are deliberately unenforced
    (see projection_schema): nothing would stop a half-delete leaving orphan
    datasheets pointing at a request that no longer exists.
    """
    ids = [r[0] for r in db.session.execute(text(
        "SELECT id FROM iec_emc_requests WHERE is_synthetic=1")).fetchall()]
    if not ids:
        return 0, 0
    ent = [r[0] for r in db.session.execute(text(
        "SELECT id FROM planner_entries WHERE test_request_id IN :i"),
        {"i": tuple(ids)}).fetchall()] or [-1]
    dids = [r[0] for r in db.session.execute(text(
        "SELECT id FROM `datasheet` WHERE planner_entry_id IN :e"),
        {"e": tuple(ent)}).fetchall()] or [-1]

    for tbl in ("datasheet_measurement", "datasheet_status_history",
                "datasheet_revision", "datasheet_modification",
                "datasheet_equipment", "datasheet_software",
                "datasheet_observation", "datasheet_observation_legend",
                "datasheet_ce", "datasheet_eft"):
        try:
            db.session.execute(text("DELETE FROM `%s` WHERE datasheet_id IN :d" % tbl),
                               {"d": tuple(dids)})
        except Exception:
            db.session.rollback()
    for tbl in ("datasheet_rev_modification", "datasheet_rev_equipment",
                "datasheet_rev_software", "datasheet_rev_observation",
                "datasheet_rev_observation_legend", "datasheet_rev_ce",
                "datasheet_rev_eft"):
        try:
            db.session.execute(text("DELETE FROM `%s` WHERE datasheet_id IN :d" % tbl),
                               {"d": tuple(dids)})
        except Exception:
            db.session.rollback()
    db.session.execute(text("DELETE FROM `datasheet` WHERE id IN :d"), {"d": tuple(dids)})
    db.session.execute(text("DELETE FROM datasheet_draft_history WHERE planner_entry_id IN :e"),
                       {"e": tuple(ent)})
    db.session.execute(text("DELETE FROM datasheet_records WHERE planner_entry_id IN :e"),
                       {"e": tuple(ent)})
    db.session.execute(text("DELETE FROM planner_entries WHERE id IN :e"), {"e": tuple(ent)})
    db.session.execute(text("DELETE FROM iec_emc_requests WHERE id IN :i"), {"i": tuple(ids)})
    db.session.commit()
    return len(ids), len(dids)


def make_request(db, seq, product, model, engineer, start):
    tco = "%s%02d" % (TCO_PREFIX, seq)
    db.session.execute(text(
        "INSERT INTO iec_emc_requests (user_id, tco_id, status, product_name, manufacturer, "
        "manufacturer_address, model_number, serial_number, test_samples, "
        "samples_available_in_lab, requester_name, requester_department, requester_group, "
        "requester_division, requester_site, requester_email, requester_contact, "
        "requester_designation, requester_date, product_type, is_synthetic, "
        "created_at, updated_at) VALUES (:u, :t, 'Datasheet Uploaded', :p, "
        "'Thermo Fisher Scientific', 'Bangalore, India', :m, :sn, 1, 'Yes', "
        "'Demo Requester', 'EMC Lab', 'IDT', 'Analytical Instruments', 'Bangalore', "
        "'demo@example.invalid', '0000000000', 'Engineer', :d, 'Laboratory Equipment', 1, "
        "NOW(), NOW())"),
        {"u": engineer.id, "t": tco, "p": product, "m": model,
         "sn": model + "-0001", "d": start})
    rid = db.session.execute(text("SELECT LAST_INSERT_ID()")).scalar()
    return rid, tco


def make_entry(db, rid, tco, code, engineer, reviewer, start):
    db.session.execute(text(
        "INSERT INTO planner_entries (test_request_id, tco_id, test_name, test_person_name, "
        "engineer_user_id, peer_reviewer_user_id, start_date, end_date, status, "
        "created_at, updated_at) VALUES (:r, :t, :n, :pn, :e, :pr, :s, :en, "
        "'datasheet_uploaded', NOW(), NOW())"),
        {"r": rid, "t": tco, "n": code, "pn": engineer.username, "e": engineer.id,
         "pr": reviewer.id, "s": start, "en": start + timedelta(days=2)})
    return db.session.execute(text("SELECT LAST_INSERT_ID()")).scalar()


def base_form(code, tco, product, engineer, when, qp):
    form = {
        "tco_id": tco, "product_name": product, "eut_name": product,
        "test_date": when.isoformat(), "tested_by": engineer.username,
        "ambient_temperature": "23", "relative_humidity": "48",
        "eut_configuration": "Bench top, mains powered",
        "eut_input_voltage_frequency": "230 V / 50 Hz",
        "monitoring_parameters": "Continuous functional monitoring",
        "test_equipment_used_rows__c0": ["LISN", "Receiver"],
        "sop_reference": "IEC-SOP-501" if code == "CE" else "IEC-SOP-504",
    }
    if qp:
        form.update(ce_grid(qp))
    return form


def add_modifications(form, code, mods):
    """EUT MODIFICATION RECORD rows, in whichever shape this test's form posts.

    CE is the bespoke form and posts parallel mod_<name>[] arrays; every other
    test posts the generic key__cN[] grid. Getting this wrong is silent - the
    projection simply finds no rows and datasheet_modification stays empty -
    which is exactly how the first run of this seeder produced a corpus with no
    modifications in it at all.
    """
    if not mods:
        return
    if code == "CE":
        form["mod_state[]"] = [m[0] for m in mods]
        form["mod_description[]"] = [m[1] for m in mods]
        form["mod_fitted_by[]"] = ["Engineering" for _ in mods]
        form["mod_date[]"] = ["" for _ in mods]
    else:
        form["eut_modification_rec_rows__c0[]"] = [m[0] for m in mods]
        form["eut_modification_rec_rows__c1[]"] = [m[1] for m in mods]
        form["eut_modification_rec_rows__c2[]"] = ["Engineering" for _ in mods]
        form["eut_modification_rec_rows__c3[]"] = ["" for _ in mods]


def submit_and_decide(db, PJ, entry_id, form, result, fail_code, criteria,
                      decision, reason_code, comment, engineer, reviewer):
    """Fill, project, freeze the revision, and record the review decision."""
    # Through the form, not straight onto the header: _header_values builds
    # datasheet.result from form["result"], so setting only the record left the
    # live header blank while the frozen revision said FAIL - the same fact
    # disagreeing with itself depending on which table you asked.
    form = dict(form, result=result)
    db.session.execute(text(
        "UPDATE datasheet_records SET form_json=:f, result=:r, updated_at=NOW() "
        "WHERE planner_entry_id=:p"),
        {"f": json.dumps(form, ensure_ascii=False), "r": result, "p": entry_id})
    db.session.commit()

    rec = dict(db.session.execute(text(
        "SELECT planner_entry_id, test_code, form_json, images_json, result "
        "FROM datasheet_records WHERE planner_entry_id=:p"), {"p": entry_id}).mappings().first())
    ent = db.session.execute(text(
        "SELECT * FROM planner_entries WHERE id=:e"), {"e": entry_id}).mappings().first()
    req = db.session.execute(text(
        "SELECT * FROM iec_emc_requests WHERE id=:r"),
        {"r": ent["test_request_id"]}).mappings().first()
    PJ.project(rec, dict(ent), dict(req))

    # After project(), because _header_values rebuilds the header from the form
    # and neither of these is a form field.
    db.session.execute(text(
        "UPDATE `datasheet` SET failure_reason_code=:c, met_performance_criteria=:m "
        "WHERE planner_entry_id=:p"),
        {"c": fail_code, "m": criteria, "p": entry_id})
    db.session.commit()

    PJ.record_transition(entry_id, "Peer Review", actor=engineer,
                         comment="Submitted for peer review.", snapshot=True,
                         submitted=True, from_status="Draft")
    PJ.record_transition(entry_id, decision, actor=reviewer, comment=comment,
                         decided=True, reason_code=reason_code,
                         from_status="Peer Review")


def run_campaign(db, PJ, entry_id, code, form, result, fail_code, criteria,
                 rejection, approve_comment, engineer, reviewer):
    """One test campaign, which is one or two revisions of one datasheet.

    A campaign takes two revisions only when the reviewer bounced the RECORD.
    The unit's result is identical across both - the paperwork changed, the
    hardware did not - which is what lets a query tell a re-issued record apart
    from a re-test.
    """
    if rejection:
        reason_code, reject_comment = rejection
        submit_and_decide(db, PJ, entry_id, form, result, fail_code, criteria,
                          "Rejected", reason_code, reject_comment,
                          engineer, reviewer)
    submit_and_decide(db, PJ, entry_id, form, result, fail_code, criteria,
                      "Approved", None, approve_comment, engineer, reviewer)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--purge", action="store_true",
                    help="delete every is_synthetic request and its data, then stop")
    args = ap.parse_args()

    import app as app_module
    from models import db, User
    import datasheet_gen.projection as PJ

    a = app_module.create_app("default")
    with a.app_context():
        n_req, n_ds = purge(db)
        if n_req:
            print("purged %d synthetic request(s), %d datasheet(s)" % (n_req, n_ds))
        if args.purge:
            return 0

        users = db.session.query(User).order_by(User.id).all()
        engineer = users[0]
        reviewer = next((u for u in users if u.id != engineer.id), engineer)
        print("engineer=%s  reviewer=%s\n" % (engineer.username, reviewer.username))

        day = date.today() - timedelta(days=240)
        seq = 1
        for product, model, code, campaigns in PRODUCTS:
            tcos = []
            for (result, fail_code, criteria, qp, mods,
                 rejection, approve_comment) in campaigns:
                # Every campaign is its OWN request: the unit went away, was
                # changed, and came back. Sharing one request across attempts
                # would make "the testing history of Product ABC" a single row.
                rid, tco = make_request(db, seq, product, model, engineer, day)
                entry_id = make_entry(db, rid, tco, code, engineer, reviewer, day)
                db.session.execute(text(
                    "INSERT INTO datasheet_records (planner_entry_id, test_code, form_json, "
                    "status, created_at, updated_at) VALUES (:p, :c, '{}', 'Draft', NOW(), NOW())"),
                    {"p": entry_id, "c": code})
                db.session.commit()

                form = base_form(code, tco, product, engineer, day, qp)
                add_modifications(form, code, mods)
                run_campaign(db, PJ, entry_id, code, form, result, fail_code,
                             criteria, rejection, approve_comment, engineer, reviewer)
                tcos.append("%s:%s%s" % (tco[-3:], result,
                                         "+" + rejection[0] if rejection else ""))
                seq += 1
                day += timedelta(days=21)      # a real turnaround between attempts
            print("  %-30s %-4s %d campaign(s)  %s"
                  % (product, code, len(campaigns), "  ".join(tcos)))
            day += timedelta(days=10)

        print("\nverifying...")
        n = db.session.execute(text(
            "SELECT COUNT(*) FROM iec_emc_requests WHERE is_synthetic=1")).scalar()
        rev = db.session.execute(text(
            "SELECT COUNT(*) FROM datasheet_revision r JOIN `datasheet` d ON d.id=r.datasheet_id "
            "JOIN planner_entries p ON p.id=d.planner_entry_id JOIN iec_emc_requests q "
            "ON q.id=p.test_request_id WHERE q.is_synthetic=1")).scalar()
        meas = db.session.execute(text(
            "SELECT COUNT(*) FROM datasheet_measurement m JOIN `datasheet` d ON d.id=m.datasheet_id "
            "JOIN planner_entries p ON p.id=d.planner_entry_id JOIN iec_emc_requests q "
            "ON q.id=p.test_request_id WHERE q.is_synthetic=1")).scalar()
        rej = db.session.execute(text(
            "SELECT COUNT(*) FROM datasheet_status_history WHERE to_status='Rejected'")).scalar()
        print("  synthetic requests %d   revisions %d   measurement rows %d   rejections %d"
              % (n, rev, meas, rej))
    return 0


if __name__ == "__main__":
    sys.exit(main())
