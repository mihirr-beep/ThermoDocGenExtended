# -*- coding: utf-8 -*-
"""Read-only SQL execution for the NL->SQL search feature.

Runs an LLM-written SELECT (already passed through sql_guard) against MySQL
with hard runtime caps:

  * a DEDICATED short-lived PyMySQL connection per query - never the app's
    SQLAlchemy pool, so the READ ONLY / timeout session settings can never
    leak into a pooled connection the app later uses for writes;
  * SET SESSION TRANSACTION READ ONLY  - the server itself refuses writes
    even if a hostile statement somehow slipped past the validator;
  * SET SESSION MAX_EXECUTION_TIME    - the server kills long SELECTs;
  * a row cap and a response-size cap  - results are for an LLM to read,
    not a bulk export.

Connection parameters are passed in explicitly (captured from Flask config at
request time) because agent tools may execute outside the Flask app context.
"""
import datetime
import decimal
import json
import re

import pymysql

from . import sql_guard
from .ledger import BudgetExceeded
from .schema_catalog import ALLOWED_TABLES, DENIED_STAR_TABLES

MAX_ROWS = 200            # hard cap on rows returned to the model
MAX_CELL_CHARS = 400      # long text cells are truncated
MAX_RESULT_CHARS = 15000  # total JSON size budget for a tool result
STATEMENT_TIMEOUT_MS = 8000
CONNECT_TIMEOUT_S = 5
READ_TIMEOUT_S = 15


def _jsonable(v):
    if v is None or isinstance(v, (int, float, str, bool)):
        if isinstance(v, str) and len(v) > MAX_CELL_CHARS:
            return v[:MAX_CELL_CHARS] + "…[truncated]"
        return v
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat(sep=" ") if isinstance(v, datetime.datetime) else str(v)
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, datetime.timedelta):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        try:
            s = v.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            s = repr(v)
        return s[:MAX_CELL_CHARS]
    return str(v)[:MAX_CELL_CHARS]


def _fail(ledger, worker, sql, message):
    """Record a failed query and hand the model a message it can act on."""
    if ledger is not None:
        ledger.record(worker, sql, error=message)
    return json.dumps({"error": message})


_B = chr(92) + "b"      # a literal backslash-b; inline it keeps being eaten
_FROM_RE = re.compile(_B + r"FROM\s+`?([A-Za-z_][A-Za-z0-9_]*)`?", re.I)
_JOIN_RE = re.compile(_B + "JOIN" + _B, re.I)


def _join_base(sql):
    """The driving table of a joined SELECT, or None when there is no join."""
    if not _JOIN_RE.search(sql or ""):
        return None
    m = _FROM_RE.search(sql)
    return m.group(1) if m else None


def _is_zero(value):
    """True for a numeric zero, however the driver typed it. Not for None."""
    if value is None or isinstance(value, bool):
        return False
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _emptiness_note(rows):
    """What to tell the model when rows came back but hold nothing.

    THE ZERO-ROWS NOTE DOES NOT COVER THIS, and that gap produced the worst
    answer measured so far. Asked "has the final report been uploaded for job
    TFS-EMC-2026-002", the model ran a query shaped
    ``SELECT (SELECT COUNT(*) ...), (SELECT COUNT(*) ...)`` which returned ONE row
    holding [0, 0]. row_count was 1, so the empty-result warning never fired, the
    model saw a successful result with data in it, and answered "Yes, the final
    report has been uploaded." Nothing had been uploaded.

    A row of zeros is not zero rows, and it is the more dangerous of the two
    precisely because it looks like a result. NULL is a third thing again: it
    means nobody recorded the value, which is neither zero nor "no" - and this
    lab has columns in exactly that state (workflow_status NULL on 35 rows,
    job_number '' on four requests).

    Returned as payload keys so the warning arrives at the moment the mistake
    would be made, not in a standing prompt thousands of tokens earlier.
    """
    cells = [v for row in rows for v in row]
    if not cells:
        return {}
    nulls = sum(1 for v in cells if v is None)

    if nulls == len(cells):
        return {"note": (
            "EVERY VALUE CAME BACK NULL. The column exists and is not filled in "
            "for these rows - nobody has recorded it. NULL is not zero, not 'no' "
            "and not 'none': report that the value is NOT RECORDED, and do not "
            "put a number in its place. Name the field that is blank, so someone "
            "can go and fill it in.")}

    if len(rows) == 1 and all(_is_zero(v) or v is None for v in cells):
        return {"note": (
            "THIS RESULT IS ZERO. A zero from COUNT/SUM is not an answer to a "
            "yes/no question and not proof that nothing happened - it is equally "
            "consistent with the filter having matched nothing at all. Before "
            "answering 'yes', 'no' or 'none', confirm the value you filtered on "
            "exists. If you filtered by a job, check you used the right column: "
            "tco_id and job_number are DIFFERENT, and a job number used in a "
            "tco_id filter matches nothing and returns exactly this.")}

    if nulls:
        return {"nulls": (
            "%d of %d values are NULL - not recorded, not zero. Say so for those "
            "rows rather than reporting a blank as a value." % (nulls, len(cells)))}
    return {}


def run_select(sql, db_params, allowed_tables=None, ledger=None, worker="sql"):
    """Validate + execute one SELECT. Returns a JSON string for the model.

    Success: {"columns": [...], "rows": [[...], ...], "row_count": N,
              "truncated": bool}
    Failure: {"error": "..."} - written so the model can fix its SQL and retry.
    Never raises.

    ``allowed_tables`` narrows the allowlist to one worker's domain, so a
    worker physically cannot read outside it however its SQL is phrased.
    ``ledger`` (when given) receives the executed SQL and every row returned -
    that record, not the model's account of it, is what the answer is later
    checked against.
    """
    tables = allowed_tables or ALLOWED_TABLES

    if ledger is not None:
        try:
            ledger.check_budget()
        except BudgetExceeded as exc:
            return json.dumps({"error": str(exc), "budget_exceeded": True})

    ok, reason, cleaned = sql_guard.validate_sql(
        sql, tables, denied_star_tables=DENIED_STAR_TABLES)
    if not ok:
        if ledger is not None:
            ledger.record(worker, sql, error=reason)
        return json.dumps({"error": reason})

    # Bound the row count on the SERVER: clamp any oversized LIMIT down to
    # MAX_ROWS, or append LIMIT (MAX_ROWS+1) when absent. The +1 in the
    # append case lets us tell "exactly full" from "there were more".
    cleaned, forced_truncated = sql_guard.enforce_row_cap(cleaned, MAX_ROWS)

    conn = None
    try:
        conn = pymysql.connect(
            host=db_params["host"], port=int(db_params.get("port") or 3306),
            user=db_params["user"], password=db_params["password"],
            database=db_params["database"], charset="utf8mb4",
            connect_timeout=CONNECT_TIMEOUT_S, read_timeout=READ_TIMEOUT_S,
            autocommit=False)
        # SSCursor streams from the server so only the rows we fetch enter app
        # memory - a huge result set can never be buffered here.
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute("SET SESSION MAX_EXECUTION_TIME=%d" % STATEMENT_TIMEOUT_MS)
        cur = conn.cursor(pymysql.cursors.SSCursor)
        try:
            cur.execute(cleaned)
            columns = [d[0] for d in cur.description] if cur.description else []
            fetched = cur.fetchmany(MAX_ROWS + 1)
        finally:
            cur.close()
        conn.rollback()

        truncated = forced_truncated or len(fetched) > MAX_ROWS
        rows = [[_jsonable(v) for v in row] for row in fetched[:MAX_ROWS]]

        # Into the ledger BEFORE any size-trimming below: the answer is checked
        # against what the database returned, not against the abridged copy the
        # model was shown.
        if ledger is not None:
            ledger.record(worker, cleaned, columns=columns, rows=rows,
                          truncated=truncated)

        payload = {"columns": columns, "rows": rows,
                   "row_count": len(rows), "truncated": truncated}

        # THE FEEDBACK SIGNAL. A wrong join does not error - it returns a
        # confident wrong number, and nothing in the result says so. The same
        # question here produced 45, 63 and 67 depending on which join was
        # picked, and all three looked equally correct.
        #
        # So when a query joins, say how many rows the driving table has on
        # its own. "base 50, returned 62" is a fan-out; "base 80, returned 45"
        # is a silent drop. Costs one cheap COUNT on the same connection and
        # no extra model turn, which is the only reason it is affordable to do
        # on every joined query.
        base = _join_base(cleaned)
        if base:
            try:
                with conn.cursor() as c2:
                    c2.execute("SELECT COUNT(*) FROM `%s`" % base)
                    payload["base_table"] = base
                    payload["base_table_rows"] = c2.fetchone()[0]
                    payload["join_check"] = (
                        "`%s` holds %d rows on its own. If this query is meant "
                        "to be one row per %s and returned more, the join "
                        "MULTIPLIED - count DISTINCT. If it returned fewer, the "
                        "join DROPPED rows - check the ON clause and whether it "
                        "should be a LEFT JOIN."
                        % (base, payload["base_table_rows"], base))
            except Exception:  # noqa: BLE001 - a hint is never worth failing over
                pass
        if not rows:
            payload["note"] = (
                "Zero rows. Before reporting this as 'none', confirm any value you "
                "filtered on actually exists - use list_values on that column, or "
                "resolve_entity for a name. A filter on a value that is not in the "
                "table returns zero rows and looks identical to a real absence. "
                "If you filtered on a DATE, also check that column is populated at "
                "all (COUNT(*) WHERE it IS NOT NULL): an entirely empty date column "
                "means you cannot answer by period, which is not the same as the "
                "events not having happened.")
        else:
            payload.update(_emptiness_note(rows))
        out = json.dumps(payload, ensure_ascii=False, default=str)

        # keep the tool result readable: halve rows until it fits, and if a
        # single wide row still blows the budget, hard-truncate the JSON string
        # (never loop forever - findings from the red-team pass).
        while len(out) > MAX_RESULT_CHARS and len(payload["rows"]) > 1:
            payload["rows"] = payload["rows"][: len(payload["rows"]) // 2]
            payload["row_count"] = len(payload["rows"])
            payload["truncated"] = True
            out = json.dumps(payload, ensure_ascii=False, default=str)
        if len(out) > MAX_RESULT_CHARS:
            payload["truncated"] = True
            payload["note"] = "result truncated to fit the size budget"
            out = json.dumps(payload, ensure_ascii=False, default=str)
            if len(out) > MAX_RESULT_CHARS:
                out = out[:MAX_RESULT_CHARS]
        return out
    except pymysql.err.OperationalError as exc:
        code = exc.args[0] if exc.args else 0
        if code == 3024:  # ER_QUERY_TIMEOUT
            msg = ("Query exceeded the %d ms time limit. Add WHERE filters or "
                   "aggregate instead of listing." % STATEMENT_TIMEOUT_MS)
        elif code == 1792:  # ER_CANT_EXECUTE_IN_READ_ONLY_TRANSACTION
            msg = "Blocked: the session is read-only. Only SELECT statements are allowed."
        else:
            msg = "MySQL error %s: %s" % (code, exc.args[1] if len(exc.args) > 1 else exc)
        return _fail(ledger, worker, cleaned, msg)
    except pymysql.err.ProgrammingError as exc:
        code = exc.args[0] if exc.args else 0
        detail = exc.args[1] if len(exc.args) > 1 else str(exc)
        return _fail(ledger, worker, cleaned,
                     "SQL error %s: %s. Fix the query and retry using only "
                     "tables/columns from the schema catalog." % (code, detail))
    except Exception as exc:  # noqa: BLE001 - tool results must never raise
        return _fail(ledger, worker, cleaned, "Unexpected database error: %s" % exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
