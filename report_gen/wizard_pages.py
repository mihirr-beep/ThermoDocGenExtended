# -*- coding: utf-8 -*-
"""Pages 2, 3 and 4 of the report wizard: review, tests, generate.

WHY THESE PAGES EXIST AT ALL
----------------------------
Page 1 collects the fields nothing else supplies. These three show
what the admin is about to sign their name under: what section 1 will actually
say, which datasheet each per-test section will be built from, and whether the
build can succeed. Sections 1 and 3 and everything from 4 onward are derived, so
they are read-only here on purpose - an editable copy would let the report
disagree with what peer review approved.

NOTHING HERE GENERATES ANYTHING
-------------------------------
The Generate button posts to the report endpoint that already exists,
``POST /api/test-requests/<id>/generate-test-report``. That endpoint is
repeatable and has no status gate of its own, and it is the one place that writes
report_file_path, routes the request into the approval flow and sends the
completion mail for a Developmental Assistance request. A second generate path
would have to keep all of that in step, and would not.

RESUMABLE
---------
``report_draft.page_reached`` is "furthest reached", so ``/resume`` sends someone
who stopped on Thursday back to where they were. The page counter is written only
when it moves forward: this database is remote and a write on every page view is
four round trips to record something that has not changed.
"""
import logging
import os

from flask import (Blueprint, jsonify, redirect, render_template, send_file,
                   url_for)
from flask_login import current_user, login_required

from . import draft
from . import render as R
from . import wizard_fields as WF
from . import wizard_review as WR
# The admin check is imported, never copied: two copies of "who may prepare a
# report" is one copy too many, and this is the check page 1 already enforces.
from .wizard_routes import _can_admin

log = logging.getLogger(__name__)

report_wizard_pages_bp = Blueprint("report_wizard_pages", __name__)

# The fixed steps, in the order the REPORT reads: section 1, then section 2, then
# the per-test overview. The tests themselves are inserted after these, one step
# each, and Generate is always last - see _pages().
#
# Section 1 comes before section 2 because the document reads that way. The
# data-entry page was first because it is where the work is, but that put the
# admin in section 2 before they had seen section 1, and numbered the report's own
# sections backwards.
_FIXED_STEPS = (
    ("summary", "Report summary", "report_wizard_pages.summary_page"),
    ("eut", "EUT information", "report_wizard.eut_page"),
    ("tests", "Tests", "report_wizard_pages.tests_page"),
)


def _deny():
    """The same 403 body page 1 returns, so the fetch handlers can share it."""
    return jsonify(success=False,
                   message="Only an admin can prepare a test report"), 403


def _test_steps(request_id, data=None):
    """One step per test on this request, in the order the report prints them.

    ``data`` is an already-collected S.collect() payload. The EUT page builds one
    for its cover preview and would otherwise pay for a second full collect just
    to label its step chips - and this database is remote, where the cost that
    matters is round trips.

    Read from wizard_review.preview_source, which is the same S.collect() the
    build makes - so the tabs are exactly the sections the document will contain,
    and a test that resolves to no section cannot appear as a tab that leads
    nowhere.

    Each step carries the URL of the test's OWN datasheet form: the page the lab
    engineer fills in. Nothing about that form is reimplemented here.
    """
    out = []
    try:
        if data is None:
            _req, _entries, data = WR.preview_source(request_id)
        if data is None:
            return out
        for t in data["tests"]:
            entry = t.get("entry")
            entry_id = getattr(entry, "id", None)
            if entry_id is None:
                continue
            out.append({"code": t["code"], "name": t["name"],
                        "section": t["section"], "entry_id": entry_id,
                        "has_data": bool(t.get("has_data"))})
    except Exception as exc:  # noqa: BLE001 - a missing tab must not 500 the page
        log.info("wizard: per-test steps unavailable for request %s: %s",
                 request_id, exc)
    return out


def datasheet_form_url(code, entry_id, readonly=True):
    """The URL of the datasheet form for one test - the engineer's own page.

    CE is the bespoke form with its own blueprint and route; the other ten share
    the generic one. Resolved with url_for so a route rename cannot silently break
    the embed, and returns None rather than a guess when neither endpoint exists.

    ``readonly`` adds ?view=1, and defaults to it. A datasheet is only meaningful
    to this report once it has gone Draft -> Peer Review -> Approved, and the
    report is built from the approved document; an admin editing or re-submitting
    from inside the report wizard would change a reviewed record outside that
    pipeline. The wizard shows the datasheet, it does not edit it - and the
    "Open in a new tab" link goes to the editable form, where such a change
    belongs and is logged as the engineer's own action.
    """
    code = (code or "").upper()
    try:
        if code == "CE":
            url = url_for("datasheet_gen.ce_form", assignment_id=int(entry_id))
        else:
            url = url_for("datasheet_generic.g_form", code=code.lower(),
                          assignment_id=int(entry_id))
    except Exception as exc:  # noqa: BLE001
        log.info("wizard: no datasheet form URL for %s/%s: %s", code, entry_id, exc)
        return None
    if not readonly:
        return url
    # view=1 locks the fields and hides the action bar; embed=1 drops the app
    # chrome so the frame shows the datasheet and not a second copy of the navbar.
    return url + ("&" if "?" in url else "?") + "view=1&embed=1"


def _pages(request_id, data=None):
    """Every wizard step for this request, in order, numbered from 1.

    DYNAMIC BY DESIGN. The fixed steps are followed by one per test and then
    Generate, so a request with four tests has eight steps and one with eleven has
    fifteen. Everything that needs to know the order - the chips, Back/Next and
    /resume - reads this one list, which is why adding the per-test steps did not
    mean touching four templates.
    """
    pages = []
    for kind, label, endpoint in _FIXED_STEPS:
        pages.append({"kind": kind, "label": label, "code": None,
                      "url": url_for(endpoint, request_id=request_id)})
    for t in _test_steps(request_id, data=data):
        pages.append({
            "kind": "test", "label": t["code"], "code": t["code"],
            "name": t["name"], "entry_id": t["entry_id"],
            "has_data": t["has_data"],
            "url": url_for("report_wizard_pages.test_page",
                           request_id=request_id, code=t["code"].lower())})
    pages.append({"kind": "generate", "label": "Generate", "code": None,
                  "url": url_for("report_wizard_pages.generate_page",
                                 request_id=request_id)})
    for i, p in enumerate(pages, 1):
        p["n"] = i
    return pages


def _mark_page(request_id, page):
    """Record how far the admin got. Returns the loaded draft.

    Only writes when the page number actually advances. draft.save() already
    refuses to move page_reached backwards, but calling it anyway would cost four
    round trips per page view on a remote database to store an unchanged value.
    """
    d = draft.load(request_id)
    if int(d.get("page") or 1) < int(page):
        draft.save(request_id, page=int(page),
                   user_id=getattr(current_user, "id", None))
    return d


def _nav(request_id, page, pages=None, data=None):
    """Back/next targets and the step chips, so no template hardcodes an endpoint.

    ``page`` is a 1-based index into _pages(). Callers that already built the list
    pass it in - or pass the collected ``data`` - rather than paying for a second
    S.collect().
    """
    pages = pages if pages is not None else _pages(request_id, data=data)
    steps = [{"n": p["n"], "label": "%d. %s" % (p["n"], p["label"]),
              "current": p["n"] == page, "url": p["url"]} for p in pages]
    by_n = {p["n"]: p for p in pages}
    return {
        "steps": steps,
        "back": by_n[page - 1]["url"] if (page - 1) in by_n else None,
        "next": by_n[page + 1]["url"] if (page + 1) in by_n else None,
    }


def _generate_url(request_id):
    """The existing endpoint's URL.

    Resolved by endpoint name so a route change cannot silently break this
    button, with the literal path as a fallback because the endpoint is declared
    inside app.create_app and a template render outside a full app would
    otherwise raise.
    """
    try:
        return url_for("generate_test_report", request_id=request_id)
    except Exception as exc:  # noqa: BLE001
        log.info("wizard pages: url_for(generate_test_report) failed: %s", exc)
        return "/api/test-requests/%d/generate-test-report" % int(request_id)


# ==========================================================================
# page 2 - section 1, read only
# ==========================================================================

@report_wizard_pages_bp.route("/report/wizard/<int:request_id>/summary",
                              methods=["GET"])
@login_required
def summary_page(request_id):
    """What section 1 of the report will contain."""
    if not _can_admin():
        return _deny()
    try:
        data = WR.summary_preview(request_id)
    except Exception as exc:  # noqa: BLE001 - show the page, say what broke
        log.error("wizard summary preview failed for request %s: %s",
                  request_id, exc)
        data = {"ok": False, "reason": "The preview could not be assembled: %s" % exc}
    if not data.get("ok") and data.get("reason") == "Request not found":
        return jsonify(success=False, message="Request not found"), 404
    _mark_page(request_id, 1)
    return render_template("report_wizard_summary.html",
                           request_id=request_id, preview=data,
                           nav=_nav(request_id, 1))


# ==========================================================================
# page 3 - section 3's criteria note and the per-test list
# ==========================================================================

@report_wizard_pages_bp.route("/report/wizard/<int:request_id>/tests",
                              methods=["GET"])
@login_required
def tests_page(request_id):
    """Which datasheet each per-test section is built from, and how."""
    if not _can_admin():
        return _deny()
    try:
        data = WR.tests_preview(request_id)
    except Exception as exc:  # noqa: BLE001
        log.error("wizard tests preview failed for request %s: %s", request_id, exc)
        data = {"ok": False, "reason": "The preview could not be assembled: %s" % exc}
    if not data.get("ok") and data.get("reason") == "Request not found":
        return jsonify(success=False, message="Request not found"), 404
    _mark_page(request_id, 3)
    return render_template("report_wizard_tests.html",
                           request_id=request_id, preview=data,
                           nav=_nav(request_id, 3))


# ==========================================================================
# one page per test - the engineer's own datasheet form, embedded
# ==========================================================================

@report_wizard_pages_bp.route("/report/wizard/<int:request_id>/test/<code>",
                              methods=["GET"])
@login_required
def test_page(request_id, code):
    """One test's section, shown as THE DATASHEET FORM THE LAB ENGINEER USES.

    WHY AN IFRAME AND NOT A COPY OF THE FORM
    ----------------------------------------
    The per-test sections of this report already drifted once: the report template
    kept its own copy of the datasheet's tables and 49 of 92 subsections had
    quietly diverged by the time anyone measured. That is what splicing the
    approved .docx fixed.

    Re-rendering the datasheet FORM inside this page would repeat the same
    mistake in HTML - two thousand lines of grids, checkbox rows and per-test
    JavaScript, kept in step by hand. Instead the real form is embedded at its own
    URL: /datasheet/g/<code>/<id>/form, or /datasheet/ce/<id>/form for CE. There is
    one implementation, and a change an engineer makes to the datasheet UI shows up
    here on the next page load with nothing to update.

    ACCESS: the embedded route enforces its own permission check
    (datasheet_gen.routes._can_access), which admits any admin - the same people
    this wizard already admits. Nothing is loosened to make the embed work.

    THE FIRST AND LAST PARTS ARE LEFT ALONE. The datasheet's front matter (job
    number, EUT details) and its sign-off block are visible in the form because
    that is the form, but they never reach the report: splice.py lifts only the
    test's own section out of the approved .docx. Hiding them here would mean
    editing the embedded page, which is exactly the coupling this avoids.
    """
    if not _can_admin():
        return _deny()
    pages = _pages(request_id)
    want = (code or "").upper()
    page = next((p for p in pages
                 if p["kind"] == "test" and p["code"] == want), None)
    if page is None:
        # A code that is not a test on this request, or a request with no tests.
        # Redirect rather than 404: the tab list is built from the same source, so
        # this is only reachable by a hand-typed or stale URL.
        log.info("wizard: %s is not a test on request %s", want, request_id)
        return redirect(url_for("report_wizard_pages.tests_page",
                                request_id=request_id))
    _mark_page(request_id, page["n"])
    return render_template(
        "report_wizard_test.html",
        request_id=request_id, nav=_nav(request_id, page["n"], pages),
        test=page,
        form_url=datasheet_form_url(page["code"], page["entry_id"]),
        # The editable form, for the "open in a new tab" link. Changing a
        # datasheet is the engineer's action in their own pipeline, not something
        # to be done inside a report the wizard is about to generate.
        edit_url=datasheet_form_url(page["code"], page["entry_id"],
                                    readonly=False))


# ==========================================================================
# the last page - readiness, preview, generate
# ==========================================================================

@report_wizard_pages_bp.route("/report/wizard/<int:request_id>/generate",
                              methods=["GET"])
@login_required
def generate_page(request_id):
    """The last page: is it ready, what is missing, and the Generate button."""
    if not _can_admin():
        return _deny()
    try:
        state = WR.readiness(request_id)
    except Exception as exc:  # noqa: BLE001
        log.error("wizard readiness failed for request %s: %s", request_id, exc)
        # Not ready is the safe answer when the check itself failed: offering
        # Generate on a page that could not evaluate its own preconditions is how
        # someone ships a report built from data nobody verified.
        state = {"ok": False, "ready": False, "outstanding": [], "warnings": [],
                 "blockers": ["The readiness check could not run: %s" % exc],
                 "pdf_available": R.available(),
                 "pdf_note": None if R.available() else R.unavailable_note()}
    if state.get("reason") == "Request not found":
        return jsonify(success=False, message="Request not found"), 404
    # Generate is the LAST step, and how many there are depends on how many
    # tests the request has - four tests make it step 8, eleven make it 15.
    pages = _pages(request_id)
    last = len(pages)
    if int(state.get("page_reached") or 1) < last:
        draft.save(request_id, page=last,
                   user_id=getattr(current_user, "id", None))
    return render_template(
        "report_wizard_generate.html",
        request_id=request_id, state=state,
        nav=_nav(request_id, last, pages),
        # {key: label} so the page can name the pictures that were uploaded
        # instead of printing the stored filenames at the reader.
        image_labels={f[0]: f[1] for f in WF.FIELDS if f[2] == "image"},
        generate_url=_generate_url(request_id),
        pdf_url=url_for("report_wizard_pages.preview_pdf", request_id=request_id),
        fields_total=len(WF.FIELDS))


# ==========================================================================
# navigation
# ==========================================================================

@report_wizard_pages_bp.route("/report/wizard/<int:request_id>", methods=["GET"])
@report_wizard_pages_bp.route("/report/wizard/<int:request_id>/resume",
                              methods=["GET"])
@login_required
def resume(request_id):
    """Send the admin back to the furthest page they reached."""
    if not _can_admin():
        return _deny()
    pages = _pages(request_id)
    page = int((draft.load(request_id) or {}).get("page") or 1)
    # Clamped to what EXISTS now. A draft saved when the request had six tests
    # would otherwise resume past the end after one was cancelled.
    page = min(max(page, 1), len(pages))
    return redirect(pages[page - 1]["url"])


# ==========================================================================
# PDF preview of the report that already exists
# ==========================================================================

@report_wizard_pages_bp.route("/report/wizard/<int:request_id>/preview.pdf",
                              methods=["GET"])
@login_required
def preview_pdf(request_id):
    """Render the last generated .docx to PDF and serve it.

    The LAST GENERATED one, deliberately - there is nothing to preview before a
    report exists, and rendering a half-built document would show the admin pages
    the endpoint never produced. render.to_pdf() returns None rather than raising,
    so a missing engine or a locked file degrades to a message.
    """
    if not _can_admin():
        return _deny()
    docx_path = WR.latest_report(request_id)
    if not docx_path:
        return jsonify(success=False,
                       message="No report has been generated for this request "
                               "yet - there is nothing to preview."), 404
    if not R.available():
        return jsonify(success=False, message=R.unavailable_note()), 503

    pdf_path = os.path.splitext(docx_path)[0] + ".pdf"
    try:
        # Reuse a PDF that is newer than its .docx: on Windows this is a Word COM
        # launch, which is far too slow to repeat every time the link is clicked.
        fresh = (os.path.exists(pdf_path)
                 and os.path.getmtime(pdf_path) >= os.path.getmtime(docx_path))
        if not fresh:
            pdf_path = R.to_pdf(docx_path, pdf_path) or ""
    except Exception as exc:  # noqa: BLE001
        log.error("wizard pdf preview failed for request %s: %s", request_id, exc)
        pdf_path = ""
    if not pdf_path or not os.path.exists(pdf_path):
        return jsonify(success=False,
                       message="The PDF could not be rendered. The .docx itself "
                               "is unaffected - download it from the request."), 500
    return send_file(pdf_path, mimetype="application/pdf",
                     as_attachment=False,
                     download_name=os.path.basename(pdf_path))


# ==========================================================================
# REGISTRATION - the exact edit to make in datasheet_gen/__init__.py
# ==========================================================================
#
# This blueprint rides the same hook as report_wizard_bp, for the same reason
# given there: report_gen has no register step of its own, and inventing a second
# boot hook for one blueprint is how a project ends up with four of them.
#
# In `D:\Mihir's Work\ThermoDocGenExtended\datasheet_gen\__init__.py`, inside
# register_datasheet_gen(), REPLACE these four lines (currently 46-49):
#
#         # The wizard's blueprint rides the same hook for the same reason.
#         from report_gen.wizard_routes import report_wizard_bp
#         if "report_wizard" not in app.blueprints:
#             app.register_blueprint(report_wizard_bp)
#
# with these seven:
#
#         # The wizard's blueprints ride the same hook for the same reason.
#         from report_gen.wizard_routes import report_wizard_bp
#         from report_gen.wizard_pages import report_wizard_pages_bp
#         if "report_wizard" not in app.blueprints:
#             app.register_blueprint(report_wizard_bp)
#         if "report_wizard_pages" not in app.blueprints:
#             app.register_blueprint(report_wizard_pages_bp)
#
# Nothing else changes. Both registrations stay inside the existing
# `try: ... except Exception as exc: app.logger.warning(...)` so a failure here
# still cannot stop the app booting - the one-click report in app.py does not
# depend on either blueprint.
#
# Why a second blueprint rather than more routes on report_wizard_bp: that file
# is yours to own, and a separate blueprint means these five routes can be
# registered, unregistered or moved without touching page 1. The two names must
# differ ("report_wizard" vs "report_wizard_pages"); url_for across them works
# unchanged, which is how _nav() links back to page 1.
