# -*- coding: utf-8 -*-
"""Admin UI + read API for the datasheet fixed values and basic-standard map.

The admin panel is section-based so a non-technical user never sees raw JSON:
  * a landing page of cards (one per datasheet + one for the standard mapping);
  * a per-datasheet subpage that renders the fixed values as labelled fields and
    editable tables (the same shape they take in the document);
  * a dedicated Product -> Basic standard mapping page.
Structured edits are round-tripped to the JSON blob; an "Advanced (raw JSON)"
box is kept as a safety hatch.
"""
import json
import re

from flask import (Blueprint, request, jsonify, render_template, redirect,
                   url_for, flash, abort)
from flask_login import login_required, current_user

from models import db
from .fixed_store import (DatasheetFixedValue, BasicStandardMap,
                          get_fixed_values, invalidate_cache)

datasheet_admin_bp = Blueprint("datasheet_admin", __name__)

_LABELS = {
    "name": "Datasheet Name", "measurement_uncertainty": "Measurement Uncertainty",
    "sop_reference": "SOP Reference Number", "software": "Software Used",
    "spec_defaults": "Test Specification — Fixed Parameters", "test_limits": "Test Limits",
    "qp_30m_1g": "Quasi-peak Limits (30 MHz – 1 GHz)", "pa_1g_6g": "Peak & Average Limits (1 GHz – 6 GHz)",
    "by_class": "Limits by Class", "odd": "Odd Harmonics", "even": "Even Harmonics",
    "limits_rows": "Test Limits", "meas_rows": "Measurement Data", "fc_rows": "Functional Check — Flicker Measurements",
    "CISPR": "CISPR / ICES", "FCC": "FCC", "A": "Class A", "B": "Class B", "C": "Class C", "D": "Class D",
    # CE per-band limit keys
    "limit_qp_015_050": "0.15–0.50 MHz Quasi-peak", "limit_avg_015_050": "0.15–0.50 MHz Average",
    "limit_qp_050_5": "0.50–5 MHz Quasi-peak", "limit_avg_050_5": "0.50–5 MHz Average",
    "limit_qp_5_30": "5–30 MHz Quasi-peak", "limit_avg_5_30": "5–30 MHz Average",
}

# Column headers for the row-tables, keyed by a token in the field path.
_COLUMNS = [
    ("qp_30m_1g", ["Frequency (MHz)", "Quasi-peak Limit (dBµV/m)"]),
    ("pa_1g_6g", ["Frequency", "Peak Limit (dBµV/m)", "Average Limit (dBµV/m)"]),
    ("software", ["Software Name", "Software Version"]),
    ("odd", ["Harmonic (n)", "Limit (A)"]),
    ("even", ["Harmonic (n)", "Limit (A)"]),
    ("fc_rows", ["Parameter", "Plt", "Max Pst", "Max dc", "Max dmax", "Max Tmax"]),
    ("meas_rows", ["Flicker Measurement", "Measured Value", "Limit"]),
    ("limits_rows", ["Flicker Measurement", "Limit"]),
]


@datasheet_admin_bp.app_template_filter("ds_label")
def _ds_label(s):
    key = str(s)
    if key in _LABELS:
        return _LABELS[key]
    # friendlier column-pair suffixes
    t = key.replace("_col_1", " (30MHz-1GHz)").replace("_col_2", " (1GHz-6GHz)")
    t = t.replace("_2", " (30MHz-1GHz)").replace("_3", " (1GHz-6GHz)") if t.endswith(("_2", "_3")) else t
    return t.replace("_", " ").title().replace("(30Mhz-1Ghz)", "(30MHz-1GHz)").replace("(1Ghz-6Ghz)", "(1GHz-6GHz)")


@datasheet_admin_bp.app_template_filter("ds_columns")
def _ds_columns(path, width=0):
    """Column headers for a row-table at `path` (falls back to generic names)."""
    p = path or ""
    for token, cols in _COLUMNS:
        if p.endswith("~~" + token) or ("~~" + token + "~~") in p or p.endswith(token):
            return cols
    return ["Column %d" % (i + 1) for i in range(width or 1)]


@datasheet_admin_bp.app_template_filter("ds_scalar_dict")
def _ds_scalar_dict(v):
    """True for a dict whose values are all scalars (render as a Field | Value table)."""
    return isinstance(v, dict) and bool(v) and all(not isinstance(x, (dict, list)) for x in v.values())


def _require_admin():
    if not current_user.is_authenticated or getattr(current_user, "role", None) != "admin":
        abort(403)


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# --------------------------------------------------------------------------
# Read API (login-only) — datasheet forms pull DB-driven values from here
# --------------------------------------------------------------------------
@datasheet_admin_bp.route("/datasheet/g/<code>/fixed-values")
@login_required
def api_fixed_values(code):
    return jsonify(success=True, code=code.upper(), values=get_fixed_values(code))


# --------------------------------------------------------------------------
# Landing page — section cards
# --------------------------------------------------------------------------
@datasheet_admin_bp.route("/datasheet/admin/config")
@login_required
def config_page():
    _require_admin()
    rows = DatasheetFixedValue.query.order_by(DatasheetFixedValue.test_code.asc()).all()
    cards = [{"code": r.test_code, "name": r.name or r.test_code,
              "updated_at": r.updated_at.strftime("%Y-%m-%d %H:%M") if r.updated_at else ""}
             for r in rows]
    map_count = BasicStandardMap.query.count()
    # Section 3's card: how many datasheets have a procedure here, and how many an admin
    # has changed. Best-effort - the landing page must still render if the table is absent.
    try:
        from .procedure_store import updated_map
        proc_edited = len(updated_map())
    except Exception:  # noqa: BLE001
        proc_edited = 0
    return render_template("datasheet_gen/admin_home.html", cards=cards, map_count=map_count,
                           proc_count=len(_PROC_CODES), proc_edited=proc_edited)


# --------------------------------------------------------------------------
# Per-datasheet editor subpage
# --------------------------------------------------------------------------
@datasheet_admin_bp.route("/datasheet/admin/config/<code>")
@login_required
def config_datasheet(code):
    _require_admin()
    code = code.upper()
    row = DatasheetFixedValue.query.filter_by(test_code=code).first()
    values = row.values() if row else get_fixed_values(code)
    name = (row.name if row else None) or values.get("name", code)
    general = {k: v for k, v in values.items() if k != "name" and not isinstance(v, (dict, list))}
    other = {k: v for k, v in values.items() if k != "name" and isinstance(v, (dict, list))}
    return render_template("datasheet_gen/admin_datasheet.html", code=code, name=name,
                           general=general, other=other, values=values,
                           raw_json=json.dumps(values, ensure_ascii=False, indent=2),
                           updated_at=(row.updated_at.strftime("%Y-%m-%d %H:%M") if row and row.updated_at else ""))


def _set_path(root, segs, value):
    """Assign `value` at the nested path `segs` inside `root`, creating dicts (for
    non-numeric segments) and lists (for numeric segments) as needed."""
    cur = root
    for i, seg in enumerate(segs):
        last = (i == len(segs) - 1)
        as_list = seg.isdigit()
        key = int(seg) if as_list else seg
        if last:
            if as_list:
                while len(cur) <= key:
                    cur.append(None)
                cur[key] = value
            else:
                cur[key] = value
            return
        child_is_list = segs[i + 1].isdigit()
        if as_list:
            while len(cur) <= key:
                cur.append(None)
            if cur[key] is None:
                cur[key] = [] if child_is_list else {}
            cur = cur[key]
        else:
            if key not in cur or cur[key] is None:
                cur[key] = [] if child_is_list else {}
            cur = cur[key]


def _compact(node):
    """Drop list holes left by removed rows (None entries)."""
    if isinstance(node, dict):
        for v in node.values():
            _compact(v)
    elif isinstance(node, list):
        node[:] = [x for x in node if x is not None]
        for x in node:
            _compact(x)


def _reconstruct(form):
    """Rebuild the values dict from the structured `fv~~a~~b~~0~~1` inputs."""
    pairs = []
    for key in form.keys():
        if not key.startswith("fv~~"):
            continue
        segs = key.split("~~")[1:]
        for val in form.getlist(key):
            pairs.append((segs, val))
    # order so list indices are built ascending (numeric-aware)
    pairs.sort(key=lambda p: "/".join(s.zfill(6) if s.isdigit() else s for s in p[0]))
    root = {}
    for segs, val in pairs:
        _set_path(root, segs, val)
    _compact(root)
    return root


@datasheet_admin_bp.route("/datasheet/admin/config/<code>/save", methods=["POST"])
@login_required
def save_datasheet(code):
    _require_admin()
    code = code.upper()
    if request.form.get("mode") == "raw":
        try:
            parsed = json.loads(request.form.get("raw_json", ""))
            if not isinstance(parsed, dict):
                raise ValueError("must be a JSON object")
        except Exception as exc:  # noqa: BLE001
            flash(f"{code}: invalid JSON — {exc}", "error")
            return redirect(url_for("datasheet_admin.config_datasheet", code=code))
    else:
        parsed = _reconstruct(request.form)
        if not parsed:
            flash(f"{code}: nothing to save.", "error")
            return redirect(url_for("datasheet_admin.config_datasheet", code=code))
    row = DatasheetFixedValue.query.filter_by(test_code=code).first()
    if row is None:
        row = DatasheetFixedValue(test_code=code)
        db.session.add(row)
    row.values_json = json.dumps(parsed, ensure_ascii=False)
    row.name = request.form.get("name") or parsed.get("name") or row.name or code
    parsed.setdefault("name", row.name)
    row.values_json = json.dumps(parsed, ensure_ascii=False)
    row.updated_by = getattr(current_user, "id", None)
    db.session.commit()
    invalidate_cache()   # admin edited a cached table
    flash(f"Saved {code} values.", "success")
    return redirect(url_for("datasheet_admin.config_datasheet", code=code))


# --------------------------------------------------------------------------
# Product -> Basic standard mapping page + CRUD
# --------------------------------------------------------------------------
@datasheet_admin_bp.route("/datasheet/admin/standard-map")
@login_required
def standard_map_page():
    _require_admin()
    # This page edits the shared emission Product -> Basic mapping (test_code IS NULL).
    # Per-test overrides (HARMONIC / VOLTAGEFLICKER) keep their own rows and still drive
    # derivation, but are not shown here so the admin view stays simple.
    maps = BasicStandardMap.query.filter(BasicStandardMap.test_code.is_(None)).all()
    maps_sorted = sorted(maps, key=lambda m: (m.sort_order or 0, m.id))
    return render_template("datasheet_gen/admin_standard_map.html",
                           mappings=[m.to_dict() for m in maps_sorted])


@datasheet_admin_bp.route("/datasheet/admin/standard-map/add", methods=["POST"])
@login_required
def add_map():
    _require_admin()
    label = (request.form.get("product_label") or "").strip()
    basic = (request.form.get("basic_standard") or "").strip()
    if not label or not basic:
        flash("Product Standard and Basic Standard are required.", "error")
        return redirect(url_for("datasheet_admin.standard_map_page"))
    # Shared emission row. The Product Standard itself is the match key (normalized);
    # test_code / is_default / sort_order are managed internally, not shown to the admin.
    max_order = (db.session.query(db.func.max(BasicStandardMap.sort_order))
                 .filter(BasicStandardMap.test_code.is_(None)).scalar()) or 0
    db.session.add(BasicStandardMap(
        test_code=None,
        product_token=_norm(label),
        product_label=label,
        basic_standard=basic,
        is_default=False,
        sort_order=int(max_order) + 10,
        active=True,
    ))
    db.session.commit()
    invalidate_cache()   # admin edited a cached table
    flash("Mapping added.", "success")
    return redirect(url_for("datasheet_admin.standard_map_page"))


@datasheet_admin_bp.route("/datasheet/admin/standard-map/<int:mid>/update", methods=["POST"])
@login_required
def update_map(mid):
    _require_admin()
    m = db.session.get(BasicStandardMap, mid)
    if m is None:
        abort(404)
    label = (request.form.get("product_label") or "").strip()
    m.product_label = label
    m.product_token = _norm(label)          # the Product Standard is what gets matched
    m.basic_standard = (request.form.get("basic_standard") or "").strip()
    m.active = bool(request.form.get("active"))
    # test_code / is_default / sort_order are managed internally — left unchanged
    db.session.commit()
    invalidate_cache()   # admin edited a cached table
    flash("Mapping updated.", "success")
    return redirect(url_for("datasheet_admin.standard_map_page"))


@datasheet_admin_bp.route("/datasheet/admin/standard-map/<int:mid>/delete", methods=["POST"])
@login_required
def delete_map(mid):
    _require_admin()
    m = db.session.get(BasicStandardMap, mid)
    if m is not None:
        db.session.delete(m)
        db.session.commit()
        invalidate_cache()   # admin edited a cached table
        flash("Mapping row deleted.", "success")
    return redirect(url_for("datasheet_admin.standard_map_page"))


# --------------------------------------------------------------------------
# Section 3 — Test Procedures, centrally
#
# The procedure each datasheet ships, and the rule that rewrites its EUT-support
# wording from EUT Configuration. Both are editable here and stored per datasheet
# (procedure_store); procedures.effective_rule() lays what is stored over the
# built-in table, which stays the default and the fallback.
# --------------------------------------------------------------------------
_PROC_CODES = ("CE", "RE", "RS_RI", "CRF", "HARMONIC", "VOLTAGEFLICKER",
               "VOLTAGEDIPS", "SURGE", "EFT", "ESD", "PFMF")


def _proc_schema_default(code):
    """The procedure text the datasheet ships, with <Standard name> resolved the way the
    form resolves it - so the admin reads what an engineer would see, not a placeholder."""
    from .registry import load_schema
    from .generic_service import _DERIVED_BASIC_STANDARDS
    try:
        schema = load_schema(code)
    except Exception:  # noqa: BLE001 - a datasheet with no schema file (CE has its own)
        return ""
    found = [""]

    def walk(node):
        if isinstance(node, dict):
            if node.get("key") == "test_procedure":
                found[0] = node.get("default") or ""
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    basic = _DERIVED_BASIC_STANDARDS.get(code.upper(), "")
    return found[0].replace("<Standard name>", basic) if basic else found[0]


def _proc_view(code):
    """Everything the editor page needs for one datasheet."""
    from . import procedures as P
    from .procedure_store import get_override
    code = code.upper()
    stored, updated = get_override(code)
    builtin = P.SUPPORT_RULES.get(code) or {}
    rule = P.effective_rule(code) or {}
    shipped = _proc_schema_default(code)
    return {
        "code": code,
        "name": (get_fixed_values(code) or {}).get("name", code),
        "mode": rule.get("mode") or "",
        "tabletop": rule.get("tabletop", ""),
        "floor": rule.get("floor", ""),
        "builtin_tabletop": builtin.get("tabletop", ""),
        "builtin_floor": builtin.get("floor", ""),
        "procedure": stored.get("procedure") or shipped,
        "shipped": shipped,
        "overridden": sorted(stored.keys()),
        "updated_at": updated.strftime("%Y-%m-%d %H:%M") if updated else "",
        # the browser previews both configurations with the same rule the document uses
        "rule_json": json.dumps(
            {"mode": rule.get("mode"), "tabletop": rule.get("tabletop", ""),
             "floor": rule.get("floor", ""), "phrases": list(rule.get("phrases") or ())},
            ensure_ascii=False),
        "ports": json.dumps(P.PORT_BLOCKS.get(code) or {}, ensure_ascii=False),
        "coupling": (P.COUPLING_BY_PORT.get(code) or {}),
    }


@datasheet_admin_bp.route("/datasheet/admin/procedures")
@login_required
def procedures_page():
    _require_admin()
    from .procedure_store import updated_map
    from . import procedures as P
    edited = updated_map()
    cards = []
    for code in _PROC_CODES:
        rule = P.effective_rule(code) or {}
        cards.append({
            "code": code,
            "name": (get_fixed_values(code) or {}).get("name", code),
            "mode": rule.get("mode") or "",
            "has_rule": bool(rule),
            "ports": len(P.PORT_BLOCKS.get(code) or {}),
            "updated_at": edited.get(code, ""),
        })
    return render_template("datasheet_gen/admin_procedures.html", cards=cards)


@datasheet_admin_bp.route("/datasheet/admin/procedures/<code>")
@login_required
def procedure_datasheet(code):
    _require_admin()
    if code.upper() not in _PROC_CODES:
        abort(404)
    return render_template("datasheet_gen/admin_procedure_edit.html", **_proc_view(code))


@datasheet_admin_bp.route("/datasheet/admin/procedures/<code>/save", methods=["POST"])
@login_required
def procedure_save(code):
    _require_admin()
    if code.upper() not in _PROC_CODES:
        abort(404)
    from . import procedures as P
    from .procedure_store import save_override
    view = _proc_view(code)
    payload = {
        "tabletop": request.form.get("tabletop", ""),
        "floor": request.form.get("floor", ""),
        "procedure": request.form.get("procedure", ""),
    }
    # Only what DIFFERS from the built-in is stored, so a datasheet left as it ships keeps
    # following the code, and "same as built-in" never becomes a frozen copy that stops
    # tracking a later change to the rule.
    if (payload["tabletop"] or "").strip() == (view["builtin_tabletop"] or "").strip():
        payload["tabletop"] = ""
    if (payload["floor"] or "").strip() == (view["builtin_floor"] or "").strip():
        payload["floor"] = ""
    if (payload["procedure"] or "").strip() == (view["shipped"] or "").strip():
        payload["procedure"] = ""
    # A phrase rule needs both wordings or neither: one alone would rewrite one
    # configuration and leave the other saying whatever the text already said.
    if view["mode"] != "variant" and bool(payload["tabletop"]) != bool(payload["floor"]):
        flash("Give both wordings, or neither - one alone would leave the other "
              "configuration unchanged.", "error")
        return redirect(url_for("datasheet_admin.procedure_datasheet", code=code.upper()))
    saved = save_override(code, payload, getattr(current_user, "id", None))
    P.invalidate_override_cache()
    flash("Saved. %s" % ("Back to the built-in wording." if not saved
                         else "Takes effect on the next form load and generation."),
          "success")
    return redirect(url_for("datasheet_admin.procedure_datasheet", code=code.upper()))


@datasheet_admin_bp.route("/datasheet/admin/procedures/<code>/reset", methods=["POST"])
@login_required
def procedure_reset(code):
    _require_admin()
    if code.upper() not in _PROC_CODES:
        abort(404)
    from . import procedures as P
    from .procedure_store import clear_override
    clear_override(code)
    P.invalidate_override_cache()
    flash("Back to the built-in wording for %s." % code.upper(), "success")
    return redirect(url_for("datasheet_admin.procedure_datasheet", code=code.upper()))
