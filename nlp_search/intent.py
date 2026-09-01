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

from . import schema_catalog as _catalog

# Generated, and generated LATER than this module was written, so read both
# defensively: a schema_catalog.py produced before the term index existed must
# degrade routing to the hand-written words, not stop the app booting. See the
# same guard in probes.py.
DOMAIN_TERMS = getattr(_catalog, "DOMAIN_TERMS", {})
TERM_SOURCES = getattr(_catalog, "TERM_SOURCES", {})
term_weight = getattr(_catalog, "term_weight", lambda _term: 0.0)

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
#
# "vs" / "versus" used to be here unconditionally, and that was wrong: most
# comparisons in this lab are between two VALUES OF ONE COLUMN, not two
# domains. "How many cables are shielded versus unshielded" is a single GROUP
# BY over one table, and treating it as cross-domain sent it to the
# orchestrator, which guessed the equipment inventory and answered "0 and 0"
# against 29 rows. A comparison only spans domains if the things being compared
# do - so the words that genuinely signal that are kept, and bare vs/versus is
# handled below where the two sides can be looked at.
# `used (on|in|for)` was here too, and it cost more than it saved. It was put in
# for "was any equipment used on a test out of calibration" - except the
# inventory slice was deliberately given datasheet_equipment so that ONE worker
# owns exactly that question (see DOMAINS in build_catalog). So the veto was
# guarding a case that needs no guarding, while vetoing every ordinary sentence
# containing the word "used": "what was the coupling method used for the CE
# test" scores datasheets 4.0 and nothing else, and was sent to the
# orchestrator for three extra turns.
_CROSS_DOMAIN = re.compile(
    r"\bcompare\b|\bagainst\b|"
    r"\bwhich .{0,30}\b(?:and|with)\b.{0,30}\b(?:also|too)\b|"
    r"\bacross\b|\bboth\b|\bas well as\b", re.I)

# Two asks in one sentence: "which jobs are behind, AND WHO is on them". One
# worker rarely owns both halves, and scoring cannot see the problem because the
# second half is usually pronouns - "who is on them" contains no schema noun at
# all, so the first half wins outright and the second is silently dropped.
#
# Reusing decompose's two regexes rather than writing a third: they already
# encode what a second ask looks like here, and they are the ones the splitter
# would use if NLP_SPLIT were on. Recognising a compound question is worth doing
# even when we have decided not to split it.
from .decompose import _QUESTION_WORD as _ASK_RE  # noqa: E402
from .decompose import _SPLIT_HINT as _JOIN_RE    # noqa: E402


def _is_compound(question):
    q = question or ""
    return bool(_JOIN_RE.search(q)) and len(_ASK_RE.findall(q)) >= 2

# X vs Y where X and Y are owned by DIFFERENT domains - "requested vs recorded",
# "scheduled vs filled in". Same word, opposite meaning to the value comparison
# above, and the difference is which domains the two sides belong to, which the
# generated term index can answer and a regex cannot.
_VS_RE = re.compile(r"\b(\w+)\s+(?:vs\.?|versus)\s+(\w+)\b", re.I)


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
    # INFLECT THESE TOO. It knew "came down" and not "come down", so "did the
    # numbers actually come down after that" missed the gate, went to a plain SQL
    # worker, and got answered by averaging every value in
    # datasheet_measurement together - frequencies, limits and margins in one
    # mean, which is not a quantity. metric_delta had the per-frequency answer
    # and was never called.
    r"|c[oa]me down|coming down|went down|go(?:ing|es)? down|dropped"
    r"|reduced by|brought .{0,20}down"
    # A reading against its limit is a measurement question however it is
    # phrased. This branch used to require the word "frequency" within thirty
    # characters, so "what were the actual readings that went over the limit"
    # matched nothing at all.
    r"|reading[s]?\b.{0,40}\b(?:limit|over|above|breach\w*|exceed\w*|margin)\b"
    r"|\b(?:over|above|exceed\w*|breach\w*)\b.{0,25}\blimit\b"
    r"|\bworst\b.{0,25}\b(?:reading|margin|frequenc\w*|breach\w*)\b"
    r"|over (?:time|its tests|the campaigns)|trend"
    # Was `(?:which|what) .{0,30}frequenc`, which is any question with
    # "frequency" within thirty characters of what/which. It matched "What
    # supply voltage and frequency should be tested for job IEC-EMC-004" - a
    # request-side lookup - and dragged it into the datasheets worker, beating
    # the correct signal from "job". A frequency question is only an insight
    # question when it asks which frequency something HAPPENED at, so the
    # measurement context is now required rather than assumed.
    r"|(?:which|what) .{0,30}frequenc\w*\s.{0,30}"
    r"\b(?:peak|peaked|breach|breached|exceed|exceeded|fail|failed|worst|"
    r"margin|highest|maximum|limit|emission)\b"
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
    #
    # INFLECT THE VERB. This was "sent back" only, so "who is sending back the
    # most work in peer review" matched nothing, went to the semantics worker -
    # which cannot see datasheet_status_history - and came back "I cannot answer
    # yet, that table is not in this catalog". Before that it had reached a worker
    # that could see iec_emc_requests instead and answered "there are zero
    # rejections logged in peer review" when six existed. One phrasing, two
    # different wrong answers, and the only thing wrong with the question was the
    # tense.
    #
    # People do not conjugate to match a regex. Cover the forms of the verb and
    # the ways a reviewer gets named, because the rejection axis is half of what
    # this feature is for.
    r"|sen[dt]s? back|sending back|sent back"
    r"|bounce[ds]?|bouncing|kicked back|pushed back|knocked back"
    r"|returned (?:it |them |the \w+ )?to the (?:engineer|lab)"
    r"|rejected in (?:peer )?review|peer[- ]review reject"
    r"|(?:why|reason).{0,30}reject"
    # A reviewer named with a VERB of deciding, or with an aggregate. Not a bare
    # "reviewer": "Who is the peer reviewer on the CE test for IEC-EMC-004" is a
    # lookup that belongs to the schedule worker, and matching review\w* here
    # dragged it into datasheets and cost a routing case.
    r"|(?:who|which reviewer).{0,40}\b(?:reject\w*|approv\w*|sen[dt]s? back)\b"
    r"|reviewer.{0,30}\b(?:most|count|how many|load|activity|busiest)\b"
    r"|\b(?:review rounds?|review history)\b"
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


# --------------------------------------------------------------------------
# scoring a question against the schema's own vocabulary
# --------------------------------------------------------------------------
# _DOMAIN_WORDS above is hand-written, and that was the whole problem: it knew
# 60-odd words while the schema uses hundreds, so questions phrased in the
# lab's actual nouns matched NOTHING. "What accessories were declared" scored
# zero against a schema containing a table called iec_emc_request_accessories.
# Measured on the four questions that exposed this, three matched no domain
# word at all and the orchestrator guessed - wrongly, every time.
#
# So the generated index is added to the hand list rather than replacing it.
# That direction matters. The hand list carries phrasing no column name
# contains - tco, requester, workload, signoff, "in progress" - and a
# replacement measured 7/12 against the old 8/12, breaking four questions that
# already worked. A union can only ever add coverage.
_WORD_RE = re.compile(r"[a-z]{3,}")

# A question needs at least this much evidence before it skips the
# orchestrator, and must beat the runner-up by this margin. Both are
# deliberately above "one weak hit": returning None costs a few model calls,
# returning the wrong domain costs a wrong answer, because a worker cannot see
# outside its allowlist and reports an absence rather than a gap.
ROUTE_FLOOR = 1.0
ROUTE_MARGIN = 0.75
MAX_ROUTED_CHARS = 220


def _term_of(word):
    """The indexed term this question word refers to, or None.

    People write plurals; column names mostly do not. The index derives `job`
    from job_number, so "which JOBS are behind" matched nothing at all - while
    "cables" happened to work only because that table is named plural. One is
    not more correct than the other, so both directions are tried rather than
    relying on which spelling the schema author picked.
    """
    for candidate in (word, word[:-1], word[:-2], word + "s"):
        if len(candidate) > 2 and candidate in DOMAIN_TERMS:
            return candidate
    return None


def _spans_domains(question):
    """True for "X vs Y" where X and Y belong to different workers.

    Decided from the term index, not from the word "vs": two values of one
    column compared against each other is one query, and the whole point of
    this function is that the same phrasing means both things.
    """
    for left, right in _VS_RE.findall(question or ""):
        a = set(DOMAIN_TERMS.get(left.lower(), ()))
        b = set(DOMAIN_TERMS.get(right.lower(), ()))
        if a and b and not (a & b):
            return True
    return False


def domain_scores(question):
    """{domain: score} - how much of this question each worker owns.

    Public because the orchestrator needs it too. When no single domain wins,
    the ranking is still the best available evidence about where to look, and
    the orchestrator previously had none: asked to count shielded cables it
    picked the equipment inventory, which has no cables in it.
    """
    q = (question or "").lower()
    scores = {}
    for domain, words in _DOMAIN_WORDS.items():
        for w in words:
            if re.search(r"\b%s\b" % re.escape(w), q):
                scores[domain] = scores.get(domain, 0.0) + 1.0
    seen = set()
    for word in set(_WORD_RE.findall(q)):
        term = _term_of(word)
        if not term or term in seen:
            continue
        seen.add(term)
        weight = term_weight(term)
        if not weight:
            continue
        for domain in DOMAIN_TERMS[term]:
            scores[domain] = scores.get(domain, 0.0) + weight
    return scores


MAX_HINT_TERMS = 8
MAX_HINT_PLACES = 3


def schema_hint(question):
    """Where in the schema this question's own words appear, or "".

    For the case single_domain() declines. The orchestrator then has to pick a
    worker with nothing to go on but tool descriptions, and it picks badly:
    asked to count shielded cables it chose the equipment inventory, which
    contains no cables, and reported "0 shielded and 0 unshielded" against 29
    rows. It was not guessing wildly - "cable" sounds like equipment. It simply
    had no way to know the word occurs in iec_emc_request_cables.cable_value.

    This is not an instruction about which worker to use. It is the evidence,
    with the domain that owns each place named, so the choice can be made from
    the schema rather than from what the words sound like.
    """
    q = (question or "").lower()
    matched, seen = [], set()
    for word in sorted(set(_WORD_RE.findall(q))):
        term = _term_of(word)
        if not term or term in seen or not term_weight(term):
            continue
        places = TERM_SOURCES.get(term) or ()
        if not places:
            continue
        seen.add(term)
        rendered = ["%s%s" % (table, "." + column if column else "")
                    for table, column in places[:MAX_HINT_PLACES]]
        matched.append((word, rendered, DOMAIN_TERMS.get(term, ())))
        if len(matched) >= MAX_HINT_TERMS:
            break
    if not matched:
        return ""

    lines = ["\n## Where this question's words appear in the schema",
             "Matched by name against the catalog - evidence, not an instruction.",
             "A word can be recorded somewhere that is not where it sounds like it",
             "should be, and this is the only way to see that before querying."]
    for word, places, owners in matched:
        lines.append("  %-16s %s   [%s]"
                     % (word, ", ".join(places), "/".join(owners)))
    ranked = sorted(domain_scores(q).items(), key=lambda kv: -kv[1])[:3]
    if ranked:
        lines.append("Weight by domain: %s"
                     % ", ".join("%s %.1f" % (d, v) for d, v in ranked))
    lines.append("Dispatch to a worker that OWNS the place the answer is in. If the "
                 "places span two workers, send a sub-question to each.")
    return "\n".join(lines) + "\n"


def single_domain(question):
    """The one domain that owns this question, or None if it is not clear-cut."""
    q = (question or "").lower()
    if not q:
        return None
    # Checked before _CROSS_DOMAIN: "why did this PRODUCT fail its TESTS" names
    # two domains and reads as cross-domain, but analyse_history spans them
    # itself, so the datasheets worker can answer the whole thing alone.
    if is_insight(q):
        return "datasheets"
    if _CROSS_DOMAIN.search(q) or _spans_domains(q) or _is_compound(q):
        return None
    if len(q) >= MAX_ROUTED_CHARS:      # long and rambling: let the orchestrator plan
        return None
    ranked = sorted(domain_scores(q).items(), key=lambda kv: -kv[1])
    if not ranked or ranked[0][1] < ROUTE_FLOOR:
        return None
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < ROUTE_MARGIN:
        return None
    return ranked[0][0]


# --------------------------------------------------------------------------
# does the question name anything in particular?
# --------------------------------------------------------------------------
# Both the orchestrator prompt and the worker prompt already say "ONLY ask when
# the user NAMED something and the name is ambiguous". Both were ignored, twice,
# in one measured run:
#
#   "Which products failed the standard, and why?"        -> "do you want only the
#        latest datasheet per product, or all historical failed datasheets?"
#   "A datasheet was rejected because a calibration date was missing. What did
#    the engineer change afterwards?"  -> "please specify the product name"
#
# Neither question names anything. The second had already been handed a written
# redirect by the primitive it reached for. So the instruction is not missing -
# it is losing, and the fix that works in this package for a losing instruction
# is to state it against THIS question instead of leaving it in a standing prompt
# thousands of tokens earlier. Same reasoning as CAUSAL_DIRECTIVE above.
#
# Note the second failure mode the old wording did not cover at all: asking
# about SCOPE ("all or just the latest?") rather than about identity. That is not
# a name question, so a rule about names never applied to it.
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")
_IDENTIFIER_RE = re.compile(r"\b[A-Z]{2,}[-_][A-Z0-9]+[-_]?\d*\b|\b\d{4,}\b")
# Sentence openers and lab words that are capitalised without naming anything.
_NOT_A_NAME = frozenset((
    "which", "what", "who", "when", "where", "how", "why", "does", "did", "are",
    "there", "the", "this", "that", "any", "all", "and", "for", "was", "were",
    "have", "has", "give", "show", "list", "tell", "can", "could", "would",
    "draft", "approved", "rejected", "pass", "passed", "fail", "failed",
    "peer", "review", "test", "tests", "job", "jobs", "lab", "engineer",
    "engineers", "datasheet", "datasheets", "equipment", "instrument",
    "calibration", "maintenance", "standard", "product", "products", "total",
))


def names_something(question):
    """True when the question names a particular person, job, product or code.

    Deliberately generous about what counts as a name: a false positive here
    only means the anti-clarification directive is withheld from a question that
    did not need it, while a false negative injects a "do not ask" instruction
    into a question where asking WAS the right move.
    """
    q = question or ""
    if _IDENTIFIER_RE.search(q):
        return True
    for m in _PROPER_NOUN_RE.finditer(q):
        if m.group(0).lower() in _NOT_A_NAME:
            continue
        if m.start() == 0 or q[:m.start()].rstrip().endswith((".", "?", "!")):
            continue                       # sentence-initial capital, not a name
        return True
    return False


# --------------------------------------------------------------------------
# A REFERENCE WITH NOTHING IN THIS MESSAGE TO REFER TO
# --------------------------------------------------------------------------
# This is the single largest cause of wrong answers in the audit log, and it
# always fails the same way: the question says "this product", nothing resolves
# it, and the pre-computed LAB-WIDE figure is handed over as though it answered.
# Measured on real turns - "Is this test requests are properly filled" was
# answered "77 tests unfilled" for a product with 0 unfilled out of 3.
#
# So the check is not "is this a follow-up" (unknowable from one message) but
# "does this message contain a referring word with no antecedent of its own".
# That IS knowable, and when it is true the honest move is to resolve the
# reference from conversation history or ask - never to substitute a total.
#
# Four exemptions, each closing a measured false positive:
#   named entity here      "why did CE fail for Mihirs Product" - self-contained
#   explicit lab scope     "how many in total across the lab" - the total IS it
#   same-sentence link     "what is out of calibration and does it affect..."
#                          - "it" is the thing named two words earlier
#   generic nouns          "this database", "this week", "this report" - "this"
#                          modifying the system or a time span refers to nothing
#                          in the data
_DANGLING_REF_RE = re.compile(
    r"\b(?:these|those|them|it|its|that one|this one|the above|"
    r"the other one|the same one|same as before|as before|"
    r"the (?:previous|last) "
    r"(?!day\b|days\b|week\b|weeks\b|month\b|months\b|quarter\b|quarters\b|"
    r"year\b|years\b|hour\b|hours\b)\w+|"
    r"the (?:same|one) (?:product|test|campaign))\b"
    # Bare "this <noun>". Without this branch the check caught 2 of 9 real
    # failures: every other one said "this product" / "this test requests" /
    # "this in review", and "this" alone was not in the list. With it, 9 of 9,
    # and the generic-noun exclusions keep "this database" and "this week" out.
    r"|\bthis\s+(?!lab\b|database\b|system\b|app\b|tool\b|week\b|month\b|"
    r"year\b|quarter\b|day\b|report\b|file\b|document\b|page\b|time\b|"
    r"session\b|screen\b)\w+",
    re.I)

# A TCO/job id or a Capitalized Multi-Word phrase named IN THIS message: either
# means the reference has a same-message antecedent and needs nothing carried.
_NAMED_ENTITY_HERE_RE = re.compile(
    r"\b[A-Za-z]{2,}-[A-Za-z]{2,}-\d+\b|"
    r"\b(?:[A-Z][a-z]+\s){1,4}[A-Z][a-z]+\b")

_GLOBAL_SCOPE_RE = re.compile(
    r"\bacross (?:the )?(?:lab|every|all)\b|\ball products?\b|"
    r"\bthe whole lab\b|\bin total\b|\boverall\b|"
    r"\bevery (?:product|job|request)\b|\bhow many.{0,20}(?:in total|altogether)\b",
    re.I)

_SAME_SENTENCE_LINK_RE = re.compile(
    r"\b(?:and|but|or)\s+(?:does|is|are|was|were|will|would|can|could|has|"
    r"have|had)\s+(?:it|its|they|them|these|those)\b", re.I)


def depends_on_earlier_turn(question):
    """True when the question refers to something it does not itself name.

    Says nothing about whether the reference CAN be resolved - only that it
    needs resolving. The caller decides what to do with that: orchestrator
    checks conversation history first and stands this down when the antecedent
    is recoverable, because a resolvable follow-up is a normal question.
    """
    q = question or ""
    if not _DANGLING_REF_RE.search(q):
        return False
    if _NAMED_ENTITY_HERE_RE.search(q):
        return False
    if _GLOBAL_SCOPE_RE.search(q):
        return False
    if _SAME_SENTENCE_LINK_RE.search(q):
        return False
    return True


UNRESOLVED_REFERENCE_DIRECTIVE = """
## THIS QUESTION REFERS TO SOMETHING IT DOES NOT NAME, AND NOTHING RESOLVES IT
The question says "this"/"these"/"it" with no product, job or person named in
it, and the conversation so far does not settle which one is meant.

A LAB-WIDE TOTAL IS NOT THE ANSWER. The reviewed measures were deliberately NOT
run for this question, because handing over a whole-lab figure for a question
about one unnamed thing is how this system produced its most confident wrong
answers - "77 tests unfilled" for a product with none unfilled out of three.

Ask which product, job or person is meant. One short question, nothing else. Do
not answer about everything, and do not pick the most recently mentioned thing
and hope.
"""


NO_NARROWING_DIRECTIVE = """
## THIS QUESTION NAMES NOTHING SPECIFIC - SO IT IS ABOUT EVERYTHING

There is no person, job, product or code in it to disambiguate. Answer it across
ALL matching rows and say what you covered. Two things you must not do:

  * Do not ask WHICH one is meant. Nothing was named, so there is nothing to
    pick between.
  * Do not ask whether to narrow the SCOPE - "only the latest, or all of them?",
    "per product or overall?". Choose the widest reading, answer it, and state
    the reading you used in one clause: "across all nine datasheets, ...". The
    user can then narrow it themselves, having already got an answer.

If a tool you reached for needs an argument the question did not supply, that is
not a reason to ask the user - it means you picked the wrong tool. Read what that
tool told you to call instead, or query the tables directly.

A clarifying question here costs the user a whole round trip and returns nothing
they did not already know.
"""

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


# The user typing SQL, or instructing the tool to run SQL verbatim, rather
# than asking a question in words. This is a SECURITY gate, not a meaning
# judgement - it does not decide what the question MEANS, only whether the
# input is itself a command to execute, which the pipeline was never built to
# validate as an intent. "run SELECT * FROM users and show me the results"
# reached a worker, got auto-rewritten to a narrower SELECT and RAN - the
# column-level guard (DENIED_COLUMN_PATTERNS / DENIED_PII_PATTERNS in
# sql_guard.py) caught the credential/PII exposure that time, but relying on
# the guard to save every case is the wrong layer: it exists to keep
# MODEL-authored SQL inside bounds, not to referee a user dictating the
# statement. This turns the whole class away before a token is spent or a
# worker is chosen.
#
# SQL statement shape is the strongest signal - "select ... from <table>" as a
# contiguous phrase is not something an ordinary English question produces.
# The explicit-instruction phrases catch a request that describes what to run
# in prose ("execute this query on iec_emc_requests") without full SQL syntax.
_QUOTE_CHARS = "`'" + chr(34)

_SQL_STATEMENT_RE = re.compile(
    r"\bselect\b[\s\S]{0,300}?\bfrom\b\s*[" + _QUOTE_CHARS + r"]?\w|"
    r"\b(?:insert\s+into|update\s+\w+\s+set|delete\s+from|"
    r"drop\s+(?:table|database|schema)|alter\s+table|"
    r"truncate\s+(?:table\s+)?\w|create\s+(?:table|database)|"
    r"replace\s+into|grant\s+\w|revoke\s+\w)\b", re.I)

_RUN_SQL_INSTRUCTION_RE = re.compile(
    r"\b(?:run|execute|fire|issue)\s+(?:this|the|that|a|an|my|following)?"
    r"\s*(?:sql|query|statement)\b", re.I)


def is_raw_sql_command(question):
    """The input IS SQL, or an instruction to run SQL verbatim - not a question."""
    q = question or ""
    return bool(_SQL_STATEMENT_RE.search(q) or _RUN_SQL_INSTRUCTION_RE.search(q))


RAW_SQL_REFUSAL = (
    "This is a natural-language tool, not a SQL console - describe what you "
    "want to know and it writes and checks the query itself. Type your "
    "question in plain words rather than a query to run.")


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
