# -*- coding: utf-8 -*-
"""What kind of question is this?

Not every question about the lab is a question about its data, and answering
the wrong kind is a reliable way to be confidently wrong. Asked "where is the
coupling method value in the DB for CE", the assistant had exactly one tool -
NL->SQL - so the only move available was to read rows and infer a container.
It picked the one free-text column on the request table and said the value
lived there. It does not; the field is on the datasheet side entirely. Every
figure quoted was real, so the grounding check passed it.

The fix is not a better prompt. It is noticing, before any retrieval happens,
that the question is about the SCHEMA and must be answered from the catalog -
a search over column names, which cannot invent a column and can honestly
return nothing.

Three kinds:

  schema     - where is X kept, which table/column, what fields exist, what
               values can this take. Answered from the generated catalog.
  data       - counts, lists, results, who did what. Answered by the workers.
  capability - what can you do, what do you know about. Answered directly.

Detection is deliberately a regex rather than a model call: the phrasing is
narrow and recognisable, it costs nothing, and a classifier that itself
hallucinates would put us back where we started. A miss is safe - the question
just takes the normal path, and the orchestrator still has find_field.
"""
import re

SCHEMA = "schema"
DATA = "data"
CAPABILITY = "capability"

# Evaluative words with no definition in this schema. Asked "how many tests are
# overdue", the assistant invented a rule - in_progress or report_uploaded with
# an end date in the past - and reported "16 overdue tests" as fact. Nothing in
# the database says that. The number was arithmetically real and completely
# made up, which is the most dangerous shape a wrong answer can take.
#
# These cannot be banned (people will keep asking) so the rule is: use one, and
# you must state the rule you applied, in the answer, so the user can disagree.
UNDEFINED_TERMS = (
    "overdue", "late", "behind", "delayed", "backlog", "stuck", "blocked",
    "pending", "outstanding", "at risk", "urgent", "critical", "idle", "free",
    "busy", "available", "underutilised", "underutilized", "efficient",
    "productive", "healthy", "problematic", "slow",
)
_UNDEFINED_RE = re.compile(
    r"\b(?:%s)\b" % "|".join(t.replace(" ", r"\s+") for t in UNDEFINED_TERMS), re.I)


def undefined_terms_in(question):
    """The judgement words a question leans on that the schema does not define."""
    seen, out = set(), []
    for m in _UNDEFINED_RE.finditer(question or ""):
        t = m.group(0).lower()
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# --------------------------------------------------------------------------
# which single domain owns this question, when one clearly does
# --------------------------------------------------------------------------
# Nine model calls to answer "how many CE datasheets does Krishna Gonela have"
# - a question that needs one SELECT. The orchestrator spends a turn deciding
# to call a worker, the worker then runs its own four-turn loop inside that
# turn, and the orchestrator spends more turns relaying the result back.
#
# For a question that plainly belongs to one domain the orchestrator adds
# nothing but latency and tokens. When the signal is unambiguous - words from
# exactly one domain and no others - the worker is run directly as the
# top-level agent. Anything cross-domain, vague, or multi-part still goes
# through the orchestrator, which is what it is for.

_DOMAIN_WORDS = {
    "datasheets": (
        "datasheet", "datasheets", "result", "results", "pass", "passed",
        "observation", "observations", "criterion", "ambient", "humidity",
        "temperature", "coupling", "measured", "recorded", "reading",
        "readings", "deviation", "signoff", "filled", "submitted"),
    "inventory": (
        "equipment", "equipments", "instrument", "instruments", "calibration",
        "calibrated", "maintenance", "asset", "serial", "make", "model",
        "probe", "analyser", "analyzer", "receiver", "generator", "kit"),
    "schedule": (
        "schedule", "scheduled", "planner", "assigned", "assignment",
        "engineer", "engineers", "reviewer", "review", "reviewing", "workload",
        "busy", "in progress", "cancelled", "peer"),
    "requests": (
        "request", "requests", "job", "jobs", "tco", "requester", "customer",
        "product", "eut", "standard", "standards", "scope", "requested",
        "approval"),
    # NOTE: "submitted by" was here and collided with the datasheets domain's
    # "submitted", so "the CE datasheets submitted by Krishna" registered as
    # two domains and fell through to the orchestrator - three extra turns for
    # a question one worker owns outright.
}

# Words that mean the question spans domains whatever else it says.
_CROSS_DOMAIN = re.compile(
    r"\bused (?:on|in|for)\b|\bvs\b|\bversus\b|\bcompare\b|\bagainst\b|"
    r"\bwhich .{0,30}\b(?:and|with)\b.{0,30}\b(?:also|too)\b|"
    r"\bacross\b|\bboth\b|\bas well as\b", re.I)


def single_domain(question):
    """The one domain that owns this question, or None if it is not clear-cut.

    Deliberately strict. Returning None costs a few model calls; returning the
    wrong domain costs a wrong answer, because a worker cannot see outside its
    own allowlist and will report an absence rather than a gap.
    """
    q = (question or "").lower()
    if not q or _CROSS_DOMAIN.search(q):
        return None
    hits = {}
    for domain, words in _DOMAIN_WORDS.items():
        n = sum(1 for w in words if re.search(r"\b%s\b" % re.escape(w), q))
        if n:
            hits[domain] = n
    if len(hits) != 1:
        return None
    domain, n = next(iter(hits.items()))
    # "equipment" alone in a long rambling question is not enough signal
    return domain if n >= 1 and len(q) < 220 else None


UNDEFINED_DIRECTIVE = """
## THE QUESTION USES A TERM THIS DATABASE DOES NOT DEFINE: {terms}

There is no column, status or rule anywhere in the schema that says what
{terms} means. You are not forbidden from answering - but you must not
silently invent the definition and report the resulting number as fact.

Do ONE of these:
  * Answer, and state the rule you applied in the same breath: "Taking
    'overdue' as a planned end date in the past and status still in_progress,
    that is N." The user can then disagree with the rule instead of trusting
    a number built on an assumption they never saw.
  * Or, if more than one reasonable rule would give materially different
    answers, ask which they mean before computing anything.

Never write a bare "there are N overdue tests". N depends entirely on a
definition you chose, and the reader has no way to know that.
"""

# "where is X stored", "which column holds X", "is there a field for X"
_SCHEMA_PATTERNS = (
    r"\bwhere\s+(?:is|are|does|do|can i find|would i find)\b[^?]{0,80}"
    r"\b(?:stored|store|saved|kept|held|come from|coming from|in the db|"
    r"in the database|in db)\b",
    r"\bwhich\s+(?:table|column|field|attribute)\b",
    r"\bwhat\s+(?:table|column|field|attribute)s?\b",
    r"\b(?:table|column|field)\s+(?:name\s+)?(?:for|holds?|contains?|stores?)\b",
    r"\bis\s+there\s+a\s+(?:table|column|field|flag)\b",
    r"\bdoes\s+the\s+(?:db|database|schema|table)\b",
    r"\bhow\s+is\s+[^?]{0,60}\b(?:stored|saved|recorded|persisted|modell?ed)\b",
    r"\bwhat\s+(?:fields|columns)\s+(?:does|do|are)\b",
    r"\bin\s+(?:the\s+)?db\b[^?]{0,40}\bwhere\b",
    r"\bschema\b",
    r"\bwhere\s+in\s+the\s+(?:db|database)\b",
)
_SCHEMA_RE = re.compile("|".join("(?:%s)" % p for p in _SCHEMA_PATTERNS), re.I)

# "what can you do", "what do you know"
_CAPABILITY_RE = re.compile(
    r"\b(?:what|which)\s+(?:can|could)\s+you\b|"
    r"\bwhat\s+(?:do|are)\s+you\s+(?:know|able|capable)\b|"
    r"\bwhat\s+(?:kind|sort|type)s?\s+of\s+questions?\b|"
    r"\bhelp\b\s*$|\bwhat\s+data\s+do\s+you\s+have\b", re.I)

# The concept the user is asking about, pulled out of a schema question so it
# can be handed straight to find_field.
_SUBJECT_PATTERNS = (
    r"\bwhere\s+is\s+(?:the\s+)?(.+?)\s+(?:value\s+)?(?:stored|saved|kept|held|"
    r"in\s+db|in\s+the\s+db|in\s+the\s+database|come|coming)\b",
    r"\bwhere\s+is\s+(?:the\s+)?(.+?)\s+(?:value|field|column)\b",
    r"\bwhich\s+(?:table|column|field)\s+(?:holds?|has|contains?|stores?)\s+"
    r"(?:the\s+)?(.+?)(?:\?|$)",
    r"\bis\s+there\s+a\s+(?:table|column|field|flag)\s+(?:for|holding|with)\s+"
    r"(?:the\s+)?(.+?)(?:\?|$)",
    r"\bhow\s+is\s+(?:the\s+)?(.+?)\s+(?:stored|saved|recorded|persisted)\b",
)
_SUBJECT_RES = tuple(re.compile(p, re.I) for p in _SUBJECT_PATTERNS)

_TEST_CODE_RE = re.compile(
    r"\b(CE|RE|EFT|ESD|SURGE|VOLTAGEDIPS|VOLTAGE[ _-]?DIPS|HARMONIC|"
    r"VOLTAGEFLICKER|FLICKER|CRF|PFMF|RS[_ ]?RI|RS)\b")


def classify(question):
    """schema / data / capability."""
    q = (question or "").strip()
    if not q:
        return DATA
    if _CAPABILITY_RE.search(q):
        return CAPABILITY
    if _SCHEMA_RE.search(q):
        return SCHEMA
    return DATA


def subject_of(question):
    """The concept a schema question is asking about, for find_field."""
    q = (question or "").strip().rstrip("?")
    for rx in _SUBJECT_RES:
        m = rx.search(q)
        if m:
            sub = m.group(1).strip(" ?.,")
            # trim a trailing "for CE" / "in the test request object"
            sub = re.sub(r"\s+(?:for|in|of|on)\s+\S.*$", "", sub, flags=re.I).strip()
            if sub:
                return sub
    # nothing matched a pattern: fall back to the whole question, which
    # find_field will tokenise anyway
    return q


def test_code_in(question):
    """The test code named in the question, normalised, or None."""
    m = _TEST_CODE_RE.search(question or "")
    if not m:
        return None
    raw = m.group(1).upper().replace(" ", "_").replace("-", "_")
    return {"VOLTAGE_DIPS": "VOLTAGEDIPS", "FLICKER": "VOLTAGEFLICKER",
            "RS": "RS_RI", "RS_RI": "RS_RI"}.get(raw, raw)


# Injected into the orchestrator's instructions for one run when the question
# turns out to be about the schema rather than the data.
SCHEMA_DIRECTIVE = """
## THIS QUESTION IS ABOUT THE SCHEMA, NOT THE DATA

The user is asking WHERE something is recorded, not what its value is. Answer
it from find_field, which searches column names in the catalog.

- Call find_field first, with the concept they named{code_hint}.
- Answer with the exact table.column names it returns. Nothing else counts as
  an answer to "where is it stored".
- If find_field returns NO matches, the field is not recorded in this database.
  Say exactly that. Do NOT go looking for a column that might plausibly hold it,
  do NOT suggest it is inside a JSON or free-text column, and do NOT run a query
  to go hunting. A wrong pointer is worse than "we do not store that", because
  someone will go and build on it.
- Do not answer a schema question by quoting data values. That the column
  custom_spec contains the text "As per the standard" is not evidence that it
  holds the coupling method.
- You may follow up with one query to show a sample value, but only AFTER you
  have named the real column, and only from that column.
"""
