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


# Questions about a product ACROSS its tests. These go straight to the
# datasheets worker, which owns the analyse_history tool.
#
# Routing them deterministically rather than letting the orchestrator decide is
# not an optimisation, it is the only thing that works. Asked why a product
# failed its first three tests, the orchestrator resolved the name, found four
# jobs, and asked which one was meant - twice, in different words, after being
# told twice not to. A small model's pull toward asking a clarifying question
# beats an instruction telling it not to. The question is recognisable from its
# shape, so recognise it in code.
# Written narrowly the first time, against the eight questions the primitives
# were built for, and it showed: of seventeen questions phrased the way people
# actually speak, three matched. "How many attempts before it PASSED" missed on a
# word boundary after "pass". "Why DO datasheets get sent back" missed because
# only did/was/were/does/is/has/have were listed. "Fail for the same REASON"
# missed because only failure/problem/issue/pattern were. Each miss sent the
# question to a worker with no analyse_history, which answered from hand-written
# SQL and reported an absence.
#
# So this is deliberately generous now. Over-matching is cheap: the datasheets
# worker still has run_sql and answers ordinary datasheet questions perfectly
# well, and it is the domain that owns results anyway. Under-matching costs a
# wrong answer. The control set in the tests keeps ordinary questions - overdue
# calibration, unfilled datasheets, requests raised in July - out of here.
_INSIGHT_RE = re.compile(
    r"(?:"
    # --- causal
    #
    # NOT a bare \bwhy\b. That was the first version and it was wrong in the way
    # that costs most: "why is the LISN overdue for calibration" routed to the
    # datasheets worker, which cannot see the equipment table at all and would
    # have answered with an absence rather than saying it needed another domain.
    # Over-matching is NOT cheap - it hands a question to a worker that is blind
    # to the tables holding the answer. Under-matching only costs the
    # orchestrator's extra turns, which is the safe direction.
    r"\bwhy\b(?=[^?.]{0,60}\b(?:fail|failed|failing|fails|pass|passed|reject|"
    r"rejected|exceed|exceeded|breach|compliant|criteri|margin|limit|emission|"
    r"sent back|bounced|result|retest|re-test)\b)"
    r"|what (?:was|went|is) (?:actually )?wrong"
    r"|root cause|underlying (?:cause|reason|issue)"
    r"|\bcaused?\b|what (?:is|was) (?:causing|behind)"
    # --- repetition / streak
    r"|\b(?:kept|keeps|repeatedly|again and again|multiple times) (?:on )?fail"
    r"|fail(?:ed|ing|s)? (?:again|repeatedly|more than once|several times)"
    r"|how many (?:attempts|tries|times|goes|rounds|iterations)"
    r"|\bbefore (?:it|they|the \w+) (?:first(?:ly)? )?pass(?:ed|es|ing)?\b"
    r"|(?:to|and) (?:make|get) (?:it|them|the \w+) (?:to )?pass"
    r"|get (?:it|them) through\b"
    # --- change between attempts
    r"|what (?:changed|differed|was different|had changed)"
    r"|(?:what|which) .{0,40}\b(?:changed|differ|different)\b"
    r"|(?:modification|modifications|change|changes|fix|fixes|fitted|retrofit|"
    r"introduced|added|replaced) .{0,40}\b(?:before|between|prior)\b"
    r"|what did (?:they|we|he|she) (?:change|do|fit|add|fix)"
    # --- trend / measurement movement
    r"|improve(?:d|ment|ments)?\b|got (?:better|worse)"
    r"|came down|went down|dropped|reduced by|brought .{0,20}down"
    r"|over (?:time|its tests|the campaigns)|trend"
    r"|(?:which|what) .{0,30}frequenc"
    # --- pattern across products
    r"|(?:other|another|any other|different) products?"
    r"|same (?:failure|problem|issue|pattern|reason|cause|mode|thing)"
    r"|similar (?:failure|pattern|problem|issue|reason|cause)"
    r"|happened (?:to anything else|elsewhere|before|again)"
    r"|most common (?:reason|failure|cause|mode|problem|issue)"
    r"|(?:failure|rejection) (?:mode|reason|pattern)s?\b"
    r"|never passed|still failing|not yet passed|no pass"
    r"|across (?:all|every|the) (?:products?|lab|tests?|campaigns?)"
    # --- history
    r"|(?:testing|test) history|history (?:of|for)\b|track record"
    # The way a person actually opens a high-level question. "In one short
    # paragraph, what happened with the Aurora Centrifuge?" matched nothing, so
    # it went to the equipment domain on the word "Centrifuge" and came back
    # offering V-LOG ARRAY ANTENNA as the closest match. The most natural
    # phrasing there is, and it was the one phrasing not covered.
    r"|what happened|what(?:'s| is) the story|the story (?:of|on|behind)"
    r"|overall picture|the picture on|big picture|where (?:do|does) .{0,20}stand"
    r"|summar(?:y|ise|ize)|walk me through|bring me up to speed"
    r"|tell me about (?:the |what )?"
    # --- the paperwork axis
    r"|sent back|bounced|rejected in (?:peer )?review|peer[- ]review reject"
    r"|(?:why|reason).{0,30}reject"
    r")", re.I)


# Words that put a question in someone else's domain whatever else it says. A
# calibration or scheduling question phrased "why..." or "what changed..." is
# still a calibration or scheduling question, and the datasheets worker cannot
# see those tables - it would report an absence, which reads as a fact.
_NOT_INSIGHT_RE = re.compile(
    r"\b(?:calibration|calibrated|recalibrat\w*|maintenance|overdue|due date|"
    r"asset|inventory|instrument|instruments|"
    r"scheduled|schedule|planner|assigned|assignment|workload|"
    r"logged in|login|password|permission|role)\b", re.I)


def is_insight(question):
    """True when the question is about a product's test history over time.

    The veto matters more than the match. Sent a question it cannot answer, a
    worker reports "there are no records", and a reader cannot tell that from
    "there is nothing to find" - so anything that plainly belongs to equipment
    or scheduling goes back to the orchestrator, which can reach both.
    """
    q = question or ""
    if _NOT_INSIGHT_RE.search(q):
        return False
    return bool(_INSIGHT_RE.search(q))


def single_domain(question):
    """The one domain that owns this question, or None if it is not clear-cut.

    Deliberately strict. Returning None costs a few model calls; returning the
    wrong domain costs a wrong answer, because a worker cannot see outside its
    own allowlist and will report an absence rather than a gap.
    """
    q = (question or "").lower()
    if not q:
        return None
    # Checked before _CROSS_DOMAIN: "why did this PRODUCT fail its TESTS" names
    # two domains and reads as cross-domain, but analyse_history spans them
    # itself, so the datasheets worker can answer the whole thing alone.
    if is_insight(q):
        return "datasheets"
    if _CROSS_DOMAIN.search(q):
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


# --------------------------------------------------------------------------
# the question asks for a CAUSE, and this database records no causes
# --------------------------------------------------------------------------
# Asked for "the confirmed root cause" of a conducted emission failure, the
# answer was: "the confirmed root cause is that it exceeded the class B
# quasi-peak limit at 0.72 MHz by 4.8 dB". That is circular - the cause of the
# failure is that it failed - and it asserts a confirmation nothing in the data
# supports. The exceedance is the symptom that DEFINES the failure.
#
# A note in the tool output did not prevent it, because the question itself
# supplies the framing: "what was the confirmed root cause" invites a confident
# answer, and a small model will supply one. So this is injected for that one
# run, where it cannot be crowded out.
_CAUSAL_RE = re.compile(
    r"\broot cause|\bcaused? by\b|what caused|\bwhy exactly\b|"
    r"\b(?:confirmed|actual|underlying|true) (?:cause|reason)|"
    # "which internal component caused ..." - an adjective sits between the two
    # words, so requiring them adjacent missed the question this was written for.
    r"\bwhich(?: \w+){0,2} (?:component|part|board|module|supplier|vendor|batch|"
    r"chip|cable|filter)\b(?:.{0,30}\bcaus)?|"
    r"\b(?:component|part|supplier|batch) (?:that |which )?caus", re.I)


def asks_for_cause(question):
    """True when the question wants a causal claim rather than a fact."""
    return bool(_CAUSAL_RE.search(question or ""))


CAUSAL_DIRECTIVE = """
## THIS QUESTION ASKS FOR A CAUSE. THIS DATABASE DOES NOT RECORD CAUSES.

It records what was measured, what was fitted, what the reviewer wrote and when
each happened. Nobody enters a diagnosis. So there is no field you can read to
answer "why", and no amount of querying will produce one.

Do NOT do either of these:
  * State a cause as confirmed. "The confirmed root cause was the missing
    common-mode choke" is a claim the data cannot support, and the engineer
    reading it may act on it.
  * Answer with the symptom dressed as a cause. "The root cause is that it
    exceeded the limit at 0.72 MHz" says the cause of failing was failing. It
    sounds like an answer and contains none.

Do this instead, in one short paragraph:
  1. Say plainly that the recorded data does not identify a cause.
  2. Give the sequence that bears on it - what the measurement did, what was
     changed between attempts, what the reviewer said. Numbers and dates.
  3. Leave the inference to the reader. "A common-mode choke was fitted between
     the failing and passing tests, and the 0.72 MHz margin improved by 5.3 dB"
     is the most useful true thing you can say. An engineer will draw the
     obvious conclusion and be right; you asserting it is how this tool starts
     being believed about things it cannot know.

If the question names something the schema has no field for at all - an internal
component, a supplier, a batch, a cost, hours spent - say that it is not
recorded, name what IS recorded that is closest, and stop.
"""
