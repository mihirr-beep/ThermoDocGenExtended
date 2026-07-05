"""Render the CE datasheet .docx from a context (+ optional images) using docxtpl.

Images are fitted INSIDE a fixed box (the document's intended size) while keeping
their aspect ratio, so any upload size/shape (2048x2048, 4K, portrait, ...) is sized
to fit and never overflows onto blank pages.
"""
import os

from docxtpl import DocxTemplate, InlineImage
from docx.shared import Mm
from docx.oxml.ns import qn

from .layout import polish_layout, page_break_before_top_sections, ce_finalize_layout

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
    "plot_line": (150, 68),      # height-bound; with blank-collapse this fits procedure + plot + table + captions on one page
    "plot_neutral": (150, 68),
    "photo_setup": (140, 90),
    "signature": (40, 20),
}
_IMAGE_VARS = tuple(_IMAGE_BOXES)


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


def render_ce_datasheet(context, output_path, images=None, template_path=TEMPLATE_PATH):
    tpl = DocxTemplate(template_path)
    images = images or {}
    for var in _IMAGE_VARS:
        path = images.get(var)
        context[var] = _fit_image(tpl, path, _IMAGE_BOXES[var]) if (path and os.path.exists(path)) else ""
    tpl.render(context, autoescape=True)
    polish_layout(tpl.docx)
    page_break_before_top_sections(tpl.docx)   # each top-level section (2, 3, ...) on a new page
    ce_finalize_layout(tpl.docx)               # CE pagination: measurement blocks, captions, 2.6+2.7 / 2.8+2.9
    strip_trailing_blank_paragraphs(tpl.docx)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tpl.save(output_path)
    return output_path
