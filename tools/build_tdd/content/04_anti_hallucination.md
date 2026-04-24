# 4. Anti-Hallucination Mechanisms

The grounding-first doctrine is enforced by a pipeline of nine mechanisms
working together. Each has a narrow job and none is sufficient on its own.
Taken together, they define a response protocol in which the LLM can
produce a useful engagement with the evidence, or flag specific claims as
hypothesis, or refuse on genuine scope grounds — but cannot produce a
confidently wrong answer without the system catching it.

v2.0 enumerated seven mechanisms (anchor resolution, clarification-first
default, anchor-conditional buckets, tag-class deviation, structured
prompt, citation enforcement, refusal). v3.0 adds two: the **deterministic
tool layer** (§4.10) and **distributional grounding** (§4.11). Both
strengthen the substrate the LLM reasons over. Both are bounded: the LLM
cannot invent a tool, and tool results carry their own citations.

## 4.1 Query Anchor Resolution

Before any evidence is assembled, the incoming query is parsed into a
structured anchor object that determines what evidence is relevant. This
step precedes retrieval, precedes context assembly, and is itself audited.
Getting the anchor right is the single most important input to a grounded
response — because most queries are implicitly about a specific moment in
time, and assembling evidence for the wrong moment produces confidently
wrong analysis no matter how good the downstream pipeline is.

### Three query classes

Every query resolves into one of three classes. The class determines which
evidence buckets are relevant, and crucially, which are actively excluded.

- **Past-event queries** — the question references a specific past time,
  run, event, or failure. Examples: "the off-quality from 3/13," "yesterday's
  delam fail," "R-20260421-03," "QR-00417." Anchor time = the event time.
  Current live tag state is explicitly excluded from context; it is noise
  for a retrospective analysis.

- **Current-state queries** — the question uses present-tense markers
  referencing right now. Examples: "why are my tenter amps so high rn,"
  "what's going on with zone 3 right now," "is puddle height drifting."
  Anchor time = query time. Live tag state is the primary evidence; stale
  event data is de-prioritized.

- **Pattern queries** — the question asks about relationships, correlations,
  or recurring behavior without a single time anchor. Examples: "do we
  always see zone 3 overshoot after element replacement," "does humidity
  correlate with delam on S-4471." Anchor time = none; historical aggregates
  and matched-pattern retrieval are primary.

### Parsing signals (as built)

The parser uses rule-based extraction; no ML classifier is required for
the MVP. The actual regex set lives in `services/anchor.py`:

| Signal | Regex / token set | Maps to |
|---|---|---|
| Run-number pattern | `\bR(?:UN)?-\d{6,8}-\d{1,3}\b` | `past_event`, sets `anchor_run_id` |
| Sample / quality result | `\bQR-\d{4,6}\b` | `past_event`, sets `anchor_event_id` |
| Numeric date (3/13, 2026-03-13, 03-13) | `_DATE_NUMERIC_RE` | `past_event`, sets `anchor_time` to UTC midnight |
| Relative date (yesterday, last shift, last week, this morning) | `_RELATIVE_DATE_RE` | `past_event`, computed offset |
| Present-tense markers (rn, right now, currently, "is the X", "are the Y") | `_PRESENT_TENSE_RE` | `current_state` |
| Pattern markers (always, usually, do we ever, correlate with, trend, typically) | `_PATTERN_RE` | `pattern` |
| Style code | `\bS-\d{3,5}\b` | sets `style_scope` |
| Failure-mode keyword (closed dictionary in `FAILURE_MODE_KEYWORDS`) | substring match (longest-first) | sets `failure_mode_scope` |
| Equipment keyword (closed dictionary in `EQUIPMENT_KEYWORDS`) | substring match | appends to `equipment_scope` |
| Control-action verb (set, increase, lower, change, adjust, bypass, ack, silence, shutdown, halt, …) | `CONTROL_VERBS_RE` | triggers refusal short-circuit before anchor is even returned |
| None of the above | — | `pattern` (default) |

The anchor object that flows downstream is the Pydantic
`QueryAnchor` model (`models/schemas.py`). All fields are JSON-serializable
and persisted in `messages.context_snapshot.parsed_anchor` so any past
response can be re-opened with the exact anchor that drove it.

## 4.2 Clarification-First Default

If the parser cannot unambiguously resolve the anchor from the question
alone, the system does not infer it. It asks. This is the hardest-enforced
principle in the anti-hallucination layer: explicit reference or explicit
clarification, never implicit inference.

A human reading the conversation might reasonably conclude that "that
scrap event" refers to the one mentioned three messages ago, or that
"why did we scrap that roll?" means the most recent scrap. The system
does not make that leap. Conversation history is a clarification aid — it
pre-fills the clarification prompt with a proposed anchor — but does not
skip the confirmation step. When the system produces an analysis, the
operator must be able to trust that it was anchored on the event they
meant.

### Three clarification patterns (encoded as `AnchorStatus`)

- `needs_clarification_enumerated` — small bounded candidate set; UI
  renders tappable buttons for each option in
  `QueryAnchor.clarification_options`.
- `needs_clarification_open` — unbounded ambiguity; UI renders a free-text
  prompt asking the operator to specify.
- `needs_clarification_scoped` — no immediate match but adjacent candidates
  exist; UI surfaces what was found and asks for confirmation.

In all three cases, `rag.handle_chat` short-circuits Phase 1 with
`confidence="insufficient_evidence"` and the prompt text becomes the
assistant message. No LLM call. The operator's selected option becomes
the resolved anchor for the actual analysis query.

### Scope clarification for current-state queries

Current-state queries have an unambiguous temporal anchor (now), but
scope can still be ambiguous. "Why is the line so slow?" is anchored on
now, but the relevant scope might be "line speed right now," "throughput
this shift," or "throughput this week." Same rule: ask, don't infer.

The economics favor asking. A clarification prompt costs a few seconds
of operator time. A wrong-anchor analysis costs the operator's time to
read plus the trust damage of catching the system confidently anchored on
the wrong event. Always-ask is the correct default even when asking
feels over-cautious.

## 4.3 Anchor-Conditional Evidence Buckets

Once the anchor is resolved, the context builder assembles evidence. The
buckets assembled are conditional on the anchor type. Buckets that would
supply misleading evidence for the query type are excluded, **not merely
de-prioritized** — they are not in the prompt at all, and the prompt
explicitly renders `[NOT APPLICABLE — past-event query]` so the LLM
cannot silently blend excluded evidence back in.

### The five evidence buckets

| Bucket                       | What it contains                                                         | Past-event | Current-state | Pattern |
|------------------------------|--------------------------------------------------------------------------|:----------:|:-------------:|:-------:|
| Pre-anchor 60 min            | Tag behavior, alarms, deviations in the 60 min before anchor             |     ✓      |       ✓       |    —    |
| Pre-anchor 24 h              | Tag behavior, events, shift transitions in the 24 h before anchor         |     ✓      |       ✓       |    —    |
| 14-day-prior normal baseline | A reference 24 h window ~14 days before, same style running              |     ✓      |       ✓       |    —    |
| Last 4 runs prior            | Aggregate tag behavior for the four runs preceding the anchor run         |     ✓      |       ✓       |    —    |
| Failure-mode-matched history | Every prior run matching (style, failure mode), unbounded in time         |     ✓ (primary) |       —       | ✓ (primary) |
| Current tag values           | Live tag reads at query time                                              | ✗ excluded |   ✓ (primary) |    —    |
| Live alarms                  | Currently active alarms                                                   | ✗ excluded |       ✓       |    —    |

The **failure-mode-matched history bucket** is the most important addition
over a naive RAG design, and it is the dominant grounding signal for most
failure-analysis queries. When an operator asks about a specific delam
event on S-4471, the system doesn't just retrieve the current event — it
pulls every prior S-4471 delam event from `defect_events` joined to
`production_runs` (matched on `product_style` and `failure_mode`),
assembles their pre-event tag behavior, and places them alongside the
current event for side-by-side comparison. "Three of the four prior S-4471
off-tenter events showed identical zone 3 overshoot in the hour before
failure" is a qualitatively different answer than "zone 3 looks elevated."

### The exclusion record

Every excluded bucket is recorded in the
`AssembledPrompt.excluded_buckets: list[BucketExclusion]` field with a
`reason` string. The list is persisted into `messages.context_snapshot.excluded_buckets`
so an audit can confirm not just what evidence was used, but what evidence
was deliberately withheld and why.

## 4.4 Tag Discovery and Classification

The system does not maintain a hand-curated list of every tag on Coater 1.
At least, that was the v2.0 design. In the as-built MVP, tag discovery is
**partially deferred**: the schema (`tag_registry` table) is fully
provisioned, but the gateway-side enumeration runs against a hardcoded
`KEY_TAGS` list in `ignition/scripts/config.py` (~50 tags hand-tagged with
category and keywords). The pre-screen `services/tag_selector.py` walks
that catalog and returns a query-conditional subset.

This is documented in detail in §15 (*Tag Selection &amp; Gateway
Integration*), including the path forward to wire `ItemInstance` SQL
discovery into `tag_registry`.

The v2.0 four-class taxonomy
(setpoint-tracking, oscillating-controlled, process-following,
discrete-state) is implemented in `services/deviation.py` and applied per
tag: setpoint-tracking tags get an absolute-deviation test; oscillating-
controlled tags get a detrended-mean test plus amplitude-change check;
process-following tags get a z-score against the conditional baseline;
discrete-state tags get an unexpected-state-change test. Tested in
`test_deviation.py`.

## 4.5 The Curated Context Package

With the anchor resolved, the evidence buckets selected, and the tag
catalog filtered, the context builder assembles the Curated Context
Package. This is the structured Pydantic payload delivered to the prompt
template. The LLM never sees raw historian data, raw tag scans, or
unfiltered document content. It sees pre-digested evidence with clear
delimiters between evidence types, scoped to the anchor.

Two-tier tag selection is implemented in `services/tag_selector.py`:
tier-1 (always-include) tags are flagged `core=True` in the catalog;
tier-2 (query-routed) tags are pulled in when their category synonyms or
their explicit keyword list matches the query text. A `_ZONE_RX` regex
also recognizes "zone 3" / "zone3" / "z3" patterns and pulls only that
zone's tags.

### Evidence section structure

For each evidence section included in the package, the renderer
(`services/context_assembler.py`) produces text the LLM can reason over.
For tag evidence specifically, every tag comes with distribution context,
not just a scalar value:

```
ZoneTemp3  current=435°F  target=420°F  class=setpoint_tracking
  pre-anchor 60-min: mean 434, min 431, max 438, std 1.4
  pre-anchor 24-h:   mean 429, min 421, max 438, std 3.1
  baseline (14d prior, S-4471 running, 24h):
                     mean 420, min 417, max 423, std 1.1
  last 4 runs (S-4471, front_step=2):
                     means [416, 418, 421, 419]   current X = 435
                     box: [|----[416----421]----|]   X far right
  failure-mode-matched (S-4471 off_tenter, 3 prior events):
                     means [431, 438, 429]   current X = 435
                     box: [|--[429----438]--|]   X inside box
```

The two small text-rendered box plots give the LLM (and the operator
reading the cited source panel) immediate visual sense of whether the
current value is a rare excursion or a match to a known failure pattern.
Phase 3 will add actual rendered SVG box plots in the expandable source
panel; the text form is shipped today and is sufficient for LLM
reasoning.

### The complete package structure

The sections present in the rendered prompt (always in this order when
present):

- A. Parsed anchor (the resolved anchor object)
- B. Tier-1 tags (always-include with full evidence rendering)
- C. Tier-2 routed tags (query-relevant tags with full evidence rendering)
- D. Deviations flagged (tags whose class-appropriate deviation test fired)
- E. Active alarms (only for current-state queries)
- F. Recent events (scoped to the anchor window for past-event queries)
- G. Failure-mode-matched history (when a failure mode is named or implied)
- H. Retrieved text evidence (ranked document chunks, RRF-fused, MMR-diversified, weighted by `document_role`)
- I. Matched business rules (or "no rules matched")
- J. Approved line memory (filtered to status in {approved, reviewed})
- K. Attached camera clips (Symphony handles for events in scope)
- L. Change ledger (NEW vs v2.0 — what changed since baseline)
- M. Multivariate anomaly (NEW vs v2.0 — Mahalanobis flag for current-state queries)
- N. ML predictions (placeholder in MVP; populated Phase 4)

## 4.6 Source Citations

Every factual claim in a response carries a numbered citation typed by
provenance. Provenance types in v3.0 (`models/schemas.py::SourceCitation.type`):

- **`LIVE_TAG`** — a current tag value read at query time (current-state queries only)
- **`HISTORIAN_STAT`** — an aggregate computed from historian data over a named window
- **`DEVIATION`** — a flagged deviation produced by the class-appropriate deviation test
- **`BASELINE_COMPARE`** — a comparison of current behavior against a named baseline bucket
- **`MATCHED_HISTORY`** — a prior run matched on style and failure mode
- **`ALARM`** — an active or recent alarm record
- **`EVENT`** — a structured event record (downtime, quality, defect)
- **`WORK_ORDER`** — a work order record from the Ignition WO database
- **`DOCUMENT`** — a retrieved document chunk
- **`CAMERA_CLIP`** — a Symphony clip attached to an event in scope
- **`RULE`** — a deterministic rule that matched current conditions
- **`MEMORY`** — an approved line memory entry
- **`ML_PREDICTION`** — a model output (Phase 4+)
- **`DISTRIBUTION`** *(NEW)* — a percentile result from `services/percentiles.py`
- **`NEAREST_RUNS`** *(NEW)* — top-K historical runs near a given value
- **`DRIFT`** *(NEW)* — Page-Hinkley CUSUM drift detection result
- **`TOOL_RESULT`** *(NEW)* — a generic tool-call return value with auto-generated ID

(v1 lowercase aliases — `live_tag`, `tag_summary`, `tag_deviation`,
`active_alarm`, `document_chunk`, `downtime_event`, `quality_result`,
`defect_event`, `business_rule`, `line_memory`, `ml_prediction` — are
also accepted by the Pydantic `Literal` for backward compatibility with
older audit records.)

A response-parsing step validates that every factual claim has a
citation. Uncited claims are appended with a `[NOTE: The assistant did
not include source citations]` warning by `services/rag.py` and the
overall confidence label is downgraded from `confirmed` to `hypothesis`.

## 4.7 Confidence Labeling and Engagement Posture

Every conclusion in a response carries a confidence label. Labels describe
certainty about specific claims; they do not gate whether the response
happens at all. The advisor's default posture is full engagement with the
evidence it can see, honestly labeled — not retreat into refusal.

### The four confidence labels

- **CONFIRMED FACT** — Directly stated in a source document or present in
  a tag reading in scope. Example: "ZoneTemp3 averaged 435°F during
  R-20260421-03 [2]."
- **LIKELY CONTRIBUTOR** — Multiple pieces of evidence converge on a
  conclusion. Example: "Three of the four prior S-4471 off-tenter events
  showed identical zone 3 overshoot [5][6][7]; the current event shares
  this signature."
- **HYPOTHESIS** — Plausible but not well-supported by retrieved evidence.
  Example: "Tillitson metering roller speed may also have contributed;
  direct evidence was not retrieved for this run."
- **INSUFFICIENT EVIDENCE** — Used narrowly for genuinely out-of-scope
  queries; see §4.8.

### Engagement posture

The advisor reports the math, the abnormalities, the discrepancies, the
matches and mismatches against baselines, the prior cases that look
similar. It does not refuse to reason just because some channels are
missing. When a meaningful evidence channel is unavailable — camera clips
not yet attached, lab results not yet posted, upstream process data
outside scope — the response flags it as context, not as grounds for
refusal. That is useful and honest. It is the opposite of refusing to
engage. The system prompt
(`config/prompts/system_prompt_v2.txt` §5) enforces this posture
explicitly.

### Visual distinction requirement

Because the default posture engages with mixed-confidence evidence, the
confidence labels must be visually unmissable in the UI. The Perspective
implementation uses all caps plus color coding: CONFIRMED FACT green,
LIKELY CONTRIBUTOR amber, HYPOTHESIS grey, INSUFFICIENT EVIDENCE red. If
the chat view rendered HYPOTHESIS text indistinguishable from CONFIRMED
FACT text, the operator would lose the ability to calibrate trust and
the whole labeling apparatus would become decorative. The visual
distinction is non-negotiable.

## 4.8 Refusal: Narrowed to Scope

Refusal is for scope mismatch, not for uncertainty. The system refuses
in exactly three cases:

- **Genuinely out of corpus.** The query is about equipment or processes
  the system has no evidence for (e.g., a question about Coater 7 when
  only Coater 1 is ingested). Response: "I don't have evidence for that
  system in the current corpus."

- **Control command.** The query asks the advisor to issue a control
  action ("increase zone 3 to 430"). The advisor is read-only. It can
  explain trade-offs of a hypothetical change but will never frame a
  recommendation as an instruction to execute. Implementation:
  `services/anchor.py::is_control_command` returns True; `rag.py`
  short-circuits in Phase 1.

- **No retrieval match at all.** Zero relevant document chunks across
  retrieval, no matching rules, no relevant memory, no events in scope.
  The response is an explicit INSUFFICIENT EVIDENCE summary listing
  what was searched. Implementation:
  `services/context_assembler.py::is_evidence_insufficient` returns True;
  `rag.py` short-circuits **without calling the LLM**.

Uncertainty, partial information, and missing evidence channels do not
trigger refusal. They trigger labeled engagement.

## 4.9 Structured Prompt Architecture

The prompt delivered to the LLM has explicit section delimiters so the
model can see what evidence is available and cite it by section and
index. The structure is anchor-aware: sections that don't apply to the
current query are omitted or explicitly marked as not-applicable so the
LLM cannot silently blend excluded evidence back in.

The system prompt is loaded from `service/config/prompts/system_prompt_v2.txt`
(143 lines, plain text). It is registered into `prompt_versions` with
`is_active=true`, and the version string is recorded in every
`messages.prompt_version` row. Iterating the prompt is a v2 → v3 → v4
migration in the same table; correlating feedback against prompt versions
is a one-line SQL JOIN.

Excerpt of the assembled user block (annotated):

```
=== SYSTEM INSTRUCTIONS ===
[role, safety constraints, response format, citation rules,
 engagement posture, refusal triggers, label color scheme]

=== PARSED ANCHOR ===
anchor_type: past_event
anchor_time: 2026-04-21T18:24:00-04:00
anchor_event_id: QR-00417
style_scope: S-4471
failure_mode_scope: delam_hotpull

=== TIER-1 TAGS (at anchor time) ===
[IsRunning, LineSpeed, StyleID, FrontStep, …]

=== TIER-2 ROUTED TAGS (with full evidence rendering) ===
[ZoneTemp3 (setpoint_tracking), TillitsonMeterRPM (process_following), …]

=== DEVIATIONS FLAGGED ===
[class-appropriate deviation test results]

=== LIVE ALARMS ===
[NOT APPLICABLE — past-event query]

=== RECENT EVENTS (scoped to anchor ± 72h) ===
[structured summaries]

=== FAILURE-MODE-MATCHED HISTORY ===
[prior S-4471 delam_hotpull events with pre-event tag behavior]

=== RETRIEVED DOCUMENTS (RRF-fused, MMR-diversified, weighted by document_role) ===
[DOC-1] (rrf=0.087, weight=1.2) DELAM_0047: ...
[DOC-2] (rrf=0.082, weight=1.0) WO-88214 narrative: ...
[DOC-3] (rrf=0.079, weight=0.6) Roisum web-handling ref: ...

=== CHANGE LEDGER ===
TAG DELTAS vs. matched-history baseline (sigma-ranked):
  ZoneTemp3 +3.4σ above baseline (current 435 vs mean 421, std 4.1)
  TillitsonMeterRPM +1.8σ above baseline (current 33 vs mean 28, std 2.7)
RECIPE DELTAS:
  recipe_id differs from dominant matched-history (R102B, 6/8 prior runs)
EQUIPMENT CHANGEOVERS in matched-history window:
  zone3_heater (WO-88214 closed 2026-04-19): element replaced, calibration drift noted

=== CAMERA CLIPS (attached to events in scope) ===
[Symphony handles + timecodes, or "no clips available"]

=== DETERMINISTIC RULES ===
[matched rules, or "no rules matched"]

=== APPROVED LINE MEMORY ===
[approved memory entries matching query]

=== MULTIVARIATE ANOMALY ===
[NOT APPLICABLE — past-event query, no live snapshot]

=== ML PREDICTIONS ===
[placeholder in MVP]

=== CONVERSATION HISTORY ===
[recent turns in this session]

=== USER QUESTION ===
[the operator's actual question]
```

## 4.10 Deterministic Tool Layer (NEW vs v2.0)

The tool layer adds a second grounding mechanism: deterministic functions
the LLM can call mid-completion to ground specific numerical hypotheses.
Five tools are registered in `services/tools.py`:

| Tool | What it does | Citation type |
|---|---|---|
| `percentile_of` | Empirical CDF of `(tag, value)` within a configurable scope | `DISTRIBUTION` |
| `compare_to_distribution` | Percentile + the K nearest historical runs labeled by outcome | `DISTRIBUTION` |
| `nearest_historical_runs` | Top-K runs whose feature value is closest to a given value | `NEAREST_RUNS` |
| `detect_drift` | Page-Hinkley CUSUM on a 90-day rolling daily mean | `DRIFT` |
| `defect_events_in_window` | All defect events for a (line, time-window, optional style/mode) | `EVENT` |

Each tool:

- Returns a `ToolResult(ok, data, citation, error)` — the citation is
  auto-generated with a unique `id` so the LLM can cite it inline.
- Has a hard SQL timeout (5 s default) to bound latency.
- Is purely read-only — no tool ever writes to the DB.
- Is exposed via OpenAI's `tool_calls` mechanism through a shared
  `_run_tool_loop` helper in `services/llm.py` that handles all three
  provider implementations identically.

Bounded budget: `rca_max_total_tool_calls=15`,
`rca_max_evidence_per_hypothesis=5`, `rca_step1_max_iters=2`,
`rca_step2_max_iters=2` (all in `settings.py`). The LLM cannot exhaust
the API budget by tool-spamming.

The OpenAI tool-spec is generated **from** the registry, not written by
hand, so the LLM can never call a tool that doesn't exist. Allowlist
filtering (`openai_tool_specs(allowlist=...)`) lets the RCA chain expose
only the four distributional tools to step 1, hiding the (more expensive)
`chunk_search` from cost-budgeted hypothesis generation.

## 4.11 Distributional Grounding (NEW vs v2.0)

The percentile service (`services/percentiles.py`) is the substrate
behind the distributional tools. It computes empirical CDFs over
`feature_snapshots` joined to `production_runs`, scoped by:

- `global` — every snapshot for the line
- `style` — same product style only
- `style_step` — same style + same `front_step`
- `equipment` — same equipment id
- `recipe` — same recipe id
- `global_ytd` — same line, current year only

Why scope matters: a `Front2_Temp` of 198 °C is "high" globally but
"normal" for style S-1234 at front_step=2 in summer. Tying the percentile
to context is the whole point.

CDFs are cached in-process keyed on `(tag, scope_key)` with a TTL so the
same query inside a single chat turn doesn't re-issue SQL. Drift
detection uses the Page-Hinkley CUSUM test on a 90-day rolling daily mean,
returning `DRIFT_DETECTED | NO_DRIFT | INSUFFICIENT_DATA` with the
change-point timestamp when applicable.

This layer is queried both directly (when a tool call is made) and
indirectly (the change ledger and multivariate anomaly both consume it
for their own grounding). All three sources surface the same numerical
substrate to the LLM through different framings.

## 4.12 Complete Audit Record

Every response writes a full audit record to the `messages` table. The
`context_snapshot` JSONB column is the durable, reconstructible record of
exactly what the LLM saw. The snapshot includes:

- The parsed anchor object in full
- Any clarification prompt shown and the user's selection (when applicable)
- The exact tag values used (with read timestamps and tag class)
- The exact historian aggregates computed for each bucket, with window bounds and aggregation mode
- The failure-mode-matched runs included, with their run IDs
- Document chunk IDs retrieved with similarity scores, RRF scores, weights, and rank positions
- Memory entry IDs with their status and version at time of use
- Rules evaluated and which matched, with rule version
- Camera clip handles included
- Prompt version ID
- LLM model name and parameters
- Token counts and per-stage latency breakdown
- The full RCA trace (step 1 hypotheses, evidence gathered, step 2
  adjudication, cache hit, total tool calls) when the chain ran

Any response can be fully reconstructed and audited. If a user challenges
"why did the system say X on 2026-04-15 at 14:35?" the audit record is
the answer: the evidence the LLM had access to is the evidence visible
in the snapshot, the anchor is explicit, and the RCA trace (when present)
shows every hypothesis considered with its supporting evidence. There is
no implicit state, no hidden prior, and no unrecorded reasoning step.

The `audit_log` table mirrors a one-row summary and is append-only at
the **database** layer (trigger `audit_log_immutable` raises on any
`UPDATE` or `DELETE`). Tampering at the application layer is also
impossible: `services/audit.py` only exposes a `write_audit` helper, no
mutation API. Both `messages` and `audit_log` are monthly partitioned
via `pg_partman` (migration 001) so old months can be detached for cold
storage without VACUUM FULL on the parent.

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Anti-hallucination mechanisms</p>
<p><span class="label">Stayed:</span> Seven mechanisms from v2.0 — anchor
resolution, clarification-first, anchor-conditional buckets, tag-class
deviation, structured prompt, citation enforcement, refusal — survive
unchanged.</p>
<p><span class="label">Changed:</span> Two new mechanisms shipped: the
deterministic tool layer (§4.10) and distributional grounding (§4.11).
Both add deterministic, bounded grounding to what was a
retrieval-and-rules-only substrate. The change ledger and multivariate
anomaly are added to the prompt structure (sections L and M).</p>
<p><span class="label">Considering:</span> Per-claim citation
enforcement (parser today validates per-response). Step-back query
abstraction (B4). HyDE-style query expansion for cold-start retrieval (B5).
Self-consistency / k-sample voting on high-stakes RCA conclusions (B6).</p>
</div>
