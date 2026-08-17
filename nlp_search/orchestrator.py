# -*- coding: utf-8 -*-
"""The orchestrator: plans a question, dispatches it to specialist workers,
and synthesizes an answer that is checked against the evidence before it goes
out.

    question
       |
       v
  ORCHESTRATOR ----> ask_requests    (EMC requests / jobs / scope)
       |        ----> ask_schedule    (who, when, peer review)
       |        ----> ask_datasheets  (what was measured)
       |        ----> ask_inventory   (equipment, calibration)
       |        ----> resolve_entity / list_values   (deterministic, no LLM)
       |
       |   every tool writes the rows it saw into the LEDGER
       v
   synthesis  --->  verify.check(answer, ledger)  --->  answer or honest refusal

The workers are tools, not handoffs, deliberately: a handoff transfers control
and the orchestrator stops being able to combine domains, which is the whole
point for a question like "which of Krishna's tests last month failed". As
tools, it can fan out, re-ask with a narrower brief, and decide when it has
enough.

The database is the only source. Nothing here reads a document, an index or
the model's own memory of the lab.

The SDK is imported lazily so the Flask app still boots when the package or the
OPENAI_API_KEY is missing - the endpoint then degrades to a clear message.
"""
import contextlib
import datetime
import json
import os
import time

from . import decompose, gates, intent, probes, semantics, verify, workers
from .ledger import Ledger

# Domain workers ON by default - measured, not assumed.
#
# The plan argued for collapsing them into one SQL author with the whole
# catalog, on the published finding that filtering a schema which already fits
# the context window hurts more than it helps. That finding did not transfer.
# Same 13 eval cases, same day, same data:
#
#     single author    10/13 correct   23% hallucination   292k tokens   144s
#     domain workers   13/13 correct    0% hallucination   182k tokens   130s
#
# The single author got "how many EMC requests are there" wrong - it answered
# "there are none" against nine rows - and reported 0 CE datasheets for an
# engineer with two. Worse on accuracy, cost AND latency, which is not the
# trade-off anyone predicted.
#
# The cross-domain gap the collapse was meant to fix turned out to be a routing
# bug, not a partitioning one: "which equipment is used most" belongs to the
# datasheets worker (datasheet_equipment), and the orchestrator was sending it
# to inventory. Fixing the tool description fixed the question.
#
# Set NLP_SINGLE_AUTHOR=1 to run the collapsed variant and re-measure.
USE_DOMAIN_WORKERS = os.environ.get("NLP_SINGLE_AUTHOR", "") != "1"

# gpt-5-nano, chosen on measured accuracy at a price that is not the deciding
# factor. Three questions gpt-4o-mini gets wrong or nearly wrong, same prompts,
# same data, same afternoon:
#
#   question                    truth   gpt-4o-mini      gpt-5-nano
#   most-used instruments       4,4,3   nine tied at 4   correct
#   requested tests unfilled    78      12               78
#   requested, never scheduled  64      64               64
#
# mini lost the first one to the datasheet_equipment -> equipment fan-out,
# which RELATIONSHIPS warns about and run_sql now reports; and on the second it
# ignored a reviewed figure of 78 that was already computed and sitting in its
# prompt. nano heeded both.
#
# Cost, measured on those three - which are deliberately the HARD ones, so this
# is an upper bound: $0.00497 a question, about $1.50 a month at 300 questions.
# Its input is a third of mini's and its cached rate a tenth, which pays for a
# lot of reasoning.
#
# THE PRICE IS PAID IN LATENCY, NOT DOLLARS. The hard question took 129 seconds
# against mini's 16. That drove two other changes: the ledger's time budget
# (60s would have cut this question off mid-work) and the chat panel, which
# showed three dots with no sense of progress.
#
# Reasoning effort is deliberately NOT set. 'low' answered the hard question
# 82 instead of 78; 'minimal' stopped it acting at all - it ran the SQL and
# then replied "do you want me to proceed?". The reasoning tokens ARE the
# correct answer. NLP_REASONING_EFFORT overrides if you want to re-measure.
#
# NLP_SEARCH_MODEL switches models per question or per deployment; gpt-4o-mini
# and gpt-4o both still work and neither is sent a reasoning parameter.
DEFAULT_MODEL = "gpt-5-nano"
MAX_TURNS = 16
# Wall-clock budget for one question, handed to the Ledger. 60s was fine
# for gpt-4o-mini; a reasoning model spends that much thinking, and the
# question this default was chosen FOR took 129s. Raised so the budget
# stops runaways rather than stopping normal work.
DEADLINE_S = int(os.environ.get("NLP_DEADLINE_S", "180"))
MAX_QUESTION_CHARS = 2000

# --------------------------------------------------------------------------
# the conversation so far
# --------------------------------------------------------------------------
# This was a single-turn endpoint, and the effect was worse than either
# alternative: the assistant would ASK A CLARIFYING QUESTION it structurally
# could not use the answer to. Asked which product Krishna is assigned tests on,
# it correctly found two different people called Krishna and asked which - and
# the reply "Krishna Gonela" then arrived as a brand-new standalone question with
# the original one gone, so it resolved the name again and asked what the user
# would like to do with this person. Same cause as "List those equipments", where
# "those" had no referent.
#
# HISTORY IS CONTEXT, NOT EVIDENCE. This is the constraint that keeps the
# grounding guarantee intact. The ledger holds the rows THIS question's queries
# returned, and verify.check tests the answer against those. If an earlier turn's
# facts were allowed to ground a claim, a stale number could be re-quoted with a
# verified badge and nobody could tell which turn it came from. So the history is
# given for resolving references - "those", "the first one", a name just chosen -
# and the model is told that every fact must come from a query it runs now.
#
# It also arrives from the browser, so it is untrusted: a client can put whatever
# it likes in a prior "assistant" turn. Treating it as context only means the
# worst a forged turn can do is misdirect a question, not manufacture a fact.
MAX_HISTORY_TURNS = 7
MAX_HISTORY_ANSWER_CHARS = 600


def _history_block(history):
    """The prior turns, as a prompt block. "" when there are none."""
    turns = []
    for turn in (history or [])[-MAX_HISTORY_TURNS:]:
        role = str((turn or {}).get("role") or "").strip().lower()
        text = " ".join(str((turn or {}).get("text") or "").split())
        if not text or role not in ("user", "assistant"):
            continue
        if role == "assistant" and len(text) > MAX_HISTORY_ANSWER_CHARS:
            text = text[:MAX_HISTORY_ANSWER_CHARS] + " [...]"
        turns.append("%s: %s" % ("User" if role == "user" else "You", text))
    if not turns:
        return ""
    return ("\n## The conversation so far, oldest first\n" + "\n".join(turns) + """

Use this to work out WHAT IS BEING ASKED - what "those", "it", "the first one" or
a bare name refers to, and what question a one-word reply is answering. If the
user is answering a question you asked, treat their reply as the missing piece of
the ORIGINAL question and answer that, in full, now. Do not ask again and do not
acknowledge the choice without answering.

EVERY FACT IN YOUR ANSWER MUST COME FROM A QUERY YOU RUN NOW. The rows behind an
earlier answer are not in front of you: you cannot see them, they may have
changed, and quoting one is asserting something you have not checked. Re-run the
query. If an earlier answer turns out to have been wrong, say so plainly.
""")


def _routing_text(question, history):
    """What to route on: a follow-up needs its antecedent to be routable.

    "Krishna Gonela" on its own names a person and no domain, so routing has
    nothing to work with; the question it answers - "which product is Krishna
    assigned tests on" - is what decides the worker. Only the last USER turn is
    borrowed, and only for the routing decision: the question the model is asked
    is still the one the user typed.
    """
    q = (question or "").strip()
    if not history or len(q) > 60:
        return q
    for turn in reversed(history):
        if str((turn or {}).get("role") or "").lower() == "user":
            prior = " ".join(str((turn or {}).get("text") or "").split())
            return ("%s %s" % (prior, q)).strip() if prior else q
    return q

_INSTRUCTIONS = """You are the EMC Test Lab data assistant for Thermo Fisher's test-plan and
datasheet application. Lab admins ask you questions in plain English. You
answer ONLY from this lab's MySQL database, through the specialist workers
below. You have no other source: no documents and no outside knowledge. You are
given the last few turns of the conversation so you can tell what a follow-up
refers to - but they are context, never evidence: every fact you state must come
from a query run in THIS turn.

Today's date is {today}. Timestamps in the database are IST.

## Your workers
- ask_requests   - what customers ASKED FOR: requests/jobs/TCOs, the product
                   under test, tests in scope, declared standards, requester,
                   approval and rejection state. ALSO owns ASKED-FOR vs
                   DELIVERED: it can see the datasheet rows too, so send it
                   "which jobs are behind", "what is outstanding on job X",
                   "requested but never recorded" as ONE sub-question. Do not
                   split those across two workers and compare the answers
                   yourself - it can do the join.
- ask_schedule   - WHO and WHEN: scheduled tests, assigned engineers, workflow
                   status, peer review, users and roles.
- ask_datasheets - what was MEASURED: results, pass/fail, ambient conditions,
                   recorded per-test parameters, individual observation cells,
                   equipment and software used, review history.
- ask_inventory  - the lab's EQUIPMENT: what exists, calibration and
                   maintenance due dates, change history. Also sees which
                   equipment was USED on datasheets, so "was anything used on
                   a test out of calibration" is one sub-question, not two.

And one deterministic lookup you can call yourself:
- resolve_entity(kind, text) - turn a name the user typed into the real rows.

## How to answer a question
1. PLAN. Decide which domains the question touches. "Requested" and "measured"
   are different domains and people conflate them - "what level was the ESD run
   at" is ask_datasheets; "what level did they ask for" is ask_requests.
2. RESOLVE FIRST. If the question names a person, job, product or instrument,
   call resolve_entity BEFORE dispatching, and pass the exact resolved value to
   the worker. If nothing resolves, say there is no such person/job - do not
   send a worker off to count rows matching a name that does not exist.
3. DISPATCH. Send each worker a specific, self-contained sub-question. Give it
   the identifiers you resolved. Send to several workers when the question
   spans domains, then join their answers yourself.

   EVERY factual answer goes through a worker. resolve_entity tells you a name
   is real; it does not tell you how many, or which, or what the result was.
   Never count, total or list from a resolve_entity reply - dispatch and let a
   worker run the actual query, even when the answer looks obvious.
4. FOLLOW UP. If a worker's answer is thin, contradicts another, or raises an
   obvious next question, ask again with a narrower brief. Two or three rounds
   is normal for a complex question.
5. ANSWER. Lead with the direct answer, then a brief supporting line. Write
   PLAIN TEXT - no markdown, no **bold**, no ### headings, no bullet syntax.
   The interface shows your reply verbatim, so markup arrives as literal
   asterisks. A short list is fine as one item per line.

## Lab rules you must follow
These are decisions the lab has made. The data cannot tell you them and you
must not decide them yourself on the fly.
{lab_rules}

## Honesty - this is the part that matters most
Every figure, name, date and status in your answer must have come back from a
worker in THIS conversation. You will be checked against the rows the database
actually returned, and unsupported claims will be removed.

- Never state a number the workers did not report. Do not compute a percentage,
  average or trend unless a worker returned the figures it needs, and say which.
- DO NOT ADD FIGURES FROM DIFFERENT QUERIES TOGETHER. Two counts of different
  things do not sum into anything meaningful - 2 draft datasheets plus 6 tests
  in progress is not "8 outstanding items", it is two separate facts about two
  separate sets that probably overlap. Report each figure with the label that
  belongs to it and leave the arithmetic to the reader.
- NEVER assert that something does not exist, or that a count is zero, unless a
  worker ran the query that shows it. You can see from the schema that a column
  only holds certain values - that is not the same as having checked, and an
  answer of "nothing failed" with no query behind it will be rejected. Dispatch
  first, then report what came back.
- A COUNT of 0 is not the same as "there are none". It usually means a filter
  matched nothing because the value does not exist. Ask the worker to confirm
  the value is real. If it is not, NAME THE REAL VOCABULARY rather than
  answering inside the user's wrong one. Asked how many datasheets are
  "Rejected" when the statuses are Approved and Draft, do not say "none are
  Rejected" - that implies Rejected is a status that happens to be unused. Say
  "datasheets are only ever Approved or Draft; there is no Rejected status.
  Both of his are Approved." The user learns the shape of the data instead of
  being quietly confirmed in a wrong assumption.
- When a name is ambiguous, ASK. If resolve_entity returns more than one
  candidate, list them with something that tells them apart (role, job) and
  ask which is meant. Do not pick one, and do not silently merge them - "2
  datasheets" is a different fact for each of two people, and either answer
  would be wrong half the time.
  A PRODUCT MATCHING SEVERAL JOBS IS NOT THIS. Two people called Sai are two
  people; one product tested four times is one product with a history. When the
  question asks about a product over time - its history, why it failed, what
  changed, whether it improved - several matching jobs ARE the answer, and
  asking which single job was meant returns nothing the user did not already
  know. Only treat multiple matches as ambiguity when they are DIFFERENT THINGS
  that happen to share a name.
- ONLY ask when the user NAMED something and the name is ambiguous. A question
  that names nothing is not ambiguous - it is asking about everything, and the
  answer is a query over the whole table. "Are there tests that were requested
  on A JOB but never scheduled" means ACROSS ALL JOBS; "is AN INSTRUMENT out of
  calibration" means any instrument. An indefinite "a" / "any" / "some" is not
  a blank for you to fill in - answering "which job did you mean?" to a
  question that deliberately did not name one is a non-answer, and the user has
  to ask twice to get something they could have had at once. If there is no
  proper noun in the question, there is nothing to disambiguate: run the query.
  ONE EXCEPTION, and it matters: tco_id is NOT a surrogate key. "IEC-EMC-004"
  is what the lab calls that job out loud, and it is the only identifier a job
  has before a job number is issued. Always show it. The ids to suppress are
  the numeric ones - request_id, planner_entry_id, datasheet_id, user_id.
  Identify a job by its job number and tco_id, NEVER by product name alone:
  several jobs share a product, so "Genpure UV xCAD plus WM is behind" does
  not tell the reader which job to go and look at.
- WRITE FOR A LAB ENGINEER, NOT FOR A DEVELOPER. The reader knows tests,
  jobs, equipment and calibration. They do not know, and must never be shown,
  the names of your tools, your measures, your tables or your columns, and
  never a SQL statement. Write "65 instruments are overdue for maintenance",
  not "Source: maintenance_overdue with include_rows=False", not "SQL shape:
  lab_metric(name='maintenance_overdue')", and not a SELECT. All three have
  been printed to a user. How you found the answer is shown separately by the
  interface; your job is the answer.
- A PRE-COMPUTED FIGURE IS THE ANSWER. WORKING OUT YOUR OWN IS NOT.
  When a DEFINED TERMS block above gives you a number for a phrase in the
  question, that number came from SQL a human wrote and reviewed. Quote it.
  Do not send a worker off to re-derive it and do not report a different
  figure for the same phrase - if your own query disagrees with the reviewed
  one, the reviewed one wins and yours is wrong. Asked how many requested
  tests are still unfilled, the reviewed answer of 68 was sitting in the
  prompt; a worker re-derived it with a broken join and 6 was published.
- IF A WORKER IS REFUSED A TABLE, THAT PART IS UNANSWERED. Do not accept a
  substitute answer to a question you did not ask, and do not present one.
  Say which part could not be answered and why.
- ACCOUNT FOR EVERY ROW. If a worker reports ten items, list ten or say "10
  in total, here are the first 5". Silently dropping half a list is as wrong as
  inventing one, and it is the easier mistake to make.
- An empty column is not an empty world. If a date-filtered question comes back
  with nothing, find out whether that date column is populated at all before
  concluding the events did not happen - "no datasheet records a test date, so
  I cannot answer by month" is the truth; "no tests ran in July" is not.
- If the data is not there, say so plainly and say what IS there instead. "The
  lab records a Pass/Fail per datasheet but no failure reason field" is a good
  answer. An invented reason is not.
- If the question is ambiguous in a way that changes the answer, ask ONE short
  clarifying question instead of guessing.
- If workers disagree, say so and give both figures with their sources.

## Questions about WHY, or about change over time
"Why did it fail", "what changed between the two tests", "which frequencies
improved", "what was fitted before it passed", "has this happened to anything
else" - send these to the datasheets specialist and say in the sub-question
that it should use its analyse_history tool. It has pre-written, checked
analyses for exactly these; a query assembled on the spot has to self-join one
row per measured cell across two campaigns, and gets it wrong quietly.

Two things that both get called "failed", and they are not the same:
  the UNIT failed the standard      - emissions over the limit, EUT reset
  the RECORD was rejected in review - calibration expired, photographs missing
A product can fail the standard on a datasheet that was ALSO sent back for a
missing photograph. Say which one you are answering; if the question could mean
either, give both, because they have different fixes and different owners.

Report the sequence, not a cause. The database records what was measured, what
was fitted and what the reviewer wrote - never why. "A common-mode choke was
fitted between the two tests, and the 0.72 MHz margin improved by 5.3 dB" is
what the evidence supports. "The choke fixed it" is not, however obvious it
looks. An engineer will draw that conclusion themselves and be right; you
stating it as fact is how the tool starts getting believed about things it
cannot know.

## Scope
Answer anything about this lab: requests, jobs, datasheets, results, equipment,
schedules, peer review, standards, users. Interpret casual wording generously -
gadget/instrument -> equipment; tester/engineer -> the assigned engineer;
job/TCO/project -> request; EUT -> the product under test; "pending review" ->
peer-review status. A BROAD question is still a data question, not a reason to give up. "How is the
lab doing", "give me a summary", "any issues" - dispatch to two or three
workers for headline figures (how many jobs and their statuses, how many
datasheets and their results, anything overdue for calibration) and summarise
what comes back. Answering "I could not find that" to a question you never
asked a worker about is the one failure mode worse than being wrong, because
the data was right there.

Words the user brings from their own world map onto this schema: "customer" and
"client" mean the REQUESTER on a request; "report" usually means the datasheet;
"certificate" is not something this system records - say so rather than
declining. If a mapping is genuinely ambiguous, say which reading you took.

Never report a raw database id to a human. If a worker hands you "user_id 5",
ask it for the name.

Decline ONLY when the topic is clearly unrelated to the lab - weather, sports,
general knowledge, current events, coding, creative writing, opinions. Never
answer those from your own knowledge and never send a worker after them.

When you decline, do not just say no. Say in one line that it is outside the
lab data, then say what you DO cover, and if their wording suggests something
in scope, offer that. Someone who asks about "the weather" may want the ambient
conditions on a test - point at it. A dead end teaches the user nothing about
what to ask next, and they stop asking.
"""


def _prepare(question, db_params, ledger, kind, verify_answer=True, history=None):
    """Everything that must happen BEFORE the model is asked anything.

    The gates test what the question assumes; the semantic layer replaces the
    lab's fuzzy words with reviewed definitions and runs their SQL. Both are
    plain SQL, so neither can invent, and a fact established here cannot be
    argued away by whatever the model writes next.

    Returns (agent, prompt_blocks, undefined_terms).
    """
    verdicts = gates.run(question, db_params, ledger=ledger) if verify_answer else []
    resolved = semantics.execute(semantics.resolve(question), db_params, ledger=ledger)
    # A word the semantic layer defines is no longer "undefined" - it has an
    # answer, it just has more than one.
    undefined = [t for t in intent.undefined_terms_in(question)
                 if not any(t in a["term"] for a in resolved.get("ambiguous", []))]
    blocks = (gates.prompt_block(verdicts), semantics.prompt_block(resolved))
    if undefined:
        blocks = blocks + (intent.UNDEFINED_DIRECTIVE.format(
            terms=", ".join("'%s'" % t for t in undefined)),)
    # Injected per question rather than left in the standing prompt: the phrasing
    # "what was the confirmed root cause" supplies its own framing, and a static
    # rule thousands of tokens earlier loses to it.
    if intent.asks_for_cause(question):
        blocks = blocks + (intent.CAUSAL_DIRECTIVE,)
    # A question that names nobody is asking about everything. Both prompts say
    # so already and both were ignored - measured twice in one run, once as
    # "which one did you mean" and once as "all of them or just the latest".
    # Stated against THIS question it stands a chance; left in a standing prompt
    # it lost. Injected for workers too, since routing now sends more questions
    # straight to one.
    if not intent.names_something(question):
        blocks = blocks + (intent.NO_NARROWING_DIRECTIVE,)
    # First, so the model reads what was already said before the rest of the
    # per-question material. Workers get it too: routing sends most questions
    # straight to one, and a follow-up is exactly the kind of short question that
    # routes cleanly.
    conversation = _history_block(history)
    if conversation:
        blocks = (conversation,) + blocks

    # A question that plainly belongs to one domain goes straight to that
    # worker. The orchestrator's own turns were most of the cost - it spends
    # one deciding to dispatch and more relaying the answer back, around a
    # single worker loop that was already going to do the work.
    if kind != intent.SCHEMA:
        domain = intent.single_domain(_routing_text(question, history))
        if domain:
            agent = workers.build_standalone(domain, db_params, ledger,
                                             extra_blocks=blocks)
            return agent, blocks, undefined

    # No single worker owns it, so the ORCHESTRATOR chooses - and it was
    # choosing from tool descriptions alone. Asked to count shielded cables it
    # picked inventory, which holds no cables, and answered 0 against 29 rows.
    # Hand it the places the question's own words actually occur.
    hint = intent.schema_hint(question)
    if hint:
        blocks = blocks + (hint,)

    agent = _build_orchestrator(
        db_params, ledger, kind=kind, undefined=undefined, extra_blocks=blocks,
        code_hint=intent.test_code_in(question) if kind == intent.SCHEMA else None)
    return agent, blocks, undefined


def _answer_in_parts(parts, question, db_params, ledger, kind, user, user_id,
                     sp, traced, tag, t0):
    """Run each sub-question separately, verify separately, assemble.

    One ledger is shared across the parts, so evidence gathered for part one is
    available to part three and the budget is spent once. The VERDICTS are
    per-part, which is the whole point - a clause that cannot be grounded costs
    that clause and nothing else.
    """
    import contextlib

    from agents import Runner
    from . import audit, tracing

    results, steps_all = [], []
    tok_cached = 0
    tok_in = tok_out = tok_tot = 0
    # Each part gets its own slice of the query budget, so a greedy first part
    # cannot starve the third of the queries it needs.
    ledger.max_queries = max(4, ledger.max_queries // max(1, len(parts))) * len(parts)
    with (sp if sp is not None else contextlib.nullcontext()):
        if sp is not None:
            tag(sp)
        for idx, part in enumerate(parts):
            part_kind = intent.classify(part)
            # The subject and any earlier conclusion travel with the part -
            # without this, "has the datasheet been filled in" arrives with no
            # idea whose datasheet, and "is any of it overdue" with no idea
            # what "it" was.
            context = decompose.context_for(part, idx, question, results)
            try:
                agent, _blocks, undefined = _prepare(part, db_params, ledger, part_kind)
                run = Runner.run_sync(agent, context + part, max_turns=MAX_TURNS)
                _i, _o, _t, _m, _c = _extract_usage(run)
                tok_in += _i; tok_out += _o; tok_tot += _t; tok_cached += _c
                draft = str(run.final_output or "").strip()
                steps_all.extend(_extract_steps(run))
                g = verify.check(part, draft, ledger, kind=part_kind,
                                 undefined=undefined)
                results.append({"question": part, "verdict": g["verdict"],
                                "answer": verify.plain_text(g["answer"] or draft),
                                "unsupported": g.get("unsupported") or []})
            except Exception as exc:  # noqa: BLE001 - one part must not kill the rest
                results.append({"question": part, "verdict": "error",
                                "answer": "", "unsupported": [str(exc)[:120]]})

    assembled = decompose.assemble(results)
    if assembled:
        assembled = verify.strip_machinery(assembled)
        # The decomposed path shares one ledger across every part, so the
        # caveats are attached once to the assembled answer rather than
        # repeated under each part.
        assembled = semantics.attach_caveats(assembled, ledger)
    ok = [r for r in results if r["verdict"] in ("grounded", "repaired", "clarify")]
    if assembled is None:
        assembled = ("I could not verify an answer to any part of that question. "
                     "Try asking one thing at a time - a single job, test, "
                     "engineer or piece of equipment.")

    verdict = ("grounded" if len(ok) == len(results)
               else "partial" if ok else "unsupported")
    route = _route_label(ledger, steps_all)
    tracing.flush()
    audit.log_query(question=question, answer=assembled, user_id=user_id,
                    username=user, route=route,
                    model=os.environ.get("NLP_SEARCH_MODEL", DEFAULT_MODEL),
                    input_tokens=tok_in, output_tokens=tok_out, total_tokens=tok_tot,
                    cached_tokens=tok_cached,
                    latency_ms=int((time.time() - t0) * 1000),
                    sql_queries=ledger.sql_log() or None, success=True)
    return {"success": True, "answer": assembled, "route": route,
            "steps": steps_all, "evidence": ledger.summary(),
            "sql": ledger.queries(),
            "grounding": {"verdict": verdict,
                          "parts": [{"q": r["question"], "verdict": r["verdict"]}
                                    for r in results],
                          "unsupported": [u for r in results for u in r["unsupported"]],
                          "notes": ["%d of %d parts answered" % (len(ok), len(results))]},
            "tokens": {"input": tok_in, "output": tok_out, "total": tok_tot}}


def _matched_columns(find_field_json):
    """'table.column, table.column' from a find_field result, or 'no match'."""
    try:
        data = json.loads(find_field_json)
    except (TypeError, ValueError):
        return "unreadable"
    hits = data.get("matches") or []
    if not hits:
        return "NO MATCH - the field is not recorded in this database"
    return ", ".join("%s.%s" % (h.get("table"), h.get("column")) for h in hits)


def _build_orchestrator(db_params, ledger, model=None, kind=intent.DATA,
                        code_hint=None, undefined=(), extra_blocks=()):
    from agents import Agent, function_tool

    tools = (list(workers.worker_tools(db_params, ledger, model=model))
             if USE_DOMAIN_WORKERS
             else [workers.author_tool(db_params, ledger, model=model)])

    @function_tool(name_override="find_field")
    def find_field(term: str, test_code: str = "") -> str:
        """WHERE a concept is recorded - the table and column names, not the
        values. Reads the schema catalog, never the data, and returns nothing
        when the field is not recorded, which is the honest answer.

        Use this ONLY for questions about the structure: "where is X stored",
        "which column holds X", "do we record X at all". For a question about
        the VALUES - what X actually is, how many, whose - do NOT call this;
        dispatch to a worker, which knows its own columns already.

        Args:
            term: The concept, in the user's words (e.g. "coupling method").
            test_code: Optional - narrows to one test (CE, RE, ESD, ...).
        """
        out = probes.find_field(term, test_code=test_code or None)
        # Record the matches compactly rather than the raw JSON. The JSON
        # carries a purpose sentence per row and overran the note's length
        # cap, silently dropping later matches - so the grounding check saw an
        # answer citing a column that "was not in the evidence" and rewrote a
        # correct answer into a wrong one.
        ledger.note("schema", "find_field(%r) -> %s" % (term, _matched_columns(out)))
        return out

    @function_tool(name_override="resolve_entity")
    def resolve_entity(kind: str, text: str) -> str:
        """Turn a name the user typed into the real database rows, before it is
        used as a filter anywhere. Returns candidates or an explicit no-match.

        Args:
            kind: One of person, job, product, equipment, standard.
            text: The name or fragment from the question.
        """
        return probes.resolve_entity(db_params, kind, text, ledger=ledger)

    # NOTE: list_values is deliberately NOT offered here, only to the workers.
    # When the orchestrator had it, it answered counting questions straight off
    # a value listing ("9 distinct rows, so 9 requests") instead of dispatching -
    # right by luck, with no query in the ledger to check the answer against.
    # Checking a value exists is a worker's job, inside the query that uses it.
    tools += [resolve_entity]

    instructions = _INSTRUCTIONS.format(
        today=datetime.date.today().isoformat(),
        lab_rules=semantics.LAB_RULES)
    # Facts established BEFORE the model was asked anything: what the gates
    # found, and what the lab's own definitions say a fuzzy word means. The
    # undefined-term directive is already inside extra_blocks (see _prepare).
    for block in extra_blocks:
        if block:
            instructions += block
    if kind == intent.SCHEMA:
        # find_field is handed over ONLY on a schema question. Offered on every
        # question it became a first reflex on data questions too - the model
        # would look up where a column lives, then answer about the column
        # instead of querying it. One run reported "0 CE datasheets" for an
        # engineer who has two. Routing means changing the toolset, not adding
        # a tool and hoping the description is read.
        tools.append(find_field)
        instructions += intent.SCHEMA_DIRECTIVE.format(
            code_hint=(" and test_code=%r" % code_hint) if code_hint else "")

    chosen = model or os.environ.get("NLP_SEARCH_MODEL", DEFAULT_MODEL)
    from .model_settings import for_model
    settings = for_model(chosen)
    return Agent(
        name="EMC Lab Data Assistant",
        instructions=instructions,
        model=chosen,
        **({"model_settings": settings} if settings else {}),
        tools=tools)


# --------------------------------------------------------------------------
# run bookkeeping
# --------------------------------------------------------------------------

def _extract_steps(result):
    """Mine the run for tool calls/results so the UI can show what happened.
    Defensive: SDK item shapes vary between versions; missing bits degrade to
    empty strings rather than errors."""
    steps = []
    try:
        for item in getattr(result, "new_items", []) or []:
            kind = getattr(item, "type", "")
            raw = getattr(item, "raw_item", None)
            if kind == "tool_call_item":
                name = getattr(raw, "name", "") or ""
                args = getattr(raw, "arguments", "") or ""
                if isinstance(args, str) and len(args) > 2000:
                    args = args[:2000] + "…"
                steps.append({"type": "call", "tool": name, "args": args})
            elif kind == "tool_call_output_item":
                out = getattr(item, "output", None)
                if out is None and isinstance(raw, dict):
                    out = raw.get("output")
                out = out if isinstance(out, str) else json.dumps(out, default=str)
                if len(out) > 3000:
                    out = out[:3000] + "…"
                steps.append({"type": "result", "output": out})
    except Exception:  # noqa: BLE001 - trace is best-effort decoration
        pass
    return steps


def _route_label(ledger, steps):
    """Which workers actually did the work, for the audit log and the UI.

    Prefer the ledger, but fall back to the orchestrator's own tool calls: a
    worker that answered from a value probe alone leaves a note rather than a
    query, and its internal calls never appear in the parent's step list, so
    the ledger looks empty when the work did happen.
    """
    used = sorted({e["worker"] for e in ledger.entries})
    if used:
        return "+".join(used)
    called = {s.get("tool") for s in steps if s.get("type") == "call"}
    asked = sorted(t[4:] for t in called if t and t.startswith("ask_"))
    if asked:
        return "+".join(asked) + " (probe only)"
    if called & {"resolve_entity", "list_values"}:
        return "probe-only"
    return "none"


def _cached_of(usage):
    """The part of input_tokens the API served from its prompt cache.

    Worth capturing rather than ignoring: this system resends a large, nearly
    identical prefix on every call, and 97% of input came back cached on a
    live measurement. Cached input is billed at half rate, so costing without
    it overstates by about 2x.
    """
    det = getattr(usage, "input_tokens_details", None)
    if det is None:
        return 0
    if isinstance(det, dict):
        return det.get("cached_tokens", 0) or 0
    return getattr(det, "cached_tokens", 0) or 0


def _extract_usage(result):
    """(input, output, total, model, cached) from a RunResult, defensively
    across SDK shapes. Worker runs are nested inside the same context, so
    their usage rolls up here too."""
    inp = out = tot = cached = 0
    model = None
    try:
        u = getattr(getattr(result, "context_wrapper", None), "usage", None)
        if u is not None:
            inp = getattr(u, "input_tokens", 0) or 0
            out = getattr(u, "output_tokens", 0) or 0
            tot = getattr(u, "total_tokens", 0) or 0
            cached = _cached_of(u)
    except Exception:  # noqa: BLE001
        pass
    try:
        raws = getattr(result, "raw_responses", None) or []
        if not tot:
            for r in raws:
                ru = getattr(r, "usage", None)
                if ru:
                    inp += getattr(ru, "input_tokens", 0) or 0
                    out += getattr(ru, "output_tokens", 0) or 0
                    tot += getattr(ru, "total_tokens", 0) or 0
                    cached += _cached_of(ru)
        for r in raws:
            m = getattr(r, "model", None)
            if m:
                model = m
    except Exception:  # noqa: BLE001
        pass
    return inp, out, (tot or (inp + out)), model, min(cached, inp)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def answer(question, db_params, user=None, user_id=None, verify_answer=True,
           history=None):
    """Run the orchestrator on one question. Returns a dict for the API layer:

    `history` is the last few turns as [{role: user|assistant, text: str}],
    oldest first, used to resolve what a follow-up refers to. It is CONTEXT
    and not evidence - see _history_block for why that distinction is what
    keeps grounding meaningful.
    {"success": True, "answer": str, "route": str, "steps": [...],
     "grounding": {...}} or {"success": False, "message": str}. Never raises.

    `user`/`user_id` (the admin who asked) are attached to the Langfuse trace
    and recorded in nlp_search_audit."""
    question = (question or "").strip()
    if not question:
        return {"success": False, "message": "Please type a question."}
    if len(question) > MAX_QUESTION_CHARS:
        return {"success": False,
                "message": "Question too long (max %d characters)." % MAX_QUESTION_CHARS}
    if not os.environ.get("OPENAI_API_KEY"):
        return {"success": False, "message":
                "OPENAI_API_KEY is not configured. Add it to the .env file and restart the app."}
    try:
        from agents import Runner, set_tracing_disabled
        from agents.exceptions import MaxTurnsExceeded
    except ImportError:
        return {"success": False, "message":
                "The 'openai-agents' package is not installed. Run: pip install openai-agents"}

    # Enable Langfuse tracing if configured (it turns SDK tracing ON itself);
    # otherwise keep SDK tracing OFF so nothing is exported to OpenAI's backend.
    from . import tracing
    traced = tracing.setup_tracing()
    if not traced:
        set_tracing_disabled(True)

    sp = None
    if traced:
        try:
            import logfire
            sp = logfire.span("NL search")
        except Exception:  # noqa: BLE001
            sp = None

    def _tag(span):
        try:
            span.set_attribute("langfuse.trace.name", question[:80])
            span.set_attribute("langfuse.trace.input", question)
            span.set_attribute("langfuse.trace.tags", ["nl-search"])
            if user:
                span.set_attribute("langfuse.trace.user_id", str(user))
                span.set_attribute("user.id", str(user))
        except Exception:  # noqa: BLE001
            pass

    from . import audit
    model_name = os.environ.get("NLP_SEARCH_MODEL", DEFAULT_MODEL)
    ledger = Ledger(deadline_s=DEADLINE_S)
    t0 = time.time()
    trace_id = None
    # Route before retrieving. A question about WHERE something is recorded is
    # answered from the catalog; sending it down the SQL path guarantees a
    # plausible wrong pointer, because rows cannot tell you which field is the
    # right one. See intent.py.
    kind = intent.classify(question)

    # Three things in one sentence is three questions. Answering them as one
    # unit is what fails: the grounding check can only pass or withhold a whole
    # reply, so a wrong second clause takes the correct first and third down
    # with it. Each part gets its own run and its own verdict.
    # Decomposition is OFF by default and opt-in via NLP_SPLIT=1 - the gate
    # lives in decompose.looks_multipart(), which returns False unless it is
    # set. See that module's docstring for the measurement.
    parts = decompose.split(question) if verify_answer else [question]

    try:
        if len(parts) > 1:
            return _answer_in_parts(parts, question, db_params, ledger, kind,
                                    user, user_id, sp, traced, _tag, t0)
        agent, blocks, undefined = _prepare(question, db_params, ledger, kind,
                                           verify_answer, history=history)
        with (sp if sp is not None else contextlib.nullcontext()):
            if sp is not None:
                _tag(sp)
                try:
                    _ctx = getattr(sp, "context", None)
                    if _ctx is not None and getattr(_ctx, "trace_id", None):
                        trace_id = format(_ctx.trace_id, "032x")
                except Exception:  # noqa: BLE001
                    pass
            result = Runner.run_sync(agent, question, max_turns=MAX_TURNS)
            draft = str(result.final_output or "").strip()
            steps = _extract_steps(result)
            route = _route_label(ledger, steps)

            # Nothing leaves without being checked against the rows we actually
            # saw. This is the control the honesty requirement rests on; the
            # prompt above is only the request.
            grounding = (verify.check(question, draft, ledger, kind=kind,
                                      undefined=undefined)
                         if verify_answer else verify.skipped())
            answer_text = verify.plain_text(grounding["answer"] or draft)
            # Machinery out first, then the caveat in - that order, so the
            # stripper never sees the Note and cannot take it back out.
            answer_text = verify.strip_machinery(answer_text)
            # AFTER verification, deliberately. A caveat is not evidence, so
            # the repair pass - which rewrites the answer from the ledger and
            # nothing else - would strip it back out again. Appending here also
            # means it survives every path: grounded, repaired, or withheld.
            answer_text = semantics.attach_caveats(answer_text, ledger)

            if sp is not None:
                try:
                    sp.set_attribute("langfuse.trace.output", answer_text)
                    sp.set_attribute("langfuse.trace.metadata.route", route)
                    sp.set_attribute("langfuse.trace.metadata.grounding",
                                     grounding["verdict"])
                except Exception:  # noqa: BLE001
                    pass
        tracing.flush()
        inp, out, tot, model, cached = _extract_usage(result)
        audit.log_query(question=question, answer=answer_text, user_id=user_id,
                        username=user, route=route, model=(model or model_name),
                        input_tokens=inp, output_tokens=out, total_tokens=tot,
                        cached_tokens=cached,
                        latency_ms=int((time.time() - t0) * 1000),
                        tool_calls=",".join(sorted({s.get("tool") for s in steps
                                                    if s.get("type") == "call"
                                                    and s.get("tool")})) or None,
                        sql_queries=ledger.sql_log() or None,
                        success=True, trace_id=trace_id)
        return {"success": True, "answer": answer_text, "route": route,
                "steps": steps, "grounding": grounding,
                "evidence": ledger.summary(), "sql": ledger.queries(),
                "tokens": {"input": inp, "output": out, "total": tot,
                           "cached": cached}}
    except MaxTurnsExceeded:
        audit.log_query(question=question, user_id=user_id, username=user, route="none",
                        model=model_name, success=False, error="Max turns exceeded",
                        latency_ms=int((time.time() - t0) * 1000),
                        sql_queries=ledger.sql_log() or None, trace_id=trace_id)
        return {"success": False, "message":
                "The question needed more steps than allowed (%d). Try asking a "
                "more specific question." % MAX_TURNS}
    except Exception as exc:  # noqa: BLE001 - surface a clean message to the UI
        audit.log_query(question=question, user_id=user_id, username=user, model=model_name,
                        success=False, error=str(exc),
                        latency_ms=int((time.time() - t0) * 1000),
                        sql_queries=ledger.sql_log() or None, trace_id=trace_id)
        return {"success": False, "message": "NL search failed: %s" % exc}
