# nlp_search — "Ask the Lab Data" (admin NL search)

Natural-language search over the EMC lab data, wired through the
[OpenAI Agents SDK](https://github.com/openai/openai-agents-python). A single
**coordinator agent** routes each question by intent to one of two tools:

| Tool | Path | Status |
|------|------|--------|
| `query_database` | NL→SQL over MySQL — structured facts (counts, statuses, assignments, dates, standards) | **implemented** |
| `search_documents` | Pinecone RAG over generated datasheet documents (top-5 semantic retrieval + metadata filter) | **implemented** (needs `PINECONE_API_KEY`) |

The SDK's own agentic loop gives us the multi-step behaviour we designed: the
model can retry SQL after an error, **break a multi-document question into
several `search_documents` calls (one per document/aspect) and loop until it has
enough**, call both tools for combined questions, and then synthesize the answer.

## Document (RAG) path

Ingestion is triggered when a **peer reviewer approves** a datasheet (planner
entry → `datasheet_uploaded`, in `app.py _apply_peer_review_action`). The
approved `.docx` is:

1. **chunked by section** (`chunker.py`) — one chunk per `Heading 1/2` section,
   tables flattened into the section, oversized matrices split on row
   boundaries; Jinja tokens stripped defensively;
2. **embedded** with OpenAI `text-embedding-3-small` (1536-d, `embeddings.py`);
3. **upserted** to Pinecone (`vector_store.py`) with metadata (tco_id,
   job_number, product_name, test_code, section, source_file, text).

Chunk ids are `pe<entry>:<code>:<i>`; on re-approval the old chunks are removed
by **prefix list + delete-by-id** (serverless Pinecone doesn't support
delete-by-filter). Retrieval (`doc_search.py`) embeds the query and returns the
**top 5** chunks, optionally filtered by `test_code` / `job_number` / `tco_id`.

Ingestion is **best-effort** — a missing key, network error, or parse failure
is logged and never blocks the approval.

### Backfill existing approved datasheets

```
python -m nlp_search.reindex          # all approved (status=datasheet_uploaded)
python -m nlp_search.reindex --all    # every planner entry that has a .docx
```

## Where it lives

```
nlp_search/
  __init__.py        register_nlp_search(app)  ← called from app.py
  routes.py          GET /admin/nlp-search  ·  POST /admin/nlp-search/ask   (admin-only)
  orchestrator.py    builds the Agent, runs it, extracts the tool trace
  sql_guard.py       SELECT-only validator (the security boundary)
  sql_tool.py        read-only executor: dedicated conn, READ ONLY txn, timeout, row/size caps
  doc_search.py      RAG stub (constant output)
  schema_catalog.py  AUTO-GENERATED — table allowlist + prompt catalog
  build_catalog.py   regenerates schema_catalog.py from the live DB
  templates/nlp_search/nlp_search.html   the admin UI (extends base.html)
```

## Setup

1. `pip install openai-agents` (pinned in `requirements.txt`).
2. Add your key to `.env`: `OPENAI_API_KEY=sk-...` (optionally `NLP_SEARCH_MODEL=gpt-4o-mini`).
3. Restart the app. Admins get an **"Ask the Lab Data"** button on the Admin Approval page,
   or go straight to `/admin/nlp-search`.

Without the key the page still loads; asking returns a clear "key not configured" message.

## Security model (NL→SQL)

The LLM writes SQL; it never gets to run anything the guard hasn't cleared. Layers:

1. **`sql_guard.validate_sql`** — single statement; must start `SELECT`/`WITH`; no
   comments; every write/DDL/admin keyword rejected; dangerous functions
   (`SLEEP`, `LOAD_FILE`, …) rejected; every FROM/JOIN table must be on the
   allowlist (schema-qualified names refused, CTE names exempt); credential
   columns refused by name; `SELECT *` refused on tables carrying secrets.
   Keyword scanning runs on a **literal-masked** copy so data values can't
   trigger false positives and can't hide smuggled SQL.
2. **`sql_tool.run_select`** — a dedicated short-lived PyMySQL connection (never
   the app's SQLAlchemy pool) with `SET SESSION TRANSACTION READ ONLY`,
   `SET SESSION MAX_EXECUTION_TIME`, a 200-row cap, and a response-size cap.
   Verified on this server: MySQL 8.0.46 enforces both the read-only txn
   (error 1792 on write) and the statement timeout (error 3024).

Credential columns are also stripped from the generated catalog, so the model
never even sees `password_hash` / `reset_token`.

## Regenerating the schema catalog

After a schema change:

```
python -m nlp_search.build_catalog
```

It keeps non-empty, business-relevant tables (drops empty `ds_*` capture
tables, the legacy `iec_emc_test_requests` orphan, raw audit logs) and embeds
low-cardinality status/code values so the model filters correctly.

## Files added for the RAG path

`embeddings.py` (OpenAI embeddings) · `vector_store.py` (Pinecone serverless) ·
`chunker.py` (docx → sections) · `ingest.py` (chunk+embed+upsert, called on
peer approval) · `reindex.py` (backfill CLI). `doc_search.py` is now the real
retriever. All Pinecone/OpenAI imports are lazy so the app boots without keys.
