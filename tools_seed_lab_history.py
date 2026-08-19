# -*- coding: utf-8 -*-
"""Seed five products with a full campaign history, through the app's own writers.

    python tools_seed_lab_history.py            # create
    python tools_seed_lab_history.py --clean    # remove

Synthetic and says so: is_synthetic=1, product names start with DEMO, TCOs in the
DEMO-EMC-31x block so nothing here collides with the 30x corpus. Written through
records.upsert_record and projection.record_transition - the functions the app
calls when an engineer presses Save - so every analytical table is populated the
way a real submission populates it. Never an INSERT into datasheet_measurement or
its siblings: a form_json of subtly the wrong shape does not error, it projects to
nothing, and the empty result reads as a mapping bug.

WHAT THIS IS FOR, beyond volume.

The 30x corpus has twelve datasheets and every failure mode appears on exactly one
product. That makes three of the eleven insight primitives permanently mute:
cohort has nobody to return, resolved_how has nothing to compare, and metric_delta
has no two campaigns of one product to difference. "Has anything else failed like
this" could only ever answer no, and it was answering no because the data was thin
rather than because the lab was clean.

So the plan below is built around the shapes that were missing:

  CE_LIMIT_EXCEEDED on THREE products, EFT_RESET and RS_MALFUNCTION on two each,
  so cohort and resolved_how return peers rather than silence.

  INCOMPLETE_OBS, CAL_EXPIRED and MISSING_PHOTO each on two or more products, so
  rejection_modes ranks something real instead of four ones.

  DEMO-EMC-311's CE test FAILS with a 8.8 dB breach at 0.72 MHz, is sent back,
  and passes on the second revision with the same frequencies measured lower.
  That is the first datasheet in this database where the readings actually CHANGE
  between revisions, which is what metric_delta was written for and has never
  once been able to demonstrate.

  DEMO-EMC-316 is a SECOND campaign of the 311 product, so the cross-campaign
  comparison has two campaigns of one product for the first time.
"""
import sys
from datetime import date

# (tco, job, product, model, serial, offset, [(code, request_spelling, life,
#                                              failure_code)])
#
# life: draft_only | approve | reject_only | reject_then_approve
PLAN = [
    ("DEMO-EMC-311", "DEMO-JOB-311", "DEMO Vantage Water Purifier",
     "DEMO-50199311", "DEMOSN0000311", 0, [
         # the rework that finally moves a reading
         ("CE", "CE", "reject_then_approve", "CE_LIMIT_EXCEEDED"),
         ("ESD", "ESD", "approve", None),
         ("EFT", "EFT", "approve", None),
         ("RS_RI", "RS", "approve", "RS_MALFUNCTION"),
         ("SURGE", "Surge", "approve", None),
         ("CRF", "CRF", "draft_only", None),
     ]),
    ("DEMO-EMC-312", "DEMO-JOB-312", "DEMO Cascade Chromatograph",
     "DEMO-50199312", "DEMOSN0000312", 1, [
         ("CE", "CE", "approve", "CE_LIMIT_EXCEEDED"),
         ("EFT", "EFT", "reject_then_approve", "EFT_RESET"),
         ("ESD", "ESD", "reject_only", None),
         ("PFMF", "POWER_FREQ", "approve", None),
         ("VOLTAGEFLICKER", "FLICKER", "approve", None),
         ("HARMONIC", "HARMONIC", "approve", "HARMONIC_OVER"),
     ]),
    ("DEMO-EMC-313", "DEMO-JOB-313", "DEMO Halcyon Incubator",
     "DEMO-50199313", "DEMOSN0000313", 2, [
         ("CE", "CE", "approve", "CE_LIMIT_EXCEEDED"),
         ("ESD", "ESD", "approve", None),
         ("SURGE", "Surge", "reject_then_approve", None),
         ("EFT", "EFT", "approve", "EFT_RESET"),
         ("CRF", "CRF", "approve", None),
     ]),
    ("DEMO-EMC-314", "DEMO-JOB-314", "DEMO Zephyr Centrifuge",
     "DEMO-50199314", "DEMOSN0000314", 0, [
         ("ESD", "ESD", "approve", "ESD_LOCKUP"),
         ("RS_RI", "RS", "reject_then_approve", None),
         ("PFMF", "POWER_FREQ", "approve", None),
         ("SURGE", "Surge", "approve", "SURGE_DAMAGE"),
         ("VOLTAGEFLICKER", "FLICKER", "draft_only", None),
     ]),
    ("DEMO-EMC-315", "DEMO-JOB-315", "DEMO Kestrel Spectrometer",
     "DEMO-50199315", "DEMOSN0000315", 1, [
         ("CE", "CE", "reject_then_approve", None),
         ("ESD", "ESD", "approve", None),
         ("EFT", "EFT", "approve", None),
         ("RS_RI", "RS", "approve", "RS_MALFUNCTION"),
         ("HARMONIC", "HARMONIC", "approve", None),
         ("PFMF", "POWER_FREQ", "reject_only", None),
     ]),
    # SAME PRODUCT as 311, a later campaign. Two campaigns of one product is what
    # config_diff and the cross-campaign metric_delta need and have never had.
    ("DEMO-EMC-316", "DEMO-JOB-316", "DEMO Vantage Water Purifier",
     "DEMO-50199311", "DEMOSN0000311", 2, [
         ("CE", "CE", "approve", None),
         ("ESD", "ESD", "approve", None),
     ]),
]

# Which reviewer finding each rejected test gets. Chosen so the codes REPEAT
# across products - a Pareto of four ones tells nobody anything.
REJECTIONS = {
    ("DEMO-EMC-311", "CE"): ("CE_LIMIT_EXCEEDED_REVIEW", None),
    ("DEMO-EMC-312", "EFT"): ("SETUP_MISMATCH",
                              "Coupling clamp position does not match the setup photograph."),
    ("DEMO-EMC-312", "ESD"): ("INCOMPLETE_OBS",
                              "Indirect discharge grid stops at row 4. HCP and VCP rows are empty."),
    ("DEMO-EMC-313", "SURGE"): ("MISSING_PHOTO",
                                "No setup photographs attached for the DC port."),
    ("DEMO-EMC-314", "RS_RI"): ("CAL_EXPIRED",
                                "Field probe calibration expired on 2026-01-31."),
    ("DEMO-EMC-315", "CE"): ("INCOMPLETE_OBS",
                             "Neutral-side grid has only two of the four frequencies."),
    ("DEMO-EMC-315", "PFMF"): ("CAL_EXPIRED",
                               "Helmholtz coil calibration certificate is not in the file."),
}
# 311's CE is sent back for a real limit breach, which is a WRONG_LIMIT finding on
# the record axis - the reviewer disputes the limit line that was applied.
REJECTIONS[("DEMO-EMC-311", "CE")] = (
    "WRONG_LIMIT", "Class B limit applied; this EUT is Class A. Recheck against "
                   "the correct limit line and resubmit.")

# CE quasi-peak grids, as the form posts them: meas_<side><i>__c<j>[] with the
# columns in _CE_MEAS_COLS order - frequency, Q-peak, limit, margin. Margin is
# POSITIVE when the reading is over the limit, which is the convention
# metric_delta's newly_compliant flag reads.
CE_FAILING = {
    "line": (["0.150", "0.720", "3.500", "12.000"],
             ["58.2", "64.8", "49.1", "45.3"],
             ["66.0", "56.0", "56.0", "60.0"],
             ["-7.8", "8.8", "-6.9", "-14.7"]),
    "neutral": (["0.150", "0.720", "3.500", "12.000"],
                ["57.4", "62.1", "48.6", "44.9"],
                ["66.0", "56.0", "56.0", "60.0"],
                ["-8.6", "6.1", "-7.4", "-15.1"]),
}
# The SAME frequencies, measured again after the choke went on. 0.72 MHz comes
# down 13.6 dB on the line side and stops breaching - so metric_delta returns a
# real improvement_db and newly_compliant=1 for the first time.
CE_FIXED = {
    "line": (["0.150", "0.720", "3.500", "12.000"],
             ["56.9", "51.2", "48.4", "45.1"],
             ["66.0", "56.0", "56.0", "60.0"],
             ["-9.1", "-4.8", "-7.6", "-14.9"]),
    "neutral": (["0.150", "0.720", "3.500", "12.000"],
                ["56.2", "50.4", "48.1", "44.7"],
                ["66.0", "56.0", "56.0", "60.0"],
                ["-9.8", "-5.6", "-7.9", "-15.3"]),
}
# 316 is a later campaign of the same product, measured lower again.
CE_LATER = {
    "line": (["0.150", "0.720", "3.500", "12.000"],
             ["54.1", "47.8", "46.9", "43.2"],
             ["66.0", "56.0", "56.0", "60.0"],
             ["-11.9", "-8.2", "-9.1", "-16.8"]),
    "neutral": (["0.150", "0.720", "3.500", "12.000"],
                ["53.6", "47.1", "46.4", "42.8"],
                ["66.0", "56.0", "56.0", "60.0"],
                ["-12.4", "-8.9", "-9.6", "-17.2"]),
}


def ce_form(db, ident, today):
    """A CE form copied from a real one on this database, then re-identified.

    CE has no schemas/CE.json - its page is hand-built HTML - so build_form
    cannot construct one. tools_lifecycle_probe solved this the same way and for
    the same reason: a real submitted form has the correct shape by construction,
    and a hand-written CE form of 147 fields would be wrong in ways that project
    to nothing without erroring.
    """
    from sqlalchemy import text
    raw = db.session.execute(text(
        "SELECT form_json FROM datasheet_records WHERE test_code='CE' "
        "AND form_json IS NOT NULL ORDER BY id LIMIT 1")).scalar()
    if not raw:
        return None
    import json as _json
    form = _json.loads(raw)
    form.update(ident)
    form["test_date"] = today
    form.pop("assignment_id", None)
    form.pop("peer_reviewer_id", None)
    failed = bool(ident.get("failure_reason_code"))
    form["overall_result"] = "FAIL" if failed else "PASS"
    if not failed:
        form.pop("failure_reason_code", None)
    return form


def apply_ce_grid(form, grid):
    """Put a quasi-peak grid into the form the way the CE page posts it."""
    form["meas_index[]"] = ["1"]
    for side, cols in grid.items():
        for j, values in enumerate(cols):
            form["meas_%s1__c%d[]" % (side, j)] = list(values)
        # the average-side columns exist in the grid; leave them empty rather
        # than inventing averages nobody measured
        for j in range(len(cols), 8):
            form["meas_%s1__c%d[]" % (side, j)] = ["" for _ in cols[0]]
    return form


def thin_the_esd_grid(form):
    """Blank the back half of the indirect-discharge grid.

    So one submission is genuinely incomplete AT THE TIME A REVIEWER SEES IT,
    which is the state docs/form_json_analysis.md A9 looks for and which only
    DEMO-EMC-304 has had until now.
    """
    for r in range(5, 9):
        for c in range(1, 7):
            form["ind_r%d_c%d" % (r, c)] = ""
    return form


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    import app as app_module
    from sqlalchemy import text
    from models import db, User, PlannerEntry
    import datasheet_gen.projection as PJ
    import datasheet_gen.records as R
    from datasheet_gen.registry import load_schema
    # the 30x seeder already solved building a correct form from a schema; reuse
    # it rather than growing a second copy that drifts
    from tools_seed_demo_requests import build_form, build_request, clean

    a = app_module.create_app("default")
    with a.app_context():
        if "--clean" in sys.argv:
            for spec in PLAN:
                print("removed %s -> %s" % (spec[0], clean(db, spec[0])))
            return 0

        engineers = db.session.query(User).filter_by(
            role="lab_engineer", is_active=True).order_by(User.id).all()
        reviewers = db.session.query(User).filter_by(
            role="admin", is_active=True).order_by(User.id).all()
        if len(engineers) < 3 or len(reviewers) < 2:
            print("need at least 3 active lab engineers and 2 admins")
            return 1

        today = date.today().strftime("%d/%m/%Y")
        made = rejected = reworked = 0

        for spec in PLAN:
            tco, job, product, model, serial, off, tests = spec
            clean(db, tco)
            engineer = engineers[off % len(engineers)]
            reviewer = reviewers[off % len(reviewers)]
            rid, entries = build_request(db, spec, engineer)
            print("\n=== %s  request=%s  %s" % (tco, rid, product))
            print("    engineer=%s  reviewer=%s  %d test(s)"
                  % (engineer.username, reviewer.username, len(tests)))

            for code, _req_code, life, fail_code in tests:
                ident = {"tco_id": tco, "job_number": job, "eut_name": product,
                         "eut_model": model, "eut_model_sku_number": model,
                         "eut_serial": serial, "eut_serial_number": serial,
                         "test_date": today, "tested_by": engineer.username,
                         "tested_by_name": engineer.username}
                if fail_code:
                    ident["failure_reason_code"] = fail_code
                if code == "CE":
                    form = ce_form(db, ident, today)
                    if form is None:
                        print("    CE skipped: no existing CE form to copy from")
                        continue
                else:
                    form = build_form(code, load_schema(code), ident, today)

                # real quasi-peak numbers for CE, so the measurement questions
                # have something to measure
                if code == "CE":
                    if tco == "DEMO-EMC-316":
                        apply_ce_grid(form, CE_LATER)
                    elif fail_code:
                        apply_ce_grid(form, CE_FAILING)
                    else:
                        apply_ce_grid(form, CE_FIXED)
                # and one genuinely half-filled grid at submission
                if (tco, code) == ("DEMO-EMC-312", "ESD"):
                    thin_the_esd_grid(form)

                entry_id = entries[code]
                assignment = db.session.get(PlannerEntry, entry_id)
                R.upsert_record(assignment, code, form, {}, R.DRAFT,
                                user=engineer, full_projection=True)
                db.session.commit()
                made += 1

                if life == "draft_only":
                    print("    %-15s draft only" % code)
                    continue

                PJ.record_transition(entry_id, "Peer Review", actor=engineer,
                                     comment="Submitted for peer review.",
                                     snapshot=True, submitted=True,
                                     from_status="Draft")

                if life in ("reject_only", "reject_then_approve"):
                    rc, note = REJECTIONS.get((tco, code),
                                              ("INCOMPLETE_OBS",
                                               "Observation grid is incomplete."))
                    PJ.record_transition(entry_id, "Rejected", actor=reviewer,
                                         decided=True, from_status="Peer Review",
                                         comment=note, reason_code=rc)
                    rejected += 1

                if life == "reject_then_approve":
                    # the engineer's correction, as a NEW revision
                    if code == "CE":
                        apply_ce_grid(form, CE_FIXED)
                        form["deviation"] = ("Class A limit line applied and the "
                                             "conducted emission remeasured after "
                                             "fitting a common-mode choke.")
                    elif code == "ESD":
                        for r in range(5, 9):
                            for c in range(1, 7):
                                form["ind_r%d_c%d" % (r, c)] = "A"
                        form["deviation"] = ("Indirect discharge grid completed "
                                             "for all eight points.")
                    else:
                        form["deviation"] = ("Reviewer finding addressed and the "
                                             "record resubmitted.")
                    R.upsert_record(assignment, code, form, {}, R.DRAFT,
                                    user=engineer, full_projection=True)
                    db.session.commit()
                    PJ.record_transition(entry_id, "Peer Review", actor=engineer,
                                         comment="Corrected and resubmitted.",
                                         snapshot=True, submitted=True,
                                         from_status="Draft")
                    reworked += 1

                if life in ("approve", "reject_then_approve"):
                    PJ.record_transition(entry_id, "Approved", actor=reviewer,
                                         comment="Checked against the raw data. "
                                                 "Approved.",
                                         decided=True, from_status="Peer Review")
                    db.session.execute(text(
                        "UPDATE planner_entries SET status='datasheet_uploaded' "
                        "WHERE id=:e"), {"e": entry_id})
                    db.session.commit()

                print("    %-15s %s%s" % (code, life,
                                          "  (unit failed: %s)" % fail_code
                                          if fail_code else ""))

        print("\n%d datasheet(s) written, %d rejected in review, %d reworked "
              "and approved" % (made, rejected, reworked))
        print("Regenerate the catalog so the assistant sees the new values:")
        print("  python -m nlp_search.build_catalog")
    return 0


if __name__ == "__main__":
    sys.exit(main())
