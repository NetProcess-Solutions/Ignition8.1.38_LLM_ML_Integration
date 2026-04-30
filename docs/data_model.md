# Data Model (v3.0)

30 tables across 9 schema groups, all created up front by
[`scripts/setup_database.sql`](../scripts/setup_database.sql) so the schema
contract is established on day one. Reference data (`failure_modes`,
`user_permissions`) is seeded by
[`scripts/seed_reference_data.sql`](../scripts/seed_reference_data.sql).

| Group | Tables |
|-------|--------|
| 1. Document corpus | `documents`, `document_chunks`, `ingestion_runs` |
| 2. Events & outcomes | `production_runs`, `downtime_events`, `quality_results`, `failure_modes`, `defect_events`, `work_orders`, `event_clips` |
| 3. Conversations & feedback-learning | `conversations`, `messages`, `message_feedback`, `user_corrections`, `outcome_linkages`, `memory_candidates`, `chunk_quality_signals` |
| 4. Durable memory | `line_memory` |
| 5. User profiles & permissions | `user_profiles`, `user_permissions` |
| 6. ML foundation | `ml_models`, `ml_predictions`, `feature_snapshots`, `feature_definitions` |
| 7. Configuration & versioning | `prompt_versions`, `business_rules` |
| 8. Audit | `audit_log` |
| 9. Tag registry | `tag_registry` |

See the planning document for the full per-column rationale; the v2.0
Technical Design Document §4 has the canonical column-level spec.

## Indexes that matter

- `idx_chunks_embedding` — pgvector ivfflat on `document_chunks.embedding`
  (cosine). Set `lists = 100` for now; tune as the corpus grows.
- `idx_memory_embedding` — pgvector ivfflat on `line_memory.embedding`
  with `lists = 50`.
- `idx_chunks_text_trgm` — trigram index for keyword fallback.
- `idx_audit_event_time`, `idx_audit_user_time`, `idx_audit_entity` —
  let you slice the audit log fast.

## Foreign key behavior

- `messages` cascade-delete with their `conversation`.
- `document_chunks` cascade-delete with their `document`.
- `message_feedback`, `user_corrections`, `outcome_linkages` cascade-delete
  with their `message`.
- `line_memory.superseded_by` is `ON DELETE SET NULL`.
- `user_corrections.created_memory_id` → `line_memory(id)` is `ON DELETE SET NULL`.

## Append-only contract

`audit_log` is append-only at the DB level: a `BEFORE UPDATE OR DELETE`
trigger raises an exception. Application code only inserts.

## v2.0 additions worth calling out

- **`failure_modes`** is a closed enum reference table; `defect_events.failure_mode`
  is `FK ... ON DELETE RESTRICT`. Adding a new mode requires a row insert
  here first. This is the discipline that makes failure-mode-matched
  retrieval (design §3.3) actually work.
- **`work_orders`** is synced nightly from the Ignition WO database.
  `problem_description` and `resolution_notes` are dual-ingested into
  `document_chunks` with `metadata.source_type='work_order'` so they're
  retrievable by text similarity in addition to structured joins.
- **`event_clips`** holds Symphony camera-clip handles only. Footage stays
  in Symphony; the LLM sees the handle as a citation reference.
- **`tag_registry`** caches discovered tag metadata from the Ignition
  ItemInstance database, with assigned behavior class
  (setpoint_tracking / oscillating_controlled / process_following /
  discrete_state). Driven by autonomous discovery — adding a tag in
  Ignition surfaces it here on next refresh, no code change.
- **`messages.context_snapshot`** captures the parsed anchor, every
  evidence bucket populated AND every bucket explicitly excluded with
  reason, retrieval scores, rules matched, memory ids, clip handles,
  and prompt+model pinning. Any response can be reconstructed end-to-end.
