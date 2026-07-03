"""Post-render layout polish + human-looking checkbox runs for generated datasheets.

Why this exists (all observed on real generated documents):
  * section headings were orphaned at a page bottom while their image landed on
    the next page;
  * the sign-off table split across pages leaving a lone "Date" row on an
    otherwise empty last page;
  * images were left-aligned while their captions are centered;
  * justified paragraphs containing soft line-breaks (textarea "\n") stretched
    words across the whole line;
  * dynamic measurement tables had wildly uneven column widths.

`polish_layout(doc)` fixes those on the rendered python-docx Document, and
`human_checkbox(...)` renders "ticked by a person" checkboxes (a pen-blue tick
drawn over the box) for `{{r ... }}` placeholders.
"""
import re
from xml.sax.saxutils import escape

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

CAPTION_RE = re.compile(r"^\s*(Photo|Figure)\s*\d*\s*:", re.I)


def _is_heading(text):
    """Section-heading heuristic: short, all letters uppercase (e.g.
    '2. SURGE IMMUNITY TEST', 'TEST SETUP PICTURES', '2.9. RESULT')."""
    t = text.strip()
    if not 3 < len(t) < 80:
        return False
    letters = [c for c in t if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


# --------------------------------------------------------------------------
# Human-ticked checkboxes ({{r ... }} placeholders)
# --------------------------------------------------------------------------

class RunsXml:
    """Raw WordprocessingML runs, safe under Jinja autoescape (docxtpl {{r }})."""

    def __init__(self, xml=""):
        self.xml = xml

    def add(self, xml):
        self.xml += xml
        return self

    def __str__(self):
        return self.xml

    def __html__(self):  # keeps Jinja autoescape from escaping the XML
        return self.xml


def _match(value, option):
    """Tolerant match: 'A' == 'Class A' == 'class a'."""
    v = (value or "").strip().lower()
    o = option.strip().lower()
    if not v:
        return False
    return v == o or v in o or o in v or v == o.split()[-1] or o.split()[-1] == v


def _box_run(checked, size=22):
    """The checkbox glyph. When checked, negative character spacing pulls the
    following tick run back over the box so the tick is drawn ON the box."""
    spacing = '<w:spacing w:val="-185"/>' if checked else ""
    return (
        '<w:r><w:rPr>'
        '<w:rFonts w:ascii="Segoe UI Symbol" w:hAnsi="Segoe UI Symbol" w:cs="Segoe UI Symbol"/>'
        f'{spacing}<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
        '</w:rPr><w:t xml:space="preserve">☐</w:t></w:r>'
    )


def _tick_run():
    """A pen-blue Wingdings tick, slightly larger and raised — looks hand-drawn."""
    return (
        '<w:r><w:rPr><w:b/><w:color w:val="1F3C88"/>'
        '<w:sz w:val="26"/><w:szCs w:val="26"/><w:position w:val="4"/>'
        '</w:rPr><w:sym w:font="Wingdings" w:char="F0FC"/></w:r>'
    )


def _label_run(text, size=22):
    return (
        f'<w:r><w:rPr><w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>'
        f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'
    )


def human_checkbox(value, options, size=22):
    """Render options as checkboxes with a human-looking tick on the selected one.

    Returns a RunsXml for a `{{r key }}` placeholder. With no/unknown value all
    boxes stay empty (like the blank paper form).
    """
    rt = RunsXml()
    for i, opt in enumerate(options):
        checked = _match(value, str(opt))
        rt.add(_box_run(checked, size))
        if checked:
            rt.add(_tick_run())
        sep = "    " if i < len(options) - 1 else ""
        rt.add(_label_run(" " + str(opt) + sep, size))
    return rt


# --------------------------------------------------------------------------
# Post-render layout polish
# --------------------------------------------------------------------------

def _text(p):
    return "".join(t.text or "" for t in p._p.iter(qn("w:t")))


def _has_image(p):
    return bool(p._p.findall(".//" + qn("w:drawing")) or p._p.findall(".//" + qn("w:pict")))


def _soft_breaks(p):
    return [b for b in p._p.findall(".//" + qn("w:br")) if b.get(qn("w:type")) != "page"]


def _keep_with_next(p):
    p.paragraph_format.keep_with_next = True


def _row_cant_split(tr):
    trPr = tr._tr.get_or_add_trPr()
    if not trPr.findall(qn("w:cantSplit")):
        trPr.append(trPr.makeelement(qn("w:cantSplit"), {}))


def _row_is_header(tr):
    """Heuristic: a data-table header row has 2+ bold runs."""
    bold = 0
    for tc in tr._tr.findall(qn("w:tc")):
        for b in tc.findall(".//" + qn("w:b")):
            bold += 1
    return bold >= 2


def _mark_header_row(tr):
    trPr = tr._tr.get_or_add_trPr()
    if not trPr.findall(qn("w:tblHeader")):
        trPr.append(trPr.makeelement(qn("w:tblHeader"), {}))


def _equalize_columns(tbl):
    """Give a plain (merge-free) wide table equal column widths."""
    el = tbl._tbl
    if el.findall(".//" + qn("w:gridSpan")) or el.findall(".//" + qn("w:vMerge")):
        return
    grid = el.find(qn("w:tblGrid"))
    if grid is None:
        return
    cols = grid.findall(qn("w:gridCol"))
    if len(cols) < 6:
        return
    widths = [int(c.get(qn("w:w")) or 0) for c in cols]
    total = sum(widths)
    if not total:
        return
    each = total // len(cols)
    for c in cols:
        c.set(qn("w:w"), str(each))
    for tc in el.findall(".//" + qn("w:tc")):
        tcW = tc.find(qn("w:tcPr") + "/" + qn("w:tcW"))
        if tcW is not None:
            tcW.set(qn("w:w"), str(each))
            tcW.set(qn("w:type"), "dxa")


def _keep_row(tr_obj):
    for cell in tr_obj.cells:
        for cp in cell.paragraphs:
            _keep_with_next(cp)


def page_break_before_top_sections(doc):
    """Force every top-level section (a 'Heading 1' paragraph) after the first to
    begin on a new page. Uses the paragraph's page-break-before property, which is
    reflow-safe (the heading stays pinned to the top of a fresh page) rather than a
    manual break run that could drift. The first top-level section is left in place
    so the document does not open with a blank page."""
    first = True
    for p in doc.paragraphs:
        name = (p.style.name if p.style is not None else "") or ""
        if name.strip().lower() in ("heading 1", "heading1"):
            if first:
                first = False
            else:
                p.paragraph_format.page_break_before = True


def polish_layout(doc):
    """Apply all layout fixes to a rendered document (body only; headers/footers
    are left untouched)."""
    paras = doc.paragraphs  # body-level paragraphs (not inside tables)

    for i, p in enumerate(paras):
        if _has_image(p):
            # image centered like its caption, and glued to the caption below
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _keep_with_next(p)
            # glue the preceding heading (skipping blanks) to the image so a
            # section title can never be orphaned at a page bottom
            j = i - 1
            while j >= 0 and not _text(paras[j]).strip():
                _keep_with_next(paras[j])
                j -= 1
            if j >= 0 and not CAPTION_RE.match(_text(paras[j]).strip()):
                _keep_with_next(paras[j])
        elif _soft_breaks(p):
            # justified paragraphs + soft line-breaks = words stretched across
            # the page; left-align them (textarea content: procedure, deviation)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT

        if _is_heading(_text(p)):
            # a section heading must never be stranded at a page bottom: glue it
            # (and any blank spacers under it) to the first following content
            _keep_with_next(p)
            j = i + 1
            while j < len(paras) and not _text(paras[j]).strip():
                _keep_with_next(paras[j])
                j += 1

    # glue each heading/label (and blanks under it) to the table that follows,
    # so "SOFTWARE USED"-style titles are never orphaned at a page bottom
    body = list(doc.element.body)
    para_by_el = {p._p: p for p in paras}
    for idx, el in enumerate(body):
        if not el.tag.endswith("}tbl"):
            continue
        j = idx - 1
        while j >= 0 and body[j].tag.endswith("}p"):
            p = para_by_el.get(body[j])
            if p is None:
                break
            _keep_with_next(p)
            if _text(p).strip():          # the heading itself — stop here
                break
            j -= 1                        # blank spacer — keep gluing upward

    for tbl in doc.tables:
        rows = tbl.rows
        for tr in rows:
            _row_cant_split(tr)          # never split a row across pages
        if len(rows) <= 6:
            # small block tables (sign-off, limits, software...) stay on one page
            for tr in rows[:-1]:
                _keep_row(tr)
        else:
            # long tables: don't strand 1-2 rows at a page bottom (orphans) or
            # leave a lone last row on the next page (widows)
            _keep_row(rows[0])
            _keep_row(rows[1])
            _keep_row(rows[-2])
            if _row_is_header(rows[0]):
                _mark_header_row(rows[0])    # repeat header on continuation pages
        _equalize_columns(tbl)
