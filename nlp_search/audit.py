# -*- coding: utf-8 -*-
"""Audit log for the NL-search assistant.

Every question is recorded in the `nlp_search_audit` table: who asked, the
question, the answer, the route taken, the SQL that ran, token counts, an
estimated cost, latency, and (for correlation) the Langfuse trace id. This is
the durable source of truth for auditing — it does not depend on Langfuse and
is not subject to any external rate limit.

The admin usage dashboard reads its numbers from this table too, so the whole
dashboard is DB-backed. All writes are best-effort and never break a response.
"""
import datetime
import json

_TABLE = "nlp_search_audit"

_CREATE = """
CREATE TABLE IF NOT EXISTS nlp_search_audit (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  created_at DATETIME NOT NULL,
  user_id INT NULL,
  username VARCHAR(150) NULL,
  question TEXT NOT NULL,
  answer MEDIUMTEXT NULL,
  route VARCHAR(20) NULL,
  model VARCHAR(100) NULL,
  input_tokens INT NULL,
  output_tokens INT NULL,
  total_tokens INT NULL,
  estimated_cost_usd DECIMAL(12,6) NULL,
  latency_ms INT NULL,
  tool_calls VARCHAR(120) NULL,
  sql_queries TEXT NULL,
  success TINYINT NOT NULL DEFAULT 1,
  error TEXT NULL,
  trace_id VARCHAR(64) NULL,
  KEY idx_nsa_created (created_at),
  KEY idx_nsa_user (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# per 1,000,000 tokens: (input $, output $). Used to estimate cost from tokens.
PRICING = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
}
_DEFAULT_PRICE = (0.15, 0.60)   # gpt-4o-mini


def estimate_cost(model, input_tokens, output_tokens):
    m = (model or "").lower()
    price = _DEFAULT_PRICE
    for key, p in PRICING.items():
        if m.startswith(key):
            price = p
            break
    return round((input_tokens or 0) / 1e6 * price[0] + (output_tokens or 0) / 1e6 * price[1], 6)


def ensure_audit_table(app):
    """Create nlp_search_audit if missing. Idempotent, best-effort."""
    try:
        from models import db
        from sqlalchemy import inspect, text
    except Exception:  # noqa: BLE001
        return
    with app.app_context():
        try:
            if _TABLE not in inspect(db.engine).get_table_names():
                db.session.execute(text(_CREATE))
                db.session.commit()
                app.logger.info("nlp_search: created table %s", _TABLE)
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            if "exist" not in str(exc).lower():
                app.logger.error("nlp_search: could not create %s: %s", _TABLE, exc)


def log_query(question, answer=None, user_id=None, username=None, route=None,
              model=None, input_tokens=None, output_tokens=None, total_tokens=None,
              latency_ms=None, tool_calls=None, sql_queries=None, success=True,
              error=None, trace_id=None, cost=None):
    """Insert one audit row. Best-effort — never raises."""
    try:
        from models import db
        from sqlalchemy import text
        if total_tokens is None and (input_tokens is not None or output_tokens is not None):
            total_tokens = (input_tokens or 0) + (output_tokens or 0)
        if cost is None:
            cost = estimate_cost(model, input_tokens, output_tokens)
        params = {
            "created_at": datetime.datetime.utcnow(),
            "user_id": user_id,
            "username": (username or None),
            "question": (question or "")[:65000],
            "answer": (answer or None),
            "route": (route or None),
            "model": (model or None),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": cost,
            "latency_ms": latency_ms,
            "tool_calls": (tool_calls or None),
            "sql_queries": (sql_queries or None),
            "success": 1 if success else 0,
            "error": (str(error)[:65000] if error else None),
            "trace_id": (trace_id or None),
        }
        db.session.execute(text("""
            INSERT INTO nlp_search_audit
              (created_at, user_id, username, question, answer, route, model,
               input_tokens, output_tokens, total_tokens, estimated_cost_usd,
               latency_ms, tool_calls, sql_queries, success, error, trace_id)
            VALUES
              (:created_at, :user_id, :username, :question, :answer, :route, :model,
               :input_tokens, :output_tokens, :total_tokens, :estimated_cost_usd,
               :latency_ms, :tool_calls, :sql_queries, :success, :error, :trace_id)
        """), params)
        db.session.commit()
        return True
    except Exception:  # noqa: BLE001 - auditing must never break the response
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return False


def _frm(days):
    try:
        days = max(1, min(int(days), 365))
    except (TypeError, ValueError):
        days = 1
    today0 = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    return today0 - datetime.timedelta(days=days - 1), days


def _f(v):
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def usage_summary(days=1, limit=100):
    """Cost/token summary + per-question audit rows for the last `days` days,
    read entirely from the audit table. Same JSON shape the dashboard expects."""
    try:
        from models import db
        from sqlalchemy import text
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": "Database unavailable: %s" % exc}
    frm, days = _frm(days)
    now = datetime.datetime.utcnow()
    try:
        tot = db.session.execute(text(
            "SELECT COUNT(*) reqs, COALESCE(SUM(input_tokens),0) inp, "
            "COALESCE(SUM(output_tokens),0) outp, COALESCE(SUM(total_tokens),0) tot, "
            "COALESCE(SUM(estimated_cost_usd),0) cost "
            "FROM nlp_search_audit WHERE created_at >= :frm"), {"frm": frm}).mappings().first()
        models = db.session.execute(text(
            "SELECT model, COUNT(*) calls, COALESCE(SUM(input_tokens),0) inp, "
            "COALESCE(SUM(output_tokens),0) outp, COALESCE(SUM(total_tokens),0) tot, "
            "COALESCE(SUM(estimated_cost_usd),0) cost "
            "FROM nlp_search_audit WHERE created_at >= :frm AND model IS NOT NULL "
            "GROUP BY model ORDER BY cost DESC"), {"frm": frm}).mappings().all()
        daily = db.session.execute(text(
            "SELECT DATE(created_at) d, COALESCE(SUM(total_tokens),0) tokens, "
            "COALESCE(SUM(estimated_cost_usd),0) cost "
            "FROM nlp_search_audit WHERE created_at >= :frm "
            "GROUP BY DATE(created_at) ORDER BY d DESC"), {"frm": frm}).mappings().all()
        rows = db.session.execute(text(
            "SELECT created_at, username, question, answer, route, model, total_tokens, "
            "estimated_cost_usd, latency_ms, success FROM nlp_search_audit "
            "WHERE created_at >= :frm ORDER BY created_at DESC LIMIT :lim"),
            {"frm": frm, "lim": max(1, min(int(limit), 500))}).mappings().all()
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": "Could not read the audit log: %s" % exc}

    reqs = int(tot["reqs"] or 0)
    cost = _f(tot["cost"])

    def _ts(v):
        try:
            return v.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:  # noqa: BLE001
            return str(v)

    label = "Today" if days == 1 else ("Last %d days" % days)
    return {
        "success": True,
        "range": {"days": days, "label": label,
                  "from": frm.strftime("%Y-%m-%d %H:%M UTC"),
                  "to": now.strftime("%Y-%m-%d %H:%M UTC")},
        "totals": {
            "cost": round(cost, 6),
            "requests": reqs,
            "input_tokens": int(tot["inp"] or 0),
            "output_tokens": int(tot["outp"] or 0),
            "total_tokens": int(tot["tot"] or 0),
            "avg_cost_per_request": round(cost / reqs, 6) if reqs else 0.0,
        },
        "by_model": [{"model": m["model"], "calls": int(m["calls"]),
                      "input": int(m["inp"] or 0), "output": int(m["outp"] or 0),
                      "total": int(m["tot"] or 0), "cost": round(_f(m["cost"]), 6)} for m in models],
        "by_day": [{"date": str(x["d"]), "tokens": int(x["tokens"] or 0),
                    "cost": round(_f(x["cost"]), 6)} for x in daily],
        "by_request": [{
            "timestamp": _ts(r["created_at"]),
            "user": r["username"],
            "question": r["question"],
            "answer": r["answer"],
            "route": r["route"],
            "tokens": int(r["total_tokens"] or 0),
            "cost": round(_f(r["estimated_cost_usd"]), 6),
            "success": bool(r["success"]),
        } for r in rows],
        "estimated": True,
    }
