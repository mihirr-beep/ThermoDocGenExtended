# -*- coding: utf-8 -*-
"""What the question is asking for, written down before any SQL exists.

WHY THIS EXISTS
---------------
The failures this package documents are not SQL failures. The SQL usually
parses, runs, and returns rows. What goes wrong is earlier and quieter: the
question asked about tests assigned to someone and the query counted tests
requested; it asked how many datasheets were submitted and the answer came
from the review table. The number is real, it is in the ledger, and the
grounding check passes it, because value-matching cannot see that the query
answered a different question.

You cannot check that without writing down what was meant. A plan is that
written form - operation, subject, state, entity, scope - produced BEFORE the
SQL and carried alongside it. It gives three things nothing else could:

  * the guard something to reject (plan_guard runs on this, not on prose);
  * the worker a target ("your SQL must implement this");
  * the verifier something to compare the executed query against, so
    "answered the wrong question" becomes a detectable state rather than an
    invisible one.

DETERMINISTIC FIRST
-------------------
Most of a plan is already decided by machinery that exists and has been
measured. scope.detect knows the corpus. semantics.resolve knows what the
judgement words mean, and has run the SQL already. table_retriever knows which
tables. probes.resolve_entity knows whether the name is real. intent knows the
question kind and the domain.

So the planner reads those first and only calls a model for the parts genuinely
left over - the operation and subject, when the question's own grammar does not
settle them. That keeps the common case free, keeps the plan stable across
runs, and means a model outage degrades the plan rather than removing it.

NEVER A GATE
------------
plan() returns None rather than raising, and a None plan means the pipeline
behaves exactly as it did before this module existed. A planner that could
block an answerable question would be a worse bug than the one it fixes.
"""
import json
import os
import re

from . import scope as scope_mod
from . import table_retriever

# --------------------------------------------------------------------------
# vocabulary
# --------------------------------------------------------------------------
# Kept small on purpose. Every value here has to mean something to plan_guard
# and to the verifier, and a taxonomy nobody checks is documentation, not code.

OPERATIONS = ("COUNT", "LIST", "AGGREGATE", "COMPARE", "EXPLAIN", "DESCRIBE")

SUBJECTS = ("TEST", "DATASHEET", "REQUEST", "PRODUCT", "ENGINEER",
            "EQUIPMENT", "MEASUREMENT", "REVIEW", "SCHEMA")

# Order matters: the first match wins, and the more specific patterns are
# first. "how many of the tests listed" is a COUNT even though it says listed.
_OPERATION_RES = (
    ("COUNT", re.compile(
        r"\bhow\s+many\b|\bnumber\s+of\b|\bcount\s+(?:of|the)\b|^\s*count\b"
        r"|\btotal\s+(?:number|count)\b", re.I)),
    ("COMPARE", re.compile(
        r"\bcompare[d]?\b|\bversus\b|\bvs\.?\b|\bdifference\s+between\b"
        r"|\bbetter\s+than\b|\bmore\s+than\s+\w+\s+(?:did|has|does)\b"
        r"|\bbefore\s+and\s+after\b|\bchanged?\s+between\b", re.I)),
    ("EXPLAIN", re.compile(
        r"\bwhy\b|\bwhat\s+caused\b|\bwhat\s+changed\b|\breason\s+for\b"
        r"|\bexplain\b|\bhow\s+come\b|\bwhat\s+happened\b", re.I)),
    ("DESCRIBE", re.compile(
        r"\bwhere\s+is\b|\bwhich\s+(?:table|column|field)\b|\bwhat\s+column\b"
        r"|\bis\s+\w+\s+(?:stored|recorded|kept)\b|\bwhere\s+.{0,30}stored\b", re.I)),
    ("AGGREGATE", re.compile(
        r"\bbreak\s*down\b|\bgroup(?:ed)?\s+by\b|\bper\s+\w+\b|\baverage\b"
        r"|\bmean\b|\bsum\s+of\b|\bhighest\b|\blowest\b|\bmost\b|\bleast\b"
        r"|\brank(?:ed|ing)?\b|\btop\s+\d+\b", re.I)),
    ("LIST", re.compile(
        r"\bwhich\b|\bwhat\s+are\b|\blist\b|\bshow\s+me\b|\bwho\s+(?:is|are|has|have)\b"
        r"|\bname\s+the\b|\bgive\s+me\b|\bwhat\s+\w+s\b", re.I)),
)

# Subject detection. A question can name several nouns; the one that decides
# the subject is what is being COUNTED or LISTED, which in English is usually
# the noun nearest the operation word. Rather than parse, each subject carries
# the words that only ever refer to it, and ties break by the order below -
# most specific first.
_SUBJECT_RES = (
    ("SCHEMA", re.compile(
        r"\b(?:table|column|field|schema|stored|recorded\s+in)\b", re.I)),
    ("MEASUREMENT", re.compile(
        r"\bmeasurement|reading|frequenc|amplitude|margin|dB\b|limit\b", re.I)),
    ("REVIEW", re.compile(
        r"\bpeer\s+review|reviewer|rejected|approval|sign(?:ed)?\s*-?off\b", re.I)),
    ("EQUIPMENT", re.compile(
        r"\bequipment|instrument|calibrat|maintenance|asset\b", re.I)),
    ("DATASHEET", re.compile(
        r"\bdatasheet|sheet\b|filled|submitted\b", re.I)),
    ("ENGINEER", re.compile(
        r"\bengineer|tester|who\b|person|people|staff|workload\b", re.I)),
    ("REQUEST", re.compile(
        r"\brequest|job\b|tco\b|campaign|customer\b", re.I)),
    ("PRODUCT", re.compile(
        r"\bproduct|eut\b|unit\s+under\s+test|model\b", re.I)),
    ("TEST", re.compile(
        r"\btest|scheduled|assigned\b", re.I)),
)

# A name in quotes, a TCO/job id, or a capitalised run of words. The same
# shapes decompose.py looks for, because they are what people actually type.
_TCO_RE = re.compile(r"\b[A-Z]{2,}-[A-Z]{2,}-[\w-]*\d+\b")
_QUOTED_RE = re.compile(r"[\"'“‘]([^\"'”’]{3,60})[\"'”’]")
_PROPER_RE = re.compile(r"\b(?:[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+){0,4})\b")

# Words that are capitalised at the start of a sentence or by convention but
# are never an entity. Without this every question beginning "How" proposes
# "How" as a product name.
_NOT_AN_ENTITY = frozenset("""
how what which who when where why is are was were do does did can could
show list give tell me the a an of for in on at to and or but if then
count total number all any some many much more most less least
test tests datasheet datasheets request requests job jobs product products
engineer engineers equipment instrument instruments calibration maintenance
demo real synthetic pass passed fail failed approved rejected draft
emc lab january february march april may june july august september october
november december monday tuesday wednesday thursday friday saturday sunday
""".split())


# "for product X", "on job X", "assigned to X" - the noun that names the kind
# is the reliable signal, not the capitalisation of what follows it. Each entry
# is (kind, pattern); the capture group is the entity text.
_CUE_RES = (
    ("product", re.compile(
        r"\b(?:for|of|on|about)\s+(?:the\s+)?product\s+(.+?)\s*[?.,]?\s*$", re.I)),
    ("product", re.compile(
        r"\bproduct\s+(?:called|named)\s+(.+?)\s*[?.,]?\s*$", re.I)),
    ("job", re.compile(
        r"\b(?:for|on|of|about)\s+(?:the\s+)?(?:job|tco|request|campaign)\s+"
        r"(.+?)\s*[?.,]?\s*$", re.I)),
    ("equipment", re.compile(
        r"\b(?:for|on|of|about)\s+(?:the\s+)?(?:equipment|instrument|asset)\s+"
        r"(.+?)\s*[?.,]?\s*$", re.I)),
    ("person", re.compile(
        r"\b(?:assigned|allocated)\s+to\s+(.+?)\s*[?.,]?\s*$", re.I)),
    ("person", re.compile(
        r"\b(?:for|by)\s+(?:the\s+)?(?:engineer|tester|user|person)\s+"
        r"(.+?)\s*[?.,]?\s*$", re.I)),
    # Bare trailing "for X" with no kind noun. Last, and offered as both a
    # product and a person, because "for smart2pure" and "for krishna" are the
    # same shape and only the database can tell them apart.
    ("product", re.compile(r"\bfor\s+(.+?)\s*[?.,]?\s*$", re.I)),
)

# A trailing phrase that is only stopwords is not a name. Without this,
# "how many tests are assigned for each engineer" proposes "each engineer".
_CUE_STOP = frozenset("""
each every all any some both other others them it this that these those
me us you today yesterday tomorrow now then here there
""".split())


def _cue_entities(question):
    """[(kind, text)] from explicit cue phrases, case-insensitively."""
    out = []
    for kind_hint, rx in _CUE_RES:
        m = rx.search(question or "")
        if not m:
            continue
        text = " ".join((m.group(1) or "").split()).strip(" .,?-")
        if not text or len(text) < 3 or len(text) > 60:
            continue
        words = [w for w in text.lower().split()]
        if all(w in _CUE_STOP or w in _NOT_AN_ENTITY for w in words):
            continue
        if any(kind_hint == k and text.lower() == t.lower() for k, t in out):
            continue
        out.append((kind_hint, text))
        # A bare "for X" is ambiguous between a product and a person; offer
        # both rather than guessing, and let resolution decide.
        if kind_hint == "product" and rx is _CUE_RES[-1][1]:
            out.append(("person", text))
    return out


def _pick(question, table):
    for label, rx in table:
        if rx.search(question or ""):
            return label
    return None


def detect_operation(question):
    return _pick(question, _OPERATION_RES)


def detect_subject(question):
    return _pick(question, _SUBJECT_RES)


def candidate_entities(question):
    """[(kind_hint, text)] worth resolving - most specific first.

    A hint, not a decision: resolve_entity does cross-kind fallback anyway, so
    guessing "product" for something that turns out to be a job costs nothing.
    """
    q = question or ""
    out = []
    for m in _TCO_RE.findall(q):
        out.append(("job", m))
    for m in _QUOTED_RE.findall(q):
        out.append(("product", m.strip()))
    # Cue phrases first, and they are case-INSENSITIVE. This is the fix for a
    # question that got a confidently wrong answer:
    #
    #     "how many test assigned for product vantage water purifier"
    #
    # Capitalisation was the only thing marking an entity, so a lower-case
    # question named none. With no entity the lab-wide measures ran, the model
    # was handed 40 / 16 / 6 for the whole lab, worked out that Vantage has no
    # real records, and printed "0, 0, 0" above three SELECTs that returned
    # 40, 16 and 6.
    #
    # People type in lower case. Requiring a capital letter to notice a product
    # name means the entity layer works in tests and not in the chat box.
    for kind_hint, text in _cue_entities(q):
        # Deduplicated on (kind, text), not text alone. The same string is
        # legitimately offered twice - "for krishna" is a person and "for
        # smart2pure" is a product and neither can be told apart without
        # asking the database, so both kinds are tried. Comparing text only
        # dropped the second one and the ambiguity was decided by list order.
        if not any(kind_hint == k and text.lower() == t.lower() for k, t in out):
            out.append((kind_hint, text))
    for m in _PROPER_RE.findall(q):
        text = m.strip()
        words = [w for w in text.split() if w.lower() not in _NOT_AN_ENTITY]
        if not words:
            continue
        text = " ".join(words)
        if len(text) < 3 or text.lower() in _NOT_AN_ENTITY:
            continue
        if any(text == t for _k, t in out):
            continue
        # Two capitalised words is usually a person and one is usually a
        # product, but "Lifecycle Probe" is two words and a product, and
        # guessing person for it lost the one signal that mattered: person
        # carries no scope, so the resolver never noticed the name exists
        # only in the demo corpus and the question got answered about an
        # unrelated real product instead. Both kinds are offered; the caller
        # takes the first that resolves.
        if len(words) >= 2:
            out.extend([("person", text), ("product", text)])
        else:
            out.append(("product", text))
    return out[:6]


def plan(question, kind=None, domain=None, resolved=None, entity=None,
         scope=None, tables=None, model=None):
    """A structured query plan, or None when one cannot be formed.

    `resolved` is semantics.resolve()'s output (already executed, so its
    metrics carry values). `entity` is a resolve_entity payload dict, when the
    caller already resolved one. Everything else is derived here.
    """
    try:
        return _plan(question, kind, domain, resolved, entity, scope, tables, model)
    except Exception:  # noqa: BLE001 - a missing plan must never cost an answer
        return None


def _plan(question, kind, domain, resolved, entity, scope, tables, model):
    q = (question or "").strip()
    if not q:
        return None

    sc = scope or scope_mod.detect(q)
    retrieved = tables if tables is not None else table_retriever.retrieve(q, domain=domain)

    operation = detect_operation(q)
    subject = detect_subject(q)

    state, state_definition, metric_names = _state_from(resolved)

    if not operation or not subject:
        guessed = _ask_model(q, model)
        operation = operation or guessed.get("operation")
        subject = subject or guessed.get("subject")

    # intent.classify decides schema-vs-data, and it decides it AFTER the model
    # has had its say, not before.
    #
    # This ordering is the fix for a regression the eval caught. "What coupling
    # method was used on the CE datasheets?" is a question about a VALUE. The
    # regexes here did not settle its operation, so it went to the model, which
    # read "what ... method" and answered DESCRIBE. The plan then told the
    # worker to answer from the schema catalog and not query rows - so it
    # didn't, and a question that had been answerable came back as "I did not
    # run any query for that".
    #
    # intent.classify had it right the whole time. It is regex, it is measured,
    # and the coupling-method question is the exact case it was written for
    # (see intent.py's docstring). So it wins: a DATA question cannot carry
    # operation DESCRIBE, whatever the model thinks.
    if kind == "schema":
        operation, subject = "DESCRIBE", "SCHEMA"
    elif operation == "DESCRIBE" or subject == "SCHEMA":
        operation = detect_operation(q) or "LIST"
        if operation == "DESCRIBE":
            operation = "LIST"
        if subject == "SCHEMA":
            subject = detect_subject(q)
            if subject == "SCHEMA":
                subject = None

    out = {
        "operation": operation,
        "subject": subject,
        "scope": sc,
        "scope_definition": scope_mod.describe(sc),
        "state": state,
        "state_definition": state_definition,
        "candidate_metrics": metric_names,
        "source_tables": [t["name"] for t in retrieved],
        "table_reasons": {t["name"]: t["reason"] for t in retrieved},
        "domain": domain,
        "question_kind": kind,
        "aggregation": operation if operation in ("COUNT", "AGGREGATE") else None,
        "grouping": _grouping(q),
        "date_range": _date_range(q),
        "entity": _entity_slot(entity),
    }
    return out


def _state_from(resolved):
    """The business state, taken from the semantic layer rather than invented.

    Returns (state, definition, [metric names]). When more than one reviewed
    measure matches the term, ALL of them are carried: the plan records that
    the question was ambiguous, which is what lets the guard ask for a
    clarification instead of the worker silently picking one.
    """
    if not resolved or not resolved.get("ambiguous"):
        return None, None, []
    item = resolved["ambiguous"][0]
    metrics = item.get("metrics") or []
    names = [m.get("name") for m in metrics if m.get("name")]
    if not names:
        return None, None, []
    state = str(item.get("term") or "").upper().replace(" ", "_")
    if len(metrics) == 1:
        return state, metrics[0].get("label"), names
    return state, " OR ".join(
        "%s (%s)" % (m.get("name"), m.get("label")) for m in metrics), names


def _entity_slot(entity):
    """The resolved entity, reduced to what a plan needs to carry."""
    if not entity or not isinstance(entity, dict):
        return None
    cands = entity.get("candidates") or []
    return {
        "type": (entity.get("entity_type") or entity.get("kind") or "").upper() or None,
        "value": entity.get("input") or entity.get("looked_for"),
        "resolved": bool(cands),
        "match_count": entity.get("match_count", len(cands)),
        "ambiguity_count": entity.get("ambiguity_count"),
        "matched_how": entity.get("matched_how"),
        "excluded_by_scope": bool(entity.get("excluded_by_scope")),
        "identifiers": [c.get("tco_id") or c.get("username") or c.get("name")
                        for c in cands[:6]],
    }


_GROUP_RE = re.compile(
    r"\b(?:by|per|for\s+each|broken\s+down\s+by|grouped\s+by)\s+"
    r"(engineer|person|tester|test|test\s+code|product|job|status|result|"
    r"month|week|day|year|reviewer|equipment)\b", re.I)


def _grouping(question):
    m = _GROUP_RE.search(question or "")
    return m.group(1).lower() if m else None


_DATE_RE = re.compile(
    r"\b(last|this|next)\s+(week|month|quarter|year)\b"
    r"|\b(?:in|during|for)\s+(january|february|march|april|may|june|july|"
    r"august|september|october|november|december)\b"
    r"|\b(?:since|after|before|until)\s+(\d{4}-\d{2}-\d{2}|\d{4})\b"
    r"|\b(today|yesterday|overdue)\b", re.I)


def _date_range(question):
    m = _DATE_RE.search(question or "")
    return m.group(0).strip().lower() if m else None


# --------------------------------------------------------------------------
# the model fallback
# --------------------------------------------------------------------------
# Deliberately tiny. It is asked for two enum values and nothing else - not the
# tables, not the state, not the entity, all of which are already known more
# reliably than a model could guess them. A narrow question is one a cheap
# model answers correctly, and a wrong answer here costs a mislabelled plan
# rather than a wrong number.

_PROMPT = """Classify this question about an EMC test lab database.

operation - what is being asked for:
  COUNT      how many
  LIST       which ones / who / show me
  AGGREGATE  a breakdown, ranking, average, or per-something grouping
  COMPARE    two things set against each other
  EXPLAIN    why something happened, or what changed
  DESCRIBE   where a value is stored in the database (schema, not data)

subject - what the answer is about:
  TEST DATASHEET REQUEST PRODUCT ENGINEER EQUIPMENT MEASUREMENT REVIEW SCHEMA

Return JSON only: {{"operation": "...", "subject": "..."}}

QUESTION
{question}
"""


def _ask_model(question, model=None):
    """{"operation", "subject"} - {} if the model is unreachable or unusable."""
    if os.environ.get("NLP_NO_PLAN_MODEL") == "1":
        return {}
    try:
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model=(model or os.environ.get("NLP_PLAN_MODEL")
                   or os.environ.get("NLP_SEARCH_MODEL") or "gpt-4o-mini"),
            messages=[{"role": "user",
                       "content": _PROMPT.format(question=question[:1200])}],
            temperature=0, max_tokens=60)
        text = (resp.choices[0].message.content or "").strip()
        blob = re.search(r"\{.*\}", text, re.S)
        data = json.loads(blob.group(0) if blob else text)
    except Exception:  # noqa: BLE001 - the plan degrades, it does not fail
        return {}
    out = {}
    op = str(data.get("operation") or "").strip().upper()
    su = str(data.get("subject") or "").strip().upper()
    if op in OPERATIONS:
        out["operation"] = op
    if su in SUBJECTS:
        out["subject"] = su
    return out


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def prompt_block(plan_dict, verdict=None):
    """The plan, rendered for the worker prompt.

    Phrased as the specification the SQL has to meet, because that is what it
    is used for downstream: the verifier compares the executed query against
    these fields, so a worker that quietly answers a different question is
    caught rather than believed.
    """
    if not plan_dict:
        return ""
    p = plan_dict
    lines = ["STRUCTURED QUERY PLAN - your SQL must implement THIS question, "
             "not a nearby one:"]
    lines.append("  operation   %s" % (p.get("operation") or "(unclear)"))
    lines.append("  subject     %s" % (p.get("subject") or "(unclear)"))
    if p.get("state"):
        lines.append("  state       %s" % p["state"])
        if p.get("state_definition"):
            lines.append("  defined as  %s" % p["state_definition"])
    ent = p.get("entity")
    if ent and ent.get("value"):
        if ent.get("excluded_by_scope"):
            # Distinguished from a plain miss because the two need opposite
            # answers. "Not resolved" means say it does not exist; this means
            # it DOES exist, in the corpus the question excluded. Rendering
            # both as [NOT RESOLVED] told the worker to say there are none,
            # which is the false zero.
            lines.append("  entity      %s %r  [EXISTS ONLY IN THE EXCLUDED "
                         "CORPUS]" % (ent.get("type") or "?", ent.get("value")))
            lines.append("  This name matches only demonstration records, so "
                         "there is nothing in scope to count. Do NOT query for "
                         "it and do NOT answer 'none' or '0' - that says it "
                         "exists here with no work against it. Say its only "
                         "records are demonstration data, and that the user "
                         "can ask for the demo data explicitly.")
        else:
            lines.append("  entity      %s %r%s" % (
                ent.get("type") or "?", ent.get("value"),
                "" if ent.get("resolved") else "  [NOT RESOLVED - do not filter on it]"))
        if ent.get("identifiers"):
            idents = [str(i) for i in ent["identifiers"] if i]
            lines.append("  resolved to %s" % ", ".join(idents))
            # Filter on THESE, not on what the user typed. The whole reason
            # resolution runs before SQL is that the typed name is not a value
            # in any column: asked about "smart2pure", the worker wrote
            # LOWER(product_name) = 'smart2pure' and got zero, because the
            # real names are "Smart2pure 6UV " and "Smart2Pure Pro 16 UVUF FS
            # 60L ". Three zeros, all correctly computed, all meaningless -
            # and a zero from a filter that matched nothing is indistinguishable
            # from a genuine absence.
            lines.append("  Filter on the identifiers above - e.g. "
                         "r.tco_id IN (%s). Do NOT match on the name the user "
                         "typed: it is not a value in any column, and an "
                         "equality test against it returns zero rows that look "
                         "exactly like a real answer of none."
                         % ", ".join("'%s'" % i for i in idents[:4]))
    if p.get("grouping"):
        lines.append("  group by    %s" % p["grouping"])
    if p.get("date_range"):
        lines.append("  date range  %s" % p["date_range"])
    if p.get("source_tables"):
        lines.append("  tables      %s" % ", ".join(p["source_tables"]))
    if p.get("candidate_metrics"):
        # The recommendation flips when the question names an entity. Every
        # reviewed measure is lab-wide, so calling lab_metric for "how many
        # tests are assigned for Smart2Pure" returns the figure for the whole
        # lab - and the model then reports it AS the product's figure. Observed
        # exactly that: 40 / 16 / 6, all three real, all three answering a
        # question nobody asked. It passed grounding, because the numbers were
        # genuinely in the evidence.
        # excluded_by_scope counts as naming an entity. Its `resolved` is False
        # - nothing matched IN SCOPE - and testing only `resolved` let the
        # lab-wide measures back in for exactly the question they ruin: asked
        # about a demo-only product, the worker called lab_metric, got the
        # whole lab's 40 / 16 / 6, and reported 0 for the product with three
        # unrelated caveats attached.
        if ent and (ent.get("resolved") or ent.get("excluded_by_scope")):
            lines.append("  DO NOT call lab_metric for this question. These "
                         "reviewed measures are LAB-WIDE and this question is "
                         "about %s: %s"
                         % (ent.get("value"), ", ".join(p["candidate_metrics"])))
            if not ent.get("excluded_by_scope"):
                lines.append("  Use their DEFINITIONS above and write SQL that "
                             "applies the same rule filtered to this entity. A "
                             "lab-wide total reported as this entity's figure "
                             "is a wrong answer even though every digit is "
                             "real.")
        else:
            lines.append("  reviewed measures that already answer this: %s"
                         % ", ".join(p["candidate_metrics"]))
            lines.append("  Prefer lab_metric(<name>) over writing your own SQL "
                         "for these - the reviewed version is already grounded.")
    if p.get("operation") in ("COUNT", "AGGREGATE") and not (
            ent and ent.get("excluded_by_scope")):
        lines.append("  A %s must come from a query. Do NOT count the "
                     "candidates an entity lookup returned - those are the "
                     "records matching a NAME, not the things being counted. "
                     "Resolving a product to four jobs does not mean four %ss."
                     % (p["operation"].lower(),
                        (p.get("subject") or "item").lower()))
    if verdict and verdict.get("warnings"):
        lines.append("  PLAN WARNINGS - resolve these before answering:")
        for w in verdict["warnings"]:
            lines.append("    - %s" % w)
    return "\n".join(lines)
