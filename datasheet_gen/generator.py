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

# Default image box (max_width_mm, max_height_mm); the image is scaled to fit WITHIN this
# box (aspect preserved). Photos and plots default to 15.92 cm (W) x 9.5 cm (H) = 159.2 x
# 95 mm (width kept <= page text width); the engineer can override per-image in the form.
_IMAGE_BOXES = {
    "func_line": (159.2, 95),      # 1.4 Functional Check plots: two stacked on their own page
    "func_neutral": (159.2, 95),
    "ambient_line": (159.2, 95),   # 1.5 Ambient plots: two stacked on their own page
    "ambient_neutral": (159.2, 95),
    "photo_setup": (159.2, 95),
    "signature": (40, 20),
}
_IMAGE_VARS = tuple(_IMAGE_BOXES)
# 2.5 Measurement-Data plots are per-Test (plot_line_i / plot_neutral_i), injected into
# each measurement_records entry at render time (same 15.92 x 9.5 cm default box).
_PLOT_BOX = (159.2, 95)


def _fit_image(tpl, path, box, exact=False):
    """Return an InlineImage for `path` sized to `box` (mm).

    With exact=True the image is stretched/squeezed to fill exactly box_w x box_h
    (both dimensions set) — this honours a size the user typed in the image editor
    (Word Picture Format -> Size) and matches its "Set image to frame" preview.
    Otherwise it is scaled to fit WITHIN the box, preserving aspect ratio."""
    box_w, box_h = box
    if exact:
        return InlineImage(tpl, path, width=Mm(box_w), height=Mm(box_h))
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


#: CE's 2.1 TEST SPECIFICATION value area is 2 grid columns; 3 equal columns from each
#: gives 6, which divides by 1, 2 and 3 so every section split lands on a boundary and
#: stays on one line. (RE re-grids to 12 for the same reason.)
_CE_VALUE_REGRID = (3, 3)


def _resolve_extra_images(tpl, entries, images, img_boxes, box=None):
    """Turn [{'key','caption'}] into renderable entries, dropping the slots with no
    upload so no caption prints over an empty frame. Captions are numbered by the
    document-order pass after rendering."""
    out = []
    for n, item in enumerate(entries or [], start=1):
        path = images.get(item.get("key"))
        if not (path and os.path.exists(path)):
            continue
        custom = img_boxes.get(item.get("key"))
        item = dict(item)
        item["img"] = _fit_image(tpl, path, custom or box or _PLOT_BOX, exact=bool(custom))
        cap = (item.get("caption") or "").strip()
        item["caption"] = cap or "Figure: additional plot"
        out.append(item)
    return out


def _ce_heading_spacing(doc, pt=6):
    """Put a little air under every section title.

    The template's 'Heading 2' style carries space_after=0, so a title sat flush against
    the text beneath it - most visible under TEST PROCEDURE, where the body text starts
    immediately. Applied to every Heading 2 so the sections stay consistent."""
    from docx.shared import Pt as _Pt
    n = 0
    for p in doc.paragraphs:
        if p.style.name == "Heading 2":
            p.paragraph_format.space_after = _Pt(pt)
            n += 1
    return n


def _ce_split_spec_rows(doc, row_splits):
    """Split the Ambient Temperature / Relative Humidity / Test Date / Tested by rows of
    2.1 TEST SPECIFICATION into the 1-3 sections the engineer chose, one per test day.

    Shares RE's helpers so both datasheets behave identically. Imported here rather than
    at module scope because generic_generator imports from this module."""
    if not row_splits:
        return False
    from .generic_generator import (_re_regrid_value_area, _re_row_by_label,
                                    _re_set_row_sections, _re_fit_row_font,
                                    _sync_row_widths)
    tb = None
    for cand in doc.tables:
        try:
            if (cand.rows[0].cells[0].text or "").strip().lower().startswith("product standard"):
                tb = cand
                break
        except (IndexError, AttributeError):
            continue
    if tb is None:
        return False
    if not _re_regrid_value_area(tb, _CE_VALUE_REGRID):
        return False                       # unexpected geometry -> leave the table alone
    for row in row_splits:
        vals = row.get("values") or []
        if vals:
            _re_set_row_sections(tb, _re_row_by_label(tb, row["needle"]), vals)
    # Cell widths must match the grid or Word re-lays the table out and the label
    # column collapses, pushing the table onto a second page.
    _sync_row_widths(tb)
    # Names are long: shrink JUST the 'Tested by' row if a value can't fit its section.
    _re_fit_row_font(tb, "tested by")
    return True


def render_ce_datasheet(context, output_path, images=None, template_path=TEMPLATE_PATH):
    tpl = DocxTemplate(template_path)
    images = images or {}
    # Per-image cm size overrides collected by build_ce_context ({imgvar: (w_mm, h_mm)}).
    # A user-set size wins over the default _IMAGE_BOXES / _PLOT_BOX box.
    _img_boxes = context.get("_img_boxes") or {}
    for var in _IMAGE_VARS:
        path = images.get(var)
        custom = _img_boxes.get(var)
        box = custom or _IMAGE_BOXES[var]
        context[var] = _fit_image(tpl, path, box, exact=bool(custom)) if (path and os.path.exists(path)) else ""
    # per-Test measurement plots -> InlineImage on each record. Four slots: a Quasi-peak
    # and an Average graph for each of the Line and Neutral conductors.
    for rec in context.get("measurement_records") or []:
        for role in ("plot_line", "plot_line_avg", "plot_neutral", "plot_neutral_avg"):
            key = rec.get(role + "_key")
            path = images.get(key)
            custom = _img_boxes.get(key)
            box = custom or _PLOT_BOX
            rec[role] = _fit_image(tpl, path, box, exact=bool(custom)) if (path and os.path.exists(path)) else ""
        # extra plots added to this Test; a slot with no upload prints nothing at all
        rec["extra_images"] = _resolve_extra_images(tpl, rec.get("extra_images"), images, _img_boxes)
    # extra Test Setup pictures
    context["ce_extra_photos"] = _resolve_extra_images(
        tpl, context.get("ce_extra_photos"), images, _img_boxes)
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
    # Runs LAST: it owns the spec table's column grid, and its per-row font shrink must
    # not be undone by the Arial-11 enforcement above.
    _ce_heading_spacing(tpl.docx)               # space under each section title
    _ce_split_spec_rows(tpl.docx, context.get("ce_row_splits"))
    # Any blank Calibration Due on a populated equipment row prints 'NA' (as RE). Covers
    # rows the engineer cleared on the form as well as equipment with no due date.
    from .generic_generator import (_re_fill_missing_na, _re_renumber_figures,
                                    _re_renumber_photos)
    _re_fill_missing_na(tpl.docx, header_needle="calibration due", value="NA")
    # Close the gaps left by the slots that printed nothing: an Average plot the engineer
    # skipped, or an extra picture slot with no upload, would otherwise burn a number.
    _re_renumber_figures(tpl.docx)
    _re_renumber_photos(tpl.docx)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tpl.save(output_path)
    return output_path
