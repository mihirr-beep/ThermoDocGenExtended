# -*- coding: utf-8 -*-
"""Pages 2, 3 and 4 of the report wizard: review, tests, generate.

WHY THESE PAGES EXIST AT ALL
----------------------------
Page 1 collects the twenty-one fields nothing else supplies. These three show
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

# Wizard page number -> endpoint. Page 1 lives in the other blueprint.
_PAGE_ENDPOINTS = {
    1: "report_wizard.eut_page",
    2: "report_wizard_pages.summary_page",
    3: "report_wizard_pages.tests_page",
    4: "report_wizard_pages.generate_page",
}


def _deny():
    """The same 403 body page 1 returns, so the fetch handlers can share it."""
    return jsonify(success=False,
                   message="Only an admin can prepare a test report"), 403


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


def _nav(request_id, page):
    """Back/next targets and the step chips, so no template hardcodes an endpoint."""
    steps = []
    for n, label in ((1, "1. EUT information"), (2, "2. Summary review"),
                     (3, "3. Tests"), (4, "4. Generate")):
        steps.append({"n": n, "label": label, "current": n == page,
                      "url": url_for(_PAGE_ENDPOINTS[n], request_id=request_id)})
    return {
        "steps": steps,
        "back": (url_for(_PAGE_ENDPOINTS[page - 1], request_id=request_id)
                 if page > 1 else None),
        "next": (url_for(_PAGE_ENDPOINTS[page + 1], request_id=request_id)
                 if page < 4 else None),
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
    _mark_page(request_id, 2)
    return render_template("report_wizard_summary.html",
                           request_id=request_id, preview=data,
                           nav=_nav(request_id, 2))


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
# page 4 - readiness, preview, generate
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
    if int(state.get("page_reached") or 1) < 4:
        draft.save(request_id, page=4, user_id=getattr(current_user, "id", None))
    return render_template(
        "report_wizard_generate.html",
        request_id=request_id, state=state, nav=_nav(request_id, 4),
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
    page = int((draft.load(request_id) or {}).get("page") or 1)
    page = min(max(page, 1), 4)
    return redirect(url_for(_PAGE_ENDPOINTS[page], request_id=request_id))


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
