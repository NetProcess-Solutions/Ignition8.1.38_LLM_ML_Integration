# Operational Runbook

## Daily

- Check service health: `curl http://localhost:8000/api/health`
- Spot-check audit log for errors:
  `SELECT event_type, details FROM audit_log
     WHERE details::text ILIKE '%error%' AND created_at > NOW() - INTERVAL '24 hours';`

## Weekly

- Review feedback rollup:
  ```sql
  SELECT signal_type, signal_value, COUNT(*)
  FROM message_feedback
  WHERE created_at > NOW() - INTERVAL '7 days'
  GROUP BY 1, 2 ORDER BY 1, 2;
  ```
- Review pending corrections needing engineer attention:
  ```sql
  SELECT id, message_id, correction_type, corrected_claim, created_at
  FROM user_corrections WHERE status = 'submitted';
  ```
- Review challenged memories:
  ```sql
  SELECT id, content, challenge_count, last_challenged_at
  FROM line_memory WHERE status = 'challenged';
  ```

## When ingesting new reports

```
docker compose exec ai-service python -m scripts.ingest \
    --source-type maintenance_report --line-id coater1 \
    --path /app/data/incoming/<your-folder>/
```

The ingester is **idempotent on `(source_type, source_id)`**: re-running
on the same file replaces its chunks. Use this when a report is corrected.

## When updating prompts

1. Edit the prompt file in `service/config/prompts/`
2. Bump the version (e.g. add `system_prompt_v2.txt`)
3. Add the new entry to `PROMPTS_TO_SEED` in `scripts/seed_initial_data.py`
4. Run the seed script — it deactivates older versions automatically
5. Verify in the next chat response: `prompt_version` field shows the new id

## When updating rules

1. Edit `service/config/rules/coater1_rules.yaml`
2. Re-run the seed script

## When rotating API_KEY

1. Generate a new strong key
2. Update `.env`
3. Restart the service: `docker compose up -d ai-service`
4. Update `ai/config.py` in the Ignition Designer with the new key
5. Save and commit the Ignition project

## Backups

The single source of truth is the `ignition_chatbot` PostgreSQL database.

```
docker compose exec postgres pg_dump -U chatbot ignition_chatbot \
    | gzip > backup-$(date +%F).sql.gz
```

## Recovery

```
gunzip -c backup-2026-04-22.sql.gz \
  | docker compose exec -T postgres psql -U chatbot -d ignition_chatbot
```

## Common issues

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| 401 from `/api/chat` | `API_KEY` mismatch between `.env` and `ai/config.py` | Update one to match the other; restart |
| 422 from `/api/chat` | Ignition sent an unknown field in `live_context` | Sync `ai/context.py` with `CuratedContextPackage` schema |
| Empty `documents` table after ingest | Wrong path or no parsable files at that path | Run with `--path` pointing directly to a `.txt` to confirm the basic path works |
| Embedding model not loading | First-run download failed | `docker compose down ai-service && docker compose up -d --build ai-service` |
| pgvector extension missing | Wrong base image or DB created before init script ran | Drop the volume: `docker compose down -v && docker compose up -d` |
| `No active prompt found` | Forgot to run the seed script | `docker compose exec ai-service python -m scripts.seed_initial_data` |
| LLM hallucination noticed | Prompt drift, too few sources retrieved | Review `messages.context_snapshot.summary`; raise `RETRIEVAL_TOP_K` or ingest more docs; consider a stricter system prompt |

## v2.0 nightly integrations

The orchestrator ships with two optional nightly jobs (off by default).
Enable them by setting environment variables in `service/.env`:

```
SCHEDULER_ENABLED=true
WO_SYNC_ENABLED=true
IGNITION_WO_DB_URL=postgresql+asyncpg://wo_user:secret@wo-host:5432/ignition_wo
SYMPHONY_BACKFILL_ENABLED=true
```

Restart the service to pick them up. Logs are emitted as
`wo_sync_complete` and `symphony_backfill_complete` events.

## Tag discovery (Ignition Gateway)

The gateway script `ai.discovery.runDiscovery()` walks the Coater 1 tag
tree and writes `tag_registry`. Schedule it as a nightly Gateway Timer
(03:00 local). The orchestrator then uses tag_registry to drive tier-1
and tier-2 tag selection per query.

To re-run on demand from a Designer script console:

```python
import ai.discovery as d
d.runDiscovery()
```

## Anchor parsing audit

When a chat answer looks wrong, fetch its parsed anchor:

```sql
SELECT context_snapshot->'parsed_anchor', context_snapshot->'excluded_buckets'
FROM messages WHERE id = '<message_id>';
```

If `anchor_status` is `clarification_needed_*` the orchestrator
short-circuited and asked for clarification. If `anchor_type` is
`past_event` and the answer mentions a current tag value, that's a
prompt regression — escalate.


## Storage growth (Sprint 2 / A3)

The 'messages' and 'audit_log' tables are monthly-partitioned via
pg_partman after migration. Run once per environment:

    psql -f scripts/migrations/001_partition_messages.sql

Maintenance (daily, via pg_cron or external cron):

    SELECT partman.run_maintenance(p_analyze := false);

Retention is enforced by pg_partman:
- audit_log: 24 months (configured in the migration script)
- messages:  indefinite (training corpus)
- feature_snapshots cache rows: see scripts/migrations/002_feature_snapshots_retention.sql

### pgvector index migration

Monitor with:

    SELECT * FROM v_pgvector_index_status;

When 'recommendation' is 'plan_migration' (>80k chunks), schedule a
maintenance window. When >100k, perform the swap using the DDL embedded
in scripts/migrations/003_pgvector_index_migration.sql. The new HNSW
index is built CONCURRENTLY; readers stay online. Followups:
- ANALYZE document_chunks
- ALTER SYSTEM SET hnsw.ef_search = 40

