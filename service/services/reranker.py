"""
Sprint 5 / B2 — Cross-encoder reranker (PLACEHOLDER).

Re-rank the top-N candidates from `retrieval.retrieve_chunks_hybrid`
using a true cross-encoder (query, chunk_text) -> relevance score.
Bi-encoder embeddings (already used for vector search) approximate
relevance with a cosine over independent encodings; a cross-encoder
attends jointly and is materially better for borderline cases.

Why this is a stub:
  * Pulls in a heavy ML dep (sentence-transformers + torch) that
    significantly bloats the service image. The user wants to defer
    until the deployment target (CPU/GPU, container size budget) is
    confirmed.
  * Recommended model: `BAAI/bge-reranker-base` (multilingual,
    ~110 MB, CPU-runnable; latency ~30 ms per (q, doc) pair on a
    modern x86 core, ~5 ms on a small GPU).
  * Alternative for tighter budgets: `cross-encoder/ms-marco-MiniLM-L-6-v2`
    (~80 MB, ~12 ms/pair on CPU).

How to wire when ready:
  1. `pip install sentence-transformers` (uncomment in requirements.txt).
  2. Implement `_load_model()` below.
  3. In `services/retrieval.retrieve_chunks_hybrid`, after MMR pruning
     to ~25 candidates, call `await rerank(query, candidates, top_k)`
     and replace `candidates` with the result before final truncation.
  4. Add a `reranker_enabled` setting (default False) so it can be
     turned on per-environment.

Determinism + caching:
  Reranker scores depend only on (query, chunk_text); cache them per
  (sha256(query) | chunk_id, model_version) in feature_snapshots so
  follow-up "tell me more" queries don't re-pay the cost.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# TODO(reranker): keep this import lazy so the service still boots
# without sentence-transformers installed. Pattern:
#
#     def _load_model():
#         global _MODEL
#         if _MODEL is None:
#             from sentence_transformers import CrossEncoder  # heavy
#             _MODEL = CrossEncoder(get_settings().reranker_model_name,
#                                   max_length=512)
#         return _MODEL


_MODEL: Any | None = None  # holds the lazily-loaded CrossEncoder


@dataclass
class RerankedChunk:
    chunk_id: str
    rerank_score: float
    bi_encoder_score: float


async def rerank(
    query: str,
    candidates: list[Any],   # services.retrieval.RetrievedChunk
    top_k: int,
) -> list[Any]:
    """
    Score (query, chunk_text) pairs with a cross-encoder and return the
    top-k candidates re-ordered by rerank_score.

    Until implemented this is a no-op pass-through that preserves the
    upstream order. That keeps the rest of the pipeline working without
    silently degrading retrieval quality (a broken reranker would be
    worse than no reranker).
    """
    # TODO(B2): implement when sentence-transformers is permitted.
    #   model = _load_model()
    #   pairs = [(query, c.chunk_text) for c in candidates]
    #   scores = await asyncio.get_running_loop().run_in_executor(
    #       None, model.predict, pairs
    #   )
    #   for c, s in zip(candidates, scores):
    #       c.metadata.setdefault("reranker", {})["score"] = float(s)
    #       c.blended_score = float(s)
    #   candidates.sort(key=lambda c: c.blended_score, reverse=True)
    return candidates[:top_k]


def is_available() -> bool:
    """Tell callers whether the reranker is wired (used to gate the call)."""
    return False  # flip to True once `_load_model` is implemented
