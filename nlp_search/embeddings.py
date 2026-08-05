# -*- coding: utf-8 -*-
"""OpenAI embeddings for the document (RAG) path.

Both ingestion (embedding datasheet chunks) and retrieval (embedding the
query) go through here, so the same model + dimension is used on both sides -
a mismatch would silently wreck similarity search.

The OpenAI client is imported lazily and reads OPENAI_API_KEY from the
environment (loaded from .env by mysql_config, as the rest of the app does).
"""
import os

DEFAULT_EMBED_MODEL = "text-embedding-3-small"
# Native dim of text-embedding-3-small is 1536; the -3-* models support the
# OpenAI `dimensions=` param to emit shorter, renormalized vectors. EMBED_DIM is
# the default target used only when creating a NEW index; at ingest/query time
# the effective dimension is taken from the LIVE index (see vector_store) so it
# always matches, whatever size the index was created at (e.g. 1024).
EMBED_DIM = int(os.environ.get("NLP_EMBED_DIM") or 1536)
_MAX_BATCH = 96                  # inputs per request (well under API limits)
_MAX_INPUT_CHARS = 8000          # trim a single chunk defensively (~2k tokens)


def embed_model():
    return os.environ.get("NLP_EMBED_MODEL", DEFAULT_EMBED_MODEL)


def _supports_dims(model):
    return "text-embedding-3" in (model or "")


def available():
    return bool(os.environ.get("OPENAI_API_KEY"))


def _client():
    from openai import OpenAI
    return OpenAI()  # reads OPENAI_API_KEY


def embed_texts(texts, dim=None):
    """Embed a list of strings -> list of vectors (same order). Batches the
    request set. `dim` forces the output dimension (must match the target
    index); defaults to EMBED_DIM. Raises on API error (callers decide)."""
    if not texts:
        return []
    client = _client()
    model = embed_model()
    d = int(dim or EMBED_DIM)
    out = []
    for i in range(0, len(texts), _MAX_BATCH):
        batch = [(_t or " ")[:_MAX_INPUT_CHARS] for _t in texts[i:i + _MAX_BATCH]]
        kwargs = {"model": model, "input": batch}
        if _supports_dims(model):
            kwargs["dimensions"] = d
        resp = client.embeddings.create(**kwargs)
        out.extend(e.embedding for e in resp.data)
    return out


def embed_query(text, dim=None):
    """Embed a single query string -> one vector (dimension = `dim`/EMBED_DIM)."""
    vecs = embed_texts([text or " "], dim=dim)
    return vecs[0] if vecs else None
