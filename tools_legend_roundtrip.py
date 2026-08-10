# -*- coding: utf-8 -*-
"""Does the observation legend survive a refresh? Strictly checked, per datasheet.

An earlier version of this check searched the whole page for the description
and reported all ten datasheets passing. It was wrong. Every page carries a
shared #obs-legend-seed-all block, so the text was found there even on the two
datasheets whose widget reads a different key entirely and rendered nothing -
EFT and PFMF seed from prefill['eft_obs_legend'] / ['pfmf_obs_legend'].

So this looks only at the seed the widget for that datasheet actually reads,
and fails if the description is not in THAT one.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.getcwd())

import app as app_module                                    # noqa: E402
from datasheet_gen.form_extract import LEGEND_PREFIX         # noqa: E402
from models import PlannerEntry, User                        # noqa: E402

PLANNER_NAME = {"RE": "RE", "ESD": "ESD", "EFT": "EFT", "CRF": "CRF",
                "SURGE": "Surge", "RS_RI": "RS_RI", "HARMONIC": "Harmonic",
                "VOLTAGEFLICKER": "VoltageFlicker", "PFMF": "PFMF",
                "VOLTAGEDIPS": "VoltageDips"}

# Which JSON blob the legend widget on that page reads. EFT and PFMF build
# their own widget in JS from `var seed = [...]`; the rest read a
# <script type="application/json"> seed block.
OWN_WIDGET = ("EFT", "PFMF")


def seeds_in(body, code):
    """Every JSON seed on the page that this datasheet's legend could read."""
    if code in OWN_WIDGET:
        return re.findall(r"var seed = (\[[^\n]*\]);", body)
    return re.findall(r'<script type="application/json" id="[^"]*obs-legend-seed[^"]*">(.*?)</script>',
                      body, re.S)


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

    print("%-16s %-24s %-8s %s" % ("datasheet", "posts as", "reads", "legend back?"))
    print("-" * 74)
    failures = []
    for code in sorted(PLANNER_NAME):
        pid = entries.get(PLANNER_NAME[code])
        if not pid:
            print("%-16s (no planner entry - skipped)" % code)
            continue
        base = LEGEND_PREFIX.get(code, "obs_legend")
        token = "LEGENDCHECK_%s" % code
        client.post("/datasheet/g/%s/save-draft" % code.lower(),
                    data={"assignment_id": str(pid), "tco_id": "IEC-EMC-010",
                          "_full_save": "1",
                          base + "_code[]": ["A"], base + "_desc[]": [token]})
        body = client.get("/datasheet/g/%s/%d/form"
                          % (code.lower(), pid)).get_data(as_text=True)
        found = any(token in s for s in seeds_in(body, code))
        if not found:
            failures.append(code)
        print("%-16s %-24s %-8s %s"
              % (code, base + "_desc[]",
                 "own JS" if code in OWN_WIDGET else "seed tag",
                 "yes" if found else "*** NO ***"))

    print("-" * 74)
    print("%d/%d restore the legend%s"
          % (len(PLANNER_NAME) - len(failures), len(PLANNER_NAME),
             "" if not failures else "  FAILED: " + ", ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
