# -*- coding: utf-8 -*-
"""Pre-flight gates: check the question before anyone tries to answer it.

Everything here is deterministic SQL against the real database. No model is
involved, so a gate cannot itself invent anything - it can only report what is
or is not in the data.

The point is the ORDER. The old design let the model answer and then tried to
judge whether the answer was true, which is a coin flip: every post-hoc
correctness signal available measures around 0.65 AUROC and cannot separate
right answers from wrong ones at any threshold. Checking first is not a better
judge, it removes the need for one.

Three checks, each aimed at a failure that was actually observed:

  1. FALSE PREMISE.  "why did the RE test fail?" smuggles in "it failed" as
     settled fact. RE passed. The old system answered the question it was
     asked and produced "no recorded results for RE" - wrong twice over.
     Now the premise is tested before an answer exists.

  2. PHANTOM LITERAL.  "how many are marked Rejected" filters on a value that
     is not in the column. MySQL returns zero rows, which reads exactly like a
     real absence, and the assistant reports "none" - quietly confirming the
     user's wrong assumption.

  3. DEAD COLUMN.  "how many tests ran in July" filters on datasheet.test_date,
     which is NULL on every row. Zero rows again, and "no tests ran in July" is
     a different claim from "we do not record when tests ran".

A gate returning a verdict does not always end the conversation - most of the
time it hands the orchestrator a fact to carry into its answer. Only a
disproved premise short-circuits, because continuing would mean answering a
question whose subject does not exist.
"""
import re

import pymysql

from .schema_catalog import COLUMNS
from .semantics import TEST_CODE_CANON

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


def _q(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()


# --------------------------------------------------------------------------
# 1. false premise
# --------------------------------------------------------------------------
# Only outcome claims are checked. "why did X fail", "which tests failed",
# "the ESD test failed right?" - all assert an outcome the data can settle.

_FAIL_PREMISE_RE = re.compile(
    r"\b(?:why|how come|what caused|reason)\b[^?]{0,60}\b(?:fail|failed|failing)\b"
    r"|\b(?:which|what|how many|any)\b[^?]{0,40}\b(?:fail|failed|failing)\b"
    r"|\b(?:test|datasheet|it)\s+(?:has\s+)?failed\b"
    r"|\bfailed\s+(?:test|tests|datasheet|datasheets)\b", re.I)

_TEST_CODE_RE = re.compile(
    r"\b(CE|RE|EFT|ESD|SURGE|VOLTAGEDIPS|VOLTAGE[ _-]?DIPS|HARMONIC|"
    r"VOLTAGEFLICKER|FLICKER|CRF|PFMF|RS[_ ]?RI|RS)\b", re.I)

# Words for a bad outcome that the result column might actually hold.
_FAILURE_VALUES = ("fail", "failed", "failure", "not met", "no")


def _check_failure_premise(cur, question):
    """Does the data support the assumed failure? Returns a verdict or None."""
    if not _FAIL_PREMISE_RE.search(question or ""):
        return None

    rows = _q(cur, "SELECT result, COUNT(*) FROM `datasheet` "
                   "WHERE result IS NOT NULL AND result <> '' GROUP BY result")
    present = {str(r[0]).strip().lower(): int(r[1]) for r in rows}
    failing = {v: n for v, n in present.items()
               if any(f in v for f in _FAILURE_VALUES)}
    if failing:
        return None                      # failures exist; the premise may hold

    vocab = ", ".join("'%s' (%d)" % (r[0], r[1]) for r in rows) or "nothing yet"

    # Narrow it to the named test if there is one, so the reply is specific.
    m = _TEST_CODE_RE.search(question or "")
    scope = ""
    if m:
        code = TEST_CODE_CANON.get(
            m.group(1).upper().replace(" ", "_").replace("-", "_"),
            m.group(1).upper())
        got = _q(cur, "SELECT result, status, job_number FROM `datasheet` "
                      "WHERE test_code = %s", (code,))
        if not got:
            return {"gate": "false_premise", "blocking": True,
                    "fact": "No datasheet exists for %s at all, so it cannot have "
                            "failed. Recorded results across all datasheets: %s."
                            % (code, vocab)}
        scope = " The %s datasheet(s) recorded: %s." % (
            code, "; ".join("%s (%s)" % (r[0] or "no result", r[1]) for r in got))

    return {"gate": "false_premise", "blocking": True,
            "fact": ("Nothing in this database has failed. The result column only "
                     "ever contains %s - there is no Fail value anywhere.%s Answer "
                     "by correcting the premise; do NOT go looking for a failure "
                     "and do NOT report an absence of results as if it were an "
                     "absence of failure." % (vocab, scope))}


# --------------------------------------------------------------------------
# 2. phantom literal
# --------------------------------------------------------------------------
# Quoted or Capitalised words in the question that look like they name a status
# or result, checked against what those columns actually contain.

_CATEGORICAL = (
    ("datasheet", "status"), ("datasheet", "result"), ("datasheet", "test_code"),
    ("planner_entries", "status"), ("iec_emc_requests", "status"),
    ("iec_emc_request_tests", "workflow_status"), ("users", "role"),
    ("equipment", "status"), ("equipment", "calibration_status_col"),
)

_CANDIDATE_RE = re.compile(r"[\"']([A-Za-z][\w ]{2,28})[\"']|\b([A-Z][a-z]{3,15})\b")
_COMMON = {
    "which", "what", "when", "where", "there", "these", "those", "krishna",
    "please", "thanks", "show", "tell", "give", "list", "count", "many",
    "test", "tests", "lab", "datasheet", "datasheets", "equipment", "report",
    "reports", "engineer", "engineers", "request", "requests", "status",
    "result", "results", "product", "products", "user", "users", "monday",
    "tuesday", "wednesday", "thursday", "friday", "january", "february",
    "march", "april", "june", "july", "august", "september", "october",
    "november", "december",
}


def _check_phantom_literal(cur, question):
    """A capitalised/quoted word that names no value in any status-ish column."""
    words = []
    for m in _CANDIDATE_RE.finditer(question or ""):
        w = (m.group(1) or m.group(2) or "").strip()
        if w and w.lower() not in _COMMON and w.lower() not in words:
            words.append(w.lower())
    if not words:
        return None

    vocab = {}
    for table, col in _CATEGORICAL:
        if col not in COLUMNS.get(table, ()):
            continue
        try:
            rows = _q(cur, "SELECT DISTINCT `%s` FROM `%s` WHERE `%s` IS NOT NULL "
                           "LIMIT 40" % (col, table, col))
        except Exception:  # noqa: BLE001
            continue
        vocab["%s.%s" % (table, col)] = [str(r[0]).strip() for r in rows if r[0]]

    known = {v.lower() for vals in vocab.values() for v in vals}
    for word in words:
        if word in known or any(word in k for k in known):
            continue
        # Only flag when it is being used AS a value - "marked X", "status X".
        if not re.search(r"\b(?:marked|flagged|status|state|result|labell?ed|"
                         r"set to|in)\s+(?:as\s+)?[\"']?%s" % re.escape(word),
                         question, re.I):
            continue
        near = {k: v for k, v in vocab.items()
                if k.endswith((".status", ".result", ".workflow_status",
                               ".calibration_status_col"))}
        return {"gate": "phantom_literal", "blocking": False,
                "fact": ("'%s' is not a value anywhere in this database. The real "
                         "values are: %s. Say there is no such value and answer in "
                         "the vocabulary that exists - do NOT report a count of "
                         "zero, which means something different."
                         % (word, "; ".join("%s = %s" % (k, ", ".join(v))
                                            for k, v in near.items())))}
    return None


# --------------------------------------------------------------------------
# 3. dead column
# --------------------------------------------------------------------------

_TIME_RE = re.compile(
    r"\b(?:last|this|next|past)\s+(?:week|month|year|quarter)\b|"
    r"\bin\s+(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\b|\bwhen\s+(?:was|were|did)\b|"
    r"\b(?:since|before|after|during)\s+\w+|\brecently\b|\btoday\b|\bthis week\b",
    re.I)

# The date columns a time question would reach for, in the order it would try.
_DATE_COLUMNS = (
    ("datasheet", "test_date"), ("datasheet", "signoff_date"),
    ("planner_entries", "start_date"), ("planner_entries", "end_date"),
    ("iec_emc_requests", "test_completion_date"),
)


def _check_dead_dates(cur, question):
    """A time-scoped question when the obvious date columns are entirely NULL."""
    if not _TIME_RE.search(question or ""):
        return None
    empty, populated = [], []
    for table, col in _DATE_COLUMNS:
        if col not in COLUMNS.get(table, ()):
            continue
        try:
            n = _q(cur, "SELECT COUNT(`%s`) FROM `%s`" % (col, table))[0][0]
        except Exception:  # noqa: BLE001
            continue
        (populated if n else empty).append(("%s.%s" % (table, col), n))
    if not empty:
        return None
    return {"gate": "dead_dates", "blocking": False,
            "fact": ("These date columns are empty on every row: %s. A question "
                     "scoped to a time period cannot be answered from them - "
                     "'no rows in that period' would mean 'we do not record the "
                     "date', not 'nothing happened'. Usable dates: %s. Say which "
                     "you used, or say the date is not recorded."
                     % (", ".join(c for c, _n in empty),
                        ", ".join("%s (%d rows)" % (c, n) for c, n in populated)
                        or "none"))}


# --------------------------------------------------------------------------
# public
# --------------------------------------------------------------------------

def run(question, db_params, ledger=None):
    """Every gate, in order. Returns [] or a list of verdicts.

    A verdict is {"gate", "blocking", "fact"}. `blocking` means the question
    rests on something the data disproves and should be answered by correcting
    it rather than by querying. Non-blocking verdicts are facts the answer must
    carry. Never raises - a gate failing must not cost the user their answer.
    """
    out = []
    conn = None
    try:
        conn = _connect(db_params)
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute("SET SESSION MAX_EXECUTION_TIME=%d" % STATEMENT_TIMEOUT_MS)
            for check in (_check_failure_premise, _check_phantom_literal,
                          _check_dead_dates):
                try:
                    v = check(cur, question)
                except Exception:  # noqa: BLE001
                    v = None
                if v:
                    out.append(v)
        conn.rollback()
    except Exception:  # noqa: BLE001 - gates are a safety net, not a dependency
        return out
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    if ledger is not None:
        for v in out:
            ledger.note("gate", "%s: %s" % (v["gate"], v["fact"]))
    return out


def prompt_block(verdicts):
    """The instruction injected for one question, or "" when nothing fired."""
    if not verdicts:
        return ""
    lines = ["", "## CHECKED BEFORE YOU ANSWER - these are facts, not guesses", ""]
    for v in verdicts:
        lines.append("- %s" % v["fact"])
        if v.get("blocking"):
            lines.append("  This DISPROVES what the question assumes. Lead with the "
                         "correction. Do not run queries hunting for the thing that "
                         "is not there.")
    lines.append("")
    return "\n".join(lines)
