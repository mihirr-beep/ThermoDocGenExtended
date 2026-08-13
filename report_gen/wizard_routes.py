# -*- coding: utf-8 -*-
"""The report wizard's EUT-information page: serve it, save it, check it.

Phase 1 of the wizard. One page, the one that carries the actual work - the
eighteen fields a report currently ships without, four of which are printed as
"NA" today so the document reads finished when nobody was ever asked.

TWO STORES, ON PURPOSE
----------------------
Six of the fields (length, width, height, dimension_unit, weight,
operating_frequency) have a column on iec_emc_requests that is empty on every
request in the database, real ones included. Those are written BACK to the
request rather than kept in the draft, so the weight of a product has one home
and the next report for the same product inherits it. Everything else has no
column anywhere and goes in report_draft.

The alternative - shadow every field in the draft - is how the per-test sections
of this same report ended up with 49 of 92 subsections disagreeing between two
copies of the truth.
"""
import logging
import os
from datetime import datetime

from flask import (Blueprint, jsonify, redirect, render_template, request,
                   url_for)
from flask_login import current_user, login_required
from sqlalchemy import text
from werkzeug.utils import secure_filename

from . import draft
from . import wizard_fields as WF

log = logging.getLogger(__name__)

report_wizard_bp = Blueprint("report_wizard", __name__)


def _request_row(request_id):
    """The request columns the wizard owns, as a plain dict."""
    from models import db
    cols = [f[0] for f in WF.by_store("request")]
    row = db.session.execute(text(
        "SELECT id, tco_id, product_name, %s FROM iec_emc_requests WHERE id=:r"
        % ", ".join("`%s`" % c for c in cols)), {"r": int(request_id)}).mappings().first()
    return dict(row) if row else None


def _write_back(request_id, values):
    """Write the store="request" fields to iec_emc_requests.

    Only keys that were actually posted, and only the six this wizard owns - a
    blanket UPDATE from a form would let a report page overwrite request data
    nobody intended it to touch.
    """
    from models import db
    owned = {f[0] for f in WF.by_store("request")}
    sets = {k: v for k, v in values.items() if k in owned}
    if not sets:
        return 0
    assign = ", ".join("`%s`=:%s" % (k, k) for k in sets)
    params = dict(sets)
    params["r"] = int(request_id)
    db.session.execute(text(
        "UPDATE iec_emc_requests SET %s WHERE id=:r" % assign), params)
    db.session.commit()
    return len(sets)


def _image_dir():
    from flask import current_app
    d = os.path.join(current_app.root_path, "uploads", "report_images")
    os.makedirs(d, exist_ok=True)
    return d


def _save_images(request_id):
    """Save any uploaded image and return {key: path}.

    Only the four keys the spec declares. An unrecognised file field is ignored
    rather than written - a wizard page is not an upload endpoint.
    """
    out = {}
    if not request.files:
        return out
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for key in WF.image_keys():
        fs = request.files.get(key)
        if not fs or not (fs.filename or "").strip():
            continue
        name = "%s_%s_%s_%s" % (request_id, key, ts, secure_filename(fs.filename))
        path = os.path.join(_image_dir(), name)
        fs.save(path)
        # Reuse the datasheet compressor: these land in a Word document and an
        # uncompressed phone photo makes a 40 MB report.
        try:
            from datasheet_gen.generic_routes import _compress_image
            _compress_image(path)
        except Exception as exc:  # noqa: BLE001 - compression is optional
            log.info("report image not compressed (%s): %s", key, exc)
        out[key] = path
    return out


def _can_admin():
    role = (getattr(current_user, "role", "") or "").lower()
    return role in ("admin", "administrator", "superadmin") or getattr(
        current_user, "is_admin", False)


@report_wizard_bp.route("/report/wizard/<int:request_id>/eut", methods=["GET"])
@login_required
def eut_page(request_id):
    """The EUT-information page of the wizard."""
    if not _can_admin():
        return jsonify(success=False,
                       message="Only an admin can prepare a test report"), 403
    row = _request_row(request_id)
    if row is None:
        return jsonify(success=False, message="Request not found"), 404
    d = draft.load(request_id)
    # The request's own values seed the form for store="request" fields, so an
    # admin never retypes something the request already knows.
    values = dict(d["form"])
    for f in WF.by_store("request"):
        key = f[0]
        if not values.get(key) and row.get(key) not in (None, ""):
            values[key] = row[key]
    return render_template(
        "report_wizard_eut.html",
        req=row, request_id=request_id, values=values,
        images=d["images"], fields=WF.FIELDS, choices=WF.CHOICES,
        optional=WF.OPTIONAL, ulr_no=WF.ULR_NO,
        # from builder.py's own DIAGRAM_BOX / PHOTO_BOX, so the crop frame in the
        # form is the box the .docx will use and not a second opinion about it
        image_boxes=WF.image_boxes(),
        outstanding=WF.outstanding(values, row))


@report_wizard_bp.route("/report/wizard/<int:request_id>/eut", methods=["POST"])
@login_required
def eut_save(request_id):
    """Save the page. Returns JSON so the form can save without navigating."""
    if not _can_admin():
        return jsonify(success=False,
                       message="Only an admin can prepare a test report"), 403
    row = _request_row(request_id)
    if row is None:
        return jsonify(success=False, message="Request not found"), 404

    # request.form, not get_json: this page posts multipart because of the
    # images. The CE datasheet had exactly this bug - it branched on
    # content_type looking for multipart and rejected urlencoded posts with a
    # misleading "Assignment ID is required".
    posted, bad = {}, []
    for key, _l, _k, _s, _loc, _h in WF.FIELDS:
        if key not in request.form:
            continue
        val, err = WF.coerce(key, request.form.get(key))
        if err:
            bad.append(err)
        else:
            posted[key] = val

    images = _save_images(request_id)
    owned = {f[0] for f in WF.by_store("request")}
    to_request = {k: v for k, v in posted.items() if k in owned}
    to_draft = {k: v for k, v in posted.items() if k not in owned}

    # THE DRAFT IS SAVED FIRST, and independently. The first run of this posted
    # "12.4 kg" into a FLOAT column; MySQL raised 1265 and the 500 that followed
    # discarded fourteen good fields and an uploaded image along with it. One
    # unparseable number must cost that number, not the page.
    draft.save(request_id, form=to_draft, images=images, page=1,
               user_id=getattr(current_user, "id", None))

    written = 0
    if to_request:
        try:
            written = _write_back(request_id, to_request)
        except Exception as exc:  # noqa: BLE001
            from models import db
            db.session.rollback()
            log.error("report wizard: write-back failed for request %s: %s",
                      request_id, exc)
            bad.append("The EUT size/weight could not be saved to the request")

    fresh_row = _request_row(request_id) or {}
    d = draft.load(request_id)
    merged = dict(d["form"])
    for f in WF.by_store("request"):
        if fresh_row.get(f[0]) not in (None, ""):
            merged[f[0]] = fresh_row[f[0]]
    left = WF.outstanding(merged, fresh_row)
    msg = "Saved. %d field(s) still outstanding." % len(left)
    if bad:
        # Partial success is reported as such. Saying "saved" while a value was
        # rejected is how someone discovers at generation time that the weight
        # they typed was never stored.
        msg = "Saved, except: %s" % "; ".join(bad)
    return jsonify(success=True, saved_to_request=written,
                   images_saved=len(images), outstanding=len(left),
                   rejected=bad,
                   outstanding_fields=[o["key"] for o in left], message=msg)


@report_wizard_bp.route("/report/wizard/<int:request_id>/check", methods=["GET"])
@login_required
def completeness(request_id):
    """What the report will and will not contain. Used where no PDF preview exists.

    Deliberately not a rendered picture. The pure-Python docx converter tested
    for this produced a plausible 26-page PDF whose numbering and field codes
    were wrong, and a preview that looks right while being wrong is worse than a
    list - the admin signs off on what they were shown.
    """
    if not _can_admin():
        return jsonify(success=False, message="Admin only"), 403
    row = _request_row(request_id)
    if row is None:
        return jsonify(success=False, message="Request not found"), 404
    d = draft.load(request_id)
    merged = dict(d["form"])
    for f in WF.by_store("request"):
        if row.get(f[0]) not in (None, ""):
            merged[f[0]] = row[f[0]]
    left = WF.outstanding(merged, row)
    from . import render as R
    return jsonify(success=True, tco_id=row.get("tco_id"),
                   product=row.get("product_name"),
                   filled=WF.filled_count(merged, row), total=len(WF.FIELDS),
                   outstanding=left,
                   images={k: os.path.basename(v) for k, v in (d["images"] or {}).items()},
                   pdf_backend=R.backend(),
                   pdf_note=None if R.available() else R.unavailable_note())
