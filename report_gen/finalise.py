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
    """Compute the fields and stop Word re-doing it. Returns True if finalised."""
    if not compute_fields(path):
        return False
    clear_update_on_open(path)
    return True
