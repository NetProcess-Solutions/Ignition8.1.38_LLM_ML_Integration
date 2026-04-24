# 11. End-to-End Walkthrough

This chapter traces a single chat turn from JWT-bearing HTTP POST to
audit-row commit. The walkthrough uses a realistic past-event causal
query — the exact path that exercises the deepest set of services
(anchor, retrieval, change ledger, RCA chain, audit). The content is
**verifiable against `services/rag.py::handle_chat`**, which is the
top-level orchestrator and the canonical reference if this chapter and
the code drift apart.

## 11.1 The Scenario

Operator Dana, on B-shift on 2026-04-21 at 23:55 local, opens the
Coater 1 Perspective chat panel and types:

> *"why did we get the off-tenter on QR-00417 earlier today?"*

The Perspective gateway hits `POST /api/chat` with the JWT in
`Authorization: Bearer ...`, the question text, and a fresh
`conversation_id`. From here the service takes over.

## 11.2 Phase 0 — Auth, Rate Limit, Conversation Resolve

`routers/chat.py::chat()` runs three dependency injections:

1. `require_api_key` — the gateway's fixed shared key (defense-in-depth
   alongside the JWT)
2. `require_attributed_user` — JWT verification (HS256 against
   `gateway_jwt_secret`), claim extraction, `user_profiles` lookup,
   `_PERMISSIONS_CACHE` populate
3. `chat_rate_limits` — slowapi wrapper, `chat_user_key(request)` keys
   on `user_id`, default `10/minute, 200/hour` per user

If any fails, the request is rejected before any DB session is opened.
On success, the route handler enters Phase 1.

## 11.3 Phase 1 — Pre-LLM Build

`rag.handle_chat` opens its own DB session (Phase 1 owns the session).
This matters: the LLM call in Phase 2 is potentially long-running, and
holding a DB connection across it would saturate the pool. The Phase 1
session closes before Phase 2 opens.

### 1a. Anchor parsing

`services.anchor.parse_query_anchor("why did we get the off-tenter on QR-00417 earlier today?")`:

- Detects `QR-00417` via the quality-result regex → sets
  `anchor_event_id = "QR-00417"`, `anchor_type = "past_event"`
- Looks up QR-00417 in `quality_results`, finds `event_time =
  2026-04-21T18:24:00-04:00` → sets `anchor_time` to UTC
- Joins through `attributed_run_id` → `production_runs` row → sets
  `anchor_run_id = "R-20260421-03"`, `style_scope = ["S-4471"]`
- Looks up the failure mode on the matching `defect_event` →
  `failure_mode_scope = ["off_tenter"]`
- Detects no `equipment_scope` in the text (no zone, no specific
  equipment named)
- The control-verb regex does not match
- The pattern-marker regex does not match
- Returns `QueryAnchor(status="ok", ...)`

### 1b. Refusal short-circuit checks

- `is_control_command(query, anchor)` — False (no control verb)
- `is_evidence_insufficient(anchor)` — False (we have a clear anchor and a
  matched run)

Phase 1 continues.

### 1c. Curated context build

`services.context_assembler.build_context(anchor, query, user)`:

1. Tier-1 tags (always-include): pulled at `anchor_time` ±60 min and
   ±24 h windows from historian, summarized per chapter 4 §4.5.
2. Tier-2 routed tags: query text passed to
   `services.tag_selector.select_tags(query, anchor)`. The query has
   no zone or equipment keyword; the matched-history failure mode is
   `off_tenter`, which routes the tenter group via
   `CATEGORY_SYNONYMS["tenter"]` → tenter exit temps, tenter chain
   speed, tenter steam pressure tags pulled with full rendering.
3. Deviations flagged: tag-class tests run; `TenterExitTemp` is +2.7σ
   above its baseline (process-following test); flagged.
4. Recent events: ±72 h around `anchor_time` from the three event
   tables.
5. Failure-mode-matched history:
   `retrieve_failure_mode_matched("S-4471", "off_tenter", anchor_time, k=8)`
   pulls the prior eight S-4471 off-tenter events with their pre-event
   tag behavior.
6. Document retrieval: `retrieve_chunks_hybrid(query, query_embedding,
   scope_filters={"failure_mode": ["off_tenter"], "equipment": []})`
   runs the six-stage pipeline (chapter 6); 10 chunks selected.
7. Business rules: `services.rules.evaluate(live_state)` — three rules
   match.
8. Approved memory: `select_memory(scope=("S-4471", "off_tenter"))`
   returns two memory entries.
9. Camera clips: `select_clips(events_in_scope)` returns one clip
   handle attached to the QR-00417 event.

### 1d. Change ledger

For past-event anchors with matched-history runs, `_maybe_build_change_ledger(current_run, matched_runs)`
runs (chapter 8 §8.3). Result: TenterExitTemp +3.4σ, RecipeDelta on
`recipe_id` (current R102C, baseline R102B in 6/8 prior), CrewDelta
(current B-shift, baseline A-shift in 7/8), one EquipmentChangeover
(`tenter_chain` WO-88245 closed 2026-04-19).

### 1e. Anomaly score

`anchor.anchor_type == "past_event"` → multivariate anomaly is
**not run**. Section M of the prompt will render `[NOT APPLICABLE —
past-event query]`.

### 1f. RCA chain decision

`should_use_rca_chain(query, anchor)`:

- `anchor.anchor_type == "past_event"` ✓
- query matches causal regex (`why did`) ✓
- `settings.rca_chain_enabled` ✓

→ `use_rca_chain = True`

Phase 1 closes its DB session and emits a structured log:

```
{"evt": "phase1.complete", "duration_ms": 312,
 "anchor": "past_event/QR-00417/R-20260421-03/S-4471/off_tenter",
 "context_size_tokens": 4180, "use_rca_chain": true}
```

## 11.4 Phase 2 — LLM Tool Loop (No DB Session)

The LLM stage runs without a DB session held. Tool execution
re-acquires its own short-lived sessions from the pool.

### 2a. RCA Step 1 — Hypothesise

`rca._step1_hypotheses(context, query)`:

1. Cache lookup on `_STEP1_CACHE`. Key:
   `("QR-00417", "R-20260421-03", "2026-04-21T22:24:00Z", "off_tenter", "system_prompt_v2|rca_step1_v1")`.
   Miss.
2. Build messages: `[system: system_prompt_v2 + rca_step1_v1, user: <curated context block>, user: <query>]`
3. `_run_tool_loop(messages, tools=openai_tool_specs(allowlist=DISTRIBUTIONAL_FOUR), max_iters=2, max_total_calls=15)`
4. LLM iteration 0: response includes tool calls
   - `compare_to_distribution(tag="TenterExitTemp", value=192, scope="style_step", scope_key="S-4471/2", k=5)` → 91st percentile, nearest-runs include three off-tenter events
   - `nearest_historical_runs(feature_set=[TenterExitTemp, TenterChainSpeed, RecipeId], target_values=[192, 92, "R102C"], k=5)` → top-2 matches both off-tenter
   - `detect_drift(tag="TenterExitTemp", scope="recipe", scope_key="R102C", window_days=90)` → DRIFT_DETECTED, change_point 2026-04-15
5. LLM iteration 1: emits structured 3-hypothesis output, no further tool calls, stops.
6. `_step1_hypotheses` returns `(hypotheses, tool_calls, cache_status="miss")`. Total tool calls so far: 3. Budget remaining: 12.

### 2b. RCA Step 2 — Adjudicate

`rca._adjudicate(hypotheses, full_context, remaining_budget=12)`:

1. Build messages: `[system: system_prompt_v2 + rca_step2_v1, user: hypotheses+evidence, user: query]`
2. `_run_tool_loop(messages, tools=openai_tool_specs(allowlist=None), max_iters=2, max_total_calls=12)`
3. LLM iteration 0: two tool calls
   - `defect_events_in_window(start=2026-04-19, end=2026-04-21T22:24Z, line="coater1", style="S-4471", fm_code="off_tenter")` → 2 prior matching events
   - `percentile_of(tag="TenterChainSpeed", value=92, scope="recipe", scope_key="R102C")` → 14th percentile (low)
4. LLM iteration 1: emits ranked adjudication + narrative response with citations, no further tools, stops.

Total tool calls across both steps: 5. Budget remaining: 10.

### 2c. Response shape

The LLM returns a `ChatResponse`-shaped JSON:

```json
{
  "body": "**Likely contributor: tenter chain speed mismatch following the WO-88245 chain rebalance.**\n\n[1] WO-88245 closed 2026-04-19 — chain rebalance pending. [2] TenterExitTemp is +3.4σ above the matched-history baseline (current 192°C vs mean 178, std 4.1) [T1, T3]. [3] TenterChainSpeed at 92 is at the 14th percentile for recipe R102C [T5] — slow chain extends dwell, drives exit temp up...",
  "confidence_label": "likely_contributor",
  "failure_mode_code": "off_tenter",
  "citations": [/* T1..T5 distributional + DOC-1..DOC-3 + WO-88245 + 2 prior events */],
  "rca_summary": { /* full step1+step2 trace */ }
}
```

## 11.5 Phase 3 — Persist + Audit

A new DB session is opened for persistence.

1. `messages` row inserted with full `context_snapshot`,
   `rca_summary`, `audit_hash`.
2. `audit_log` row chained from previous (SHA-256 of prior
   `audit_hash` || current payload).
3. `chunk_quality_signals` not yet updated (waits for explicit
   feedback).
4. The structured response shape is returned to the gateway.

Total wall-clock: ~3.4 s (Phase 1: 0.31s; Phase 2 step1: 1.42s; Phase 2 step2: 0.98s; Phase 3: 0.18s).

## 11.6 Operator Sees

In Perspective, Dana sees the answer text with citation chips.
Tapping `[2]` expands the change-ledger panel; tapping `[T1]` shows
the percentile detail with the text-rendered box plot. The
**LIKELY CONTRIBUTOR** label is amber, visually distinct from
**CONFIRMED FACT** green.

She taps `[👍 Helpful]` and selects `actionable`. The feedback intake
runs in <50 ms — a `message_feedback` row inserted, the affected
chunks' `chunk_quality_signals.helpful_count` incremented.

## 11.7 24 Hours Later

The nightly outcome closure job runs at 04:00 UTC. It finds Dana's
`message_id` in the past-24h `confirmed|likely_contributor` set.
It joins to `defect_events` and `quality_results` in the following
24 h scoped to `S-4471` and finds a closed-out maintenance entry
attributing root cause to `tenter_chain rebalance overdue` (linked
to WO-88245). The scheduler files an `outcome_linkages` row with
`outcome_type='confirmed'`. The materialized view
`v_rca_precision_daily` refreshes; today's confirmed_count + 1.

The system has just learned, in a bounded and auditable way, that
its `LIKELY CONTRIBUTOR` label was right on this case. Cumulative
precision ticks up by some small amount; the trust dashboard reflects.

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Walkthrough</p>
<p><span class="label">Stayed:</span> The single-turn shape: parse →
build context → LLM → persist. The user-facing experience.</p>
<p><span class="label">Changed:</span> Three-phase DB-session
discipline (own session in Phase 1, none in Phase 2, new session in
Phase 3). Two-step RCA chain replaces single-shot LLM call. Tool
calls execute inline with budget tracking. Change ledger and
distributional tools surface as cited evidence. Outcome closure runs
asynchronously the next day and updates the precision dashboard.</p>
<p><span class="label">Considering:</span> A "trace view" UI mode that
lets engineers step through Phase 1/2/3 of a past chat turn cell by
cell — the audit data already supports it; just needs a Perspective
view. Streaming response (LLM tokens stream to the client as they
arrive). Per-turn cost surfacing in the UI for Azure/OpenAI users
who care about budgeting.</p>
</div>
