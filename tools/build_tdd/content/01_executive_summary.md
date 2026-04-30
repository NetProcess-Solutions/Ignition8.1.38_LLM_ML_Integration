# 1. Executive Summary

## Overview

A read-only conversational AI advisor, embedded in Ignition Perspective, that
answers operator and engineer questions about Coater 1 using live tag data,
historian aggregates, maintenance and quality records, downtime and defect
events, deterministic process rules, distributional baselines, and curated
line memory. Every response is source-cited, confidence-labeled, and fully
auditable. The system is shipped — 155 tests pass, 0 fail, 2 skip; the
schema is built; the RAG pipeline runs end-to-end; the gateway integration
spec is documented and templates are in place; only the operator-side
"go-live" steps in §3 of `docs/GAP_ANALYSIS.md` remain.

## Problem &amp; Approach

An industrial chatbot that hallucinates is worse than useless — operator
trust, once lost, does not return. Off-the-shelf LLMs fabricate tag values,
invent document references, and answer confidently from insufficient
evidence. This product is built grounding-first: retrieval quality, citation
traceability, and refusal behavior are the core value proposition of the
MVP, not incremental features. Predictive ML (supervised classifiers, vision
models) is architecturally provisioned but deliberately deferred until the
grounding layer has proven reliable in production.

What changed since v2.0: the grounding layer ships with **strictly more
mechanism** than v2.0 specified. Hybrid retrieval, deterministic tool calls
the LLM can invoke for distributional facts, an explicit two-step RCA
reasoning chain, multivariate anomaly detection over the curated tag block,
and a change ledger that auto-surfaces "what changed since baseline" — all
contribute to a tighter grounding loop than the v2.0 design contemplated.
None of these introduce model-side learning; every one of them is
deterministic, auditable, and bounded.

## Key Capabilities (As Built)

- **Query-Anchored Context Assembly.** Every query is parsed for its temporal
  anchor (past event, current state, or pattern) before evidence is
  assembled. Retrospective queries never see current live tag state;
  current-state queries never see stale event data. The anchor is explicit,
  clarified with the user when ambiguous, and audited.
  ✅ Shipped: `service/services/anchor.py`, exhaustively tested in
  `service/tests/test_anchor.py` and `test_anchor_regression.py`.

- **Conditional Baselines.** Tag evidence is contextualized against five
  temporal buckets — 60 min before anchor, 24 h before anchor, a 14-day-prior
  normal-operation sample, the last four runs prior, and every prior run of
  the same style matched to the same failure mode. Values are rendered with
  distribution context, not just raw numbers.
  ✅ Shipped: `services/baseline_cache.py`, `services/context_assembler.py`.

- **Tag-Class Aware Deviation.** Setpoint-tracking, oscillating-controlled,
  process-following, and discrete-state tags each get appropriate deviation
  tests. Oscillations around setpoint are recognized as normal operation;
  amplitude changes are flagged as anomalies.
  ✅ Shipped: `services/deviation.py` (was `anomaly.py`, renamed during B7
  to disambiguate from the multivariate anomaly detector).

- **Hybrid Retrieval (NEW vs v2.0).** Vector cosine + BM25 trigram fused via
  Reciprocal Rank Fusion (RRF), conditionally boosted by failure-mode and
  equipment metadata, then diversified via Maximal Marginal Relevance (MMR)
  with token-level Jaccard similarity. Bounded retrieval-quality blending
  from feedback (±30%). Plant-specific document weights (1.0–1.3) override
  general reference textbook chunks (0.5–0.7).
  ✅ Shipped: `services/retrieval.py::retrieve_chunks_hybrid`. Tested in
  `test_retrieval_hybrid.py` and `test_retrieval_golden.py`.

- **Deterministic Tool Layer (NEW vs v2.0).** Five typed read-only tools the
  LLM can call mid-completion: `percentile_of`, `compare_to_distribution`,
  `nearest_historical_runs`, `detect_drift`, `defect_events_in_window`.
  Each returns a `ToolResult` with auto-generated citation. Hard SQL
  timeouts; no tool ever writes.
  ✅ Shipped: `services/tools.py`, `services/llm.py::_run_tool_loop`.
  Tested in `test_tools.py` and `test_llm_tool_loop.py`.

- **Two-Step RCA Reasoning Chain (NEW vs v2.0).** When the parsed anchor is
  a past event AND the query has causal intent ("why did", "what caused",
  "root cause"), the orchestrator dispatches a two-step LLM workflow:
  (1) generate up to N hypotheses with required evidence, (2) gather evidence
  via deterministic tools under a hard budget, (3) adjudicate. Step 1 is
  TTL-cached on `(anchor_event_id, anchor_run_id, anchor_time, failure_mode,
  prompt_version)` so repeat questions about the same event do not re-pay.
  ✅ Shipped: `services/rca.py`. Tested in `test_rca.py` and
  `test_rca_e2e.py`.

- **Mandatory Source Citations.** Every factual claim carries a numbered,
  provenance-typed citation (live tag, historian stat, document, event,
  work order, camera clip, memory, rule, ML prediction, distribution,
  nearest-runs, drift, tool result). Responses with zero citations are
  downgraded by the parser and flagged.
  ✅ Shipped: 19 provenance types in `models/schemas.py::SourceCitation.type`,
  validated by `services/response_parser.py`.

- **Engagement with Honest Labeling.** The advisor engages with the math,
  abnormalities, and discrepancies it can see. Claims are labeled CONFIRMED
  FACT, LIKELY CONTRIBUTOR, or HYPOTHESIS. Missing evidence channels (cameras,
  lab results) are called out in the response as context, not used as grounds
  for refusal.
  ✅ Shipped: enforced in `service/config/prompts/system_prompt_v2.txt`
  §§4–5 and validated by `services/response_parser.py::parse_confidence`
  with downgrade-on-no-citation.

- **Full Audit Records.** Every response persists the exact tags read,
  aggregates computed, chunks retrieved, memories used, rules matched,
  parsed anchor, prompt version, model parameters, and the full RCA trace
  when the chain ran. Any answer can be reconstructed and reviewed.
  ✅ Shipped: `messages.context_snapshot` JSONB column populated in
  `services/rag.py::handle_chat` Phase 3.

- **Structured Feedback Loop.** A multi-level signal taxonomy feeds a staged
  memory-candidate workflow. No feedback auto-promotes — every memory entry
  passes an engineer checkpoint. Bounded ±30% retrieval re-ranking from
  accumulated chunk quality signals.
  ✅ Shipped (signals + bounded blending): `routers/feedback.py`,
  `routers/corrections.py`, `routers/outcomes.py`. Memory candidate
  promotion UI is <span class="status-deferred">DEFERRED</span> to phase 3.

- **Outcome Closure.** Past-event messages older than 24 h with no outcome
  linkage are surfaced for follow-up; precision per failure mode is
  aggregated nightly into the `v_rca_precision_daily` materialized view.
  ✅ Shipped: `services/outcome_closure.py`,
  `scripts/migrations/004_v_rca_precision_daily.sql`.

## Technical Foundation

PostgreSQL 16 with `pgvector` (ivfflat index, with `hnsw` migration plan
documented in `scripts/migrations/003_pgvector_index_migration.sql`);
`pg_trgm` for keyword retrieval; `uuid-ossp` for UUID PKs;
`pg_partman` for monthly partitioning of `messages` and `audit_log` (one
partition per month, retained 24 months by default). FastAPI orchestration
with `asyncio` tool-call loop; per-user rate limiting via `slowapi` and
Prometheus metrics via `prometheus-fastapi-instrumentator`. Ignition
Perspective front-end with gateway-side Jython 2.7 scripts that read tags,
query the historian, evaluate alarms, and assemble the curated context
package. `all-MiniLM-L6-v2` embeddings (384 dim) served in-process. LLM
provider is pluggable: OpenAI, Azure OpenAI, or any OpenAI-compatible
endpoint (vLLM, llama.cpp server, LM Studio); switching providers is a
single `LLM_PROVIDER` env-var change.

Tag discovery is **scaffolded but not yet wired**: the `tag_registry` table
exists and the `ItemInstance`-query design is documented (§15), but the
gateway-side enumeration runs through a hardcoded `KEY_TAGS` list in
`ignition/scripts/config.py` for the MVP. The pre-screen `services/tag_selector.py`
filters that catalog by category and keyword to keep curated payloads
bounded. This is a deliberate MVP simplification with a documented path
forward (§15 and `/memories/repo/optimization_backlog.md`).

Symphony camera integration is <span class="status-stub">STUB</span>:
`services/symphony_capture.py` returns `extraction_status="stub"` until the
Symphony API endpoint and auth credentials are wired in. Schema
(`event_clips` table) is in place from day one so live wiring is purely an
adapter swap.

The full schema (30 tables across 9 domains, including the `failure_modes`
reference table that backs the closed enum) is provisioned by
`scripts/setup_database.sql` on first container start.

## Scope &amp; Phased Roadmap (Updated)

- **MVP (Phase 1–2):** ✅ **Shipped.** Grounded RAG chat with query-anchored
  context, conditional baselines, tag-class aware deviation, hybrid
  retrieval, deterministic tool layer, RCA chain, distributional grounding,
  multivariate anomaly, change ledger, standardized defect-mode taxonomy,
  work order structured-and-narrative ingestion, feedback + correction +
  outcome linkage, role-based presentation, full audit trail.

- **Phase 3 (partial):** Some Phase-3 items shipped early (multivariate
  anomaly was implemented as B7 within the MVP because it required no
  labels). Remaining Phase 3: memory curation UI, AI-suggested memory
  candidates, distribution-shape SVG visualizations in the chat UI, and the
  challenge-flow UI.

- **Phase 4:** Supervised classifiers (delam, failure-mode prediction)
  trained on outcome-validated event labels with SHAP explanations; vision
  models on high-value camera angles; enterprise scaling via the UNS.
  All <span class="status-considering">CONSIDERING</span> — not started.

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Executive summary</p>
<p><span class="label">Stayed:</span> Doctrine intact: read-only,
grounding-first, every claim cited, refusal narrowed to scope.</p>
<p><span class="label">Changed:</span> Capabilities list expanded with
five additions that were not in v2.0 — hybrid retrieval, tool layer,
RCA chain, distributional grounding, change ledger. All are deterministic
and bounded; none introduce model-side learning.</p>
<p><span class="label">Considering:</span> Cross-encoder reranker (B2),
HyDE (B5), self-consistency / k-sample voting (B6), and explicit active
learning trainer (B11) remain on the table for after a shift of
production traffic clarifies what the bottleneck actually is.</p>
</div>
