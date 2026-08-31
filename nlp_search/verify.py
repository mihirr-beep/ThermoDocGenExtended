# -*- coding: utf-8 -*-
"""Grounding check: does the answer only say things the database returned?

Telling a model not to make things up is a request. This is the control.

Every answer is checked against the ledger - the rows the tools actually
received - before it reaches the user. Two passes, cheapest first:

  1. **Deterministic.** Pull every number and date out of the draft and require
     each to appear in the evidence, in the question, or as a row count. This
     is the pass that matters, because numbers are what people act on, and it
     cannot itself hallucinate. Most answers clear it and cost nothing.

  2. **Adjudication.** Only when pass 1 flags something. One cheap model call
     decides, per flagged token, whether the evidence really supports it - a
     figure can be legitimately derived (3 rows returned -> "three tests") in a
     way string matching cannot see. The adjudicator may only ever mark a claim
     supported or unsupported; it cannot add content, so a hallucination there
     can withhold a true answer but never manufacture a false one.

If something is genuinely unsupported the answer is rewritten from the evidence
alone, and if that still fails it degrades to an honest statement of what was
and was not found. Failing closed is the point: a missing answer is a nuisance,
a confident wrong number is a defect that reaches a report.

Separately, "there are none" is only allowed when the ledger shows somebody
checked that the value exists. A filter on a value that is not in the table
returns zero rows and reads exactly like a real absence; that confusion is the
single most common way an NL->SQL system is confidently wrong.
"""
import json
import os
import re

VERIFIER_MODEL_ENV = "NLP_VERIFY_MODEL"
# The judge now shares the generator's model, which is not ideal - a judge
# with the same blind spots will nod through the same mistakes. It is
# accepted here because cost is the priority and the exposure is bounded:
# the pass that actually matters is the DETERMINISTIC one above, which
# requires every number in the answer to appear in the ledger and cannot
# itself hallucinate. The model call is only the second pass, and it may
# just mark a claim supported or unsupported - it cannot add content. So a
# blind spot here can let a bad claim through, never invent one.
#
# Set NLP_VERIFY_MODEL=gpt-4o to buy back the independence for ~$0.002 a
# question; that is the cheap half of the call.
DEFAULT_VERIFIER_MODEL = "gpt-4o-mini"

# Numbers worth checking. Bare 0-2 are excluded: they are almost always prose
# ("one of the", "both") rather than a claim, and they generate noise. The
# decimal point must be followed by a digit, or a sentence-final "...in 2026."
# is captured as the token "2026." and never matches anything.
#
# (?<!\w-) drops the tail of a hyphenated identifier. The lab's own names are
# built that way - AUR-C5-230, DEMO-EMC-201, IEC-EMC-004 - and without this the
# verifier pulled "230" out of a model number, looked for a cell containing
# 230, found none, and flagged a correct answer as unsupported. It cannot be
# the broader (?<!-): a margin of -3.4 dB is a real quantity that must still be
# checked, and the difference between the two cases is whether the hyphen
# follows a word character or a space.
_NUMBER_RE = re.compile(r"(?<![\w.])(?<!\w-)(\d[\d,]*(?:\.\d+)?)(?![\w])")
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_TRIVIAL = {"0", "1", "2"}

# Phrases that assert absence. These need a value-existence probe behind them.
_ABSENCE_RE = re.compile(
    r"\b(?:there are no|there is no|no such|none of|nothing (?:was|is) |"
    r"not any|zero|no results|no records|no matching|does not exist|"
    r"aren't any|isn't any|no datasheets|no tests|no equipment)\b", re.I)

# An answer that declines or asks for clarification makes no factual claim.
# The decline now names what IS in scope, so it no longer starts with the old
# fixed sentence - match the shape rather than the wording.
_NON_CLAIM_RE = re.compile(
    r"^\s*(?:i can only help|i'm not able|i am not able|could you clarify|"
    r"which .{0,40}\?$|do you mean|that(?:'s| is) outside|"
    r"(?:sorry,?\s*)?(?:but\s*)?i (?:can'?t|cannot|don'?t) (?:help|answer|provide)|"
    r"i (?:do not|don't) have (?:access to )?(?:weather|information about))", re.I)
# "Engineer ID 5", "user_id 5", "datasheet ID of 7" - a foreign key shown to a
# human. It has appeared in three separate runs despite the prompt forbidding
# it in two places, so it is caught mechanically instead.
_ID_LEAK_RE = re.compile(
    r"\b(?:engineer|user|reviewer|requester|datasheet|planner|request|equipment)"
    r"[\s_]?id\b(?:\s+of)?[:\s]*\d+", re.I)

_SCOPE_DECLINE_RE = re.compile(
    r"\b(?:outside|not part of|unrelated to)\b[^.]{0,40}\b(?:lab|emc|scope|data)\b",
    re.I)


# Shown when the answer was written without a single query behind it. The old
# wording was a dead end - "I could not answer that" tells a user nothing about
# what to ask instead, and they stop asking. Say what is actually in here.
_NO_EVIDENCE = (
    "I did not run any query for that, so I have nothing to stand behind and "
    "would rather not guess.\n\n"
    "I can answer from the lab database: test requests and jobs (product, "
    "requester, status), the schedule and who is assigned what, filled "
    "datasheets and their results and conditions, individual test observations, "
    "and the equipment inventory with calibration dates.\n\n"
    "Try naming one of those - a job number, a test code like CE or ESD, an "
    "engineer, or a piece of equipment - and I will pull the exact rows.")


def skipped():
    return {"verdict": "skipped", "answer": None, "unsupported": [], "notes": []}


# MARKDOWN IS NOW RENDERED, so it is no longer stripped.
#
# This used to flatten bold, headings and code spans, and the reason was sound at
# the time: the chat bubble set textContent, so a heading arrived as a literal
# "## " and bold as "**2**" in the middle of a sentence. Stripping was the lesser
# evil.
#
# The cost was that every answer came out as one undifferentiated block of prose.
# A lab result is a heading, a short summary and a table of rows, and asking a
# reader to find the margin at 0.72 MHz in a paragraph is asking them not to
# bother. renderAnswer in base.html now parses headings, lists, pipe tables and
# inline spans - escaping first, since this is model output - so the markdown is
# worth keeping.
#
# Only the fences go. A ```sql block is machinery the reader did not ask for, and
# strip_machinery removes its contents already; leaving the fence behind renders
# as an empty code block.
_MD_FENCE = re.compile(r"(?m)^\s*```[\w-]*\s*$")


def plain_text(text):
    """Tidy an answer for display. Markdown is kept - the UI renders it now."""
    if not text:
        return text
    return _MD_FENCE.sub("", text).strip()


_FAIL_CLAIM_RE = re.compile(r"\bfail(?:ed|ure|s|ing)?\b", re.I)
AXIS_WINDOW = 110


def _axis_crossed(draft):
    """A review-rejection reason presented as why the UNIT failed a standard.

    The two axes are the distinction this whole taxonomy exists for. A unit fails
    a standard - CE_LIMIT_EXCEEDED, SURGE_DAMAGE - and a RECORD gets sent back by
    a peer reviewer - CAL_EXPIRED, MISSING_PHOTO. Every primitive keeps them
    apart. The model's prose does not:

        "the issue that went wrong was a CE failure tied to expired calibration;
         the datasheet was held up by calibration expiry"
        "Recommended next step: recalibrate the equipment and re-run the CE test"

    Both sentences are made of true facts and the linkage is invented. The CE test
    failed because emissions exceeded the limit line; the calibration finding is
    why the paperwork came back. An engineer following that advice recalibrates a
    rig to fix an emissions problem.

    Nothing caught it, because grounding checks NUMBERS and there was no wrong
    number - which is why this is a window search around a review_rejection code
    rather than a rule about figures. REASON_FAMILIES says which axis a code is
    on, so the check is schema-derived: add a code to emc_reason_code and it is
    covered without editing anything here.

    Returns the offending code, or None.
    """
    try:
        from .schema_catalog import REASON_FAMILIES
    except Exception:  # noqa: BLE001 - an older catalog simply skips the check
        return None
    low = draft.lower()
    for code, pair in (REASON_FAMILIES or {}).items():
        family = (pair[0] if isinstance(pair, (list, tuple)) else "") or ""
        if family != "review_rejection":
            continue
        label = (pair[1] if isinstance(pair, (list, tuple)) and len(pair) > 1
                 else "") or ""
        for needle in (code.lower(), label.lower()):
            if not needle:
                continue
            start = 0
            while True:
                at = low.find(needle, start)
                if at < 0:
                    break
                if _FAIL_CLAIM_RE.search(low[max(0, at - AXIS_WINDOW):at]):
                    return code
                start = at + 1
    return None


def _instrumentation_numbers(ledger):
    """Numbers WE put into the SQL, which are not facts about the lab.

    sql_guard appends LIMIT MAX_ROWS to every query it lets through, so a model
    that mentions how many rows it looked at is quoting our own row cap back at
    us. Nothing in ledger.values() covers it - that set is built from result
    CELLS - so 200 came out as an unverified claim.

    Asked "how can we make that Test accepted again", the answer was withheld
    and replaced with a raw table dump over two numbers, and one of them was
    this: LIMIT 200, appended by the guard, echoed by the model, flagged as
    invented.
    """
    out = set()
    for e in ledger.entries:
        for m in re.finditer(r"\bLIMIT\s+(\d+)", e.get("sql") or "", re.I):
            out.add(m.group(1))
    return out


def check(question, draft, ledger, model=None, kind="data", undefined=()):
    """Verify a draft answer against the ledger.

    Returns {"verdict", "answer", "unsupported", "notes"} where verdict is one
    of grounded / repaired / unsupported / no-evidence / clarify / skipped, and
    "answer" is the text that should actually be shown.
    """
    draft = (draft or "").strip()
    notes = []

    if not draft:
        return {"verdict": "no-evidence", "unsupported": [], "notes": ["empty draft"],
                "answer": "I could not produce an answer for that."}

    # A refusal or a clarifying question asserts nothing; nothing to ground.
    #
    # But "ends with a question mark" is not the same as "asserts nothing", and
    # treating it that way opened the widest hole in this module. Models end
    # answers with a courtesy follow-up constantly - the live pipeline produced
    # "...5 draft datasheets out of 12 total. ... Would you like details on any
    # of these?" unprompted - and any such answer under 300 characters skipped
    # grounding completely. Measured against a ledger holding 5:
    #
    #   "There are 47 datasheets in draft."                  -> repaired to 5
    #   "There are 47 datasheets in draft. Want details?"    -> shown as 47
    #
    # The only difference was politeness. So a draft carrying figures is an
    # answer no matter how it ends, and goes through grounding like any other.
    asks_only = (draft.endswith("?") and len(draft) < 300
                 and not _claim_tokens(draft))
    if _NON_CLAIM_RE.search(draft) or _SCOPE_DECLINE_RE.search(draft) or asks_only:
        return {"verdict": "clarify", "answer": draft, "unsupported": [], "notes": []}

    # An answer built on no evidence at all is the highest-risk case: the model
    # never touched the database and is answering from its own head.
    if not ledger.entries and not ledger.notes:
        if _looks_factual(draft):
            return {"verdict": "no-evidence", "unsupported": [], "answer": _NO_EVIDENCE,
                    "notes": ["factual claims with an empty ledger"]}
        return {"verdict": "no-evidence", "answer": draft, "unsupported": [], "notes": []}

    # A primary key is not evidence for a quantity. "10 tests assigned" was
    # passed as grounded because planner_entries.id 10 came back in a result
    # set; see Ledger.id_only_numbers for the full account. Subtracted BEFORE
    # the union so a figure that also appears as a genuine value survives.
    supported = ((ledger.values() - ledger.id_only_numbers())
                 | _question_tokens(question)
                 | _row_counts(ledger) | _temporal_tokens()
                 | _instrumentation_numbers(ledger))
    flagged = [tok for tok in _claim_tokens(draft) if tok.lower() not in supported]

    # On a schema question the claim IS the identifier. "The value is in
    # custom_spec" contains no number to check, so the numeric pass sees a
    # clean answer while the one thing that matters is invented. Every
    # table/column an answer names must have come back from find_field.
    if kind == "schema":
        flagged += [t for t in _identifier_tokens(draft)
                    if not _identifier_supported(t, supported)]

    # "none" claims need a probe behind them, whatever the numbers say.
    absence = bool(_ABSENCE_RE.search(draft))
    unprobed_absence = absence and not ledger.probed_values_for()
    if unprobed_absence:
        notes.append("asserts absence without a value-existence check")

    # A raw foreign key presented as if it identified a person or a thing.
    id_leak = _ID_LEAK_RE.search(draft)
    if id_leak:
        notes.append("names a raw id (%s) instead of the person or job"
                     % id_leak.group(0).strip())
        # ONE fault, reported once. The id was also sitting in `flagged`,
        # because id_only_numbers() deliberately keeps id cells out of the
        # supported set - right, so that an id=75 cell cannot prop up "75
        # datasheets", and wrong here, where the model is naming the row it
        # looked up. Counting it twice turned a cosmetic slip into an
        # unverifiable claim and cost the user a true answer.
        leaked = set(re.findall(r"\d+", id_leak.group(0)))
        flagged = [t for t in flagged if t not in leaked]

    # A judgement word the schema does not define, used to produce a number
    # without saying what rule produced it.
    undisclosed = _undisclosed_rule(draft, undefined)
    if undisclosed:
        notes.append("reports a figure for '%s' without stating the rule used"
                     % undisclosed)

    # Answering inside a vocabulary the data does not have.
    phantom = _phantom_value(draft, ledger)
    if phantom:
        notes.append("answers about '%s', which is not a value in the data" % phantom)

    # Omission is the failure the grounding check cannot see: an answer that
    # lists five of the ten rows it was handed says nothing false. Advisory
    # only - the adjudicator decides, because summarising is often correct.
    missed = _coverage_gap(question, draft, ledger)
    if missed:
        notes.append("answer covers %d of %d listed items (%s)"
                     % (missed["cited"], missed["total"], missed["missing"]))

    # A counting question is always adjudicated, even when every number in the
    # answer appears somewhere in the evidence. "Somewhere" is the loophole: a
    # figure from one query attaches perfectly well to a claim about another,
    # and value-matching cannot tell the difference. The adjudicator can,
    # because it now sees the SQL each figure came from.
    counting = bool(_COUNT_QUESTION_RE.search(question or "")) and _claim_tokens(draft)

    # A figure the semantic layer computed needs no adjudication. Its SQL was
    # written and reviewed by a person, executed by us, and recorded before the
    # model saw it - there is nothing for a language model to second-guess, and
    # when it tried it threw away correct answers ("6 overdue tests" rejected
    # against a ledger entry containing exactly 6).
    if counting and _all_from_semantics(_claim_tokens(draft), ledger):
        counting = False
        notes.append("figures come from reviewed metric definitions")

    # The two axes crossed in prose. Checked here rather than after adjudication
    # because there is no number involved - Pass 1 would have returned "grounded"
    # on an answer telling an engineer to recalibrate a rig to fix an emissions
    # failure, and it did.
    crossed = _axis_crossed(draft)
    if crossed:
        notes.append("presents %s, a peer-review finding, as why the unit failed "
                     "a standard" % crossed)

    # Did the SQL implement the PLAN, or answer a nearby question with real
    # numbers? Value-matching cannot see this, which is the whole reason the
    # plan is written down before any SQL exists. Both are advisory signals
    # into adjudication, not verdicts of their own.
    mismatch = _plan_mismatch(ledger, draft)
    if mismatch:
        notes.append(mismatch)

    contamination = _scope_breach(ledger)
    if contamination:
        notes.append(contamination)

    if (not flagged and not unprobed_absence and not missed and not phantom
            and not counting and not undisclosed and not id_leak and not crossed
            and not mismatch and not contamination):
        return {"verdict": "grounded", "answer": draft, "unsupported": [], "notes": notes}

    # Pass 2: let a model adjudicate the flags against the evidence.
    verdicts = _adjudicate(question, draft, ledger, flagged, unprobed_absence,
                           missed, phantom, counting=counting,
                           undisclosed=undisclosed, model=model)
    if verdicts is None:                      # adjudicator unavailable
        # Grounding used to weaken here exactly when the service was under
        # stress: a rate limit or an outage made every flagged figure pass
        # through unrepaired, with only a note withdrawing the "verified" badge.
        # The answer was still shown, wrong numbers and all.
        #
        # Arithmetic does not need a language model. Most surviving flags are
        # legitimately DERIVED - "42%" from 5 and 12, "7 more than last month" -
        # and that is checkable by trying the combinations. What cannot be
        # derived from anything we measured is not a rounding of the evidence,
        # it is a figure from nowhere, and the honest response is the one the
        # adjudicated path already uses: withhold the prose, show the rows.
        #
        # Deliberately monotonic. A coincidental arithmetic match marks a figure
        # supported and shows it, which is exactly what happened before this
        # existed - so the fallback can only match today's behaviour or improve
        # on it, never do worse.
        pool = _numeric_pool(ledger)
        still_bad = [t for t in flagged if not _derivable(t, pool)]
        derived = [t for t in flagged if t not in still_bad]
        if derived:
            notes.append("adjudicator unreachable; %d figure(s) checked "
                         "arithmetically against the evidence" % len(derived))
        if not still_bad and not phantom:
            notes.append("checked without the adjudicator")
            return {"verdict": "grounded", "answer": draft,
                    "unsupported": [], "notes": notes}
        notes.append("adjudicator unreachable; %d figure(s) could not be "
                     "derived from anything measured" % len(still_bad))
        return {"verdict": "unsupported", "unsupported": still_bad, "notes": notes,
                "answer": _cannot_verify(still_bad, ledger)}

    bad = verdicts.get("unsupported") or []
    incomplete = bool(verdicts.get("incomplete"))
    causal = _causal_overreach(question, draft)
    if (not bad and not verdicts.get("absence_unsupported")
            and not incomplete and not phantom and not undisclosed and not id_leak
            and not causal and not crossed):
        return {"verdict": "grounded", "answer": draft, "unsupported": [], "notes": notes}

    repaired = _repair(question, draft, ledger, bad, incomplete=incomplete,
                       phantom=phantom, undisclosed=undisclosed,
                       id_leak=id_leak.group(0) if id_leak else None,
                       causal=causal, crossed=crossed, model=model)
    if repaired:
        notes.append("rewrote the answer without %d unsupported claim(s)" % len(bad))
        return {"verdict": "repaired", "answer": repaired, "unsupported": bad,
                "notes": notes}

    # Repair failed. Before dumping rows, ask what the fault actually was: a
    # leaked surrogate id is a PRESENTATION problem - "datasheet id 75" should
    # have read "the harmonic test on IEC-EMC-004" - and everything else in the
    # answer may be perfectly true. Withholding over it trades a whole correct
    # answer for a cosmetic complaint, and that is what happened: asked how to
    # get a test accepted again, the user got two tables of raw rows instead of
    # the answer, because the draft named a row id.
    #
    # The charter for this module is that it may withhold a true answer but
    # never manufacture a false one. That trade is worth making when TRUTH is at
    # stake. It is not worth making over a formatting slip, so when nothing
    # factual is in doubt the answer goes out with the note attached.
    if not bad and id_leak and not (incomplete or phantom or undisclosed or causal):
        notes.append("shown despite naming a raw id: nothing factual was in doubt")
        return {"verdict": "grounded", "answer": draft, "unsupported": [],
                "notes": notes}

    return {"verdict": "unsupported", "unsupported": bad, "notes": notes,
            "answer": _cannot_verify(bad, ledger)}


def _cannot_verify(bad, ledger):
    """The last resort: withhold the PROSE, show the DATA.

    The previous version answered with an apology and a row count, which threw
    away work that had already been done - one run fetched 102 rows across
    three queries and displayed nothing but "I cannot stand behind this". The
    figure the model derived may be unverifiable, but the rows behind it are
    exactly as trustworthy as they were a moment ago, and a lab admin can read
    a table perfectly well.

    So: drop the sentence that could not be checked, keep everything the
    database actually returned, and say plainly which part was dropped.
    """
    rows_shown = _render_evidence(ledger)
    if not rows_shown:
        s = ledger.summary()
        return ("I could not verify that answer against the data, and I have no "
                "rows to show you either - %d quer%s returned nothing usable. "
                "Try naming a specific job, test or engineer."
                % (s["queries"], "y" if s["queries"] == 1 else "ies"))
    head = ("I could not verify one part of my answer%s, so here is what the "
            "database actually returned instead - these rows are exact:"
            % (" (%s)" % ", ".join(str(b) for b in bad[:4]) if bad else ""))
    return "%s\n\n%s\n\nAsk me about any single line above and I will go deeper." % (
        head, rows_shown)


def _render_evidence(ledger, max_queries=6, max_rows=12):
    """The ledger as something a person can read."""
    blocks = []
    for e in ledger.entries[:max_queries]:
        if e["error"] or not e["rows"]:
            continue
        cols = [str(c) for c in e["columns"]]
        widths = [len(c) for c in cols]
        body = []
        for row in e["rows"][:max_rows]:
            cells = ["" if c is None else str(c)[:38] for c in row]
            widths = [max(w, len(c)) for w, c in zip(widths, cells)]
            body.append(cells)
        if not body:
            continue
        line = "  ".join(c.ljust(w) for c, w in zip(cols, widths))
        out = [line, "  ".join("-" * w for w in widths)]
        out += ["  ".join(c.ljust(w) for c, w in zip(cells, widths)) for cells in body]
        if e["row_count"] > max_rows:
            out.append("... and %d more row(s)" % (e["row_count"] - max_rows))
        blocks.append("\n".join(out))
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------
# deterministic pass
# --------------------------------------------------------------------------

def _claim_tokens(text):
    """The numbers and dates in an answer - the parts someone would act on."""
    out = []
    for m in _DATE_RE.finditer(text):
        out.append(m.group(1))
    stripped = _DATE_RE.sub(" ", text)
    for m in _NUMBER_RE.finditer(stripped):
        tok = m.group(1)
        bare = tok.replace(",", "")
        if bare in _TRIVIAL:
            continue
        out.append(bare)
    seen, uniq = set(), []
    for t in out:
        if t.lower() not in seen:
            seen.add(t.lower())
            uniq.append(t)
    return uniq


# snake_case names and dotted table.column references - what a schema answer
# is actually made of.
_IDENT_RE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+(?:\.[a-z][a-z0-9_]+)?)\b")
# words that look like identifiers but are ours, not the schema's
_IDENT_IGNORE = {"test_code", "find_field", "resolve_entity", "list_values",
                 "run_sql", "table_column", "so_far"}


def _identifier_tokens(text):
    """The table/column names an answer claims exist."""
    out, seen = [], set()
    for m in _IDENT_RE.finditer(text or ""):
        tok = m.group(1)
        if tok in _IDENT_IGNORE or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _identifier_supported(token, supported):
    """Is this table.column in the evidence?

    find_field returns the table and the column as separate JSON fields, so the
    dotted form the answer writes them in never appears verbatim. Accept it
    when both halves are there - otherwise a perfectly correct
    "iec_emc_requests.rejection_reason" gets rejected and rewritten into a
    wrong "there is no such field", which is how a guard makes things worse.
    """
    low = token.lower()
    if low in supported:
        return True
    parts = [p for p in low.split(".") if p]
    return len(parts) > 1 and all(p in supported for p in parts)


def _question_tokens(question):
    """Numbers the user themselves supplied - echoing those is not a claim."""
    vals = set()
    for m in _NUMBER_RE.finditer(question or ""):
        vals.add(m.group(1).replace(",", "").lower())
    for m in _DATE_RE.finditer(question or ""):
        vals.add(m.group(1).lower())
    for word in re.findall(r"[A-Za-z][\w./-]{2,}", question or ""):
        vals.add(word.lower())
    return vals


def _all_from_semantics(tokens, ledger):
    """Every figure in the answer came out of a reviewed metric."""
    if not tokens:
        return False
    trusted = set()
    for e in ledger.entries:
        if e["worker"] != "semantics":
            continue
        for row in e["rows"]:
            for cell in row:
                trusted.add(str(cell).strip().lower())
    return bool(trusted) and all(t.lower() in trusted for t in tokens)


def _temporal_tokens():
    """Dates the assistant may state without them being a claim about the data.

    An answer that says "no tests failed in July 2026" is reporting the window
    it looked at, not asserting a value from a row. Without this the current
    year gets flagged on almost every date-scoped question and triggers a
    pointless rewrite.
    """
    import datetime
    today = datetime.date.today()
    out = {str(today.year), str(today.year - 1), str(today.year + 1),
           today.isoformat(), str(today.month), str(today.day)}
    first = today.replace(day=1)
    prev_last = first - datetime.timedelta(days=1)
    out |= {prev_last.isoformat(), first.isoformat(),
            str(prev_last.month), str(prev_last.year)}
    return out


# "none are marked as Rejected" - the answer denies a specific label. If that
# label is nowhere in the evidence, the answer is reasoning inside a vocabulary
# the database does not have, which quietly confirms the user's wrong premise
# instead of correcting it. Deterministic, because asking the model nicely to
# name the real values did not survive contact with real questions.
_DENIED_LABEL_RE = re.compile(
    r"\b(?:no|none|not|aren't|isn't|neither)\b[^.;\n]{0,60}?"
    r"\b(?:marked|flagged|labell?ed|listed|recorded|set)\s+(?:as\s+)?"
    r"[\"']?([A-Za-z][\w -]{2,30}?)[\"']?(?=[\s.,;:)]|$)", re.I)

_COUNT_QUESTION_RE = re.compile(
    r"\bhow many\b|\bcount\b|\bnumber of\b|\btotal\b|\bhow much\b", re.I)

_LIST_MIN_ROWS = 4          # below this, "summarised" and "listed" look the same
_LIST_QUESTION_RE = re.compile(
    r"\b(which|list|what .{0,20}s\b|name the|all of|every|each|show me)\b", re.I)


# Phrases that show the answer OWNED its definition rather than hiding it.
_RULE_STATED_RE = re.compile(
    r"\b(?:taking|treating|counting|defining|by which i mean|i am using|"
    r"interpreting|on the basis|assuming|where\s+\w+\s+means|i have taken|"
    r"if .{0,30}\bmeans\b|there is no .{0,30}\bdefinition\b|"
    r"the (?:database|schema|system) does not (?:define|record|track))\b", re.I)


def _undisclosed_rule(draft, undefined):
    """A number reported for an undefined term, with no rule given.

    "There are 16 overdue tests" is the shape to catch: arithmetically real,
    definitionally invented, and indistinguishable from a fact. Stating the
    rule - "taking overdue as a planned end date in the past" - makes it
    something the reader can argue with, which is all that is being asked.
    """
    if not undefined or not _claim_tokens(draft):
        return None
    if _RULE_STATED_RE.search(draft):
        return None
    low = draft.lower()
    for term in undefined:
        if term in low:
            return term
    return None


def _phantom_value(draft, ledger):
    """The label an answer denies, when that label is not in the data at all.

    Returns the offending word, or None. Requires that some probe actually
    listed a column's values - without that we do not know the vocabulary and
    have no business second-guessing the answer.
    """
    if not ledger.probed_values_for():
        return None
    known = ledger.values()
    low = draft.lower()
    for m in _DENIED_LABEL_RE.finditer(draft):
        label = m.group(1).strip().strip("\"'").lower()
        if not label or label in known:
            continue
        # already disclosed as non-existent? then the answer is doing the right
        # thing and needs no repair
        if any(p in low for p in ("no such", "does not exist", "there is no %s" % label,
                                  "not a valid", "no status called")):
            return None
        return m.group(1).strip()
    return None


def _coverage_gap(question, draft, ledger):
    """Did the answer quietly drop rows it was given?

    Looks at the identifying (first) column of each multi-row result and counts
    how many of its values reach the answer. Advisory: a GROUP BY the answer
    correctly totals would trip this too, which is why the adjudicator gets the
    last word.

    Only runs on questions that actually asked for a list. Otherwise every
    supporting query the workers ran along the way - the standards on a
    request, the values in a column - looks like an unanswered enumeration and
    triggers a rewrite nobody needed.
    """
    if not _LIST_QUESTION_RE.search(question or ""):
        return None
    low = draft.lower()
    worst = None
    for e in ledger.entries:
        if e["error"] or e["row_count"] < _LIST_MIN_ROWS or not e["columns"]:
            continue
        keys, seen = [], set()
        for row in e["rows"]:
            v = "" if not row or row[0] is None else str(row[0]).strip()
            if v and len(v) > 1 and v.lower() not in seen:
                seen.add(v.lower())
                keys.append(v)
        if len(keys) < _LIST_MIN_ROWS:
            continue
        missing = [k for k in keys if k.lower() not in low]
        if not missing:
            continue
        gap = {"cited": len(keys) - len(missing), "total": len(keys),
               "missing": ", ".join(missing[:8])}
        if worst is None or len(missing) > worst["total"] - worst["cited"]:
            worst = gap
    return worst


def _numeric_pool(ledger, cap=400):
    """Every number the database actually handed us, plus the row counts.

    Capped because the combination search below is quadratic and a 200-row
    result would otherwise make it the slowest thing in the request.
    """
    pool = set()
    for e in ledger.entries:
        pool.add(float(e["row_count"]))
        for row in e["rows"]:
            for v in row:
                if isinstance(v, bool) or v is None:
                    continue
                try:
                    pool.add(float(v))
                except (TypeError, ValueError):
                    continue
                if len(pool) >= cap:
                    return pool
    return pool


def _derivable(token, pool, tol=0.05):
    """Could this figure have come out of the numbers we measured?

    Not a proof of correctness - a proof that the figure is ARITHMETIC on the
    evidence rather than invented. A percentage of two measured counts, a sum, a
    difference: all legitimate things for an answer to state, none of which
    appear literally in any cell, and all of which the token check flags.
    Without this the fallback would withhold correct answers for saying "42%".
    """
    try:
        n = float(str(token).replace(",", "").rstrip("%"))
    except (TypeError, ValueError):
        return False
    if not pool:
        return False
    for a in pool:
        if abs(n - a) <= tol:
            return True
        for b in pool:
            if abs(n - (a + b)) <= tol or abs(n - (a - b)) <= tol:
                return True
            if b and abs(n - (100.0 * a / b)) <= tol:
                return True
            if b and abs(n - (a / b)) <= tol:
                return True
    return False


def _row_counts(ledger):
    """A model may legitimately count the rows it was handed: a query that
    returned 7 rows supports "7 tests" even though no cell contains a 7."""
    out = set()
    for e in ledger.entries:
        out.add(str(e["row_count"]))
    return out


def _looks_factual(text):
    """Does this answer assert anything checkable at all?

    "No tests failed last month" carries no number and is short, but it is a
    claim about the data as surely as a count is - and one made with an empty
    ledger is a claim about data nobody looked at.
    """
    return (bool(_claim_tokens(text)) or len(text) > 200
            or bool(_ABSENCE_RE.search(text)))


# --------------------------------------------------------------------------
# adjudication + repair
# --------------------------------------------------------------------------

_ADJUDICATE = """You are a fact-checker. You are given the QUESTION a user asked, the EVIDENCE a
database returned (each result shown with the SQL that produced it), and a
DRAFT ANSWER written from that evidence.

Decide, for each figure and claim in the draft, whether the evidence genuinely
supports it AS AN ANSWER TO THIS QUESTION. A number is NOT supported merely
because it appears somewhere in the evidence - check the SQL that produced it
and confirm it is measuring the thing the draft says it measures. A count of
requested tests is not a count of datasheets, however similar the numbers look.

A figure IS supported if it appears in a result whose SQL matches the claim, or
follows from one by counting rows or simple arithmetic.

QUESTION
{question}

{absence_clause}
{coverage_clause}

Reply with JSON only:
{{"unsupported": ["<token>", ...], "absence_unsupported": true|false,
  "incomplete": true|false, "reason": "<one short sentence>"}}

Be conservative. List a token in "unsupported" ONLY if you can name the
mismatch - the evidence does not contain it at all, or the SQL that produced it
was measuring something else. If the evidence plainly supports it, or you are
unsure, leave it out. A wrongly flagged figure costs the user a correct answer.

Do not add commentary, do not rewrite anything.

EVIDENCE
{evidence}

DRAFT ANSWER
{draft}

FLAGGED TOKENS
{flagged}
"""

_ABSENCE_CLAUSE = ("The draft also states that something does not exist / that there "
                   "are none. Set \"absence_unsupported\": true unless the evidence "
                   "shows the relevant column or entity was actually checked (a value "
                   "listing, an entity lookup, or a query whose filter is clearly on a "
                   "value present in the evidence). A query returning zero rows is NOT "
                   "on its own evidence of absence.")

_PHANTOM_CLAUSE = (
    "The draft denies that anything is marked '{label}', but '{label}' does not "
    "appear anywhere in the evidence. If the evidence shows what values that "
    "field really takes, the draft is answering inside a category the data does "
    "not have. Set \"incomplete\": true so it gets rewritten to name the real "
    "values instead.")

_COVERAGE_CLAUSE = ("The evidence lists {total} items but only {cited} appear in the "
                    "draft; missing: {missing}. Set \"incomplete\": true if the "
                    "question asked for these items and the draft simply omits some "
                    "of them. Set it FALSE if the draft legitimately aggregates or "
                    "summarises them, or says how many it is showing.")

_REPAIR = """Rewrite the answer to this question using ONLY the evidence below.

Remove or correct these unsupported claims: {bad}

Rules:
- Every figure, name, date and status must appear in the evidence.
- Do not add anything the evidence does not show. Do not soften an unsupported
  number into a vaguer one - drop it.
- If removing them leaves the question unanswered, say plainly what the data
  does show and what is missing.
- Same tone as the original: lead with the direct answer, then one short
  supporting line. Plain text.

QUESTION
{question}

EVIDENCE
{evidence}

ORIGINAL ANSWER (contains unsupported claims)
{draft}
"""


def _model_name(model=None):
    # Never falls back to NLP_SEARCH_MODEL - that made the judge the generator.
    return model or os.environ.get(VERIFIER_MODEL_ENV) or DEFAULT_VERIFIER_MODEL


def _complete(prompt, model=None, max_tokens=700):
    """One plain completion. Returns text, or None if the model is unreachable."""
    try:
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model=_model_name(model),
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=max_tokens)
        return (resp.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001 - verification must never break the answer
        return None


def _adjudicate(question, draft, ledger, flagged, absence, missed, phantom=None,
                counting=False, undisclosed=None, model=None):
    """{"unsupported": [...], "absence_unsupported": bool, "incomplete": bool}
    or None when the adjudicator could not be reached."""
    if (not flagged and not absence and not missed and not phantom
            and not counting and not undisclosed):
        return {"unsupported": [], "absence_unsupported": False, "incomplete": False}
    text = _complete(_ADJUDICATE.format(
        question=(question or "")[:500],
        evidence=ledger.evidence_digest(max_rows_per_query=25)[:9000],
        draft=draft[:3000],
        flagged=", ".join(flagged) or "(none - check the figures below anyway)",
        absence_clause=_ABSENCE_CLAUSE if absence else "",
        coverage_clause=((_COVERAGE_CLAUSE.format(**missed) if missed else "")
                         + ("\n" + _PHANTOM_CLAUSE.format(label=phantom)
                            if phantom else ""))),
        model=model, max_tokens=400)
    if not text:
        return None
    try:
        blob = re.search(r"\{.*\}", text, re.S)
        data = json.loads(blob.group(0) if blob else text)
        return {"unsupported": [str(t) for t in (data.get("unsupported") or [])],
                "absence_unsupported": bool(data.get("absence_unsupported")),
                "incomplete": bool(data.get("incomplete")),
                "reason": str(data.get("reason") or "")[:300]}
    except Exception:  # noqa: BLE001 - unparseable verdict = no verdict
        return None


# An answer that asserts a cause this database does not record.
#
# Nothing in the schema holds a diagnosis - no engineer types one in - so any
# confirmed cause is the model's inference wearing the costume of a record. It
# was told not to in the standing prompt, then in the tool output beside the
# rows, then in a directive injected for that one question. All three lost to
# the question's own framing: asked for "the confirmed root cause", it answered
# "the confirmed root cause is CE_LIMIT_EXCEEDED" - which is also circular, the
# code being the name of the failure rather than a reason for it.
#
# So it is checked here instead, after the answer exists, where an instruction
# cannot be crowded out by four thousand tokens of catalog.
_CAUSE_ASSERT_RE = re.compile(
    r"(?i)\b(?:the\s+)?(?:confirmed|actual|underlying|true|identified)\s+"
    r"(?:root\s+)?(?:cause|reason)\b[^.\n]{0,60}?\b(?:is|was|were)\b"
    r"|\broot cause\b[^.\n]{0,40}?\b(?:is|was)\b"
    r"|\bwas caused by\b|\bis caused by\b|\bthe cause of\b[^.\n]{0,40}\b(?:is|was)\b")


def _causal_overreach(question, draft):
    """The asserted-cause sentence, when the question asked for one and got one."""
    from . import intent
    if not intent.asks_for_cause(question):
        return None
    m = _CAUSE_ASSERT_RE.search(draft or "")
    return m.group(0).strip() if m else None


def _repair(question, draft, ledger, bad, incomplete=False, phantom=None,
            undisclosed=None, id_leak=None, causal=None, crossed=None,
            model=None):
    """A rewrite constrained to the evidence, or None."""
    extra_axis = ""
    if crossed:
        try:
            from .schema_catalog import REASON_FAMILIES
            label = (REASON_FAMILIES.get(crossed) or ("", ""))[1]
        except Exception:  # noqa: BLE001
            label = ""
        extra_axis = (
            "\n- The original treats %s%s as a reason the UNIT failed its test. "
            "It is not. It is a PEER REVIEW finding: why the record was sent back "
            "to the engineer, on a completely separate axis from whether the "
            "hardware met the standard. Rewrite so the two are separate "
            "sentences and neither explains the other - say what the unit did "
            "against the standard, then say separately what the reviewer asked "
            "to be corrected. Remove any recommendation that treats the review "
            "finding as a fix for the test result."
            % (crossed, " (%s)" % label if label else ""))
    extra = ("\n- The original omitted items the evidence lists. Include every one "
             "of them, or state the total and say how many you are showing."
             if incomplete else "") + extra_axis
    if phantom:
        extra += ("\n- The original says nothing is marked '%s', but '%s' is not a "
                  "value this data has. Do not answer inside that category. Say "
                  "plainly that there is no such value, name the values the field "
                  "actually takes (they are in the evidence), and then answer the "
                  "question in those terms." % (phantom, phantom))
    if undisclosed:
        extra += ("\n- The original gives a figure for '%s', which this database does "
                  "not define anywhere. Keep the figure, but say in the same sentence "
                  "exactly what rule produced it - which columns, which condition - so "
                  "the reader can disagree with the rule instead of trusting a number "
                  "built on a hidden assumption." % undisclosed)
    if causal:
        extra += ("\n- The original says '%s'. This database records no causes - "
                  "measurements, fitted parts, reviewer comments and dates, but "
                  "nobody enters a diagnosis - so a confirmed cause cannot come "
                  "from it. Remove that claim. Open by saying the data does not "
                  "identify a cause, then give the sequence that bears on it: "
                  "what the measurement did across attempts, what was changed "
                  "between them, what the reviewer wrote. Keep every number. "
                  "Naming the failure code is NOT a cause - 'the cause was "
                  "CE_LIMIT_EXCEEDED' says the reason it failed was that it "
                  "failed." % causal)
    if id_leak:
        extra += ("\n- The original says '%s'. A database id means nothing to a "
                  "reader. Replace it with the name - the evidence carries "
                  "username / test_person_name / job_number / equipment name. If "
                  "the name is genuinely not in the evidence, drop the claim "
                  "rather than printing the number." % id_leak)
    out = _complete(_REPAIR.format(
        question=question[:1000],
        evidence=ledger.evidence_digest(max_rows_per_query=40)[:9000],
        draft=draft[:3000], bad=", ".join(bad) or "(none)") + extra, model=model,
        max_tokens=1200)
    if not out:
        return None
    # The repair must not smuggle a bad FIGURE back in. Only figures: a word
    # like "approved" or a phantom label the rewrite was asked to name will of
    # course reappear, and rejecting the repair for that withholds a corrected
    # answer in favour of nothing at all.
    ph = (phantom or "").lower()
    still = [b for b in bad
             if b.lower() != ph and _NUMBER_RE.fullmatch(b.replace(",", ""))
             and b.lower() in out.lower()]
    return None if still else out


# --------------------------------------------------------------------------
# Machinery does not belong in a user's answer
# --------------------------------------------------------------------------
# The prompt asks for this - "never show tool names, measures, tables or a SQL
# statement, the interface shows those separately" - and asking did not work.
# The run immediately after that instruction was added answered:
#
#   65 instruments are overdue for maintenance. SQL shape used: SELECT COUNT(*)
#   FROM maintenance WHERE maintenance_due_date < CURDATE();
#
# The statement is also WRONG - the figure came from `equipment`, not
# `maintenance` - so the model was inventing SQL to display, incorrectly, to
# somebody who did not ask for it. Earlier runs printed
# "Source: maintenance_overdue with include_rows=False" and
# "Total equipment_history rows: missing".
#
# Same lesson as the caveats: a prompt is a request, code is a control. The
# real SQL is already shown in its own panel, straight from the ledger, so
# nothing is lost by cutting the model's rendition of it.
_SQL_STMT_RE = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE)\b[\s\S]*?(?:;|(?=\n\s*\n)|$)", re.I)
# "SQL shape used:", "Source: maintenance_overdue", "SQL behind the figure:".
# Anchored to a line start OR a sentence end, because these turn up mid-sentence
# too - "...overdue for maintenance. SQL shape used: SELECT..." - and a
# start-of-line anchor left the label stranded once the statement was cut.
# Deliberately NOT matching "Note:", which is how the reader's caveat arrives.
_MACHINERY_LABEL_RE = re.compile(
    r"(?im)(?:^|(?<=[.!?])[ \t])[ \t>*-]*"
    r"(?:sql[^\n:]{0,24}|source|query|statement|tool|route)[ \t]*:[^\n]*")

# The scaffolding the insight primitives print above their own output, matched
# LITERALLY. An earlier version keyed on "counts", "analysis", "method" and
# "approach" with the colon optional, which are ordinary lab words in ordinary
# positions: it reduced "Method: IEC 61000-4-6 was applied for the conducted
# immunity test" to nothing at all, and would have done the same to any answer
# opening "Analysis:". A stripper that eats real content is worse than the leak
# it was added for.
_INSIGHT_LABEL_RE = re.compile(
    r"(?im)^[ \t>*-]*(?:"
    r"in words\b[^\n]*"
    r"|counts?\b[ \t]*[-–:][ \t]*use these[^\n]*"
    r"|analy[sz]ed with\b[^\n]*"
    r"|what i did\b[ \t]*:?[ \t]*$"
    r"|evidence,? not cause\b[^\n]*"
    r"|that is the complete list\b[^\n]*"
    r")")

# Same machinery, written as prose instead of a labelled line.
_PROCESS_PROSE_RE = re.compile(
    r"(?im)^[ \t>*-]*(?:"
    r"(?:ran|called|used|invoked|queried)\s+analy[sz]e_history\b"
    r"|the original (?:answer|draft)\b"
    r"|(?:the )?(?:evidence|rows) (?:shows?|indicates?) a total of"
    r")[^\n]*")

# "There are 378 rows of data, but only the results from that date are shown."
# The size of the evidence is not a fact about the lab, and a reader has no way
# to tell the two apart.
_ROWCOUNT_PROSE_RE = re.compile(
    r"(?i)[-*\s]*\.?\s*(?:there (?:are|were)|it (?:has|had)|"
    r"the (?:evidence|data) (?:has|have|contains?|includes?|shows?))"
    r"\s+[\d,]+\s+rows?\s+of\s+(?:data|evidence)"
    r"[^.\n]*\.?")
# lab_metric(name='x', include_rows=False) and the bare keyword form.
# analyse_history joined the list as soon as the primitives went in: answers
# were signing off with the literal call they had made, arguments and all.
_TOOL_CALL_RE = re.compile(
    r"\b(?:lab_metric|run_sql|read_grid|list_values|resolve_entity|find_field|"
    r"describe_table|sample_rows|profile_column|analyse_history|ask_\w+)"
    r"\s*\([^)]*\)")
_KWARG_RE = re.compile(r"\b(?:include_rows|max_rows|name)\s*=\s*[^\s,.;)]+")
# "Total equipment_history rows: missing" - a count the model could not get,
# reported as a field rather than dropped.
#
# Matches the CLAUSE, not the line. The first version began `^.*` and deleted a
# whole correct answer along with the stray clause at the end of it - exactly
# the failure mode this function is supposed to prevent, committed by the
# function itself.
_MISSING_FIELD_RE = re.compile(
    r"(?im)(?:^|(?<=[.!?;])[ \t])[ \t>*-]*(?:total[ \t]+)?"
    r"\w+(?:[ \t]+\w+){0,2}[ \t]*:[ \t]*(?:missing|unknown|not available|n/?a)[ \t]*\.?")

# The scope filter, narrated. Arrived with the real/demo policy: told that any
# query touching the request table must carry is_synthetic, the worker started
# explaining that it had - "10 EMC test requests in the system. Filtering on
# r.is_synthetic = 0 to exclude synthetic data." The filter is correct and
# mandatory and the user does not need to hear about it; what they are owed is
# the plain-language scope caveat, which attach_caveats adds separately.
#
# Stripped rather than prompted away because it is a predictable consequence
# of an instruction the worker cannot be allowed to ignore - the more firmly
# the guard insists on the filter, the more the model wants credit for it.
# The tail is bounded to scope vocabulary rather than "everything up to the
# full stop". An earlier version used [^.\n]* and deleted the answer: given
# "Filtering on r.is_synthetic = 0, there are 10 requests." it swallowed the
# clause carrying the figure and returned an empty string. Over-deletion in
# this module is worse than a leak - the leak is untidy, the deletion is a
# blank reply to a question that was answered correctly.
_SCOPE_TAIL = (r"(?:\s*(?:[=,]|\b(?:\d+|to|for|and|the|only|out|of|"
               r"non-?synthetic|synthetic|demo|real|rows?|records?|entries|"
               r"data|dataset|exclude[sd]?|excluding|include[sd]?|including|"
               r"filter(?:ed)?)\b))*")

_SCOPE_PROSE_RE = re.compile(
    r"(?:(?:^|(?<=[.\n]))\s*"
    r"(?:\(?\s*(?:note|nb)[:,]?\s*)?"
    r"(?:this (?:count|figure|result)\s+)?"
    r"(?:filter(?:ed|ing)?|exclud(?:ed|ing)|restrict(?:ed|ing)?|scoped?)"
    # [^\n] not [^.\n]: the gap has to cross the dot in "r.is_synthetic",
    # which is exactly how the alias-qualified form is written.
    r"[^\n]{0,60}?\bis_synthetic\b" + _SCOPE_TAIL + r"\.?\)?)"
    r"|(?:\b(?:where|with)\s+\w*\.?is_synthetic\s*=\s*\d\b" + _SCOPE_TAIL + r")"
    r"|(?:\b\w*\.?is_synthetic\s*=\s*\d\b)",
    re.I)


# Which tables can hold one row per SUBJECT. Counting a subject from a table
# whose grain is something else is the most common way to get a real number
# that answers the wrong question, and it is invisible to value-matching:
# asked how many TESTS are assigned for a product, the worker counted
# iec_emc_requests rows and answered 4. Four is the number of JOBS. The answer
# is 16, and nothing about "4" looked wrong.
#
# Only subjects with an unambiguous grain are listed. PRODUCT is absent on
# purpose - products are counted off the request table, so the grain question
# does not arise.
_SUBJECT_TABLES = {
    "TEST": ("iec_emc_request_tests", "planner_entries", "datasheet"),
    "DATASHEET": ("datasheet", "datasheet_records", "datasheet_revision"),
    "REQUEST": ("iec_emc_requests",),
    "ENGINEER": ("users", "planner_entries", "iec_emc_request_tests", "datasheet"),
    "EQUIPMENT": ("equipment", "datasheet_equipment", "maintenance"),
    "MEASUREMENT": ("datasheet_measurement", "datasheet_observation"),
    "REVIEW": ("datasheet_status_history", "datasheet_revision", "planner_entries"),
}

# Does the answer tell the reader the thing lives in the other corpus? Any of
# these words does the job; the exact phrasing is the writer's business.
_DISCLOSES_SCOPE_RE = re.compile(
    r"\bdemo\b|\bdemonstration\b|\bsynthetic\b|\bsample\s+data\b"
    r"|\bnot\s+(?:in|part of)\s+(?:the\s+)?real\b"
    r"|\bonly\s+(?:in|exists?\s+in)\b", re.I)

# Which column has to appear in the SQL for a state to have been measured at
# all. This is the concrete form of "did the query implement the plan": you
# cannot have counted assigned-on-the-request without reading
# assigned_engineer_id, whatever else the query did.
#
# Only states with an unambiguous column are listed. A state whose definition
# is a join shape rather than a column is left out deliberately - a check that
# guesses produces false alarms, and a false alarm here rewrites a correct
# answer.
#
# NOTE FOR THIS TREE: 8 of these 11 names are metrics our semantics.METRICS
# does not define yet, so those entries are inert until it does. Kept whole
# rather than trimmed to the three we have, because the cost of an unused key
# is nothing and the cost of forgetting to add it back is a silent check.
_STATE_COLUMNS = {
    "test_assigned_on_request": ("assigned_engineer_id", "assigned_engineer_name"),
    "test_assigned_in_schedule": ("engineer_user_id",),
    "request_assigned_status": ("status",),
    "datasheet_submitted": ("submitted_at",),
    "datasheet_approved": ("status",),
    "datasheet_draft": ("status",),
    "datasheet_rejected_in_review": ("to_status",),
    "request_rejected": ("rejected_at",),
    "test_passed": ("result",),
    "test_failed": ("result",),
    "test_in_progress": ("status",),
}


def _plan_mismatch(ledger, draft=None):
    """Did the executed SQL implement the plan, or answer something adjacent?

    The grounding check asks whether a number is real. This asks whether it is
    the ANSWER - which is a different question, and the one that was invisible
    before a plan existed to compare against. A figure from query A attaches
    perfectly well to a claim about query B, and value-matching cannot tell.
    """
    plan = getattr(ledger, "plan", None)
    if not plan:
        return None

    entity = plan.get("entity") or {}

    # Checked FIRST because it needs the opposite answer from a plain miss.
    # "Not resolved" means say it does not exist; this means it DOES exist, in
    # the corpus the question excluded, and reporting zero says the thing is
    # here with no work against it - a different and false statement.
    if entity.get("excluded_by_scope") and not _DISCLOSES_SCOPE_RE.search(draft or ""):
        return ("%r has records ONLY in the corpus this question excludes. The "
                "answer must say that - reporting zero, or 'no records', "
                "implies the thing exists here with no work against it, which "
                "is a different and false statement" % entity.get("value"))

    # A count must come from a query, never from the size of a lookup result.
    # Resolving a product to four jobs does not mean four tests.
    if plan.get("operation") in ("COUNT", "AGGREGATE"):
        ran_sql = [e for e in ledger.entries if not e.get("error")]
        if not ran_sql:
            return ("this question asks for a %s and no query was executed - "
                    "any figure in the answer was read off a lookup, not "
                    "measured" % plan["operation"].lower())

    if not ledger.entries:
        return None
    if all(e.get("worker") == "semantics" for e in ledger.entries if not e.get("error")):
        return None

    queried = ledger.tables_queried()
    if not queried:
        return None

    # PLAN CONSISTENCY - the planned tables were never read.
    planned = {t.lower() for t in (plan.get("source_tables") or [])}
    if planned and not (planned & queried):
        return ("the plan expected %s and the queries read %s instead"
                % (", ".join(sorted(planned)[:3]), ", ".join(sorted(queried)[:3])))

    # SUBJECT GRAIN - a count off the wrong table counts the wrong thing.
    subject = plan.get("subject")
    if plan.get("operation") in ("COUNT", "AGGREGATE"):
        grain = _SUBJECT_TABLES.get(subject)
        if grain and not (set(grain) & queried):
            return ("the question counts %ss, which live in %s, but the "
                    "queries only read %s - a count off the wrong table counts "
                    "the wrong thing"
                    % (subject.lower(), " / ".join(grain[:3]),
                       ", ".join(sorted(queried)[:3])))

    # ENTITY SCOPE - the question named a thing and no query filtered on it.
    # This is the "77 unfilled tests" bug, detected instead of shipped.
    idents = [str(i) for i in (entity.get("identifiers") or []) if i]
    if entity.get("resolved") and idents:
        sql = ledger.sql_text()
        value = str(entity.get("value") or "").lower()
        named = any(i.lower() in sql for i in idents) or (value and value in sql)
        if not named:
            return ("the question is about %s (%s) and no executed query "
                    "filters on it - these figures look lab-wide"
                    % (entity.get("value"), ", ".join(idents[:3])))

    # The defining column of a single reviewed reading was never read.
    metrics = plan.get("candidate_metrics") or []
    if len(metrics) == 1:
        cols = _STATE_COLUMNS.get(metrics[0])
        if cols:
            sql = ledger.sql_text()
            if not any(c in sql for c in cols):
                return ("the question is about %s, which is defined by %s, and "
                        "no executed query reads that column"
                        % (metrics[0], " / ".join(cols)))
    return None


def _scope_breach(ledger):
    """Did a REAL-scoped answer end up standing on synthetic rows?

    The guard rejects a query that does not mention is_synthetic, but it is
    deliberately shallow - it does not check the comparison is the right way
    round. This is where a wrong comparison surfaces: the returned rows
    themselves are inspected, and only when the query actually selected the
    flag as an output column, which is the only case that can be checked
    without re-running anything.
    """
    plan = getattr(ledger, "plan", None)
    if not plan or plan.get("scope") != "REAL":
        return None
    for entry in ledger.entries:
        cols = [str(c).lower() for c in (entry.get("columns") or [])]
        if "is_synthetic" not in cols:
            continue
        idx = cols.index("is_synthetic")
        for row in entry.get("rows") or ():
            if idx < len(row) and str(row[idx]).strip() in ("1", "True", "true"):
                return ("a query returned synthetic/demo rows although this "
                        "question is scoped to real lab data")
    return None


def strip_machinery(text):
    """Remove SQL, tool calls and internal labels from an answer.

    Deliberately conservative: it only touches text carrying one of the markers
    above. Bare identifiers are left alone, because a SCHEMA question's answer
    IS an identifier - "the coupling method is in datasheet_ce.coupling_method"
    is exactly right and must survive.
    """
    if not text:
        return text
    out = _SQL_STMT_RE.sub("", text)
    out = _TOOL_CALL_RE.sub("", out)
    out = _MACHINERY_LABEL_RE.sub("", out)
    out = _INSIGHT_LABEL_RE.sub("", out)
    out = _PROCESS_PROSE_RE.sub("", out)
    out = _ROWCOUNT_PROSE_RE.sub("", out)
    out = _SCOPE_PROSE_RE.sub("", out)
    out = _MISSING_FIELD_RE.sub("", out)
    out = _KWARG_RE.sub("", out)
    # tidy what removal left behind: orphaned punctuation and blank runs
    out = re.sub(r"[ \t]*\(\s*\)", "", out)
    out = re.sub(r"[ \t]+([.,;:])", r"\1", out)
    out = re.sub(r"(?m)^[ \t]*[.;,]+[ \t]*$", "", out)
    # A LABEL WITH NOTHING LEFT UNDER IT. Removing the SQL leaves its lead-in
    # behind, and an answer ending "Query used:." tells the reader that
    # something was cut without saying what - it reads like a truncation bug,
    # which is worse than either showing the SQL or never mentioning it. Drop
    # any heading-shaped line whose content went with the statement.
    out = re.sub(r"(?mi)^[ \t]*(?:#{1,6}[ \t]*)?"
                 r"(?:sql|query|queries)(?:[ \t]+\w+){0,3}[ \t]*:[ \t]*[.;,]?[ \t]*$",
                 "", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()
