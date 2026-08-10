# -*- coding: utf-8 -*-
"""SELECT-only SQL validator for the NL->SQL search feature.

The LLM writes the SQL; this module decides whether it is allowed to run.

Masking is the heart of the guard. Two length-preserving views of the query
are produced by ``_mask_atoms``:

  * FULL mask (``mask_idents=True``)  - the contents of string literals AND
    backtick identifiers are blanked. Used for structural scans (statement
    count, comments, verb, forbidden keywords/functions/clauses) so that a
    value OR a quoted identifier named e.g. ``drop table x`` can never smuggle
    a keyword past the scan.
  * IDENT mask (``mask_idents=False``) - only string literals are blanked;
    backtick identifier text is preserved. Used for everything that must read
    real identifier names: the table allowlist, the schema-qualified / system-
    schema blocks, the credential-column block, and the ``SELECT *`` guard.
    Backtick-quoting an identifier therefore can no longer hide it.

Masking is length-preserving, so offsets in a masked view line up with the
original SQL (relied on by the row-cap rewrite in ``enforce_row_cap``).

Execution-side caps (row limit, timeout, read-only session) live in
sql_tool.py - this module is pure validation and has no DB dependency.
"""
import re

MAX_SQL_LEN = 8000

# Statement verbs / clauses that must never appear anywhere (scanned on the
# FULL-masked text with word boundaries, so snake_case columns like
# update_time / created_at never trip it). DESC is intentionally absent
# (ORDER BY ... DESC is legitimate); DESCRIBE is present.
_FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE",
    "RENAME", "REPLACE", "GRANT", "REVOKE", "LOAD", "OUTFILE", "DUMPFILE",
    "INFILE", "LOCK", "UNLOCK", "CALL", "EXECUTE", "PREPARE", "DEALLOCATE",
    "SET", "USE", "SHOW", "DESCRIBE", "EXPLAIN", "HANDLER", "DO", "KILL",
    "SHUTDOWN", "ANALYZE", "OPTIMIZE", "FLUSH", "RESET", "PURGE", "BINLOG",
    "MASTER", "PROCESSLIST", "COMMIT", "ROLLBACK", "SAVEPOINT", "XA", "INTO",
)

# Forbidden as statement verbs, permitted as read-only function calls - but
# ONLY in the `NAME(` form. Deliberately short: each entry is a name where
# MySQL has both a write statement and a harmless scalar function, and where
# the function is one this schema genuinely needs.
_ALSO_FUNCTIONS = frozenset(("REPLACE",))


def _only_as_function(upper_sql, kw):
    """True when every occurrence of `kw` is immediately a call: NAME(...)."""
    for m in re.finditer(r"\b%s\b" % kw, upper_sql):
        if not upper_sql[m.end():m.end() + 1].lstrip(" ").startswith("("):
            # allow one space before the paren, nothing else
            rest = upper_sql[m.end():]
            if not re.match(r"\s*\(", rest):
                return False
    return True


_FORBIDDEN_FUNCTIONS = (
    "SLEEP", "BENCHMARK", "GET_LOCK", "RELEASE_LOCK", "IS_FREE_LOCK",
    "IS_USED_LOCK", "LOAD_FILE", "UPDATEXML", "EXTRACTVALUE", "NAME_CONST",
    "SYS_EVAL", "SYS_EXEC", "MASTER_POS_WAIT",
)

_FORBIDDEN_CLAUSES = (
    re.compile(r"\bFOR\s+UPDATE\b", re.I),
    re.compile(r"\bFOR\s+SHARE\b", re.I),
    re.compile(r"\bLOCK\s+IN\s+SHARE\s+MODE\b", re.I),
)

# Columns that must never be read, whichever table they live on.
DENIED_COLUMN_PATTERNS = (
    re.compile(r"password", re.I),
    re.compile(r"\bpwd\b", re.I),
    re.compile(r"secret", re.I),
    re.compile(r"api_key", re.I),
    re.compile(r"(?:reset|auth|session|csrf|access|refresh)_token", re.I),
)

# System schemas are never reachable, in any position, backticked or not.
_SYSTEM_SCHEMA_RE = re.compile(
    r"(?<![\w`])`?(?:information_schema|mysql|performance_schema|sys)`?\s*\.", re.I)

# One table reference: bare or backticked, optionally schema-qualified.
_TABLE_REF = r"(?:`[^`]*`|[A-Za-z_]\w*)(?:\s*\.\s*(?:`[^`]*`|[A-Za-z_]\w*))*"
_TABLE_REF_AT_START = re.compile(r"^(?:`[^`]*`|[A-Za-z_]\w*)(?:\s*\.\s*(?:`[^`]*`|[A-Za-z_]\w*))*")
_JOIN_RE = re.compile(r"\bJOIN\s+(" + _TABLE_REF + r")", re.I)
_FROM_KW_RE = re.compile(r"\bFROM\s+", re.I)
# keywords that end a comma-separated table list in a FROM clause
_CLAUSE_KW_AT_START = re.compile(
    r"^\s*\b(?:WHERE|GROUP|ORDER|HAVING|LIMIT|UNION|INTERSECT|EXCEPT|FOR|ON|"
    r"USING|JOIN|INNER|LEFT|RIGHT|CROSS|OUTER|STRAIGHT_JOIN|NATURAL)\b", re.I)
_ALIAS_RE = re.compile(r"\s+(?:AS\s+)?([A-Za-z_]\w*)", re.I)

_CTE_NAME_RE = re.compile(
    r"(?:\bWITH\s+(?:RECURSIVE\s+)?|,\s*)([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s+AS\s*\(", re.I)

# trailing LIMIT (matched on the FULL-masked text so a literal 'LIMIT 5' cannot fool it)
_LIMIT_TAIL = re.compile(r"\bLIMIT\s+(\d+)(?:\s*,\s*(\d+)|\s+OFFSET\s+(\d+))?\s*$", re.I)

# JSON accessors whose path literal could name a credential column
_JSON_FUNC_RE = re.compile(
    r"(?:JSON_EXTRACT|JSON_VALUE|JSON_UNQUOTE|JSON_SEARCH|JSON_CONTAINS_PATH)\s*\([^)]*?(['\"])(.*?)\1", re.I)
_JSON_ARROW_RE = re.compile(r"->>?\s*(['\"])(.*?)\1")


def _mask_atoms(sql, mask_idents=True):
    """Blank the contents of string literals ('...', "..."), and - when
    mask_idents is True - backtick identifiers too. Delimiters are kept and the
    result is the same length as the input. Handles backslash escapes and
    doubled quotes. Returns (masked_text, balanced) where balanced is False if
    a literal/identifier was left unterminated (fail closed)."""
    out = []
    i, n = 0, len(sql)
    balanced = True
    while i < n:
        c = sql[i]
        if c in ("'", '"', "`"):
            quote = c
            blank = mask_idents or quote != "`"
            out.append(quote)
            i += 1
            closed = False
            while i < n:
                ch = sql[i]
                if ch == "\\" and quote != "`" and i + 1 < n:
                    out.append("  " if blank else sql[i:i + 2])
                    i += 2
                    continue
                if ch == quote:
                    if i + 1 < n and sql[i + 1] == quote:  # doubled quote inside literal
                        out.append("  " if blank else sql[i:i + 2])
                        i += 2
                        continue
                    out.append(quote)
                    i += 1
                    closed = True
                    break
                out.append(" " if blank else ch)
                i += 1
            if not closed:
                balanced = False
                break
        else:
            out.append(c)
            i += 1
    return "".join(out), balanced


def _split_qualified(ref):
    """Split a possibly-backticked dotted reference into lowercased segments,
    respecting backticks (a '.' inside backticks is part of the name)."""
    segs, cur, inbt = [], [], False
    for c in ref:
        if c == "`":
            inbt = not inbt
            continue
        if c == "." and not inbt:
            segs.append("".join(cur).strip().lower())
            cur = []
            continue
        cur.append(c)
    segs.append("".join(cur).strip().lower())
    return segs


def _referenced_tables(text):
    """Every identifier used in a table position (FROM list incl. comma joins,
    and JOIN), as lowercased dot-joined names. `text` must be the IDENT-masked
    view so backtick identifier text is intact. CTE names are handled by the
    caller."""
    raw_refs = [m.group(1) for m in _JOIN_RE.finditer(text)]
    for m in _FROM_KW_RE.finditer(text):
        i = m.end()
        while i < len(text):
            while i < len(text) and text[i].isspace():
                i += 1
            if i >= len(text) or text[i] == "(":  # derived table: its inner FROM is matched on its own
                break
            tm = _TABLE_REF_AT_START.match(text[i:])
            if not tm:
                break
            raw_refs.append(tm.group(0))
            i += tm.end()
            rest = text[i:]
            am = _ALIAS_RE.match(rest)
            if am and not _CLAUSE_KW_AT_START.match(rest):
                i += am.end()
                rest = text[i:]
            cm = re.match(r"\s*,", rest)
            if not cm:
                break
            i += cm.end()
    return [".".join(_split_qualified(r)) for r in raw_refs]


def _cte_names(text):
    return {m.group(1).lower() for m in _CTE_NAME_RE.finditer(text)}


def has_limit(sql):
    """True if the query already has a trailing LIMIT (checked on the
    FULL-masked text so 'LIMIT' inside a literal cannot fool it)."""
    masked, _ = _mask_atoms(sql)
    return _LIMIT_TAIL.search(masked) is not None


def enforce_row_cap(sql, max_rows):
    """Return (capped_sql, forced_truncated).

    * No trailing LIMIT  -> append ``LIMIT max_rows+1`` (the +1 lets the caller
      detect that more rows existed). forced_truncated=False.
    * Trailing LIMIT n>max_rows (or offset form) -> rewrite the count down to
      max_rows. forced_truncated=True (the user asked for more than we return).
    * Trailing LIMIT n<=max_rows -> unchanged. forced_truncated=False.
    """
    masked, _ = _mask_atoms(sql)
    m = _LIMIT_TAIL.search(masked)
    if not m:
        return sql + " LIMIT %d" % (max_rows + 1), False
    g1, g2, g3 = m.group(1), m.group(2), m.group(3)
    if g2 is not None:            # LIMIT offset, count
        off, cnt, form = int(g1), int(g2), "off_cnt"
    elif g3 is not None:          # LIMIT count OFFSET offset
        off, cnt, form = int(g3), int(g1), "cnt_off"
    else:                          # LIMIT count
        off, cnt, form = None, int(g1), "cnt"
    if cnt <= max_rows:
        return sql, False
    if form == "off_cnt":
        tail = "LIMIT %d, %d" % (off, max_rows)
    elif form == "cnt_off":
        tail = "LIMIT %d OFFSET %d" % (max_rows, off)
    else:
        tail = "LIMIT %d" % max_rows
    return sql[:m.start()] + tail, True


def validate_sql(sql, allowed_tables, denied_star_tables=()):
    """Validate an LLM-written query.

    Returns (ok, reason_or_none, cleaned_sql). The reason is written for the
    LLM to read and self-correct.
    """
    if not isinstance(sql, str) or not sql.strip():
        return False, "Empty SQL. Provide one SELECT statement.", ""
    cleaned = sql.strip()
    if len(cleaned) > MAX_SQL_LEN:
        return False, "SQL too long (max %d chars). Simplify the query." % MAX_SQL_LEN, ""
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()

    masked, balanced = _mask_atoms(cleaned)              # strings + backticks blanked
    idents, balanced_i = _mask_atoms(cleaned, mask_idents=False)  # only strings blanked

    # fail closed on an unterminated / unbalanced quote or identifier
    if not balanced or not balanced_i or (masked.count("'") % 2) \
            or (masked.count('"') % 2) or (masked.count("`") % 2):
        return False, "Unbalanced quote / unterminated string or identifier. Fix the SQL.", ""

    if ";" in masked:
        return False, "Multiple statements are not allowed. Send exactly one SELECT.", ""
    if "--" in masked or "#" in masked or "/*" in masked:
        return False, "SQL comments are not allowed. Remove -- / # / /* */.", ""
    if not re.match(r"^\s*(SELECT|WITH)\b", masked, re.I):
        return False, "Only SELECT statements are allowed (optionally starting with WITH).", ""

    upper = masked.upper()
    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(r"\b%s\b" % kw, upper):
            # Some of these names are also read-only FUNCTIONS. REPLACE is the
            # one that bit: REPLACE INTO is a write, REPLACE(col,' ','_') is
            # ordinary string handling - and it is what canon_sql() emits, so
            # the guard was rejecting the exact join recipe the prompt tells
            # the model to use. Asked how many requested tests are unfilled,
            # the model wrote the correct normalised join three times, was
            # blocked three times, gave up and fell back to matching test_code
            # raw. That dropped the four codes spelled differently across
            # tables and answered 6 where the truth is 68.
            #
            # A trailing "(" is the whole distinction: no MySQL write verb is
            # ever followed by one, so allowing the call form gives up nothing.
            if kw in _ALSO_FUNCTIONS and _only_as_function(upper, kw):
                continue
            return False, "Keyword '%s' is not allowed. This tool is strictly read-only SELECT." % kw, ""
    for fn in _FORBIDDEN_FUNCTIONS:
        if re.search(r"\b%s\s*\(" % fn, upper):
            return False, "Function %s() is not allowed." % fn, ""
    for clause in _FORBIDDEN_CLAUSES:
        if clause.search(masked):
            return False, "Locking clauses (FOR UPDATE / FOR SHARE) are not allowed.", ""
    if "@" in masked:
        return False, "User variables (@x) are not allowed.", ""

    # system schemas anywhere (backticked or not) - checked on ident view
    if _SYSTEM_SCHEMA_RE.search(idents):
        return False, ("System-schema access (information_schema / mysql / "
                       "performance_schema / sys) is not allowed."), ""

    # table allowlist - read real identifier names from the ident view
    allowed = {t.lower() for t in allowed_tables}
    ctes = _cte_names(idents)
    for name in _referenced_tables(idents):
        segs = name.split(".")
        if not name or any(s == "" for s in segs):
            return False, "Malformed or empty table identifier is not allowed.", ""
        if len(segs) > 1:
            return False, ("Schema-qualified table '%s' is not allowed. Use plain table "
                           "names from the provided schema catalog." % name), ""
        if name in ctes:
            continue
        if name not in allowed:
            return False, ("Table '%s' is not in the allowed schema catalog. Only use the "
                           "tables listed in your instructions." % name), ""

    # credential/secret columns by name - on the ident view (backticks visible)
    for pat in DENIED_COLUMN_PATTERNS:
        if pat.search(idents):
            return False, ("The query references a credential/secret column, which is never "
                           "allowed. Select only the specific business columns you need."), ""

    # credential names hidden inside JSON path literals - scan the ORIGINAL sql
    json_paths = [g2 for _q, g2 in _JSON_FUNC_RE.findall(cleaned)]
    json_paths += [m.group(2) for m in _JSON_ARROW_RE.finditer(cleaned)]
    for path in json_paths:
        for pat in DENIED_COLUMN_PATTERNS:
            if pat.search(path):
                return False, "JSON access to a credential/secret field is not allowed.", ""

    # SELECT * / t.* on tables carrying denied columns (ident view; skip COUNT(*) etc.)
    if denied_star_tables:
        denied = {d.lower() for d in denied_star_tables}
        touched = [t for t in _referenced_tables(idents) if t in denied]
        if touched and re.search(r"(?:^|[\s,])(?:\w+\.)?\*", idents):
            return False, ("SELECT * is not allowed on '%s' because it contains restricted "
                           "columns. List the specific columns you need instead." % touched[0]), ""

    return True, None, cleaned
