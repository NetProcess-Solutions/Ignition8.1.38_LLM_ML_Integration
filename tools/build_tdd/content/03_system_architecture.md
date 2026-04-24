# 3. System Architecture

## 3.1 Component Overview

The system is composed of five first-class components and four supporting
services. Each component has a narrow, well-defined responsibility, and no
component reaches across the boundaries of another.

### First-class components

- **Ignition Perspective (front-end and context origination).**
  Operator-facing chat view, live tag dashboards, feedback UI, and
  gateway-side Jython 2.7 scripts that read tags, query the historian,
  evaluate alarms, and assemble the curated context package. Runs on the
  existing Plant 4 Ignition gateway (`shawmfg04`).
  Ships as: `ignition/scripts/{client,context,config,discovery,selector}.py`
  plus `ignition/perspective/gateway_wiring.py` (specification +
  copy-paste templates).

- **FastAPI service (orchestration and retrieval).** Receives curated
  context packages from the gateway, performs hybrid retrieval against the
  document corpus, queries structured event tables, evaluates business
  rules, dispatches the RCA chain when triggered, assembles the final
  prompt, calls the LLM (with a tool-calling loop bounded by `max_iters`),
  parses and validates the response, and writes the audit record.
  Stateless apart from a per-process LLM concurrency semaphore, the RCA
  step-1 cache, and the multivariate-anomaly fitted-model cache.
  Horizontally scalable. Ships as: `service/main.py` mounting routers from
  `service/routers/{chat,feedback,corrections,outcomes,health,select_tags}.py`.

- **PostgreSQL 16 + pgvector (unified data store).** Single database for
  documents, chunks, embeddings, events, conversations, feedback, memory,
  user profiles, ML metadata, audit log, and tag registry. Vector search
  via `pgvector` IVF-flat index on 384-dim embeddings; keyword search via
  `pg_trgm` GIN index on chunk text; monthly partitioning of `messages` and
  `audit_log` via `pg_partman`; closed enum of failure modes via FK to
  `failure_modes`. Ships as: `scripts/setup_database.sql` (~700 lines DDL)
  + `scripts/migrations/{001..004}*.sql`.

- **Embedding model (retrieval backbone).** `all-MiniLM-L6-v2`, 384-dim,
  served locally via `sentence-transformers`. Loaded once at FastAPI
  startup (`warmup_embeddings()` in `services/embeddings.py`); subsequent
  embeds are CPU-bound and sub-50 ms per query. Chosen for small footprint,
  acceptable retrieval quality on short technical text, and zero external
  dependency.

- **LLM (reasoning engine).** Pluggable provider (OpenAI, Azure OpenAI, or
  any OpenAI-compatible HTTP server such as vLLM, llama.cpp, or LM Studio).
  Selected by `LLM_PROVIDER` env-var. Model name, temperature, and
  `max_tokens` are recorded on every response in `messages.model_name` and
  `messages.model_params` for reproducibility. Concurrency is bounded
  process-wide by an `asyncio.Semaphore(llm_max_concurrency)`.
  Ships as: `services/llm.py::{OpenAIChatClient, AzureOpenAIChatClient,
  LocalOpenAICompatibleClient}`.

### Supporting services

- **Rule engine.** YAML-defined deterministic rules evaluated against the
  curated context. Rule matches are fed into the prompt as a labeled
  evidence section; they cannot directly cause an LLM response, but the
  LLM must cite them if it invokes their conclusion. Ships as:
  `services/rules.py` and `service/config/rules/coater1_rules.yaml`.

- **Tool layer (NEW vs v2.0).** Five typed read-only tools the LLM can
  invoke during a tool-enabled completion:
  `percentile_of`, `compare_to_distribution`, `nearest_historical_runs`,
  `detect_drift`, `defect_events_in_window`. Each tool is a pure function
  over the existing DB; each return value carries a `SourceCitation` so
  tool-derived facts inherit the same audit trail as retrieved evidence.
  Ships as: `services/tools.py`. The OpenAI tool spec is generated from
  the registry, so the LLM can never call a tool that doesn't exist.

- **Audit log (supporting).** Append-only table receiving event records
  from every meaningful system action: queries, feedback, corrections,
  outcome linkages, memory state changes, model activations, ingestion
  runs, and prompt version changes. Tamper-resistance is enforced by
  database trigger (`audit_log_immutable()`) that raises on any
  `UPDATE` or `DELETE`.

- **Nightly scheduler.** A lightweight in-process loop in `service/main.py`
  that, when enabled, runs work-order sync, Symphony backfill, and outcome
  follow-up sweep + materialized-view refresh once per `nightly_jobs_interval_seconds`
  (default 86400). Production deployments may swap this for APScheduler or
  a dedicated worker; the in-process loop keeps the MVP single-container.

## 3.2 High-Level Data Flow

A single chat query traverses the path below. The orchestrator
(`services/rag.py::handle_chat`) is structured into three phases with
explicit DB-session lifetimes; the LLM call (Phase 2) does **not** hold an
asyncpg pool slot, which matters under load because LLM responses average
5–15 seconds.

1. **Operator submits query.** Text is captured in the Perspective chat
   component along with session metadata (Ignition `userName`, session id,
   line id, signed gateway JWT). Posted to `POST /api/chat`.

2. **Gateway-built curated context arrives in `live_context`.** The
   gateway script reads tier-1 tags always plus the subset selected by
   `services/tag_selector.py` for query-relevant categories, computes
   60-minute historian aggregates per tag, identifies deviations against
   recent baselines, queries active alarms, reads recipe context (style,
   recipe id, front step, crew, shift, target specs), and packages the
   result into a structured JSON payload that conforms to
   `CuratedContextPackage`.

3. **Phase 1 — pre-LLM (own DB session).**
   - `_ensure_user_profile` upserts the user; `_get_or_create_conversation`
     resolves the conversation id; the user message is persisted
     immediately for audit-friendliness.
   - Anchor resolved by `anchor.resolve_anchor` if the gateway didn't
     already supply one (gateway can pre-resolve to avoid redundant work).
   - `is_control_command` short-circuit: refuse and persist.
   - `anchor.anchor_status != "resolved"` short-circuit: ask for clarification.
   - `embed_one(req.query)` produces the 384-dim query vector.
   - **Hybrid retrieval** runs `retrieve_chunks_hybrid` (vector + BM25 RRF
     + boosted + MMR-diversified). Falls back to vector-only if
     `retrieval_mode != "hybrid"`.
   - Anchor-aware event retrieval: `retrieve_events_around_anchor` for
     past-event queries; `retrieve_recent_events` for current-state.
   - Failure-mode-matched history when `style_scope` AND
     `failure_mode_scope` are both present.
   - Work-order lookup scoped by `equipment_scope` and a 30-day window
     before the anchor.
   - Memory retrieval (vector cosine on `line_memory.embedding` filtered
     to status in {approved, reviewed}); `mark_memories_accessed` updates
     the `access_count` and `last_accessed`.
   - Rule evaluation runs `evaluate_rules` against the curated context.
   - Best-effort change ledger is built for past-event anchors
     (`_maybe_build_change_ledger`); best-effort multivariate anomaly is
     scored for current-state anchors (`_maybe_score_anomaly`).
   - `assemble_prompt` produces the structured user block + citation list +
     summary + excluded-bucket list.
   - `is_evidence_insufficient` short-circuit: persist a templated
     refusal **without calling the LLM** and return.
   - Phase 1 commits and closes its DB session.

4. **Phase 2 — LLM call (no DB session held).**
   - If `should_use_rca_chain(query, anchor)` returns True, dispatch
     `services/rca.py::handle_rca` (two-step chain with bounded tool
     budget); otherwise call `llm.complete(sys_prompt, user_block)`
     one-shot.
   - Response validated by `parse_confidence` and `has_any_citations`;
     uncited responses are downgraded.
   - `extract_cited_ids` filters the offered citation list down to the
     subset the LLM actually cited.

5. **Phase 3 — persist (new DB session).**
   - `_insert_message` writes the assistant row with the full
     `context_snapshot` (parsed anchor, every populated bucket, every
     excluded bucket with reason, retrieval scores, work-order ids,
     camera-clip handles, all citations *offered*, RCA trace if any).
   - `write_audit` appends a one-row `audit_log` summary.

6. **Response rendered in Perspective** with numbered source citations,
   color-coded confidence labels, expandable source panel, feedback
   controls, and (for diagnostic responses) a "Root cause confirmed?"
   button that posts `signal_type=root_cause_confirmed` plus an
   `outcome_linkages` row.

## 3.3 Deployment Topology

All components run on-premises within the Plant 4 network. The Ignition
gateway runs on `shawmfg04` as it does today. The FastAPI service and
PostgreSQL instance run as containers via `docker-compose` (see
`docker-compose.yml`). The embedding model runs in-process with the
FastAPI service to eliminate network latency for retrieval. LLM inference
is the only external dependency in the MVP, routed through Shaw's
approved API egress path.

### `docker-compose.yml` services

- `postgres` — `pgvector/pgvector:pg16` image. Healthcheck
  `pg_isready`. Bind-mounts `setup_database.sql` and `seed_reference_data.sql`
  into `/docker-entrypoint-initdb.d/` so first-time start populates the
  schema and reference data automatically. Persistent named volume
  `postgres_data:/var/lib/postgresql/data`.

- `ai-service` — built from `service/Dockerfile`. Depends on `postgres`
  with `condition: service_healthy`. Mounts `service/` for hot reload in
  dev (remove the bind mount in production). Cached HuggingFace model
  weights live in named volume `model_cache:/root/.cache/huggingface`.

- Healthcheck endpoint `GET /api/health` returns `{db, embedding_model,
  llm_provider, version}` — exercised every 15s by docker-compose.

### Network &amp; security boundary

- The advisor is **read-only with respect to Ignition**: reads tags via
  `system.tag.readBlocking()` and queries the historian; never writes any
  tag values or triggers any actions.
- All inter-component calls are internal to the Plant 4 network. The
  FastAPI service is not exposed externally. The only egress is the LLM
  API call.
- User identity is sourced from Ignition's authenticated session
  (`session.props.auth.user.userName`) and signed into a short-lived
  HMAC-SHA256 JWT by the gateway script (`ai.client`) using
  `GATEWAY_HMAC_SECRET`. The service verifies the JWT and treats the
  embedded `user_id` as the authoritative identity. Token TTL is bounded
  to 120s by default (`GATEWAY_TOKEN_TTL_S` on the gateway,
  `gateway_token_max_age_s` on the service).
- The audit log is append-only and tamper-resistant at the database layer
  (trigger `audit_log_immutable`).
- LLM API calls include no PII beyond the user's role and display name.
  Raw query text and evidence are sent because they are operationally
  necessary; they are also logged locally in `messages.context_snapshot`
  for audit.
- Camera clip handles are stored as Symphony URLs or persistent IDs, not
  as embedded video data. Clips render in Perspective by fetching from
  Symphony at view time; they are not copied into PostgreSQL and they are
  not sent to the LLM. The LLM sees the handle as a citation reference only.

## 3.4 LLM Hosting Options

Three viable LLM backends are wired up today in `services/llm.py`:

- **`openai`** (default). `AsyncOpenAI` client; reads `OPENAI_API_KEY`
  and `OPENAI_MODEL`. Best instruction-following and citation discipline
  available off the shelf. Per-query cost; external egress required.
  Recommended for the MVP because the grounding-first doctrine depends
  on tight instruction-following.

- **`azure_openai`**. `AsyncAzureOpenAI` client; reads
  `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`,
  `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION`. Identical
  contract; routed through the Shaw Azure tenant.

- **`local`**. `LocalOpenAICompatibleClient` (also wraps `AsyncOpenAI`
  with a custom `base_url`). Points at any OpenAI-compatible HTTP server
  — vLLM, llama.cpp server, LM Studio, Databricks Foundation Model APIs,
  or Databricks Model Serving. Reads `local_llm_endpoint`, `local_llm_model`,
  `local_llm_api_key`. Data residency stays inside whatever infrastructure
  hosts the endpoint.

Switching providers is a single environment-variable change. Tool-calling
support is implemented identically across all three via a shared
`_run_tool_loop` helper.

The recommended evolution path documented in v2.0 (frontier API for MVP →
Databricks-served open model after ~3 months / 5,000 logged queries) is
preserved unchanged; the only difference in v3.0 is that the swap is now
strictly a `LLM_PROVIDER=local` plus `local_llm_endpoint=...` change, no
code modifications required.

## 3.5 Observability &amp; Operational Surface

Two observability surfaces ship with v3.0:

- **Prometheus metrics** via `prometheus-fastapi-instrumentator` plus
  custom counters/histograms in `services/metrics.py`:
  `chat_in_flight`, `chat_total_seconds`, `chat_short_circuit_total{reason}`,
  `chat_confidence_total{label}`, `retrieval_latency_seconds{stage}`,
  `retrieval_mode_used{mode}`, `rca_chain_total{outcome}`, plus
  `llm_token_usage_total{model,kind}`. Scrape via the default
  `/metrics` endpoint exposed by the instrumentator.

- **Structured JSON logs** via `structlog` configured in `main.py`. Every
  log line is JSON with `iso_time`, `level`, `event`, plus event-specific
  keys. Rotate via the standard container log driver.

A degraded-vs-down distinction is exposed via `/api/health`: degraded
when DB or embedding model fails to load but the service is otherwise up.

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — System architecture</p>
<p><span class="label">Stayed:</span> Five first-class components plus
rule engine and audit log; on-premises deployment topology; LLM as the
only external dependency; read-only stance.</p>
<p><span class="label">Changed:</span> Two extra supporting services
(tool layer, nightly scheduler) shipped that v2.0 did not enumerate
separately. Three LLM providers wired (OpenAI / Azure / OpenAI-compatible
local) instead of one. Three-phase orchestration with explicit DB-session
lifetime breaks (Phase 1 commits before LLM call) — v2.0 spoke of "one
synchronous flow" without the lifecycle nuance. Prometheus + structlog
observability shipped.</p>
<p><span class="label">Considering:</span> Replace the in-process nightly
scheduler with APScheduler or a dedicated worker container once we have
more than three jobs. Add OpenTelemetry trace propagation from
Perspective → FastAPI → DB so a single chat turn produces a single trace
span.</p>
</div>
