# Appendix A — Glossary

Plain-English meanings for every recurring term. Where a term is a
real file/setting/function name in the code, it appears in `code font`.

## Core concepts

- **LLM** — Large Language Model. The AI brain (e.g. GPT-4o-mini, Llama 3.1).
- **RAG** — Retrieval-Augmented Generation. The pattern this whole
  system uses: search relevant evidence, then ask the LLM to write an
  answer using only that evidence.
- **Embedding** — A list of ~1500 numbers that represents the meaning
  of a piece of text. Two pieces of text with similar meaning have
  similar number lists. Computed by a separate AI model
  (`text-embedding-3-small`).
- **Vector search** — Searching by embedding similarity rather than by
  exact words.
- **BM25** — A classic keyword-ranking algorithm; "Ctrl-F with relevance."
- **Hybrid retrieval** — Running both vector and BM25, fusing the
  rankings (RRF), then de-duplicating (MMR).
- **RRF** — Reciprocal Rank Fusion. The math formula
  `1/(k + rank)` summed across rankings. `k=60` constant.
- **MMR** — Maximal Marginal Relevance. Picks the top-K results trading
  off relevance vs diversity (`λ=0.7`).
- **Chunk** — A bite-sized piece of a longer document (paragraph,
  table row, list item) stored in `document_chunks` so it can be
  retrieved on its own.
- **Citation** — A pointer back to the chunk or tool result an answer
  came from, so a human can verify it.
- **Prompt** — The written instructions given to the LLM. The active
  one is `system_prompt_v2.txt`.
- **Tool / tool call** — A specific function the LLM is allowed to
  invoke mid-response to get a real number or do a real lookup.
- **Token** — The unit OpenAI charges by. Roughly ¾ of a word.
  1000 tokens ≈ 750 English words.

## Anchor concepts (chapter 4)

- **Anchor** — The `(time, run, event, scope)` tuple a query is
  about. Resolved by `services/anchor.py` before any retrieval.
- **Past-event anchor** — Query references a specific past time, run,
  event, or failure (e.g. "QR-00417").
- **Current-state anchor** — Query uses present-tense markers ("rn",
  "right now", "currently").
- **Pattern anchor** — Query asks about relationships, correlations,
  or recurring behavior; no single time anchor.
- **Clarification-first** — When the parser cannot uniquely resolve
  the anchor, the system asks rather than infers.
- **Failure-mode-matched history** — A retrieval bucket that pulls
  every prior run matching `(style, failure_mode)` regardless of recency.
- **Confidence label** — `CONFIRMED FACT | LIKELY CONTRIBUTOR |
  HYPOTHESIS | INSUFFICIENT EVIDENCE` — required on every response.

## RCA concepts (chapter 7)

- **RCA** — Root Cause Analysis. Figuring out *why* something happened.
- **Two-step RCA chain** — Hypothesise (step 1) → tools execute →
  Adjudicate (step 2). Replaces single-shot LLM guessing.
- **Hypothesise step** — LLM proposes up to 3 candidate causes from
  the curated context, with evidence references and confidence.
- **Adjudicate step** — LLM weighs each hypothesis against the evidence
  the tools returned, ranks them, assigns final confidence label.
- **Tool budget** — Bounded total tool-call cap (15) shared across
  both RCA steps.
- **`_STEP1_CACHE`** — In-process TTL cache (5 min default) keyed on
  `(anchor_event_id, anchor_run_id, anchor_time, failure_mode, prompt_version)`.

## Distributional concepts (chapter 8)

- **Percentile** — Where a value ranks in the historical distribution.
- **Scope** — The slice of history a percentile is computed over:
  `global | style | style_step | equipment | recipe | global_ytd`.
- **Drift** — Sustained directional change in a tag's mean over time.
  Detected via Page-Hinkley CUSUM.
- **Page-Hinkley CUSUM** — A statistical test for change-point
  detection on streaming data.
- **Mahalanobis distance** — A multivariate distance measure that
  accounts for feature correlations. Used by the anomaly detector.
- **Ridge stabilization** — Adding `λ·I` to the covariance matrix
  before inversion to handle highly-correlated features.
- **Change ledger** — Structural diff between current run and the
  matched-history baseline: `TagDelta`, `RecipeDelta`, `CrewDelta`,
  `EquipmentChangeover`.

## Storage concepts (chapter 5)

- **Schema** — The database blueprint: which tables exist and what
  columns they have.
- **pgvector** — Postgres extension for storing and searching
  embeddings.
- **ivfflat** — Approximate-nearest-neighbor index from pgvector;
  fast for ≤250K rows.
- **hnsw** — A different ANN index type, better for >250K rows;
  ivfflat → hnsw cutover documented in migration 003.
- **GIN** — Generalized Inverted Index in Postgres; the index type
  that makes BM25 queries fast.
- **TSVECTOR** — Postgres's generated text-search representation.
- **JSONB** — Postgres's binary JSON column type. Used heavily for
  flexible metadata.
- **pg_partman** — Postgres extension that manages monthly partition
  creation/retention. `messages` and `audit_log` use it.
- **Materialized view** — A pre-computed summary table refreshed on
  a schedule. `v_rca_precision_daily` is the trust-dashboard one.
- **Migration** — A careful script that upgrades an existing database
  to a new schema without losing data. Lives in `scripts/migrations/`.

## Operations concepts (chapter 13)

- **Container / Docker** — A standardized box that holds a program
  plus everything it needs to run.
- **Docker Compose** — The tool that orchestrates multiple containers
  as one stack.
- **APScheduler** — The Python library scheduling the nightly outcome
  closure and the 4-hourly anomaly re-fit.
- **slowapi** — Rate-limiting library; keys on resolved `user_id`.
- **structlog** — Structured-logging library; produces JSON log lines.
- **Prometheus** — Metrics-collection format and server.
- **Grafana** — Dashboarding tool consuming Prometheus.
- **JWT** — JSON Web Token. The signed token the Ignition gateway
  issues to authenticate operators. Verified HS256.

## Ignition / plant concepts

- **PLC** — Programmable Logic Controller. The industrial computer
  that controls the line.
- **HMI** — Human-Machine Interface. The operator's screen.
- **Tag** — A single named value in Ignition (e.g. "ZoneTemp3").
- **UDT** — User-Defined Type. A reusable folder structure of tags.
- **Ignition Gateway** — The server software (Inductive Automation)
  running Perspective screens and talking to PLCs.
- **Perspective** — Ignition's web-based HMI module.
- **Designer** — Ignition's desktop app for *building* projects.
- **Tag provider** — The source of tags (PLC connection, MQTT, etc.).
- **Tag path** — The slash-separated address of a tag (e.g.
  `[default]Shaw/F0004/Coating/Coater1/Zones/Zone3/Temp`).

## Manufacturing concepts

- **Failure mode (FM)** — A standardized name for a type of defect
  (`delam_hotpull`, `sag`, `coating_weight_var`, ...). Closed-enum
  in `failure_modes` reference table.
- **Style** — A product variant (e.g. `S-4471`).
- **Recipe** — The setpoint configuration for running a style.
- **MOC** — Management of Change. A formal review process for
  modifying production parameters.
- **SOP** — Standard Operating Procedure.
- **Work order (WO)** — A maintenance task record.
- **Crew / shift** — A particular team running the line at a particular
  time.
- **Off-tenter** — A specific failure mode where coated web exits the
  tenter frame outside spec.
- **Delamination (delam)** — Coating separating from the substrate.
- **fpm** — Feet per minute. Line speed unit.

## Code-base concepts

- **Stub** — A placeholder function or file that exists but doesn't
  do anything useful yet.
- **Audit hash** — SHA-256 chained across `audit_log` rows for tamper
  detection.
- **Bounded re-rank** — The ±30%-clamped feedback-driven chunk rank
  adjustment.
- **Cold start** — A query against a brand-new corpus before
  feedback signals exist.
- **Provider** — `openai | azure_openai | local`. Picked via
  `LLM_PROVIDER` env.
- **Air-gapped** — A deployment that can't reach external networks;
  requires `local` LLM provider.
