"""Render the CE datasheet .docx from a context (+ optional images) using docxtpl.

Images are fitted INSIDE a fixed box (the document's intended size) while keeping
their aspect ratio, so any upload size/shape (2048x2048, 4K, portrait, ...) is sized
to fit and never overflows onto blank pages.
"""
import os

from docxtpl import DocxTemplate, InlineImage
from docx.shared import Mm
from docx.oxml.ns import qn

from .layout import (polish_layout, page_break_before_top_sections, ce_finalize_layout,
                     enforce_arial_fonts, enforce_body_arial, enforce_arial_procedure)

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "word_templates", "IEC-FRM-504_CE.docx")


def strip_trailing_blank_paragraphs(doc):
    """Drop empty paragraphs at the very end of the body (keeping the final
    section properties) so an empty trailing page is not produced."""
    body = doc.element.body
    for el in reversed(list(body)):
        tag = el.tag.split("}")[-1]
        if tag == "sectPr":
            continue
        if tag == "p":
            has_text = "".join(t.text or "" for t in el.iter(qn("w:t"))).strip()
            has_img = el.findall(".//" + qn("w:drawing")) or el.findall(".//" + qn("w:pict"))
            if not has_text and not has_img:
                body.remove(el)
                continue
        break

# Per the document: plots 9x16 cm, photo 9x14 cm. Stored as (max_width_mm, max_height_mm);
# the image is scaled to fit WITHIN this box (aspect preserved). Widths kept <= page text width.
_IMAGE_BOXES = {
    "func_line": (150, 90),      # 1.4 Functional Check plots: two stacked on their own page
    "func_neutral": (150, 90),
    "ambient_line": (150, 90),   # 1.5 Ambient plots: two stacked on their own page
    "ambient_neutral": (150, 90),
    "photo_setup": (140, 90),
    "signature": (40, 20),
}
_IMAGE_VARS = tuple(_IMAGE_BOXES)
# 2.5 Measurement-Data plots are per-Test (plot_line_i / plot_neutral_i), injected into
# each measurement_records entry at render time. Sized so each Figure+Table fills its own
# page (reference layout: one figure+table per page).
_PLOT_BOX = (150, 90)


def _fit_image(tpl, path, box):
    """Return an InlineImage scaled to fit within `box` (mm), preserving aspect ratio."""
    box_w, box_h = box
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        if w and h and (w / h) > (box_w / box_h):
            return InlineImage(tpl, path, width=Mm(box_w))   # wide image -> width is binding
        return InlineImage(tpl, path, height=Mm(box_h))      # tall/square -> height is binding
    except Exception:
        return InlineImage(tpl, path, width=Mm(box_w))


_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"


def _add_image_borders(doc, emu=6350, color="000000"):
    """Wrap every inline body image in a thin rectangular border (a box), matching the
    reference datasheet where the plots sit in a bordered box. The header logo lives in
    the header (not the body), so it is left untouched. emu=6350 -> 0.5pt."""
    for spPr in doc.element.body.iter("{%s}spPr" % _PIC_NS):
        for ln in spPr.findall("{%s}ln" % _A_NS):
            spPr.remove(ln)
        ln = spPr.makeelement("{%s}ln" % _A_NS, {"w": str(emu)})
        fill = spPr.makeelement("{%s}solidFill" % _A_NS, {})
        clr = spPr.makeelement("{%s}srgbClr" % _A_NS, {"val": color})
        fill.append(clr)
        ln.append(fill)
        spPr.append(ln)


def render_ce_datasheet(context, output_path, images=None, template_path=TEMPLATE_PATH):
    tpl = DocxTemplate(template_path)
    images = images or {}
    for var in _IMAGE_VARS:
        path = images.get(var)
        context[var] = _fit_image(tpl, path, _IMAGE_BOXES[var]) if (path and os.path.exists(path)) else ""
    # per-Test measurement plots (plot_line_i / plot_neutral_i) -> InlineImage on each record
    for rec in context.get("measurement_records") or []:
        for role in ("plot_line", "plot_neutral"):
            path = images.get(rec.get(role + "_key"))
            rec[role] = _fit_image(tpl, path, _PLOT_BOX) if (path and os.path.exists(path)) else ""
    tpl.render(context, autoescape=True)
    polish_layout(tpl.docx)
    page_break_before_top_sections(tpl.docx)   # each top-level section (2, 3, ...) on a new page
    ce_finalize_layout(tpl.docx)               # CE pagination: measurement blocks, captions, 2.6+2.7 / 2.8+2.9
    strip_trailing_blank_paragraphs(tpl.docx)
    # Uniform Arial 11: table cells + body paragraphs + Normal style (headings keep
    # their heading size). Fixes Calibri leaking into form-rendered body/table runs.
    enforce_arial_fonts(tpl.docx)              # table cells -> Arial 11
    enforce_arial_procedure(tpl.docx)          # Test Procedure body -> Arial
    enforce_body_arial(tpl.docx)               # body paragraphs + Normal style -> Arial 11
    _add_image_borders(tpl.docx)               # box every plot/photo image (reference layout)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tpl.save(output_path)
    return output_path
