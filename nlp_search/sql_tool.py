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

import pymysql

from . import sql_guard
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


def run_select(sql, db_params):
    """Validate + execute one SELECT. Returns a JSON string for the model.

    Success: {"columns": [...], "rows": [[...], ...], "row_count": N,
              "truncated": bool}
    Failure: {"error": "..."} - written so the model can fix its SQL and retry.
    Never raises.
    """
    ok, reason, cleaned = sql_guard.validate_sql(
        sql, ALLOWED_TABLES, denied_star_tables=DENIED_STAR_TABLES)
    if not ok:
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
        payload = {"columns": columns, "rows": rows,
                   "row_count": len(rows), "truncated": truncated}
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
            return json.dumps({"error": "Query exceeded the %d ms time limit. "
                               "Add WHERE filters or aggregate instead of listing."
                               % STATEMENT_TIMEOUT_MS})
        if code == 1792:  # ER_CANT_EXECUTE_IN_READ_ONLY_TRANSACTION
            return json.dumps({"error": "Blocked: the session is read-only. "
                               "Only SELECT statements are allowed."})
        return json.dumps({"error": "MySQL error %s: %s" % (code, exc.args[1] if len(exc.args) > 1 else exc)})
    except pymysql.err.ProgrammingError as exc:
        code = exc.args[0] if exc.args else 0
        msg = exc.args[1] if len(exc.args) > 1 else str(exc)
        return json.dumps({"error": "SQL error %s: %s. Fix the query and retry "
                           "using only tables/columns from the schema catalog." % (code, msg)})
    except Exception as exc:  # noqa: BLE001 - tool results must never raise
        return json.dumps({"error": "Unexpected database error: %s" % exc})
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
