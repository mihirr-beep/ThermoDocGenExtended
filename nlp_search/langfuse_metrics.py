# -*- coding: utf-8 -*-
"""Fetch cost + token usage from Langfuse for the admin usage dashboard.

Aggregates from Langfuse's **observations** API (per-call model, token usage and
cost) and **traces** API (per-question rows + counts). We deliberately do NOT
use the Daily Metrics API (`/api/public/metrics/daily`) because on this plan it
is limited to only 10 requests PER DAY — the observations/traces endpoints have
normal limits. Read-only; uses the same LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY
as the tracer. Never raises — returns {"success": False, "message": ...} on any
problem so the UI can show it, and results are cached to stay under the limits.
"""
import base64
import datetime
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import os

_TIMEOUT = 25
MAX_DAYS = 90
_MAX_PAGES = 30          # safety bound on pagination
_PAGE = 100             # Langfuse max page size


def available():
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def _base():
    return (os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST")
            or "https://cloud.langfuse.com").rstrip("/")


def _auth():
    pk = os.environ["LANGFUSE_PUBLIC_KEY"]
    sk = os.environ["LANGFUSE_SECRET_KEY"]
    return base64.b64encode(("%s:%s" % (pk, sk)).encode()).decode()


def _get(path):
    req = urllib.request.Request(_base() + path, headers={"Authorization": "Basic " + _auth()})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode())


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_text(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return str(v)


def _http_message(exc):
    """Friendly message for an HTTPError."""
    code = getattr(exc, "code", None)
    if code == 429:
        return "Langfuse is rate-limiting requests right now. Please wait a moment and refresh."
    if code in (401, 403):
        return "Langfuse rejected the credentials (HTTP %s). Check the LANGFUSE keys." % code
    return "Could not reach Langfuse (HTTP %s)." % code


def _fetch_observations(frm, now):
    """All cost-bearing generation observations in range -> list of
    {model, input, output, total, cost, day}. Raises HTTPError/URLError up to
    the caller so it can craft a friendly message."""
    base_qs = {"type": "GENERATION", "fromStartTime": _iso(frm), "toStartTime": _iso(now), "limit": _PAGE}
    out, page = [], 1
    while page <= _MAX_PAGES:
        qs = urllib.parse.urlencode(dict(base_qs, page=page))
        d = _get("/api/public/observations?" + qs)
        for o in d.get("data") or []:
            u = o.get("usage") or {}
            inp = u.get("input") or 0
            outp = u.get("output") or 0
            cost = o.get("calculatedTotalCost")
            if cost is None:
                cost = (o.get("costDetails") or {}).get("total") or 0
            out.append({
                "model": o.get("model") or "(no model)",
                "input": inp, "output": outp,
                "total": u.get("total") or (inp + outp),
                "cost": cost or 0,
                "day": (o.get("startTime") or "")[:10],
            })
        meta = d.get("meta") or {}
        if not d.get("data") or page >= (meta.get("totalPages") or 1):
            break
        page += 1
    return out


def _fetch_requests(frm, now, limit=100):
    """Per-question rows (traces tagged 'nl-search') + the total count.
    Best-effort: returns ([], 0) on error so it can't break the aggregates."""
    qs = urllib.parse.urlencode({
        "tags": "nl-search",
        "fromTimestamp": _iso(frm), "toTimestamp": _iso(now),
        "orderBy": "timestamp.DESC",
        "limit": max(1, min(int(limit), 100)),
    })
    try:
        d = _get("/api/public/traces?" + qs) or {}
    except Exception:  # noqa: BLE001
        return [], 0
    total = (d.get("meta") or {}).get("totalItems") or 0
    rows = []
    for t in d.get("data") or []:
        meta = t.get("metadata") or {}
        rows.append({
            "timestamp": t.get("timestamp"),
            "question": _as_text(t.get("input") if t.get("input") is not None else t.get("name"))[:400],
            "route": meta.get("route") if isinstance(meta, dict) else None,
            "cost": round(t.get("totalCost") or 0, 6),
            "latency": round(t.get("latency"), 2) if isinstance(t.get("latency"), (int, float)) else None,
            "user": t.get("userId"),
        })
    rows.sort(key=lambda r: r["timestamp"] or "", reverse=True)
    return rows, total


_CACHE_TTL = int(os.environ.get("NLP_USAGE_CACHE_TTL") or 120)
_cache = {}  # days -> (fetched_at, result)


def fetch_usage(days=1):
    """Cached wrapper: reuses a per-`days` result for NLP_USAGE_CACHE_TTL seconds
    (default 120) so the dashboard stays well under Langfuse's API rate limit, and
    serves the last good result (flagged ``stale``) if a live refresh is
    rate-limited or errors — so the admin still sees numbers instead of a 503."""
    try:
        d = max(1, min(int(days), MAX_DAYS))
    except (TypeError, ValueError):
        d = 1
    now = time.time()
    entry = _cache.get(d)
    if entry and (now - entry[0] < _CACHE_TTL):
        return dict(entry[1], cached=True)
    result = _compute_usage(d)
    if result.get("success"):
        _cache[d] = (now, result)
        return result
    if entry:  # refresh failed — serve last good data rather than an error
        stale = dict(entry[1])
        stale["stale"] = True
        stale["notice"] = result.get("message") or "Live refresh unavailable — showing last loaded data."
        return stale
    return result


def _compute_usage(days=1):
    """Return a cost/token summary for the last `days` calendar days (UTC),
    computed from the observations + traces APIs."""
    if not available():
        return {"success": False,
                "message": "Langfuse is not configured. Add LANGFUSE_PUBLIC_KEY / "
                           "LANGFUSE_SECRET_KEY to the .env file to see usage."}
    try:
        days = max(1, min(int(days), MAX_DAYS))
    except (TypeError, ValueError):
        days = 1

    today0 = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    frm = today0 - datetime.timedelta(days=days - 1)
    now = datetime.datetime.utcnow()

    try:
        obs = _fetch_observations(frm, now)
    except urllib.error.HTTPError as exc:
        return {"success": False, "message": _http_message(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "message": "Could not reach Langfuse: %s" % exc}

    tot_cost = tin = tout = 0
    models, day_map = {}, {}
    for o in obs:
        tot_cost += o["cost"]
        tin += o["input"]
        tout += o["output"]
        m = models.setdefault(o["model"], {"model": o["model"], "calls": 0,
                                           "input": 0, "output": 0, "total": 0, "cost": 0.0})
        m["calls"] += 1
        m["input"] += o["input"]
        m["output"] += o["output"]
        m["total"] += o["total"]
        m["cost"] += o["cost"]
        if o["day"]:
            dd = day_map.setdefault(o["day"], {"date": o["day"], "cost": 0.0, "tokens": 0})
            dd["cost"] += o["cost"]
            dd["tokens"] += o["total"]

    by_request, req_total = _fetch_requests(frm, now, limit=100)

    by_model = [dict(m, cost=round(m["cost"], 6)) for m in models.values()]
    by_model.sort(key=lambda x: -x["cost"])
    by_day = [dict(x, cost=round(x["cost"], 6)) for x in day_map.values()]
    by_day.sort(key=lambda x: x["date"], reverse=True)

    label = "Today" if days == 1 else ("Last %d days" % days)
    return {
        "success": True,
        "range": {"days": days, "label": label,
                  "from": frm.strftime("%Y-%m-%d %H:%M UTC"),
                  "to": now.strftime("%Y-%m-%d %H:%M UTC")},
        "totals": {
            "cost": round(tot_cost, 6),
            "requests": req_total,
            "input_tokens": tin,
            "output_tokens": tout,
            "total_tokens": tin + tout,
            "avg_cost_per_request": round(tot_cost / req_total, 6) if req_total else 0.0,
        },
        "by_model": by_model,
        "by_day": by_day,
        "by_request": by_request,
    }
