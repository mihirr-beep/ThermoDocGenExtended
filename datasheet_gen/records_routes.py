"""Viewing of saved datasheet records (drafts + submitted).

GET /datasheet/records            -> list (admin: all; lab_engineer: own)
GET /datasheet/records/<id>       -> read-only view of one filled datasheet
GET /datasheet/records/<id>/download -> the generated .docx (if submitted)
"""
import os

from flask import Blueprint, render_template, abort, send_file, url_for
from flask_login import login_required, current_user

from . import records as R

datasheet_records_bp = Blueprint("datasheet_records", __name__)


def _form_url(rec):
    """URL of the fillable form for this record (to resume/edit)."""
    code = (rec.get("test_code") or "").upper()
    pid = rec.get("planner_entry_id")
    if not pid:
        return None
    if code == "CE":
        return url_for("datasheet_gen.ce_form", assignment_id=pid)
    return url_for("datasheet_generic.g_form", code=code, assignment_id=pid)


@datasheet_records_bp.route("/datasheet/records")
@login_required
def records_list():
    if current_user.role not in ("admin", "lab_engineer"):
        abort(403)
    rows = R.list_records(current_user)
    return render_template("datasheet_gen/records_list.html", records=rows)


@datasheet_records_bp.route("/datasheet/records/<int:record_id>")
@login_required
def record_detail(record_id):
    rec = R.get_record(record_id)
    if rec is None:
        abort(404)
    if not R.can_view(rec, current_user):
        abort(403)
    vm = R.record_view_model(rec)
    return render_template(
        "datasheet_gen/record_view.html",
        rec=rec, vm=vm, form_url=_form_url(rec),
        can_download=bool(rec.get("generated_file_path")
                          and os.path.exists(rec["generated_file_path"])),
    )


@datasheet_records_bp.route("/datasheet/records/<int:record_id>/download")
@login_required
def record_download(record_id):
    rec = R.get_record(record_id)
    if rec is None:
        abort(404)
    if not R.can_view(rec, current_user):
        abort(403)
    path = rec.get("generated_file_path")
    if not path or not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))
