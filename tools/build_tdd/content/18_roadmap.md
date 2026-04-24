# 18. Updated Phased Roadmap

The v2.0 document framed the work in three phases. v3.0 is the
landing of phases 1–2 + most of phase 3, with phase 3 polish items
gated on measurement and phase 4 (ML maturation) wired in substrate
form. This chapter is the explicit forward roadmap from the v3.0 cut.

## 18.1 Phase Status

| Phase                           | v2.0 Plan                                             | v3.0 Status |
|---------------------------------|-------------------------------------------------------|-------------|
| **Phase 1 — Foundations**       | DB schema, ingestion, basic retrieval, single-shot LLM | <span class="status-shipped">SHIPPED</span> (A1–A7) |
| **Phase 2 — Trust Substrate**   | Citations, refusal, anchor parsing, audit, feedback   | <span class="status-shipped">SHIPPED</span> (chapters 4, 9, 14) |
| **Phase 3 — Capability Depth**  | Hybrid retrieval, tools, RCA chain, anomaly, ledger    | <span class="status-shipped">SHIPPED</span> for load-bearing items; polish items deferred |
| **Phase 4 — ML Maturation**     | Predictive models, fine-tuning, evaluation, distillation | <span class="status-stub">SUBSTRATE WIRED</span> (chapters 5 §5.9, 16 §16.4) |

## 18.2 Pilot Window (Weeks 1–4 Post-Go-Live)

The pilot phase is **observation-driven**. Build nothing speculative;
fill the placeholder slots in the configuration; deploy; observe.

### Pre-go-live checklist (chapters 13, 16)

1. Replace every placeholder per [INSTALL.md](INSTALL.md) §3:
   `API_KEY`, `DATABASE_URL`, `OPENAI_API_KEY`, `GATEWAY_JWT_SECRET`,
   gateway `AI_SERVICE_URL` + `LINE_ID` + `TAG_PROVIDER` +
   `COATER1_ROOT`
2. `docker compose up postgres` → run `setup_database.sql` →
   `seed_reference_data.sql` → `python -m service.scripts.seed_initial_data`
3. `docker compose up service` → `GET /api/health/deep` → expect 200 with `{db, embeddings, llm}` ok
4. From a service-host shell: `POST /api/chat` with a hand-built
   structured query, confirm grounded response with citations
5. Wire Ignition (INSTALL.md Part 5), open Perspective ChatView,
   send the first real operator query

### First 4 weeks of measurement

Track these metrics weekly via the Grafana dashboard:

- p50 / p95 chat latency (target: ≤ 4s p95)
- Refusal rate (target: ≤ 15%; spikes signal ingestion gap)
- Tool-call cost per query (track for budget calibration)
- RCA precision (target: ≥ 60% strict, ≥ 80% lenient at 30-day window)
- Per-prompt-version answer distribution
- Top 5 highest-frequency unique queries (for SOP gap detection)
- Per-operator usage (find power users; recruit them as labelers
  for B13)

## 18.3 Sprint A (Weeks 5–10) — Polish on Demand

Six work items, prioritized by what the pilot measurement actually
showed. Each item is gated on a specific signal:

| Item | Build trigger | Estimated work |
|------|---------------|----------------|
| **B2 (cross-encoder reranker)** | Recall-bound query class observed in pilot traffic | 1 week |
| **B5 (HyDE)** | Cold-start retrieval miss rate > 10% | 1 week |
| **B6 (k-sample voting)** | RCA precision < target on high-stakes queries | 1.5 weeks (incl. budget tuning) |
| **B11 trainer job** | Sufficient feedback volume (>500/wk) and re-rank consumer evidence of plateau | 2 weeks |
| **B13 eval harness build-out** | ~50 labeled golden cases collected from pilot traffic | 2 weeks (after corpus exists) |
| **`tag_registry` cutover** | Second line in scope OR ≥3 SOP gaps traced to missing-tag observability | 3 weeks |

The expected outcome of Sprint A: **at most three** of the six items
are built. The trigger gates exist precisely so the team doesn't
build all six speculatively. If pilot measurement shows none of the
triggers, Sprint A is replaced by Phase 4 ML preparation.

## 18.4 Phase 4 — ML Maturation (Quarter 2 Post-Go-Live)

The substrate is in place. Phase 4 is the actual model work.

### 4.1 Predictive coating-weight model

Substrate: `feature_snapshots` populated by every Phase 1 query;
`production_runs.target_specs` carries the labels (`coating_weight_target`,
`measured_coating_weight`).

Build:

1. Curate a 90-day window of `feature_snapshots` joined to
   `quality_results.coating_weight`
2. Train baseline gradient boosted regressor (`xgboost` or `lightgbm`)
3. Register in `ml_models` with `is_active = false`
4. Shadow-predict against live snapshots for 4 weeks; persist to
   `ml_predictions`
5. Compare prediction vs realized coating weight; promote if MAE
   below threshold

The model exposes via a 6th tool: `predict_coating_weight(snapshot)`
returns the model's prediction with confidence interval. The LLM
consumes it like any other tool result.

### 4.2 Failure-mode classifier (auto-FM-tagging)

Substrate: `messages.failure_mode_code` is auto-populated today by
the LLM; ground truth is `defect_events.fm_code` for confirmed
outcomes via `outcome_linkages`.

Build:

1. Train a small LLM-based classifier (or a TF-IDF + LR baseline) on
   the labeled `(query, outcome.fm_code)` pairs
2. Compare against the LLM's auto-tag; surface disagreements as
   review candidates
3. Use as a regularizer on the eval harness — flag responses where
   classifier and LLM disagree as high-priority for review

### 4.3 Fine-tuned coating-specific LLM

Substrate: `user_corrections` and `outcome_linkages` form a labeled
correction corpus.

Build (approximately Q3+):

1. Curate the correction corpus into `(query, context, gold_response)`
   triples
2. SFT a base 8B model (`Llama-3.1-8B-Instruct`, `Mistral-7B-Instruct`,
   `Qwen2.5-7B`) on the triples
3. Deploy via the local-LLM provider (B12) — no service code change
4. A/B against the OpenAI baseline via prompt-version tracking
5. Promote when precision and operator satisfaction match or exceed

This is the path to true cost independence and full air-gap
operation.

### 4.4 Distillation track

Substrate: existing prompts + the curated correction corpus + the
fine-tuned model.

Build:

1. Use the fine-tuned model to label a much larger synthetic corpus
2. Distill into a 1–3B model suitable for edge deployment (per-line
   gateway-co-located inference)
3. Reserve the cloud / 8B model for the RCA chain (high-stakes);
   fast queries hit the edge model

## 18.5 The Honest Decision Tree

The roadmap above is conditional. The decision tree:

```
                         pilot for 4 weeks
                                │
                  ┌─────────────┼──────────────┐
                  ▼             ▼              ▼
            metrics good   metrics mixed   metrics poor
                  │             │              │
                  ▼             ▼              ▼
         skip Sprint A,    Sprint A:      Sprint A:
         go Phase 4        2-3 items      eval harness FIRST,
                                          then triggered items
                  │             │              │
                  └─────┬───────┴──────────────┘
                        ▼
                   Phase 4 ML
                  (Q2 onward)
```

This is the discipline of "don't build it until measurement says it
helps" applied to the roadmap as a whole.

## 18.6 Out-of-Scope (Won't Build)

These were considered and explicitly excluded. They are not phase-N
items waiting for prioritization; they are *no*.

- **Writing back to PLCs.** Architectural principle #1.
- **Voice input.** Out of scope; Perspective handles touch + text well.
- **Native mobile app.** Perspective's responsive layout works on
  tablets; a native app is out of scope for the pilot.
- **Open document upload by operators.** Engineer-mediated ingestion
  only; operators surface `missed_evidence` signals, engineers approve.
- **Cross-plant memory sharing.** Pilot is one plant. Cross-plant
  knowledge sharing requires a separate governance discussion.

## 18.7 The Long-Run Vision

In ~12 months from go-live, the system should look like:

- Pilot expanded to all coating lines (3–4 lines)
- Discovered tag registry across all lines
- Fine-tuned coating-LLM running on local hardware with cloud fallback
- Eval harness gating every prompt change with ~250 golden cases
- Predictive models for coating weight, scrap risk, equipment failure
  surfacing into the chat panel via tool calls
- Operators view it as another senior engineer who's always on shift
- Engineers view it as the corpus surface that reduces tribal-knowledge
  loss and accelerates new-hire ramp time

That's the destination. The v3.0 cut is the foundation that makes it
reachable in increments.

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Roadmap</p>
<p><span class="label">Stayed:</span> Three-phase framing. Phase 4 as
the ML maturation horizon. The principle that the substrate must come
before the polish.</p>
<p><span class="label">Changed:</span> Phase 3 is largely complete in
v3.0; what remains in Phase 3 is polish gated on pilot measurement.
Phase 4 is now substrate-ready (`feature_snapshots`, `ml_models`,
`ml_predictions` tables populated by the live pipeline). Sprint A is
explicitly conditional ("at most three of six items"). The decision
tree is documented; the discipline is "don't build until measurement
justifies."</p>
<p><span class="label">Considering:</span> Pulling the predictive
coating-weight model forward to Sprint A if pilot measurement makes a
clear case. Doing the discovered-tag-registry work in parallel with
Sprint A if a second line is committed. Beginning the fine-tuned-LLM
distillation in Q2 even if cloud LLM economics remain favorable, as
insurance against vendor risk.</p>
</div>
