"""Blueprint for CE datasheet generation (Plan A: full-page form).

GET  /datasheet/ce/<assignment_id>/form      -> the document-faithful CE form (prefilled)
POST /datasheet/ce/generate                  -> build .docx (multipart w/ images), store path, return download URL
GET  /datasheet/ce/<assignment_id>/download  -> download the generated .docx
GET  /datasheet/ce/<assignment_id>/prefill   -> auto-fill values as JSON (optional)
"""
import json
import os
import re
from datetime import datetime

from flask import (Blueprint, request, jsonify, send_file, url_for,
                   render_template, current_app, abort)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import db, PlannerEntry, EMCRequest, User
from .service import build_ce_context, collect_ce_prefill
from .generator import render_ce_datasheet, _IMAGE_VARS
from . import records as R

datasheet_gen_bp = Blueprint("datasheet_gen", __name__, template_folder="templates",
                             static_folder="static", static_url_path="/datasheet_gen_static")


def _output_dir():
    return os.path.join(current_app.root_path, "uploads", "test_datasheets")


def _can_access(assignment):
    if current_user.role not in ("admin", "lab_engineer"):
        return False
    if (current_user.role == "lab_engineer"
            and assignment.engineer_user_id
            and assignment.engineer_user_id != current_user.id):
        return False
    return True


def _future_dates(form_data):
    """Return labels of date fields set in the future (not allowed). Calibration
    Due is intentionally excluded - it is a future date by nature."""
    today = datetime.now().date()
    labels = []

    def _check(label, value):
        v = value.strip() if isinstance(value, str) else ""
        if v:
            try:
                if datetime.strptime(v, "%Y-%m-%d").date() > today:
                    labels.append(label)
            except ValueError:
                pass

    _check("Test Date", form_data.get("test_date"))
    _check("Tested By Date", form_data.get("tested_by_date"))
    md = form_data.get("mod_date[]")
    for v in (md if isinstance(md, list) else [md]):
        _check("Modification Date", v)
    seen = []
    for x in labels:
        if x not in seen:
            seen.append(x)
    return seen


def _compress_image(path, max_side=1600):
    """Downscale large uploads (e.g. 4K photos) so the .docx doesn't balloon."""
    try:
        from PIL import Image
        img = Image.open(path)
        fmt = (img.format or "").upper()
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side))
        kwargs = {}
        if fmt in ("JPEG", "JPG"):
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            kwargs = {"quality": 85, "optimize": True}
        elif fmt == "PNG":
            kwargs = {"optimize": True}
        img.save(path, format=fmt or None, **kwargs)
    except Exception:
        pass


def _parent_request(assignment):
    return (db.session.get(EMCRequest, assignment.test_request_id)
            if assignment.test_request_id else None)


def _ist_now():
    try:
        from app import get_ist_now
        return get_ist_now()
    except Exception:
        return datetime.now()


def _reviewer_candidates():
    """Active admins + lab engineers (excluding the current user) who can peer-review."""
    try:
        users = (User.query
                 .filter(User.is_active.is_(True), User.role.in_(["admin", "lab_engineer"]))
                 .order_by(User.username).all())
    except Exception:
        users = []
    return [{"id": u.id, "name": u.username or u.email, "role": u.role}
            for u in users if u.id != current_user.id]


def _resolve_reviewer(raw_id):
    """Validate a submitted peer_reviewer_id -> (User, None) or (None, error_message)."""
    if not raw_id:
        return None, "Please select a peer reviewer."
    try:
        rid = int(raw_id)
    except (TypeError, ValueError):
        return None, "Invalid peer reviewer."
    if rid == current_user.id:
        return None, "You cannot assign yourself as the peer reviewer."
    u = db.session.get(User, rid)
    if u is None or not getattr(u, "is_active", False) or u.role not in ("admin", "lab_engineer"):
        return None, "Selected peer reviewer is not valid."
    return u, None


def _append_review_note(entry, comment, username, label):
    """Append a peer-review note to datasheet_comments (mirrors app.py's format)."""
    ts = _ist_now().strftime("%d %b %Y, %I:%M %p")
    note = f"[{ts} | {username} | {label}]\n{comment}"
    existing = str(entry.datasheet_comments or "").strip()
    entry.datasheet_comments = (existing + "\n\n" + note) if existing else note


@datasheet_gen_bp.route("/datasheet/ce/<int:assignment_id>/form")
@login_required
def ce_form(assignment_id):
    assignment = db.session.get(PlannerEntry, assignment_id)
    if assignment is None:
        abort(404)
    if not _can_access(assignment):
        abort(403)
    prefill = collect_ce_prefill(_parent_request(assignment), assignment)
    # Resume a saved draft/record: scalar values the engineer already entered win
    # over the DB auto-fill, so re-opening the form continues where they left off.
    draft = R.draft_form(assignment.id)
    draft_status = ""
    if draft:
        rec = R.get_record_for_assignment(assignment.id)
        draft_status = (rec or {}).get("status", "")
        for k, v in draft.items():
            if not k.endswith("[]") and isinstance(v, str) and v.strip():
                prefill[k] = v
    saved_images = [os.path.basename(p) for p in R.draft_images(assignment.id).values() if p]
    return render_template(
        "datasheet_gen/ce_form.html",
        assignment_id=assignment.id,
        tco_id=assignment.tco_id or "",
        test_name=assignment.test_name or "CE",
        prefill=prefill,
        draft_status=draft_status,
        saved_images=saved_images,
        reviewers=_reviewer_candidates(),
        assigned_reviewer_id=assignment.peer_reviewer_user_id,
        entry_status=assignment.status,
        today=datetime.now().strftime("%Y-%m-%d"),
    )


@datasheet_gen_bp.route("/datasheet/ce/<int:assignment_id>/prefill")
@login_required
def prefill_ce(assignment_id):
    assignment = db.session.get(PlannerEntry, assignment_id)
    if assignment is None:
        return jsonify(success=False, message="Assignment not found"), 404
    if not _can_access(assignment):
        return jsonify(success=False, message="Access denied"), 403
    return jsonify(success=True, data=collect_ce_prefill(_parent_request(assignment), assignment))


def _read_payload():
    """Return (form_data dict, assignment_id, tco_id, files) for multipart or JSON."""
    ctype = request.content_type or ""
    if "multipart/form-data" in ctype:
        raw = request.form
        form_data = {}
        for key in raw.keys():
            form_data[key] = raw.getlist(key) if key.endswith("[]") else raw.get(key)
        return form_data, raw.get("assignment_id"), raw.get("tco_id"), request.files
    payload = request.get_json(silent=True) or {}
    return payload.get("form_data") or {}, payload.get("assignment_id"), payload.get("tco_id"), None


def _save_images(files, assignment_id):
    images = {}
    if not files:
        return images
    img_dir = os.path.join(_output_dir(), "images")
    os.makedirs(img_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    plot_keys = [k for k in files.keys() if re.match(r"^plot_(line|neutral)_\d+$", k)]
    for var in list(_IMAGE_VARS) + plot_keys:
        fs = files.get(var)
        if fs and (fs.filename or "").strip():
            safe = secure_filename(fs.filename)
            path = os.path.join(img_dir, f"{assignment_id}_{var}_{ts}_{safe}")
            fs.save(path)
            _compress_image(path)
            images[var] = path
    return images


def _merge_draft_images(assignment_id, images):
    """Fill any image the engineer didn't re-upload from a previously saved draft."""
    merged = dict(images or {})
    for k, p in R.draft_images(assignment_id).items():
        if k not in merged and p and os.path.exists(p):
            merged[k] = p
    return merged


@datasheet_gen_bp.route("/datasheet/ce/save-draft", methods=["POST"])
@login_required
def save_draft_ce():
    """Persist the CE form as a draft (status 'Not Submitted') without generating
    the document, so the engineer can continue later."""
    try:
        form_data, assignment_id, tco_id, files = _read_payload()
        if not assignment_id:
            return jsonify(success=False, message="Assignment ID is required"), 400
        try:
            assignment_id = int(assignment_id)
        except (TypeError, ValueError):
            return jsonify(success=False, message="Invalid assignment ID"), 400
        assignment = db.session.get(PlannerEntry, assignment_id)
        if assignment is None:
            return jsonify(success=False, message="Assignment not found"), 404
        if not _can_access(assignment):
            return jsonify(success=False, message="Access denied"), 403
        images = _save_images(files, assignment_id)
        R.upsert_record(assignment, "CE", form_data, images, R.DRAFT, user=current_user)
        return jsonify(success=True, message="Draft saved")
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.error("Error saving CE draft: %s", exc)
        return jsonify(success=False, message="An error occurred while saving the draft"), 500


def _render_ce_docx(assignment, form_data, tco_id, files):
    """Build the CE datasheet .docx; return (path, images, filename). Shared by
    'send to peer review' and the post-approval 'generate final' regeneration."""
    parent = _parent_request(assignment)
    context = build_ce_context(form_data)
    images = _merge_draft_images(assignment.id, _save_images(files, assignment.id))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_tco = secure_filename(str(tco_id or (parent.tco_id if parent else "") or "TCO"))
    filename = f"{safe_tco}_CE_{ts}.docx"
    output_path = os.path.join(_output_dir(), filename)
    render_ce_datasheet(context, output_path, images=images)
    try:
        with open(output_path + ".json", "w", encoding="utf-8") as fh:
            json.dump(form_data, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return output_path, images, filename


def _apply_test_date(assignment, form_data):
    td = form_data.get("test_date")
    td = td.strip() if isinstance(td, str) else ""
    if td:
        try:
            d = datetime.strptime(td, "%Y-%m-%d").date()
            assignment.completion_date = d
            assignment.start_date = assignment.start_date or d
            assignment.end_date = d
        except ValueError:
            pass


@datasheet_gen_bp.route("/datasheet/ce/generate", methods=["POST"])
@login_required
def generate_ce():
    """SEND TO PEER REVIEW: generate the CE datasheet .docx and route it into the
    company's peer-review queue (status='Peer Review', reviewer assigned). Not
    final until approved; the final copy is produced via /generate-final."""
    try:
        form_data, assignment_id, tco_id, files = _read_payload()
        if not assignment_id:
            return jsonify(success=False, message="Assignment ID is required"), 400
        try:
            assignment_id = int(assignment_id)
        except (TypeError, ValueError):
            return jsonify(success=False, message="Invalid assignment ID"), 400

        assignment = db.session.get(PlannerEntry, assignment_id)
        if assignment is None:
            return jsonify(success=False, message="Assignment not found"), 404
        if not _can_access(assignment):
            return jsonify(success=False, message="Access denied"), 403

        reviewer, rev_err = _resolve_reviewer((form_data or {}).get("peer_reviewer_id"))
        if rev_err:
            return jsonify(success=False, message=rev_err), 400

        future = _future_dates(form_data)
        if future:
            return jsonify(success=False,
                           message="Date cannot be in the future: " + ", ".join(future)), 400

        output_path, images, filename = _render_ce_docx(assignment, form_data, tco_id, files)

        assignment.datasheet_file_path = output_path
        assignment.datasheet_uploaded_at = _ist_now()
        assignment.datasheet_uploaded_by = current_user.id
        assignment.peer_reviewer_user_id = reviewer.id
        assignment.peer_review_assigned_at = _ist_now()
        assignment.status = "Peer Review"
        _append_review_note(
            assignment, f"CE datasheet generated and sent to {reviewer.username} for peer review.",
            current_user.username, "SENT FOR REVIEW")
        _apply_test_date(assignment, form_data)
        db.session.commit()

        # Persist the filled form as a Submitted datasheet record (best-effort:
        # a store failure must not fail an otherwise-successful submission).
        try:
            R.upsert_record(assignment, "CE", form_data, images, R.SUBMITTED,
                            generated_file_path=output_path, user=current_user)
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            current_app.logger.error("CE datasheet record save failed: %s", exc)

        return jsonify(
            success=True,
            status="Peer Review",
            message=f"Sent to {reviewer.username} for peer review.",
        )
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.error("Error sending CE datasheet for peer review: %s", exc)
        return jsonify(success=False, message="An error occurred while sending the CE datasheet for peer review"), 500


@datasheet_gen_bp.route("/datasheet/ce/<int:assignment_id>/generate-final", methods=["POST"])
@login_required
def generate_ce_final(assignment_id):
    """Post-approval: regenerate the final CE .docx from the approved saved data
    and return it for download. Allowed only after peer-review approval
    (planner entry status == 'datasheet_uploaded')."""
    try:
        assignment = db.session.get(PlannerEntry, assignment_id)
        if assignment is None:
            return jsonify(success=False, message="Assignment not found"), 404
        if not _can_access(assignment):
            return jsonify(success=False, message="Access denied"), 403
        if assignment.status != "datasheet_uploaded":
            return jsonify(success=False,
                           message="This datasheet has not been approved in peer review yet."), 400
        form_data = R.draft_form(assignment.id)
        if not form_data:
            return jsonify(success=False, message="No saved datasheet data to generate."), 400
        output_path, images, filename = _render_ce_docx(assignment, form_data, assignment.tco_id, None)
        assignment.datasheet_file_path = output_path
        db.session.commit()
        return jsonify(
            success=True,
            filename=filename,
            download_url=url_for("datasheet_gen.download_ce", assignment_id=assignment.id),
        )
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.error("Error generating final CE datasheet: %s", exc)
        return jsonify(success=False, message="An error occurred while generating the final CE datasheet"), 500


@datasheet_gen_bp.route("/datasheet/ce/<int:assignment_id>/download")
@login_required
def download_ce(assignment_id):
    assignment = db.session.get(PlannerEntry, assignment_id)
    if assignment is None or not assignment.datasheet_file_path:
        abort(404)
    if not _can_access(assignment):
        abort(403)
    if not os.path.exists(assignment.datasheet_file_path):
        abort(404)
    return send_file(
        assignment.datasheet_file_path,
        as_attachment=True,
        download_name=os.path.basename(assignment.datasheet_file_path),
    )
