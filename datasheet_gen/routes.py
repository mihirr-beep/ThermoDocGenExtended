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


def _compress_image(path, max_side=2400):
    """Downscale only very large uploads so the .docx doesn't balloon, while keeping
    enough resolution + JPEG quality that datasheet plots/line-art/text stay crisp.
    (max_side 2400 ~= 400 DPI in a 150 mm slot; JPEG at q95 with no chroma
    subsampling avoids the ringing that q85/4:2:0 produced on thin lines & text.)"""
    try:
        from PIL import Image
        img = Image.open(path)
        fmt = (img.format or "").upper()
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side), Image.LANCZOS)
        kwargs = {}
        if fmt in ("JPEG", "JPG"):
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            kwargs = {"quality": 95, "optimize": True, "subsampling": 0}
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
    # One fetch, three uses - see the note in records.form_from_record.
    record = R.get_record_for_assignment(assignment.id)
    draft = R.form_from_record(record)
    draft_status = ""
    if draft:
        draft_status = (record or {}).get("status", "")
        for k, v in draft.items():
            if not k.endswith("[]") and isinstance(v, str) and v.strip():
                prefill[k] = v

        # The loop above restores SCALARS only - every repeating table on this
        # form posts as "name[]" and was therefore skipped, so the equipment and
        # modification rows an engineer typed were saved into form_json and then
        # never shown again. The generic datasheets rebuild their grids from the
        # schema; CE has no schema, so it is done explicitly here.
        #
        # service._rows already pairs these arrays into the exact row shape the
        # template loops over - it is what the document generator uses - so the
        # pairing rule lives in one place rather than two.
        try:
            from .service import _rows
            equipment = _rows(
                draft,
                ["eq_name[]", "eq_make[]", "eq_model[]", "eq_serial[]", "eq_cal_due[]"],
                ["name", "make", "model", "serial", "cal_due"])
            if equipment:
                prefill["equipment"] = equipment
            modifications = _rows(
                draft,
                ["mod_state[]", "mod_description[]", "mod_fitted_by[]", "mod_date[]"],
                ["state", "description", "fitted_by", "date"])
            if modifications:
                prefill["modifications"] = modifications
        except Exception:  # noqa: BLE001 - a lost grid must not blank the form
            pass

    # MEASUREMENT DATA. The form builds these client-side - one repeated block
    # per Test, each with its own index - and the page always started with a
    # single empty one, so a refresh wiped the measurement tables off the screen
    # even though every meas_* key was still in form_json.
    #
    # addTest(label, data) could already restore them: it takes saved column
    # headings, rows and captions. Nothing ever called it with a draft. So the
    # records are rebuilt here with service._measurement_records - the same
    # function the document generator uses, which already knows how the indexed
    # keys pair up - and handed to the template to replay.
    #
    # Rows are passed as their `cells` arrays rather than the dicts: makeMeasRow
    # fills positionally against the column headings actually rendered, so a
    # table the engineer narrowed to two columns comes back with two.
    meas_records = []
    if draft:
        try:
            from .service import _measurement_records
            for rec in _measurement_records(draft) or []:
                meas_records.append({
                    "label": rec.get("label") or "",
                    "line_headers": rec.get("line_headers") or [],
                    "neutral_headers": rec.get("neutral_headers") or [],
                    "line_rows": [r.get("cells") or []
                                  for r in (rec.get("line_rows") or [])],
                    "neutral_rows": [r.get("cells") or []
                                     for r in (rec.get("neutral_rows") or [])],
                    "line_caption": rec.get("line_caption") or "",
                    "neutral_caption": rec.get("neutral_caption") or "",
                })
        except Exception:  # noqa: BLE001 - fall back to one empty Test
            meas_records = []
    # {field_key: basename} so the form can preview each draft image on reload
    saved_images = {k: os.path.basename(p)
                    for k, p in R.images_from_record(record).items()
                    if p and os.path.exists(p)}
    return render_template(
        "datasheet_gen/ce_form.html",
        assignment_id=assignment.id,
        tco_id=assignment.tco_id or "",
        test_name=assignment.test_name or "CE",
        prefill=prefill,
        meas_records=meas_records,
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
    # per-Test measurement plots, the extra plots added to a Test, and the extra
    # Test Setup pictures are all dynamic keys, so they are matched by shape
    dynamic = [k for k in files.keys()
               if re.match(r"^plot_(line|neutral)(_avg)?_\d+$", k)
               or re.match(r"^plot_extra_\d+_\d+$", k)
               or re.match(r"^ce_extra_photo_\d+$", k)]
    for var in list(_IMAGE_VARS) + dynamic:
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
        # the Save Draft button marks its save; the autosave timer does not, and
        # only pays for the header projection (records.upsert_record)
        full = bool(form_data.pop("_full_save", None))
        R.upsert_record(assignment, "CE", form_data, images, R.DRAFT,
                        user=current_user, full_projection=full)
        return jsonify(success=True, message="Draft saved")
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.error("Error saving CE draft: %s", exc)
        return jsonify(success=False, message="An error occurred while saving the draft"), 500


@datasheet_gen_bp.route("/datasheet/ce/<int:assignment_id>/draft-image/<key>")
@login_required
def ce_draft_image(assignment_id, key):
    """Serve one image saved in this CE assignment's draft, so the form can preview
    it on reload. Only paths in the draft's images_json are servable; access-checked."""
    assignment = db.session.get(PlannerEntry, assignment_id)
    if assignment is None:
        abort(404)
    if not _can_access(assignment):
        abort(403)
    path = R.draft_images(assignment_id).get(key)
    if not path or not os.path.exists(path):
        abort(404)
    return send_file(path)


@datasheet_gen_bp.route("/datasheet/ce/<int:assignment_id>/delete-draft", methods=["POST"])
@login_required
def ce_delete_draft(assignment_id):
    """Discard the saved CE draft data + uploaded images for this assignment.
    Refused for an already-submitted record so a submission can't be lost here."""
    assignment = db.session.get(PlannerEntry, assignment_id)
    if assignment is None:
        return jsonify(success=False, message="Assignment not found"), 404
    if not _can_access(assignment):
        return jsonify(success=False, message="Access denied"), 403
    rec = R.get_record_for_assignment(assignment_id)
    if rec and rec.get("status") == R.SUBMITTED:
        return jsonify(success=False,
                       message="This datasheet is already submitted; its data can't be discarded here."), 400
    R.delete_record_for_assignment(assignment_id)
    return jsonify(success=True, message="Draft removed")


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


@datasheet_gen_bp.route("/datasheet/ce/preview-docx", methods=["POST"])
@login_required
def preview_ce_docx():
    """DRAFT DOCUMENT: render the CE datasheet .docx from the form exactly as it
    stands and hand it back as a download, so the engineer can see how the real
    document looks BEFORE sending it for peer review.

    Deliberately side-effect free: no status/reviewer change, no datasheet record
    written, and no peer reviewer required."""
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

        output_path, _images, filename = _render_ce_docx(assignment, form_data, tco_id, files)
        return send_file(
            output_path,
            as_attachment=True,
            download_name="DRAFT_" + filename,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("CE draft document preview failed: %s", exc)
        return jsonify(success=False, message="Could not generate the draft document"), 500


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
            # freeze what the reviewer is being asked to look at, and start the
            # audit trail for this review
            from .projection import record_transition
            record_transition(assignment.id, "Peer Review", actor=current_user,
                              from_status="Draft", snapshot=True, submitted=True,
                              comment="Sent to %s for peer review." % reviewer.username)
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
