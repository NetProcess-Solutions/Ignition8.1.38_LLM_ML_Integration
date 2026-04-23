# Data Model

27 tables across 8 schema groups. All created up front by
[`scripts/setup_database.sql`](../scripts/setup_database.sql) so the schema
contract is established on day one.

| Group | Tables |
|-------|--------|
| 1. Document corpus | `documents`, `document_chunks`, `ingestion_runs` |
| 2. Events & outcomes | `production_runs`, `downtime_events`, `quality_results`, `defect_events` |
| 3. Conversations & feedback-learning | `conversations`, `messages`, `message_feedback`, `user_corrections`, `outcome_linkages` |
| 4. Durable memory | `line_memory`, `memory_candidates` |
| 5. User profiles & permissions | `user_profiles`, `user_permissions` |
| 6. ML foundation | `feature_definitions`, `ml_models`, `ml_predictions`, `feature_snapshots` |
| 7. Configuration & versioning | `prompt_versions`, `business_rules` |
| 8. Audit + auxiliary | `audit_log`, `chunk_quality_signals` |

See the planning document for the full per-column rationale.

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

`audit_log` is append-only by convention (no UPDATE or DELETE policy
enforced at the DB level in MVP — add a Postgres trigger if your
compliance requirements demand it).
