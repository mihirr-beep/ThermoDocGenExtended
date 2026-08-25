# -*- coding: utf-8 -*-
"""Word vs LibreOffice on the SAME report. Measures the divergence instead of
assuming it.

    python tools_render_engine_compare.py <report.docx>

WHY THIS EXISTS
---------------
Production is Linux, development is Windows, and the two hosts finish a report
differently. report_gen/finalise.py computes the table-of-contents page numbers
by driving Word over COM, which Linux cannot do; there it falls back to writing
the entry TEXT in pure Python and leaving the page numbers to whatever Word the
reader opens the file in.

LibreOffice can paginate, so it is the obvious candidate for closing that gap on
the server. The question nobody should answer from intuition is whether its
layout AGREES with Word's - because if it does not, freezing LibreOffice's page
numbers into a .docx that is then read in Word produces a contents page that is
confidently wrong, with nothing to signal it. That is worse than the prompt it
was meant to remove.

So this renders one document through both engines and reports:

  * total page count from each
  * the page each Heading-1 lands on, per engine, side by side
  * every heading where they disagree, and by how much

WHAT A RESULT MEANS
-------------------
Identical page counts and zero disagreements: LibreOffice is a safe substitute
for the finalise step on Linux, for documents shaped like this one.

Any disagreement: the PDF is still fine - it is self-consistent, the numbers in
it match its own pages - but page numbers computed by LibreOffice must NOT be
frozen into a .docx destined for Word. Keep w:updateFields and let the reader's
own Word compute them, which is what the Linux path already does.

Needs both engines present. On a host with only one it says so and stops rather
than reporting a comparison it did not make.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_WD_FORMAT_PDF = 17
_WD_ALERTS_NONE = 0


def _word_pdf(src, dst):
    """Render via Word over COM, with fields updated first so the TOC is real."""
    import pythoncom
    import win32com.client as win32
    pythoncom.CoInitialize()
    word = doc = None
    try:
        word = win32.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = _WD_ALERTS_NONE
        doc = word.Documents.Open(os.path.abspath(src), ReadOnly=False,
                                  AddToRecentFiles=False, Visible=False)
        doc.TrackRevisions = False
        doc.Fields.Update()
        for i in range(1, doc.TablesOfContents.Count + 1):
            doc.TablesOfContents(i).Update()
        for i in range(1, doc.TablesOfFigures.Count + 1):
            doc.TablesOfFigures(i).Update()
        doc.Repaginate()
        doc.ExportAsFixedFormat(OutputFileName=os.path.abspath(dst),
                                ExportFormat=_WD_FORMAT_PDF)
        return os.path.exists(dst)
    finally:
        try:
            if doc is not None:
                doc.Close(SaveChanges=0)
        except Exception:      # noqa: BLE001
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:      # noqa: BLE001
            pass
        pythoncom.CoUninitialize()


def _soffice_pdf(src, dst, timeout=300):
    """Render via soffice --headless, the exact call report_gen/render.py makes."""
    from report_gen.render import _soffice_path
    exe = _soffice_path()
    if not exe:
        return False
    with tempfile.TemporaryDirectory() as profile:
        outdir = os.path.dirname(os.path.abspath(dst)) or "."
        cmd = [exe, "--headless", "--norestore", "--invisible",
               "-env:UserInstallation=file:///%s"
               % profile.replace("\\", "/").lstrip("/"),
               "--convert-to", "pdf", "--outdir", outdir, os.path.abspath(src)]
        try:
            subprocess.run(cmd, check=True, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print("   soffice failed: %s" % exc)
            return False
    made = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + ".pdf")
    if os.path.abspath(made) != os.path.abspath(dst) and os.path.exists(made):
        os.replace(made, dst)
    return os.path.exists(dst)


def _pdf_pages(path):
    """Page count, read from the PDF itself rather than from either engine's word."""
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader          # older name
        except ImportError:
            return None
    try:
        return len(PdfReader(path).pages)
    except Exception:                              # noqa: BLE001
        return None


def _pdf_page_texts(path):
    """[text per page]. Used to find which page each heading actually landed on."""
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            return []
    try:
        return [(p.extract_text() or "") for p in PdfReader(path).pages]
    except Exception:                              # noqa: BLE001
        return []


def _headings(docx_path):
    """The document's Heading-1 texts, in order, from the .docx itself."""
    from docx import Document
    out = []
    for p in Document(docx_path).paragraphs:
        name = (p.style.name or "") if p.style else ""
        if name.startswith("Heading 1") and p.text.strip():
            out.append(p.text.strip())
    return out


def _norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip().upper()


# A contents entry carries dotted leaders and a page number; the real heading in
# the body does not. Matching on the heading text alone found it in the TABLE OF
# CONTENTS first - which is pages 2-7 of this report - so every section resolved
# to a contents page and the comparison measured nothing. A skip_pages count was
# the first fix and the wrong one: it is a guess about one document's front
# matter that silently breaks on the next.
_LEADERS_RE = re.compile(r"\.{3,}|\t\s*\d+\s*$")


def _page_of(page_texts, heading):
    """The page where this heading appears AS A HEADING, not as a contents entry.

    A body heading is its own line, optionally numbered ("4. CONDUCTED EMISSION
    TEST"), with nothing after it. That distinguishes it from the contents entry
    for the same section, which is the same words followed by dot leaders and a
    page number.
    """
    want = _norm(heading)
    if not want:
        return None
    for i, text in enumerate(page_texts):
        for raw in (text or "").split("\n"):
            line = raw.strip()
            if not line or _LEADERS_RE.search(line):
                continue                      # a contents entry, not the heading
            # drop a leading section number so "4. FOO" matches "FOO"
            bare = _norm(re.sub(r"^\s*\d+(?:\.\d+)*\.?\s*", "", line))
            if bare == want:
                return i + 1
    return None


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if len(sys.argv) < 2:
        print(__doc__.strip().split("\n")[2].strip())
        return 2
    src = os.path.abspath(sys.argv[1])
    if not os.path.exists(src):
        print("no such file: %s" % src)
        return 2

    from report_gen.render import _soffice_path, _word_available
    have_word, have_lo = _word_available(), bool(_soffice_path())
    print("Word: %s    LibreOffice: %s" % (have_word, _soffice_path() or False))
    if not (have_word and have_lo):
        print("\nBoth engines are needed to compare them. Nothing measured.")
        return 1

    work = tempfile.mkdtemp(prefix="engine_cmp_")
    # a copy each, so neither engine's field update is seen by the other
    w_src = os.path.join(work, "for_word.docx")
    l_src = os.path.join(work, "for_lo.docx")
    shutil.copy(src, w_src)
    shutil.copy(src, l_src)
    w_pdf = os.path.join(work, "word.pdf")
    l_pdf = os.path.join(work, "lo.pdf")

    print("\nrendering through Word ...")
    ok_w = _word_pdf(w_src, w_pdf)
    print("   %s" % ("ok" if ok_w else "FAILED"))
    print("rendering through LibreOffice ...")
    ok_l = _soffice_pdf(l_src, l_pdf)
    print("   %s" % ("ok" if ok_l else "FAILED"))
    if not (ok_w and ok_l):
        print("\nOne engine did not produce a PDF. Nothing to compare.")
        return 1

    pw, pl = _pdf_pages(w_pdf), _pdf_pages(l_pdf)
    print()
    print("=" * 74)
    print("PAGE COUNT   Word: %s    LibreOffice: %s%s"
          % (pw, pl, "" if pw == pl else "     <<< DIFFERENT"))
    if pw is None:
        print("(install pypdf to get page counts and the per-heading comparison)")
        print("PDFs kept at: %s" % work)
        return 0

    wt, lt = _pdf_page_texts(w_pdf), _pdf_page_texts(l_pdf)
    heads = _headings(src)
    print()
    print("%-52s %6s %6s %s" % ("HEADING", "Word", "LO", ""))
    print("-" * 74)
    disagree = unfound = 0
    for h in heads:
        a, b = _page_of(wt, h), _page_of(lt, h)
        if a is None or b is None:
            unfound += 1
            note = "  (not located in one of the PDFs)"
        elif a != b:
            disagree += 1
            note = "  <<< off by %+d" % (b - a)
        else:
            note = ""
        print("%-52s %6s %6s%s" % (h[:52], a, b, note))
    print("-" * 74)
    print("%d heading(s): %d agree, %d disagree, %d not located"
          % (len(heads), len(heads) - disagree - unfound, disagree, unfound))
    print()
    if disagree == 0 and pw == pl:
        print("VERDICT  The two engines agree on this document. LibreOffice is a")
        print("         safe substitute for the finalise step for reports shaped")
        print("         like this one.")
    else:
        print("VERDICT  The engines DISAGREE. The LibreOffice PDF is still fine -")
        print("         it is self-consistent - but page numbers computed by")
        print("         LibreOffice must not be frozen into a .docx that will be")
        print("         opened in Word. Keep w:updateFields on the Linux path.")
    print()
    print("PDFs kept for inspection: %s" % work)
    return 0


if __name__ == "__main__":
    sys.exit(main())
