"""
Local sentence-transformer embedding model.

Loaded once at startup, used for both ingestion and query embedding.
"""
from __future__ import annotations

import asyncio
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from config.settings import get_settings


@lru_cache
def _model() -> SentenceTransformer:
    return SentenceTransformer(get_settings().embedding_model)


def embed_sync(texts: list[str]) -> list[list[float]]:
    """Synchronous batch embed. Use for ingestion scripts."""
    if not texts:
        return []
    vectors = _model().encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return vectors.tolist()


async def embed(texts: list[str]) -> list[list[float]]:
    """Async wrapper that runs the (CPU-bound) model in a thread."""
    if not texts:
        return []
    return await asyncio.to_thread(embed_sync, texts)


async def embed_one(text: str) -> list[float]:
    vecs = await embed([text])
    return vecs[0]


def warmup() -> None:
    """Force the model to load. Call once at startup."""
    _model()
