# -*- coding: utf-8 -*-
"""Which engine will finish the Test Report on THIS host, and what that costs.

Run it after deploying (and after scripts/install_report_engine.sh) to confirm the
app itself agrees that the engine is there - not just that the package is installed.
The distinction matters: a venv built without --system-site-packages cannot import
uno even though python3-uno is present, and the app is what has to import it.

    python tools_report_engine_check.py          # human summary, exit 0 / 1
    python tools_report_engine_check.py --json   # machine readable

Exit status is 0 when reports will come out finished (page numbers computed, no
field prompt for the reader) and 1 when they will not - so it can gate a deploy.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def probe():
    from report_gen import finalise, render
    from report_gen.render import _soffice_path
    word_or_lo = bool(finalise.available())
    lo = bool(finalise.libreoffice_available())
    # finalise.available() is true for EITHER engine; separate them so the report
    # names the one that will actually run.
    engine = "word" if (word_or_lo and not lo and os.name == "nt") else (
        "libreoffice" if lo else ("word" if word_or_lo else "python"))
    return {
        "platform": sys.platform,
        "fields_will_be_computed": word_or_lo or lo,
        "engine": engine,
        "libreoffice_usable": lo,
        "soffice_path": _soffice_path() or None,
        "pdf_preview_available": bool(render.available()),
        "REPORT_DISABLE_WORD": os.environ.get("REPORT_DISABLE_WORD") or None,
        "REPORT_DISABLE_LIBREOFFICE": os.environ.get("REPORT_DISABLE_LIBREOFFICE") or None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    info = probe()
    ok = info["fields_will_be_computed"]
    if args.json:
        print(json.dumps(info, indent=2))
        return 0 if ok else 1

    print("Test Report engine on this host")
    print("-" * 58)
    for key in ("platform", "engine", "fields_will_be_computed",
                "libreoffice_usable", "soffice_path", "pdf_preview_available"):
        print("  %-26s %s" % (key, info[key]))
    for key in ("REPORT_DISABLE_WORD", "REPORT_DISABLE_LIBREOFFICE"):
        if info[key]:
            print("  %-26s %s   (set - forcing a lower tier)" % (key, info[key]))
    print("-" * 58)
    if ok:
        print("  OK - reports will come out finished: contents and figure lists")
        print("       carry page numbers and the reader gets no field prompt.")
        if not info["pdf_preview_available"]:
            print("  NOTE - no docx->PDF renderer, so the wizard shows the")
            print("         completeness check instead of a preview.")
    else:
        print("  NOT READY - reports will still BUILD (every test's section is")
        print("       spliced with pure Python) but the contents and figure lists")
        print("       will have no page numbers, and the reader's Word will ask to")
        print("       update the fields.")
        print("")
        print("  Fix on a Debian/Ubuntu server:")
        print("       sudo ./scripts/install_report_engine.sh")
        print("  (installs libreoffice-writer + python3-uno, then verifies both)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
