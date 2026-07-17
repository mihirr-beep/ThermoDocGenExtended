# -*- coding: utf-8 -*-
"""Admin-only NL search: page + ask endpoint.

GET  /admin/nlp-search      - the search UI (admins only)
POST /admin/nlp-search/ask  - {"question": "..."} -> {"success", "answer", "steps"}

The heavy lifting lives in orchestrator.py; this layer only guards access,
shapes JSON, and hands over the DB connection parameters (captured here from
Flask config because agent tools may run outside the app context).
"""
from flask import Blueprint, current_app, jsonify, render_template, request, abort
from flask_login import login_required, current_user

from . import orchestrator

nlp_search_bp = Blueprint("nlp_search", __name__, template_folder="templates")


def _require_admin():
    if not current_user.is_authenticated or getattr(current_user, "role", None) != "admin":
        abort(403)


def _db_params():
    cfg = current_app.config
    return {
        "host": cfg.get("MYSQL_HOST", "localhost"),
        "port": cfg.get("MYSQL_PORT", 3306),
        "user": cfg.get("MYSQL_USER", "root"),
        "password": cfg.get("MYSQL_PASSWORD", ""),
        "database": cfg.get("MYSQL_DATABASE", ""),
    }


@nlp_search_bp.route("/admin/nlp-search")
@login_required
def page():
    _require_admin()
    return render_template("nlp_search/nlp_search.html")


@nlp_search_bp.route("/admin/nlp-search/ask", methods=["POST"])
@login_required
def ask():
    _require_admin()
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    try:
        result = orchestrator.answer(question, _db_params())
    except Exception as exc:  # noqa: BLE001 - belt & suspenders; answer() shouldn't raise
        current_app.logger.error("NL search error: %s", exc)
        return jsonify(success=False, message="NL search failed unexpectedly."), 500
    status = 200 if result.get("success") else 400
    if not result.get("success") and "OPENAI_API_KEY" in (result.get("message") or ""):
        status = 503
    return jsonify(**result), status
