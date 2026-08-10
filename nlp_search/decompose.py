# -*- coding: utf-8 -*-
"""Split a multi-part question, answer each part on its own, assemble.

People do not ask one thing. They ask:

    "how many jobs are open, who is working on each of them, and has anything
     been sitting without a job number?"

That is three questions in one sentence, and answering it as one unit is what
broke. The pipeline scores 13/13 on single-fact questions and roughly 1 in 12
on these, and the reason is not retrieval - it is that the grounding check can
only pass or withhold a WHOLE answer. A reply whose second clause is wrong is
either published entire, which is how "engineer1 has 13 tasks" reached a user,
or destroyed entire, which is how a correct table became an apology. There is
no way to say "clause one is fine, clause two is not".

So each part gets its own run and its own verdict. A part that cannot be
grounded is dropped and named; the parts that survive are still delivered. The
depth is capped at three, which is as deep as real questions here go, and the
cap is enforced rather than requested.

OFF BY DEFAULT SINCE THE MOVE TO gpt-4o
---------------------------------------
That reasoning held for gpt-4o-mini. It does not hold for gpt-4o, which was
measured on the same eight complex questions with splitting on and off: the
same six passed and the same two failed either way. The scaffolding was
compensating for a weaker model, and against a stronger one it only added
latency and a failure mode of its own - it split "which jobs are behind on
datasheets, and how many instruments need calibration" across the clause that
selects the reviewed metric, so the reviewed SQL never fired and the model
wrote its own.

The module is kept, not deleted: it is the right shape for a genuinely
independent multi-part question, and a cheaper model in some future
deployment would want it back. Set NLP_SPLIT=1 to re-enable and re-measure.
"""
import json
import os
import re

MAX_PARTS = 3

# Conjunctions that actually join two ASKS, not two nouns. "tests and results"
# is one question; "how many tests and who ran them" is two. The distinction is
# a verb or a question word on the right-hand side.
_SPLIT_HINT = re.compile(
    r",\s*and\s+\w|"
    r"\band\s+(?:also\s+)?(?:can|could|tell|show|give|what|which|who|when|"
    r"where|how|is|are|does|do|has|have|whether|if)\b|"
    r"\balso\b[^.?]{0,40}\?|"
    r";|"
    r"\?\s*\w", re.I)

_QUESTION_WORD = re.compile(
    r"\b(?:what|which|who|when|where|how|is|are|does|do|did|has|have|can|"
    r"whether|any)\b", re.I)


def looks_multipart(question):
    """Cheap, conservative check. False means "treat as one question".

    Splitting is opt-in: NLP_SPLIT=1. See the module docstring for the
    measurement that turned it off.
    """
    if os.environ.get("NLP_SPLIT") != "1":
        return False
    q = (question or "").strip()
    if len(q) < 55:
        return False
    if not _SPLIT_HINT.search(q):
        return False
    # at least two things that look like an ask
    return len(_QUESTION_WORD.findall(q)) >= 2


_SPLIT_PROMPT = """Split this question into the separate things it asks for.

Rules:
- At most {max_parts} parts. If it asks for more, merge the closest two.
- Each part must be answerable ON ITS OWN - repeat the subject, the job number,
  the person's name in every part that needs it. "who is working on each of
  them" is useless alone; write "who is the assigned engineer on each open job".
- Do NOT invent parts. Do not add anything the user did not ask for.
- If it is really one question, return one part.
- Keep the user's own wording where you can, including their terminology.

Return JSON only: {{"parts": ["...", "..."]}}

QUESTION
{question}
"""


def split(question, model=None):
    """[subquestion, ...] - one entry when the question is single-part.

    Never raises and never returns empty: if the model is unreachable or
    replies with nonsense, the original question comes back unchanged, which
    is exactly the old behaviour.
    """
    q = (question or "").strip()
    if not q or not looks_multipart(q):
        return [q]
    try:
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model=(model or os.environ.get("NLP_SPLIT_MODEL")
                   or os.environ.get("NLP_SEARCH_MODEL") or "gpt-4o-mini"),
            messages=[{"role": "user",
                       "content": _SPLIT_PROMPT.format(question=q[:1500],
                                                       max_parts=MAX_PARTS)}],
            temperature=0, max_tokens=400)
        text = (resp.choices[0].message.content or "").strip()
        blob = re.search(r"\{.*\}", text, re.S)
        parts = json.loads(blob.group(0) if blob else text).get("parts") or []
        parts = [str(p).strip() for p in parts if str(p).strip()]
    except Exception:  # noqa: BLE001 - splitting is an optimisation, not a gate
        return [q]

    if not parts:
        return [q]
    # A split that returns one part, or that loses most of the question, is
    # not worth the extra runs.
    if len(parts) == 1:
        return [q]
    return parts[:MAX_PARTS]


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

_GOOD = ("grounded", "repaired", "clarify")

# A part that refers back to something without naming it.
_BACKREF_RE = re.compile(
    r"\b(?:it|its|them|they|their|those|these|each one|that one|the same|"
    r"i am|i have|my|me\b)\b", re.I)

# Things a part must not lose: names, job numbers, test codes, products.
_ENTITY_RE = re.compile(
    r"\b(?:[A-Z][a-z]+\s+[A-Z][a-z]+)\b"          # Krishna Muthangi
    r"|\b(?:[A-Z]{2,}-[A-Z]{2,}-[\w-]+)\b"        # TFS-EMC-2026-002
    r"|\b(?:CE|RE|EFT|ESD|SURGE|HARMONIC|CRF|PFMF|RS_RI|VOLTAGEDIPS|"
    r"VOLTAGEFLICKER)\b")


def context_for(part, index, original, prior):
    """What a sub-question needs from the rest of the request to stand alone.

    Two things go wrong without this, and both were observed:

    * The splitter drops the subject. "This is Krishna Muthangi. What tests am
      I assigned to, and has the datasheet been filled in?" split into a second
      part reading "For each test I am assigned to, has the datasheet been
      filled in" - first person with no antecedent. It answered about nobody
      and offered the user a list of all twenty usernames.

    * The second part depends on the FIRST PART'S ANSWER, not just on shared
      evidence. "Which equipment was used on ESD, and is any of it out of
      calibration" - "it" is whatever part one found. Sharing a ledger is not
      enough; the conclusion has to travel.

    Returns a preamble string, or "".
    """
    bits = []
    lost = [e for e in set(_ENTITY_RE.findall(original))
            if e not in part and e.lower() not in part.lower()]
    if lost and (_BACKREF_RE.search(part) or index > 0):
        bits.append("This is part of a larger question about: %s."
                    % ", ".join(sorted(lost)[:6]))
    if index > 0 and prior:
        answered = [p for p in prior
                    if p.get("verdict") in _GOOD and (p.get("answer") or "").strip()]
        if answered:
            last = answered[-1]
            bits.append("Already established by an earlier part of this same "
                        "question - treat it as given and build on it, do not "
                        "re-derive it:\n  Q: %s\n  A: %s"
                        % (last["question"], last["answer"].strip()[:700]))
    return ("\n\n".join(bits) + "\n\n") if bits else ""


def assemble(parts):
    """Join the per-part results into one reply.

    `parts` is [{"question", "answer", "verdict"}]. Parts that could not be
    grounded are named rather than silently dropped - the user asked three
    things and deserves to know which one came back empty.
    """
    kept = [p for p in parts if p.get("verdict") in _GOOD and (p.get("answer") or "").strip()]
    lost = [p for p in parts if p not in kept]

    if not kept:
        return None                       # caller falls back to the single-run answer

    if len(parts) == 1:
        return kept[0]["answer"].strip()

    chunks = []
    for p in kept:
        body = p["answer"].strip()
        # one short paragraph reads better without a heading
        chunks.append(body if len(kept) == 1 else "%s\n%s" % (_heading(p["question"]), body))

    out = "\n\n".join(chunks)
    if lost:
        out += "\n\n" + ("I could not answer %s of your questions from the data: %s"
                         % ("one" if len(lost) == 1 else str(len(lost)),
                            "; ".join('"%s"' % _heading(p["question"], bare=True)
                                      for p in lost)))
    return out


def _heading(question, bare=False):
    """The sub-question, trimmed to something that reads as a heading."""
    h = re.sub(r"\s+", " ", (question or "").strip().rstrip("?"))
    if len(h) > 90:
        h = h[:87] + "..."
    return h if bare else h + ":"
