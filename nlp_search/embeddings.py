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
EMBED_DIM = 1536                 # dimension of text-embedding-3-small
_MAX_BATCH = 96                  # inputs per request (well under API limits)
_MAX_INPUT_CHARS = 8000          # trim a single chunk defensively (~2k tokens)


def embed_model():
    return os.environ.get("NLP_EMBED_MODEL", DEFAULT_EMBED_MODEL)


def available():
    return bool(os.environ.get("OPENAI_API_KEY"))


def _client():
    from openai import OpenAI
    return OpenAI()  # reads OPENAI_API_KEY


def embed_texts(texts):
    """Embed a list of strings -> list of vectors (same order). Batches the
    request set. Raises on API error (callers decide how to handle)."""
    if not texts:
        return []
    client = _client()
    model = embed_model()
    out = []
    for i in range(0, len(texts), _MAX_BATCH):
        batch = [(_t or " ")[:_MAX_INPUT_CHARS] for _t in texts[i:i + _MAX_BATCH]]
        resp = client.embeddings.create(model=model, input=batch)
        out.extend(d.embedding for d in resp.data)
    return out


def embed_query(text):
    """Embed a single query string -> one vector."""
    vecs = embed_texts([text or " "])
    return vecs[0] if vecs else None
