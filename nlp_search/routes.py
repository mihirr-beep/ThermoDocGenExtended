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
    return render_template("nlp_search/usage.html")


@nlp_search_bp.route("/admin/nlp-search/usage-data")
@login_required
def usage_data():
    """Cost + token usage + per-question audit log for the last N days
    (default 1), read from the nlp_search_audit table (DB-backed = durable, no
    external rate limit)."""
    _require_admin()
    from . import audit
    result = audit.usage_summary(request.args.get("days", 1))
    return jsonify(**result), (200 if result.get("success") else 500)


@nlp_search_bp.route("/admin/nlp-search/history")
@login_required
def history():
    """This admin's own past turns, so a page refresh does not lose the chat.

    Served from nlp_search_audit, which already records every question and answer
    against a user_id. `starts_conversation` marks where a long gap fell, so the
    panel can show earlier threads while sending only the current one as context.
    """
    _require_admin()
    from . import audit
    turns = audit.recent_turns(getattr(current_user, "id", None))
    return jsonify(success=True, turns=turns,
                   gap_minutes=audit.CONVERSATION_GAP_MINUTES), 200


def _history(raw):
    """Sanitise the browser-supplied conversation history.

    Shape-checked and bounded here rather than trusted, because it arrives from
    the client: a caller can send any number of turns of any length, claiming any
    role. The orchestrator treats history as context and never as evidence (see
    _history_block there), so a forged turn can at worst misdirect a question -
    but it should not be able to blow the prompt budget either.
    """
    if not isinstance(raw, list):
        return []
    out = []
    for turn in raw[-(orchestrator.MAX_HISTORY_TURNS * 2):]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip().lower()
        text = str(turn.get("text") or "").strip()
        if role in ("user", "assistant") and text:
            out.append({"role": role, "text": text[:MAX_TURN_CHARS]})
    return out[-orchestrator.MAX_HISTORY_TURNS:]


# One turn's text, before the orchestrator trims answers further. Generous
# enough for a real question, small enough that a hostile client cannot make the
# prompt arbitrarily large.
MAX_TURN_CHARS = 4000


@nlp_search_bp.route("/admin/nlp-search/ask", methods=["POST"])
@login_required
def ask():
    _require_admin()
    payload = request.get_json(silent=True) or {}
    question = (payload.get("question") or "").strip()
    try:
        result = orchestrator.answer(question, _db_params(),
                                     user=getattr(current_user, "username", None),
                                     user_id=getattr(current_user, "id", None),
                                     history=_history(payload.get("history")))
    except Exception as exc:  # noqa: BLE001 - belt & suspenders; answer() shouldn't raise
        current_app.logger.error("NL search error: %s", exc)
        return jsonify(success=False, message="NL search failed unexpectedly."), 500
    status = 200 if result.get("success") else 400
    if not result.get("success") and "OPENAI_API_KEY" in (result.get("message") or ""):
        status = 503
    return jsonify(**result), status
