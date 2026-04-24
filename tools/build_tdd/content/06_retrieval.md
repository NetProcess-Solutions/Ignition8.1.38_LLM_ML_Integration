# 6. Retrieval Layer (NEW vs v2.0)

The v2.0 design specified vector retrieval over `document_chunks`. The
as-built MVP ships a **hybrid retrieval pipeline** that fuses dense
vector search with BM25 keyword search, applies failure-mode and
equipment boosts, diversifies via Maximal Marginal Relevance (MMR),
and re-ranks by per-chunk quality signals — all with strict bounds so
no individual signal can dominate.

This is the largest single departure from v2.0 and is the foundation
the rest of the grounding pipeline depends on. Bad retrieval poisons
every downstream stage: the LLM gets the wrong evidence and produces
confidently wrong answers from it. Good retrieval is the difference
between a useful advisor and a glorified search bar.

The whole pipeline lives in
[service/services/retrieval.py](service/services/retrieval.py); core
behavior is exercised in
[service/tests/test_retrieval_hybrid.py](service/tests/test_retrieval_hybrid.py)
(35 tests; 0 failing).

## 6.1 Pipeline Stages

The top-level entry point is `retrieve_chunks_hybrid(query, embedding,
*, top_k, scope_filters, ...)`. It runs five sequential stages:

```
                               ┌──────────────────────┐
        query + embedding ────►│ 1. Vector retrieval  │──► top-K_v candidates
                               └──────────────────────┘
                               ┌──────────────────────┐
        query text       ────►│ 2. BM25 retrieval    │──► top-K_b candidates
                               └──────────────────────┘
                                          │
                                          ▼
                               ┌──────────────────────┐
                               │ 3. RRF fusion        │──► merged ranking
                               └──────────────────────┘
                                          │
                                          ▼
                               ┌──────────────────────┐
                               │ 4. Conditional boost │──► FM/equipment-aware ranking
                               └──────────────────────┘
                                          │
                                          ▼
                               ┌──────────────────────┐
                               │ 5. MMR diversify     │──► top-K final
                               └──────────────────────┘
                                          │
                                          ▼
                               ┌──────────────────────┐
                               │ 6. Quality re-rank   │──► (bounded ±30%)
                               └──────────────────────┘
```

## 6.2 Stage 1 — Vector Retrieval

`retrieve_chunks(query_embedding, top_k=K_v)` runs a cosine-distance
ANN query against the `document_chunks.embedding` ivfflat index.
`K_v = 50` by default (`settings.retrieval_vector_top_k`). Scope filters
are applied as SQL `WHERE` clauses before the ANN scan when present:

- `failure_mode_codes && ARRAY['delam_hotpull']::text[]`
- `equipment_codes && ARRAY['zone3_heater']::text[]`
- `effective_date <= anchor_time AND (superseded_by IS NULL OR superseded_at > anchor_time)`

Cosine similarity is converted to a `vector_score` in `[0, 1]` for
downstream fusion. Failure to match the index (cold start, brand-new
corpus) is handled by a fallback to a plain seq scan — slow but
correct, with a `warn_once_memo` log entry to surface the missing
index. There are integration tests for both the indexed and
unindexed paths in
[service/tests/test_retrieval_hybrid.py](service/tests/test_retrieval_hybrid.py).

## 6.3 Stage 2 — BM25 Keyword Retrieval

`retrieve_chunks_keyword(query_text, top_k=K_b)` runs a Postgres
full-text query against `document_chunks.bm25_tsv` (a generated
`TSVECTOR` column on `chunk_text`). `K_b = 50` by default
(`settings.retrieval_keyword_top_k`).

The `tsvector` index uses the `english` configuration. Query text is
normalized via `plainto_tsquery('english', $1)` so operator phrases
like "delam in zone three" produce sensible matches without the
operator needing to learn `tsquery` syntax.

`ts_rank_cd` ranks results, normalized to `[0, 1]` as `keyword_score`.

This is the BM25 of the gap analysis — it is the leg of the hybrid
that catches queries where the operator uses a specific identifier
(`R-20260421-03`, `S-4471`, `WO-88214`) the embedder cannot generalize
to. Without it, identifier-anchored queries silently fail to retrieve
their target documents.

## 6.4 Stage 3 — RRF Fusion (`_rrf_fuse`)

Reciprocal Rank Fusion combines the two ranked lists by summing
`1 / (k + rank_i)` across both rankings, where `k = 60` (the
canonical RRF constant from the original Cormack/Clarke/Buettcher paper).

```python
rrf_score(chunk) = sum(
    1.0 / (k_rrf + rank_in_list)
    for list in (vector_list, bm25_list)
    if chunk in list
)
```

RRF has two crucial properties for our use case:

1. **It is rank-based, not score-based.** A chunk that ranks #3 in
   vector and #5 in BM25 contributes `1/63 + 1/65`; the actual cosine
   similarity and ts_rank values don't enter the fusion. This makes the
   fusion robust to score-distribution skew between the two retrievers.
2. **It is parameter-free** beyond the `k` constant, which is fixed at
   60 across virtually all published RAG implementations. There is no
   weight knob to mis-tune.

The fused list is truncated to `top_k_fused = 30`
(`settings.retrieval_rrf_top_k`).

## 6.5 Stage 4 — Conditional Boost (`_conditional_boost`)

When the parsed anchor carries a failure-mode scope or an equipment
scope, chunks whose `failure_mode_codes` or `equipment_codes` overlap
get a multiplicative boost on their RRF score:

| Match condition                               | Boost  |
|-----------------------------------------------|--------|
| Chunk failure_mode_codes ∩ anchor.failure_mode_scope | 1.5×   |
| Chunk equipment_codes    ∩ anchor.equipment_scope    | 1.3×   |
| `documents.document_role = 'internal_authoritative'` | 1.2×   |
| `documents.document_role = 'wo_narrative'`           | 1.0×   |
| `documents.document_role = 'external_reference'`     | 0.6×   |

These multipliers stack. An internal SOP chunk that mentions both
`delam_hotpull` and `zone3_heater` for an anchor scoped to that pair
gets `1.5 × 1.3 × 1.2 = 2.34×` its base RRF score.

The role-weight values are clamped to `[0.5, 2.5]` to prevent any
single signal from dominating the ranking. The boost values are
constants in `services/retrieval.py` and exposed via
`settings.retrieval_boost_*` for future experimentation but not
expected to need tuning in normal operation.

## 6.6 Stage 5 — MMR Diversification (`_mmr_select`)

Maximal Marginal Relevance selects the final top-K from the boosted
candidates with a relevance/diversity trade-off:

```
MMR = argmax over chunk c not yet selected of:
      lambda * boosted_score(c)
    - (1 - lambda) * max(cosine_sim(c, c') for c' in selected)
```

`lambda = 0.7` by default (`settings.retrieval_mmr_lambda`). Higher
lambda biases toward relevance; lower toward diversity. The 0.7 default
is the standard RAG-pipeline value from the published MMR literature.

This step is what prevents the final context from being five
near-duplicate paragraphs of the same SOP. Without MMR, a single
high-quality, dense doc can crowd out the broader evidence base; with
it, the context surfaces five qualitatively-different sources rather
than one source repeated.

The default `final_top_k = 10` (`settings.retrieval_top_k`).

## 6.7 Stage 6 — Quality Re-Rank (Bounded)

The final stage applies per-chunk feedback signals from
`chunk_quality_signals` (chapter 5, group 1):

```
quality_multiplier = 1 + clamp(
    (helpful - unhelpful) * w_help
  + (cited_in_correct - cited_in_incorrect) * w_outcome,
    -0.3, +0.3
)
```

The clamp at ±30% is the **bounded re-ranking** principle from
the trust model: a single bad rating cannot bury a useful chunk
forever, and a single good rating cannot crown a chunk above the
evidence ranking. `w_help = 0.05`, `w_outcome = 0.10`
(`settings.feedback_re_rank_*`). The clamp is non-negotiable.

Absent any rows in `chunk_quality_signals` for a chunk, the
multiplier defaults to 1.0 (identity). The system runs sensibly from
day 1 without the table being populated — the re-rank stage simply
becomes a no-op until feedback accumulates.

## 6.8 Failure-Mode-Matched History Path

Distinct from text retrieval, the **failure-mode-matched history**
bucket (chapter 4, §4.3) calls
`retrieve_failure_mode_matched(style, failure_mode, anchor_time, *, k)`
which runs a structured-SQL join across `production_runs ⨝
defect_events ⨝ work_orders` filtered to the same `(product_style,
fm_code)` and ordered by recency. Its results are rendered as a
distinct evidence section in the prompt (section G in the layout from
chapter 4) — they are **not** funneled through RRF/MMR with text
chunks. Mixing them would make the per-chunk citation IDs incoherent.

Tested in `test_retrieval_hybrid.py::test_failure_mode_matched_*`
(8 cases).

## 6.9 Work-Order Scoping

`retrieve_work_orders(scope, anchor_time, window)` retrieves WO records
overlapping a temporal window around an anchor. Used by the change
ledger (chapter 8) and the RCA chain (chapter 7) to surface "the
crew that did element replacement on zone 3 last week" as
context for an off-tenter event today.

## 6.10 Settings Reference

All retrieval-tunable knobs live in
[service/config/settings.py](service/config/settings.py) under the
`retrieval_*` prefix:

| Setting                          | Default | Effect |
|----------------------------------|---------|--------|
| `retrieval_vector_top_k`         | 50      | Stage 1 ANN candidate count |
| `retrieval_keyword_top_k`        | 50      | Stage 2 BM25 candidate count |
| `retrieval_rrf_top_k`            | 30      | Stage 3 fused list size |
| `retrieval_top_k`                | 10      | Stage 5 MMR final size |
| `retrieval_rrf_k`                | 60      | RRF constant (fixed; do not tune) |
| `retrieval_mmr_lambda`           | 0.7     | MMR relevance/diversity |
| `retrieval_boost_failure_mode`   | 1.5     | FM scope match boost |
| `retrieval_boost_equipment`      | 1.3     | Equipment scope match boost |
| `retrieval_role_weight_min/max`  | 0.5/2.5 | Role-weight clamp |
| `feedback_re_rank_help_weight`   | 0.05    | per-helpful-vote weight |
| `feedback_re_rank_outcome_weight`| 0.10    | per-correct-citation weight |
| `feedback_re_rank_clamp`         | 0.30    | ±30% bound (non-negotiable) |

## 6.11 Observability

Every retrieval call emits a structured log line via `services/metrics.py`:

```
{
  "evt": "retrieval.complete",
  "duration_ms": 142,
  "vector_candidates": 50,
  "keyword_candidates": 47,
  "fused_size": 30,
  "boosted": {"fm": 8, "equipment": 12, "role": 30},
  "mmr_selected": 10,
  "scope": {"failure_mode": ["delam_hotpull"], "equipment": ["zone3_heater"]},
  "trace_id": "..."
}
```

Plus Prometheus histograms:

- `retrieval_duration_seconds{stage="vector|keyword|rrf|boost|mmr|rerank"}`
- `retrieval_candidate_count{stage}`
- `retrieval_boost_applied_total{type}`

These let an operator answer "is retrieval slow because vector ANN
is slow, or because we're pulling too many candidates into the
fusion stage?" without spelunking application logs.

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Retrieval</p>
<p><span class="label">Stayed:</span> Nothing. v2.0 specified
single-leg vector retrieval; v3.0 ships a six-stage pipeline.</p>
<p><span class="label">Changed:</span> Hybrid vector + BM25, RRF fusion
with k=60, conditional FM/equipment boost (1.5×/1.3×), document_role
weighting (0.6×/1.0×/1.2× clamped to [0.5, 2.5]), MMR diversification
with λ=0.7, bounded ±30% feedback-driven quality re-rank. Separate
structured failure-mode-matched-history path that is not funneled
through RRF. Full observability via structured logs + Prometheus
histograms.</p>
<p><span class="label">Considering:</span> Cross-encoder reranker
between MMR and the LLM (B2 — currently a pass-through stub awaiting
the install of <code>sentence-transformers</code> + the
<code>BAAI/bge-reranker-base</code> model). HyDE-style hypothetical
document expansion for cold-start queries (B5). Query-rewriter as a
standalone step rather than emergent from tool-call loop (B4).
Per-style ANN index partitioning once corpus exceeds 1M chunks.</p>
</div>
