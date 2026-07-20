"""Generic, schema-driven datasheet routes for all non-CE tests.

GET  /datasheet/g/<code>/<assignment_id>/form
POST /datasheet/g/<code>/generate
GET  /datasheet/g/<assignment_id>/download
"""
import json
import os
from datetime import datetime

from flask import (Blueprint, request, jsonify, send_file, url_for,
                   render_template, current_app, abort)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import db, PlannerEntry, User
from .registry import REGISTRY, normalize_code, load_schema
from . import generic_service as gs
from . import generic_generator as gg
from . import records as R
from .routes import _can_access, _parent_request, _compress_image, _output_dir

datasheet_generic_bp = Blueprint("datasheet_generic", __name__)


def _valid(code):
    return code in REGISTRY and code != "CE"


def _future(schema, form_data):
    today = datetime.now().date()
    bad = []
    for f in gs.iter_scalar_fields(schema):
        if f.get("input") == "image":
            continue
        k = f["key"].lower()
        if "date" in k and not any(x in k for x in ("due", "cal")):
            v = form_data.get(f["key"])
            v = v.strip() if isinstance(v, str) else ""
            if v:
                try:
                    if datetime.strptime(v, "%Y-%m-%d").date() > today:
                        bad.append(f.get("label", f["key"]))
                except ValueError:
                    pass
    out = []
    for x in bad:
        if x not in out:
            out.append(x)
    return out


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


@datasheet_generic_bp.route("/datasheet/g/<code>/<int:assignment_id>/form")
@login_required
def g_form(code, assignment_id):
    code = normalize_code(code)
    if not _valid(code):
        abort(404)
    a = db.session.get(PlannerEntry, assignment_id)
    if a is None:
        abort(404)
    if not _can_access(a):
        abort(403)
    schema = load_schema(code)
    pre = gs.collect_prefill(schema, _parent_request(a), a)

    # Resume a saved draft/record: scalar entries win over auto-fill, and saved
    # table rows are injected back into the schema so the grids render as left.
    draft = R.draft_form(a.id)
    draft_status = ""
    measurement_groups = []
    if draft:
        rec = R.get_record_for_assignment(a.id)
        draft_status = (rec or {}).get("status", "")
        for k, v in draft.items():
            if not k.endswith("[]") and isinstance(v, str) and v.strip():
                pre[k] = v
        for sec in schema.get("sections", []):
            for it in sec.get("items", []):
                if it.get("type") != "table":
                    continue
                col_keys = [c["key"] for c in it.get("columns", [])]
                cols = {c: draft.get(f"{it['key']}__{c}[]") or [] for c in col_keys}
                cols = {c: (val if isinstance(val, list) else [val]) for c, val in cols.items()}
                n = max((len(val) for val in cols.values()), default=0)
                rows = []
                for i in range(n):
                    row = {c: (cols[c][i] if i < len(cols[c]) else "") for c in col_keys}
                    if any(str(x).strip() for x in row.values()):
                        rows.append(row)
                if rows:
                    it["rows"] = rows
        if code == "RE":
            from .generic_service import _re_measurement_groups
            measurement_groups = _re_measurement_groups(draft)
        elif code == "HARMONIC":
            from .generic_service import _harmonic_build_context
            pre.update(_harmonic_build_context(draft))
            measurement_groups = []
        else:
            measurement_groups = []

    # Prefill repeating tables (equipment / RE Test Limits / software) from the
    # request + derivations, but only where the engineer has no saved draft rows.
    prefill_tables = gs.collect_prefill_tables(schema, _parent_request(a), a)
    if prefill_tables:
        for sec in schema.get("sections", []):
            for it in sec.get("items", []):
                if it.get("type") == "table" and not it.get("rows") and prefill_tables.get(it["key"]):
                    it["rows"] = prefill_tables[it["key"]]
    saved_images = [os.path.basename(p) for p in R.draft_images(a.id).values() if p]
    return render_template(
        "datasheet_gen/generic_form.html",
        code=code, schema=schema, prefill=pre,
        assignment_id=a.id, tco_id=a.tco_id or "", test_name=a.test_name or code,
        draft_status=draft_status, saved_images=saved_images,
        measurement_groups=measurement_groups,
        reviewers=_reviewer_candidates(),
        assigned_reviewer_id=a.peer_reviewer_user_id,
        entry_status=a.status,
        today=datetime.now().strftime("%Y-%m-%d"),
    )


def _read_generic_payload():
    raw = request.form
    form_data = {k: (raw.getlist(k) if k.endswith("[]") else raw.get(k)) for k in raw.keys()}
    return form_data, raw.get("assignment_id"), raw.get("tco_id")


def _save_generic_images(ikeys, assignment_id):
    out = {}
    img_dir = os.path.join(_output_dir(), "images")
    os.makedirs(img_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    all_keys = list(ikeys)
    if request.files:
        for k in request.files.keys():
            if k.startswith("meas_img_") and k not in all_keys:
                all_keys.append(k)
    for k in all_keys:
        fs = request.files.get(k)
        if fs and (fs.filename or "").strip():
            path = os.path.join(img_dir, f"{assignment_id}_{k}_{ts}_{secure_filename(fs.filename)}")
            fs.save(path)
            _compress_image(path)
            out[k] = path
    return out


@datasheet_generic_bp.route("/datasheet/g/harmonic/parse-avgmax", methods=["POST"])
@login_required
def g_parse_avgmax():
    """Parse an uploaded IEC 61000-3-2 instrument RTF and return the 'Average and
    Maximum harmonic current results' table as JSON rows (c0..c9). Used by the
    Harmonic form's Functional Check 'Import TXT' button."""
    fs = request.files.get("file")
    if fs is None or not (fs.filename or "").strip():
        return jsonify(success=False, message="No file uploaded"), 400
    try:
        from . import rtf_import
        rows = rtf_import.parse_avgmax_table(fs.read())
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("Avg/Max RTF parse failed: %s", exc)
        return jsonify(success=False, message="Could not read the RTF file"), 500
    if not rows:
        return jsonify(success=False,
                       message="No 'Average and Maximum harmonic current results' table found in that file"), 422
    return jsonify(success=True, rows=rows, count=len(rows))


@datasheet_generic_bp.route("/datasheet/g/flicker/parse-fc", methods=["POST"])
@login_required
def g_parse_flicker_fc():
    """Parse an uploaded IEC 61000-3-3 instrument RTF and return the Functional
    Check 'Flicker Measurements' rows (Line 1 / Limits / Results) as JSON. Used
    by the Flicker form's Functional Check 'Import TXT' button."""
    fs = request.files.get("file")
    if fs is None or not (fs.filename or "").strip():
        return jsonify(success=False, message="No file uploaded"), 400
    try:
        from . import rtf_import
        rows = rtf_import.parse_flicker_fc(fs.read())
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("Flicker RTF parse failed: %s", exc)
        return jsonify(success=False, message="Could not read the RTF file"), 500
    if not rows:
        return jsonify(success=False,
                       message="No 'Flicker Measurements' data found in that file"), 422
    return jsonify(success=True, rows=rows, count=len(rows))


@datasheet_generic_bp.route("/datasheet/g/<code>/save-draft", methods=["POST"])
@login_required
def g_save_draft(code):
    """Persist a non-CE datasheet form as a draft (no document generation)."""
    try:
        code = normalize_code(code)
        if not _valid(code):
            abort(404)
        schema = load_schema(code)
        form_data, assignment_id, tco_id = _read_generic_payload()
        if not assignment_id:
            return jsonify(success=False, message="Assignment ID is required"), 400
        a = db.session.get(PlannerEntry, int(assignment_id))
        if a is None:
            return jsonify(success=False, message="Assignment not found"), 404
        if not _can_access(a):
            return jsonify(success=False, message="Access denied"), 403
        images = _save_generic_images(gs.image_keys(schema), assignment_id)
        R.upsert_record(a, code, form_data, images, R.DRAFT, user=current_user)
        return jsonify(success=True, message="Draft saved")
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.error("Generic draft save error: %s", exc)
        return jsonify(success=False, message="An error occurred while saving the draft"), 500


def _render_datasheet_docx(code, schema, a, form_data, tco_id):
    """Build the datasheet .docx from form_data; return (path, images, filename).
    Shared by 'send to peer review' (initial generation) and the post-approval
    'generate final' regeneration so both produce an identical document."""
    parent = _parent_request(a)
    ctx = gs.build_context(schema, form_data)
    ikeys = gs.image_keys(schema)
    images = _save_generic_images(ikeys, a.id)
    # reuse any image saved in an earlier draft that wasn't re-uploaded now
    for k, p in R.draft_images(a.id).items():
        if k not in images and p and os.path.exists(p):
            images[k] = p
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_tco = secure_filename(str(tco_id or (parent.tco_id if parent else "") or "TCO"))
    filename = f"{safe_tco}_{code}_{ts}.docx"
    out = os.path.join(_output_dir(), filename)
    gg.render(code, ctx, ikeys, images, out)
    try:
        with open(out + ".json", "w", encoding="utf-8") as fh:
            json.dump(form_data, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass
    return out, images, filename


@datasheet_generic_bp.route("/datasheet/g/<code>/generate", methods=["POST"])
@login_required
def g_generate(code):
    """SEND TO PEER REVIEW: generate the datasheet .docx and route it into the
    company's peer-review queue (status='Peer Review', reviewer assigned). The
    datasheet is NOT final until a reviewer approves it on the peer-review page;
    after approval the engineer produces the final copy via /generate-final."""
    try:
        code = normalize_code(code)
        if not _valid(code):
            abort(404)
        schema = load_schema(code)
        raw = request.form
        form_data = {k: (raw.getlist(k) if k.endswith("[]") else raw.get(k)) for k in raw.keys()}
        assignment_id = raw.get("assignment_id")
        tco_id = raw.get("tco_id")
        if not assignment_id:
            return jsonify(success=False, message="Assignment ID is required"), 400
        a = db.session.get(PlannerEntry, int(assignment_id))
        if a is None:
            return jsonify(success=False, message="Assignment not found"), 404
        if not _can_access(a):
            return jsonify(success=False, message="Access denied"), 403

        reviewer, rev_err = _resolve_reviewer(raw.get("peer_reviewer_id"))
        if rev_err:
            return jsonify(success=False, message=rev_err), 400

        fut = _future(schema, form_data)
        if fut:
            return jsonify(success=False, message="Date cannot be in the future: " + ", ".join(fut)), 400

        out, images, filename = _render_datasheet_docx(code, schema, a, form_data, tco_id)

        a.datasheet_file_path = out
        a.datasheet_uploaded_at = _ist_now()
        a.datasheet_uploaded_by = current_user.id
        a.peer_reviewer_user_id = reviewer.id
        a.peer_review_assigned_at = _ist_now()
        a.status = "Peer Review"
        _append_review_note(
            a, f"Datasheet generated and sent to {reviewer.username} for peer review.",
            current_user.username, "SENT FOR REVIEW")
        db.session.commit()

        try:
            R.upsert_record(a, code, form_data, images, R.SUBMITTED,
                            generated_file_path=out, user=current_user)
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            current_app.logger.error("%s datasheet record save failed: %s", code, exc)

        return jsonify(success=True, status="Peer Review",
                       message=f"Sent to {reviewer.username} for peer review.")
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.error("Generic datasheet send-to-review error: %s", exc)
        return jsonify(success=False, message="An error occurred while sending the datasheet for peer review"), 500


@datasheet_generic_bp.route("/datasheet/g/<code>/<int:assignment_id>/generate-final", methods=["POST"])
@login_required
def g_generate_final(code, assignment_id):
    """Post-approval: regenerate the final .docx from the approved saved data and
    return it for download. Allowed only after peer-review approval
    (planner entry status == 'datasheet_uploaded')."""
    try:
        code = normalize_code(code)
        if not _valid(code):
            abort(404)
        a = db.session.get(PlannerEntry, assignment_id)
        if a is None:
            return jsonify(success=False, message="Assignment not found"), 404
        if not _can_access(a):
            return jsonify(success=False, message="Access denied"), 403
        if a.status != "datasheet_uploaded":
            return jsonify(success=False,
                           message="This datasheet has not been approved in peer review yet."), 400
        schema = load_schema(code)
        form_data = R.draft_form(a.id)
        if not form_data:
            return jsonify(success=False, message="No saved datasheet data to generate."), 400
        out, images, filename = _render_datasheet_docx(code, schema, a, form_data, a.tco_id)
        a.datasheet_file_path = out
        db.session.commit()
        return jsonify(success=True, filename=filename,
                       download_url=url_for("datasheet_generic.g_download", assignment_id=a.id))
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.error("Generic datasheet generate-final error: %s", exc)
        return jsonify(success=False, message="An error occurred while generating the final datasheet"), 500


@datasheet_generic_bp.route("/datasheet/g/<int:assignment_id>/download")
@login_required
def g_download(assignment_id):
    a = db.session.get(PlannerEntry, assignment_id)
    if a is None or not a.datasheet_file_path:
        abort(404)
    if not _can_access(a):
        abort(403)
    if not os.path.exists(a.datasheet_file_path):
        abort(404)
    return send_file(a.datasheet_file_path, as_attachment=True,
                     download_name=os.path.basename(a.datasheet_file_path))
