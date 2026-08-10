# -*- coding: utf-8 -*-
"""For every datasheet: does a filled form come back, and does it reach the tables?

Two questions in one pass, because they fail independently:

  ROUND-TRIP  fill every field the form renders, save through the real
              endpoint, reload the real form, check each value is shown again.
  PROJECTION  after the same save, check the normalised tables got rows -
              datasheet, datasheet_<code>, and the shared child tables.

The field list is taken from the RENDERED PAGE, not from the schema. That
matters: a schema can declare keys the template never emits - HARMONIC has
frequency_range, measurement_time and test_port and renders none of them - and
a browser cannot post what is not on the page. Counting those as lost data
invents failures, which an earlier version of this file did.

Two other things it gets right, having got them wrong first:
  * a <select> is filled with one of ITS OWN options; a sentinel can never be
    selected, so it would always look lost;
  * a date input is given a real date.

    python tools_datasheet_audit.py
"""
import os
import re
import sys

sys.path.insert(0, os.getcwd())

import pymysql                                              # noqa: E402
import app as app_module                                    # noqa: E402
from datasheet_gen.form_extract import LEGEND_PREFIX         # noqa: E402
from datasheet_gen.registry import REGISTRY                  # noqa: E402
from models import PlannerEntry, User                        # noqa: E402

PLANNER_NAME = {"CE": "CE", "RE": "RE", "ESD": "ESD", "EFT": "EFT", "CRF": "CRF",
                "SURGE": "Surge", "RS_RI": "RS_RI", "HARMONIC": "Harmonic",
                "VOLTAGEFLICKER": "VoltageFlicker", "PFMF": "PFMF",
                "VOLTAGEDIPS": "VoltageDips"}

# The observation matrix EFT and SURGE build client-side: kind -> row count.
OBS_MATRIX = {"EFT": {"power": 7, "signal": 3},
              "SURGE": {"ac": 3, "dc": 3, "signal": 3}}
OBS_SEP = {"EFT": ",", "SURGE": "|"}

# Identifiers the form needs intact, plus the one field the page deliberately
# re-derives from the request on every load instead of trusting the draft
# (harmonic_normalize_values rebuilds the mains supply string).
SKIP = {"assignment_id", "tco_id", "_full_save", "csrf_token",
        # Re-derived from the request on every load, by design: the mains
        # supply string and the Test Mode names. A draft value for either is
        # SUPPOSED to be replaced - see harmonic_normalize_values and
        # _re_functional_mode_names.
        "eut_input_voltage_frequency", "test_mode"}

# A name is only real if it could come off a live form. These are picked up
# from <template> blocks and from JavaScript that builds markup by string
# concatenation, and they are not fields at all.
_NOT_A_FIELD = re.compile(r"%%|\+|'|\"|\s")

_FIELD_RE = re.compile(r"<(input|textarea|select)\b([^>]*)>", re.I)
_NAME_RE = re.compile(r'name="([^"]+)"')
_TYPE_RE = re.compile(r'type="([^"]+)"')
DATE_VALUE = "2026-08-10"


def rendered_fields(body):
    """[(name, kind)] for every fillable field the page actually renders."""
    out, seen = [], set()
    for tag, attrs in _FIELD_RE.findall(body):
        m = _NAME_RE.search(attrs)
        if not m:
            continue
        name = m.group(1)
        tag = tag.lower()
        typ = tag if tag != "input" else (
            (_TYPE_RE.search(attrs).group(1).lower()
             if _TYPE_RE.search(attrs) else "text"))
        if name in seen or name in SKIP or _NOT_A_FIELD.search(name):
            continue
        # the legend is filled separately, under whichever name this
        # datasheet reads; letting the generic filler touch it too would set
        # two different values for one field
        if "obs_legend" in name:
            continue
        if typ in ("hidden", "file", "checkbox", "radio", "submit", "button"):
            continue
        seen.add(name)
        out.append((name, typ))
    return out


def select_options(body, name):
    """The real option values of one <select>, so it can be filled validly."""
    m = re.search(r'<select[^>]*name="%s"[^>]*>(.*?)</select>' % re.escape(name),
                  body, re.S)
    if not m:
        return []
    opts = re.findall(r"<option[^>]*?(?:value=\"([^\"]*)\")?[^>]*>([^<]*)</option>",
                      m.group(1))
    return [(v if v else t).strip() for v, t in opts if (v or t).strip()]


def fill(body, code):
    """(payload, expected) for every field the page renders."""
    payload = {"assignment_id": "", "tco_id": "IEC-EMC-010", "_full_save": "1"}
    expect = {}
    for name, typ in rendered_fields(body):
        if typ == "select":
            opts = select_options(body, name)
            if not opts:
                continue
            val = opts[-1]
        elif typ == "date":
            val = DATE_VALUE
        else:
            # Derived from the NAME, not from enumeration order. Order shifts
            # between the empty render and the render after a save, so an
            # index-based sentinel silently checks the wrong field and reports
            # a loss that never happened - it did exactly that for
            # tested_by_name.
            val = "ZQ%s%06X" % (code[:2], abs(hash(name)) % 0xFFFFFF)
        payload.setdefault(name, [] if name.endswith("[]") else val)
        if name.endswith("[]"):
            payload[name] = [val]
        else:
            payload[name] = val
        expect[name] = val

    base = LEGEND_PREFIX.get(code, "obs_legend")
    payload[base + "_code[]"] = ["A"]
    payload[base + "_desc[]"] = ["ZQLEGEND" + code]

    for kind, nrows in OBS_MATRIX.get(code, {}).items():
        payload["%s_obs_%s_cols" % (code.lower(), kind)] = OBS_SEP[code].join(["+1", "-1"])
        for ri in range(nrows):
            payload["%s_obs_%s_row_%d" % (code.lower(), kind, ri)] = "ROW%d" % ri
            payload["%s_obs_%s_%d__c0" % (code.lower(), kind, ri)] = "A"
    return payload, expect


def main():
    app = app_module.create_app("default")
    app.config["WTF_CSRF_ENABLED"] = False
    app.login_manager.session_protection = None
    with app.app_context():
        admin_id = User.query.filter_by(role="admin").first().id
        entries = {e.test_name: e.id for e in
                   PlannerEntry.query.filter_by(test_request_id=10).all()}
        cfg = app.config
        db_params = dict(host=cfg["MYSQL_HOST"], port=cfg["MYSQL_PORT"],
                         user=cfg["MYSQL_USER"], password=cfg["MYSQL_PASSWORD"],
                         database=cfg["MYSQL_DATABASE"])

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin_id)
        sess["_fresh"] = True

    conn = pymysql.connect(**db_params)
    children = ("datasheet_equipment", "datasheet_software",
                "datasheet_modification", "datasheet_observation",
                "datasheet_observation_legend")

    print("ROUND-TRIP  fill every rendered field -> save -> refresh -> shown again?")
    print("PROJECTION  same save -> did the normalised tables get rows?")
    print()
    print("%-15s %-12s %-8s %-7s %s"
          % ("datasheet", "round-trip", "legend", "spec", "child rows"))
    print("-" * 96)
    rt_bad, proj_bad = [], []

    for code in sorted(REGISTRY):
        pid = entries.get(PLANNER_NAME[code])
        if not pid:
            print("%-15s (no planner entry)" % code)
            continue
        form_url = ("/datasheet/ce/%d/form" % pid if code == "CE"
                    else "/datasheet/g/%s/%d/form" % (code.lower(), pid))
        save_url = ("/datasheet/ce/save-draft" if code == "CE"
                    else "/datasheet/g/%s/save-draft" % code.lower())

        body = client.get(form_url).get_data(as_text=True)
        payload, expect = fill(body, code)
        payload["assignment_id"] = str(pid)

        res = client.post(save_url, data=payload, content_type="multipart/form-data")
        if res.status_code != 200:
            print("%-15s SAVE FAILED %s" % (code, res.status_code))
            rt_bad.append(code)
            continue

        back = client.get(form_url).get_data(as_text=True)
        missing = [k for k, v in expect.items() if v not in back]
        if missing:
            rt_bad.append(code)
        has_legend_widget = ("obs_legend" in body) or ("obs-legend" in body)
        legend_ok = (("ZQLEGEND" + code) in back) if has_legend_widget else None

        with conn.cursor() as cur:
            cur.execute("SELECT id FROM `datasheet` WHERE planner_entry_id=%s", (pid,))
            row = cur.fetchone()
            did = row[0] if row else None
            spec, counts = 0, {}
            if did:
                cur.execute("SELECT COUNT(*) FROM `datasheet_%s` WHERE datasheet_id=%%s"
                            % code.lower(), (did,))
                spec = cur.fetchone()[0]
                for t in children:
                    cur.execute("SELECT COUNT(*) FROM `%s` WHERE datasheet_id=%%s" % t, (did,))
                    counts[t.replace("datasheet_", "")[:4]] = cur.fetchone()[0]
        if not did or spec < 1:
            proj_bad.append(code)

        print("%-15s %-12s %-8s %-7s %s"
              % (code, "%d/%d" % (len(expect) - len(missing), len(expect)),
                 ("yes" if legend_ok else "NO") if legend_ok is not None else "n/a",
                 "yes" if spec else "NO",
                 " ".join("%s=%s" % kv for kv in sorted(counts.items()))))
        if missing:
            print("%-15s   lost: %s" % ("", ", ".join(missing[:8])))

    conn.close()
    print("-" * 96)
    print("round-trip failures:", ", ".join(rt_bad) or "none")
    print("projection failures:", ", ".join(proj_bad) or "none")
    return 1 if (rt_bad or proj_bad) else 0


if __name__ == "__main__":
    sys.exit(main())
