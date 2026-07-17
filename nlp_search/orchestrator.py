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

from . import doc_search, sql_tool
from .schema_catalog import catalog_prompt_text

DEFAULT_MODEL = "gpt-4o-mini"
MAX_TURNS = 12
MAX_QUESTION_CHARS = 2000

_INSTRUCTIONS = """You are the EMC Test Lab data assistant for Thermo Fisher's test-plan/datasheet
application. Lab admins ask you questions in plain English; you answer from the
application's MySQL database and from the generated datasheet documents.

Today's date is {today}. Timestamps in the database are IST.

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


def answer(question, db_params):
    """Run the coordinator on one question. Returns a dict for the API layer:
    {"success": True, "answer": str, "steps": [...]} or
    {"success": False, "message": str}. Never raises."""
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

    set_tracing_disabled(True)
    try:
        agent = _build_agent(db_params)
        result = Runner.run_sync(agent, question, max_turns=MAX_TURNS)
        return {"success": True,
                "answer": str(result.final_output or "").strip(),
                "steps": _extract_steps(result)}
    except MaxTurnsExceeded:
        return {"success": False, "message":
                "The question needed more steps than allowed (%d). Try asking a "
                "more specific question." % MAX_TURNS}
    except Exception as exc:  # noqa: BLE001 - surface a clean message to the UI
        return {"success": False, "message": "NL search failed: %s" % exc}
