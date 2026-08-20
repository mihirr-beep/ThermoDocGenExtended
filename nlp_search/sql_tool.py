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


_ALL_FROM_RE = re.compile(_B + r"(?:FROM|JOIN)\s+`?([A-Za-z_][A-Za-z0-9_]*)`?", re.I)
_WHERE_RE = re.compile(_B + "WHERE" + _B, re.I)
_GROUP_BY_RE = re.compile(_B + r"GROUP\s+BY" + _B, re.I)
_AGG_RE = re.compile(_B + r"(?:COUNT|SUM)\s*\(", re.I)


def _single_table(sql):
    """The one table this query reads, or None when it reads more than one.

    Deliberately counts FROM *and* JOIN, so a subquery against a second table
    disqualifies the query too. A denominator is only honest when the numerator
    and the total are drawn from the same population; the moment two tables are
    involved, "of what?" has no single answer and a plausible-looking total is
    worse than none.
    """
    names = {m.group(1).lower() for m in _ALL_FROM_RE.finditer(sql or "")}
    return names.pop() if len(names) == 1 else None


def _scope_note(conn, sql, rows, truncated):
    """How much of the table the filter kept, so a bare number becomes a ratio.

    THE FAILURE THIS EXISTS FOR. A filtered query that returns a number gives
    the model something unfalsifiable: "3 datasheets were rejected" is
    indistinguishable from "I filtered on the wrong column and 3 rows happened
    to match". Both look like answers. Measured in this lab: a question about
    calibration read `status` instead of `calibration_status_col`, matched real
    rows, and produced a confident count of the wrong thing.

    A ratio is falsifiable. "3 of 11" either adds up against the table or it
    does not, and the two ends of the range are diagnoses on their own:

      0 of 89   the filter is the problem, not the data
      89 of 89  the filter excluded nothing - did it apply at all?

    Only computed when the query reads exactly ONE table and actually filters -
    see _single_table for why. Costs one COUNT on the connection already open,
    which is what makes it affordable on every query rather than a special case
    someone has to remember to ask for.

    Returns payload keys, and the terse form for the ledger separately: the
    numbers are evidence and must be groundable (ledger.note text is tokenised
    into values()), while the instruction around them is commentary and must not
    become something a claim can be grounded against.
    """
    if not _WHERE_RE.search(sql or ""):
        return {}, None                 # nothing was filtered; a total says nothing
    table = _single_table(sql)
    if not table:
        return {}, None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM `%s`" % table)
            total = cur.fetchone()[0]
    except Exception:  # noqa: BLE001 - a denominator is never worth failing over
        return {}, None
    if not total:
        return {}, None                 # empty table: the zero-rows note covers it

    payload = {"scope_table": table, "scope_table_rows": total}
    grouped = bool(_GROUP_BY_RE.search(sql))

    # An aggregate answers with its value, not its row count: one row holding
    # one number IS the numerator. Anything grouped is a count of groups, so it
    # must not be compared against a row total.
    cells = [v for row in rows for v in row]
    if (not grouped and len(rows) == 1 and len(cells) == 1
            and _AGG_RE.search(sql) and not isinstance(cells[0], bool)):
        try:
            n = float(cells[0])
        except (TypeError, ValueError):
            n = None
        if n is not None:
            shown = int(n) if n == int(n) else n
            payload["scope"] = "%s of %d rows in `%s`" % (shown, total, table)
            if n == 0:
                payload["scope_check"] = (
                    "ZERO of the %d rows in `%s` matched. The filter excluded "
                    "every single row, which is what a WRONG COLUMN or a wrong "
                    "value looks like - it is not evidence that nothing happened. "
                    "Confirm the column you filtered on is the one that holds "
                    "this fact before answering 'none'." % (total, table))
            elif n >= total:
                payload["scope_check"] = (
                    "This matched ALL %d rows in `%s` - the filter excluded "
                    "nothing. Check it is doing what you think; an answer that "
                    "silently covers the whole table is rarely the question that "
                    "was asked." % (total, table))
            else:
                payload["scope_check"] = (
                    "STATE THIS AS A RATIO: %s of the %d rows in `%s`. A bare "
                    "count cannot be checked by the person reading it; a ratio "
                    "can." % (shown, total, table))
            return payload, "`%s`: %s matched of %d rows total" % (table, shown, total)

    if not truncated:
        kept = len(rows)
        noun = "groups from" if grouped else "of"
        payload["scope"] = "%d %s %d rows in `%s`" % (kept, noun, total, table)
        if not grouped:
            payload["scope_check"] = (
                "STATE THE SCOPE: these %d rows are out of %d in `%s`. Saying "
                "'%d of %d' lets the reader catch a wrong filter; saying '%d' "
                "does not." % (kept, total, table, kept, total, kept))
        return payload, "`%s`: %d %s %d rows total" % (table, kept, noun, total)

    return payload, "`%s` holds %d rows in total" % (table, total)


def _is_zero(value):
    """True for a numeric zero, however the driver typed it. Not for None."""
    if value is None or isinstance(value, bool):
        return False
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def _null_column_note(names, rows):
    """Columns that are NULL on EVERY row returned. Named, not counted.

    THE FAILURE THIS EXISTS FOR. Asked which tests were past their deadline and
    unsubmitted, the model wrote a query that selected test_code from a datasheet
    it had just guaranteed to be absent - LEFT JOIN datasheet d ON ... AND
    d.submitted_at IS NOT NULL, then WHERE d.id IS NULL. If a row qualifies, d is
    NULL by construction. Sixteen rows came back, every test_code empty, and the
    answer listed sixteen blanks where the test names should be:

        IEC-EMC-006 | TFS-EMC-2026-001 | Smart2Pure Pro 16 | 2026-04-29 |
        IEC-EMC-004 | TFS-EMC-2026-002 | Smart2pure 6UV    | 2026-05-04 |

    The count was right. The answer was unusable, and nothing objected, because
    an empty column is not a wrong number - every existing check is about values
    that are present. The names were in planner_entries.test_name all along.

    Distinct from the aggregate NULL count already reported below: "9 of 112
    values are NULL" says nothing about WHICH field is missing, and a field that
    is missing on every single row is a different fact from a field that is
    patchy. One means nobody filled it in; the other usually means the query
    cannot see it.
    """
    if len(rows) < 2 or not names:
        return {}
    dead = []
    for i, name in enumerate(names):
        # '' counts as empty, not just NULL. col_label came back as the empty
        # string on all sixteen rows of an answer and sailed past this check,
        # so the reader got a column of nothing with a header over it.
        if all(row[i] is None or row[i] == "" for row in rows if i < len(row)):
            dead.append(name)
    if not dead or len(dead) == len(names):
        return {}                       # all-NULL everywhere is the other note
    return {"dead_columns": (
        "%s came back EMPTY on ALL %d rows. Do NOT present %s as a value or a "
        "blank - the query cannot see it. A column empty on every row usually "
        "means it was selected from a table the WHERE clause excludes (a LEFT "
        "JOIN whose row is then required to be NULL), so the field lives "
        "somewhere this query never reached. Find the column that holds it and "
        "ask again before answering; if the question was ABOUT that field, you "
        "have not answered it yet."
        % (", ".join("`%s`" % d for d in dead), len(rows),
           "it" if len(dead) == 1 else "them"))}


def _repeated_column_note(names, rows):
    """Columns holding the SAME value on every row. Context, not data.

    Asked which fields had issues, the answer reproduced the query: nine columns
    by sixteen rows, with tco_id, job_number and product_name identical all the
    way down because the whole result was one product. Three columns of the nine
    carried no information at all, one was empty, and the two the reader actually
    wanted - the field and its value - were the last two on a line wide enough to
    need a scrollbar.

    A value that is the same on every row belongs in the sentence above the table,
    once. Saying it sixteen times is not thoroughness, it is the query's shape
    leaking into the answer.

    Only fires from three rows up, and only when something varies - a single-row
    result is all "constants" and a table where nothing varies is a different
    problem (see _scope_note).
    """
    if len(rows) < 3 or len(names) < 3:
        return {}
    fixed = []
    for i, name in enumerate(names):
        seen = {row[i] for row in rows if i < len(row)}
        if len(seen) == 1:
            only = next(iter(seen))
            if only not in (None, ""):
                fixed.append((name, str(only)[:40]))
    if not fixed or len(fixed) >= len(names):
        return {}
    return {"repeated_columns": (
        "%s the same on ALL %d rows: %s. State those once in the sentence above "
        "the table and DROP the columns - repeating a value down every row is the "
        "query's shape, not the answer's, and it pushes the columns the reader "
        "asked for off the edge of the panel."
        % ("This column is" if len(fixed) == 1 else "These columns are",
           len(rows), "; ".join("%s = %s" % (n, v) for n, v in fixed)))}


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


def _unique_columns(columns):
    """Column names a dict can hold without losing one.

    `SELECT d.id, r.id FROM datasheet d JOIN datasheet_revision r` returns two
    columns both called `id`, which is not exotic - it is what happens the
    moment anyone joins. dict(zip(names, row)) keeps the last and silently drops
    the first, so half the result would vanish with nothing to show it had. The
    second occurrence becomes id__2, which is ugly and honest.
    """
    seen, out = {}, []
    for c in columns:
        c = c if c is not None else "?"
        n = seen.get(c, 0) + 1
        seen[c] = n
        out.append(c if n == 1 else "%s__%d" % (c, n))
    return out


def _as_objects(names, rows):
    """One JSON object per row, keyed by column name.

    Positional arrays make the model do the join between the header and each
    row by counting, and a miscount does not look like an error - it looks like
    an answer about the wrong column. Naming every value costs tokens and
    removes the whole failure mode.

    NULL is kept as an explicit null rather than dropped: an absent key reads as
    "this column does not exist", when what it means is "nobody recorded it".
    That distinction is the difference between "no reading" and "a reading of
    zero", and this lab has columns in exactly that state.
    """
    return [dict(zip(names, row)) for row in rows]


# Advisory text, in the order it should be read. Folded out of the payload body
# into one list so the model can tell evidence from instruction at a glance -
# everything under "rows" came from the database, everything under "guidance"
# came from us.
_GUIDANCE_KEYS = ("note", "dead_columns", "repeated_columns", "scope_check",
                  "join_check", "nulls")


def _fold_guidance(payload):
    notes = [payload.pop(k) for k in _GUIDANCE_KEYS if payload.get(k)]
    for k in _GUIDANCE_KEYS:
        payload.pop(k, None)
    if notes:
        payload["guidance"] = notes
    return payload


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

        names = _unique_columns(columns)
        payload = {"row_count": len(rows), "truncated": truncated,
                   "columns": names, "rows": _as_objects(names, rows)}

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
        else:
            # No join, so fan-out is not the risk here - a wrong filter is. Say
            # what fraction of the table survived it. Mutually exclusive with
            # join_check by construction: _single_table refuses anything joined.
            scope, ledger_line = _scope_note(conn, cleaned, rows, truncated)
            payload.update(scope)
            if ledger_line and ledger is not None:
                # Through note(), not used_metric(): a measured COUNT is
                # evidence, so the answer must be allowed to quote it. Kept
                # terse for that exact reason - every word here becomes a value
                # a claim can be grounded against.
                ledger.note("scope", ledger_line)
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
            payload.update(_null_column_note(names, rows))
            payload.update(_repeated_column_note(names, rows))
        out = json.dumps(_fold_guidance(payload), ensure_ascii=False, default=str)

        # Naming every value costs about 2.5x on a wide result - 200 x 11
        # measured 15751 chars positional against 43751 named. Paid blindly,
        # that halves what survives the size budget (100 rows -> 50), so the
        # model would trade a format it reads better for half the evidence.
        # Never worth it: fall back to positional BEFORE dropping a single row,
        # and say plainly how to read it. Small results - almost every question
        # anyone actually asks - keep the named form, which is where misreading
        # a column was going to happen anyway.
        if len(out) > MAX_RESULT_CHARS:
            payload["rows"] = rows
            payload["row_format"] = (
                "Rows are POSITIONAL here, not named: each row is an array whose "
                "values line up with `columns` in order. This result was too "
                "large to name every value, and dropping rows would have been "
                "worse. Index carefully - `columns[i]` describes `row[i]`.")
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
