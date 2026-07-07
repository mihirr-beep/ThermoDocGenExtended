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

from models import db, PlannerEntry, EMCRequest
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


@datasheet_gen_bp.route("/datasheet/ce/generate", methods=["POST"])
@login_required
def generate_ce():
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

        future = _future_dates(form_data)
        if future:
            return jsonify(success=False,
                           message="Date cannot be in the future: " + ", ".join(future)), 400

        parent = _parent_request(assignment)
        context = build_ce_context(form_data)
        images = _merge_draft_images(assignment_id, _save_images(files, assignment_id))

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

        assignment.datasheet_file_path = output_path
        try:
            from app import get_ist_now
            assignment.datasheet_uploaded_at = get_ist_now()
        except Exception:
            assignment.datasheet_uploaded_at = datetime.now()
        assignment.datasheet_uploaded_by = current_user.id
        assignment.datasheet_comments = "Generated from CE datasheet form"
        assignment.status = "datasheet_uploaded"

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

        try:
            from app import _update_parent_request_datasheet_status
            _update_parent_request_datasheet_status(assignment)
        except Exception:
            pass

        db.session.commit()

        # Persist the filled form as a Submitted datasheet record (best-effort:
        # a store failure must not fail an otherwise-successful generation).
        try:
            R.upsert_record(assignment, "CE", form_data, images, R.SUBMITTED,
                            generated_file_path=output_path, user=current_user)
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            current_app.logger.error("CE datasheet record save failed: %s", exc)

        return jsonify(
            success=True,
            message="CE datasheet generated successfully",
            filename=filename,
            download_url=url_for("datasheet_gen.download_ce", assignment_id=assignment.id),
        )
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.error("Error generating CE datasheet: %s", exc)
        return jsonify(success=False, message="An error occurred while generating the CE datasheet"), 500


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
