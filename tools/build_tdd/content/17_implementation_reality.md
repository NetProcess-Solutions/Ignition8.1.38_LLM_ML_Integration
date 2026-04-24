# 17. Implementation Reality (Cross-Cutting Deltas)

This chapter consolidates every meaningful difference between the
v2.0 design document and what actually shipped, in one place. The
preceding chapters discuss deltas in the context of their subject
matter; this chapter is the single-pane-of-glass index for the
question *"what is different from v2.0?"* — the question the
maintainer two years from now will most want answered.

The structure follows the user's original request: **what stayed,
what changed, what we're considering**. Every entry references the
chapter that goes deep on it.

## 17.1 What Stayed (Faithful to v2.0)

These v2.0 commitments survive intact in v3.0. If you read the v2.0
document expecting to find these in the as-built code, you will.

| Area | What stayed | Chapter |
|------|-------------|---------|
| Read-only architecture | No write path to PLCs/setpoints/recipes/alarms exists | 14 |
| Two-layer auth | API key + gateway-issued JWT (HS256) on every request | 14 |
| Curated context only | Raw historian dumps never reach the LLM; pre-aggregation only | 4 |
| Anchor-conditional buckets | Past-event vs current-state vs pattern; 5 buckets, conditional inclusion | 4 |
| Mandatory citations | `[N]`-style citations validated, missing ones surfaced | 4 |
| Confidence labels | CONFIRMED \| LIKELY CONTRIBUTOR \| HYPOTHESIS \| INSUFFICIENT EVIDENCE | 4 |
| Refusal narrowed to scope | Refuse only on out-of-corpus, control command, or no retrieval | 4 |
| 4-class tag taxonomy | Setpoint-tracking, oscillating-controlled, process-following, discrete-state | 4, 6 |
| Engineer-mediated memory | Memory candidates → engineer review → approved memory | 9 |
| Memory challenge threshold | 3 independent challenges → status flips to challenged | 9 |
| Single-VM Docker Compose | Postgres + service + (optional) vLLM sidecar | 13 |
| `/api/health` deployment probe | Returns 200 with `{db, embeddings, llm}` status | 13 |
| FastAPI + Pydantic + asyncpg | The framework choice was correct and held | 3 |
| Materialized RCA precision view | `v_rca_precision_daily` is the trust dashboard | 9 |

## 17.2 What Changed (Beyond v2.0)

These are the substantive deltas — additive functionality that wasn't
in v2.0 or that materially differs from it. They are what justify a
v3.0 cut rather than a v2.x patch.

### Substrate-level changes

| Change | What it is | Chapter |
|--------|-----------|---------|
| **Hybrid retrieval (B1)** | Vector + BM25 fused via RRF (k=60), MMR diversification (λ=0.7), conditional FM/equipment boost (1.5×/1.3×), document_role weighting clamped to [0.5, 2.5] | 6 |
| **Deterministic tool layer (B0)** | 5 tools: `percentile_of`, `compare_to_distribution`, `nearest_historical_runs`, `detect_drift`, `defect_events_in_window`. Hard SQL timeouts, auto-citations, bounded result sizes | 7 |
| **Tool-calling LLM loop (B0.5)** | Provider-agnostic `_run_tool_loop` handles OpenAI/Azure/local identically; budget tracking; full tool-call persistence to `messages.tool_calls` | 7 |
| **Two-step RCA chain (B8)** | Hypothesise (allowlist=4 distributional tools) → tools execute → adjudicate (full toolset). Bounded budget (15 calls), TTL cache, full trace to `messages.rca_summary` | 7 |
| **Distributional grounding service** | `services/percentiles.py` with 6 scopes (global, style, style_step, equipment, recipe, global_ytd); Page-Hinkley CUSUM drift; in-process CDF cache | 8 |
| **Multivariate anomaly (B7)** | Numpy-only Mahalanobis with ridge stabilization, p95 threshold, top-K contributing tags, 4-hourly re-fit | 8 |
| **Change ledger (B9)** | TagDelta (sigma-ranked, top 10), RecipeDelta, CrewDelta, EquipmentChangeover. Rendered as prompt section L | 8 |
| **Outcome closure (B10)** | Nightly sweep, `outcome_linkages`, materialized view refresh, 4 outcome types | 9 |
| **Local-LLM provider (B12)** | OpenAI-compatible adapter unblocks fully air-gapped deployments (vLLM, llama.cpp, LM Studio) | 7 |
| **Structure-aware chunker (B3)** | `chunk_structured` preserves headings, tables, bullet lists; chunk_type column added | 5, 6 |

### Schema and data changes

| Change | What it is | Chapter |
|--------|-----------|---------|
| **`failure_modes` reference table** | Closed-enum FK enforcement on `defect_events.fm_code`. Hardens hallucination guardrail at DB layer | 5 |
| **`chunk_quality_signals` table** | Per-chunk feedback counters drive bounded ±30% retrieval re-rank | 5 |
| **`tag_registry` table (scaffolded)** | Provisioned but unpopulated; forward-compatible for B13/A5/A6 cutover | 5, 15 |
| **pg_partman monthly partitioning** | `messages` and `audit_log` partitioned by month, 24-month hot retention | 5, 13 |
| **`audit_log_immutable` trigger** | DB-layer prohibition of UPDATE/DELETE on `audit_log` | 5, 14 |
| **Audit hash chain** | Each row's `audit_hash` chained from prior; tamper-detection at row level | 14 |
| **`v_rca_precision_daily` materialized view** | Migration 004; nightly refresh hooked in `main.py::lifespan` | 5, 9 |
| **`v_pgvector_index_status` view** | Operator-facing readiness check for ivfflat → hnsw cutover | 5, 13 |
| **`v_chat_perf_daily` materialized view** | Per-day p50/p95 latency + token cost rollup | 5 |

### 10-value feedback enum (was 3)

| v2.0 | v3.0 |
|------|------|
| `helpful`, `unhelpful`, `refute` | `helpful`, `unhelpful`, `wrong_anchor`, `wrong_failure_mode`, `wrong_citation`, `missed_evidence`, `actionable`, `not_actionable`, `confirmed_outcome`, `refuted_outcome` |

The wider enum is what makes the re-ranker meaningful and the
precision dashboard useful.

### Three-phase request lifecycle

The `services/rag.py::handle_chat` orchestrator runs three explicit
phases, each with its own DB-session discipline:

- **Phase 1 (Pre-LLM)** — owns its own DB session; builds anchor,
  context, change ledger, anomaly score, decides RCA path
- **Phase 2 (LLM)** — no DB session held; tool calls re-acquire
  short-lived sessions
- **Phase 3 (Persist)** — new DB session for `messages` + `audit_log`
  inserts, hash chain extension

This was implicit in v2.0; v3.0 makes it explicit and enforces it
through code structure (chapter 11).

### Observability

Three-layer (structlog + Prometheus + Postgres logs) with a reference
Grafana dashboard. Documented alert thresholds. Per-stage Prometheus
histograms surfacing where latency lives (chapter 13).

### Deployment cutover procedures

Three documented procedures (chapter 13):
- ivfflat → hnsw (zero-downtime hot index swap)
- Prompt version bump (rolling activation in `prompt_versions`)
- Embedding-model bump (sibling-column backfill)

## 17.3 What Diverged or Slipped (Honest Disclosure)

Items where the as-built differs from v2.0 in a way that is **not**
strictly an addition.

### `tag_registry` is a scaffold, not the source of truth

v2.0 specified the gateway-discovered tag registry. v3.0 ships the
table provisioned but unpopulated; the actual catalog is the hardcoded
`KEY_TAGS` list in `ignition/scripts/config.py`. The forward path is
documented (chapter 15 §15.4) and is a ~3-week piece of work, but it
is not in the v3.0 cut.

### B2, B5, B6, B11, B13 — deferred behind measurement

| ID | Item | Status | Rationale |
|----|------|--------|-----------|
| B2 | Cross-encoder reranker | <span class="status-stub">STUB</span> | Marginal recall gain unmeasured against this corpus; install path documented inline |
| B5 | HyDE | <span class="status-deferred">DEFERRED</span> | Cold-start retrieval miss rate not yet measured |
| B6 | k-sample voting | <span class="status-deferred">DEFERRED</span> | Multiplies LLM cost by k; gating on observed precision |
| B11 | Active-learning trainer | <span class="status-stub">PARTIAL</span> | Bounded re-rank consumer ships; the asynchronous trainer job does not |
| B13 | Eval harness | <span class="status-stub">STUB</span> | Blocked on absence of labeled golden corpus |

Every deferral is a "don't build it until measurement says it helps"
decision (chapter 12 §12.4), not a "ran out of time" descope. The
optimization backlog
([/memories/repo/optimization_backlog.md](/memories/repo/optimization_backlog.md))
is the standing list of "build when measurement justifies."

### Symphony video capture is a stub

`services/symphony_capture.py` returns
`extraction_status: "stub"`. Schema (`event_clips.extracted_text`) is
in place; integration awaits gateway-side stream access.

### Multi-line / multi-plant is config, not code

Schema supports it (every table has `line_id`); seed data and
Perspective view ship configured for Coater 1 only. Adding line N+1
is a configuration exercise.

## 17.4 What We're Considering (Roadmap Nudge)

These are the items not yet committed to a sprint but actively under
consideration. Full backlog in chapter 18.

### Quality polish (gated on pilot measurement)

- B2 reranker, when pilot traffic shows recall-bound queries
- B5 HyDE, when cold-start retrieval misses are observed
- B6 k-sample voting on high-stakes RCA, when precision dashboard
  flags drift below target
- B11 trainer job, when feedback volume justifies pattern mining

### Substrate extension

- Per-claim citation enforcement (currently per-response validation)
- Step-back query abstraction as a deterministic preliminary tool
- Streaming LLM responses for sub-second perceived latency
- Per-style ANN partitioning when corpus exceeds 1M chunks

### Operational

- Multi-instance HA deployment guide
- Postgres logical-replication read replica for analytics
- Auto-tuned `lists` parameter on ivfflat as row count grows
- Formal penetration test against the deployed stack

### ML maturation (Phase 4)

- Time-series forecasting models on key tags (substrate already wired
  via `feature_snapshots`, `ml_models`, `ml_predictions`)
- Coating-specific fine-tuned LLM on the accumulated correction corpus
- Cross-LLM ensembling for high-stakes safety incidents
- Per-failure-mode predictive model with operator-facing "scrap risk in
  next 30 min" surface

### Personalization (chapter 10 deferred items)

- Density preference applied at response shaping
- Per-role tool subsets
- Saved-question library with role scoping

## 17.5 The Honest One-Sentence Summary

v3.0 ships the v2.0 architecture **and** a working hybrid retrieval
pipeline, deterministic tool layer, two-step RCA chain, multivariate
anomaly detection, change ledger, outcome closure, local-LLM provider,
and per-row tamper-evident audit log — with five quality-polish items
explicitly deferred behind measurement and one (the discovered tag
registry) deferred behind the second line.

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Reality Index</p>
<p><span class="label">Stayed:</span> Read-only architecture, two-layer
auth, curated context only, anchor-conditional buckets, mandatory
citations, confidence labels, narrowed refusal, 4-class tag taxonomy,
engineer-mediated memory, FastAPI + Pydantic + asyncpg.</p>
<p><span class="label">Changed:</span> Hybrid retrieval (B1), 5-tool
deterministic layer (B0), tool-calling loop (B0.5), two-step RCA chain
(B8), distributional grounding service, Mahalanobis anomaly (B7),
change ledger (B9), outcome closure (B10), local-LLM provider (B12),
structure-aware chunker (B3), failure_modes FK enforcement,
chunk_quality_signals, pg_partman partitioning, audit_log immutability
trigger + hash chain, 10-value feedback enum, three-phase request
lifecycle, three-layer observability, three documented cutover
procedures.</p>
<p><span class="label">Considering:</span> Cross-encoder reranker (B2),
HyDE (B5), k-sample voting (B6), active-learning trainer (B11), eval
harness (B13), discovered tag registry (gateway B13/A5/A6 wiring),
Symphony stream wiring, multi-line config, multi-instance HA,
Postgres read replica, ML maturation (Phase 4), per-role response
shaping.</p>
</div>
