# -*- coding: utf-8 -*-
"""Put the GENERATED datasheet's own pages into the report, instead of a copy.

WHY
---
The report template carries its own hand-maintained version of every per-test
section, and mapping.py fills it. Measured against the eleven datasheet
templates, 49 of 92 per-test subsections have drifted apart: SOFTWARE USED is
4x2 in the datasheet and 2x3 in the report, TEST EQUIPMENT USED is 4x5 against
5x5, RESULT is two tables against one, and EFT and SURGE build their observation
grid at generation time so the report's fixed 9x9 cannot match it by
construction. Two templates, one truth. It will drift again.

So the report stops keeping a copy. Each test's section is lifted out of the
datasheet .docx that peer review actually approved, which makes the report equal
to the approved document by construction rather than by diligence.

HOW, AND WHY NOT WORD
---------------------
Production is Linux with Python and nothing else - no Word, no LibreOffice. So
this is pure OOXML via docxcompose (already in requirements.txt), which copies
the XML verbatim: fonts, spacing, table geometry and images arrive exactly as
they were, because they ARE the same XML. Re-rendering through a converter would
reflow precisely the layout this exists to preserve.

Composer.insert does the parts that are genuinely hard, per element:

    skips CT_SectPr .................. the datasheet's page setup cannot
                                       overwrite the report's
    remove_header_and_footer_references  ULR NO / TEST REPORT NO / Page X of Y
                                       survive
    add_referenced_parts, add_images .. image relationships remapped
    add_styles + style id mapping ..... fonts and spacing come across
    add_numberings .................... list numbering
    renumber_bookmarks, docpr_ids ..... id collisions

WHAT THIS MODULE DOES NOT DO YET (phase 2)
------------------------------------------
  * captions -> SEQ fields. The datasheets have ZERO SEQ fields and the report
    has 43, so a spliced figure will not appear in LIST OF FIGURES until its
    caption becomes one.
  * cross-references. "Refer 1.2" in a datasheet is "Refer 2.4" in the report -
    the same section at a different number. Literal text, not REF fields.
"""
import logging
import os

log = logging.getLogger(__name__)

# The top-level heading that opens each test's own section in its datasheet.
# Everything before it - EUT AND TECHNICAL DETAILS, EUT DETAILS, EUT
# MODIFICATION RECORD, FUNCTIONAL CHECK, MONITORING PARAMETERS - restates the
# report's section 2 and is dropped. This is the "only the pages that are
# needed" boundary, and it is a heading match rather than a page count because
# page counts move with the data.
TEST_HEADING = {
    "CE": "CONDUCTED EMISSION",
    "RE": "RADIATED EMISSION",
    "ESD": "ELECTROSTATIC DISCHARGE",
    "EFT": "ELECTRICAL FAST TRANSIENT",
    "SURGE": "SURGE IMMUNITY",
    "CRF": "CONDUCTED RADIO FREQUENCY",
    "RS_RI": "RADIATED SUSCEPTIBILITY",
    "HARMONIC": "HARMONIC CURRENT",
    "VOLTAGEFLICKER": "FLICKER",
    "PFMF": "POWER FREQUENCY MAGNETIC",
    "VOLTAGEDIPS": "VOLTAGE DIPS",
}


def _norm(s):
    return " ".join((s or "").split()).upper()


def _is_heading(p_el):
    """True when this w:p carries a Heading style."""
    from docx.oxml.ns import qn
    ppr = p_el.find(qn("w:pPr"))
    if ppr is None:
        return False
    st = ppr.find(qn("w:pStyle"))
    if st is None:
        return False
    return (st.get(qn("w:val")) or "").lower().startswith("heading")


def _text_of(p_el):
    from docx.oxml.ns import qn
    return "".join(t.text or "" for t in p_el.iter(qn("w:t")))


def region_start(doc, code):
    """Index in body of the w:p that opens ``code``'s own section, or None."""
    needle = TEST_HEADING.get(code)
    if not needle:
        return None
    for i, el in enumerate(doc.element.body):
        if el.tag.endswith("}p") and _is_heading(el) and needle in _norm(_text_of(el)):
            return i
    return None


def extract_region(path, code):
    """The datasheet at ``path``, trimmed to ``code``'s own section.

    Returns a python-docx Document whose body holds only the per-test region -
    ready to hand to Composer.insert. The file on disk is untouched; the trim
    happens on an in-memory copy.
    """
    from docx import Document
    doc = Document(path)
    start = region_start(doc, code)
    if start is None:
        raise ValueError("no %r heading in %s" % (TEST_HEADING.get(code, code),
                                                  os.path.basename(path)))
    body = doc.element.body
    # Drop everything before the test heading. Walk backwards so the indices of
    # the elements still to remove do not shift under us.
    for el in list(body)[:start]:
        body.remove(el)
    return doc


def report_section_span(report_doc, code):
    """(start, end) body indices of ``code``'s section in the REPORT.

    ``end`` is exclusive and stops at the next top-level test heading, so the
    span covers the whole section - its subsections, tables and captions -
    without eating the section that follows.
    """
    needle = TEST_HEADING.get(code)
    if not needle:
        return None
    body = list(report_doc.element.body)
    start = None
    for i, el in enumerate(body):
        if not (el.tag.endswith("}p") and _is_heading(el)):
            continue
        txt = _norm(_text_of(el))
        if start is None:
            if needle in txt:
                start = i
            continue
        # the next TEST section, at the same level, closes this one
        if any(other in txt for c, other in TEST_HEADING.items()
               if c != code and other not in needle and needle not in other):
            return (start, i)
    if start is None:
        return None
    return (start, len(body))


def replace_section(report_path, code, datasheet_path, out_path=None):
    """Swap the report's own ``code`` section for the datasheet's.

    Returns a dict describing what happened, so the caller can log it rather
    than assume it.
    """
    from docx import Document
    from docxcompose.composer import Composer

    report = Document(report_path)
    span = report_section_span(report, code)
    if span is None:
        raise ValueError("no %r section in the report" % code)
    start, end = span

    region = extract_region(datasheet_path, code)
    region_blocks = len(list(region.element.body))

    body = report.element.body
    removed = 0
    for el in list(body)[start:end]:
        body.remove(el)
        removed += 1

    Composer(report).insert(start, region)

    out_path = out_path or report_path
    report.save(out_path)
    return {"code": code, "report_blocks_removed": removed,
            "datasheet_blocks_inserted": region_blocks,
            "inserted_at": start, "path": out_path}
