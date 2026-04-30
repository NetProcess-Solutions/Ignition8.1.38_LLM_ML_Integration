# 5. Database Schema (As-Built)

The Postgres 16 schema is the durable substrate of the system. Every
piece of state — documents, chunks, events, tags, conversations,
audits, ML artifacts — lives in this database. Application restarts,
service redeployments, and even full container rebuilds change nothing
about what the system knows. The schema is the source of truth.

The v2.0 design specified ~27 tables across 8 schema groups. The
as-built MVP ships **30 tables across 9 groups**, plus three views, two
materialized views, and one immutability trigger. The additions are
deliberate: a closed-enum reference table for failure modes, a
re-rank-time signals table for chunk quality, and a forward-compatible
tag registry that the gateway will populate later.

Everything in this chapter is verifiable in
[scripts/setup_database.sql](scripts/setup_database.sql),
[scripts/seed_reference_data.sql](scripts/seed_reference_data.sql),
and the four migrations in
[scripts/migrations/](scripts/migrations/).

## 5.1 Schema Groups (At a Glance)

| Group                       | Tables                                                                                                                              | Purpose |
|-----------------------------|-------------------------------------------------------------------------------------------------------------------------------------|---------|
| 1. Document Corpus          | `documents`, `document_chunks`, `ingestion_runs`, `chunk_quality_signals`                                                           | Searchable text knowledge |
| 2. Reference Data           | `failure_modes`, `tag_registry`, `prompt_versions`, `business_rules`                                                                | Closed-enum lookups + ops config |
| 3. Production Telemetry     | `production_runs`, `downtime_events`, `quality_results`, `defect_events`                                                            | Structured plant events |
| 4. Work Orders              | `work_orders`, `event_clips`                                                                                                        | Maintenance + camera context |
| 5. Identity & Permissions   | `user_profiles`, `user_permissions`                                                                                                 | Per-operator personalization scaffolding |
| 6. Conversation             | `conversations`, `messages`                                                                                                          | Chat state + full audit substrate |
| 7. Feedback & Learning      | `message_feedback`, `user_corrections`, `outcome_linkages`, `memory_candidates`, `line_memory`                                      | Operator-driven learning |
| 8. ML Artifacts             | `ml_models`, `ml_predictions`, `feature_definitions`, `feature_snapshots`                                                           | Predictive model wiring (Phase 4-ready) |
| 9. Audit                    | `audit_log`                                                                                                                          | Append-only forensic record |

Total: 30 tables. The 9-group taxonomy improves on v2.0 by separating
*Reference Data* (closed-enum lookups + config) from *Document Corpus*
(searchable text) — a distinction the original design conflated.

## 5.2 Group 1 — Document Corpus

**`documents`**: top-level container for an ingested artifact (SOP, work-order
narrative, MOC packet, manual, internal note). Columns of note:

- `document_id UUID PK`, `source_uri TEXT`, `title TEXT`, `doc_type TEXT`
- `document_role TEXT` — `internal_authoritative` (1.2× weight), `external_reference` (0.6× weight),
  `wo_narrative` (1.0×). Drives the `_conditional_boost` step in retrieval.
- `effective_date DATE`, `superseded_by UUID NULL` — for SOP versioning
- `failure_mode_scope TEXT[]`, `equipment_scope TEXT[]` — coarse pre-filters that
  bypass embedding lookup when the query has a hard scope
- `metadata JSONB` — flexible per-source metadata (page ranges, original
  authors, etc.)

**`document_chunks`**: structure-aware fragments produced by
[service/services/chunker.py](service/services/chunker.py).

- `chunk_id UUID PK`, `document_id UUID FK`, `chunk_index INT`
- `chunk_text TEXT`, `chunk_tokens INT`
- `embedding VECTOR(1536)` (OpenAI `text-embedding-3-small` dimensionality)
- `bm25_tsv TSVECTOR` — generated column from `chunk_text` for keyword search
- `heading_path TEXT[]` — preserves the heading hierarchy this chunk lives under (e.g. `['DELAM_0047', 'Resolution', 'Step 3']`)
- `chunk_type TEXT` — `paragraph | table_row | list_item | code | callout`
- `failure_mode_codes TEXT[]`, `equipment_codes TEXT[]` — propagated from
  parent doc + locally re-extracted

Indexes:

- `idx_chunks_embedding_ivfflat` — `ivfflat (embedding vector_cosine_ops) WITH (lists = 100)`. The pgvector default for ≤100K rows.
- `idx_chunks_bm25_gin` — `GIN (bm25_tsv)` for the BM25 leg
- `idx_chunks_failure_mode_gin`, `idx_chunks_equipment_gin` — `GIN` for filtered retrieval

The hand-off plan when corpus exceeds ~250K chunks: switch to `hnsw`
(see migration 003 below). The view `v_pgvector_index_status` exposes
current row count + index type so an operator can monitor when the cutover is due.

**`ingestion_runs`**: one row per ingestion job, tracking embedding model
version, tokens consumed, success/failure counts, and source manifest. This is
how we know which corpus snapshot any given chat response was grounded against.

**`chunk_quality_signals`** *(NEW vs v2.0)*: per-chunk, per-feedback-type
counters used by the bounded re-rank step in retrieval (B11 partial). Columns:
`chunk_id`, `helpful_count`, `unhelpful_count`, `cited_in_correct_count`,
`cited_in_incorrect_count`, `last_updated`. The retrieval re-ranker pulls these
to apply a bounded ±30% multiplier to the RRF score; absent the table, the
re-rank step degrades to identity.

## 5.3 Group 2 — Reference Data

**`failure_modes`** *(NEW vs v2.0 prose)*: closed-enum reference of every failure
mode the system recognizes. Columns: `fm_code TEXT PK`, `fm_label TEXT`,
`fm_category TEXT`, `description TEXT`, `seeded_at TIMESTAMPTZ`. Pre-populated by
[scripts/seed_reference_data.sql](scripts/seed_reference_data.sql) with the
coating-line vocabulary (`delam_hotpull`, `delam_coldpull`, `sag`,
`coating_weight_var`, `pinhole`, `streak`, `fish_eye`, `crater`, `mottle`,
`off_tenter`, `tail_curl`, `web_break`, `splice_failure`, etc., ~25 codes).

A foreign key from `defect_events.fm_code` enforces that no defect can be
classified into a fabricated failure mode — a hallucination guardrail at
the database layer, not the application layer. v2.0 left this as a free-text
column; v3.0 hardens it.

**`tag_registry`**: scaffold for gateway-discovered tag enumeration. Columns:
`tag_path TEXT PK`, `tag_class TEXT`, `equipment_id TEXT`, `category TEXT`,
`engineering_units TEXT`, `core BOOL`, `keywords TEXT[]`, `discovered_at TIMESTAMPTZ`.

**Status**: <span class="status-stub">SCAFFOLD</span>. The MVP runs against the
hardcoded `KEY_TAGS` list in `ignition/scripts/config.py` (~50 tags). The
forward path (chapter 15) wires Ignition gateway `system.tag.browse` results
into this table.

**`prompt_versions`**: every system prompt that has ever been active.
`prompt_id UUID PK`, `prompt_name TEXT`, `version TEXT`, `body TEXT`,
`is_active BOOL`, `activated_at TIMESTAMPTZ`. Lookup pattern is
`(prompt_name, is_active=TRUE)` — the active version is selected by the
`is_active` flag, not by versioned naming. Currently seeded:
`system_prompt` (versions `v1` deprecated, `v2` active), `rca_step1` (`v1`),
`rca_step2` (`v1`). Every `messages.prompt_version` row references the
version string, enabling per-prompt-version A/B analysis.

**`business_rules`**: declarative YAML rules surfaced via
[service/services/rules.py](service/services/rules.py). Columns:
`rule_id UUID PK`, `rule_name TEXT`, `rule_yaml TEXT`, `is_active BOOL`,
`version INT`. Rules fire against the live curated tag block to surface
deterministic warnings ("if line speed > 250 fpm and Style ∈ {A, B},
surface delamination warning"). Hot-loadable; engineers add rules without
touching code.

## 5.4 Group 3 — Production Telemetry

The structured event tables. These are the system's primary source of
"what happened on the line" — distinct from the document corpus, which
captures "what we know about why it happens."

- **`production_runs`** — one row per run. PK `run_id`. Columns: `line_id`,
  `start_time`, `end_time`, `product_style`, `recipe_id`, `crew`, `shift`,
  `target_specs JSONB`, `metadata JSONB` (extended for change-ledger lookups),
  `total_yards`, `scrap_yards`.
- **`downtime_events`** — structured downtime with `start_time`, `end_time`,
  `category`, `subcategory`, `narrative`, `attributed_run_id`.
- **`quality_results`** — sample-level lab + inline test results.
  `qr_id PK` (matches the QR-NNNNN regex in anchor parsing), `sample_time`,
  `test_name`, `value NUMERIC`, `unit`, `pass_fail`, `attributed_run_id`.
- **`defect_events`** — discrete defect occurrences. `defect_id PK`,
  `event_time`, `fm_code TEXT FK→failure_modes`, `severity`, `attributed_run_id`,
  `equipment_id`, `narrative`, `metadata JSONB`. The FK enforcement (§5.3) is
  what makes this table an audit-grade source for failure-mode trending.

These tables are the substrate for the `nearest_historical_runs` and
`defect_events_in_window` tools (chapter 7), the failure-mode-matched
history bucket (chapter 4), and the precision dashboard (chapter 9).

## 5.5 Group 4 — Work Orders & Camera Context

**`work_orders`**: WO records joined to event scope. `wo_id PK`,
`equipment_id`, `opened_at`, `closed_at`, `summary`, `narrative TEXT`,
`work_type`, `parts_used JSONB`. Narratives flow into the chunker and end up
in `document_chunks` with `document_role='wo_narrative'`.

**`event_clips`**: Symphony video clip handles attached to events. `clip_id PK`,
`event_type` (`downtime|defect|quality`), `event_id` (polymorphic), `clip_handle`,
`start_time`, `end_time`, `extraction_status TEXT`, `extracted_text TEXT NULL`.

**Status**: clip ingestion is <span class="status-stub">STUB</span>. The
`extracted_text` column is intentionally nullable for forward compatibility
with the Symphony adapter (B11 in roadmap).

## 5.6 Group 5 — Identity & Permissions

**`user_profiles`**: `user_id PK`, `display_name`, `gateway_subject` (JWT
`sub` claim), `default_role`, `personalization JSONB` (UI prefs, density,
preferred response length).

**`user_permissions`**: `user_id FK`, `permission TEXT`, `scope JSONB`. Used
by `routers/deps.py::require_attributed_user` — gateway-issued JWT must
carry `sub`, role, and scope claims that resolve to a row here. The
in-process `_PERMISSIONS_CACHE` reloads from this table on each cache miss
with a TTL of 60 s.

## 5.7 Group 6 — Conversation State + Audit

**`conversations`**: `conversation_id PK`, `user_id`, `created_at`,
`metadata JSONB` (UI surface, entry context like alarm-triggered).

**`messages`**: the **load-bearing** table of the system. Every chat turn
writes one row.

| Column                  | Purpose |
|-------------------------|---------|
| `message_id UUID PK`    | Stable ID, also returned to client for feedback round-tripping |
| `conversation_id UUID FK` | Parent conversation |
| `role TEXT`             | `user|assistant|system` |
| `body TEXT`             | The text content shown to the user |
| `created_at TIMESTAMPTZ` | UTC, used for partition routing |
| `prompt_version TEXT`   | The version of `system_prompt_v?` active when this turn ran |
| `model_name TEXT`       | E.g. `gpt-4o-mini-2024-07-18` |
| `model_params JSONB`    | Temperature, top_p, max_tokens |
| `latency_ms_total INT`  | Wall clock |
| `latency_breakdown JSONB` | Per-stage (anchor, retrieve, llm, persist) |
| `tokens_prompt INT`, `tokens_completion INT` | Cost accounting |
| `tool_calls JSONB`      | Full record of any tool calls + results |
| `citations JSONB`       | The structured `SourceCitation` list returned to client |
| `confidence_label TEXT` | `confirmed|likely|hypothesis|insufficient_evidence` |
| `failure_mode_code TEXT NULL` | Auto-classified for assistant turns when applicable |
| `excluded_buckets JSONB` | The list of evidence buckets withheld and why |
| `context_snapshot JSONB` | The full curated context package — every tag value, every retrieved chunk ID, every rule evaluated, every memory entry consulted, the parsed anchor, the change ledger, the anomaly result, the RCA trace |
| `rca_summary JSONB NULL` | Populated only when the two-step RCA chain ran (chapter 7) |
| `audit_hash TEXT`       | SHA-256 of `context_snapshot || body || citations` for tamper detection |

The `context_snapshot` column is what makes every response replayable. Any
operator-visible answer can be reconstructed from this single row.

**Partitioning**: `messages` is range-partitioned monthly by `created_at`
via `pg_partman` (migration 001). The retention policy keeps 24 hot months
+ infinite cold (detached, archived to S3). Detaching a partition does not
break query plans because the parent table is the only thing the
application addresses.

## 5.8 Group 7 — Feedback & Learning

The substrate for the operator-driven learning loop (chapter 9).

- **`message_feedback`** — `feedback_id PK`, `message_id FK`, `user_id FK`,
  `signal_type` (10 enum values: `helpful`, `unhelpful`, `wrong_anchor`,
  `wrong_failure_mode`, `wrong_citation`, `missed_evidence`, `actionable`,
  `not_actionable`, `confirmed_outcome`, `refuted_outcome`),
  `signal_payload JSONB`, `created_at`. The 10-value enum is wider than v2.0's
  3-value (👍/👎/refute) — the additional codes are what make the
  re-ranker meaningful.
- **`user_corrections`** — when the operator explicitly corrects an answer.
  `correction_id PK`, `message_id FK`, `correction_type`, `before TEXT`,
  `after TEXT`, `engineer_reviewed BOOL`, `applied_to_memory BOOL`.
- **`outcome_linkages`** — links an RCA conclusion to its 24-h-later
  resolution. `linkage_id PK`, `message_id FK`, `outcome_event_id`,
  `outcome_type` (`confirmed|partial|refuted|inconclusive`), `closed_at`.
  Drives the precision view (§5.10).
- **`memory_candidates`** — proposed line-memory entries awaiting engineer
  review. `candidate_id PK`, `proposed_text`, `proposed_by_user_id`,
  `source_message_id`, `status` (`pending|approved|rejected`).
- **`line_memory`** — engineer-curated tribal knowledge. `memory_id PK`,
  `text TEXT`, `equipment_scope TEXT[]`, `failure_mode_scope TEXT[]`,
  `style_scope TEXT[]`, `status` (`approved|reviewed|challenged|deprecated`),
  `version INT`, `last_reviewed_at`. Approved memories rank highest in
  retrieval (1.5× weight).

## 5.9 Group 8 — ML Artifacts

Wired but not yet populated by a live training pipeline.

- **`ml_models`**: `model_id PK`, `model_name`, `version`, `framework`,
  `training_run_id`, `is_active BOOL`, `metadata JSONB`.
- **`ml_predictions`**: `prediction_id PK`, `model_id FK`, `target_entity_type`,
  `target_entity_id`, `prediction JSONB`, `confidence NUMERIC`, `created_at`.
- **`feature_definitions`**: `feature_id PK`, `feature_name`, `feature_class`
  (`scalar|aggregate|categorical`), `source TEXT`, `unit`.
- **`feature_snapshots`**: `snapshot_id PK`, `entity_type` (`run|event|live`),
  `entity_id`, `snapshot_time`, `features JSONB` (sparse map), `metadata JSONB`.
  This is the table the multivariate anomaly detector (chapter 8) reads from.
  It is **already being populated** by the live pipeline — every time
  `_phase_pre_llm` runs a current-state query, a snapshot is persisted. The
  Mahalanobis fit consumes 90 d of historical snapshots.

## 5.10 Group 9 — Audit + Views

**`audit_log`** — an append-only summary of every state-changing operation.
One row per chat turn (mirror of `messages`), one row per feedback event,
one row per memory state transition, one row per ingestion run, one row
per RCA step.

| Column         | Purpose |
|----------------|---------|
| `audit_id UUID PK` | |
| `actor_user_id` | NULL for system actors |
| `action TEXT` | Verb-noun (`chat.respond`, `feedback.submit`, `memory.approve`, `ingest.complete`, `rca.step1`, `rca.step2`) |
| `entity_type TEXT`, `entity_id UUID` | Polymorphic reference |
| `payload JSONB` | Action-specific (citations, outcome verdict, memory diff) |
| `created_at TIMESTAMPTZ` | UTC |
| `audit_hash TEXT` | SHA-256 chained to the previous audit row |

**Immutability trigger** (`audit_log_immutable`): raises an exception on
any `UPDATE` or `DELETE` against `audit_log`. This is enforced at the
**database** layer — even a compromised application cannot tamper with
historical audit rows. The trigger is non-disableable by application-role
users; a superuser would be required, and the act of disabling itself
generates a `pg_audit` event.

`audit_log` is also pg_partman-managed (migration 001), monthly partitions,
infinite retention.

### Views

- **`v_pgvector_index_status`**: real-time row counts + index type for
  `document_chunks.embedding`. Operator-facing for cutover monitoring.
- **`v_messages_recent`**: last-7-day messages join with conversation +
  user, with per-row latency breakdown surfaced as columns. Drives the
  ops dashboard.

### Materialized views

- **`v_rca_precision_daily`** *(NEW vs v2.0, migration 004)*: nightly
  refresh aggregating `outcome_linkages` to produce per-day RCA precision.
  Columns: `day DATE`, `total_rca INT`, `confirmed INT`, `partial INT`,
  `refuted INT`, `inconclusive INT`, `precision_strict NUMERIC`,
  `precision_lenient NUMERIC`. Refreshed nightly by the scheduler in
  `service/main.py` lifespan. Drives the public report card on the
  assistant's own accuracy (chapter 9).
- **`v_chat_perf_daily`** *(migration 2026_04)*: per-day p50/p95 latency
  and token-cost rollups from `messages`.

## 5.11 Migrations Shipped

| File | Purpose |
|------|---------|
| `001_partition_messages.sql` | pg_partman setup for `messages` + `audit_log`; monthly granularity, 24-month hot retention, automatic partition pre-creation |
| `002_feature_snapshots_retention.sql` | 90-day retention policy on `feature_snapshots` (older rows aggregated into monthly summaries before deletion) |
| `003_pgvector_index_migration.sql` | Adds `v_pgvector_index_status` view + the runbook for the ivfflat → hnsw cutover when row count exceeds threshold |
| `004_v_rca_precision_daily.sql` | Materialized view + nightly refresh hook |
| `2026_04_v_chat_perf_daily.sql` | Performance rollup view |

## 5.12 As-Built Deltas — Schema Specifics

Three changes worth flagging beyond the additive tables already noted:

1. **`production_runs.metadata` extended.** v2.0 specified a thin metadata
   column. As built, `metadata` carries `recipe_dominant`, `crew_dominant`,
   `equipment_changeovers`, and `target_specs_version` keys consumed by the
   change-ledger builder. No schema migration needed; JSONB additive.
2. **`messages.context_snapshot.parsed_anchor`** schema added. Every snapshot
   now contains the full `QueryAnchor` Pydantic dump. Older rows (pre-A1) lack
   this key; the audit reconstruction path handles `NULL` gracefully.
3. **`defect_events.fm_code` FK** to `failure_modes`. Hard guardrail; required
   recovery script for any pre-existing rows with non-canonical codes
   (handled in `scripts/seed_reference_data.sql` post-conditions).

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Database Schema</p>
<p><span class="label">Stayed:</span> 27 of the v2.0 tables ship as designed,
including all of `documents`/`document_chunks`/`ingestion_runs`,
`production_runs`/`downtime_events`/`quality_results`/`defect_events`,
`work_orders`/`event_clips`, `conversations`/`messages`,
`message_feedback`/`user_corrections`/`outcome_linkages`/`memory_candidates`/`line_memory`,
all four `ml_*` tables, `audit_log`, and `business_rules`/`prompt_versions`/`user_profiles`/`user_permissions`.</p>
<p><span class="label">Changed:</span> Three new tables added —
<code>failure_modes</code> (FK-enforced closed enum, hardens hallucination
guardrail at DB layer), <code>chunk_quality_signals</code> (per-chunk
re-rank inputs for B11 partial), <code>tag_registry</code> (scaffold
ahead of B13/A5/A6 gateway wiring). Two materialized views added
(<code>v_rca_precision_daily</code>, <code>v_chat_perf_daily</code>),
two infra views (<code>v_pgvector_index_status</code>,
<code>v_messages_recent</code>). Monthly partitioning on
<code>messages</code> + <code>audit_log</code> via pg_partman.
Database-layer immutability trigger on <code>audit_log</code>.</p>
<p><span class="label">Considering:</span> Cutover from ivfflat to hnsw
once <code>document_chunks</code> exceeds ~250K rows (migration 003 has
the runbook; trigger is operator-monitored, not yet automated).
Partitioning <code>feature_snapshots</code> by month (currently
retention-managed in-place; will become unwieldy past ~10M rows).
Splitting <code>audit_log.payload</code> by action verb into typed
sibling tables for query performance once volume warrants.</p>
</div>
