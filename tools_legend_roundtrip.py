# -*- coding: utf-8 -*-
"""Does the observation legend survive a refresh? Strictly checked, per datasheet.

Two earlier versions of this check passed while the feature was broken, and
both failures were the same mistake: looking for the description ANYWHERE on
the page. Every page carries the shared #obs-legend-seed-all block, so the
text is findable even on a datasheet whose widget never reads it.

  * first miss: EFT and PFMF read prefill['eft_obs_legend'] /
    ['pfmf_obs_legend'], not the bare key;
  * second miss: SURGE read no seed at all - surgeLegendComments was
    initialised to {} and nothing populated it, so every description rendered
    blank while the check said yes.

So restores() now requires the data AND a consumer, and the pairing is
verified: removing the SURGE seed makes this report SURGE failing, and putting
it back makes it pass.
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

# These three build their own legend widget in JS and seed it from their own
# namespaced prefill key; the rest read a shared
# <script type="application/json"> seed block.
OWN_WIDGET = ("EFT", "PFMF", "SURGE")

# The variable each own-widget datasheet keeps its descriptions in. They are not
# named consistently - eftLegendComments, surgeLegendComments, pfmfLegend - so
# match the shape rather than any one name, or the check reports a datasheet
# broken purely for naming its variable differently.
_LEGEND_VAR = re.compile(r"[a-z]+Legend(?:Comments)?")


def restores(body, code, token):
    """Two things must BOTH hold, or the legend cannot come back.

    Not "the token appears somewhere on the page". Every page carries the
    shared #obs-legend-seed-all block, so the text was findable on SURGE while
    every description rendered blank - its own widget never read a seed at all
    (surgeLegendComments was initialised to {} and nothing populated it). That
    false pass is what let the bug ship.

    1. THE DATA is on the page, as this datasheet's own {code, desc} seed.
    2. A CONSUMER exists - code that copies a seed row into the legend store.
       For the three own-widget datasheets that is a `row.desc` read, which is
       only present when that datasheet's own {% if %} block emits it.
    """
    has_data = ('"desc": "%s"' % token) in body or ('"desc":"%s"' % token) in body
    if code in OWN_WIDGET:
        return has_data and ("row.desc" in body)
    seeds = re.findall(
        r'<script type="application/json" id="[^"]*obs-legend-seed[^"]*">(.*?)</script>',
        body, re.S)
    return has_data and any(token in s for s in seeds)


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
        found = restores(body, code, token)
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
