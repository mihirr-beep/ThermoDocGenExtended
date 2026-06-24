"""Render any test's docxtpl template (by code) with a context + fitted images."""
import os

from docxtpl import DocxTemplate, InlineImage
from docx.shared import Mm

TPL_DIR = os.path.join(os.path.dirname(__file__), "word_templates")


def _box(key):
    k = key.lower()
    if "sign" in k:
        return (40, 20)
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


def render(code, context, img_keys, img_paths, output_path):
    tpl = DocxTemplate(os.path.join(TPL_DIR, f"{code}.docx"))
    for k in img_keys:
        p = img_paths.get(k)
        context[k] = _fit(tpl, p, _box(k)) if (p and os.path.exists(p)) else ""
    tpl.render(context, autoescape=True)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    tpl.save(output_path)
    return output_path
