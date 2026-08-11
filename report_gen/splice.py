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


# --------------------------------------------------------------------------
# Phase 2a: captions have to become fields, or the lists stay empty
# --------------------------------------------------------------------------
# The datasheets carry ZERO SEQ fields - a caption there is the literal text
# "Photo 1: ESD test setup - Indirect discharge (HCP)". The report's three lists
# are TOC \c "Figure" / "Photo" / "Table" fields, and those collect paragraphs
# containing a matching SEQ field, not paragraphs that happen to start with the
# word Photo. So a spliced caption is invisible to LIST OF PHOTOS until its
# number becomes a field.
#
# Both documents already use the Caption paragraph style, which is the half of
# this that was free. What is rebuilt is the runs, in the report's own shape:
#
#     text "Photo "  |  SEQ Photo \* ARABIC  |  ": "  |  the caption text
#
# The run formatting is taken from the caption's OWN first run, not from the
# report, so a spliced caption keeps the font and size it was generated with -
# which is the point of splicing rather than re-rendering.
_CAPTION_RE = None          # built below, see the note on \b


def _caption_re():
    global _CAPTION_RE
    if _CAPTION_RE is None:
        import re
        # Built rather than written as a literal: a \b typed into a heredoc has
        # arrived here as a backspace byte three times in this repo's history.
        b = chr(92) + "b"
        _CAPTION_RE = re.compile(
            r"^\s*(Figure|Photo|Table)%s\s*(\d+)\s*[:.\-]?\s*(.*)$" % b,
            re.IGNORECASE | re.DOTALL)
    return _CAPTION_RE


def _seq_runs(label, number, rest, rpr):
    """The runs for one caption: label, SEQ field, separator, text."""
    import copy
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    def run(text=None, field=None, fld=None):
        r = OxmlElement("w:r")
        if rpr is not None:
            r.append(copy.deepcopy(rpr))
        if fld is not None:
            fc = OxmlElement("w:fldChar")
            fc.set(qn("w:fldCharType"), fld)
            r.append(fc)
        elif field is not None:
            it = OxmlElement("w:instrText")
            it.set(qn("xml:space"), "preserve")
            it.text = field
            r.append(it)
        else:
            t = OxmlElement("w:t")
            t.set(qn("xml:space"), "preserve")
            t.text = text
            r.append(t)
        return r

    out = [run(text="%s " % label),
           run(fld="begin"),
           run(field=" SEQ %s %s* ARABIC " % (label, chr(92))),
           run(fld="separate"),
           run(text=str(number)),          # cached value; Word recomputes it
           run(fld="end")]
    tail = ": %s" % rest if rest else ":"
    out.append(run(text=tail))
    return out


def captions_to_seq(doc):
    """Turn literal caption numbers into SEQ fields. Returns the count changed."""
    from docx.oxml.ns import qn
    from .docx_tools import template_rpr

    changed = 0
    for p in doc.element.body.iter(qn("w:p")):
        ppr = p.find(qn("w:pPr"))
        style = ""
        if ppr is not None:
            st = ppr.find(qn("w:pStyle"))
            if st is not None:
                style = (st.get(qn("w:val")) or "").lower()
        text = "".join(t.text or "" for t in p.iter(qn("w:t")))
        m = _caption_re().match(text)
        if not m or "caption" not in style:
            continue
        # already a field? leave it - re-running must not double-wrap
        if any(True for _ in p.iter(qn("w:instrText"))):
            continue
        label, number, rest = m.group(1), m.group(2), (m.group(3) or "").strip()
        rpr = template_rpr(p)
        for r in list(p.findall(qn("w:r"))):
            p.remove(r)
        for r in _seq_runs(label.title(), number, rest, rpr):
            p.append(r)
        changed += 1
    return changed


# --------------------------------------------------------------------------
# Phase 2b: the same section has a different number in the two documents
# --------------------------------------------------------------------------
# A datasheet points at its own EUT Modification Record, which is its section
# 1.2. In the report that content is section 2.4. The reference is literal text
# in the spec table's label column, not a REF field, so it needs rewriting -
# and Test Mode gains one, because the report cross-references it and the
# datasheet does not.
#
# Exact whole-cell matches only. "Test Mode" as a substring would also hit
# "EUT Modes of Operation" and "Test Mode of Operation".
CROSSREF = {
    "EUT Modification state (Refer 1.2)": "EUT Modification state (Refer 2.4)",
    "EUT Modification state\n(Refer 1.2)": "EUT Modification state\n(Refer 2.4)",
    "Test Mode": "Test Mode (Refer 2.7)",
}


def rewrite_crossrefs(doc):
    """Point the spliced section's cross-references at the REPORT's numbering."""
    from docx.oxml.ns import qn
    changed = 0
    for tc in doc.element.body.iter(qn("w:tc")):
        texts = [t for t in tc.iter(qn("w:t"))]
        whole = "".join(t.text or "" for t in texts).strip()
        want = CROSSREF.get(whole)
        if not want:
            continue
        # collapse into the first run's text and blank the rest, so the cell's
        # own formatting survives
        texts[0].text = want
        for t in texts[1:]:
            t.text = ""
        changed += 1
    return changed


def replace_section_in_doc(report, code, datasheet_path):
    """Swap the report's own ``code`` section for the datasheet's, in place.

    Takes an ALREADY-OPEN report so the builder can splice while it still holds
    the document - reopening from disk mid-build would discard everything filled
    so far. Returns a dict describing what happened, so the caller can log it
    rather than assume it.
    """
    from docxcompose.composer import Composer

    span = report_section_span(report, code)
    if span is None:
        raise ValueError("no %r section in the report" % code)
    start, end = span

    region = extract_region(datasheet_path, code)
    region_blocks = len(list(region.element.body))
    # Fix the region BEFORE it is inserted: it is a standalone document here, so
    # the caption and cross-reference passes cannot reach the rest of the report
    # and change something that was already correct.
    captions = captions_to_seq(region)
    crossrefs = rewrite_crossrefs(region)

    body = report.element.body
    removed = 0
    for el in list(body)[start:end]:
        body.remove(el)
        removed += 1

    Composer(report).insert(start, region)

    return {"code": code, "report_blocks_removed": removed,
            "datasheet_blocks_inserted": region_blocks,
            "captions_to_seq": captions, "crossrefs_rewritten": crossrefs,
            "inserted_at": start,
            "source": os.path.basename(datasheet_path)}


def replace_section(report_path, code, datasheet_path, out_path=None):
    """File-level wrapper around replace_section_in_doc, for testing by hand."""
    from docx import Document
    report = Document(report_path)
    info = replace_section_in_doc(report, code, datasheet_path)
    out_path = out_path or report_path
    report.save(out_path)
    info["path"] = out_path
    return info
