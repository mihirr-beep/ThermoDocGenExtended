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
`human_checkbox(...)` renders checkboxes with a crossed box (☒) on the
selected option for `{{r ... }}` placeholders.
"""
import re
from xml.sax.saxutils import escape

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

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
    """The checkbox glyph. Uses a single run with ballot box characters."""
    color = '<w:color w:val="000000"/>' if checked else ""
    char = "☒" if checked else "☐"
    return (
        '<w:r><w:rPr>'
        '<w:rFonts w:ascii="Segoe UI Symbol" w:hAnsi="Segoe UI Symbol" w:cs="Segoe UI Symbol"/>'
        f'{color}<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
        f'</w:rPr><w:t xml:space="preserve">{char}</w:t></w:r>'
    )


def _label_run(text, size=22):
    return (
        f'<w:r><w:rPr><w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr>'
        f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'
    )


def human_checkbox(value, options, size=22):
    """Render options as checkboxes with a human-looking cross on the selected one.

    Returns a RunsXml for a `{{r key }}` placeholder. With no/unknown value all
    boxes stay empty (like the blank paper form).
    """
    rt = RunsXml()
    for i, opt in enumerate(options):
        checked = _match(value, str(opt))
        rt.add(_box_run(checked, size))
        sep = "    " if i < len(options) - 1 else ""
        rt.add(_label_run(" " + str(opt) + sep, size))
    return rt


def cumulative_checkbox(level, options, size=22):
    """Tick every option up to and including `level` (options given in ascending
    order). Used for Surge / EFT test-voltage rows where the level is DERIVED from
    the standard and selecting e.g. ±1 kV implies ±0.5 kV was also applied — so both
    boxes are ticked. A 'Custom' level ticks only the Custom box; a blank/unknown
    level ticks nothing. Returns a RunsXml for a `{{r key }}` placeholder.
    """
    rt = RunsXml()
    sel = -1
    for i, opt in enumerate(options):
        if _match(level, str(opt)):
            sel = i
            break
    sel_is_custom = sel >= 0 and "custom" in str(options[sel]).strip().lower()
    for i, opt in enumerate(options):
        opt_is_custom = "custom" in str(opt).strip().lower()
        if sel_is_custom:
            checked = (i == sel)                      # only the Custom box
        else:
            checked = (sel >= 0 and i <= sel and not opt_is_custom)
        rt.add(_box_run(checked, size))
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


def _remove_blank_spacers_before(para):
    """Delete empty paragraphs immediately preceding `para` (template spacers used
    to push a section onto a new page — redundant once a page break is forced, and
    a cause of blank pages). Stops at the first paragraph that has text, an image,
    or section properties (a sectPr must never be removed)."""
    prev = para._p.getprevious()
    while prev is not None and prev.tag == qn("w:p"):
        txt = "".join(t.text or "" for t in prev.iter(qn("w:t"))).strip()
        has_img = prev.find(".//" + qn("w:drawing")) is not None
        has_sectpr = prev.find(".//" + qn("w:sectPr")) is not None
        if txt or has_img or has_sectpr:
            break
        nxt = prev.getprevious()
        prev.getparent().remove(prev)
        prev = nxt


def paginate_generic_datasheet(doc, last_block_heading="TEST EQUIPMENT USED"):
    """Pagination for the generic immunity datasheets (RS_RI / ESD / CRF / PFMF /
    HARMONIC / VOLTAGEFLICKER / VOLTAGEDIPS / EFT / SURGE).

      (1) Every major section (a 'Heading 1' paragraph) after the first starts on
          a NEW page, even if the previous page has room — so section 2 never
          continues under section 1.
      (3) The final subsections (from `last_block_heading` to the end: TEST
          EQUIPMENT USED / SOFTWARE USED / RESULT, i.e. 2.6 / 2.7 / 2.8) are pushed
          onto a fresh page and glued together, so they always land together on
          the LAST page instead of drifting up under the setup photos.

    Reflow-safe: uses page-break-before + keep-with-next + cantSplit only. Any
    manual break runs and the template's blank 'spacer' paragraphs that used to
    push a section down are removed first, so forcing the break never leaves an
    empty page in between."""
    # Remove manual run-level page breaks so they cannot double up with the
    # page-break-before set below (a cause of blank pages).
    for br in list(doc.element.body.iter(qn("w:br"))):
        if br.get(qn("w:type")) == "page":
            br.getparent().remove(br)

    # (1) each major section (Heading 1 after the first) begins a new page; drop the
    #     blank spacer paragraphs before it so no empty page appears in between.
    first = True
    for p in list(doc.paragraphs):
        name = (p.style.name if p.style is not None else "") or ""
        if name.strip().lower() in ("heading 1", "heading1"):
            if first:
                first = False
            else:
                _remove_blank_spacers_before(p)
                p.paragraph_format.page_break_before = True

    # (3) push the final block onto a fresh (last) page, dropping any spacer
    #     paragraphs before it, then keep the whole block together.
    target = last_block_heading.strip().upper()
    block_head = None
    for p in doc.paragraphs:
        if _text(p).strip().upper().startswith(target):
            block_head = p
            break
    if block_head is None:
        return
    _remove_blank_spacers_before(block_head)
    block_head.paragraph_format.page_break_before = True

    # recompute element positions AFTER the removals for the keep-together pass
    body = list(doc.element.body)
    pos = {el: i for i, el in enumerate(body)}
    start = pos.get(block_head._p, -1)
    # glue every paragraph from here to the end to what follows (except the very
    # last), so the 2.6/2.7/2.8 block cannot break across pages
    tail_paras = [p for p in doc.paragraphs if pos.get(p._p, -1) >= start]
    for p in tail_paras[:-1]:
        _keep_with_next(p)
    # tables in the tail (equipment / software / result): never split a row, and
    # keep their rows together so the whole small table stays on the page
    for tbl in doc.tables:
        if pos.get(tbl._tbl, -1) >= start:
            rows = tbl.rows
            for tr in rows:
                _row_cant_split(tr)
            for tr in rows[:-1]:
                _keep_row(tr)


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


def ce_finalize_layout(doc):
    """CE-only pagination + caption placement (per the DS504 reference layout).

    Applied after polish_layout + page_break_before_top_sections, from the CE
    generator only (generic forms are untouched). It:

      1. Moves each "Table N:" caption to sit BELOW its table (the source
         template puts the caption above; the reference shows image-then-caption
         and table-then-caption -- caption always below the object).
      2. Forces page breaks so the datasheet paginates like the reference:
           * "TEST PROCEDURE"      -> new page: procedure + Measurement-Data
                                       figure 1 & table 1 sit together.
           * Figure 2 block        -> new page: figure 2 & table 2 on their own page.
           * "TEST SETUP PICTURES" -> new page: photo (2.6) + equipment (2.7) together.
           * "SOFTWARE USED"       -> new page: software (2.8) + result (2.9) last page.
      3. Keeps each image -> caption -> table block from splitting across a page.
    """
    # 0) collapse runs of >1 consecutive blank paragraphs to a single blank. The
    #    source template carries several spacer lines (e.g. 4 blanks before
    #    "Measurement Data") that waste ~2cm and push the Table 1 caption onto the
    #    next page; one blank is enough separation.
    #    Table-aware: a table (or any non-paragraph) between two blanks breaks the
    #    run, so the single spacer between a table and the next heading is kept.
    body = doc.element.body
    w_p = qn("w:p")
    prev_blank = False
    for el in list(body):
        if el.tag == w_p:
            txt = "".join(t.text or "" for t in el.iter(qn("w:t"))).strip()
            has_img = el.findall(".//" + qn("w:drawing")) or el.findall(".//" + qn("w:pict"))
            blank = (not txt) and (not has_img)
            if blank and prev_blank:
                body.remove(el)
            else:
                prev_blank = blank
        else:
            prev_blank = False   # table / sectPr / etc. ends the blank run

    # 1) table caption -> below its table
    for p in list(doc.paragraphs):
        t = _text(p).strip()
        if t[:6].lower() == "table " and ":" in t[:14]:
            nxt = p._p.getnext()
            if nxt is not None and nxt.tag == qn("w:tbl"):
                nxt.addnext(p._p)   # relocate the caption to after the table

    # 2) reset all keep-with-next (polish_layout glues nearly every paragraph,
    #    which would otherwise chain the whole document into one un-fittable block).
    for p in doc.paragraphs:
        p.paragraph_format.keep_with_next = False

    # 3) Group the tail of the datasheet into discrete keep-together blocks, each
    #    of which fits on one page, and let Word FLOW them (no forced page breaks,
    #    which double up into blank pages when the previous page is full). A block
    #    that doesn't fit the remaining space moves whole to the next page, giving:
    #      p1 section 1 | p2 2.1-2.3 | p3 procedure+fig1/table1 | p4 fig2/table2
    #      p5 photo(2.6)+equipment(2.7) | p6 software(2.8)+result(2.9)
    paras = doc.paragraphs
    texts = [_text(p).strip().lower() for p in paras]

    def find(pred, start=0):
        for k in range(start, len(paras)):
            if pred(texts[k]):
                return k
        return None

    a0 = find(lambda t: t == "test procedure")                       # procedure (2.4)
    c0 = find(lambda t: t == "test setup pictures")                  # photo (2.6) + equipment (2.7)
    d0 = find(lambda t: t == "software used")                        # software (2.8) + result (2.9)
    fig1s = [k for k in range(len(paras)) if texts[k].startswith("figure 1")]
    fig2s = [k for k in range(len(paras)) if texts[k].startswith("figure 2")]

    def glue(start, end):
        """Bind [start, end) into one keep-together block: keep_together stops any
        single paragraph (e.g. the procedure text) splitting across pages, and
        keep_with_next chains the members. The last member is left free so the
        next block can begin on a fresh page."""
        if start is None:
            return
        end = len(paras) if end is None else end
        last = min(end, len(paras))
        for k in range(start, last):
            paras[k].paragraph_format.keep_together = True
        for k in range(start, last - 1):
            paras[k].paragraph_format.keep_with_next = True

    # Measurement records (one per Test), laid out like the reference: the Test Procedure
    # keeps its own page, then EACH Figure+Table pair gets its own page. Per record the
    # label sits two paragraphs above its "Figure 1" caption (label, image, caption).
    m0 = find(lambda t: t == "measurement data")      # 2.5 heading (stays with Figure 1)
    n = min(len(fig1s), len(fig2s))
    if a0 is not None and n:
        glue(a0, m0 if m0 is not None else max(0, fig1s[0] - 2))   # 2.4 Test Procedure: its own page
    if m0 is not None:
        paras[m0].paragraph_format.page_break_before = True        # 2.5 + Figure 1 begin a new page
    for k in range(n):
        label_k = max(0, fig1s[k] - 2)
        pn_img = fig2s[k] - 1                         # plot_neutral image (above the Figure 2 caption)
        nxt = (fig1s[k + 1] - 2) if (k + 1) < n else c0
        first_with_heading = (k == 0 and m0 is not None)
        a_start = m0 if first_with_heading else label_k
        glue(a_start, pn_img)                         # Figure 1 + Table 1 -> its own page
        glue(pn_img, nxt)                             # Figure 2 + Table 2 -> its own page
        if not first_with_heading:
            paras[label_k].paragraph_format.page_break_before = True   # Fig1/Fig3 block -> new page
        paras[pn_img].paragraph_format.page_break_before = True        # Fig2/Fig4 block -> new page

    glue(c0, d0)                # Group C: 2.6 photo + 2.7 equipment
    glue(d0, None)              # Group D: 2.8 software + 2.9 result
    if c0 is not None:
        paras[c0].paragraph_format.page_break_before = True   # 2.6 setup + 2.7 equipment on their own page

    # 2.8 Software + 2.9 Result belong together on the final page. Group C (photo +
    # equipment) leaves the page part-empty, so software would otherwise flow up onto
    # it; a page break here moves the pair down cleanly (Group C is not full, so this
    # does not create a blank page).
    if d0 is not None:
        paras[d0].paragraph_format.page_break_before = True

    # 4) 1.4 Functional Check plots (Line + Neutral): put them on their own page with
    #    each bold heading directly ABOVE its image, and keep each heading glued to its
    #    image. Detected as the image paragraphs that appear BEFORE the "Conducted
    #    Emission Test" section (the 2.5/2.6 plots live after it).
    sec2 = find(lambda t: t == "conducted emission test")
    limit = sec2 if sec2 is not None else len(paras)
    func_imgs = [k for k in range(limit) if _has_image(paras[k])]
    if func_imgs:
        def _heading_above(k):
            j = k - 1
            while j > 0 and not _text(paras[j]).strip():
                j -= 1
            return j
        paras[_heading_above(func_imgs[0])].paragraph_format.page_break_before = True
        for k in func_imgs:
            hk = _heading_above(k)
            paras[hk].paragraph_format.keep_with_next = True
            paras[hk].paragraph_format.keep_together = True
            paras[k].paragraph_format.keep_together = True
        amb = find(lambda t: t == "ambient")          # 1.5 Ambient -> its own page (after the 1.4 plots)
        if amb is not None:
            paras[amb].paragraph_format.page_break_before = True
            paras[amb].paragraph_format.keep_with_next = True

    # 5) Glue each table to the "Table N:" / "Figure N:" caption that sits BELOW it.
    #    A table has no keep-with-next of its own, so when it lands at the bottom of a
    #    page Word can strand its caption on the next page (caption orphaned from its
    #    table). Setting keepNext on the table's cell paragraphs keeps the whole table
    #    with the caption that follows it. Scoped to tables followed by such a caption
    #    (the small measurement plot/data tables), so large tables are unaffected.
    from docx.oxml import OxmlElement
    for tbl in body.findall(qn("w:tbl")):
        nxt = tbl.getnext()
        if nxt is None or nxt.tag != w_p:
            continue
        cap = "".join(t.text or "" for t in nxt.iter(qn("w:t"))).strip().lower()
        if not cap.startswith("table "):   # data table -> its "Table N:" caption below
            continue
        for cp in tbl.iter(w_p):
            pPr = cp.find(qn("w:pPr"))
            if pPr is None:
                pPr = OxmlElement("w:pPr"); cp.insert(0, pPr)
            if pPr.find(qn("w:keepNext")) is None:
                pPr.append(OxmlElement("w:keepNext"))


# --------------------------------------------------------------------------
# Font enforcement — force Arial on every table cell run
# --------------------------------------------------------------------------

def enforce_arial_procedure(doc):
    """Force Arial on the TEST PROCEDURE body text.

    The procedure text is entered/edited on the web form and rendered into a
    template paragraph whose style is 'Normal (Web)' (Times New Roman) or the
    document default — so the run inherits Times New Roman. This walks the body
    paragraphs between the 'TEST PROCEDURE' heading and the next heading and sets
    every run's font to Arial (symbol/checkbox runs are skipped)."""
    from docx.oxml import OxmlElement
    ARIAL = "Arial"
    SKIP_FONTS = {"Segoe UI Symbol", "Symbol", "Wingdings"}
    in_proc = False
    for p in doc.paragraphs:
        name = (p.style.name if p.style is not None else "") or ""
        if name.strip().lower().startswith("heading"):
            in_proc = "test procedure" in (p.text or "").strip().lower()
            continue
        if not in_proc or not (p.text or "").strip():
            continue
        # The procedure is one paragraph with soft line-breaks; if the template
        # justifies it, every line is stretched with big word gaps. Left-align it
        # (matches polish_layout's textarea handling) so the text reads normally.
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for run in p.runs:
            rPr = run._r.get_or_add_rPr()
            rFonts = rPr.find(qn("w:rFonts"))
            if rFonts is not None:
                current = rFonts.get(qn("w:ascii")) or rFonts.get(qn("w:hAnsi")) or ""
                if current in SKIP_FONTS:
                    continue
                rFonts.set(qn("w:ascii"), ARIAL)
                rFonts.set(qn("w:hAnsi"), ARIAL)
                rFonts.set(qn("w:cs"), ARIAL)
            else:
                rf = OxmlElement("w:rFonts")
                rf.set(qn("w:ascii"), ARIAL)
                rf.set(qn("w:hAnsi"), ARIAL)
                rf.set(qn("w:cs"), ARIAL)
                rPr.insert(0, rf)
            run.font.name = ARIAL


def enforce_arial_fonts(doc):
    """Walk every paragraph in every table cell and force the font to Arial.

    This overrides any Calibri runs that come from:
      * docxtpl Jinja-rendered measurement-data rows (inherit Normal/Calibri style)
      * any cell whose run-level rFonts was left unset (Word defaults to Calibri)

    Body paragraphs (headings, procedure text) are left untouched because
    they already carry explicit Arial formatting via the template styles.
    The checkbox runs (Segoe UI Symbol) are skipped so ballot boxes render
    correctly.
    """
    SKIP_FONTS = {"Segoe UI Symbol", "Symbol", "Wingdings"}
    ARIAL = "Arial"

    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        run.font.size = Pt(11)
                        rPr = run._r.get_or_add_rPr()
                        # Skip special symbol runs (checkboxes)
                        rFonts = rPr.find(qn("w:rFonts"))
                        if rFonts is not None:
                            current = (
                                rFonts.get(qn("w:ascii")) or
                                rFonts.get(qn("w:hAnsi")) or ""
                            )
                            if current in SKIP_FONTS:
                                continue
                            # Update existing rFonts element
                            rFonts.set(qn("w:ascii"), ARIAL)
                            rFonts.set(qn("w:hAnsi"), ARIAL)
                            rFonts.set(qn("w:cs"), ARIAL)
                        else:
                            # Create new rFonts element
                            from docx.oxml import OxmlElement
                            rf = OxmlElement("w:rFonts")
                            rf.set(qn("w:ascii"), ARIAL)
                            rf.set(qn("w:hAnsi"), ARIAL)
                            rf.set(qn("w:cs"), ARIAL)
                            rPr.insert(0, rf)
                        # Also set run.font.name so python-docx's own cache is consistent
                        run.font.name = ARIAL


def enforce_body_arial(doc, size=11):
    """Force Arial on every body (non-table) paragraph and set <size>pt on every
    non-heading paragraph (Heading/Title styles keep their heading size). Also pins
    the Normal / Normal (Web) styles to Arial <size> so any run that merely INHERITS
    comes out Arial instead of Word's Calibri fallback (the root cause of the stray
    Calibri: those base styles carry no explicit font). Symbol/checkbox runs keep
    their font. Companion to enforce_arial_fonts (which covers table cells)."""
    from docx.oxml import OxmlElement
    SKIP_FONTS = {"Segoe UI Symbol", "Symbol", "Wingdings"}
    ARIAL = "Arial"
    for style_name in ("Normal", "Normal (Web)"):
        try:
            stl = doc.styles[style_name]
            stl.font.name = ARIAL
            stl.font.size = Pt(size)
        except KeyError:
            pass
    for para in doc.paragraphs:
        nm = (para.style.name if para.style is not None else "") or ""
        is_heading = nm.strip().lower().startswith(("heading", "title"))
        for run in para.runs:
            rPr = run._r.get_or_add_rPr()
            rFonts = rPr.find(qn("w:rFonts"))
            if rFonts is not None:
                current = rFonts.get(qn("w:ascii")) or rFonts.get(qn("w:hAnsi")) or ""
                if current not in SKIP_FONTS:
                    rFonts.set(qn("w:ascii"), ARIAL)
                    rFonts.set(qn("w:hAnsi"), ARIAL)
                    rFonts.set(qn("w:cs"), ARIAL)
                    run.font.name = ARIAL
            else:
                rf = OxmlElement("w:rFonts")
                rf.set(qn("w:ascii"), ARIAL)
                rf.set(qn("w:hAnsi"), ARIAL)
                rf.set(qn("w:cs"), ARIAL)
                rPr.insert(0, rf)
                run.font.name = ARIAL
            if not is_heading:
                run.font.size = Pt(size)


def shrink_wide_obs_tables(doc, size=8):
    """Re-shrink the wide Surge observation matrices (17 columns) after the global
    Arial pass — 11pt cannot fit 17 columns on a portrait page. Keeps the (already
    Arial) font, only reduces the point size and centres each cell. Matched by the
    header (Common/Differential Mode) or the Signal-line label."""
    for t in doc.tables:
        try:
            hdr = " ".join(c.text for c in t.rows[0].cells)
            first = t.rows[0].cells[0].text.strip()
        except Exception:
            continue
        if ("Common Mode" in hdr and "Differential Mode" in hdr) or first.startswith("Name of the signal"):
            for row in t.rows:
                for c in row.cells:
                    for p in c.paragraphs:
                        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        for r in p.runs:
                            r.font.size = Pt(size)
