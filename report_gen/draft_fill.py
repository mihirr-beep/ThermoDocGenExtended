# -*- coding: utf-8 -*-
"""Write the admin's wizard answers into IEC-FRM-516, and report what was not asked.

WHY THIS EXISTS
---------------
The wizard collects twenty-one values that the database has no source for, saves
them in ``report_draft``, and until now nothing read them back. Generating a
report today ignores all of them: measured on a real build, six of their
destinations ship blank, four ship "NA" - DATE OF RECEIPT OF EUT, SOFTWARE AND
FIRMWARE DETAILS, EUT CONFIGURATION DURING TEST, EUT MONITORING PARAMETERS - and
the four image slots ship empty. (The blank Signature is deliberately not one of
them; it is out of scope.) Those four NAs are the reason this module exists.
``cleanup_instructions`` writes NA over any placeholder it cannot source, so the
document reads as though the lab decided the field was not applicable when in
fact nobody was ever asked. Which of blank and NA a field got is not a decision
anyone made either - it is only whether the template happened to ship a prompt in
that cell.

TWO RULES THAT SHAPE EVERYTHING BELOW
-------------------------------------
1. **This module never writes "NA".** An unsupplied field is returned in
   ``missing`` and left exactly as the template had it, so the caller decides
   what an unanswered question looks like. Writing NA here would move the defect
   rather than fix it.

2. **Fill before cleanup, do not teach cleanup about fields.** The proof is in
   the template: cover rows 8 and 11 carry the *identical* placeholder
   ``<Click or tap to enter a date.>``; row 11 prints a date because
   ``fill_cover`` wrote it first, row 8 prints NA because nothing did. A filled
   cell is immune to every ``_INSTRUCTION_PATTERNS`` entry by construction, so
   there is no skip-list to maintain and no second copy of the key -> block map.

Everything here writes through helpers that already exist in ``builder`` and
``docx_tools`` - ``_set_row_value``, ``_fill_text_block``, ``_append_photo``,
``insert_image_before``. No new table writer: the per-test sections of this same
report drifted in 49 of 92 subsections because two places each kept their own
idea of where a field goes.
"""
import logging
import os

from docx.table import Table
from docx.text.paragraph import Paragraph

from . import builder as B
from . import docx_tools as T
from . import draft
from . import service as S
from . import wizard_fields as WF

log = logging.getLogger(__name__)

SEC = "EUT INFORMATION"

# The wizard's screenshot becomes a real numbered Photo rather than being pasted
# into the template's 2.8 placeholder table. See _fill_monitoring_photo.
MONITORING_LABEL = "Monitoring software screenshot"


# ==========================================================================
# what happened, per field
# ==========================================================================

class _Log(object):
    """Per-field outcome, because "the report built" is not the same as "it is complete"."""

    def __init__(self):
        self.written, self.images, self.missing, self.notes = [], [], [], []

    def wrote(self, key, where):
        self.written.append(key)
        self.notes.append("%s -> %s" % (key, where))

    def wrote_many(self, keys, where):
        """One destination, several spec keys - 2.1's size is built from four."""
        self.written.extend(keys)
        self.notes.append("%s -> %s" % ("+".join(keys), where))

    def wrote_image(self, key, where):
        self.images.append(key)
        self.notes.append("%s -> %s" % (key, where))

    def absent(self, key, why=""):
        if key not in self.missing:
            self.missing.append(key)
        if why:
            self.notes.append("%s: %s" % (key, why))

    def note(self, msg):
        self.notes.append(msg)

    def as_dict(self):
        return {"written": self.written, "images": self.images,
                "missing": self.missing, "notes": self.notes}


def _safe(rec, what, fn, key=None):
    """One write. A failure costs that field and nothing else.

    Every destination goes through here because the report is the deliverable: an
    unreadable upload or a drifted template row must not be able to turn a
    finished build into a 500.

    ``key`` is a wizard field key or None for a whole group. None on purpose:
    "cover" is not a field, and putting it in ``missing`` would hand the caller a
    key that is not in the spec. The group's fields are caught by apply_draft's
    closing sweep instead.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        log.error("report draft: could not write %s: %s", what, exc)
        if key:
            rec.absent(key, "write failed (%s)" % exc)
        else:
            rec.note("%s: write failed (%s)" % (what, exc))
        return False


# ==========================================================================
# reading the two stores
# ==========================================================================

def _s(v):
    return "" if v is None else str(v).strip()


def _request_fields(request_id):
    """The six store="request" columns, as a plain dict. {} when unreadable.

    Reuses the wizard's own reader so there is one SELECT and one column list -
    a second copy here is how the form and the document would start disagreeing
    about what "weight" means. Imported inside the function: this module is also
    imported by the builder, which must stay importable without the Flask
    request context that wizard_routes pulls in at module scope.
    """
    try:
        from .wizard_routes import _request_row
        return _request_row(request_id) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("report draft: request columns unreadable for %s: %s",
                    request_id, exc)
        return {}


def _dimensions(row, rec):
    """"120 x 45 x 30 mm" from whatever axes exist, plus which parts were missing.

    Partial on purpose: three quarters of the size is worth more to a reader than
    a blank cell, and the axes that were not supplied are still reported missing
    so the wizard can keep asking for them.
    """
    for key in ("length", "width", "height", "dimension_unit"):
        if not _s(row.get(key)):
            rec.absent(key)
    # SimpleNamespace so service._dimensions can be reused unchanged - it reads
    # attributes off the request object and already does the "%g" per axis, the
    # " x " join and the mm default. Duplicating that here is how the cover and
    # 2.1 would end up formatting the same number two ways.
    from types import SimpleNamespace
    text = S._dimensions(SimpleNamespace(**{k: row.get(k) for k in (
        "length", "width", "height", "dimension_unit")}))
    if text and not all(_s(row.get(k)) for k in ("length", "width", "height")):
        rec.note("2.1 Size of the EUT: written partial (%r) - missing %s" % (
            text, ", ".join(k for k in ("length", "width", "height")
                            if not _s(row.get(k)))))
    return text


def _weight(row):
    """Kilograms with the unit the column cannot hold.

    The column is FLOAT (the first save posted "12.4 kg" and MySQL raised 1265),
    so the unit is added here - same format string as service.collect, so a
    request that already has a weight and one the wizard just typed print alike.
    """
    v = row.get("weight")
    if v in (None, ""):
        return ""
    try:
        return "%g kg" % float(v)
    except (TypeError, ValueError):
        return _s(v)


# ==========================================================================
# small document questions
# ==========================================================================

def _row_is_blank(table, label):
    """True/False for "this row still has nothing real in it"; None if no such row.

    None is template drift - the row the code expects is not in the document any
    more - and is reported rather than silently skipped, because a silent skip is
    exactly how five 2.1 rows stayed empty for so long without anyone noticing.
    """
    row = B._find_row(table, label)
    if row is None:
        return None
    cells = T.distinct_cells(row)
    if len(cells) < 2:
        return None
    txt = T.full_text(cells[-1]).strip()
    return (not txt) or bool(B._PLACEHOLDER_ONLY.match(txt))


def _has_image_before(caption):
    """Does the slot above this caption already hold a picture?

    Guards against a second insert under one caption. fill_eut_information
    already inserts Figure 1 when the request's block_diagram column has data,
    and the draft is a fallback for that column, not a second copy of it. Asks
    the document rather than the meta dict, because the document is what ships.
    """
    prev = caption._p.getprevious()
    while prev is not None:
        if prev.tag != T.qn("w:p"):
            return False
        p = Paragraph(prev, caption._parent)
        if p._p.findall(".//" + T.qn("w:drawing")):
            return True
        if T.text_of(p):
            return False            # a real paragraph: the slot is above it
        prev = prev.getprevious()
    return False


def _photo_anchor(blocks):
    """The last text paragraph before the subsection's first table.

    The monitoring photo goes after the whole monitoring narrative, not after its
    first line - set_paragraph_lines turns one textarea into several paragraphs,
    and anchoring on the first would drop the picture into the middle of the text.
    """
    anchor = None
    for b in blocks[1:]:
        if isinstance(b, Table):
            break
        if isinstance(b, Paragraph) and T.text_of(b):
            anchor = b
    return anchor


# ==========================================================================
# the destinations
# ==========================================================================

def _fill_cover(outline, form, rec):
    """Five cover rows. The draft wins here, by design.

    fill_cover has already written this table from the request, but four of these
    five rows are things only the admin knows, and TEST REPORT ISSUE DATE was
    filled with today's date - if the draft did not override it, the wizard would
    collect an issue date and the report would print the build date instead.
    """
    tables = [b for b in outline.blocks[:6] if isinstance(b, Table)]
    if not tables:
        rec.note("cover: table not found - no cover field written")
        for key in ("condition_on_receipt", "date_of_receipt", "test_location",
                    "report_issue_date", "issued_to"):
            rec.absent(key, "cover table not found")
        return
    cover = tables[0]

    # (key, exact template label, value). Dates are formatted with the same
    # helper the rest of the document uses so "2026-08-11" from a date input
    # prints as "11 Aug 2026" like every other date on the page.
    rows = (
        ("condition_on_receipt", "CONDITION OF EUT ON RECEIPT",
         _s(form.get("condition_on_receipt"))),
        ("date_of_receipt", "DATE OF RECEIPT OF EUT",
         S.fmt_date(_s(form.get("date_of_receipt")))),
        ("test_location", "LOCATION OF PERFORMANCE OF TEST",
         _s(form.get("test_location"))),
        ("report_issue_date", "TEST REPORT ISSUE DATE",
         S.fmt_date(_s(form.get("report_issue_date")))),
        ("issued_to", "ISSUED TO", _s(form.get("issued_to"))),
    )
    for key, label, value in rows:
        if not value:
            rec.absent(key)
            continue

        def _write(label=label, value=value, key=key):
            if B._set_row_value(cover, label, value):
                rec.wrote(key, "cover %s" % label)
                return True
            rec.absent(key, "cover row %r not in the template" % label)
            return False
        _safe(rec, "cover " + label, _write, key=key)


def _fill_eut_details(outline, form, row, rec):
    """2.1 EUT DETAILS - five rows, two different precedence rules.

    Size and weight have no other source, so the wizard's value is written
    outright. Operating frequency, power rating and measured current DO have one:
    service.collect derives them from the request's eut_specs blob, and
    fill_eut_information has already written those. There the rule is "spec wins,
    the draft fills the gap" - a value peer review saw must not be overwritten by
    a later typed one, and which of the two wins must not depend on whether an
    eut_specs row happens to exist.
    """
    tabs = outline.tables_in(SEC, "EUT DETAILS")
    keys = ("length", "width", "height", "dimension_unit", "weight",
            "operating_frequency", "power_rating", "measured_current")
    if not tabs:
        rec.note("2.1 EUT DETAILS: table not found - no detail row written")
        for key in keys:
            rec.absent(key, "2.1 table not found")
        return
    tb = tabs[0]

    # ---- always ours: nothing else in the database supplies these ----
    # One row, four spec keys - only the axes that were actually supplied count
    # as written, so a partial size still reports its missing axes.
    dims = _dimensions(row, rec)
    if dims:
        supplied = [k for k in ("length", "width", "height", "dimension_unit")
                    if _s(row.get(k))]

        def _write_size():
            if B._set_row_value(tb, "Size of the EUT", dims):
                rec.wrote_many(supplied, "2.1 Size of the EUT (%s)" % dims)
                return True
            for k in supplied:
                rec.absent(k, "2.1 'Size of the EUT' row not in the template")
            return False
        _safe(rec, "2.1 Size of the EUT", _write_size)

    weight = _weight(row)
    if not weight:
        rec.absent("weight")
    else:
        def _write_weight():
            if B._set_row_value(tb, "Weight of the EUT", weight):
                rec.wrote("weight", "2.1 Weight of the EUT")
                return True
            rec.absent("weight", "2.1 'Weight of the EUT' row not in the template")
            return False
        _safe(rec, "2.1 Weight of the EUT", _write_weight, key="weight")

    # ---- gap-fill only: the EUT spec outranks the draft ----
    for key, label in (("operating_frequency", "EUT Operating Frequency"),
                       ("power_rating", "EUT Power Rating"),
                       ("measured_current", "Measured EUT Current")):
        value = _s(form.get(key)) or _s(row.get(key))
        blank = _row_is_blank(tb, label)
        if blank is None:
            rec.absent(key, "2.1 row %r not in the template" % label)
            continue
        if not blank:
            if value:
                # said out loud: the admin typed something the document did not
                # use, which is the kind of silence this phase exists to remove
                rec.note("2.1 %s: kept the EUT-spec value, the wizard's %r was "
                         "not used" % (label, value))
            continue
        if not value:
            rec.absent(key)
            continue

        def _write(label=label, value=value, key=key):
            if B._set_row_value(tb, label, value):
                rec.wrote(key, "2.1 %s" % label)
                return True
            rec.absent(key, "2.1 row %r not in the template" % label)
            return False
        _safe(rec, "2.1 " + label, _write, key=key)


def _fill_text_blocks(outline, form, rec):
    """2.3, 2.5, 2.7, 2.8 - four free-text subsections.

    2.3 is new: builder deliberately left its placeholder alone because nothing
    sourced it, which is what prints NA today. 2.5, 2.7 and 2.8 already have a
    writer; supplying them here rather than through meta keeps the draft's value
    ahead of the request's for the two that have both. 2.7 goes through the same
    _fill_text_block as the rest - one textarea line per mode, and the leftover
    <Mode B: ...> prompt is dropped by _drop_placeholder_paragraphs inside it.
    """
    for key, sub in (("software_firmware", "SOFTWARE AND FIRMWARE DETAILS"),
                     ("eut_configuration", "EUT CONFIGURATION DURING TEST"),
                     ("modes_of_operation", "EUT MODES OF OPERATION"),
                     ("monitoring_parameters", "EUT MONITORING PARAMETERS")):
        value = _s(form.get(key))
        if not value:
            rec.absent(key)
            continue

        def _write(key=key, sub=sub, value=value):
            blocks = outline.sub_blocks(SEC, sub)
            if not blocks:
                rec.absent(key, "subsection %r not in the template" % sub)
                return False
            if B._first_body_paragraph(blocks) is None:
                rec.absent(key, "%r has no body paragraph to write into" % sub)
                return False
            B._fill_text_block(outline, SEC, sub, value)
            rec.wrote(key, sub)
            return True
        _safe(rec, sub, _write, key=key)


def _write_ulr(doc, rec):
    """ULR NO, which lives in the running header and nothing has ever written.

    cleanup_instructions cannot reach it - Outline.blocks is doc.element.body
    only - so the template's <TC14704YY0XXXXXXXXF> ships verbatim in every
    report to date. Matching on the label inside the cell and retyping the label,
    exactly as fill_header does for TEST REPORT NO, because set_cell_text
    replaces the whole cell. (This belongs as a branch in fill_header; it is here
    because this change adds no lines to builder.py - see the integration note.)
    """
    ulr = _s(WF.ULR_NO)
    if not ulr:
        rec.absent("ulr_no", "wizard_fields.ULR_NO is empty")
        return
    hit = 0
    for section in doc.sections:
        for hf in (section.header, section.first_page_header):
            if hf is None:
                continue
            for tbl in hf.tables:
                for row in tbl.rows:
                    for cell in T.distinct_cells(row):
                        if "ULR NO" in T.full_text(cell).upper():
                            T.set_cell_text(cell, "ULR NO: %s" % ulr)
                            hit += 1
    if hit:
        rec.wrote("ulr_no", "header ULR NO (%d cell(s))" % hit)
    else:
        rec.absent("ulr_no", "no header cell mentions ULR NO")


# ==========================================================================
# images
# ==========================================================================

def _image_path(images, key, rec):
    """The stored path, or None with the reason recorded.

    The wizard saves files and stores paths, so a key with no file on disk means
    the upload directory was cleaned behind the draft - worth saying, not worth
    raising over.
    """
    path = _s((images or {}).get(key))
    if not path:
        rec.absent(key)
        return None
    if not os.path.exists(path):
        rec.absent(key, "file missing from disk (%s)" % os.path.basename(path))
        return None
    return path


def _insert_at_caption(outline, sub, index, key, path, box, rec):
    """Put one picture in the slot above the index-th Figure/Photo caption.

    Matched by position, not by caption text: section 2's captions have no
    mapping.image_captions() entry (that exists for the per-test sections only),
    and the template's slot order IS the intended order - Photo 1 the EUT,
    Photo 2 its rating label.
    """
    caps = B._caption_paragraphs(outline.sub_blocks(SEC, sub), B.IMAGE_CAPTIONS)
    if len(caps) <= index:
        rec.absent(key, "%s has no caption %d to attach to" % (sub, index + 1))
        return False
    cap = caps[index]
    if _has_image_before(cap):
        rec.note("%s: %s already has a picture, draft image not inserted"
                 % (key, T.text_of(cap)[:40]))
        return False
    if T.insert_image_before(cap, path, max_width_mm=box[0],
                             max_height_mm=box[1]) is None:
        # insert_image_before removes the paragraph it created and returns None
        # rather than raising, so a corrupt upload degrades to "no picture"
        rec.absent(key, "not a readable image (%s)" % os.path.basename(path))
        return False
    rec.wrote_image(key, "%s / %s" % (sub, T.text_of(cap)[:40]))
    return True


def _fill_monitoring_photo(outline, path, rec):
    """The 2.8 screenshot, as a numbered Photo rather than in the template's table.

    The template reserves a 2-row table for it, and filling that table is the
    obvious move - but its first row reads "Monitoring of EUT using <xxxxx>
    <software version x.y>", and keeping the table alive puts that row in front
    of cleanup's _INLINE_NOISE pass, which turns <xxxxx> into "NA". Measured: the
    report would print "Monitoring of EUT using NA" - a brand-new silent NA, in
    the phase whose whole purpose is removing them - and there is no wizard field
    holding the software name to compose that sentence from.

    So the table is left to cleanup_instructions, which already deletes it (it
    matches "< Screenshot of monitoring software >"), and the picture is appended
    with _append_photo instead. That also buys what the table never gave: a real
    "Photo N:" SEQ caption, so Word numbers it with the others and lists it under
    LIST OF PHOTOS.

    THE DETOUR THROUGH 2.6 IS DELIBERATE. _append_photo clones the anchor's
    formatting, and its only existing caller anchors on a Caption paragraph, so it
    inherits caption formatting for free. 2.8 has no caption to anchor on - the
    anchor is body text - and the first build of this shipped a left-aligned 11 pt
    body-styled caption sitting among centred 10 pt ones. Building it against the
    template's own Figure 1 caption and then moving the two paragraphs into place
    keeps the look right without a second copy of what a caption is.
    """
    anchor = _photo_anchor(outline.sub_blocks(SEC, "EUT MONITORING PARAMETERS"))
    if anchor is None:
        rec.absent("img_monitoring", "2.8 has no paragraph to anchor a photo to")
        return False
    models = B._caption_paragraphs(outline.sub_blocks(SEC, "EUT SETUP DETAILS"),
                                  B.IMAGE_CAPTIONS)
    born_at = models[0] if models else anchor
    if not models:
        rec.note("img_monitoring: no template caption to copy formatting from, "
                 "the photo caption will carry body formatting")
    cap = B._append_photo(born_at, path, MONITORING_LABEL, B.PHOTO_BOX,
                          kind="Photo")
    if cap is not None and born_at is not anchor and _has_image_before(cap):
        # lxml moves an element on insert, so this relocates rather than copies.
        # Picture first, then caption - the caption goes under its picture.
        pic = cap._p.getprevious()
        anchor._p.addnext(cap._p)
        cap._p.addprevious(pic)
    if cap is None or not _has_image_before(cap):
        # _append_photo writes the caption first and inserts the picture above
        # it, and insert_image_before returns None on an unreadable file - so a
        # corrupt upload would leave a "Photo N:" caption over blank space and
        # point LIST OF PHOTOS at nothing. That is the defect the datasheets grew
        # _drop_captionless_photos for; take the caption back out instead.
        if cap is not None:
            T.remove(cap)
        rec.absent("img_monitoring", "not a readable image (%s)"
                   % os.path.basename(path))
        return False
    rec.wrote_image("img_monitoring", "2.8 EUT MONITORING PARAMETERS / Photo")
    return True


def _fill_images(outline, images, rec):
    """The four uploads: Figure 1, Photo 1, Photo 2, and the 2.8 screenshot."""
    plan = (
        ("img_block_diagram", "EUT SETUP DETAILS", 0, B.DIAGRAM_BOX),
        ("img_eut_photo", "EUT AND ACCESSORIES PICTURES", 0, B.PHOTO_BOX),
        ("img_eut_label", "EUT AND ACCESSORIES PICTURES", 1, B.PHOTO_BOX),
    )
    for key, sub, index, box in plan:
        path = _image_path(images, key, rec)
        if path:
            _safe(rec, sub,
                  lambda k=key, s=sub, i=index, p=path, b=box:
                  _insert_at_caption(outline, s, i, k, p, b, rec), key=key)

    path = _image_path(images, "img_monitoring", rec)
    if path:
        _safe(rec, "2.8 monitoring screenshot",
              lambda p=path: _fill_monitoring_photo(outline, p, rec),
              key="img_monitoring")


# ==========================================================================
# entry point
# ==========================================================================

def apply_draft(outline, doc, request_id, meta=None):
    """Write every admin-supplied value from report_draft into the document.

    Returns {"written": [keys], "images": [keys], "missing": [keys]} - plus
    "notes", a per-field trail for the build summary, in the same spirit as
    cleanup_instructions' return value.

    ``missing`` is every wizard key this build did not put in the document,
    whether nobody supplied it or the write failed. Nothing here writes "NA" for
    it: what an unanswered question should look like is the caller's decision,
    and today's answer - a silent NA from cleanup_instructions - is the defect
    being fixed. WF.OPTIONAL says which of them nobody needs to chase.

    All three lists carry WF.FIELDS keys, plus one that is not a field:
    ``"ulr_no"`` for the header constant, which has no column and no wizard input
    but does have a destination that nothing has ever written. It lands in
    ``missing`` only if the header row it belongs in is gone from the template.

    MUST run after fill_eut_information and before cleanup_instructions. After,
    because the spec-derived 2.1 rows have to be in place for the gap-fill rule
    to see them; before, because a cell still holding a <placeholder> is what
    cleanup turns into NA.

    ``meta`` is accepted for symmetry with the other fill_* functions and is not
    read: every precedence question this module has to answer is answered by
    looking at what is in the document, which is what actually ships, rather than
    at what meta intended.
    """
    rec = _Log()
    try:
        d = draft.load(request_id)              # one round trip, never raises
        form = d.get("form") or {}
        images = d.get("images") or {}
        row = _request_fields(request_id)
        if not d.get("exists"):
            rec.note("no report_draft row for request %s - nothing was entered "
                     "in the wizard" % request_id)

        _safe(rec, "cover rows", lambda: _fill_cover(outline, form, rec))
        _safe(rec, "2.1 EUT DETAILS",
              lambda: _fill_eut_details(outline, form, row, rec))
        _safe(rec, "header ULR NO", lambda: _write_ulr(doc, rec), key="ulr_no")
        _safe(rec, "2.3/2.5/2.7/2.8 text",
              lambda: _fill_text_blocks(outline, form, rec))

        # The text writes above insert sibling paragraphs, which leaves
        # outline.blocks' positional indices stale. Refresh before the images:
        # the 2.8 photo has to be anchored after the LAST line of the monitoring
        # text, and only a refreshed outline can see those new lines.
        outline.refresh()
        _safe(rec, "section 2 pictures",
              lambda: _fill_images(outline, images, rec))
        outline.refresh()
    except Exception as exc:  # noqa: BLE001 - a draft must never cost the report
        log.error("report draft: apply_draft failed for request %s: %s",
                  request_id, exc)
        rec.note("apply_draft aborted: %s" % exc)

    # Anything the spec declares and nobody wrote is missing, including keys a
    # branch above never reached. Computed from WF.FIELDS rather than accumulated
    # by hand, so a field added to the spec cannot be quietly forgotten here.
    done = set(rec.written) | set(rec.images)
    for key in WF.keys():
        if key not in done:
            rec.absent(key)
    return rec.as_dict()


# ==========================================================================
# INTEGRATION - the exact edit to make in report_gen/builder.py
# ==========================================================================
#
# ONE edit, in build_report(): one call, inserted immediately after the existing
# `outline.refresh()` on line 1285 (the one that follows
# `fill_eut_information(outline, data)` on line 1284) and before
# `tick_decision_rules(outline, data["meta"])` on line 1286, so the new block
# reads:
#
#     1284    fill_eut_information(outline, data)
#     1285    outline.refresh()
#     1286+   # The wizard's twenty-one admin-entered values. Must land here:
#     1287+   # after the spec-derived 2.1 rows so it only fills the gaps, and
#     1288+   # before cleanup_instructions (line 1321) turns any <placeholder>
#     1289+   # it left alone into "NA".
#     1290+   from . import draft_fill as DF
#     1291+   drafted = DF.apply_draft(outline, doc, getattr(request_obj, "id", None),
#     1292+                            meta=data["meta"])
#     1293    tick_decision_rules(outline, data["meta"])
#
# Import INSIDE the function, matching `from . import splice as SPL` at line
# 1301: draft_fill imports builder at module scope, so a top-level import here
# would be a cycle.
#
# apply_draft never raises and returns
# {"written": [...], "images": [...], "missing": [...], "notes": [...]}, so
# `drafted` needs no try/except. Surface it in the summary dict that build_report
# returns (alongside `cleaned`, `dropped`, `spliced`) - a build that omitted twelve
# of the twenty-one fields must not look identical to a complete one (request 15's
# draft holds nine, so that is the live case, not a hypothetical):
#
#     "draft": drafted,
#     "draft_missing": [k for k in drafted["missing"] if k not in WF.OPTIONAL],
#
# (with `from . import wizard_fields as WF` where you keep the other imports).
#
# THREE THINGS THE CALLER MUST STILL DECIDE - deliberately not done here:
#
# 1. What an unanswered field looks like. cleanup_instructions still turns the
#    placeholders in `drafted["missing"]` destinations into "NA", which is the
#    behaviour this module exists to make visible rather than fix by itself. The
#    honest options are to block generation while a required field is missing, or
#    to keep the visible <placeholder>. Either way it is now reported, not silent.
#
# 2. `_COVER_DEFAULTS` (builder.py:572-575) still prints "Permanent" for
#    LOCATION OF PERFORMANCE OF TEST when the admin left it blank. It is
#    placeholder-guarded, so a supplied `test_location` already wins - but it is
#    the same "looks decided, was guessed" defect, now for a field the wizard
#    asks about, and should probably be deleted.
#
# 3. The signature block's Date cells are written by fill_cover (builder.py:226)
#    from meta["issue_date"] = today. apply_draft rewrites only the cover's TEST
#    REPORT ISSUE DATE row, so an admin-entered issue date will differ from the
#    three signature dates. Signature is out of scope per the brief; if you want
#    them to agree, set data["meta"]["issue_date"] from the draft BEFORE
#    fill_cover (line 1280) and apply_draft's write becomes a no-op-equivalent.
#
# ALSO WORTH KNOWING: an empty image slot leaves its caption pointing at blank
# space, and those captions are real SEQ fields - so LIST OF FIGURES / LIST OF
# PHOTOS gain entries for pictures that are not there, and Figure 1 / Photo 1 / 2
# still consume numbers. The report has no `_drop_captionless_photos` equivalent
# (that lives in datasheet_gen/generic_generator.py:1060, called only from the
# datasheet render). Pruning a caption is a document-shape decision, not a value
# write, so it is not done here: `drafted["missing"]` names exactly which slots
# are empty if you add that pass.
