# -*- coding: utf-8 -*-
"""Turn the finished .docx into a PDF the admin can actually look at.

WHY A RENDERER AND NOT A PURE-PYTHON CONVERTER
----------------------------------------------
Measured, on this project's own 25-page report. aspose-words-foss (MIT, pip
installable, no system dependency - the obvious candidate) produced a plausible
26-page PDF and dropped every measured value in it: 60.8, 57.9, 52.6, 0.720,
56.0, and both uncertainty figures were absent. The table of contents read "1.
SURGE IMMUNITY TEST" for every section and the header printed the literal field
code, "PAGE: PAGE of 40".

A preview that silently omits the numbers is worse than no preview, because the
admin signs off on what they were shown. So this uses a real layout engine or it
does not pretend:

    Windows  Word via COM      - already a dependency, finalise.py uses it
    Linux    soffice --headless - one system package, Word-compatible layout

Both are the same call shape, so the wizard asks for a PDF and does not care
which ran. If neither is present, available() says so and the caller shows the
completeness check instead of a picture - see the note at the bottom.
"""
import logging
import os
import shutil
import subprocess
import tempfile

log = logging.getLogger(__name__)

_WD_FORMAT_PDF = 17
_WD_ALERTS_NONE = 0


def _word_available():
    # REPORT_DISABLE_WORD=1 also steers the PDF preview to LibreOffice on a
    # machine that has both, which is the only way to see on a dev box what the
    # Linux host will actually render. Same flag as finalise.available().
    if os.environ.get("REPORT_DISABLE_WORD"):
        return False
    if os.name != "nt":
        return False
    try:
        import win32com.client  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _soffice_path():
    """LibreOffice, if it is on PATH or in one of the usual places."""
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for guess in ("/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice",
                  "/opt/libreoffice/program/soffice",
                  r"C:\Program Files\LibreOffice\program\soffice.exe"):
        if os.path.exists(guess):
            return guess
    return None


def backend():
    """Which engine will be used: "word", "soffice" or None."""
    if _word_available():
        return "word"
    if _soffice_path():
        return "soffice"
    return None


def available():
    return backend() is not None


def _via_word(src, dst):
    import pythoncom
    import win32com.client as win32

    pythoncom.CoInitialize()
    word = doc = None
    try:
        # DispatchEx for the same reason finalise.py uses it: a private instance,
        # so this never attaches to - or quits - a Word the user has open with
        # unsaved work in it.
        word = win32.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = _WD_ALERTS_NONE
        doc = word.Documents.Open(src, ReadOnly=True, AddToRecentFiles=False,
                                  Visible=False, ConfirmConversions=False)
        # ReadOnly above matters: exporting must not be able to modify the
        # report. The builder already computed and froze the fields; if a field
        # is still live, Word may recompute it for the export, and a PDF that
        # disagrees with the .docx is a preview nobody can trust.
        doc.ExportAsFixedFormat(OutputFileName=dst, ExportFormat=_WD_FORMAT_PDF,
                                OpenAfterExport=False)
        return os.path.exists(dst)
    finally:
        try:
            if doc is not None:
                doc.Close(SaveChanges=False)
        except Exception:  # noqa: BLE001
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:  # noqa: BLE001
            pass
        pythoncom.CoUninitialize()


def _via_soffice(src, dst, timeout=180):
    exe = _soffice_path()
    if not exe:
        return False
    # Its own profile directory per call. Without it a second conversion while
    # one is running silently reuses the first profile and can hang forever,
    # which on a web request means a worker held until timeout.
    with tempfile.TemporaryDirectory() as profile:
        outdir = os.path.dirname(dst) or "."
        cmd = [exe, "--headless", "--norestore", "--invisible",
               "-env:UserInstallation=file:///%s" % profile.replace("\\", "/").lstrip("/"),
               "--convert-to", "pdf", "--outdir", outdir, src]
        try:
            subprocess.run(cmd, check=True, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.error("soffice conversion failed: %s", exc)
            return False
    produced = os.path.join(outdir,
                            os.path.splitext(os.path.basename(src))[0] + ".pdf")
    if produced != dst and os.path.exists(produced):
        os.replace(produced, dst)
    return os.path.exists(dst)


def to_pdf(docx_path, pdf_path=None):
    """Render ``docx_path`` to PDF. Returns the path, or None.

    Returns None rather than raising: a preview is a convenience, and failing to
    produce one must never stop the admin downloading the report itself.
    """
    src = os.path.abspath(docx_path)
    if not os.path.exists(src):
        return None
    dst = os.path.abspath(pdf_path or os.path.splitext(src)[0] + ".pdf")
    which = backend()
    try:
        ok = _via_word(src, dst) if which == "word" else (
            _via_soffice(src, dst) if which == "soffice" else False)
    except Exception as exc:  # noqa: BLE001
        log.error("pdf render (%s) failed for %s: %s", which, src, exc)
        return None
    if not ok:
        return None
    log.info("rendered %s -> %s via %s", os.path.basename(src),
             os.path.basename(dst), which)
    return dst


def unavailable_note():
    """What to tell the admin when there is no engine, instead of a bad preview.

    Deliberately not a fallback conversion. The tested pure-Python converter
    lost every measured value while still producing 26 pages that looked right,
    so a degraded preview here would be actively misleading. The wizard shows
    the completeness check in this case: which fields are filled, which are
    outstanding, which tests are included. That catches DATA problems better
    than looking at pages does.
    """
    return ("No PDF preview on this server - no Word and no LibreOffice. "
            "Install libreoffice-writer to enable it. The completeness check "
            "below lists exactly what the report will and will not contain.")
