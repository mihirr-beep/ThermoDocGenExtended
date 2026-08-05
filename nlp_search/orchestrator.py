# -*- coding: utf-8 -*-
"""Coordinator agent for the NL search, built on the OpenAI Agents SDK.

One agent, two tools, routing by INTENT (as designed):

  query_database   - NL->SQL over the live MySQL DB (validated, read-only,
                     capped). Structured facts: counts, statuses, lists,
                     assignments, dates, people, standards.
  search_documents - content of the GENERATED datasheet documents (Pinecone
                     RAG). Currently a stub with a fixed answer; the agent
                     still routes to it so the path is testable end-to-end.

The SDK's own loop gives us the agentic behaviour we designed: the model can
call tools repeatedly (fixing SQL after an error message), call both tools for
combined questions, then synthesize the final answer. max_turns bounds cost.

The SDK is imported lazily so the Flask app still boots when the package or
the OPENAI_API_KEY is missing - the endpoint then degrades to a clear message.
"""
import datetime
import json
import os
import time

from . import doc_search, sql_tool
from .schema_catalog import catalog_prompt_text

DEFAULT_MODEL = "gpt-4o-mini"
MAX_TURNS = 12
MAX_QUESTION_CHARS = 2000

_INSTRUCTIONS = """You are the EMC Test Lab data assistant for Thermo Fisher's test-plan/datasheet
application. Lab admins ask you questions in plain English; you answer from the
application's MySQL database and from the generated datasheet documents.

Today's date is {today}. Timestamps in the database are IST.

## Scope - answer ONLY about this lab's data
You answer questions about this EMC test lab: its test requests / jobs,
datasheets, equipment, schedules/planner, peer review, standards, users, and the
content of generated datasheet documents.

Interpret casual wording, synonyms and abbreviations GENEROUSLY and map them to
the schema - e.g. gadget/instrument/tool -> equipment; tester/engineer ->
assigned engineer or planner engineer; job / TCO / project -> request; report ->
datasheet; EUT -> the product being tested; "pending review" -> peer-review /
At Review status. A question about a specific PERSON by name ("who is X", "what
is X's role", "is X an admin") is a lab question - look them up in the users
table with LIKE on the name; if there is no such user, say so rather than
declining. A question is IN SCOPE if its topic relates to the lab in ANY
way, INCLUDING broad or summary questions like "how is the lab doing this month?"
or "give me an overview". For a broad question, give a short summary from the
data (or ask ONE clarifying question) - do NOT decline it, and do NOT assume a
question is off-topic just because its wording does not match column names.

DECLINE (one sentence: "I can only help with the EMC lab's data and datasheets.")
ONLY when the topic is clearly unrelated to the lab - weather, sports, general
knowledge, current events, math, coding, creative writing, personal opinions.
Never answer those from your own knowledge or call the tools for them.

## Honesty - never guess or fabricate
- If you cannot map a question to the schema/tools, or the tools return nothing
  relevant, SAY SO plainly ("I couldn't find that in the system" / "there is no
  such field") - never invent a number, name, status or value.
- A COUNT of 0 is suspicious: it often means you filtered on a value that does
  not exist. Before reporting 0 or "none", verify the value actually exists
  (e.g. run SELECT DISTINCT on that column); if the concept maps to no real
  field/value, tell the user that instead of reporting 0. Note: there is no
  "failed"/"rejected" status value in the database - test Pass/Fail is recorded
  INSIDE the datasheet documents (use search_documents), and a rejected request
  is one whose rejected_at / rejected_by / rejection_reason columns are filled.
- If the question is ambiguous or underspecified, ask ONE short clarifying
  question instead of guessing what was meant.

## Routing - decide by INTENT
- query_database: anything stored as structured records - counts, lists,
  statuses, who is assigned what, requests/jobs, planner entries, peer-review
  state, dates and durations, standards mapping, equipment, users/roles.
- search_documents: what a generated (peer-approved) datasheet document SAYS -
  test limits/observations/procedure wording written inside a document,
  comparing the contents of two documents, reasons in in-document notes.
- Combined questions: use both. If the document part depends on identifiers
  (job numbers, TCO ids), fetch those from the database first; otherwise the
  calls are independent.

## Searching documents - BREAK THE QUERY + agentic loop
search_documents runs ONE semantic retrieval (top 5 chunks) per call. For any
question that spans MORE THAN ONE document or aspect, decompose it and make
SEVERAL focused calls, then reason across the returned chunks:
- Comparisons ("compare the test limits in the CE datasheet for job A vs job B"):
  issue one search per document, narrowing with the test_code / job_number /
  tco_id filter args, then compare the results in your answer.
- Multi-aspect questions: search each aspect separately (e.g. one call for
  "test limits", one for "test procedure").
- If the first retrieval is thin or clearly missing something, search again
  with a refined query or a different filter (keep looping until you have
  enough - typically 1-4 calls). Always pass a filter when you know the test
  code or job so retrieval stays on the right document.
- Cite what you used: mention the test code / job number / section the facts
  came from. Never invent document content that is not in the returned chunks.

## Writing SQL (MySQL 8)
- ONE single SELECT statement per call. No comments, no semicolons, no SET,
  nothing but SELECT (WITH ... SELECT is fine).
- Use ONLY the tables and columns in the schema catalog below. Never invent
  names. Join with the keys shown in the catalog.
- Always include LIMIT (200 max). Prefer COUNT/GROUP BY aggregates over
  dumping rows. For fuzzy text matching use LIKE '%...%' (try LOWER() for
  case-insensitive matching). For relative dates use CURDATE() and
  DATE_SUB/INTERVAL.
- If the tool returns {{"error": ...}}, read the message, FIX the SQL and try
  again (up to 3 attempts). Do not apologise to the user about intermediate
  errors - only the final answer matters.
- Never query credential/secret columns; never SELECT * on the users table.
- Only filter a categorical column against a value shown in the schema catalog
  below. If you need a value that is NOT listed there, first run
  SELECT DISTINCT on that column to learn the real values, THEN filter - never
  invent a status/result/type literal (a made-up value silently returns 0 rows).
- For NAMES, products, standards and other free-text lookups, match case-
  insensitively with a PARTIAL LIKE (e.g. WHERE LOWER(name) LIKE '%krishna%'),
  never exact '='. People are stored by full name (e.g. 'Krishna Gonela'), so a
  first name like "Krishna" must use LIKE. If any lookup returns 0 rows, RETRY
  once with a broader LIKE (and check both name and username/email) BEFORE you
  tell the user it was not found.

## Answering
- Lead with the direct answer (the number, the list, the status), then a short
  supporting explanation. Plain text, no markdown tables unless listing rows.
- If a result was truncated, say the numbers reflect the first N rows.
- If the data genuinely is not there, say so - never fabricate values.
- If document search returns status "unavailable", tell the user document
  search is not configured yet, and still answer whatever the database can.
- If document search returns status "ok" but no results, say no matching
  datasheet content was found (the document may not be approved/indexed yet).

## Schema catalog (the ONLY tables you may query)
{catalog}
"""


def _build_agent(db_params):
    from agents import Agent, function_tool

    @function_tool
    def query_database(sql: str) -> str:
        """Run ONE read-only MySQL SELECT statement against the EMC lab database.

        Args:
            sql: A single SELECT statement using only tables from the schema
                catalog. Must include a LIMIT clause (max 200 rows).
        """
        return sql_tool.run_select(sql, db_params)

    @function_tool
    def search_documents(query: str, test_code: str = "", job_number: str = "",
                         tco_id: str = "", top_k: int = 5) -> str:
        """Semantic search over the CONTENT of generated datasheet documents
        (test limits, observations, procedures written inside approved .docx).
        Returns the top matching chunks with their section + document metadata.
        Call it once per document/aspect for multi-document questions.

        Args:
            query: What to look for inside the documents.
            test_code: Optional filter - restrict to one test (CE, RE, SURGE,
                EFT, ESD, HARMONIC, VOLTAGEFLICKER, VOLTAGEDIPS, CRF, PFMF, RS_RI).
            job_number: Optional filter - restrict to one job number.
            tco_id: Optional filter - restrict to one TCO id.
            top_k: How many chunks to return (default 5, max 15).
        """
        return doc_search.search_documents(
            query, top_k=top_k, test_code=test_code or None,
            job_number=job_number or None, tco_id=tco_id or None)

    instructions = _INSTRUCTIONS.format(
        today=datetime.date.today().isoformat(),
        catalog=catalog_prompt_text())
    return Agent(
        name="EMC Lab Data Assistant",
        instructions=instructions,
        model=os.environ.get("NLP_SEARCH_MODEL", DEFAULT_MODEL),
        tools=[query_database, search_documents])


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


def _route_label(steps):
    """Which path(s) the orchestrator actually used, from the tool calls."""
    tools = {s.get("tool") for s in steps if s.get("type") == "call"}
    db, doc = "query_database" in tools, "search_documents" in tools
    if db and doc:
        return "both"
    if db:
        return "database"
    if doc:
        return "documents"
    return "none"


def _extract_usage(result):
    """(input_tokens, output_tokens, total_tokens, model) from a RunResult,
    defensively across SDK shapes. Falls back to summing raw_responses."""
    inp = out = tot = 0
    model = None
    try:
        u = getattr(getattr(result, "context_wrapper", None), "usage", None)
        if u is not None:
            inp = getattr(u, "input_tokens", 0) or 0
            out = getattr(u, "output_tokens", 0) or 0
            tot = getattr(u, "total_tokens", 0) or 0
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
        for r in raws:
            m = getattr(r, "model", None)
            if m:
                model = m
    except Exception:  # noqa: BLE001
        pass
    return inp, out, (tot or (inp + out)), model


def _sql_and_tools(steps):
    """(joined SQL string, comma tool list) from the extracted steps."""
    sqls, tools = [], []
    for s in steps or []:
        if s.get("type") != "call":
            continue
        if s.get("tool") not in tools:
            tools.append(s.get("tool"))
        if s.get("tool") == "query_database":
            try:
                sql = json.loads(s.get("args") or "{}").get("sql")
                if sql:
                    sqls.append(sql)
            except Exception:  # noqa: BLE001
                pass
    return ("\n\n".join(sqls) or None), (",".join(t for t in tools if t) or None)


def answer(question, db_params, user=None, user_id=None):
    """Run the coordinator on one question. Returns a dict for the API layer:
    {"success": True, "answer": str, "steps": [...]} or
    {"success": False, "message": str}. Never raises.

    `user`/`user_id` (the admin who asked) are attached to the Langfuse trace and
    recorded in the nlp_search_audit table for auditing (who asked, question,
    answer, tokens, cost, route, SQL, latency)."""
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
    import contextlib
    from . import tracing
    traced = tracing.setup_tracing()
    if not traced:
        set_tracing_disabled(True)

    # Wrap the run in a tagged Langfuse trace carrying the question/answer/route,
    # so the admin usage dashboard can show a per-question cost table. The
    # 'nl-search' tag lets us filter these out from background indexing traces.
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
    t0 = time.time()
    trace_id = None
    try:
        agent = _build_agent(db_params)
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
            answer_text = str(result.final_output or "").strip()
            steps = _extract_steps(result)
            route = _route_label(steps)
            if sp is not None:
                try:
                    sp.set_attribute("langfuse.trace.output", answer_text)
                    sp.set_attribute("langfuse.trace.metadata.route", route)
                except Exception:  # noqa: BLE001
                    pass
        tracing.flush()
        inp, out, tot, model = _extract_usage(result)
        sqls, tools = _sql_and_tools(steps)
        audit.log_query(question=question, answer=answer_text, user_id=user_id, username=user,
                        route=route, model=(model or model_name), input_tokens=inp,
                        output_tokens=out, total_tokens=tot,
                        latency_ms=int((time.time() - t0) * 1000), tool_calls=tools,
                        sql_queries=sqls, success=True, trace_id=trace_id)
        return {"success": True, "answer": answer_text, "route": route, "steps": steps,
                "tokens": {"input": inp, "output": out, "total": tot}}
    except MaxTurnsExceeded:
        audit.log_query(question=question, user_id=user_id, username=user, route="none",
                        model=model_name, success=False, error="Max turns exceeded",
                        latency_ms=int((time.time() - t0) * 1000), trace_id=trace_id)
        return {"success": False, "message":
                "The question needed more steps than allowed (%d). Try asking a "
                "more specific question." % MAX_TURNS}
    except Exception as exc:  # noqa: BLE001 - surface a clean message to the UI
        audit.log_query(question=question, user_id=user_id, username=user, model=model_name,
                        success=False, error=str(exc),
                        latency_ms=int((time.time() - t0) * 1000), trace_id=trace_id)
        return {"success": False, "message": "NL search failed: %s" % exc}
