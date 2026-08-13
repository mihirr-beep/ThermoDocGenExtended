# -*- coding: utf-8 -*-
"""Does a spliced test section still look like the datasheet it came from?

WHY THIS IS NOT OBVIOUS FROM THE CODE
-------------------------------------
splice.py inserts the datasheet's own XML, so the content is identical by
construction. The FORMATTING is not guaranteed, for two reasons that do not show
up in a diff of the text:

  1. docxcompose maps styles by NAME. If the report and the datasheet both define
     a style called "Table Grid" or "Caption" with different fonts or sizes, the
     inserted content is re-pointed at the REPORT's definition and silently
     changes appearance. Direct run formatting survives; style-based formatting
     may not.
  2. The report's own w:sectPr - page size and margins - governs whatever lands
     inside it. A table sized to the datasheet's text width can end up wider or
     narrower than its column grid intends.

So this measures rather than assumes: fingerprint the region in the datasheet,
splice it into a fresh report, fingerprint the result, and diff position by
position. Fonts, sizes and table geometry, because those are what a reader sees.

    python tools_splice_format_audit.py            # all 11 test codes
    python tools_splice_format_audit.py -k SURGE   # one
    python tools_splice_format_audit.py -v         # every difference, not a summary
"""
import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docx.oxml.ns import qn  # noqa: E402

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _val(el, tag, attr="w:val"):
    if el is None:
        return None
    child = el.find(qn(tag))
    return None if child is None else child.get(qn(attr))


def _run_fp(r):
    """Font, size, weight and colour of one run - what the eye actually reads.

    Every element is a string, never None: these tuples go into sorted(set(...))
    and a None beside a str is a TypeError, not a finding.
    """
    rpr = r.find(qn("w:rPr"))
    fonts = None if rpr is None else rpr.find(qn("w:rFonts"))
    return tuple("" if v is None else str(v) for v in (
        (fonts.get(qn("w:ascii")) if fonts is not None else None),
        _val(rpr, "w:sz"),
        # bold/italic are present-or-absent toggles; w:val="0" turns them off
        ("b" if rpr is not None and rpr.find(qn("w:b")) is not None
         and rpr.find(qn("w:b")).get(qn("w:val")) not in ("0", "false") else ""),
        ("i" if rpr is not None and rpr.find(qn("w:i")) is not None
         and rpr.find(qn("w:i")).get(qn("w:val")) not in ("0", "false") else ""),
        _val(rpr, "w:color"),
        _val(rpr, "w:highlight"),
    ))


def _para_fp(p):
    ppr = p.find(qn("w:pPr"))
    spacing = None if ppr is None else ppr.find(qn("w:spacing"))
    ind = None if ppr is None else ppr.find(qn("w:ind"))
    runs = [_run_fp(r) for r in p.findall(qn("w:r"))]
    text = "".join(t.text or "" for t in p.iter(qn("w:t"))).strip()
    return {
        "kind": "p",
        "style": _val(ppr, "w:pStyle"),
        "align": _val(ppr, "w:jc"),
        "spacing": None if spacing is None else (
            spacing.get(qn("w:before")), spacing.get(qn("w:after")),
            spacing.get(qn("w:line"))),
        "indent": None if ind is None else (
            ind.get(qn("w:left")), ind.get(qn("w:right")),
            ind.get(qn("w:firstLine"))),
        # the SET of run formats, not the sequence: Word splits runs on spell
        # check and revision boundaries, so run counts differ between two copies
        # of identical-looking text and would produce noise, not findings
        "runs": sorted(set(runs)),
        "text": text[:40],
        "drawings": len(p.findall(".//" + qn("w:drawing"))),
    }


def _tbl_fp(t):
    tblpr = t.find(qn("w:tblPr"))
    grid = t.find(qn("w:tblGrid"))
    tblw = None if tblpr is None else tblpr.find(qn("w:tblW"))
    cells = []
    for tr in t.findall(qn("w:tr")):
        for tc in tr.findall(qn("w:tc")):
            tcpr = tc.find(qn("w:tcPr"))
            tcw = None if tcpr is None else tcpr.find(qn("w:tcW"))
            cells.append((
                (tcw.get(qn("w:w")) if tcw is not None else None),
                (tcw.get(qn("w:type")) if tcw is not None else None),
                _val(tcpr, "w:shd", "w:fill"),
                _val(tcpr, "w:vAlign"),
            ))
    return {
        "kind": "tbl",
        "style": _val(tblpr, "w:tblStyle"),
        "width": None if tblw is None else (tblw.get(qn("w:w")), tblw.get(qn("w:type"))),
        # column grid: the geometry a reader perceives as "the table looks wrong"
        "grid": [] if grid is None else [c.get(qn("w:w"))
                                        for c in grid.findall(qn("w:gridCol"))],
        "rows": len(t.findall(qn("w:tr"))),
        "cells": cells,
        "runs": sorted({_run_fp(r) for r in t.iter(qn("w:r"))}),
    }


def fingerprint(body_elements):
    out = []
    for el in body_elements:
        if el.tag == W + "p":
            out.append(_para_fp(el))
        elif el.tag == W + "tbl":
            out.append(_tbl_fp(el))
    return out


def _describe(a, b):
    """The differences between two fingerprints of the same block."""
    diffs = []
    if a["kind"] != b["kind"]:
        return ["block kind %s -> %s" % (a["kind"], b["kind"])]
    for key in ("style", "align", "spacing", "indent", "width", "grid", "rows"):
        if key in a and a.get(key) != b.get(key):
            diffs.append("%s %r -> %r" % (key, a.get(key), b.get(key)))
    if a.get("runs") != b.get("runs"):
        only_src = [r for r in a["runs"] if r not in b["runs"]]
        only_dst = [r for r in b["runs"] if r not in a["runs"]]
        if only_src or only_dst:
            diffs.append("run format: lost %r gained %r"
                         % (only_src[:3], only_dst[:3]))
    if a.get("cells") != b.get("cells"):
        diffs.append("cell widths/shading differ (%d vs %d cells)"
                     % (len(a.get("cells") or []), len(b.get("cells") or [])))
    if a.get("drawings") != b.get("drawings"):
        diffs.append("images %s -> %s" % (a.get("drawings"), b.get("drawings")))
    return diffs


def audit(code, datasheet_path, verbose=False):
    """Fingerprint the region before and after splicing. Returns a result dict."""
    from docx import Document
    import report_gen.splice as SPL
    from report_gen.registry import TEMPLATE_PATH

    # BEFORE: the region as it stands in the datasheet, after the same trim the
    # splice does, so we compare like with like rather than region-vs-whole-file
    src_doc = SPL.extract_region(datasheet_path, code)
    before = fingerprint(list(src_doc.element.body))

    # AFTER: the same region once inserted into a fresh report
    report = Document(TEMPLATE_PATH)
    info = SPL.replace_section_in_doc(report, code, datasheet_path)
    start = info["inserted_at"]
    span = SPL.report_section_span(report, code)
    body = list(report.element.body)
    end = span[1] if span else len(body)
    after = fingerprint(body[start:end])

    # The splice deliberately rewrites captions into SEQ fields and cross
    # references, so a caption paragraph is EXPECTED to differ. Compare the rest.
    pairs, mismatches = 0, []
    n = min(len(before), len(after))
    for i in range(n):
        a, b = before[i], after[i]
        if a["kind"] == "p" and (a.get("style") or "").lower().startswith("caption"):
            continue
        pairs += 1
        d = _describe(a, b)
        if d:
            mismatches.append((i, a.get("text") or a["kind"], d))
    return {
        "code": code,
        "blocks_before": len(before),
        "blocks_after": len(after),
        "compared": pairs,
        "mismatches": mismatches,
        "length_changed": len(before) != len(after),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", "--filter", default="")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    import app as app_module
    from models import db
    from sqlalchemy import text
    import report_gen.splice as SPL

    flask_app = app_module.create_app("default")
    with flask_app.app_context():
        rows = db.session.execute(text("""
            SELECT p.test_name,
                   MAX(CASE WHEN d.generated_file_path IS NOT NULL
                            AND d.generated_file_path<>'' THEN d.generated_file_path END) AS path
            FROM planner_entries p JOIN datasheet_records d ON d.planner_entry_id=p.id
            GROUP BY p.test_name""")).fetchall()
    from datasheet_gen.registry import normalize_code
    todo = []
    for name, path in rows:
        if not path:
            continue
        code = normalize_code(name)
        if code not in SPL.TEST_HEADING:
            continue
        p = os.path.normpath(path)
        if os.path.exists(p) and (args.filter.upper() in code):
            todo.append((code, p))
    todo.sort()

    print("=" * 78)
    print("SPLICE FORMATTING AUDIT - does the report section match the datasheet?")
    print("=" * 78)
    clean, dirty = [], []
    for code, path in todo:
        try:
            r = audit(code, path, args.verbose)
        except Exception as exc:  # noqa: BLE001
            print("\n%-16s ERROR %s: %s" % (code, type(exc).__name__, exc))
            dirty.append(code)
            continue
        flag = "OK" if not r["mismatches"] and not r["length_changed"] else "DIFFERS"
        print("\n%-16s %-8s blocks %d -> %d, compared %d, %d mismatch(es)"
              % (code, flag, r["blocks_before"], r["blocks_after"],
                 r["compared"], len(r["mismatches"])))
        (clean if flag == "OK" else dirty).append(code)
        shown = r["mismatches"] if args.verbose else r["mismatches"][:4]
        for idx, what, diffs in shown:
            print("     block %-3d %-34s" % (idx, str(what)[:34]))
            for d in diffs:
                print("           %s" % d[:150])
        if not args.verbose and len(r["mismatches"]) > 4:
            print("     ... and %d more (use -v)" % (len(r["mismatches"]) - 4))
    print("\n" + "=" * 78)
    print("identical formatting : %d/%d  %s" % (len(clean), len(todo), ", ".join(clean)))
    print("differs              : %d/%d  %s" % (len(dirty), len(todo), ", ".join(dirty)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
