# -*- coding: utf-8 -*-
"""Pinecone vector store for the datasheet RAG path (pinecone 9.1.0, serverless).

Everything is env-driven and lazy so the app boots with no Pinecone key:
    PINECONE_API_KEY     - required to actually use the store (else available()==False)
    PINECONE_INDEX       - index name           (default 'emc-datasheets')
    PINECONE_CLOUD       - serverless cloud      (default 'aws')
    PINECONE_REGION      - serverless region     (default 'us-east-1')
    PINECONE_NAMESPACE   - namespace             (default 'datasheets')

Serverless caveat (verified for 9.1.0): delete-by-metadata-filter is NOT
supported server-side. We therefore give every chunk of one document a shared
id prefix ``pe<entry>:<code>:`` and dedupe on re-ingest by listing ids by
prefix and deleting them by id.
"""
import os
import threading

from .embeddings import EMBED_DIM

_index_cache = {}
_index_lock = threading.Lock()


def index_name():
    return os.environ.get("PINECONE_INDEX", "emc-datasheets")


def namespace():
    return os.environ.get("PINECONE_NAMESPACE", "datasheets")


def available():
    return bool(os.environ.get("PINECONE_API_KEY"))


_dim_cache = {}


def index_dimension():
    """Effective embedding dimension = the LIVE index's dimension if the index
    exists, else the configured EMBED_DIM (used when creating a new index).
    Cached per index name. This is what ingest/query embed to, so vectors
    always match the index (e.g. a pre-created 1024-d index)."""
    from .embeddings import EMBED_DIM
    if not available():
        return EMBED_DIM
    name = index_name()
    if name in _dim_cache:
        return _dim_cache[name]
    dim = EMBED_DIM
    try:
        pc = _client()
        if pc.has_index(name):
            dim = int(pc.describe_index(name).dimension)
    except Exception:  # noqa: BLE001 - fall back to the configured default
        dim = EMBED_DIM
    _dim_cache[name] = dim
    return dim


def _client():
    from pinecone import Pinecone
    return Pinecone(api_key=os.environ["PINECONE_API_KEY"])


def get_index():
    """Return a ready Index handle, creating the serverless index on first use.
    Cached per index name. Raises if Pinecone is unavailable/misconfigured."""
    name = index_name()
    cached = _index_cache.get(name)
    if cached is not None:
        return cached
    from pinecone import ServerlessSpec
    # Serialize first-time creation so two threads can't both create_index and
    # have the loser hit a 409 (create-then-use race under a threaded server).
    with _index_lock:
        cached = _index_cache.get(name)
        if cached is not None:
            return cached
        pc = _client()
        if not pc.has_index(name):
            try:
                pc.create_index(
                    name=name,
                    dimension=EMBED_DIM,
                    metric="cosine",
                    spec=ServerlessSpec(
                        cloud=os.environ.get("PINECONE_CLOUD", "aws"),
                        region=os.environ.get("PINECONE_REGION", "us-east-1")),
                )
            except Exception:
                # a concurrent creator (another process/worker) may have won the
                # race; only re-raise if the index truly still does not exist.
                if not pc.has_index(name):
                    raise
        idx = pc.Index(name)
        _index_cache[name] = idx
        return idx


def upsert(vectors):
    """vectors: list of {"id","values","metadata"}. Upserts in batches."""
    if not vectors:
        return 0
    idx = get_index()
    ns = namespace()
    n = 0
    for i in range(0, len(vectors), 100):
        idx.upsert(vectors=vectors[i:i + 100], namespace=ns)
        n += len(vectors[i:i + 100])
    return n


def query(vector, top_k=5, flt=None):
    """Return a list of {"id","score","metadata"} for the nearest chunks."""
    if vector is None:
        return []
    idx = get_index()
    res = idx.query(vector=vector, top_k=top_k, include_metadata=True,
                    namespace=namespace(), filter=(flt or None))
    out = []
    for m in getattr(res, "matches", None) or []:
        out.append({"id": getattr(m, "id", None),
                    "score": getattr(m, "score", None),
                    "metadata": dict(getattr(m, "metadata", None) or {})})
    return out


def delete_by_prefix(prefix):
    """Serverless-safe dedupe: list ids under `prefix` and delete them by id.
    Deleting non-existent ids is a no-op. Returns the count deleted."""
    idx = get_index()
    ns = namespace()
    ids = []
    try:
        for page in idx.list(prefix=prefix, namespace=ns):
            for item in getattr(page, "vectors", None) or []:
                iid = getattr(item, "id", None)
                if iid:
                    ids.append(iid)
    except TypeError:
        # some client builds yield bare id-lists instead of page objects
        for page in idx.list(prefix=prefix, namespace=ns):
            ids.extend(page if isinstance(page, list) else [])
    for i in range(0, len(ids), 1000):
        idx.delete(ids=ids[i:i + 1000], namespace=ns)
    return len(ids)


def stats():
    """Best-effort index stats for the admin/health view."""
    try:
        idx = get_index()
        s = idx.describe_index_stats()
        return {"index": index_name(), "namespace": namespace(),
                "total_vectors": getattr(s, "total_vector_count", None)}
    except Exception as exc:  # noqa: BLE001
        return {"index": index_name(), "error": str(exc)}
