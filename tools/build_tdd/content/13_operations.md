# 13. Operations & Deployment

The system runs as a single Docker Compose stack: Postgres 16 with
pgvector, the FastAPI service container, and (optionally) a vLLM
sidecar. This chapter documents the as-built operational surface —
what the deploy looks like, what gets monitored, what gets backed up,
what gets paged, and how cutovers happen.

## 13.1 Deployment Topology

```
┌──────────────────────────────────────────────────────────────────┐
│ Plant Linux VM (mid-range, ≥16 GB RAM, ≥4 vCPU, ≥200 GB SSD)     │
│                                                                  │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────────┐    │
│  │ postgres-16  │◄──┤ fastapi-svc  │──►│ vllm-host (opt)    │    │
│  │ + pgvector   │   │ (uvicorn x4) │   │ OR public OpenAI   │    │
│  └──────────────┘   └──────┬───────┘   └────────────────────┘    │
│         │                  │                                     │
│         ▼                  ▼                                     │
│   /var/lib/postgresql  /var/log/svc                              │
└──────────────────────────────────────────────────────────────────┘
                              ▲
                              │ HTTPS (8000)
                              │ Bearer JWT + API key
                              │
                ┌─────────────┴─────────────┐
                │ Ignition Gateway          │
                │ Perspective ChatView      │
                └───────────────────────────┘
```

Resource sizing for the pilot deployment:

| Component        | CPU   | RAM   | Disk        |
|------------------|-------|-------|-------------|
| Postgres 16      | 2     | 6 GB  | 100 GB SSD  |
| FastAPI service  | 2     | 4 GB  | 5 GB        |
| vLLM sidecar (opt) | 4 GPU | 24 GB | 80 GB     |
| Headroom         | —     | 2 GB  | 15 GB       |

Single-VM is the pilot configuration. Horizontal scale is straightforward
(stateless service behind a load balancer; Postgres is the only shared
state) but is not required at pilot volumes (≤5 concurrent operators per
service instance).

## 13.2 docker-compose.yml

The shipped [docker-compose.yml](docker-compose.yml) defines two services
(plus an optional commented-out `vllm` block):

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: ignition_chatbot
      POSTGRES_USER: chatbot
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./scripts:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U chatbot -d ignition_chatbot"]
      interval: 10s
    ports:
      - "5432:5432"

  service:
    build: ./service
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+asyncpg://chatbot:${POSTGRES_PASSWORD}@postgres:5432/ignition_chatbot
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      API_KEY: ${API_KEY}
      LLM_PROVIDER: ${LLM_PROVIDER:-openai}
      LOCAL_LLM_ENDPOINT: ${LOCAL_LLM_ENDPOINT:-}
      SERVICE_ENV: ${SERVICE_ENV:-production}
    ports:
      - "8000:8000"
    restart: unless-stopped

volumes:
  pgdata:
```

The `./scripts` mount auto-initializes a fresh database with the schema
on first boot. Subsequent boots are no-ops (Postgres skips the init
directory once `PGDATA` exists).

## 13.3 Environment File

The deployment reads secrets and config from `.env` at the compose root.
Required keys:

```
POSTGRES_PASSWORD=<strong random>
DATABASE_URL=postgresql+asyncpg://chatbot:<pw>@postgres:5432/ignition_chatbot
OPENAI_API_KEY=sk-...
API_KEY=<32+ char random>
GATEWAY_JWT_SECRET=<HS256 shared secret>
LLM_PROVIDER=openai
SERVICE_ENV=production
```

Optional overrides for Azure / local LLM:

```
LLM_PROVIDER=azure_openai
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT=<deployment-name>
AZURE_OPENAI_API_VERSION=2024-08-01-preview
```

```
LLM_PROVIDER=local
LOCAL_LLM_ENDPOINT=http://vllm-host:8000/v1
LOCAL_LLM_MODEL=meta-llama/Llama-3.1-8B-Instruct
```

The full env reference lives in [INSTALL.md](INSTALL.md) and Appendix B.

## 13.4 Startup Sequence

`service/main.py::lifespan()`:

1. Open Postgres connection pool (`asyncpg`, default 20 connections,
   configured via `db_pool_size`).
2. Run a one-time migration check (`pg_partman` extension present?
   `audit_log_immutable` trigger present? `vector` extension present?).
   On any missing required extension, log critical and exit 1.
3. Warm the embedding client (one-shot `OpenAI Embeddings.create` against
   `"warmup"` to surface auth failures at boot rather than first request).
4. Schedule the nightly outcome-closure job (APScheduler,
   default `0 4 * * *` UTC).
5. Schedule the 4-hourly anomaly-model re-fit
   (`anomaly_fit_interval_seconds`).
6. Start the FastAPI app.

The route handlers do not block on extension or migration checks at
request time; the lifespan check is the choke point. `GET /api/health`
exposes the booted-state result so external monitoring can confirm.

## 13.5 Health & Readiness Endpoints

Three endpoints from
[service/routers/health.py](service/routers/health.py):

- `GET /api/health` — returns `200 {"db": "ok", "embeddings": "ok",
  "llm": "ok|degraded|down"}`. Used by container healthcheck and
  external monitoring.
- `GET /api/health/deep` — exercises a single round-trip through the DB,
  the embedding API, and the LLM provider. Slow (~1 s); used for boot
  validation only, not livenessprobe.
- `GET /api/version` — git SHA + prompt versions + model identifier.

## 13.6 Observability

Three layers:

1. **Structured logs** via `structlog`. Every chat turn emits one
   `chat.complete` event with `trace_id`, `user_id`, `phase_durations`,
   `tool_calls_count`, `confidence_label`. Forwardable to any
   JSON-aware aggregator (Loki, Splunk, Elastic). Local default writes
   to `/var/log/svc/app.log` with daily rotation.
2. **Prometheus metrics** at `/metrics` (Prometheus exposition format).
   Histograms for retrieval/LLM/total latency; counters for
   tool-call type, confidence-label distribution, refusal rate; gauges
   for active conversations, memory entries by status, anomaly model
   age.
3. **Postgres logs** to the standard Postgres log destination
   (configurable; default container stdout).

A reference Grafana dashboard ships in `docs/observability/dashboards/coater1.json`
(created if not already present in the repo).

### What gets paged

The recommended alerting (built around standard Prometheus rules):

- `up{job="coater1-svc"} == 0 for 5m` — service down
- `pg_up == 0 for 1m` — Postgres down
- `rate(chat_responses_total{confidence="insufficient_evidence"}[15m])
   / rate(chat_responses_total[15m]) > 0.20` — refusal rate spike
- `rca_precision_daily{precision_strict_7d_avg} < 0.50` — precision
  dropped below target
- `histogram_quantile(0.95, retrieval_duration_seconds_bucket) > 1.0`
  — retrieval latency p95 > 1 s
- `pg_stat_activity{state="active"} > 0.8 * pool_size` — connection
  pool exhaustion imminent

## 13.7 Backup & Restore

Postgres is the only stateful component. The recommended backup posture:

- **`pg_dump` nightly** at `02:00 UTC` to `/var/backups/coater1/`,
  retention 14 days local.
- **WAL archiving** to S3 (or compatible) for point-in-time recovery
  past the 14-day local window.
- **Quarterly restore drill** — pick a backup, restore to a sandbox
  environment, run the test suite against it, confirm green. The
  procedure is documented in [docs/runbook.md](docs/runbook.md).

`document_chunks.embedding` data takes the bulk of disk; estimating
~6 KB per chunk including text + embedding (1536-dim float16). 100K
chunks ≈ 600 MB. Chat turns at ~5 KB/turn including JSONB context_snapshot.

## 13.8 Rate Limiting

`slowapi` integrated via
[service/routers/rate_limit.py](service/routers/rate_limit.py).
Default limits per `chat_user_key` (which keys on resolved `user_id`):

- `chat_rate_limits = "10/minute, 200/hour"` for `/api/chat`
- `feedback_rate_limits = "60/minute, 1000/hour"` for `/api/feedback`
- `corrections_rate_limits = "5/minute, 50/hour"` for `/api/corrections`

Limits are tunable via env (`CHAT_RATE_LIMITS=...`). 429 responses
include a `Retry-After` header.

A 429 from the chat endpoint is logged as a structured event so the
ops team can spot legitimate operators being throttled (a sign that
limits need raising) vs runaway scripted clients (a sign of a bug).

## 13.9 Cutover Procedures

Three planned cutovers are documented:

### ivfflat → hnsw (chapter 5 §5.10)

Triggered when `v_pgvector_index_status.row_count > 250000`. Procedure:

1. Build `idx_chunks_embedding_hnsw` with `CREATE INDEX CONCURRENTLY`
2. `ANALYZE document_chunks`
3. Compare query plans on a representative sample; expect hnsw to win
4. Drop ivfflat: `DROP INDEX idx_chunks_embedding_ivfflat`

Zero downtime; hot index swap.

### Prompt version (e.g. v2 → v3)

1. Insert new row into `prompt_versions` with `is_active = false`,
   identical `prompt_name`, bumped `version`
2. Verify prompt by running eval set (manual until B13 ships)
3. Flip active: `UPDATE prompt_versions SET is_active = false WHERE
   prompt_name = 'system_prompt' AND is_active = true; UPDATE ...
   v3 SET is_active = true`
4. Monitor `v_rca_precision_daily` for the next 7 days; rollback
   procedure same UPDATE in reverse

### Embedding model bump (e.g. text-embedding-3-small → text-embedding-3-large)

This is the most expensive cutover; embedding dimensions and
similarity space change.

1. Provision a sibling column `document_chunks.embedding_v2 VECTOR(3072)`
2. Backfill via batched `re_embed_all.py` script (estimated cost given
   in script header)
3. Build sibling ivfflat/hnsw index on `embedding_v2`
4. Flip retrieval to use `embedding_v2` via env override
5. Drop `embedding` column after monitoring window

This is a Phase 4 procedure; not exercised in the current deployment.

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Operations</p>
<p><span class="label">Stayed:</span> Single-VM Docker Compose
deployment. Postgres as sole stateful component. /api/health probe.
JWT + API key combined auth.</p>
<p><span class="label">Changed:</span> APScheduler nightly outcome
closure + 4-hourly anomaly re-fit baked into `lifespan()`. Three-layer
observability (structlog + Prometheus + Postgres logs) with reference
dashboard. slowapi rate limiting on three endpoints, all keyed on
resolved `user_id`. pg_partman managing monthly partitions on
`messages` and `audit_log` (migration 001). Documented cutover
procedures for ivfflat → hnsw, prompt version, and embedding-model
bump.</p>
<p><span class="label">Considering:</span> A multi-instance HA
deployment guide (single-VM is not the architectural ceiling, just
the pilot configuration). Postgres logical replication to a read
replica for analytics workloads. Scheduled VACUUM + REINDEX on the
ivfflat index. Auto-tuned `lists` parameter on ivfflat as row count
grows.</p>
</div>
