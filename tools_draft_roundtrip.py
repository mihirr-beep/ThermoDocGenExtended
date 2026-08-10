# -*- coding: utf-8 -*-
"""Does a draft survive a refresh? Every field, every datasheet.

Fills each form with a unique sentinel per field, saves the draft through the
real endpoint, reloads the real form route, and reports which sentinels came
back. Anything missing is a field the engineer would see go blank.
"""
import os
import sys

sys.path.insert(0, os.getcwd())

import app as app_module                                    # noqa: E402
from datasheet_gen.registry import load_schema, REGISTRY     # noqa: E402
from models import PlannerEntry, User                        # noqa: E402

PLANNER_NAME = {"CE": "CE", "RE": "RE", "ESD": "ESD", "EFT": "EFT", "CRF": "CRF",
                "SURGE": "Surge", "RS_RI": "RS_RI", "HARMONIC": "Harmonic",
                "VOLTAGEFLICKER": "VoltageFlicker", "PFMF": "PFMF",
                "VOLTAGEDIPS": "VoltageDips"}

# Types whose value is free text we can round-trip. Selects/radios only accept
# their own options, and images need a file, so they are reported separately
# rather than counted as failures.
FREE_TEXT = ("field", "textarea", "text", "number", "date")


def fields_for(code):
    """[(form_key, kind)] for one schema: scalars and every table column."""
    if code == "CE":
        return []                       # no JSON schema; handled by name below
    schema = load_schema(code)
    out = []
    for sec in schema.get("sections", []):
        for it in sec.get("items", []):
            key, typ = it.get("key"), it.get("type")
            if not key:
                continue
            if typ == "table":
                for col in it.get("columns", []):
                    out.append(("%s__%s[]" % (key, col["key"]), "grid"))
            elif typ in FREE_TEXT and not it.get("options"):
                out.append((key, "scalar"))
    return out


def main():
    app = app_module.create_app("default")
    app.config["WTF_CSRF_ENABLED"] = False
    app.login_manager.session_protection = None
    with app.app_context():
        admin_id = User.query.filter_by(role="admin").first().id
        entries = {e.test_name: e.id for e in
                   PlannerEntry.query.filter_by(test_request_id=10).all()}

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
        sess["_fresh"] = True

    print("%-16s %6s %6s %6s   %s" % ("datasheet", "fields", "kept", "LOST", "lost keys"))
    print("-" * 96)
    grand_ok = grand_lost = 0
    details = {}
    for code in sorted(REGISTRY):
        planner = PLANNER_NAME.get(code)
        pid = entries.get(planner)
        if not pid:
            print("%-16s   (no planner entry - skipped)" % code)
            continue
        specs = fields_for(code)
        if not specs:
            print("%-16s   (no JSON schema - CE tested separately)" % code)
            continue

        payload = {"assignment_id": str(pid), "tco_id": "IEC-EMC-010",
                   "_full_save": "1"}
        sentinel = {}
        for i, (key, kind) in enumerate(specs):
            val = "ZQ%s%04d" % (code[:2], i)
            sentinel[key] = val
            payload[key] = [val, val + "B"] if kind == "grid" else val

        save = client.post("/datasheet/g/%s/save-draft" % code.lower(), data=payload)
        if save.status_code != 200 or not (save.get_json() or {}).get("success"):
            print("%-16s   SAVE FAILED %s" % (code, save.status_code))
            continue

        body = client.get("/datasheet/g/%s/%d/form" % (code.lower(), pid)).get_data(as_text=True)
        lost = [k for k, v in sentinel.items() if v not in body]
        kept = len(sentinel) - len(lost)
        grand_ok += kept
        grand_lost += len(lost)
        details[code] = lost
        print("%-16s %6d %6d %6d   %s"
              % (code, len(sentinel), kept, len(lost),
                 ", ".join(lost[:4]) + (" ..." if len(lost) > 4 else "")))

    print("-" * 96)
    print("%-16s %6d %6d %6d" % ("TOTAL", grand_ok + grand_lost, grand_ok, grand_lost))
    print()
    for code, lost in sorted(details.items()):
        if lost:
            print("%s lost %d:" % (code, len(lost)))
            for k in lost:
                print("    ", k)
    return 0


if __name__ == "__main__":
    sys.exit(main())
