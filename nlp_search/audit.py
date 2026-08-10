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
  route VARCHAR(120) NULL,
  model VARCHAR(100) NULL,
  input_tokens INT NULL,
  cached_tokens INT NULL,
  output_tokens INT NULL,
  total_tokens INT NULL,
  estimated_cost_usd DECIMAL(12,6) NULL,
  latency_ms INT NULL,
  tool_calls VARCHAR(255) NULL,
  sql_queries TEXT NULL,
  success TINYINT NOT NULL DEFAULT 1,
  error TEXT NULL,
  trace_id VARCHAR(64) NULL,
  KEY idx_nsa_created (created_at),
  KEY idx_nsa_user (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# per 1,000,000 tokens: (input $, output $, CACHED input $).
#
# The cached rate is not a detail here, it is most of the bill. This system
# sends a large, near-identical prefix every call - system prompt, lab rules,
# join paths, the worker's slice of the catalog - and measured on a live
# question 20,864 of 21,432 input tokens came back marked cached: 97%. Input
# is ~98% of all tokens, so pricing it at the full rate overstates the true
# cost by about 2x, and a dashboard that does that is worse than none.
#
# Longest key wins, so "gpt-4o-mini" is not matched by the "gpt-4o" prefix.
PRICING = {
    "gpt-4o-mini": (0.15, 0.60, 0.075),
    "gpt-4o": (2.50, 10.0, 1.25),
    # The GPT-5 tier. Two things about it differ from the 4o tier and both
    # matter here. The cached rate is a TENTH of input rather than a half,
    # which suits a pipeline where 68% of input is cached; and output is
    # dear - $2.00 against gpt-4o-mini's $0.60 - which only shows up on the
    # bill if the model emits reasoning tokens, since those bill as output.
    "gpt-5-mini": (0.25, 2.00, 0.025),
    "gpt-5-nano": (0.05, 0.40, 0.005),
    "gpt-5": (1.25, 10.00, 0.125),
    "gpt-4.1": (2.00, 8.00, 0.50),
    "gpt-4.1-mini": (0.40, 1.60, 0.10),
    "gpt-4.1-nano": (0.10, 0.40, 0.025),
    "text-embedding-3-small": (0.02, 0.0, 0.02),
    "text-embedding-3-large": (0.13, 0.0, 0.13),
}
_DEFAULT_PRICE = (0.15, 0.60, 0.075)   # gpt-4o-mini

# Rates move. Check https://openai.com/api/pricing/ before quoting these.
PRICING_CHECKED_ON = "2026-08-07"


def estimate_cost(model, input_tokens, output_tokens, cached_tokens=None):
    """Cost in USD. `cached_tokens` is the part of input_tokens served from
    the prompt cache, and is billed at the cheaper cached rate."""
    m = (model or "").lower()
    price = _DEFAULT_PRICE
    for key in sorted(PRICING, key=len, reverse=True):
        if m.startswith(key):
            price = PRICING[key]
            break
    inp = input_tokens or 0
    cached = min(cached_tokens or 0, inp)
    fresh = inp - cached
    return round(fresh / 1e6 * price[0]
                 + cached / 1e6 * price[2]
                 + (output_tokens or 0) / 1e6 * price[1], 6)


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
            else:
                _widen_columns(app, db, text, inspect)
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            if "exist" not in str(exc).lower():
                app.logger.error("nlp_search: could not create %s: %s", _TABLE, exc)


# route is a "+"-joined list of the workers a question touched, so it grows
# with the number of domains. At VARCHAR(20) every multi-domain question -
# "datasheets+inventory+semantics" is 30 characters - failed to insert under
# strict mode, silently, because log_query swallows its errors by design. The
# audit table was therefore missing precisely the expensive questions, which
# is the worst possible sample to lose from a cost record.
_WIDEN = {"route": "VARCHAR(120)", "tool_calls": "VARCHAR(255)"}
_ADD = {"cached_tokens": "INT NULL AFTER input_tokens"}


def _widen_columns(app, db, text, inspect):
    try:
        have = {c["name"]: c for c in inspect(db.engine).get_columns(_TABLE)}
    except Exception:  # noqa: BLE001
        return
    for col, decl in _ADD.items():
        if col in have:
            continue
        try:
            db.session.execute(text("ALTER TABLE %s ADD COLUMN %s %s" % (_TABLE, col, decl)))
            db.session.commit()
            app.logger.info("nlp_search: added %s.%s", _TABLE, col)
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            app.logger.warning("nlp_search: could not add %s.%s: %s", _TABLE, col, exc)
    for col, decl in _WIDEN.items():
        info = have.get(col)
        if info is None:
            continue
        want = int(decl.split("(")[1].rstrip(")"))
        cur = getattr(info.get("type"), "length", None)
        if cur is not None and cur >= want:
            continue
        try:
            db.session.execute(text("ALTER TABLE %s MODIFY COLUMN %s %s NULL"
                                    % (_TABLE, col, decl)))
            db.session.commit()
            app.logger.info("nlp_search: widened %s.%s to %s", _TABLE, col, decl)
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            app.logger.warning("nlp_search: could not widen %s.%s: %s",
                               _TABLE, col, exc)


def log_query(question, answer=None, user_id=None, username=None, route=None,
              model=None, input_tokens=None, output_tokens=None, total_tokens=None,
              cached_tokens=None,
              latency_ms=None, tool_calls=None, sql_queries=None, success=True,
              error=None, trace_id=None, cost=None):
    """Insert one audit row. Best-effort — never raises."""
    try:
        from models import db
        from sqlalchemy import text
        if total_tokens is None and (input_tokens is not None or output_tokens is not None):
            total_tokens = (input_tokens or 0) + (output_tokens or 0)
        if cost is None:
            cost = estimate_cost(model, input_tokens, output_tokens, cached_tokens)
        params = {
            "created_at": datetime.datetime.utcnow(),
            "user_id": user_id,
            "username": (username or None),
            "question": (question or "")[:65000],
            "answer": (answer or None),
            "route": ((route or None) and str(route)[:120]),
            "model": (model or None),
            "input_tokens": input_tokens,
            "cached_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": cost,
            "latency_ms": latency_ms,
            "tool_calls": ((tool_calls or None) and str(tool_calls)[:255]),
            "sql_queries": (sql_queries or None),
            "success": 1 if success else 0,
            "error": (str(error)[:65000] if error else None),
            "trace_id": (trace_id or None),
        }
        db.session.execute(text("""
            INSERT INTO nlp_search_audit
              (created_at, user_id, username, question, answer, route, model,
               input_tokens, cached_tokens, output_tokens, total_tokens,
               estimated_cost_usd, latency_ms, tool_calls, sql_queries, success,
               error, trace_id)
            VALUES
              (:created_at, :user_id, :username, :question, :answer, :route, :model,
               :input_tokens, :cached_tokens, :output_tokens, :total_tokens,
               :estimated_cost_usd, :latency_ms, :tool_calls, :sql_queries, :success,
               :error, :trace_id)
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
