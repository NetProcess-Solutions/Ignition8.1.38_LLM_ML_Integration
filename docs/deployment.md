# Deployment Guide

## Prerequisites

- Docker & Docker Compose
- Network connectivity from the Docker host to:
  - The Ignition gateway (port 8088 typical)
  - The LLM provider (OpenAI: api.openai.com:443; Azure: your endpoint)
- An OpenAI or Azure OpenAI API key
- Read access to the source files / database where your Coater 1
  maintenance reports, downtime reports, and quality data live

## Step-by-step

### 1. Configure environment

```
cp .env.example .env
```

Edit `.env`:
- Set `POSTGRES_PASSWORD` to a strong value
- Set `API_KEY` to a strong random value (at least 32 chars)
- Set `OPENAI_API_KEY` (or the Azure equivalents) and your model name
- Optionally adjust `RETRIEVAL_TOP_K`, `RETRIEVAL_MIN_SCORE`,
  `RETRIEVAL_RECENT_EVENTS_HOURS`

### 2. Bring up the stack

```
docker compose up -d --build
```

The first build downloads the embedding model (~80 MB) into the image.

### 3. Verify

```
curl http://localhost:8000/api/health
```

Expected:
```json
{"status":"ok","database":true,"embedding_model":true,"llm_provider":"openai","version":"0.1.0"}
```

### 4. Seed prompts, rules, demo memory, and admin user profile

```
docker compose exec ai-service python -m scripts.seed_initial_data
```

You should see lines like:
```
+ prompt seeded: system_prompt v1
+ rule seeded: zone3_overshoot_high_speed
+ memory seeded: equipment_fact
+ user profile seeded: admin
```

### 5. Ingest your initial corpus

Mount or copy your reports into the container, then run the ingest script.
Two patterns:

**Patter A: text files (one report per file)**

```
docker compose exec ai-service python -m scripts.ingest \
    --source-type maintenance_report \
    --line-id coater1 \
    --path /app/data/incoming/maintenance/
```

**Pattern B: quality CSV with rows**

```
docker compose exec ai-service python -m scripts.ingest \
    --source-type quality_report_csv \
    --line-id coater1 \
    --path /app/data/incoming/quality.csv
```

For PDF / Word / Excel formats, see [docs/ingestion.md](ingestion.md) — you
will write a small parser adapter that converts your format into either a
folder of text files or a CSV the existing ingester can consume.

### 6. Configure Ignition

1. In the Ignition Designer, install the gateway scripts under
   `Project Browser > Scripting > Script Library`. See
   [ignition/scripts/README.md](../ignition/scripts/README.md).
2. Edit `ai/config.py`:
   - `AI_SERVICE_URL` — the URL the Ignition gateway reaches the FastAPI
     service at. Inside the same host this is usually
     `http://<docker-host-ip>:8000`.
   - `API_KEY` — must match the `API_KEY` you set in `.env`.
   - `KEY_TAG_PATHS` — your real Coater 1 tag paths.
3. Build the Perspective views per
   [ignition/perspective/README.md](../ignition/perspective/README.md).

### 7. End-to-end test

Open the ChatView in a Perspective session and ask:

> "What's the current state of Coater 1?"

You should see a response that:
- Cites live tag values with numbered citations
- Includes a CONFIDENCE: line at the end
- Renders feedback buttons

Click 👍 or 👎 — verify the audit log:

```
docker compose exec postgres psql -U chatbot -d ignition_chatbot \
  -c "SELECT event_type, entity_id, details FROM audit_log ORDER BY created_at DESC LIMIT 5;"
```

## Production hardening checklist

- [ ] Run PostgreSQL on durable storage (not the default Docker volume)
- [ ] Put the FastAPI service behind a reverse proxy with TLS (e.g. nginx)
- [ ] Use long, randomly generated secrets in `.env` — store via a secret manager
- [ ] Rotate `API_KEY` periodically; update both `.env` and `ai/config.py`
- [ ] Restrict network access: only the Ignition gateway should reach the
      FastAPI service; only the FastAPI service and DB tools should reach
      PostgreSQL
- [ ] Configure a backup schedule for the `ignition_chatbot` database
- [ ] Send container logs to a central log store (Loki, ELK, etc.)
- [ ] Set retention on `audit_log` (12 months hot, archive older)
- [ ] Restrict Perspective views by role (operator/engineer/admin)
