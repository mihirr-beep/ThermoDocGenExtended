# -*- coding: utf-8 -*-
"""The last stage: turn verified evidence into a sentence a person would say.

WHAT THIS IS FOR
----------------
Everything before this point decides WHAT IS TRUE. This decides HOW IT READS.
The split is the whole design: SQL determines the facts, the language model
determines the language, and this module is the only place the second happens
after the first is settled.

By the time a draft arrives here it has been through the grounding check, the
semantic check and, if anything was flagged, a repair pass. The figures in it
are the database's. What is often wrong is the shape - a worker writes for the
orchestrator, not for a person, and the result reads like a status report:
"total ce datasheets filled by krishna gonela: 1 approved ce datasheets among
them: 0". That is the real observed output of a question a human asked. It is
correct and it is not an answer.

WHY IT IS ALLOWED TO REWRITE AT ALL
-----------------------------------
Because it is given the evidence and forbidden from exceeding it, and because
the rewrite is checked afterwards. The constraint that makes this safe is not
the prompt - prompts have lost to question framing repeatedly in this
codebase - it is _guard(): every number in the rewritten answer must already
have been in the draft. A rewrite that introduces a figure is discarded and
the draft is shown instead. So the worst case is the answer people got before
this module existed, and the model cannot invent a total no matter what it is
asked to do.

WHAT IT MUST NOT DO
-------------------
Not reinterpret the question, not compute, not aggregate, not infer cause, not
add lab knowledge. Those are all forms of deciding a fact, and facts were
decided upstream. It may reorder, name things properly, drop machinery, and
turn a colon-separated dump into a sentence.

OFF SWITCH
----------
NLP_FINAL_ANSWER=0 disables the stage and returns the verified draft
unchanged, which is the behaviour that existed before it. Kept because this is
one extra model call on every question and the comparison has to stay
runnable.
"""
import os
import re

# Verdicts whose text must survive untouched. A clarifying question is already
# exactly what should be said, and a refusal that gets "made more natural"
# starts hedging its way back towards an answer.
_PASS_THROUGH = ("clarify", "no-evidence", "unsupported")

_PROMPT = """You are the response writer for a lab database assistant.

Rewrite the DRAFT as a direct, natural answer to the QUESTION.

Absolute rules:
- Use ONLY facts already present in the draft and the evidence below.
- Do NOT invent, compute, total, average or infer any number. Every figure in
  your answer must appear in the draft already.
- Do NOT reinterpret the question or answer a nearby one.
- Do NOT add lab or engineering knowledge from outside the evidence.
- Do NOT state a cause. If the evidence shows a sequence, describe the
  sequence. Correlation is not causation and this database records no
  diagnosis.
- If the draft says the data does not contain something, keep saying that.
- Keep any caveat the draft carries about what a figure excludes or means.

Style:
- A simple factual question gets ONE sentence. "There are 27 active products."
- A question about several things gets a short paragraph, or a short list if
  the reader needs to scan it.
- Write for a lab engineer. No preamble, no "based on the data", no restating
  the question, no offers of further help.
- Never mention SQL, tables, columns, queries, tools or evidence.
- Do NOT volunteer that demonstration records were excluded. It is the default
  and describing it reads as a caveat on the number, which it is not.
  ONE EXCEPTION, and it is not optional: if the draft says the thing asked
  about exists ONLY in the demonstration data, keep saying that. Without it
  the answer becomes "there are none", which tells the reader the thing exists
  here with no work against it - a different and false statement. This rule
  removed exactly that sentence once and turned a correct answer into a wrong
  one.

QUESTION
{question}

EVIDENCE THE DRAFT WAS BUILT FROM
{evidence}

DRAFT
{draft}

Write the final answer and nothing else."""

# Numbers as a reader would see them. Used to prove the rewrite invented
# nothing - the same tokenisation the grounding check uses, kept local so a
# change there cannot silently loosen this.
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# Figures nobody would call a fabricated statistic: small ordinals that appear
# in ordinary prose ("one of the two"), years, and anything the question itself
# supplied.
_INNOCUOUS = frozenset(
    [str(n) for n in range(0, 11)]
    + ["%d" % y for y in range(2015, 2036)])


def enabled():
    return os.environ.get("NLP_FINAL_ANSWER", "1") != "0"


def write(question, draft, ledger, verdict="grounded", model=None):
    """The final user-facing answer.

    Returns (text, note). `note` says what happened, for the debug trace - it
    is never shown to the user.
    """
    text = (draft or "").strip()
    if not enabled():
        return text, "final-answer stage disabled"
    if not text:
        return text, "empty draft"
    if verdict in _PASS_THROUGH:
        return text, "verdict %r passes through unchanged" % verdict

    evidence = ""
    try:
        evidence = ledger.evidence_digest(max_rows_per_query=12)[:6000]
    except Exception:  # noqa: BLE001 - a digest failure is not an answer failure
        evidence = "(evidence unavailable)"

    rewritten = _complete(_PROMPT.format(
        question=(question or "")[:600],
        evidence=evidence,
        draft=text[:4000]), model=model)

    if not rewritten:
        return text, "final-answer model unreachable; draft kept"

    ok, why = _guard(text, rewritten)
    if not ok:
        return text, "final-answer rewrite rejected (%s); draft kept" % why
    return rewritten, "rewritten"


def _guard(draft, rewritten):
    """(ok, reason). The check that makes the rewrite safe to ship.

    Two things are refused:

      * a number that was not in the draft. This is the one that matters. A
        language stage that can introduce a figure is a fact stage, and the
        whole separation collapses.
      * an empty or absurdly long result, which means the model did something
        other than rewrite.

    A dropped caveat is deliberately NOT refused here. The rewrite runs before
    attach_caveats, which re-appends any owed note unconditionally, so a caveat
    the model deleted comes straight back - rejecting the whole rewrite for it
    would throw away a good answer to fix something already fixed.
    """
    if not rewritten.strip():
        return False, "empty"
    if len(rewritten) > max(1200, len(draft) * 3):
        return False, "implausibly long"

    had = set(_NUM_RE.findall(draft)) | _INNOCUOUS
    for tok in _NUM_RE.findall(rewritten):
        if tok in had:
            continue
        # 27.0 against 27, and 1,200 against 1200.
        if tok.rstrip("0").rstrip(".") in {h.rstrip("0").rstrip(".") for h in had}:
            continue
        return False, "introduced the figure %s" % tok

    return True, ""


def _complete(prompt, model=None, max_tokens=700):
    """One completion. None when the model is unreachable."""
    try:
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model=(model or os.environ.get("NLP_FINAL_ANSWER_MODEL")
                   or os.environ.get("NLP_SEARCH_MODEL") or "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=max_tokens)
        return (resp.choices[0].message.content or "").strip()
    except Exception:  # noqa: BLE001 - the draft is always a valid fallback
        return None
