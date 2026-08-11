# -*- coding: utf-8 -*-
"""Compute the report's fields on the server, so Word never has to.

WHY
---
The builder blanks the template's cached table-of-contents entries (they belong
to a different 40-page document) and sets ``w:updateFields`` so Word rebuilds
them on open. That works, and it has three costs the reader pays:

  * "This document contains fields that may refer to other files" on open;
  * "Update Table of Figures - page numbers only / entire table", four times,
    where the harmless-looking default leaves the lists BLANK because the
    cached entries were deliberately cleared;
  * and the one that actually breaks the deliverable: asking Word to modify the
    document on open means the modification can be RECORDED. With track changes
    active the rebuild lands as revisions - a contents page in red underline, and
    "Field Code Changed" balloons in a markup margin down the side of the page.

No amount of cleaning the file prevents that third one, because the file is
clean: the balloons are created after opening, by Word, out of the rebuild we
asked for. The fix is to stop asking. Compute the fields here, drop the
``w:updateFields`` flag, and the document that reaches the reader is finished -
no prompts, no rebuild, nothing to record.

HOW
---
Word itself does the layout, driven over COM, with revision recording turned
off first and any existing revisions accepted. Best-effort by design: on a host
without Word or pywin32 this returns False and the caller keeps the previous
behaviour, which is correct but chatty. Never raises into the request.
"""
import logging
import os

log = logging.getLogger(__name__)

# Word constants (we avoid importing the type library just for these)
_WD_ALERTS_NONE = 0
_WD_DO_NOT_SAVE = 0
_WD_FORMAT_XML_DOCUMENT = 12       # .docx


def available():
    """True when this host can drive Word."""
    if os.name != "nt":
        return False
    try:
        import win32com.client  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


# Said once per process, not once per report, so it is visible in the boot log
# without burying every generation behind it.
_warned = [False]


def warn_if_unavailable():
    """Say plainly that reports will ship un-computed on this host.

    PRODUCTION IS LINUX WITH PYTHON AND NOTHING ELSE - no Word, no LibreOffice.
    So this module is a no-op there, and that is not a detail: it means the
    report keeps w:updateFields, every reader gets the "update the fields?"
    prompt and the four "page numbers only / entire table" prompts, and if their
    Word has track changes on, its field rebuild lands as revisions - the red
    contents page and the balloon margin.

    Computing them server-side needs a layout engine to know what page anything
    falls on. Python has none, so on Linux this cannot be fixed by trying
    harder; it needs either a Windows/Word or LibreOffice host for this one step,
    or the prompts are accepted as the cost of a Linux-only stack.

    Returning False silently was the worse option: it looked like the problem
    had been fixed everywhere when it had been fixed on one developer's laptop.
    """
    if available() or _warned[0]:
        return
    _warned[0] = True
    log.warning(
        "report fields will NOT be computed on this host (%s, no Word) - reports "
        "keep updateFields, so readers get the field prompts and, with track "
        "changes on, a markup margin. See report_gen/finalise.py.", os.name)


def compute_fields(path, timeout_hint=120):
    """Open ``path`` in Word, compute every field, save, close.

    Returns True when the document was finalised. On any failure the file is
    left exactly as the builder wrote it - a report with un-computed fields is
    worse to read but still correct, and losing the report entirely is not an
    acceptable trade for tidier page numbers.
    """
    if not available():
        return False

    path = os.path.abspath(path)
    if not os.path.exists(path):
        return False

    import pythoncom
    import win32com.client as win32

    pythoncom.CoInitialize()
    word = doc = None
    try:
        # DispatchEx, not Dispatch: a private instance, so we never attach to -
        # or quit - a Word the user has open with unsaved work in it.
        word = win32.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = _WD_ALERTS_NONE
        # Stop Word recording OUR field rebuild as somebody's edits. This is the
        # setting that produced the red contents page and the balloon margin.
        try:
            word.Options.SaveNormalPrompt = False
        except Exception:  # noqa: BLE001 - not present on every build
            pass

        doc = word.Documents.Open(path, ReadOnly=False, AddToRecentFiles=False,
                                  Visible=False, ConfirmConversions=False)

        doc.TrackRevisions = False
        try:
            if doc.Revisions.Count:
                doc.Revisions.AcceptAll()
        except Exception:  # noqa: BLE001
            pass
        # documentProtection would refuse the edits below; the builder already
        # strips it, this is belt and braces for a hand-edited template.
        try:
            if doc.ProtectionType != -1:      # wdNoProtection
                doc.Unprotect()
        except Exception:  # noqa: BLE001
            pass

        # Order matters. Fields first so captions and SEQ numbers settle,
        # then the tables that INDEX them, then repaginate so PAGE / NUMPAGES
        # and every PAGEREF resolve against the final layout, then fields once
        # more to pick those page numbers up.
        doc.Fields.Update()
        for i in range(1, doc.TablesOfContents.Count + 1):
            doc.TablesOfContents(i).Update()
        for i in range(1, doc.TablesOfFigures.Count + 1):
            doc.TablesOfFigures(i).Update()
        doc.Repaginate()
        doc.Fields.Update()

        pages = int(doc.ComputeStatistics(2))          # wdStatisticPages
        doc.Save()
        doc.Close(SaveChanges=_WD_DO_NOT_SAVE)
        doc = None
        log.info("report finalised in Word: %s (%d pages)",
                 os.path.basename(path), pages)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("could not finalise %s in Word (%s) - leaving the "
                    "update-on-open flag in place", os.path.basename(path), exc)
        return False
    finally:
        try:
            if doc is not None:
                doc.Close(SaveChanges=_WD_DO_NOT_SAVE)
        except Exception:  # noqa: BLE001
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:  # noqa: BLE001
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass


def clear_update_on_open(path):
    """Remove ``w:updateFields`` once the fields are already computed.

    Without this Word still asks "do you want to update the fields" on every
    open, and answering yes starts the whole rebuild - and the recording - over
    again.
    """
    try:
        from docx import Document
        from docx.oxml.ns import qn
        doc = Document(path)
        settings = doc.settings.element
        removed = 0
        for node in settings.findall(qn("w:updateFields")):
            settings.remove(node)
            removed += 1
        if removed:
            doc.save(path)
        return removed
    except Exception as exc:  # noqa: BLE001
        log.warning("could not clear updateFields on %s: %s",
                    os.path.basename(path), exc)
        return 0


def finalise(path):
    """Finish the report as well as this host can. Returns what was done.

    Windows with Word: everything, including the page numbers, and the
    update-on-open flag is dropped so the reader gets no prompts at all.

    Anywhere else: the contents entries and caption numbers are written in pure
    Python and updateFields is LEFT in place, so a reader who says yes still gets
    perfect page numbers and a reader who says no still gets a readable document.
    """
    warn_if_unavailable()
    if compute_fields(path):
        clear_update_on_open(path)
        return {"engine": "word", "page_numbers": True}
    info = populate_lists(path)
    info.update({"engine": "python", "page_numbers": False})
    return info


# ==========================================================================
# The Linux path: everything that does not need a layout engine
# ==========================================================================
# compute_fields needs Word, so on the production host it does nothing and the
# report ships with blanked contents lists - clear_toc_entries empties the
# template's stale entries and, without Word, nothing puts real ones back.
#
# Page numbers genuinely cannot be computed here. Knowing that a heading falls on
# page 31 means laying the document out, and Python has no engine for that.
# Everything ELSE about those lists is countable from the document itself: which
# headings exist, at what level, in what order, and how many figures precede a
# given caption.
#
# So this writes the entry TEXT and the caption numbers and leaves only the page
# number blank. Two things follow, and the second is the point:
#
#   * a reader who clicks Yes gets a perfect document, exactly as before;
#   * a reader who clicks No now gets a READABLE contents page instead of a blank
#     one - and because Word then never modifies the document, there is nothing
#     for it to record. The red contents page and the balloon margin cannot
#     happen on that path at all.
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _q(tag):
    return _W + tag


def _text_of(el):
    return "".join(t.text or "" for t in el.iter(_q("t"))).strip()


def _style_of(el):
    ppr = el.find(_q("pPr"))
    if ppr is None:
        return ""
    st = ppr.find(_q("pStyle"))
    return (st.get(_q("val")) or "") if st is not None else ""


def _collect_entries(doc):
    """What each of the four lists should hold, in document order.

    Heading numbers are computed from level counters rather than read off the
    paragraph: the body numbers its headings through list definitions, so the
    text does not carry "4.2." anywhere.
    """
    out = {"TOC": [], "Figure": [], "Photo": [], "Table": []}
    counters = [0, 0, 0]
    seen = {"Figure": 0, "Photo": 0, "Table": 0}
    for el in doc.element.body.iter(_q("p")):
        style = _style_of(el).lower()
        text = _text_of(el)
        if not text:
            continue
        if style.startswith("heading") and style[-1:].isdigit():
            lvl = int(style[-1])
            if 1 <= lvl <= 3:
                counters[lvl - 1] += 1
                for deeper in range(lvl, 3):
                    counters[deeper] = 0
                number = ".".join(str(counters[i]) for i in range(lvl)) + "."
                out["TOC"].append((number, text))
            continue
        if style == "caption":
            for label in ("Figure", "Photo", "Table"):
                if not text.startswith(label):
                    continue
                seen[label] += 1
                tail = text[len(label):].lstrip()
                tail = tail.split(":", 1)[1].strip() if ":" in tail else tail
                out[label].append(("%s %d:" % (label, seen[label]), tail))
                break
    return out


def number_seq_fields(doc):
    """Set each SEQ field's cached number by counting them in document order.

    Word would compute these. Without Word the cached value is whatever the
    source document carried, so a spliced "Photo 1" stays 1 even when it is the
    ninth photo in the report.
    """
    counts, changed = {}, 0
    for p in doc.element.body.iter(_q("p")):
        runs = list(p)
        for i, r in enumerate(runs):
            instr = "".join(t.text or "" for t in r.iter(_q("instrText")))
            if "SEQ" not in instr:
                continue
            parts = instr.split()
            if "SEQ" not in parts:
                continue
            label = parts[parts.index("SEQ") + 1] if len(parts) > parts.index("SEQ") + 1 else ""
            if not label:
                continue
            counts[label] = counts.get(label, 0) + 1
            for later in runs[i:]:
                fc = later.find(_q("fldChar"))
                if fc is not None and fc.get(_q("fldCharType")) == "end":
                    break
                done = False
                for t in later.iter(_q("t")):
                    if (t.text or "").strip().isdigit():
                        t.text = str(counts[label])
                        changed += 1
                        done = True
                        break
                if done:
                    break
    return changed


def _regions(doc):
    """[(kind, [paragraphs of the field])] for the four TOC-family fields."""
    paras = list(doc.element.body.iter(_q("p")))
    out, depth, cur = [], 0, None
    for i, el in enumerate(paras):
        instr = " ".join("".join(t.text or "" for t in el.iter(_q("instrText"))).split())
        for fc in el.iter(_q("fldChar")):
            typ = fc.get(_q("fldCharType"))
            if typ == "begin":
                depth += 1
                if depth == 1 and instr.startswith("TOC"):
                    kind = "TOC"
                    for label in ("Figure", "Photo", "Table"):
                        if '"%s"' % label in instr:
                            kind = label
                    cur = (kind, i)
            elif typ == "end":
                if depth == 1 and cur is not None:
                    out.append((cur[0], paras[cur[1]:i + 1]))
                    cur = None
                depth = max(0, depth - 1)
    return out


def _write_entry(p, number, text):
    """Rewrite one contents paragraph as number, tab, text, tab.

    The field machinery - the outer TOC chars and the nested PAGEREF - is kept
    exactly as it was, so Word can still rebuild the list and fill the page in.
    Only the visible text is replaced, which is the same surgery
    docx_tools.clear_toc_entries already does to blank it.
    """
    import copy
    from docx.oxml import OxmlElement
    rpr = None
    for r in list(p):
        if r.tag != _q("r"):
            continue
        if r.find(_q("fldChar")) is not None or r.find(_q("instrText")) is not None:
            continue
        if rpr is None:
            found = r.find(_q("rPr"))
            if found is not None:
                rpr = copy.deepcopy(found)
        p.remove(r)
    for link in p.findall(_q("hyperlink")):
        for t in link.iter(_q("t")):
            t.text = ""

    run = OxmlElement("w:r")
    if rpr is not None:
        run.append(rpr)
    t = OxmlElement("w:t")
    t.set(_XML_SPACE, "preserve")
    t.text = "%s\t%s\t" % (number, text)
    run.append(t)

    ppr = p.find(_q("pPr"))
    if ppr is not None:
        ppr.addnext(run)
    else:
        p.insert(0, run)


def populate_lists(path):
    """Write the contents entries and caption numbers without Word.

    Best-effort: any failure leaves the file exactly as the builder wrote it,
    which is the behaviour this replaces.
    """
    try:
        from docx import Document
        doc = Document(path)
        seq = number_seq_fields(doc)
        entries = _collect_entries(doc)
        written = dropped = 0
        for kind, paras in _regions(doc):
            want = entries.get(kind) or []
            # only paragraphs with no field machinery may be rewritten or removed;
            # the first and last of a region carry the field and must survive
            slots = [p for p in paras
                     if p.find(_q("fldChar")) is None
                     and p.find(_q("instrText")) is None]
            for i, p in enumerate(slots):
                if i < len(want):
                    _write_entry(p, want[i][0], want[i][1])
                    written += 1
                else:
                    parent = p.getparent()
                    if parent is not None:
                        parent.remove(p)
                        dropped += 1
        doc.save(path)
        log.info("report lists written without Word: %d entries, %d caption "
                 "numbers, %d surplus lines removed (page numbers left to Word)",
                 written, seq, dropped)
        return {"entries_written": written, "captions_numbered": seq,
                "surplus_removed": dropped}
    except Exception as exc:  # noqa: BLE001
        log.warning("could not write the report lists for %s: %s",
                    os.path.basename(path), exc)
        return {"entries_written": 0, "captions_numbered": 0, "surplus_removed": 0}
