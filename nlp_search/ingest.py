# -*- coding: utf-8 -*-
"""Ingest a generated datasheet into Pinecone.

Trigger: a peer reviewer APPROVES a datasheet (planner entry -> 'datasheet_uploaded').
The approved .docx is chunked by section, embedded with OpenAI, and upserted to
Pinecone with metadata for retrieval + filtering + citations.

``ingest_on_approval(entry)`` is the one call the app's approval path makes -
it resolves the test code, parent request, and .docx path from the planner
entry itself, and is fully best-effort (never raises into the approval flow).
"""
import os

from . import chunker, embeddings, vector_store

_META_TEXT_CAP = 3000     # keep chunk text in metadata but bounded (< 40KB/vector limit)


def _resolve_code(entry):
    """Test code (CE/RE/SURGE/...) for a planner entry: prefer the persisted
    datasheet_records.test_code, fall back to the entry's test_name."""
    try:
        from datasheet_gen import records as R
        rec = R.get_record_for_assignment(entry.id)
        if rec and rec.get("test_code"):
            return str(rec["test_code"]).strip().upper()
    except Exception:  # noqa: BLE001
        pass
    return (getattr(entry, "test_name", "") or "").strip().upper()


def _parent_request(entry):
    try:
        from models import db, EMCRequest
        rid = getattr(entry, "test_request_id", None)
        if rid:
            return db.session.get(EMCRequest, rid)
    except Exception:  # noqa: BLE001
        pass
    return None


def _first(*vals):
    for v in vals:
        if v not in (None, ""):
            return str(v)
    return ""


def ingest_datasheet(docx_path, planner_entry_id, code, meta=None, logger=None):
    """Chunk -> embed -> upsert one datasheet. Best-effort; returns a status
    dict and never raises."""
    result = {"status": "skipped", "chunks": 0, "planner_entry_id": planner_entry_id, "code": code}
    try:
        if not vector_store.available():
            result["reason"] = "PINECONE_API_KEY not configured"
            return result
        if not embeddings.available():
            result["reason"] = "OPENAI_API_KEY not configured"
            return result
        if not docx_path or not os.path.exists(docx_path):
            result["reason"] = "datasheet file not found: %s" % docx_path
            return result

        chunks = chunker.chunk_datasheet(docx_path)
        if not chunks:
            result["reason"] = "no chunks extracted"
            return result

        vecs = embeddings.embed_texts([c["text"] for c in chunks])
        base = dict(meta or {})
        base.setdefault("planner_entry_id", planner_entry_id)
        base.setdefault("test_code", code)
        base.setdefault("source_file", os.path.basename(docx_path))

        prefix = "pe%s:%s:" % (planner_entry_id, code)
        vectors = []
        for i, (c, v) in enumerate(zip(chunks, vecs)):
            md = dict(base)
            md["section"] = c["section"]
            md["chunk_index"] = i
            md["text"] = c["text"][:_META_TEXT_CAP]
            vectors.append({"id": "%s%d" % (prefix, i), "values": v, "metadata": md})

        # replace any previous version of this document's chunks (serverless-safe)
        removed = vector_store.delete_by_prefix(prefix)
        upserted = vector_store.upsert(vectors)
        result.update(status="ok", chunks=upserted, replaced=removed)
        if logger:
            logger.info("nlp_search ingested %d chunks for %s (replaced %d)", upserted, prefix, removed)
        return result
    except Exception as exc:  # noqa: BLE001 - never break the caller
        if logger:
            logger.warning("nlp_search ingest failed for entry %s: %s", planner_entry_id, exc)
        result.update(status="error", reason=str(exc))
        return result


def ingest_async(app, planner_entry_id):
    """Fire-and-forget ingestion for an approved datasheet, AFTER the approval
    has been committed. Runs in a daemon thread with its OWN app context + DB
    session, so it holds no lock and never delays the approval response. Fully
    best-effort. Call this from the route *after* db.session.commit()."""
    import threading

    def _run():
        try:
            with app.app_context():
                from models import db, PlannerEntry
                try:
                    entry = db.session.get(PlannerEntry, planner_entry_id)
                    if entry is not None:
                        ingest_on_approval(entry, logger=app.logger)
                finally:
                    db.session.remove()
        except Exception:  # noqa: BLE001 - background thread must never surface
            pass

    try:
        threading.Thread(target=_run, name="nlp-ingest-%s" % planner_entry_id,
                         daemon=True).start()
    except Exception:  # noqa: BLE001
        pass


def ingest_on_approval(entry, logger=None):
    """Called from the peer-review APPROVE path. Resolves everything from the
    planner entry. Best-effort."""
    try:
        code = _resolve_code(entry)
        parent = _parent_request(entry)
        meta = {
            "planner_entry_id": entry.id,
            "test_code": code,
            "test_name": _first(getattr(entry, "test_name", "")),
            "tco_id": _first(getattr(entry, "tco_id", ""), getattr(parent, "tco_id", "")),
            "job_number": _first(getattr(parent, "job_number", ""), getattr(parent, "job_id", "")),
            "product_name": _first(getattr(parent, "product_name", ""), getattr(parent, "model_number", "")),
            "manufacturer": _first(getattr(parent, "manufacturer", "")),
            "approved": True,
        }
        return ingest_datasheet(getattr(entry, "datasheet_file_path", None),
                                entry.id, code, meta=meta, logger=logger)
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger.warning("nlp_search ingest_on_approval failed: %s", exc)
        return {"status": "error", "reason": str(exc)}
