"""Render the CE datasheet .docx from a context (+ optional images) using docxtpl.

Images are fitted INSIDE a fixed box (the document's intended size) while keeping
their aspect ratio, so any upload size/shape (2048x2048, 4K, portrait, ...) is sized
to fit and never overflows onto blank pages.
"""
import os

from docxtpl import DocxTemplate, InlineImage
from docx.shared import Mm

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "word_templates", "IEC-FRM-504_CE.docx")

# Per the document: plots 9x16 cm, photo 9x14 cm. Stored as (max_width_mm, max_height_mm);
# the image is scaled to fit WITHIN this box (aspect preserved). Widths kept <= page text width.
_IMAGE_BOXES = {
    "plot_line": (150, 90),
    "plot_neutral": (150, 90),
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
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tpl.save(output_path)
    return output_path
