# -*- coding: utf-8 -*-
"""The evidence ledger: every row the system actually saw while answering.

One ledger per question. The critical property is that **only the tools write
to it** - a worker agent narrates, but it cannot append evidence, because the
append happens inside ``sql_tool`` after MySQL returns. So the ledger is a
record of what the database said, not of what the model believes.

That is what makes the grounding check in verify.py possible: a claim in the
final answer is either supported by a cell in here or it is not. Prompting a
model to "never make things up" is a request; checking the answer against the
rows is a control.

The ledger is also where the per-question budget lives, because the natural
place to count queries and rows is the thing that already sees all of them.
"""
import re
import time


class BudgetExceeded(Exception):
    """Raised into a tool call when the question has used up its allowance."""


class Ledger:
    """Evidence + budget for a single question."""

    # 24 queries let one question run to 626k tokens and 64 seconds hunting
    # through a schema it could now see all of. Twelve is more than any real
    # question here has needed - the complex cross-domain ones settle in 2-4.
    def __init__(self, max_queries=12, max_rows=3000, deadline_s=60):
        self.entries = []
        self.max_queries = max_queries
        self.max_rows = max_rows
        self.deadline_s = deadline_s
        self.started = time.time()
        self._rows_seen = 0
        self._values = None          # lazily built, invalidated on append
        self.notes = []              # non-SQL observations (probe results, refusals)
        # Reviewed measures that actually ran, with the caveat each one carries.
        # Kept OFF self.notes on purpose: note text is tokenised into values()
        # for grounding, and a caveat is commentary, not evidence - it must not
        # become something a claim can be grounded against.
        self.metrics = []

    # -- budget ------------------------------------------------------------

    def check_budget(self):
        """Raise BudgetExceeded when this question has had enough.

        Checked BEFORE a query runs. The caller turns the exception into a
        tool-result string so the agent can wind up gracefully and answer with
        what it already has, rather than the run dying mid-flight.
        """
        if len(self.entries) >= self.max_queries:
            raise BudgetExceeded(
                "Query budget spent (%d queries). Answer from the evidence "
                "already gathered, or say what is still missing."
                % self.max_queries)
        if self._rows_seen >= self.max_rows:
            raise BudgetExceeded(
                "Row budget spent (%d rows). Aggregate instead of listing, or "
                "answer from what you have." % self.max_rows)
        elapsed = time.time() - self.started
        if elapsed >= self.deadline_s:
            raise BudgetExceeded(
                "Time budget spent (%ds). Answer from the evidence already "
                "gathered." % self.deadline_s)

    def remaining(self):
        return {"queries": max(0, self.max_queries - len(self.entries)),
                "rows": max(0, self.max_rows - self._rows_seen),
                "seconds": max(0, int(self.deadline_s - (time.time() - self.started)))}

    # -- writing -----------------------------------------------------------

    def record(self, worker, sql, columns=(), rows=(), truncated=False, error=None):
        """Append one executed query and what it returned. Tools only."""
        entry = {
            "n": len(self.entries) + 1,
            "worker": worker,
            "sql": (sql or "").strip(),
            "columns": list(columns or ()),
            "rows": [list(r) for r in (rows or ())],
            "row_count": len(rows or ()),
            "truncated": bool(truncated),
            "error": error,
            "at": round(time.time() - self.started, 2),
        }
        self.entries.append(entry)
        self._rows_seen += entry["row_count"]
        self._values = None
        return entry

    def used_metric(self, name, caveat=None, label=None):
        """Record that a reviewed measure ran. The caveat travels with it so
        the answer can carry it whether or not the model repeats it."""
        self.metrics.append({"name": name, "caveat": (caveat or "").strip(),
                             "label": label or name})

    def note(self, kind, text):
        """Record something that is evidence but is not a SELECT result -
        a probe that listed a column's real values, or a refusal."""
        self.notes.append({"kind": kind, "text": str(text)[:1000],
                           "at": round(time.time() - self.started, 2)})

    # -- reading -----------------------------------------------------------

    @property
    def query_count(self):
        return len(self.entries)

    @property
    def row_count(self):
        return self._rows_seen

    def successful(self):
        return [e for e in self.entries if not e["error"]]

    def has_rows(self):
        return any(e["row_count"] for e in self.entries)

    def probed_values_for(self, needle=""):
        """True if something actually checked what values exist - the
        precondition for being allowed to say "there are none of those".

        Both probes count: list_values establishes that a category is or is not
        real, and resolve_entity does the same for a name. Either is a genuine
        check; a query that merely returned no rows is not.
        """
        needle = (needle or "").lower()
        return any(n["kind"] in ("values", "resolve")
                   and (not needle or needle in n["text"].lower())
                   for n in self.notes)

    def values(self):
        """Every scalar the database returned, lower-cased, for grounding.

        Numbers are also stored bare (1,234 -> 1234, 23.40 -> 23.4) so an
        answer that reformats a value still matches the cell it came from.
        """
        if self._values is None:
            vals = set()
            for e in self.entries:
                for row in e["rows"]:
                    for cell in row:
                        for v in _normalise(cell):
                            vals.add(v)
                # a COUNT of 0 is evidence of absence and must be citable
                if e["row_count"] == 0 and not e["error"]:
                    vals.add("0")
            for n in self.notes:
                # probe output is JSON, so strip the punctuation that would
                # otherwise leave tokens like '"table":' unmatchable
                for tok in re.split(r"[,\s]+", n["text"]):
                    tok = tok.strip("'\"()[]{}:").lower()
                    if tok:
                        vals.add(tok)
                        if "." in tok:
                            vals.update(p for p in tok.split(".") if p)
            self._values = vals
        return self._values

    def id_only_numbers(self):
        """Bare integers that appear ONLY in identifier columns.

        WHY THIS EXISTS
        ---------------
        values() returns every scalar the database handed back, primary keys
        included, and verify.py treats that whole set as things an answer is
        allowed to assert. Asked which engineers have not filled in a single
        datasheet, the model answered "Kondababu Arjilli has 10 tests assigned
        with no datasheet, and Krishna Gonela has 3". Its own queries returned
        12 and 2. But `planner_entries.id` 10 and 3 both exist and both came
        back in a result set, so both numbers looked grounded, and the answer
        was shown with a badge reading "Corrected to match the data".

        A row's primary key is not evidence for how many of anything there
        are. Nothing legitimate is lost by refusing it: an answer that quotes a
        raw id is already a separate defect, caught by _ID_LEAK_RE.

        Only NUMERIC values are withheld, and only where every occurrence was
        in an id column. tco_id holds 'IEC-EMC-004' - the identifier a lab
        engineer actually uses - and that stays citable. A number that also
        appears as a COUNT, a measure or any ordinary column stays citable too,
        because then it really is evidence.
        """
        in_ids, elsewhere = set(), set()
        for e in self.entries:
            if e["error"]:
                continue
            cols = [str(c or "").strip().lower() for c in e["columns"]]
            flags = [c == "id" or c.endswith("_id") for c in cols]
            for row in e["rows"]:
                for i, cell in enumerate(row):
                    if cell is None:
                        continue
                    s = str(cell).strip()
                    if not s or not _INT_RE.match(s.replace(",", "")):
                        continue
                    is_id = flags[i] if i < len(flags) else False
                    (in_ids if is_id else elsewhere).update(_normalise(cell))
        return in_ids - elsewhere

    # -- handover ----------------------------------------------------------

    def evidence_digest(self, max_rows_per_query=8, with_sql=True):
        """A compact rendering of the ledger for the synthesis + verify steps.

        The SQL is included because a checker that sees only numbers cannot
        tell what they are numbers OF. A run once answered "11 CE datasheets"
        for an engineer with two: 11 was a genuine figure from a different
        query in the same ledger, so value-matching passed it. With the
        statement alongside, "that 11 counted requested tests, not datasheets"
        becomes visible.
        """
        out = []
        for e in self.entries:
            if e["error"]:
                out.append("[%d] %s -> ERROR: %s" % (e["n"], e["worker"], e["error"]))
                continue
            head = "[%d] %s -> %d row(s)%s" % (
                e["n"], e["worker"], e["row_count"],
                " (truncated)" if e["truncated"] else "")
            out.append(head)
            if with_sql and e["sql"]:
                out.append("    sql: " + " ".join(e["sql"].split())[:400])
            if e["columns"]:
                out.append("    columns: " + ", ".join(str(c) for c in e["columns"]))
            for row in e["rows"][:max_rows_per_query]:
                out.append("    " + " | ".join("" if c is None else str(c)[:80] for c in row))
            if e["row_count"] > max_rows_per_query:
                out.append("    ... %d more row(s)" % (e["row_count"] - max_rows_per_query))
        for n in self.notes:
            out.append("[note:%s] %s" % (n["kind"], n["text"]))
        return "\n".join(out) if out else "(no evidence gathered)"

    def sql_log(self):
        return "\n\n".join("-- %s\n%s" % (e["worker"], e["sql"])
                           for e in self.entries if e["sql"])

    def queries(self):
        """Every statement that actually ran, for display to the user.

        The UI used to dig the SQL out of the model's tool-call arguments,
        which stopped working the moment the tool was renamed and showed
        nothing at all for a while. This is the record of what executed, so it
        cannot drift from the answer it produced.
        """
        return [{"worker": e["worker"], "sql": e["sql"],
                 "rows": e["row_count"], "error": e["error"]}
                for e in self.entries if e["sql"]]

    def summary(self):
        return {"queries": self.query_count, "rows": self.row_count,
                "probes": len(self.notes),
                "workers": sorted({e["worker"] for e in self.entries}),
                "errors": sum(1 for e in self.entries if e["error"]),
                "elapsed_s": round(time.time() - self.started, 2)}


_NUM_RE = re.compile(r"^-?[\d,]*\.?\d+$")
# Whole numbers only. A measured value like 0.212 is a fact even in a column
# whose name ends in _id, and withholding it would reject a real answer.
_INT_RE = re.compile(r"^\d+$")
_TOKEN_RE = re.compile(r"[A-Za-z0-9.]+")


def _normalise(cell):
    """The forms a cell might legitimately be written back in.

    Includes the pieces INSIDE a cell, not just the cell whole. A basic
    standard is stored as one string - "IEC 61000-4-2:2008 & EN 61000-4-2:2009"
    - and an answer that cites "61000-4-2" or "2008" is quoting the evidence,
    not inventing. Whole-cell matching alone rejects those and triggers a
    pointless rewrite of a perfectly good answer.
    """
    if cell is None:
        return ("none", "null", "-")
    s = str(cell).strip()
    if not s:
        return ()
    out = {s.lower()}
    bare = s.replace(",", "")
    if _NUM_RE.match(bare):
        out.add(bare.lower())
        try:
            f = float(bare)
            out.add(repr(int(f)) if f == int(f) else repr(f))
            out.add(("%g" % f).lower())
            # str() as well, for the one form the other two never produce: a
            # whole number with a decimal point. MySQL hands back a measurement
            # as DECIMAL(18,6), so 52 dBuV arrives as "52.000000", and the forms
            # above reduce that to "52" - while an answer quoting the reading
            # writes "52.0", which is the same number and was rejected as
            # unsupported. The grounding check then replaced a correct answer
            # with a raw evidence dump.
            out.add(str(f).lower())
        except ValueError:
            pass
    # dates: 2026-05-12 00:00:00 is the same fact as 2026-05-12
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        out.add(s[:10].lower())
    # sub-tokens, so a figure quoted out of a longer cell still grounds
    if len(s) > 3:
        for tok in _TOKEN_RE.findall(s)[:60]:
            tok = tok.strip(".").lower()
            if len(tok) > 1:
                out.add(tok)
    return tuple(out)
