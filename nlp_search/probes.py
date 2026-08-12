# -*- coding: utf-8 -*-
"""Deterministic lookups. No model writes the SQL for these.

Two failure modes account for most wrong NL->SQL answers, and neither is fixed
by better prompting:

  1. The model filters on a literal that does not exist - ``WHERE status =
     'Failed'`` against a column whose values are 'Pass' and 'A'. MySQL returns
     zero rows, which reads exactly like a real "none", and the assistant
     confidently reports that nothing failed.

  2. The model guesses at a name - 'Krishna Gonela' when the row says
     'Krishna Muthangi' - and reports "not found" for someone who is right
     there.

``list_values`` and ``resolve_entity`` remove the guessing. Both build their
own SQL from a validated identifier plus bound parameters, so there is no
model-authored SQL to guard and nothing to inject. The identifiers are checked
against the generated catalog, which means a probe can only ever read a column
the model was already allowed to see.
"""
import difflib
import json
import re

import pymysql

from .schema_catalog import (ALLOWED_TABLES, COLUMNS, ENUM_VALUES,
                             GRID_COLUMNS, TABLE_PURPOSE)

MAX_VALUES = 60
MAX_CANDIDATES = 15
CONNECT_TIMEOUT_S = 5
READ_TIMEOUT_S = 10
STATEMENT_TIMEOUT_MS = 5000


def _connect(db_params):
    return pymysql.connect(
        host=db_params["host"], port=int(db_params.get("port") or 3306),
        user=db_params["user"], password=db_params["password"],
        database=db_params["database"], charset="utf8mb4",
        connect_timeout=CONNECT_TIMEOUT_S, read_timeout=READ_TIMEOUT_S,
        autocommit=False)


def _read_only(cur):
    cur.execute("SET SESSION TRANSACTION READ ONLY")
    cur.execute("SET SESSION MAX_EXECUTION_TIME=%d" % STATEMENT_TIMEOUT_MS)


def _check(table, column, allowed=None):
    """Validate an identifier pair against the catalog. Returns an error
    string, or None when the pair is safe to interpolate."""
    tables = allowed or ALLOWED_TABLES
    if table not in tables:
        return ("Unknown table '%s'. Available here: %s"
                % (table, ", ".join(sorted(tables))))
    cols = COLUMNS.get(table, ())
    if column not in cols:
        return ("Unknown column '%s' on %s. Its columns are: %s"
                % (column, table, ", ".join(cols)))
    return None


def _check_table(table, allowed=None):
    """Validate a table identifier alone (no column)."""
    tables = allowed or ALLOWED_TABLES
    if table not in tables:
        return ("Unknown table '%s'. Available here: %s"
                % (table, ", ".join(sorted(tables))))
    return None


def _plain(v):
    """A JSON-safe scalar. Dates, decimals and timedeltas are not."""
    import datetime
    import decimal
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")[:200]
    if isinstance(v, (datetime.date, datetime.datetime, datetime.time,
                      datetime.timedelta)):
        return str(v)
    return str(v)[:200]


def list_values(db_params, table, column, contains=None, allowed_tables=None,
                ledger=None):
    """The DISTINCT values actually present in one column, with counts.

    Call this before filtering on any value you have not already seen in the
    catalog or in an earlier result. Returns JSON:
    {"table","column","distinct":N,"values":[{"value":..,"rows":N}],"truncated":bool}
    """
    err = _check(table, column, allowed_tables)
    if err:
        return json.dumps({"error": err})
    conn = None
    try:
        conn = _connect(db_params)
        with conn.cursor() as cur:
            _read_only(cur)
            sql = ("SELECT `%s` AS v, COUNT(*) AS n FROM `%s` WHERE `%s` IS NOT NULL"
                   % (column, table, column))
            args = []
            if contains:
                sql += " AND LOWER(CAST(`%s` AS CHAR)) LIKE %%s" % column
                args.append("%" + str(contains).strip().lower() + "%")
            sql += " GROUP BY `%s` ORDER BY n DESC, v LIMIT %d" % (column, MAX_VALUES + 1)
            cur.execute(sql, args)
            rows = cur.fetchall()
        conn.rollback()
        truncated = len(rows) > MAX_VALUES
        vals = [{"value": None if r[0] is None else str(r[0])[:120], "rows": int(r[1])}
                for r in rows[:MAX_VALUES]]
        payload = {"table": table, "column": column, "distinct": len(vals),
                   "values": vals, "truncated": truncated}
        if not vals:
            payload["note"] = ("This column has no non-NULL values at all, so no "
                               "filter on it can match anything.")
        if ledger is not None:
            ledger.note("values", "%s.%s = %s" % (
                table, column, ", ".join(str(v["value"]) for v in vals[:40]) or "(none)"))
        return json.dumps(payload, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001 - tool results never raise
        return json.dumps({"error": "Value lookup failed: %s" % exc})
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# What "find me the person / job / product" actually means in this schema.
# Each entry: (table, id column, [columns to match on], [columns to return]).
def _same_thing_many_jobs(kind, cands):
    """True when every candidate is the SAME product, just a different job.

    Products live on iec_emc_requests, which has one row per test campaign, so
    a product tested four times resolves to four rows. Distinguishing that from
    a genuine name clash is the difference between answering a history question
    and asking the user to repeat themselves.
    """
    if kind != "product" or len(cands) < 2:
        return False
    names = {(str(c.get("product_name") or "").strip().lower(),
              str(c.get("model_number") or "").strip().lower()) for c in cands}
    return len(names) == 1


_ENTITIES = {
    "person": ("users", "id", ["username", "email"], ["id", "username", "email", "role"]),
    "job": ("iec_emc_requests", "id", ["job_number", "tco_id", "job_id"],
            ["id", "tco_id", "job_number", "product_name", "status"]),
    "product": ("iec_emc_requests", "id", ["product_name", "model_number", "manufacturer"],
                ["id", "tco_id", "product_name", "model_number", "manufacturer"]),
    "equipment": ("equipment", "id", ["name", "make", "model_no", "serial_no", "asset_id"],
                  ["id", "name", "make", "model_no", "serial_no", "calibration_due_date"]),
    "standard": ("iec_emc_request_product_standards", "id", ["standard_value"],
                 ["id", "request_id", "standard_value"]),
}


def _fetch_all(conn, table, out_cols, limit=2000):
    with conn.cursor() as cur:
        _read_only(cur)
        cur.execute("SELECT DISTINCT %s FROM `%s` LIMIT %d"
                    % (", ".join("`%s`" % c for c in out_cols), table, limit))
        return cur.fetchall()


def _row_dicts(rows, out_cols):
    return [dict(zip(out_cols, [None if v is None else str(v)[:120] for v in r]))
            for r in rows]


# Structured identifiers - TFS-EMC-2026-002, IEC-EMC-004, asset tags. Two
# characters apart is a DIFFERENT job, not a typo of this one, so fuzzy
# matching has to be near-exact here or a fabricated job number comes back as
# nine plausible matches.
# Deliberately narrow: a prefix, a separator, then digits. "TFS-EMC-2026-002"
# and "IEC-EMC-004" qualify; "Smart2pure 6UV" does NOT, even though it carries
# a digit - it is a product name and must stay fuzzily matchable.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z]{2,}[-_][A-Za-z0-9_-]*\d{2,}$|^\d[\d-]{4,}$")


def _fuzzy(needle, rows, out_cols, match_cols, cutoff=0.72):
    """Rows whose matchable text is CLOSE to the needle, best first.

    Three passes, cheapest first:
      1. any single word of the needle appears in the value - catches
         "kirshna gonela", where the surname is spelled correctly;
      2. any word of the VALUE is a near-match for a word of the needle -
         catches a typo in the only word given;
      3. whole-string similarity.

    Every table this runs against holds tens of rows, so matching in Python
    costs nothing and avoids depending on a MySQL edit-distance function that
    may not be installed. SOUNDEX was the other option and is English-phonetic,
    which is the wrong tool for the names in this database.
    """
    needle = needle.lower().strip()
    identifier = bool(_IDENTIFIER_RE.match(needle))
    words = [w for w in re.split(r"\W+", needle) if len(w) > 2]
    scored = []
    for r in rows:
        values = [str(v).lower() for i, v in enumerate(r)
                  if out_cols[i] in match_cols and v is not None and str(v).strip()]
        if not values:
            continue

        if identifier:
            # Whole value against whole needle, nothing else. Word-level
            # matching is actively wrong here: every job number shares the
            # token "emc", which scores 1.0 and made a fabricated
            # TFS-EMC-2027-999 come back as nine plausible jobs.
            score = max(difflib.SequenceMatcher(None, needle, v).ratio() for v in values)
            if score >= 0.9:
                scored.append((score, r))
            continue

        hay = " ".join(values)
        if any(w in hay for w in words):
            score = 0.95
        else:
            best = 0.0
            for hw in re.split(r"\W+", hay):
                if len(hw) < 3:
                    continue
                for w in (words or [needle]):
                    best = max(best, difflib.SequenceMatcher(None, w, hw).ratio())
            score = max(best, difflib.SequenceMatcher(None, needle, hay).ratio())
        if score >= cutoff:
            scored.append((score, r))
    scored.sort(key=lambda s: -s[0])
    return [r for _s, r in scored[:MAX_CANDIDATES]], (scored[0][0] if scored else 0.0)


def resolve_entity(db_params, kind, text, ledger=None, cross_kind=True):
    """Find the real rows matching a name the user typed, before it is used
    as a filter.

    Exact substring first, then a fuzzy pass, then the other entity kinds.
    That escalation matters more than it sounds: an exact-only LIKE reported
    "there is no person named kirshna gonela" for someone with eleven
    datasheets, and because the rest of this package treats a resolve miss as
    trustworthy evidence of absence, a single transposed letter became a
    confident wrong answer. A typo must never be indistinguishable from
    absence.
    """
    kind = (kind or "").strip().lower()
    text = (text or "").strip()
    if kind not in _ENTITIES:
        return json.dumps({"error": "Unknown entity kind '%s'. Use one of: %s"
                           % (kind, ", ".join(sorted(_ENTITIES)))})
    if not text:
        return json.dumps({"error": "Nothing to look up."})
    table, _idc, match_cols, out_cols = _ENTITIES[kind]
    conn = None
    try:
        conn = _connect(db_params)
        with conn.cursor() as cur:
            _read_only(cur)
            where = " OR ".join("LOWER(CAST(`%s` AS CHAR)) LIKE %%s" % c for c in match_cols)
            sql = ("SELECT DISTINCT %s FROM `%s` WHERE %s LIMIT %d"
                   % (", ".join("`%s`" % c for c in out_cols), table, where,
                      MAX_CANDIDATES + 1))
            cur.execute(sql, ["%" + text.lower() + "%"] * len(match_cols))
            rows = cur.fetchall()

        cands = _row_dicts(rows[:MAX_CANDIDATES], out_cols)
        how = "exact"
        confidence = 1.0

        if not cands:
            fuzzy_rows, confidence = _fuzzy(text, _fetch_all(conn, table, out_cols),
                                            out_cols, match_cols)
            if fuzzy_rows:
                cands = _row_dicts(fuzzy_rows, out_cols)
                how = "approximate"

        elsewhere = []
        if not cands and cross_kind:
            # "smart2pure" is not a job, so a job lookup returns nothing - and
            # the assistant announced the thing does not exist. It is a product.
            for other in _ENTITIES:
                if other == kind:
                    continue
                ot, _oid, omc, ooc = _ENTITIES[other]
                found, _sc = _fuzzy(text, _fetch_all(conn, ot, ooc), ooc, omc)
                if found:
                    elsewhere.append({"kind": other,
                                      "candidates": _row_dicts(found[:5], ooc)})
        conn.rollback()

        payload = {"kind": kind, "looked_for": text, "table": table,
                   "matched_how": how, "match_count": len(cands),
                   "candidates": cands}
        if cands and how == "approximate":
            payload["confidence"] = round(confidence, 2)
            payload["note"] = (
                "NO EXACT MATCH - these are the closest names in the database, "
                "probably a typo for one of them. Confirm which one you mean "
                "before reporting numbers about them, and use the exact spelling "
                "shown here in any query.")
        elif not cands and elsewhere:
            payload["found_elsewhere"] = elsewhere
            payload["note"] = (
                "Nothing matches as a %s, but the same text DOES match other "
                "things (see found_elsewhere). Use those - do NOT report that it "
                "does not exist." % kind)
        elif not cands:
            payload["note"] = (
                "No %s in the database matches '%s', and nothing close to it "
                "matches any other kind either. Say that no such %s exists - do "
                "NOT filter on this value and report a count of zero, which "
                "would mean something different." % (kind, text, kind))
        elif len(cands) > 1 and _same_thing_many_jobs(kind, cands):
            # One product tested four times is not four products. The generic
            # "ask which one" below is right for two people who share a name and
            # wrong here, and it was wrong in the way that costs most: asked for
            # a product's testing history, the assistant listed the four jobs it
            # had just found and asked which one was meant - handing back the
            # question as the answer.
            payload["note"] = (
                "These are %d JOBS ON THE SAME PRODUCT, not %d different "
                "products - one unit tested more than once. This is a history, "
                "not an ambiguity: do NOT ask which one is meant. If the "
                "question is about the product over time (its history, why it "
                "failed, what changed, whether it improved), ALL of these are "
                "in scope - hand the product name to the datasheets specialist "
                "and have it run analyse_history. Only pick a single job if the "
                "question named one." % (len(cands), len(cands)))
        elif len(cands) > 1:
            payload["note"] = ("More than one match - use the exact value from the "
                               "candidate you mean, or ask which one.")
        if ledger is not None:
            ledger.note("resolve", "%s '%s' [%s] -> %d match(es): %s%s" % (
                kind, text, how, len(cands),
                "; ".join(str(list(c.values())[:3]) for c in cands[:5]) or "none",
                (" | also matches: %s" % [e["kind"] for e in elsewhere])
                if elsewhere else ""))
        return json.dumps(payload, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": "Entity lookup failed: %s" % exc})
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


ENTITY_KINDS = tuple(sorted(_ENTITIES))


# --------------------------------------------------------------------------
# where is X kept?
# --------------------------------------------------------------------------
# "Where is the coupling method stored" is a question about the SCHEMA, and
# routing it through NL->SQL cannot work: the only thing SQL can look at is
# rows, so the model finds the most plausible-looking container and asserts it.
# That is how the assistant came to say CE coupling method lives in
# iec_emc_request_test_ce.custom_spec. Every value it quoted was real; the
# relationship was invented, and a value-level grounding check cannot see that.
#
# The answer needs a search over column NAMES, not over data. That search is
# deterministic, so it cannot invent a column, and when nothing matches it can
# say nothing matches - which is the true answer surprisingly often.

_STOP = {"the", "a", "an", "of", "for", "in", "on", "is", "are", "value",
         "values", "field", "column", "db", "database", "table", "where",
         "what", "which", "stored", "store", "kept", "test", "data"}

# Lab phrasing -> the words that actually appear in column names.
_SYNONYMS = {
    "coupling": ("coupling", "coupl"),
    "eut": ("eut", "product"),
    "engineer": ("engineer", "tested_by", "test_person"),
    "reviewer": ("peer_reviewer", "reviewer"),
    "job": ("job_number", "job_id", "tco_id"),
    "outcome": ("result", "status"),
    "pass": ("result",),
    "fail": ("result",),
    "temperature": ("ambient_temperature", "temperature"),
    "humidity": ("relative_humidity", "humidity"),
    "calibration": ("calibration",),
    "observation": ("observation", "obs"),
    "limit": ("limit",),
    "standard": ("standard",),
    "frequency": ("frequency", "freq"),
    "level": ("level", "test_level"),
    "software": ("software",),
    "equipment": ("equipment",),
    "photo": ("images_json", "photo", "img"),
    "picture": ("images_json", "photo", "img"),
    "signature": ("signature", "signoff"),
    "comment": ("comment", "remarks", "notes"),
    "reason": ("reason", "comment", "deviation"),
}

MAX_FIELD_HITS = 25


def _terms(text):
    words = [w for w in re.split(r"[^A-Za-z0-9]+", (text or "").lower()) if w]
    out = []
    for w in words:
        if w in _STOP or len(w) < 3:
            continue
        out.append(w)
        out.extend(s for s in _SYNONYMS.get(w, ()) if s != w)
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def find_field(term, test_code=None):
    """Which columns hold a given concept. Reads the catalog, never the data.

    Returns JSON: {"looked_for", "terms", "match_count",
                   "matches":[{table, column, table_purpose, values}], "note"}
    An empty match list is a real answer: the database does not record that.
    """
    term = (term or "").strip()
    if not term:
        return json.dumps({"error": "Nothing to look for."})
    terms = _terms(term)
    if not terms:
        return json.dumps({"error": "Nothing searchable in %r." % term})

    code = (test_code or "").strip().lower().replace("-", "_")
    hits = []
    for table, cols in COLUMNS.items():
        for col in cols:
            hay = col.lower()
            matched = [t for t in terms if t in hay]
            if not matched:
                continue
            # Rank, or the answer drowns in noise: searching "rejection reason
            # for a datasheet" matched every datasheet_id in the schema.
            score = len(matched) * 10
            parts = set(hay.split("_"))
            score += 5 * sum(1 for t in matched if t in parts)   # whole word
            if hay in terms:
                score += 20                                      # exact name
            if hay.endswith("_id") and not any(t.endswith("id") for t in matched):
                score -= 25                                      # a join key, not the concept
            if code and code in table.lower():
                score += 15                                      # the test asked about
            hits.append({
                "_score": score, "table": table, "column": col,
                "table_purpose": TABLE_PURPOSE.get(table, ""),
                "values": list(ENUM_VALUES.get("%s.%s" % (table, col), ())) or None,
            })
    hits.sort(key=lambda h: (-h["_score"], h["table"], h["column"]))
    best = hits[0]["_score"] if hits else 0
    # keep only what is genuinely competitive with the best match
    hits = [h for h in hits if h["_score"] >= max(1, best - 15)]
    for h in hits:
        h.pop("_score", None)

    payload = {"looked_for": term, "terms_searched": terms,
               "match_count": len(hits), "matches": hits[:MAX_FIELD_HITS],
               "truncated": len(hits) > MAX_FIELD_HITS}
    if not hits:
        payload["note"] = (
            "No column in this database has a name matching %r. Report that the "
            "field is NOT recorded - do NOT point at some other column that "
            "might plausibly contain it. If the concept exists in the product "
            "but not in the schema, say it is not captured in the database."
            % term)
    else:
        payload["note"] = (
            "These are the ONLY columns whose names match. Name the exact "
            "table.column. Do not claim the value lives anywhere else, and do "
            "not guess that it is buried inside a JSON or free-text column "
            "unless one of these matches is that column.")
    return json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------------
# the measurement and observation grids
# --------------------------------------------------------------------------
# A CE datasheet's real content is its measurement table - frequency, quasi-peak,
# limit, margin, one row per measured point. It lives in a JSON column, which
# the catalog deliberately hides because a model cannot usefully write SQL
# against it and selecting it blows the size budget.
#
# Hiding it made it unreachable. Asked for the CE measurement values, the
# assistant ran out of time hunting and answered that it could not retrieve
# them - and people will keep asking, because that table IS the datasheet.
#
# So it is read here instead: parse the column, hand back labelled rows. No SQL
# over JSON, no schema exposed, nothing the model could invent. The projection
# writes these grids self-describing ({label, columns, rows}) precisely so this
# is possible.

_GRID_SUFFIX = "_json"
_NOT_A_GRID = {"images_json", "form_json", "values_json"}


def _grid_columns(table):
    # NOT from COLUMNS - the catalog hides these deliberately so the model does
    # not try to SELECT them. GRID_COLUMNS is the generator's record of what it
    # hid, which is exactly what this tool needs.
    return sorted(c for c in GRID_COLUMNS.get(table, ())
                  if c not in _NOT_A_GRID)


def grid_tables():
    """{per-test table: [grid column, ...]} - what read_grid can reach."""
    return {t: _grid_columns(t) for t in ALLOWED_TABLES if _grid_columns(t)}


def describe_table(names, allowed_tables=None):
    """Columns, notes and enum values for one or more tables.

    Takes a comma-separated list so a question needing three tables costs one
    turn, not three. Turns are the expensive unit here: the whole prompt is
    resent on each one, so a chatty tool can cost more than the catalog it
    replaced.
    """
    from .schema_catalog import table_detail, tables_for
    allowed = set(allowed_tables or tables_for())
    wanted = [n.strip().strip("`\"'") for n in str(names or "").split(",")]
    wanted = [n for n in wanted if n]
    if not wanted:
        return "Name at least one table, e.g. describe_table('datasheet, datasheet_equipment')."
    out, missing = [], []
    for n in wanted:
        if n not in allowed:
            missing.append(n)
            continue
        detail = table_detail(n)
        out.append(detail if detail else "### %s - no catalog entry" % n)
    if missing:
        out.append("NOT IN YOUR DOMAIN: %s. You cannot query these - say so and "
                   "name what you would need." % ", ".join(sorted(missing)))
    return ("\n" + "\n").join(out)


def read_grid(db_params, datasheet_id, grid=None, ledger=None):
    """The measurement / observation tables recorded on one datasheet.

    Returns JSON: {"datasheet_id", "test_code", "grids": {name: {label,
    columns:[...], row_count, rows:[[...]]}}}. `grid` narrows to one by name.

    Args:
        datasheet_id: `datasheet`.id - get it from a query first.
        grid: optional grid name, e.g. "line_measurements".
    """
    try:
        did = int(datasheet_id)
    except (TypeError, ValueError):
        return json.dumps({"error": "datasheet_id must be a number - query "
                                    "`datasheet` for it first."})
    conn = None
    try:
        conn = _connect(db_params)
        with conn.cursor() as cur:
            _read_only(cur)
            cur.execute("SELECT test_code, job_number FROM `datasheet` WHERE id=%s",
                        (did,))
            head = cur.fetchone()
            if not head:
                return json.dumps({"error": "No datasheet with id %d." % did})
            code, job = head[0], head[1]
            table = "datasheet_" + str(code).lower()
            cols = _grid_columns(table)
            if not cols:
                return json.dumps({
                    "datasheet_id": did, "test_code": code, "grids": {},
                    "note": "%s datasheets record no measurement grid." % code})
            cur.execute("SELECT %s FROM `%s` WHERE datasheet_id=%%s"
                        % (", ".join("`%s`" % c for c in cols), table), (did,))
            row = cur.fetchone()
        conn.rollback()

        grids = {}
        for col, raw in zip(cols, row or []):
            name = col[:-len(_GRID_SUFFIX)]
            if grid and grid.strip().lower() not in (name, col):
                continue
            if not raw:
                continue
            data = raw if isinstance(raw, dict) else json.loads(raw)
            rows = data.get("rows") or [r for b in data.get("blocks", [])
                                        for r in b.get("rows", [])]
            grids[name] = {
                "label": data.get("label") or name,
                "columns": [c.get("label") or c.get("key")
                            for c in data.get("columns", [])],
                "row_count": len(rows),
                "rows": rows[:60],
                "truncated": len(rows) > 60,
            }
        payload = {"datasheet_id": did, "test_code": code, "job_number": job,
                   "grids": grids}
        if not grids:
            payload["note"] = (
                "No grid recorded on this datasheet%s. Available grid names for "
                "%s: %s. Say the measurement table is empty - do not describe "
                "values that are not here."
                % ((" called %r" % grid) if grid else "", code,
                   ", ".join(c[:-len(_GRID_SUFFIX)] for c in cols) or "none"))
        if ledger is not None:
            for name, g in grids.items():
                ledger.record("grid", "read_grid(%d, %r)" % (did, name),
                              columns=g["columns"], rows=g["rows"])
        return json.dumps(payload, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": "Could not read the grid: %s" % exc})
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def field_exists(table, column):
    """True when this exact table.column is in the catalog."""
    return column in COLUMNS.get(table, ())


def columns_matching(term):
    """[(table, column)] - the plain-Python form of find_field, for verify."""
    terms = _terms(term)
    if not terms:
        return []
    return [(t, c) for t, cols in COLUMNS.items() for c in cols
            if any(x in c.lower() for x in terms)]


# --------------------------------------------------------------------------
# Exploration: let the model look at the DATA, not just the schema
# --------------------------------------------------------------------------
# Almost every wrong answer this system has produced was a DATA problem, not
# a schema problem. The schema says planner_entries.test_name is a varchar;
# it does not say the values are 'VoltageFlicker' where the request table
# says 'FLICKER', and that difference is the whole reason a join silently
# drops four of eleven test types. Nor does it say equipment.name repeats, so
# joining on it turns 50 rows into 62.
#
# None of that is discoverable from a column list. All of it is obvious from
# five sample rows. So the model gets tools to look.

SAMPLE_ROWS = 5


def sample_rows(db_params, table, limit=SAMPLE_ROWS, allowed_tables=None,
                ledger=None):
    """A few real rows, so the model can see what the values LOOK like.

    Formats, spellings, casing, whether a "date" column holds dates or free
    text, whether a code is 'CE' or 'ce' or 'Conducted Emission'. A column
    list cannot tell you any of that.
    """
    err = _check_table(table, allowed_tables)
    if err:
        return json.dumps({"error": err})
    try:
        limit = max(1, min(int(limit), 20))
    except (TypeError, ValueError):
        limit = SAMPLE_ROWS
    from .schema_catalog import columns_for
    cols = [c for c in columns_for(table)]
    if not cols:
        return json.dumps({"error": "No readable columns for %r." % table})
    sql = "SELECT %s FROM `%s` LIMIT %d" % (
        ", ".join("`%s`" % c for c in cols), table, limit)
    conn = None
    try:
        conn = _connect(db_params)
        with conn.cursor() as cur:
            _read_only(cur)
            cur.execute(sql)
            rows = [[_plain(v) for v in r] for r in cur.fetchall()]
        conn.rollback()
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)[:200]})
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    if ledger is not None:
        ledger.record("sample", sql, columns=cols, rows=rows)
    return json.dumps({"table": table, "columns": cols, "rows": rows},
                      ensure_ascii=False, default=str)


def profile_column(db_params, table, column, allowed_tables=None, ledger=None):
    """What one column actually contains: nulls, blanks, distinctness, range.

    The question this answers is "can I trust this column for what I am about
    to do with it". Three facts decide that and none are in the schema:

      * how much of it is EMPTY - a column that is 80%% null cannot support
        "how many X are past due", and answering anyway produces a confident
        undercount;
      * whether values are UNIQUE - if not, joining on it multiplies rows;
      * the actual RANGE - a date column whose newest row is two years old
        means "last month" has no answer.
    """
    err = _check(table, column, allowed_tables)
    if err:
        return json.dumps({"error": err})
    out = {"table": table, "column": column}
    conn = None
    try:
        conn = _connect(db_params)
        with conn.cursor() as cur:
            _read_only(cur)
            cur.execute(
                "SELECT COUNT(*), COUNT(`%s`), COUNT(DISTINCT `%s`), "
                "MIN(`%s`), MAX(`%s`) FROM `%s`"
                % (column, column, column, column, table))
            total, non_null, distinct, lo, hi = cur.fetchone()
            out.update({"rows": total, "populated": non_null,
                        "empty": total - non_null, "distinct": distinct,
                        "min": _plain(lo), "max": _plain(hi)})
            try:
                cur.execute("SELECT COUNT(*) FROM `%s` WHERE TRIM(`%s`) = ''"
                            % (table, column))
                out["blank_string"] = cur.fetchone()[0]
            except Exception:  # noqa: BLE001 - not a string column
                pass
        conn.rollback()
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)[:200]})
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    notes = []
    if out.get("rows"):
        filled = 100.0 * out["populated"] / out["rows"]
        if filled < 90:
            notes.append("ONLY %.0f%% POPULATED - any count or filter on this "
                         "column silently ignores the other %.0f%%. Say so."
                         % (filled, 100 - filled))
        if out["populated"] and out["distinct"] < out["populated"]:
            notes.append("NOT UNIQUE: %d distinct values over %d populated "
                         "rows. IF YOU JOIN ON THIS COLUMN the result will "
                         "multiply - count with COUNT(DISTINCT ...) or join on "
                         "a key instead."
                         % (out["distinct"], out["populated"]))
    out["notes"] = notes
    if ledger is not None:
        ledger.note("profile", "%s.%s: %s"
                    % (table, column, "; ".join(notes) or "clean"))
    return json.dumps(out, ensure_ascii=False, default=str)
