# nlp_search — "Ask the Lab Data" (admin NL search)

Natural-language questions about the EMC lab, answered **from the database and
nothing else**. Built on the
[OpenAI Agents SDK](https://github.com/openai/openai-agents-python).

There is no vector store and no document-retrieval lane. Every fact in an
answer has to come out of a validated, read-only `SELECT` against MySQL — so
when the data is not there, the assistant can say so instead of producing a
plausible number from somewhere else. That is the whole design constraint.

| Tool | Path |
|------|------|
| `query_database` | NL→SQL over MySQL — counts, statuses, assignments, dates, standards, equipment, and the recorded datasheet values |

The SDK's own agentic loop supplies the multi-step behaviour: the model can
retry SQL after an error, probe a column for its real values before filtering
on it, and chain several queries before answering. `max_turns` bounds the cost.

## Where it lives

```
nlp_search/
  __init__.py        register_nlp_search(app)  ← called from app.py
  routes.py          GET /admin/nlp-search  ·  POST /admin/nlp-search/ask   (admin-only)
  orchestrator.py    builds the Agent, runs it, extracts the tool trace
  sql_guard.py       SELECT-only validator (the security boundary)
  sql_tool.py        read-only executor: dedicated conn, READ ONLY txn, timeout, row/size caps
  schema_catalog.py  AUTO-GENERATED — table allowlist + prompt catalog
  build_catalog.py   regenerates schema_catalog.py from the live DB
  audit.py           per-question audit log (who asked, tokens, cost, SQL, latency)
  tracing.py         optional Langfuse/OTEL export
  langfuse_metrics.py  cost + usage rollups for the admin dashboard
  templates/nlp_search/usage.html   the admin usage page
```

## Setup

1. `pip install -r requirements.txt` (pins `openai-agents`).
2. Add your key to `.env`: `OPENAI_API_KEY=sk-...` (optionally `NLP_SEARCH_MODEL=gpt-4o-mini`).
3. Restart the app. Admins get an **"Ask the Lab Data"** button on the Admin
   Approval page, or go straight to `/admin/nlp-search`.

Without the key the page still loads; asking returns a clear "key not
configured" message.

## Security model (NL→SQL)

The LLM writes the SQL; it never gets to run anything the guard has not
cleared. Two layers:

1. **`sql_guard.validate_sql`** — single statement; must start `SELECT`/`WITH`;
   no comments; every write/DDL/admin keyword rejected; dangerous functions
   (`SLEEP`, `LOAD_FILE`, …) rejected; every FROM/JOIN table must be on the
   allowlist (schema-qualified names refused, CTE names exempt); credential
   columns refused by name; `SELECT *` refused on tables carrying secrets.
   Keyword scanning runs on a **literal-masked** copy so data values can't
   trigger false positives and can't hide smuggled SQL.
2. **`sql_tool.run_select`** — a dedicated short-lived PyMySQL connection
   (never the app's SQLAlchemy pool) with `SET SESSION TRANSACTION READ ONLY`,
   `SET SESSION MAX_EXECUTION_TIME`, a 200-row cap, and a response-size cap.
   Verified on this server: MySQL 8.0.46 enforces both the read-only
   transaction (error 1792 on write) and the statement timeout (error 3024).

Credential columns are also stripped from the generated catalog, so the model
never even sees `password_hash` / `reset_token`.

## Regenerating the schema catalog

After a schema change:

```
python -m nlp_search.build_catalog
```

It keeps non-empty, business-relevant tables and embeds low-cardinality
status/code values so the model filters against real literals instead of
inventing them.
