# -*- coding: utf-8 -*-
"""Audit every answer the assistant has already given. FREE, no tokens, ~2s.

    python tools_answer_audit.py                # every recorded turn
    python tools_answer_audit.py --days 1       # just today
    python tools_answer_audit.py --id 30        # one turn, in full

WHY THIS EXISTS
---------------
Every defect fixed in this project so far was found the same way: a person read
an answer, felt something was off, and said so. That does not scale, and it means
a wrong answer is only caught when somebody happens to be looking at it.

But nothing about that inspection needed a person. nlp_search_audit already
stores the question, the answer, the route and the SQL for every turn ever run.
The checks below are the ones that found real defects by hand, written down so
they run over the whole history at once instead of one turn at a time.

WHAT IT CANNOT DO
-----------------
It cannot tell you an answer is WRONG. It tells you an answer has the SHAPE of
answers that turned out to be wrong. The three free evals test fixed inputs;
this tests the questions a person actually asked, which is the only corpus that
grows on its own. A clean run is not proof, and every finding still needs
reading - see rule 4 in docs/test_questions.md, which this tool obeys: the
verdicts narrow what you have to read, they do not replace reading it.

Each check records the hit rate it was calibrated at. A check that starts firing
on everything has stopped being a signal and should be tightened or deleted.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# --- the checks ------------------------------------------------------------
# Severity is about what a reader LOSES, not about how odd the row looks:
#   high    the answer could be believed and be wrong
#   medium  the answer is right but says something the reader cannot use
#   low     cosmetic, or a turn that produced nothing
HIGH, MEDIUM, LOW = "high", "medium", "low"


def check_route_none(turn, ctx):
    """A turn no worker owned: the question came back unanswered.

    Not a defect in itself - asking for a product name is often correct - but a
    rising count means questions are being turned away that should be answered.
    Calibrated at 3 of 30 turns.
    """
    if (turn["route"] or "").strip().lower() == "none":
        return (LOW, "no worker took this question")
    return None


# Words that belong to the SCHEMA, not to a person reading an answer. Reason
# codes are deliberately excluded: CE_LIMIT_EXCEEDED is the lab's own vocabulary
# and appears on screen, so it is UPPER_SNAKE and this only matches lower_snake.
# `timeline` and `cohort` are left OUT on purpose: they are ordinary English
# before they are primitive names, and including them fired on answers that were
# simply using the words.
_PRIMITIVE_NAMES = (
    "modifications_before_pass", "config_diff", "common_config", "metric_delta",
    "failure_modes", "rejection_modes", "review_history", "review_load",
    "resolved_how", "failure_detail", "analyse_history",
)


def check_schema_leak(turn, ctx):
    """Table names, column names and primitive names in the prose.

    "23 tests are currently in progress across 23 planner entries (out of 61
    total planner_entries)" - the reader does not have a table called
    planner_entries, they have a schedule. Worse when it is a primitive name:
    "the modifications_before_pass trace shows" tells them about the machine
    rather than about their lab.

    Calibrated at 9 of 30 turns.
    """
    answer = turn["answer"] or ""
    found = []
    for name in _PRIMITIVE_NAMES:
        if name in answer:
            found.append(name)
    # ONLY identifiers carrying an underscore. A bare table name is usually
    # also the English word for the thing - equipment, users, datasheet - and
    # matching those fired on half of every turn recorded, which is not a
    # signal, it is a synonym list. planner_entries and rejected_at are nobody's
    # English; test_code and failure_reason_code are the ones worth catching.
    for name in ctx["tables"] | ctx["columns"]:
        if "_" not in name:
            continue
        if re.search(r"(?<![`\w])" + re.escape(name) + r"(?![`\w])", answer):
            found.append(name)
    if found:
        uniq = sorted(set(found))[:6]
        return (MEDIUM, "schema words in the prose: " + ", ".join(uniq))
    return None


_UNVERIFIED_RE = re.compile(
    r"could not verify[^.]*?\(([^)]*\d[^)]*)\)", re.I)


def check_unverified_leak(turn, ctx):
    """The verify layer showing its working to the user.

    "I could not verify one part of my answer (75, 200), so here is what the
    database actually returned instead" - 75 was a row id and 200 was the LIMIT.
    Neither is a claim about the lab, and naming them makes a sound answer look
    unsound. Calibrated at 2 of 30 turns.
    """
    m = _UNVERIFIED_RE.search(turn["answer"] or "")
    if m:
        return (MEDIUM, "withheld over instrumentation numbers: (%s)" % m.group(1))
    return None


# "no X", "none", "nothing", "0 X" - phrased the several ways the model phrases
# them. Anchored to the start of a sentence so a mid-sentence "no" does not fire.
_ABSENCE_RE = re.compile(
    r"(?:^|[.!?]\s+|\n)\s*(?:no |none |nothing |there (?:are|were|is|was) no |"
    r"0 (?:records|rows|results|changes|modifications|datasheets|tests))",
    re.I)


def check_absence_claim(turn, ctx):
    """An answer that reports an ABSENCE, which is the costly failure here.

    "No changes were recorded as introduced before the unit passed" was produced
    by a lookup that matched nothing, not by a lab in which nothing happened, and
    the two are indistinguishable in the answer. Every absence is worth reading
    once: it is the one shape where a bug and a fact look identical.

    Calibrated at 5 of 30 turns. This one is EXPECTED to fire on correct answers
    - it is a reading list, not an accusation.
    """
    if _ABSENCE_RE.search(turn["answer"] or ""):
        return (HIGH, "asserts an absence - confirm the filter did not manufacture it")
    return None


_NAMES_ONE_RE = re.compile(r"\b(?:DEMO|IEC|TFS)[- ][A-Z0-9-]{3,}\b")


def check_mixed_scope(turn, ctx):
    """An answer about ONE thing carrying a number counted over everything.

    Asked which of DEMO Kestrel Spectrometer's tests were finished, the answer
    ended "There are also 14 tests in draft status and 3 tests with no results".
    Both were SELECT COUNT(*) with no product filter - the whole lab. Kestrel has
    one draft and nothing without a result, so the reader learns this product has
    fourteen unfinished tests when it has one.

    Fires when the answer names exactly ONE job or product AND one of its
    queries is a bare unfiltered total. Calibrated at 1 of 37 turns.
    """
    from nlp_search import sql_tool
    answer = turn["answer"] or ""
    # EXACTLY ONE subject. An answer listing many jobs is a lab-wide answer and a
    # lab-wide count belongs in it; the damage is done when one product is named
    # and the number beside it came from all of them. Without this the check fired
    # on 4 of 37 turns and only one was real.
    if len(set(_NAMES_ONE_RE.findall(answer))) != 1:
        return None
    for sql in _statements(turn["sql_queries"]):
        if sql_tool._whole_lab_note(sql):
            return (HIGH, "names one product but counted the whole lab: %s"
                    % sql[:64])
    return None


def check_alias_confusion(turn, ctx):
    """A column aliased to the name of a DIFFERENT real column.

    SELECT p.tco_id AS job_number put a column headed "Job Number" over a list
    of TCOs. Detected at query time now; kept here so the history is checkable
    and so a regression shows up. Calibrated at 1 of 224 queries.
    """
    from nlp_search import sql_tool
    hits = []
    for sql in _statements(turn["sql_queries"]):
        note = sql_tool._alias_confusion_note(sql)
        if note:
            hits.append(note["alias_check"].split(" - ", 1)[-1].split(".")[0])
    if hits:
        return (HIGH, "misleading column name: " + "; ".join(sorted(set(hits))))
    return None


_NOW_RE = re.compile(r"\b(right now|currently|at the moment|today|as of now)\b", re.I)
_DATE_COL_RE = re.compile(r"\b(start_date|end_date|test_date|planned_end_date|due_date)\b", re.I)
_STATUS_RE = re.compile(r"\bstatus\s*(?:=|IN)\b", re.I)


def check_status_as_time(turn, ctx):
    """A question about NOW answered from a workflow status alone.

    in_progress is a state a row sits in until a person moves it. Asked what was
    being tested right now, a status-only filter returned 23 entries whose
    scheduled end dates had every one already passed. If the question says now,
    the query has to look at the dates. Calibrated at 1 of 30 turns.
    """
    asks_now = _NOW_RE.search((turn["question"] or "") + " " + (turn["answer"] or ""))
    if not asks_now:
        return None
    for sql in _statements(turn["sql_queries"]):
        if _STATUS_RE.search(sql) and not _DATE_COL_RE.search(sql):
            return (HIGH, "a 'now' question filtered on status with no date test")
    return None


_EMPTY_MARKER_RE = re.compile(r"(?:^|\n)\s*(?:[-*+]|\d+[.)])\s*(?:\n|$)")


def check_render_artifact(turn, ctx):
    """Markup that reaches the screen as noise.

    Two empty bullets hung under a schedule table as stray dashes. Dropped by the
    renderer now; kept here because the model still emits them and a second
    artifact of the same kind would otherwise go unnoticed.
    Calibrated at 2 of 30 turns.
    """
    if _EMPTY_MARKER_RE.search(turn["answer"] or ""):
        return (LOW, "empty list marker in the answer")
    return None


CHECKS = (
    ("mixed_scope", check_mixed_scope),
    ("alias_confusion", check_alias_confusion),
    ("status_as_time", check_status_as_time),
    ("absence_claim", check_absence_claim),
    ("schema_leak", check_schema_leak),
    ("unverified_leak", check_unverified_leak),
    ("render_artifact", check_render_artifact),
    ("route_none", check_route_none),
)


# --- plumbing --------------------------------------------------------------

def _statements(blob):
    """The SQL of one turn, one statement per entry, comments dropped."""
    out = []
    for line in str(blob or "").split("\n"):
        line = line.strip()
        if line and not line.startswith("--"):
            out.append(line)
    return out


def _context():
    from nlp_search import schema_catalog as cat
    cols = set()
    for names in cat.COLUMNS.values():
        cols.update(names)
    return {"tables": set(cat.ALLOWED_TABLES), "columns": cols}


def _turns(cur, days, one):
    where, args = "", []
    if one:
        where, args = "WHERE id = %s", [one]
    elif days:
        where, args = "WHERE created_at >= NOW() - INTERVAL %s DAY", [days]
    cur.execute(
        "SELECT id, created_at, question, answer, route, sql_queries, "
        "estimated_cost_usd, total_tokens, latency_ms "
        "FROM nlp_search_audit " + where + " ORDER BY id", args)
    keys = ("id", "created_at", "question", "answer", "route", "sql_queries",
            "cost", "tokens", "latency")
    return [dict(zip(keys, r)) for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None)
    ap.add_argument("--id", type=int, default=None)
    args = ap.parse_args()
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    import mysql_config  # noqa: F401 - loads .env into os.environ
    import pymysql
    cn = pymysql.connect(host=os.environ.get("MYSQL_HOST", "localhost"),
                         user=os.environ.get("MYSQL_USER", "root"),
                         password=os.environ.get("MYSQL_PASSWORD", ""),
                         database=os.environ.get("MYSQL_DATABASE", ""))
    cur = cn.cursor()
    turns = _turns(cur, args.days, args.id)
    cn.close()
    if not turns:
        print("no recorded turns to audit")
        return 0

    ctx = _context()
    findings = {}
    for turn in turns:
        for name, fn in CHECKS:
            try:
                got = fn(turn, ctx)
            except Exception as exc:            # noqa: BLE001
                got = (LOW, "check crashed: %s" % exc)
            if got:
                findings.setdefault(name, []).append((turn, got[0], got[1]))

    rank = {HIGH: 0, MEDIUM: 1, LOW: 2}
    order = sorted(findings, key=lambda n: (rank[findings[n][0][1]], n))
    for name in order:
        hits = findings[name]
        sev = hits[0][1]
        print()
        print("%-7s %-18s %d of %d turn(s)" % (sev.upper(), name, len(hits), len(turns)))
        print("-" * 78)
        for turn, _sev, why in hits:
            print("  turn %-4s %s" % (turn["id"], (turn["question"] or "")[:60]))
            print("            %s" % why)
            if args.id:
                print()
                print(turn["answer"])
    print()
    high = sum(len(v) for k, v in findings.items() if v[0][1] == HIGH)
    print("%d turn(s) audited, %d finding(s), %d at high severity"
          % (len(turns), sum(len(v) for v in findings.values()), high))
    print("A finding is a READING LIST, not a verdict. An absence is the one "
          "shape where a bug and a fact look identical.")
    return 1 if high else 0


if __name__ == "__main__":
    sys.exit(main())
