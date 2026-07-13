"""Render any test's docxtpl template (by code) with a context + fitted images."""
import os

from docxtpl import DocxTemplate, InlineImage
from docx.shared import Mm

from .generator import strip_trailing_blank_paragraphs, _add_image_borders
from .layout import polish_layout, page_break_before_top_sections, enforce_arial_fonts, enforce_arial_procedure

TPL_DIR = os.path.join(os.path.dirname(__file__), "word_templates")


def _box(key, code=None):
    k = key.lower()
    if "sign" in k:
        return (40, 20)
    if (code or "").upper() == "RE":
        if "photo" in k:
            return (140, 90)
        return (160, 90)
    if "photo" in k:
        return (140, 90)
    return (150, 90)


def _fit(tpl, path, box):
    bw, bh = box
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        if w and h and (w / h) > (bw / bh):
            return InlineImage(tpl, path, width=Mm(bw))
        return InlineImage(tpl, path, height=Mm(bh))
    except Exception:
        return InlineImage(tpl, path, width=Mm(bw))


def _prune_empty_limit_tables(doc):
    """Remove any RE Test-Limit table whose row-loop produced no data (header only)
    together with its 'Maximum permissible...' intro paragraph. Empty tables occur
    when that (family x band) combination doesn't apply — docxtpl leaves just the
    header row, which we then drop so only the applicable limit tables print."""
    from docx.oxml.ns import qn
    for tbl in list(doc.tables):
        hdr = " ".join(c.text for c in tbl.rows[0].cells).lower()
        is_limit = ("quasi-peak limit" in hdr) or ("peak limit" in hdr and "average limit" in hdr)
        if not is_limit or len(tbl.rows) > 1:
            continue
        prev = tbl._tbl.getprevious()
        while prev is not None and prev.tag != qn("w:p"):
            prev = prev.getprevious()
        if prev is not None:
            ptext = "".join(prev.itertext()).lower()
            if "permissible" in ptext or "as per" in ptext:
                prev.getparent().remove(prev)
        tbl._tbl.getparent().remove(tbl._tbl)


def _re_pageprop(p, tag):
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    pPr = p._p.get_or_add_pPr()
    if pPr.find(qn(tag)) is None:
        pPr.insert(0, OxmlElement(tag))


def _re_clearprop(p, tag):
    from docx.oxml.ns import qn
    pPr = p._p.find(qn("w:pPr"))
    if pPr is not None:
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)


_RE_CAPTION_PREFIX = ("FIGURE", "PHOTO", "TABLE")


def strip_manual_page_breaks(doc):
    """Remove manual run-level page breaks (<w:br w:type="page"/>) from the template.
    This prevents double page breaks when heading page-break properties are added."""
    from docx.oxml.ns import qn
    for br in list(doc.element.body.iter(qn("w:br"))):
        if br.get(qn("w:type")) == "page":
            br.getparent().remove(br)


def _re_paginate(doc):
    """Lay the RE datasheet out page-by-page like the intended structure:
      * page break before 1.4 FUNCTIONAL CHECK, 2.2 DEVIATION (so 2.1 TEST
        SPECIFICATION sits alone), 2.5 MEASUREMENT DATA, 2.6 TEST SETUP PICTURES,
        and 2.7 TEST EQUIPMENT USED (so 2.7/2.8/2.9 share the last page);
      * keep every image with its caption so a page break never orphans a label.
    Two 90 mm plots + captions + a heading fit one page naturally."""
    from docx.oxml.ns import qn
    BREAK_HEADINGS = ("FUNCTIONAL CHECK", "DEVIATION FROM THE STANDARD",
                      "MEASUREMENT DATA", "TEST SETUP PICTURES", "TEST EQUIPMENT USED")
    pm = {p._p: p for p in doc.paragraphs}
    tm = {t._tbl: t for t in doc.tables}
    in_meas = False
    for ch in doc.element.body.iterchildren():
        if ch.tag == qn("w:p"):
            p = pm.get(ch)
            if p is None:
                continue
            t = p.text.strip()
            if p.style.name.startswith("Heading"):
                up = t.upper()
                in_meas = ("MEASUREMENT DATA" in up)
                if any(b in up for b in BREAK_HEADINGS):
                    _re_pageprop(p, "w:pageBreakBefore")
                continue
            is_caption = t.upper().startswith(_RE_CAPTION_PREFIX)
            if p._p.findall(".//" + qn("w:drawing")):
                _re_pageprop(p, "w:keepNext")          # image stays with its caption
            elif is_caption:
                # a Figure/Photo/Table caption belongs to the item ABOVE it — it must
                # NOT be glued to whatever follows (polish_layout glues pre-table
                # paras), or the caption+table block gets pushed to the next page.
                _re_clearprop(p, "w:keepNext")
        elif ch.tag == qn("w:tbl"):
            tb = tm.get(ch)
            if tb is not None and in_meas:
                hdr = " ".join(c.text for c in tb.rows[0].cells).lower()
                if "polarization" in hdr and "eut angle" in hdr:
                    # Keep the whole data table together so it moves as a unit
                    # instead of splitting across pages.
                    rows = tb.rows
                    for r in rows[:-1]:
                        for cell in r.cells:
                            for cp in cell.paragraphs:
                                cp.paragraph_format.keep_with_next = True


def _eft_insert_observation(doc, power, signal):
    """Insert the EFT dynamic observation table(s) right after the 'Power Line:' /
    'Signal Line:' heading paragraphs. Columns are variable (built from the selected
    test voltage), so the table is created here rather than templated."""
    def _find(text):
        for p in doc.paragraphs:
            if p.text.strip() == text:
                return p
        return None

    def _build(data):
        if not data or not data.get("cols"):
            return None
        cols, rows = data["cols"], data.get("rows", [])
        t = doc.add_table(rows=1 + len(rows), cols=1 + len(cols))
        try:
            t.style = "Table Grid"
        except Exception:
            pass
        hdr = t.rows[0].cells
        hdr[0].text = "Coupling path / line"
        for j, c in enumerate(cols):
            hdr[1 + j].text = c
        for i, row in enumerate(rows):
            cs = t.rows[1 + i].cells
            cs[0].text = row.get("label", "")
            for j, v in enumerate(row.get("cells", [])):
                if 1 + j < len(cs):
                    cs[1 + j].text = v
        el = t._tbl
        el.getparent().remove(el)          # detach from the end; re-inserted at the marker
        return el

    for text, data in (("Power Line:", power), ("Signal Line:", signal)):
        marker = _find(text)
        if marker is None:
            continue
        el = _build(data)
        if el is not None:
            marker._p.addnext(el)
        else:
            marker._p.getparent().remove(marker._p)   # port not tested -> drop the dangling heading


def render(code, context, img_keys, img_paths, output_path):
    tpl = DocxTemplate(os.path.join(TPL_DIR, f"{code}.docx"))
    for k in img_keys:
        p = img_paths.get(k)
        context[k] = _fit(tpl, p, _box(k, code)) if (p and os.path.exists(p)) else ""
    if code == "RE":
        for group in context.get("measurement_groups") or []:
            for role in ("img_vertical", "img_horizontal"):
                key = group.get(role + "_key")
                p = img_paths.get(key)
                group[role] = _fit(tpl, p, _box(role, code)) if (p and os.path.exists(p)) else ""
    tpl.render(context, autoescape=True)
    
    if code == "RE":
        # Strip manual template breaks after rendering content/applying layouts
        strip_manual_page_breaks(tpl.docx)
        _prune_empty_limit_tables(tpl.docx)   # drop CISPR/FCC limit tables that don't apply
        polish_layout(tpl.docx)
        enforce_arial_fonts(tpl.docx)              # force Arial on all table cell runs (override Calibri)
        enforce_arial_procedure(tpl.docx)          # force Arial on the Test Procedure body text
        page_break_before_top_sections(tpl.docx)   # each top-level (Heading 1) section on a new page
        _re_paginate(tpl.docx)                # runs LAST so it wins over polish_layout's keep-with-next
        strip_trailing_blank_paragraphs(tpl.docx)
    else:
        # For HARMONIC and other generic templates, preserve the manual layout/breaks of the template
        enforce_arial_fonts(tpl.docx)
        enforce_arial_procedure(tpl.docx)          # force Arial on the Test Procedure body text

    if code == "EFT":
        _eft_insert_observation(tpl.docx, context.get("eft_obs_power"), context.get("eft_obs_signal"))

    # Pagination polish for the rebuilt immunity datasheets (no manual breaks in
    # their templates): rows never split across a page, small tables stay whole,
    # long tables repeat their header, and section headings never dangle at a page
    # bottom. Runs after EFT's observation table is inserted so it's covered too.
    if code in ("EFT", "VOLTAGEDIPS", "SURGE"):
        polish_layout(tpl.docx)

    _add_image_borders(tpl.docx)                     # thin black border on every image
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tpl.save(output_path)
    return output_path
