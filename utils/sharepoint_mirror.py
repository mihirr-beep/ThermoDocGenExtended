# -*- coding: utf-8 -*-
"""Mirror a generated datasheet to SharePoint, off the request thread.

WHY OFF-THREAD, AND WHY FAILURE IS RECORDED RATHER THAN RAISED

A Graph PUT inside the peer-review approve request makes the reviewer wait on the network,
and a SharePoint outage would then fail the approval outright. A lab workflow action must
never depend on SharePoint being reachable, so the upload runs in a thread, and a failure
lands in planner_entries.sharepoint_error where it is visible and retryable. The local
.docx is untouched either way - report_gen reads it off disk and needs it there.

`finalise.prewarm(background=True)` is the existing precedent for threading in this app.
"""
import logging
import os
import threading
import traceback
from datetime import datetime

log = logging.getLogger(__name__)


def _client():
    from utils import sharepoint_client
    return sharepoint_client


def enabled():
    return _client().enabled()


# ---------------------------------------------------------------------------
# the folder name, resolved once per request
# ---------------------------------------------------------------------------
def resolve_folder_name(request_obj, db=None):
    """This request's SharePoint job folder name, stored on first use and reused after.

    Returns the stored name when there is one. Otherwise derives it from the lab's
    convention and writes it back, so a later edit to product_name or job_number cannot
    move the folder or start a second one.
    """
    from utils import sharepoint_paths as paths
    stored = (getattr(request_obj, "sharepoint_folder_name", "") or "").strip()
    if stored:
        return stored
    name = paths.job_folder_name(request_obj)
    try:
        request_obj.sharepoint_folder_name = name
        if db is not None:
            db.session.commit()
    except Exception as exc:  # noqa: BLE001 - a name we cannot persist is still usable now
        if db is not None:
            db.session.rollback()
        log.warning("SharePoint: could not store the folder name for request %s: %s",
                    getattr(request_obj, "id", "?"), exc)
    return name


# ---------------------------------------------------------------------------
# one datasheet
# ---------------------------------------------------------------------------
def push(entry_id, local_path, approved, app=None):
    """Upload one datasheet and record the result on its planner entry.

    Synchronous - `push_async` is what the routes call. Returns the webUrl, or None when
    the mirror is off or nothing could be uploaded; the reason is recorded either way.
    """
    from flask import current_app
    from models import db, PlannerEntry
    from utils import sharepoint_paths as paths
    sp = _client()

    flask_app = app or current_app._get_current_object()
    with flask_app.app_context():
        entry = db.session.get(PlannerEntry, entry_id)
        if entry is None:
            log.warning("SharePoint: planner entry %s is gone; nothing to mirror", entry_id)
            return None
        if not local_path or not os.path.exists(local_path):
            _record(db, entry, error="the generated file was not on disk: %s" % local_path)
            return None

        request_obj = _request_of(entry)
        if request_obj is None:
            _record(db, entry, error="no test request behind this assignment")
            return None

        try:
            folder = resolve_folder_name(request_obj, db)
            dest = paths.destination(
                request_obj,
                tco_id=getattr(entry, "tco_id", "") or getattr(request_obj, "tco_id", ""),
                code=getattr(entry, "test_name", "") or "",
                approved=approved,
                stored_name=folder,
                local_path=local_path)
            # The job folder gets the same subfolder skeleton the lab's own template folder
            # has, so an app-created folder is indistinguishable from a hand-made one.
            job_path = paths.job_folder_path(request_obj, folder)
            token = sp.get_token()
            for sub in paths.JOB_SKELETON:
                sp.ensure_folder("%s/%s" % (job_path, sub), token)
            url = sp.upload(local_path, dest, token)
            _record(db, entry, url=url, approved=approved)
            log.info("SharePoint: %s datasheet for entry %s -> %s",
                     "approved" if approved else "draft", entry_id, dest)
            return url
        except Exception as exc:  # noqa: BLE001 - never propagate into a workflow action
            log.error("SharePoint: mirror failed for entry %s: %s", entry_id, exc)
            log.debug("%s", traceback.format_exc())
            _record(db, entry, error=str(exc)[:2000])
            return None


def _request_of(entry):
    """The EMCRequest behind a planner entry, however this entry is linked to one."""
    from models import db, EMCRequest
    direct = getattr(entry, "test_request", None)
    if direct is not None:
        return direct
    rid = getattr(entry, "test_request_id", None)
    return db.session.get(EMCRequest, rid) if rid else None


def _record(db, entry, url=None, approved=False, error=None):
    """Write the outcome onto the entry. A success clears the previous error."""
    try:
        if url:
            if approved:
                entry.sharepoint_approved_url = url
            else:
                entry.sharepoint_draft_url = url
            entry.sharepoint_synced_at = datetime.now()
            entry.sharepoint_error = None
        else:
            entry.sharepoint_error = error
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        log.warning("SharePoint: could not record the result on entry %s: %s",
                    getattr(entry, "id", "?"), exc)


def push_async(entry_id, local_path, approved, app=None):
    """Mirror in the background. Returns immediately; never raises into the caller.

    A no-op when the mirror is disabled, so every call site can be unconditional.
    """
    if not enabled():
        return False
    from flask import current_app
    flask_app = app or current_app._get_current_object()

    def run():
        try:
            push(entry_id, local_path, approved, app=flask_app)
        except Exception:  # noqa: BLE001 - a thread dying silently is worse than a log line
            log.error("SharePoint: background mirror crashed for entry %s\n%s",
                      entry_id, traceback.format_exc())

    threading.Thread(target=run, name="sharepoint-mirror-%s" % entry_id, daemon=True).start()
    return True
