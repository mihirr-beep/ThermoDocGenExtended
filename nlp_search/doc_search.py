# -*- coding: utf-8 -*-
"""Document-search path of the NL search (Pinecone RAG).

Embeds the query with OpenAI and retrieves the top-K most similar datasheet
chunks from Pinecone, optionally filtered by test code / job / tco. Returns a
JSON string for the coordinator agent to read and cite.

Degrades gracefully: if Pinecone or the OpenAI key is not configured, returns
an honest "not connected" status (routing still works, so the path is testable
before the index exists).
"""
import json

from . import embeddings, vector_store

DEFAULT_TOP_K = 5
MAX_TOP_K = 15
_SNIPPET = 1200


def _clean_filters(test_code=None, job_number=None, tco_id=None):
    """Build a Pinecone metadata filter from the optional narrowing args."""
    flt = {}
    if test_code:
        flt["test_code"] = {"$eq": str(test_code).strip().upper()}
    if job_number:
        flt["job_number"] = {"$eq": str(job_number).strip()}
    if tco_id:
        flt["tco_id"] = {"$eq": str(tco_id).strip()}
    return flt or None


def search_documents(query, top_k=DEFAULT_TOP_K, test_code=None, job_number=None, tco_id=None):
    """Semantic search over generated datasheet chunks. Returns a JSON string:
    {"status":"ok","results":[{score,section,test_code,tco_id,job_number,
      product_name,source_file,text}, ...]} or a status/error object.
    Never raises."""
    query = (query or "").strip()
    if not query:
        return json.dumps({"status": "error", "message": "Empty document query."})
    if not vector_store.available():
        return json.dumps({
            "status": "unavailable",
            "message": ("Document search (Pinecone) is not configured yet. Set PINECONE_API_KEY "
                        "to enable retrieval over the generated datasheets."),
            "results": []})
    if not embeddings.available():
        return json.dumps({
            "status": "unavailable",
            "message": "OPENAI_API_KEY is not configured, so the query cannot be embedded.",
            "results": []})
    try:
        try:
            k = max(1, min(int(top_k), MAX_TOP_K))
        except (TypeError, ValueError):
            k = DEFAULT_TOP_K
        qv = embeddings.embed_query(query, dim=vector_store.index_dimension())
        matches = vector_store.query(qv, top_k=k,
                                     flt=_clean_filters(test_code, job_number, tco_id))
        results = []
        for m in matches:
            md = m.get("metadata") or {}
            results.append({
                "score": round(m.get("score"), 4) if isinstance(m.get("score"), (int, float)) else m.get("score"),
                "section": md.get("section"),
                "test_code": md.get("test_code"),
                "tco_id": md.get("tco_id"),
                "job_number": md.get("job_number"),
                "product_name": md.get("product_name"),
                "source_file": md.get("source_file"),
                "text": (md.get("text") or "")[:_SNIPPET],
            })
        return json.dumps({"status": "ok", "query": query, "count": len(results),
                           "results": results}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"status": "error",
                           "message": "Document search failed: %s" % exc, "results": []})
