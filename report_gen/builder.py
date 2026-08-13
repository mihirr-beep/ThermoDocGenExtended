# -*- coding: utf-8 -*-
"""Build the IEC-FRM-516 EMI EMC Test Report for one request.

Entry point: ``build_report(request_obj, planner_entries, output_path)``.

Strategy (see ``docx_tools`` for the why): open the official blank form, delete
the Heading-1 sections for tests this request does not include, fill everything
else from the request + the approved datasheets, then hand the field
recalculation (table of contents, lists of figures/photos/tables, Figure/Photo/
Table numbers, "Page X of Y") back to Word by setting ``w:updateFields``.

Section 3 (IMMUNITY CRITERIA AND DECISION RULE) is static by specification and
is only touched to tick the request's chosen decision rule.
"""
import logging
import os
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.table import Table
from docx.text.paragraph import Paragraph

log = logging.getLogger(__name__)

from . import docx_tools as T
from . import mapping as M
from . import service as S
from . import registry as REG

# Document image slots, in mm. The template states "Plot Size should be
# 9cm X 16cm" and "Photo Size should be 9cm X 14cm" (height x width).
PLOT_BOX = (160.0, 90.0)
PHOTO_BOX = (140.0, 90.0)
DIAGRAM_BOX = (150.0, 95.0)
SIGNATURE_BOX = (40.0, 20.0)

# Paragraphs of guidance text that must not survive into a finished report.
_SIZE_HINT = re.compile(r"^<+\s*(plot|photo)\s+size\s+should\s+be", re.I)


# ==========================================================================
# document outline
# ==========================================================================

class Outline(object):
    """Index of the report's blocks by heading, rebuilt after any deletion."""

    def __init__(self, doc):
        self.doc = doc
        self.refresh()

    def refresh(self):
        self.blocks = list(T.iter_block_items(self.doc))
        self.h1 = []            # [(index, title)]
        self.h2 = []            # [(index, title, owning h1 index)]
        cur = None
        for i, b in enumerate(self.blocks):
            if not isinstance(b, Paragraph):
                continue
            st = T.style_name(b)
            if st == "Heading 1":
                cur = i
                self.h1.append((i, T.text_of(b)))
            elif st == "Heading 2":
                self.h2.append((i, T.text_of(b), cur))

    def section_span(self, title):
        """(start, end) block indices of a Heading-1 section, end exclusive."""
        canon = REG.canonical(title)
        for n, (idx, t) in enumerate(self.h1):
            if REG.canonical(t) == canon:
                end = self.h1[n + 1][0] if n + 1 < len(self.h1) else len(self.blocks)
                return idx, end
        return None, None

    def sub_span(self, section_title, sub_title):
        """(start, end) block indices of a Heading-2 subsection within a section."""
        s, e = self.section_span(section_title)
        if s is None:
            return None, None
        canon = REG.canonical(sub_title)
        subs = [(i, t) for i, t, owner in self.h2 if s <= i < e]
        for n, (idx, t) in enumerate(subs):
            if REG.canonical(t) == canon:
                end = subs[n + 1][0] if n + 1 < len(subs) else e
                return idx, end
        return None, None

    def sub_blocks(self, section_title, sub_title):
        s, e = self.sub_span(section_title, sub_title)
        return self.blocks[s:e] if s is not None else []

    def tables_in(self, section_title, sub_title=None):
        blocks = (self.sub_blocks(section_title, sub_title) if sub_title
                  else self.blocks[slice(*self.section_span(section_title))])
        return [b for b in blocks if isinstance(b, Table)]

    def paragraphs_in(self, section_title, sub_title=None):
        blocks = (self.sub_blocks(section_title, sub_title) if sub_title
                  else self.blocks[slice(*self.section_span(section_title))])
        return [b for b in blocks if isinstance(b, Paragraph)]


# ==========================================================================
# helpers
# ==========================================================================

def _find_row(table, *needles):
    """The row whose label contains all/any of ``needles`` (normalised)."""
    wanted = [M.norm_label(n) for n in needles]
    for row in table.rows:
        lab = M.norm_label(T.row_label(row))
        if any(w and w in lab for w in wanted):
            return row
    return None


def _set_row_value(table, label, value, bold=None):
    """Write ``value`` into the value cell of the row labelled ``label``."""
    row = _find_row(table, label)
    if row is None:
        return False
    cells = T.distinct_cells(row)
    if len(cells) < 2:
        return False
    if T.has_checkboxes(cells[-1]):
        T.tick_checkboxes(cells[-1], value)
    else:
        T.set_cell_text(cells[-1], value, bold=bold)
    return True


def _first_body_paragraph(blocks, skip_hints=True):
    """The first real content paragraph of a subsection (heading/blanks skipped)."""
    for b in blocks[1:]:
        if not isinstance(b, Paragraph):
            continue
        txt = T.text_of(b)
        if not txt:
            continue
        if skip_hints and _SIZE_HINT.match(txt):
            continue
        return b
    return None


def _strip_size_hints(blocks):
    for b in blocks:
        if isinstance(b, Paragraph) and _SIZE_HINT.match(T.text_of(b)):
            T.remove(b)


_CAPTION_KIND = re.compile(r"^\s*(Figure|Photo|Table)\b", re.I)


def _caption_paragraphs(blocks, kinds=("figure", "photo", "table")):
    """Caption paragraphs of a subsection, in order.

    ``kinds`` filters by caption type - images must only ever be attached to a
    Figure/Photo caption, never to a "Table N:" caption.
    """
    out = []
    for b in blocks:
        if not isinstance(b, Paragraph) or T.style_name(b) != "Caption":
            continue
        txt = T.text_of(b)
        if not txt:
            continue
        m = _CAPTION_KIND.match(txt)
        if m is None:
            if "any" in kinds:
                out.append(b)
            continue
        if m.group(1).lower() in kinds:
            out.append(b)
    return out


IMAGE_CAPTIONS = ("figure", "photo")


# ==========================================================================
# 0. cover page
# ==========================================================================

def fill_cover(outline, meta):
    """The cover table + the Prepared/Reviewed/Authorized signature block."""
    tables = [b for b in outline.blocks[:6] if isinstance(b, Table)]
    if not tables:
        return
    cover = tables[0]
    for label, value in (
        ("MANUFACTURER", meta["manufacturer"]),
        ("ADDRESS", meta["manufacturer_address"]),
        ("EUT NAME", meta["eut_name"]),
        ("EUT MODEL", meta["eut_model"]),
        ("EUT SERIAL", meta["eut_serial"]),
        ("CONDITION OF EUT ON RECEIPT", meta["sample_condition"]),
        ("DATE OF RECEIPT OF EUT", meta["sample_received"]),
        ("TEST REPORT ISSUE DATE", meta["issue_date"]),
    ):
        if value:
            _set_row_value(cover, label, value)

    row = _find_row(cover, "DATES ON WHICH TESTS WERE PERFORMED")
    if row is not None and meta["tests_from"]:
        cells = T.distinct_cells(row)
        span = ("From %s to %s" % (meta["tests_from"], meta["tests_to"])
                if meta["tests_to"] and meta["tests_to"] != meta["tests_from"]
                else "On %s" % meta["tests_from"])
        T.set_cell_text(cells[-1], span)

    row = _find_row(cover, "ISSUED TO")
    if row is not None:
        who = [x for x in (meta["requester_name"], meta["requester_email"],
                           meta["requester_contact"]) if x]
        if who:
            T.set_cell_text(T.distinct_cells(row)[-1], "\n".join(who))

    # Signature block: only the Name row is derivable - who submitted the
    # datasheets, who peer reviewed them, who manages the laboratory.
    #
    # The Date row is deliberately NOT filled here any more. It used to receive
    # the report's issue date in all three cells, which put a date against a
    # signature nobody had given: a reader sees "Reviewed By / signature blank /
    # dated 13 Aug" and the document asserts an approval that has not happened.
    # The date somebody signs is theirs to state, so the wizard asks for it and
    # draft_fill writes it. Unsigned and undated is the honest resting state.
    if len(tables) > 1:
        sign = tables[1]
        row = _find_row(sign, "Name")
        if row is not None:
            cells = T.distinct_cells(row)
            for i, val in enumerate(["", meta["prepared_by"], meta["reviewed_by"],
                                     meta["lab_manager_name"]]):
                if i and val and i < len(cells):
                    T.set_cell_text(cells[i], val)


def fill_header(doc, meta):
    """Fill the running header's TEST REPORT NO from the request's job number."""
    for section in doc.sections:
        for hf in (section.header, section.first_page_header):
            if hf is None:
                continue
            for tbl in hf.tables:
                for row in tbl.rows:
                    for cell in T.distinct_cells(row):
                        txt = T.full_text(cell)
                        if "TEST REPORT NO" in txt.upper() and meta["job_number"]:
                            T.set_cell_text(
                                cell, "TEST REPORT NO: %s" % meta["job_number"])


# ==========================================================================
# 1. TEST REPORT SUMMARY
# ==========================================================================

def fill_summary(outline, data):
    """1.1 Test Method, 1.2 Applicable Standards, 1.4 Measurement Uncertainty."""
    meta, tests = data["meta"], data["tests"]
    codes = {t["code"] for t in tests}

    # ---- 1.1 TEST METHOD: keep only the rows for tests in this request ----
    tabs = outline.tables_in("TEST REPORT SUMMARY", "TEST METHOD")
    if tabs:
        tb = tabs[0]
        by_code = {t["code"]: t for t in tests}
        for row in list(tb.rows[1:]):
            label = M.norm_label(T.row_label(row))
            code = None
            for text, c in REG.TEST_METHOD_ROWS:
                if M.norm_label(text)[:18] in label or label[:18] in M.norm_label(text):
                    code = c
                    break
            if code is None or code not in codes:
                T.remove(row)
                continue
            t = by_code[code]
            cells = T.distinct_cells(row)
            if len(cells) >= 4:
                spec = _test_method_spec(code, t["form"], meta)
                if spec:
                    T.set_cell_text(cells[1], spec)
                T.set_cell_text(cells[2], REG.TEST_METHOD_PORT.get(code, ""))
                verdict = _verdict(code, t["form"])
                if verdict:
                    T.set_cell_text(cells[3], verdict)

    # ---- 1.2 APPLICABLE STANDARDS ----
    tabs = outline.tables_in("TEST REPORT SUMMARY", "APPLICABLE STANDARDS")
    if tabs:
        _fill_standards(tabs[0], meta, tests)

    # ---- 1.4 MEASUREMENT UNCERTAINTY: only the tests present ----
    tabs = outline.tables_in("TEST REPORT SUMMARY", "MEASUREMENT UNCERTAINITY")
    if tabs:
        tb = tabs[0]
        unc = data["uncertainty"]
        for row in list(tb.rows[1:]):
            label = M.norm_label(T.row_label(row))
            code = None
            for text, c in REG.UNCERTAINTY_ROWS:
                if M.norm_label(text)[:14] in label:
                    code = c
                    break
            if code is None or code not in codes:
                T.remove(row)
                continue
            if unc.get(code):
                T.set_cell_text(T.distinct_cells(row)[-1], unc[code])


def _vdips_criteria(form):
    """Voltage Dips records one criterion per level, not a single value."""
    met = form.get("vdips_met_criteria[]") or []
    met = [str(x).strip() for x in (met if isinstance(met, list) else [met])
           if str(x).strip()]
    seen = []
    for x in met:
        if x not in seen:
            seen.append(x)
    return ", ".join(seen)


def _verdict(code, form):
    """The 1.1 summary's Results cell: PASS/FAIL for emissions, the met
    performance criteria for immunity tests.

    The split is on the TEST KIND, not on whichever key happens to be filled.
    Every immunity datasheet carries both a PASS/FAIL radio and an A/B/C/D
    select, and both get filled, so reading ``result`` first made 1.1 print PASS
    for ESD and EFT while their own RESULT tables a few pages later printed A and
    B. One document, two answers to the same question - and the criterion, which
    is the whole reason an assessor reads an immunity row, never reached the
    summary at all.

    Each branch still falls back to the other key: an immunity record saved
    before the criterion was mandatory prints its PASS rather than an empty cell.
    """
    if code in REG.IMMUNITY_CODES:
        v = (M._val(form, "met_performance_criteria") or _vdips_criteria(form)
             or M._val(form, "overall_result") or M._val(form, "result"))
    else:
        v = (M._val(form, "overall_result") or M._val(form, "result")
             or M._val(form, "met_performance_criteria") or _vdips_criteria(form))
    return v.upper() if v and v.lower() in ("pass", "fail", "incomplete") else v


_STANDARD_RANGE = {"CE": "150 kHz - 30 MHz", "RE": "30 MHz - 1 GHz",
                   "CRF": "150 kHz - 80 MHz", "RS_RI": "80 MHz - 6 GHz"}


def _freq_range(code, form):
    """The test's frequency range for the 1.1 summary.

    A datasheet may record a non-specific value ("As per the standard") because
    the printed option row already covers it; the summary table needs the actual
    numbers, so fall back to the standard range for that test.
    """
    v = M._val(form, "frequency_range")
    if not v or "standard" in v.lower() or "as per" in v.lower():
        return _STANDARD_RANGE.get(code, v)
    return v


def _test_method_spec(code, form, meta):
    """The 'Frequency range / Class / Test level' cell of the 1.1 summary."""
    cls = meta.get("class_type", "")
    if code in ("CE", "RE"):
        return "\n".join(x for x in (cls, _freq_range(code, form)) if x)
    if code in ("HARMONIC",):
        return M._val(form, "classification") or cls
    if code == "VOLTAGEFLICKER":
        return "NA"
    if code == "ESD":
        bits = []
        for key, lbl in (("direct_contact_discharge", "CD"), ("air_discharge", "AD")):
            v = M._val(form, key)
            if v:
                bits.append("%s: %s" % (lbl, v))
        return "\n".join(bits)
    if code == "RS_RI":
        v1, v2 = M.band_values(form, "field_strength_col_1")
        return "\n".join(x for x in ("80MHz-1GHz: %s" % v1 if v1 else "",
                                     "1GHz-6GHz: %s" % v2 if v2 else "") if x)
    if code == "EFT":
        bits = []
        for key, lbl in (("test_voltage_power_line", "Power"),
                         ("test_voltage_signal_line", "Signal")):
            v = M._val(form, key)
            if v:
                bits.append("%s: %s" % (lbl, v))
        return "\n".join(bits)
    if code == "SURGE":
        bits = []
        for key, lbl in (("surge_tv_cm_power", "CM"), ("surge_tv_dm_power", "DM")):
            v = M._val(form, key)
            if v:
                bits.append("%s: %s" % (lbl, v))
        return "\n".join(bits)
    if code == "CRF":
        lvl = M._val(form, "test_level")
        rng = _freq_range(code, form)
        return "%s: %s" % (rng, lvl) if lvl else rng
    if code == "PFMF":
        return M._val(form, "test_level")
    if code == "VOLTAGEDIPS":
        from datasheet_gen.generic_service import VDIPS_LEVELS
        lv = VDIPS_LEVELS.get(M._val(form, "immunity_test_requirement")) or {}
        rows = list(lv.get("dips", [])) + list(lv.get("intr", []))
        return "\n".join("%s%%: %s" % (r["pct"], r["spec"]) for r in rows)
    return ""


def standards_cells(products, basics):
    """1.2's data rows, cell by cell: [[prod, prod, basic, basic], ...].

    The table is four columns - two for product standards, two for basic - so
    each row carries two of each and the table grows to whichever list is longer.
    An odd-length list therefore leaves a REAL EMPTY CELL in the last row, which
    is a thing worth being able to see before the document is built: with four
    product standards and three basic ones this returns two rows and the fourth
    cell of the second is "".

    Public and returning data rather than writing, so the wizard's preview shows
    the cells this function will actually produce instead of keeping a second copy
    of the pairing arithmetic beside it.
    """
    products, basics = list(products or []), list(basics or [])
    rows_needed = max(-(-len(products) // 2), -(-len(basics) // 2), 1)
    out = []
    for i in range(rows_needed):
        out.append([products[2 * i] if 2 * i < len(products) else "",
                    products[2 * i + 1] if 2 * i + 1 < len(products) else "",
                    basics[2 * i] if 2 * i < len(basics) else "",
                    basics[2 * i + 1] if 2 * i + 1 < len(basics) else ""])
    return out


def _fill_standards(table, meta, tests):
    """1.2 APPLICABLE STANDARDS: the request's product standards + the basic
    standards actually used by the tests in this report."""
    products = list(meta.get("product_standards") or [])
    basics, seen = [], set()
    for t in tests:
        v = M._val(t["form"], "basic_standard")
        # a test's basic standard may list several, joined with '&' or ';'
        for part in re.split(r"\s*[&;]\s*", v):
            part = part.strip()
            if part and part.lower() not in seen:
                seen.add(part.lower())
                basics.append(part)
    if not products and not basics:
        return
    rows = standards_cells(products, basics)
    T.ensure_row_count(table, len(rows), template_row_index=-1, first_data_row=1)
    for i, vals in enumerate(rows):
        cells = T.distinct_cells(table.rows[1 + i])
        for ci, v in enumerate(vals):
            if ci < len(cells):
                T.set_cell_text(cells[ci], v)


# ==========================================================================
# 2. EUT INFORMATION
# ==========================================================================

_EUT_DETAIL_ROWS = [
    ("Manufacturer", "manufacturer"),
    ("EUT Name", "eut_name"),
    ("EUT Model", "eut_model"),
    ("EUT Serial", "eut_serial"),
    ("Number of Test Samples", "test_samples"),
    ("Size of the EUT", "dimensions"),
    ("Weight of the EUT", "weight"),
    ("EUT Operating Voltage", "operating_voltage"),
    ("EUT Operating Frequency", "operating_frequency"),
    ("EUT Power Rating", "power_rating"),
    ("Measured EUT Current", "measured_current"),
]


def fill_eut_information(outline, data):
    meta = data["meta"]
    sec = "EUT INFORMATION"

    # ---- 2.1 EUT DETAILS ----
    tabs = outline.tables_in(sec, "EUT DETAILS")
    if tabs:
        tb = tabs[0]
        for label, key in _EUT_DETAIL_ROWS:
            if meta.get(key):
                _set_row_value(tb, label, meta[key])
        row = _find_row(tb, "EUT Category")
        if row is not None and meta.get("categories"):
            T.tick_checkboxes(T.distinct_cells(row)[-1], meta["categories"], multi=True)
        row = _find_row(tb, "Type of Equipment")
        if row is not None and meta.get("product_type"):
            T.tick_checkboxes(T.distinct_cells(row)[-1], meta["product_type"])

    # ---- 2.2 / 2.3 / 2.5 free-text blocks ----
    _fill_text_block(outline, sec, "DESCRIPTION OF EUT", meta.get("description"))
    _fill_text_block(outline, sec, "EUT CONFIGURATION DURING TEST",
                     meta.get("configuration"))
    # ---- 2.3 SOFTWARE AND FIRMWARE DETAILS ----
    # It had no source in the REQUEST, which is what the old comment here meant,
    # and that was mistaken for having no source at all: every datasheet records
    # the software it ran under, so the report can list them per test. Written as
    # a table because the reader's question is "what ran MY test", and a
    # paragraph of comma-separated names does not answer it.
    _fill_software_table(outline, sec, meta.get("software_rows") or [])

    # ---- 2.4 EUT MODIFICATION RECORD ----
    tabs = outline.tables_in(sec, "EUT MODIFICATION RECORD")
    if tabs and meta.get("modifications"):
        T.fill_table_rows(tabs[0], meta["modifications"])

    # ---- 2.6 EUT SETUP DETAILS: block diagram -> Figure 1 ----
    if meta.get("block_diagram"):
        blocks = outline.sub_blocks(sec, "EUT SETUP DETAILS")
        caps = _caption_paragraphs(blocks)
        if caps:
            from io import BytesIO
            T.insert_image_before(caps[0], BytesIO(meta["block_diagram"]),
                                  max_width_mm=DIAGRAM_BOX[0],
                                  max_height_mm=DIAGRAM_BOX[1])

    # ---- 2.7 EUT MODES OF OPERATION ----
    modes = meta.get("modes") or []
    if modes:
        blocks = outline.sub_blocks(sec, "EUT MODES OF OPERATION")
        p = _first_body_paragraph(blocks)
        if p is not None:
            T.set_paragraph_lines(p, modes)
            # the template prints one placeholder line per example mode
            # (<Mode A: ...>, <Mode B: ...>); drop any the request did not fill
            _drop_placeholder_paragraphs(outline.sub_blocks(
                sec, "EUT MODES OF OPERATION"))

    # ---- 2.8 EUT MONITORING PARAMETERS ----
    if meta.get("monitoring"):
        blocks = outline.sub_blocks(sec, "EUT MONITORING PARAMETERS")
        p = _first_body_paragraph(blocks)
        if p is not None:
            T.set_paragraph_lines(p, _lines(meta["monitoring"]))

    # ---- 2.9 ACCESSORIES + CABLES ----
    tabs = outline.tables_in(sec, "ACCESSORIES/INTERFACES AND CABLE DETAILS")
    if len(tabs) >= 1 and meta.get("accessories"):
        T.fill_table_rows(tabs[0], meta["accessories"])
    if len(tabs) >= 2 and meta.get("cables"):
        T.fill_table_rows(tabs[1], meta["cables"])


def _fill_software_table(outline, sec, rows):
    """2.3: replace the placeholder paragraph with a Test / Software / Version table.

    The template gives this subsection one <placeholder> paragraph and no table,
    so the table is created rather than filled. It is inserted BEFORE the
    placeholder and the placeholder is then dropped, which keeps the new table
    inside the subsection - appending after it would land in 2.4 the moment the
    placeholder happened to be the last block.

    Silent no-op when there are no rows: leaving the visible <placeholder> is
    honest about a datasheet that recorded no software, where an empty table
    would look like a finished answer of "none".
    """
    if not rows:
        return 0
    blocks = outline.sub_blocks(sec, "SOFTWARE AND FIRMWARE DETAILS")
    anchor = _first_body_paragraph(blocks)
    if anchor is None:
        return 0
    try:
        T.insert_table_before(anchor, outline.doc,
                              ["Test", "Software / Firmware", "Version"], rows)
    except Exception as exc:  # noqa: BLE001 - a table must not cost the report
        log.warning("2.3 software table not inserted: %s", exc)
        return 0
    _drop_placeholder_paragraphs(outline.sub_blocks(
        sec, "SOFTWARE AND FIRMWARE DETAILS"))
    outline.refresh()          # a new block shifts every index after it
    return len(rows)


def _fill_text_block(outline, section, sub, text):
    if not text:
        return
    p = _first_body_paragraph(outline.sub_blocks(section, sub))
    if p is not None:
        T.set_paragraph_lines(p, _lines(text))
        _drop_placeholder_paragraphs(outline.sub_blocks(section, sub))


_PLACEHOLDER_ONLY = re.compile(r"^<[^<>]{3,120}>$")


def _drop_placeholder_paragraphs(blocks):
    """Remove paragraphs that are nothing but a ``<placeholder>`` prompt.

    Only used where real data has just been written - a subsection with no data
    keeps its prompt so whoever finalises the report can see what is pending.
    """
    for b in blocks:
        if isinstance(b, Paragraph) and _PLACEHOLDER_ONLY.match(T.text_of(b)):
            T.remove(b)


# ==========================================================================
# author-instruction cleanup
# ==========================================================================
# The blank form carries notes addressed to whoever fills it in - "<software or
# firmware details to be updated>", "<Add if required (Based on the product
# requirement)>", "< Screenshot of monitoring software >", the spare <XYZ>
# abbreviation row. They are scaffolding for a hand-typed report and must not
# survive into a generated, customer-facing one.
#
# Genuine report content is NOT touched: the laboratory Note under 1.2, the
# disclaimers, the A/B/C performance criteria, and cross-references such as
# "(Refer 2.7)" all stay.

# Text that marks a paragraph/cell as an instruction to the author.
_INSTRUCTION_PATTERNS = (
    r"to be updated",
    r"^\s*<\s*screenshot\b",
    r"^\s*<\s*add if required",
    r"^\s*<\s*xyz\s*>\s*$",
    r"^\s*<\s*explanation of",
    r"^\s*<\s*software or firmware",
    r"give overview about the monitoring",
    r"^\s*<\s*mode [a-z]\s*:",
    r"if needed use more criteria",
    r"write the observation in case",
    r"^\s*<+\s*(plot|photo)\s+size\s+should\s+be",
)
_INSTRUCTION_RE = re.compile("|".join(_INSTRUCTION_PATTERNS), re.I)

# Inline markers stripped from otherwise-real sentences.
_INLINE_NOISE = (
    (re.compile(r"\s*<\s*remove if not required\s*>", re.I), ""),
    (re.compile(r"\s*<\s*add if required[^>]*>", re.I), ""),
    (re.compile(r"<\s*xxxxx\s*>", re.I), "NA"),
    (re.compile(r"<\s*software version [^>]*>", re.I), ""),
)

# Cover rows the database cannot fill, and what to print instead of the prompt.
_COVER_DEFAULTS = {
    # the tests are run at the permanent Thermo Fisher facility named in ISSUED BY
    "LOCATION OF PERFORMANCE OF TEST": "Permanent",
}

NOT_APPLICABLE = "NA"


def _is_instruction(text):
    text = (text or "").strip()
    if not text:
        return False
    if _INSTRUCTION_RE.search(text):
        return True
    # a paragraph that is nothing but an angle-bracket prompt
    return bool(_PLACEHOLDER_ONLY.match(text))


def cleanup_instructions(outline, meta):
    """Strip the blank form's author instructions from the generated report.

    Returns a list of what was removed/replaced, for the build summary.
    """
    changes = []

    # ---- cover: fill the rows the DB has no source for ----
    tables = [b for b in outline.blocks[:6] if isinstance(b, Table)]
    if tables:
        for label, value in _COVER_DEFAULTS.items():
            row = _find_row(tables[0], label)
            if row is None:
                continue
            cell = T.distinct_cells(row)[-1]
            if _PLACEHOLDER_ONLY.match(T.full_text(cell).strip()):
                T.set_cell_text(cell, value)
                changes.append("cover %s -> %r" % (label.title(), value))

    # ---- abbreviations: drop the spare "<XYZ> / <Add if required>" row ----
    # (identified by its first row, "AC | Alternating Current"; it sits far into
    # the document, after the TOC and the three lists, so scan every table)
    for b in outline.blocks:
        if not isinstance(b, Table) or len(b.rows) < 5:
            continue
        first = [T.full_text(c).strip().lower() for c in T.distinct_cells(b.rows[0])]
        if first[:2] != ["ac", "alternating current"]:
            continue
        for row in list(b.rows):
            texts = [T.full_text(c).strip() for c in T.distinct_cells(row)]
            if any(_is_instruction(t) for t in texts):
                T.remove(row)
                changes.append("abbreviations: removed the spare %r row"
                               % (texts[0][:20] if texts else "?"))
        break

    # ---- 2.8 monitoring: the screenshot table has no source, so it goes ----
    for b in outline.sub_blocks("EUT INFORMATION", "EUT MONITORING PARAMETERS"):
        if isinstance(b, Table):
            texts = [T.full_text(c).strip()
                     for row in b.rows for c in T.distinct_cells(row)]
            if any(_is_instruction(t) for t in texts):
                T.remove(b)
                changes.append("2.8: removed the monitoring-screenshot placeholder table")

    # ---- every remaining instruction paragraph ----
    outline.refresh()
    for b in list(outline.blocks):
        if not isinstance(b, Paragraph):
            continue
        txt = T.text_of(b)
        if not txt:
            continue
        if _is_instruction(txt):
            # a lone prompt under a heading becomes NA so the section is not empty
            prev = _previous_paragraph(outline, b)
            if prev is not None and T.style_name(prev).startswith("Heading"):
                T.set_paragraph_text(b, NOT_APPLICABLE)
                changes.append("%r -> NA" % txt[:46])
            else:
                T.remove(b)
                changes.append("removed %r" % txt[:46])
            continue
        cleaned = txt
        for pattern, repl in _INLINE_NOISE:
            cleaned = pattern.sub(repl, cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        if cleaned != txt:
            if cleaned:
                T.set_paragraph_text(b, cleaned)
                changes.append("trimmed %r" % txt[:46])
            else:
                T.remove(b)
                changes.append("removed %r" % txt[:46])

    # ---- and inside table cells ----
    for b in outline.blocks:
        if not isinstance(b, Table):
            continue
        for row in b.rows:
            for cell in T.distinct_cells(row):
                if T.has_checkboxes(cell):
                    continue
                txt = T.full_text(cell).strip()
                if not txt:
                    continue
                if _is_instruction(txt):
                    T.set_cell_text(cell, NOT_APPLICABLE)
                    changes.append("cell %r -> NA" % txt[:40])
                    continue
                cleaned = txt
                for pattern, repl in _INLINE_NOISE:
                    cleaned = pattern.sub(repl, cleaned)
                if cleaned == txt:
                    continue        # real data - never reformat it
                # only tidy spacing left behind by a removed marker
                cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
                T.set_cell_text(cell, cleaned or NOT_APPLICABLE)
                changes.append("cell trimmed %r" % txt[:40])
    return changes


def _previous_paragraph(outline, block):
    """The Paragraph immediately before ``block`` in document order."""
    prev = block._p.getprevious()
    while prev is not None:
        if prev.tag == T.qn("w:p"):
            p = Paragraph(prev, block._parent)
            if T.text_of(p) or T.style_name(p).startswith("Heading"):
                return p
        prev = prev.getprevious()
    return None


def _lines(text):
    return [l.strip() for l in re.split(r"[\r\n]+", str(text or "")) if l.strip()]


# ==========================================================================
# 3. IMMUNITY CRITERIA AND DECISION RULE  (static except the ticked rule)
# ==========================================================================

_DECISION_RULE_LABELS = {
    "standard_measured": "Standard",
    "standard_uncertainty": "Standard inclusive of measurement uncertainty",
    "customer_spec": "Customer specifications",
    "customer_spec_uncertainty": "Customer specifications inclusive of measurement uncertainty",
    "measured_uncertainty": "Report on the measured results and the uncertainty",
}


def _decision_rule_of(paragraph_norm):
    """Which rule key this 3.2 line IS - the longest label that prefixes it.

    Matching each selected rule against every line ticked too many boxes. The
    label for ``standard_measured`` normalises to "standard", which is a prefix
    of "standardinclusiveofmeasurementuncertainty", so a request that chose one
    decision rule produced a report claiming two. Every request in the database
    chooses exactly that rule, so every report shipped with the extra tick.

    Asking instead "which rule is this LINE" and taking the longest match makes
    the two Standard lines and the two Customer specification lines distinct,
    because the longer label always wins where one is a prefix of the other.
    """
    best, best_len = None, -1
    for key, label in _DECISION_RULE_LABELS.items():
        n = M.norm_label(label)
        if n and paragraph_norm.startswith(n) and len(n) > best_len:
            best, best_len = key, len(n)
    return best


def tick_decision_rules(outline, meta):
    """Tick the decision rule(s) the request selected in 3.2 DECISION RULE."""
    rules = meta.get("decision_rules") or []
    if not rules:
        return
    # A value with no entry in the label map is matched on itself rather than
    # dropped, so a rule added to the request form still ticks its line.
    chosen = set(rules)
    unmapped = {M.norm_label(r) for r in rules if r not in _DECISION_RULE_LABELS}
    ticked = 0
    for p in outline.paragraphs_in("IMMUNITY CRITERIA AND DECISION RULE",
                                   "DECISION RULE"):
        txt = T.text_of(p)
        if not txt or not T.has_checkboxes(p):
            continue
        n = M.norm_label(txt)
        key = _decision_rule_of(n)
        hit = (key in chosen) or any(u and n.startswith(u) for u in unmapped)
        if not hit:
            continue
        slots = T.checkbox_slots(p)
        if slots:
            T.set_checkbox(slots[0][0], True)
        else:
            lit = T.literal_checkbox_slots(p)
            if lit:
                T.set_literal_checkbox(lit[0][0], lit[0][1], True)
        ticked += 1
    if ticked != len(chosen):
        log.info("3.2 decision rules: request selected %s, ticked %d line(s)",
                 sorted(chosen), ticked)


# ==========================================================================
# 4..14 per-test sections
# ==========================================================================

def fill_test_section(outline, test, meta):
    """Fill one test's Heading-1 section from its datasheet form + images."""
    code, form, images = test["code"], test["form"], test["images"]
    section = test["section"]
    stats = {"code": code, "spec": 0, "unresolved": 0, "images": 0, "extra": 0}
    if not form:
        return stats

    ctx = {"code": code, "class_type": meta.get("class_type", ""),
           "product_group": meta.get("product_group", "")}

    # ---- TEST SPECIFICATION ----
    tabs = outline.tables_in(section, REG.SUB_SPEC)
    if tabs:
        filled, unresolved = M.fill_spec_table(tabs[0], code, form, ctx)
        stats["spec"], stats["unresolved"] = filled, unresolved

    # ---- DEVIATION FROM THE STANDARD ----
    dev = M._val(form, "deviation") or "NA"
    p = _first_body_paragraph(outline.sub_blocks(section, REG.SUB_DEVIATION))
    if p is not None:
        T.set_paragraph_lines(p, _lines(dev))

    # ---- TEST PROCEDURE ----
    proc = M._val(form, "test_procedure")
    if proc:
        blocks = outline.sub_blocks(section, REG.SUB_PROCEDURE)
        paras = [b for b in blocks[1:] if isinstance(b, Paragraph) and T.text_of(b)]
        if paras:
            # the template's procedure is several paragraphs; replace the whole
            # run of them with the datasheet's text, keeping the first's format
            for extra in paras[1:]:
                T.remove(extra)
            T.set_paragraph_lines(paras[0], _lines(proc))

    # ---- TEST LIMITS (emission tests) ----
    if code in REG.EMISSION_CODES:
        _fill_limits(outline, section, code, form)

    # ---- MEASUREMENT DATA / TEST OBSERVATION ----
    used_images = set()
    if code in REG.EMISSION_CODES:
        n_img, used_images = _fill_measurement(outline, section, code, form, images)
        stats["images"] += n_img
    else:
        _fill_observation(outline, section, code, form)

    # ---- TEST SETUP PICTURES ----
    n_img, n_extra, used_images = _fill_pictures(
        outline, section, code, form, images, already_used=used_images)
    stats["images"] += n_img
    stats["extra"] += n_extra
    stats["unplaced"] = sorted(k for k in images
                               if k not in used_images
                               and k not in M.NON_FIGURE_KEYS)

    # ---- TEST EQUIPMENT USED ----
    tabs = outline.tables_in(section, REG.SUB_EQUIPMENT)
    if tabs:
        rows = M.equipment_rows(code, form) or _equipment_fallback(code)
        if rows:
            T.fill_table_rows(tabs[0], rows)
        # with nothing recorded, the template's blank grid is left in place so the
        # report shows a form to complete rather than a header-only table

    # ---- SOFTWARE USED ----
    tabs = outline.tables_in(section, REG.SUB_SOFTWARE)
    if tabs:
        rows = M.software_rows(code, form, test["name"])
        if not rows:
            rows = [[test["name"]] + r for r in _software_fallback(code)]
        if rows:
            T.fill_table_rows(tabs[0], rows)

    # ---- RESULT ----
    _fill_result(outline, section, code, form, meta)
    return stats


def _equipment_fallback(code):
    """Equipment Master rows for a test whose datasheet captured none.

    Uses the same selector the datasheet form uses to pre-fill the table, so the
    report shows the lab's registered equipment for that test rather than an
    empty grid.
    """
    try:
        from datasheet_gen.generic_service import _equipment_rows_for
        rows = _equipment_rows_for(code) or []
    except Exception:
        return []
    return [[r.get("c%d" % i, "") for i in range(5)] for r in rows]


def _software_fallback(code):
    """Software Used rows from the Equipment Master (same selector as the form)."""
    try:
        from datasheet_gen.generic_service import _software_rows_for
        rows = _software_rows_for(code) or []
    except Exception:
        return []
    return [[r.get("c0", ""), r.get("c1", "")] for r in rows]


def _fill_limits(outline, section, code, form):
    """TEST LIMITS tables. Values are derived by the datasheet engine and posted
    as hidden rows, so they are read straight out of the form."""
    tabs = outline.tables_in(section, REG.SUB_LIMITS)
    if not tabs:
        return
    if code == "CE":
        # 0.15-0.50 and 0.50-30 MHz x quasi-peak / average
        pairs = [("limit_qp_015_050", "limit_avg_015_050"),
                 ("limit_qp_050_5", "limit_avg_050_5")]
        for ri, (qp, avg) in enumerate(pairs):
            if 2 + ri >= len(tabs[0].rows):
                break
            cells = T.distinct_cells(tabs[0].rows[2 + ri])
            if len(cells) >= 3:
                T.set_cell_text(cells[1], M._val(form, qp))
                T.set_cell_text(cells[2], M._val(form, avg))
        return
    if code == "RE":
        for label, key in (("30 to 230", "f_30_to_230"),
                           ("230 to 1000", "f_230_to_1000")):
            row = _find_row(tabs[0], label)
            if row is not None:
                cells = T.distinct_cells(row)
                T.set_cell_text(cells[-1], M._val(form, key))
        if len(tabs) > 1:
            rows = M.table_rows(form, "test_limits_rows_2", 3) or \
                   M.table_rows(form, "pa_1g_6g_rows", 3)
            if rows:
                T.fill_table_rows(tabs[1], rows)
        return
    if code == "HARMONIC":
        for ti, key in enumerate(("test_limits_rows", "test_limits_rows_2")):
            if ti < len(tabs):
                rows = M.table_rows(form, key, 2)
                if rows:
                    T.fill_table_rows(tabs[ti], rows)
        return
    if code == "VOLTAGEFLICKER":
        rows = M.table_rows(form, "test_limits_rows", 2)
        if rows:
            T.fill_table_rows(tabs[0], rows)


# The CE / RE measurement grids are captioned in the template but the grids
# themselves are not present (they were pasted in by hand), so they are built
# from the datasheet. Headers mirror the datasheet's own column labels.
_CE_MEAS_HEADERS = ["Frequency (MHz)", "Quasi-peak (dBµV)", "Limit (dBµV)",
                    "Margin (dB)", "Frequency (MHz)", "Average (dBµV)",
                    "Limit (dBµV)", "Margin (dB)"]
_CE_MEAS_COLS = ["qp_freq", "qp", "qp_limit", "qp_margin",
                 "avg_freq", "avg", "avg_limit", "avg_margin"]


def _fill_measurement(outline, section, code, form, images):
    """MEASUREMENT DATA: plots (matched by caption) and the measured-value grids.

    Returns (images_inserted, used_image_keys) so TEST SETUP PICTURES does not
    place or append the same plot again.
    """
    blocks = outline.sub_blocks(section, REG.SUB_MEASUREMENT)
    _strip_size_hints(blocks)
    blocks = outline.sub_blocks(section, REG.SUB_MEASUREMENT)
    # MEASUREMENT DATA prints "Figure N:" captions, so only plots may fill them
    inserted, used = _insert_captioned_images(blocks, code, images, PLOT_BOX,
                                             kinds=(M.KIND_PLOT,))

    blocks = outline.sub_blocks(section, REG.SUB_MEASUREMENT)
    tabs = [b for b in blocks if isinstance(b, Table)]

    if code == "HARMONIC" and tabs:
        rows = M.table_rows(form, "harmonic_row", 4) or M.table_rows(form, "harmonic_rows", 4)
        if rows:
            _fill_harmonic_table(tabs[0], rows)
        return inserted, used
    if code == "VOLTAGEFLICKER" and tabs:
        rows = M.table_rows(form, "measurement_data_rows", 3)
        if rows:
            T.fill_table_rows(tabs[0], rows)
        return inserted, used

    # CE / RE: create the missing grids under their "Table N:" captions
    datasets = []
    if code == "CE":
        for prefix in ("line_", "neutral_"):
            datasets.append((_CE_MEAS_HEADERS,
                             M._ce_arrays(form, prefix, _CE_MEAS_COLS)))
    elif code == "RE":
        headers = _re_meas_headers()
        for key in ("re_table1_rows", "re_table2_rows"):
            datasets.append((headers, M.table_rows(form, key, len(headers))))
    _build_caption_tables(outline, section, datasets)
    return inserted, used


def _re_meas_headers():
    """RE measurement grid headers, read from the datasheet schema."""
    try:
        from datasheet_gen.registry import load_schema
        for sec in load_schema("RE").get("sections", []):
            if sec.get("title") != "MEASUREMENT DATA":
                continue
            for it in sec.get("items", []):
                if it.get("type") == "table" and it.get("columns"):
                    return [c.get("label") or c["key"] for c in it["columns"]]
    except Exception:
        pass
    return ["Frequency (MHz)", "Polarization", "EUT Angle (deg)",
            "Antenna Height (cm)", "EMI (dBuV/m)", "Limit (dBuV/m)", "Margin (dB)"]


def _build_caption_tables(outline, section, datasets):
    """Insert a grid above each "Table N:" caption that has no table before it.

    Captions with no data left over keep their caption but get no empty grid, so
    the report never shows a header-only table.
    """
    blocks = outline.sub_blocks(section, REG.SUB_MEASUREMENT)
    pending = [d for d in datasets if d[1]]
    if not pending:
        return
    prev_was_table = False
    for b in blocks:
        if isinstance(b, Table):
            prev_was_table = True
            continue
        if not isinstance(b, Paragraph):
            continue
        txt = T.text_of(b)
        if T.style_name(b) == "Caption" and re.match(r"^\s*Table\s*\d*\s*:", txt):
            if not prev_was_table and pending:
                headers, rows = pending.pop(0)
                T.insert_table_before(b, outline.doc, headers, rows)
            prev_was_table = False
        elif txt:
            prev_was_table = False


def _fill_harmonic_table(table, rows):
    """The harmonic results grid repeats its header mid-table, so rows are placed
    by harmonic order rather than sequentially."""
    by_order = {}
    for r in rows:
        key = re.sub(r"[^0-9]", "", str(r[0]))
        if key:
            by_order[key] = r
    for row in table.rows:
        cells = T.distinct_cells(row)
        if len(cells) < 4:
            continue
        order = re.sub(r"[^0-9]", "", T.cell_text(cells[0]))
        src = by_order.get(order)
        if not src:
            continue
        for ci in range(1, min(4, len(cells))):
            if ci < len(src):
                T.set_cell_text(cells[ci], src[ci])


def _fill_observation(outline, section, code, form):
    """TEST OBSERVATION grids + the A/B/C/D legend beneath them."""
    blocks = outline.sub_blocks(section, REG.SUB_OBSERVATION)
    tabs = [b for b in blocks if isinstance(b, Table)]
    specs = M.observation_tables(code, form)

    if specs and tabs:
        # match each dataset to a table: by the nearest preceding bold caption
        # ("Power Line:", "AC Power Line:") when there is more than one table
        labels = _table_hints(blocks)
        used = set()
        for hint, rows in specs:
            target = None
            if hint:
                for ti, tb in enumerate(tabs):
                    if ti in used:
                        continue
                    if hint.lower() in (labels.get(ti) or "").lower():
                        target = ti
                        break
            if target is None:
                for ti in range(len(tabs)):
                    if ti not in used:
                        target = ti
                        break
            if target is None:
                continue
            used.add(target)
            if rows:
                _fill_obs_table(tabs[target], rows)

    legend = M.observation_legend(code, form)
    if legend:
        _replace_legend(blocks, legend)


def _table_hints(blocks):
    """{table index: nearest preceding non-heading paragraph text}."""
    hints, last = {}, ""
    ti = 0
    for b in blocks:
        if isinstance(b, Paragraph):
            txt = T.text_of(b)
            if txt and T.style_name(b) not in ("Heading 1", "Heading 2", "Caption"):
                last = txt
        elif isinstance(b, Table):
            hints[ti] = last
            ti += 1
    return hints


def _fill_obs_table(table, rows):
    """Write observation rows below the table's header.

    The grids have 1-3 header rows and their top rows are vertically merged, so
    writing into the wrong row would overwrite a header - hence the structural
    header count rather than a fixed offset.
    """
    T.fill_table_rows(table, rows, first_data_row=T.header_row_count(table))


def _replace_legend(blocks, legend):
    """Replace the static A/B/C/D legend lines with the codes actually used."""
    lines = [b for b in blocks
             if isinstance(b, Paragraph)
             and re.match(r"^[A-D]\s*:", T.text_of(b) or "")]
    if not lines:
        return
    text = ["%s: %s" % (c, d) for c, d in legend]
    for extra in lines[1:]:
        T.remove(extra)
    T.set_paragraph_lines(lines[0], text)


def _fill_pictures(outline, section, code, form, images, already_used=()):
    """TEST SETUP PICTURES: one image per caption, growing the section when the
    datasheet captured more than the template prints slots for.

    ``already_used`` are keys consumed earlier in this section (the MEASUREMENT
    DATA plots), so they are neither re-placed nor appended again.
    """
    blocks = outline.sub_blocks(section, REG.SUB_PICTURES)
    _strip_size_hints(blocks)
    blocks = outline.sub_blocks(section, REG.SUB_PICTURES)
    # TEST SETUP PICTURES prints "Photo N:" captions - photographs only
    inserted, used = _insert_captioned_images(blocks, code, images, PHOTO_BOX,
                                             already_used=already_used,
                                             kinds=(M.KIND_PHOTO,))

    # Anything the datasheet captured that no caption claimed. Rather than
    # dropping it (evidence the engineer recorded), append it with its own
    # datasheet caption so the reader knows exactly what it shows - e.g. a
    # functional-check capture keeps the label "Surge Verification 1".
    caps = _caption_paragraphs(blocks, IMAGE_CAPTIONS)
    extras = [k for k in M.ordered_image_keys(code, images) if k not in used]
    grown = 0
    if extras and caps:
        anchor = caps[-1]
        for key in extras:
            label = _extra_label(form, key, code)
            anchor = _append_photo(anchor, images[key], label, PHOTO_BOX)
            used.add(key)
            grown += 1
    return inserted, grown, used


def _extra_label(form, key, code=None):
    """Caption for a photo the report template has no printed slot for.

    Preference order: the caption the engineer typed on the form, then the
    datasheet schema's own label for that slot (e.g. "PFMF test setup - Y axis"),
    and only as a last resort the humanised key - which is what produced the
    useless "Img Photo 2" captions before.
    """
    cap = M._val(form, key + "_caption")
    if cap:
        return cap
    if code:
        for norm, schema_key in (M.image_captions(code) or {}).items():
            if schema_key != key:
                continue
            label = _schema_image_label(code, key)
            if label:
                return label
    return key.replace("_", " ").title()


def _schema_image_label(code, key):
    """The datasheet schema's label for an image slot, minus its Photo/Figure prefix."""
    try:
        from datasheet_gen.registry import load_schema
        schema = load_schema(code)
    except Exception:
        return ""
    for sec in schema.get("sections", []):
        for it in sec.get("items", []):
            candidates = []
            if it.get("type") == "fields":
                candidates = [f for f in it.get("fields", [])
                              if f.get("input") == "image"]
            elif it.get("type") == "image" or (it.get("type") == "field"
                                               and it.get("input") == "image"):
                candidates = [it]
            for f in candidates:
                if f.get("key") == key:
                    return re.sub(r"^\s*(photo|figure)\s*\d*\s*[:.]?\s*", "",
                                  f.get("label") or "", flags=re.I).strip()
    return ""


def _append_photo(anchor, path, label, box, kind="Photo"):
    """Add a picture + a numbered caption after ``anchor``; returns the caption.

    The caption is built as "Photo <SEQ>: label" so Word numbers it in sequence
    with the template's own captions and lists it under LIST OF PHOTOS.
    """
    import copy
    rpr = T.template_rpr(anchor)
    cap_p = copy.deepcopy(anchor._p)
    T.clear_runs(cap_p)
    cap_p.append(T.make_run("%s " % kind, rpr))
    cap_p.append(T.make_seq_field(kind, rpr))
    cap_p.append(T.make_run(": " + label, rpr))
    anchor._p.addnext(cap_p)
    new_cap = Paragraph(cap_p, anchor._parent)
    T.insert_image_before(new_cap, path,
                          max_width_mm=box[0], max_height_mm=box[1])
    return new_cap


def _insert_captioned_images(blocks, code, images, box, already_used=(),
                             kinds=None):
    """Insert each image immediately above the caption that names it.

    Matching is by caption text (the datasheet and the report caption the same
    picture identically once the auto-number prefix is dropped); unmatched
    captions fall back to slot order so a renamed caption still gets its photo.

    Returns (inserted_count, used_keys). The caller MUST use the returned keys
    when working out what is left over - an earlier version recomputed the
    matches from caption text alone, which missed everything the positional
    fallback had consumed and so appended those images a second time.
    """
    caption_map = M.image_captions(code)
    caps = _caption_paragraphs(blocks, IMAGE_CAPTIONS)
    inserted = 0
    used = set(already_used)
    pending = []
    for cap in caps:
        key = caption_map.get(M.caption_key(T.text_of(cap)))
        if key and key in images and key not in used:
            if T.insert_image_before(cap, images[key],
                                     max_width_mm=box[0], max_height_mm=box[1]):
                used.add(key)
                inserted += 1
        else:
            pending.append(cap)
    # positional fallback for captions whose text did not match. Restricted by
    # slot kind so a test-setup photo can never land in a measurement-plot slot.
    if pending:
        leftover = [k for k in M.ordered_image_keys(code, images, kinds=kinds)
                    if k not in used]
        for cap, key in zip(pending, leftover):
            if T.insert_image_before(cap, images[key],
                                     max_width_mm=box[0], max_height_mm=box[1]):
                used.add(key)
                inserted += 1
    return inserted, used


def _fill_result(outline, section, code, form, meta):
    """RESULT: a sentence for the emission tests, a criteria table otherwise."""
    blocks = outline.sub_blocks(section, REG.SUB_RESULT)
    tabs = [b for b in blocks if isinstance(b, Table)]
    if code == "VOLTAGEDIPS" and tabs:
        req, met = M.vdips_result_rows(form)
        tb = tabs[0]
        for row in tb.rows:
            lab = M.norm_label(T.row_label(row))
            vals = req if "required" in lab else (met if "met" in lab else None)
            if not vals:
                continue
            cells = T.distinct_cells(row)[1:]
            for i, cell in enumerate(cells):
                if i < len(vals) and vals[i]:
                    T.set_cell_text(cell, vals[i])
        return
    if tabs:
        required, met = M.result_criteria(form)
        if required:
            _set_row_value(tabs[0], "Required Performance Criteria", required)
        if met:
            _set_row_value(tabs[0], "Met Performance Criteria", met)
        return
    text = M.result_text(code, form, meta.get("class_type", ""))
    if text:
        p = _first_body_paragraph(blocks)
        if p is not None:
            T.set_paragraph_text(p, text)


# ==========================================================================
# top level
# ==========================================================================

def build_report(request_obj, planner_entries, output_path, now=None):
    """Build the report for one request. Returns (path, summary dict)."""
    data = S.collect(request_obj, planner_entries, now=now)
    tests = data["tests"]
    if not tests:
        raise ValueError("This request has no completed tests to report on.")

    doc = Document(REG.TEMPLATE_PATH)
    outline = Outline(doc)

    # 1. drop the sections for tests that are not part of this request
    keep = {t["code"] for t in tests}
    dropped = []
    for title, code in REG.SECTION_TO_CODE:
        if code in keep:
            continue
        start, end = outline.section_span(title)
        if start is None:
            continue
        T.remove_blocks(outline.blocks[start:end])
        dropped.append(code)
        outline.refresh()

    # 2. front matter
    fill_cover(outline, data["meta"])
    fill_header(doc, data["meta"])
    fill_summary(outline, data)
    outline.refresh()
    fill_eut_information(outline, data)
    outline.refresh()
    tick_decision_rules(outline, data["meta"])

    # 2b. everything the admin typed into the wizard, over the top of what the
    # request supplied. This runs AFTER the request-driven fills on purpose: for
    # the five cover rows and the 2.1 details the draft is the later, more
    # specific answer - the issue date, the condition on receipt and the
    # Permanent/Onsite choice exist nowhere else - and for the rest it fills only
    # what is still blank. Without this call the wizard collected every one of
    # its fields into a document that never read them.
    from . import draft_fill as DF
    outline.refresh()
    drafted = DF.apply_draft(outline, doc, getattr(request_obj, "id", None),
                             meta=data["meta"])
    outline.refresh()

    # 3. one section per test
    #
    # Preferred: SPLICE the test's own pages out of the datasheet .docx that peer
    # review approved, replacing the report's hand-maintained copy of the same
    # tables. Measured against the eleven datasheet templates, 49 of 92 per-test
    # subsections had drifted apart - SOFTWARE USED 4x2 against 2x3, RESULT two
    # tables against one - and EFT and SURGE build their observation grid at
    # generation time, so a fixed copy cannot match by construction.
    #
    # Fallback: fill the template section as before. A test approved before this
    # change, or one whose .docx has been cleaned off disk, has no region to
    # splice, and a report that builds with an older-looking section beats one
    # that refuses to build at all. Which path each test took is logged.
    from . import splice as SPL
    per_test, spliced = [], []
    for test in tests:
        outline.refresh()
        path = test.get("datasheet_path")
        if path:
            try:
                info = SPL.replace_section_in_doc(doc, test["code"], path)
                spliced.append(info)
                outline.refresh()
                continue
            except Exception as exc:  # noqa: BLE001
                log.warning("could not splice %s from %s (%s) - filling the "
                            "template section instead", test["code"],
                            os.path.basename(path), exc)
                outline.refresh()
        per_test.append(fill_test_section(outline, test, data["meta"]))

    # 4. finishing passes
    outline.refresh()
    cleaned = cleanup_instructions(outline, data["meta"])
    T.enforce_arial(doc)
    T.add_image_borders(doc)
    cleared = T.clear_field_cache(doc)
    toc_cleared = T.clear_toc_entries(doc)
    # must run before refresh_fields_on_open: with the form's tracked-changes
    # protection left in place, Word would log its own field rebuild as
    # revisions and show a markup margin full of balloons
    revisions = T.remove_revision_markup(doc)
    # The blank form highlights its guidance and reds its example values so a
    # person filling it in knows what to replace. A finished report shipped with
    # 24 of those highlights and PASS in red, because writing into a cell
    # inherits the cell's run formatting. Strip them last, so nothing written
    # after this point can reintroduce them.
    marks = T.strip_authoring_marks(doc)
    T.refresh_fields_on_open(doc)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    doc.save(output_path)

    # Finish the document HERE rather than leaving Word to do it on open.
    #
    # refresh_fields_on_open above asks Word to rebuild the contents page, the
    # lists of figures/photos/tables and "Page X of Y". Asking Word to modify
    # the document on open has a consequence that took a reader's screenshot to
    # see: the modification can be RECORDED. With track changes active the
    # rebuild arrived as revisions - a contents page in red underline and
    # "Field Code Changed" balloons down the margin - on a file that contained
    # no revisions when it left here. Cleaning the file cannot prevent that,
    # because Word creates them afterwards, out of the rebuild we requested.
    #
    # So compute the fields now and drop the flag. Best-effort: on a host
    # without Word this returns False and behaviour is exactly as before -
    # correct, but with the prompts and the risk.
    from . import finalise as FZ
    finalised = FZ.finalise(output_path)

    summary = {
        "path": output_path,
        # {"engine": "word"|"python", "page_numbers": bool, ...} - which host
        # finished the document, and therefore whether the reader will be asked
        # to update anything. Was a bare bool when Word was the only path.
        "finalised": finalised,
        "finalised_in_word": (finalised or {}).get("engine") == "word",
        "tests": [t["code"] for t in tests],
        "tests_without_data": [t["code"] for t in tests if not t["has_data"]],
        "dropped_sections": dropped,
        "skipped": data["skipped"],
        "per_test": per_test,
        "spliced": spliced,
        "spliced_from_datasheet": [i["code"] for i in spliced],
        "fields_cleared": cleared,
        "toc_entries_cleared": toc_cleared,
        "revision_markup_removed": revisions,
        "highlights_removed": marks[0],
        "example_reds_removed": marks[1],
        "images": sum(s["images"] for s in per_test),
        "extra_blocks": sum(s["extra"] for s in per_test),
        "instructions_cleaned": cleaned,
        # what the wizard actually put in the document, and what it could not.
        # "missing" is the honest list: nobody entered it, or the write failed.
        "draft_written": drafted.get("written", []),
        "draft_images": drafted.get("images", []),
        # in the document, written by something other than the draft - the 2.1
        # spec rows and the 2.5/2.8 request columns. Neither written nor missing.
        "draft_satisfied": drafted.get("satisfied", []),
        "draft_missing": drafted.get("missing", []),
        "draft_notes": drafted.get("notes", []),
    }
    return output_path, summary
