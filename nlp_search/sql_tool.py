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
