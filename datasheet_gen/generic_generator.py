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
        # RE plots/photos are landscape spectrum charts sized to the datasheet's
        # 9.3x15.6 cm plot cell / 9.5x15.9 cm photo cell (width x height, mm).
        if "photo" in k:
            return (159, 95)
        return (156, 93)
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
    strip_trailing_blank_paragraphs(tpl.docx)
    _add_image_borders(tpl.docx)                     # thin black border on every image
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tpl.save(output_path)
    return output_path
