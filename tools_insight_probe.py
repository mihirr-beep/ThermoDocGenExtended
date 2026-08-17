#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Call every insight primitive with real arguments and report what came back.

    python tools_insight_probe.py            # all
    python tools_insight_probe.py timeline   # one

Free and deterministic - no model involved. insights.py is where the arithmetic
behind "why" lives, deliberately in SQL rather than in a prompt, so whether a
primitive works is a fact about the data and the query and can be checked
without spending a token.

A primitive that returns nothing is NOT necessarily broken. Several of these are
cross-campaign by design - metric_delta, config_diff, modifications_before_pass
compare a product's SECOND test against its FIRST - so they are empty until some
product has been tested twice with datasheets both times. That distinction is
what this reports: "no rows because the query found nothing" against "no rows
because the shape the question needs does not exist in the data yet".
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Arguments drawn from the demo corpus. Empty dict = takes no arguments.
CALLS = [
    ("failure_modes", {}, "lab-wide: what units failed for, grouped"),
    ("rejection_modes", {}, "lab-wide: why records were sent back"),
    ("resolved_how", {}, "lab-wide: what was fitted by the time it passed"),
    ("resolve_reason_codes", {}, "which codes are actually in use"),
    ("cohort", {"reason_code": "SURGE_DAMAGE"},
     "other products that failed the same way"),
    ("timeline", {"product": "DEMO Orion Vacuum Pump Controller"},
     "one product across its tests"),
    ("timeline", {"tco": "DEMO-EMC-302"}, "one job"),
    ("failure_detail", {"product": "DEMO Spectra Bench Photometer"},
     "what actually breached, per test"),
    ("modifications_before_pass", {"product": "DEMO Spectra Bench Photometer"},
     "CROSS-CAMPAIGN: what was changed before it passed"),
    ("metric_delta", {"tco_before": "DEMO-EMC-302", "tco_after": "DEMO-EMC-303"},
     "CROSS-CAMPAIGN: did the readings move"),
    ("config_diff", {"tco_before": "DEMO-EMC-302", "tco_after": "DEMO-EMC-303"},
     "CROSS-CAMPAIGN: what changed in the setup"),
    ("common_config", {"tcos": ["DEMO-EMC-301", "DEMO-EMC-302", "DEMO-EMC-303"]},
     "what these jobs had in common"),
]

# A reply is "empty" when the primitive says so in its own words rather than
# raising - they all return prose, not exceptions.
_EMPTY_RE = re.compile(r"no rows|\(none\)|nothing to compare|not found|no matching",
                       re.I)


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    import mysql_config
    from nlp_search import insights

    cfg = mysql_config.config["default"]
    params = {"host": cfg.MYSQL_HOST, "port": int(cfg.MYSQL_PORT),
              "user": cfg.MYSQL_USER, "password": cfg.MYSQL_PASSWORD,
              "database": cfg.MYSQL_DATABASE}

    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    empty, filled, failed = [], [], []

    for name, kwargs, why in CALLS:
        if only and name not in only:
            continue
        label = "%s(%s)" % (name, ", ".join("%s=%r" % kv for kv in kwargs.items()))
        print("=" * 78)
        print("%s\n  -- %s" % (label, why))
        print("=" * 78)
        try:
            out = insights.run(params, name, **kwargs)
        except Exception as exc:  # noqa: BLE001
            print("  RAISED %s: %s" % (type(exc).__name__, exc))
            failed.append(label)
            continue
        text = str(out or "")
        body = [l for l in text.split("\n")
                if l.strip() and not l.strip().startswith(("EVIDENCE", "That is the"))]
        for line in body[:12]:
            print("  " + line.strip()[:150])
        if len(body) > 12:
            print("  ... %d more line(s)" % (len(body) - 12))
        if _EMPTY_RE.search(text) or not body:
            empty.append(label)
            print("\n  -> EMPTY")
        else:
            filled.append(label)
            print("\n  -> returned data")
        print()

    total = len(empty) + len(filled) + len(failed)
    print("=" * 78)
    print("SUMMARY  %d primitive call(s)" % total)
    print("=" * 78)
    print("  returned data  %d" % len(filled))
    for x in filled:
        print("     %s" % x)
    print("  EMPTY          %d" % len(empty))
    for x in empty:
        print("     %s" % x)
    print("  RAISED         %d" % len(failed))
    for x in failed:
        print("     %s" % x)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
