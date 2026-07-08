"""Render any test's docxtpl template (by code) with a context + fitted images."""
import os

from docxtpl import DocxTemplate, InlineImage
from docx.shared import Mm

from .generator import strip_trailing_blank_paragraphs, _add_image_borders
from .layout import polish_layout, page_break_before_top_sections

TPL_DIR = os.path.join(os.path.dirname(__file__), "word_templates")


def _box(key, code=None):
    k = key.lower()
    if "sign" in k:
        return (40, 20)
    if (code or "").upper() == "RE":
        # Sized so TWO images (plus captions + a section/group label) fit on one page.
        if "photo" in k:
            return (140, 80)
        return (160, 80)
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


def _re_paginate(doc):
    """Lay the RE datasheet out page-by-page like the intended structure:
      * page break before 1.4 FUNCTIONAL CHECK, 2.2 DEVIATION (so 2.1 TEST
        SPECIFICATION sits alone), 2.5 MEASUREMENT DATA, 2.6 TEST SETUP PICTURES,
        and 2.7 TEST EQUIPMENT USED (so 2.7/2.8/2.9 share the last page);
      * inside 2.5, a page break before every group after the first, so each
        group's two plots share a page and its table follows;
      * keep every image with its caption so a page break never orphans a label.
    Two 85 mm plots + captions + a heading fit one page, giving 2 images/page."""
    from docx.oxml.ns import qn
    BREAK_HEADINGS = ("FUNCTIONAL CHECK", "DEVIATION FROM THE STANDARD",
                      "MEASUREMENT DATA", "TEST SETUP PICTURES", "TEST EQUIPMENT USED")
    pm = {p._p: p for p in doc.paragraphs}
    tm = {t._tbl: t for t in doc.tables}
    in_meas = False
    after_meas_table = False
    for ch in doc.element.body.iterchildren():
        if ch.tag == qn("w:p"):
            p = pm.get(ch)
            if p is None:
                continue
            t = p.text.strip()
            if p.style.name.startswith("Heading"):
                up = t.upper()
                in_meas = ("MEASUREMENT DATA" in up)
                after_meas_table = False
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
            if in_meas and after_meas_table and t and not is_caption:
                # the paragraph right after a measurement table is that table's own
                # caption; the NEXT non-caption line is the next group's label — break
                # there so each group's plots start a fresh page.
                _re_pageprop(p, "w:pageBreakBefore")
                after_meas_table = False
        elif ch.tag == qn("w:tbl"):
            tb = tm.get(ch)
            if tb is not None and in_meas:
                hdr = " ".join(c.text for c in tb.rows[0].cells).lower()
                if "polarization" in hdr and "eut angle" in hdr:
                    after_meas_table = True
                    # Keep the whole data table together so it moves to a fresh page
                    # as a unit (the two plots fill the previous page) instead of
                    # squeezing its header onto the images page. keep_with_next on
                    # every row but the last chains them; for a table too big for one
                    # page Word relaxes this and splits normally.
                    rows = tb.rows
                    for r in rows[:-1]:
                        for cell in r.cells:
                            for cp in cell.paragraphs:
                                cp.paragraph_format.keep_with_next = True


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
        _prune_empty_limit_tables(tpl.docx)   # drop CISPR/FCC limit tables that don't apply
    polish_layout(tpl.docx)
    page_break_before_top_sections(tpl.docx)   # each top-level (Heading 1) section on a new page
    if code == "RE":
        _re_paginate(tpl.docx)                # runs LAST so it wins over polish_layout's keep-with-next
    strip_trailing_blank_paragraphs(tpl.docx)
    _add_image_borders(tpl.docx)                     # thin black border on every image
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tpl.save(output_path)
    return output_path
