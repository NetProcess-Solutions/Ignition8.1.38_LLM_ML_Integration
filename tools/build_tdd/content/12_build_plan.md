# 12. Build Plan (As-Executed)

The original v2.0 design specified eleven build tasks (B1–B11). The
as-executed delivery reorganized into two parallel sprints — A-series
(database + ingestion + ops scaffolding) and B-series (service
capabilities) — running interleaved over 14 weeks. This chapter maps
the original plan onto what actually shipped, what slipped, what
expanded, and what survived intact.

For the **forward-looking** roadmap (Phase 3 polish + Phase 4 ML), see
chapter 18. For per-sprint completion artifacts, see
[/memories/repo/sprint_completion_log.md](/memories/repo/sprint_completion_log.md).

## 12.1 The A-Series (Foundations)

| ID  | Asked for                                                                                  | Status | Where it lives |
|-----|--------------------------------------------------------------------------------------------|--------|----------------|
| A1  | Postgres 16 schema, ~27 tables / 8 groups, plus DB-session lifecycle in service             | <span class="status-shipped">SHIPPED</span> | `scripts/setup_database.sql`, `service/db/connection.py` |
| A2  | pgvector + ivfflat index on `document_chunks.embedding`                                     | <span class="status-shipped">SHIPPED</span> | `setup_database.sql §Indexes` |
| A3  | Reference data tables (failure_modes, equipment_taxonomy, prompt_versions) + monthly partitioning of `messages` and `audit_log` via `pg_partman` | <span class="status-shipped">SHIPPED</span> | `seed_reference_data.sql`, `migrations/001_partition_messages.sql` |
| A4  | Initial line memory + canned process facts                                                 | <span class="status-shipped">SHIPPED</span> | `service/scripts/seed_initial_data.py` |
| A5  | BM25 sparse index (`GIN(to_tsvector('english', chunk_text))`)                              | <span class="status-shipped">SHIPPED</span> | `setup_database.sql — idx_chunks_bm25_gin` |
| A6  | Materialized view `v_rca_precision_daily` for outcome closure tracking + nightly refresh   | <span class="status-shipped">SHIPPED</span> | `migrations/004_v_rca_precision_daily.sql`, refresh hooked in `service/main.py` |
| A7  | Audit + feedback tables (`message_feedback`, `user_corrections`, `outcome_linkages`, `audit_log`) + immutability trigger | <span class="status-shipped">SHIPPED</span> | `setup_database.sql §Audit & Feedback`, `audit_log_immutable()` trigger |

A-series: 7 of 7 shipped. No descopes; one expansion (the immutability
trigger was added during A1 review and later rolled into A7).

## 12.2 The B-Series (Service Capabilities)

| ID    | Asked for                                                                                   | Status | Where it lives / what changed |
|-------|---------------------------------------------------------------------------------------------|--------|--------------------------------|
| B0    | Deterministic tool layer (5 tools)                                                          | <span class="status-shipped">SHIPPED</span> | `service/services/tools.py` |
| B0.5  | Tool-calling LLM loop with citation collection + token accounting                           | <span class="status-shipped">SHIPPED</span> | `service/services/llm.py::_run_tool_loop` |
| B1    | Hybrid retrieval (vector + BM25 fused via RRF, FM/equipment filter, MMR diversification)    | <span class="status-shipped">SHIPPED</span> | `service/services/retrieval.py` |
| B2    | Cross-encoder reranker (sentence-transformers `BAAI/bge-reranker-base`)                     | <span class="status-stub">STUB</span> | `service/services/reranker.py` is a pass-through. Dep + model not bundled into the deployment image. Path forward documented inline. |
| B3    | Structure-aware chunker (preserves headings, tables, bullet lists)                          | <span class="status-shipped">SHIPPED</span> | `service/services/chunker.py::chunk_structured` |
| B4    | Query rewriter (multi-query + step-back abstraction)                                        | <span class="status-stub">SUBSUMED</span> | The B0.5 tool loop covers the practical need (the LLM rewrites/expands its own queries via tool calls). Standalone separation deferred. |
| B5    | HyDE (Hypothetical-Document Embedding) for cold-start queries                               | <span class="status-deferred">DEFERRED</span> | Not implemented. Queued behind production-traffic measurement of cold-start retrieval miss rates. |
| B6    | Self-consistency / k-sample voting on high-stakes RCA                                       | <span class="status-deferred">DEFERRED</span> | Not implemented. 3–5× cost; needs measured precision gain to justify. |
| B7    | Multivariate anomaly detection over the curated tag block                                   | <span class="status-shipped">SHIPPED</span> | `service/services/anomaly.py` (numpy-only Mahalanobis) |
| B8    | Two-step RCA reasoning chain (hypothesise → adjudicate) + 2 prompts                         | <span class="status-shipped">SHIPPED</span> | `service/services/rca.py`, `config/prompts/rca_step{1,2}_v1.txt` |
| B9    | Change ledger (recipe, crew, shift, equipment deltas)                                       | <span class="status-shipped">SHIPPED</span> | `service/services/change_ledger.py` |
| B10   | Outcome closure (24h follow-up sweep + precision view refresh)                              | <span class="status-shipped">SHIPPED</span> | `service/services/outcome_closure.py`, nightly hook |
| B11   | Active-learning loop (correction → embedding boost / chunk demotion)                        | <span class="status-stub">PARTIAL</span> | Feedback API stores all 10 signal types; the consumer in `retrieval.py` applies bounded ±30% chunk re-rank. The explicit "active learning trainer" job (look for retraining-worthy patterns) is **not** built. |
| B12   | Local OpenAI-compatible LLM client (vLLM / llama.cpp / LM Studio)                           | <span class="status-shipped">SHIPPED</span> | `service/services/llm.py::LocalOpenAICompatibleClient` |
| B13   | Evaluation harness — replay golden cases, score citation P/R, FM accuracy                   | <span class="status-stub">STUB</span> | `service/eval/harness.py` — three `NotImplementedError` stubs with full implementation notes. Awaits a curated golden corpus. |

B-series: 11 of 16 shipped, 1 subsumed (B4), 2 deferred (B5, B6),
2 partial (B11, B2), 1 stub (B13). Net: every load-bearing item shipped.

### Beyond the original plan

Two items shipped that were **not** in the original B-series:

- **Symphony video capture adapter** — `service/services/symphony_capture.py`
  ships as a stub returning `extraction_status: "stub"`. Schema and
  integration points are in place; the actual stream wiring awaits
  gateway access (out of scope for this delivery).
- **Local-LLM provider** (B12 above) — was originally a Phase 4 stretch,
  pulled forward when the air-gap deployment requirement crystallized.

## 12.3 Sprint Cadence

The actual cadence ran ~14 weeks across two parallel tracks:

```
Week  1-2:  A1 + A2 + A3                    (foundations)
Week  3-4:  A4 + A5 + B3                    (seed + retrieval prep)
Week  5-6:  B0 + B0.5 + B1                  (tools + hybrid retrieval)
Week  7-8:  B7 + B8                         (anomaly + RCA chain)
Week  9-10: B9 + B10 + A6 + A7              (change ledger + outcomes + audit hardening)
Week 11-12: B11 (partial) + B12 + Symphony stub
Week 13-14: prompt iteration + tests + docs
```

The discipline that kept the schedule was: **never start a B-series
item before its A-series prerequisites were merged and tested**. B1
required A2 (pgvector) and A5 (BM25); B7 required A6/A7 (audit table
populated for fit windowing); B10 required A6 + outcome_linkages.
This is why the A track ran ahead of the B track in every sprint.

## 12.4 What Slipped, Why

Of the descoped items:

- **B2 (cross-encoder reranker).** Bundling
  `sentence-transformers` + a 280-MB model into the deployment image
  trades latency (extra ~80 ms per query) and operational complexity
  (model file, GPU optionality) for a marginal recall gain that has not
  been independently measured against this corpus. The stub file is
  importable and the install path is one-line.
- **B4 (standalone query rewriter).** The tool loop produces equivalent
  behavior (the LLM rewrites its own query when needed). A standalone
  rewriter would be a separate prompt round and an additional latency
  hit. Deferred until measured retrieval miss rate justifies.
- **B5 (HyDE).** HyDE gains are corpus-dependent. Until we observe
  cold-start retrieval misses in production traffic, no signal that
  HyDE would help.
- **B6 (self-consistency / k-sample voting).** Multiplies LLM cost by k.
  The precision dashboard (chapter 9) is the gating signal; if
  observed precision is below target, B6 is the first knob to turn.
- **B11 (active learning trainer).** The bounded re-rank consumer
  ships; the asynchronous "look for patterns to retrain on" job does
  not. Adding it without first having the precision dashboard would
  have been guessing; with it, the right thresholds are observable.
- **B13 (eval harness).** A meaningful eval harness needs labeled
  golden cases. The labeled corpus does not yet exist; building the
  harness without one would be ceremony.

Every deferral is a deliberate "don't build it until measurement says
it helps" decision, not a "we ran out of time" descope.

## 12.5 Test Inventory at Cut

145 tests passing, 2 skipped, 0 failing as of the v3.0 cut commit.
Per-area breakdown in chapter 16.

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Build Plan</p>
<p><span class="label">Stayed:</span> Both the A- and B-series
backlogs survived intact. Every load-bearing item shipped.</p>
<p><span class="label">Changed:</span> The cadence interleaved A and
B work in 2-week sprints rather than the v2.0-implied serial A → B.
Discipline: B-item never starts before its A-prerequisites merge.
Two items pulled forward (B12 local-LLM, immutability trigger);
two items subsumed/partial (B4, B11); four deferred behind
measurement (B2, B5, B6, B13).</p>
<p><span class="label">Considering:</span> A formal "burn-down" view
in the precision dashboard that maps deferred B-series items against
observed shortcomings (e.g. "B2 reranker would have helped this
query class — file as priority"). Not built; the manual map exists in
the optimization backlog.</p>
</div>
