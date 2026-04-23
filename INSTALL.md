# Coater 1 Intelligent Operations Advisor - Install Guide

> A complete, no-assumptions, click-by-click installation guide. Read in order. Do not skip.

---

## Part 0 - What you are building

You are installing 3 things that talk to each other:

1. **PostgreSQL** - a database that stores documents, events, conversations, audit logs, etc. (lives in Docker).
2. **AI Service** - a Python FastAPI app that does retrieval, prompts the LLM, and audits everything (lives in Docker).
3. **Ignition Gateway scripts + Perspective view** - the operator-facing chat embedded in your existing Ignition project.

Operator types a question -> Ignition gathers live tags -> sends to AI Service -> AI Service retrieves grounding evidence + asks LLM -> answer + citations come back -> Ignition shows it.

---

## Part 1 - Things you need BEFORE you start

Get these ready first or you will hit a wall halfway through.

### 1.1 - On the AI Service host (the Linux/Windows box that will run Docker)

| Item | Where to get it | Notes |
|------|-----------------|-------|
| Docker Desktop (Windows) OR Docker Engine + Docker Compose (Linux) | https://www.docker.com/products/docker-desktop | Confirm with `docker --version` and `docker compose version` |
| Git | https://git-scm.com/downloads | Confirm with `git --version` |
| ~10 GB free disk | n/a | Postgres data + HuggingFace model cache |
| Open inbound TCP port 8000 | Your firewall / IT | Ignition must be able to reach this |

### 1.2 - LLM API key

You need ONE of these. Pick one and put the key somewhere safe:

| Provider | Where to get | Env var you'll set later |
|----------|--------------|--------------------------|
| OpenAI (default) | https://platform.openai.com -> API Keys -> Create new secret key | `OPENAI_API_KEY` |
| Azure OpenAI | Azure portal -> your OpenAI resource -> Keys and Endpoint | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` |

Cost note: with `gpt-4o-mini` (the default) a typical answer costs less than $0.01.

### 1.3 - Two random strings you generate yourself

Generate them now and save them to a notepad file. You will paste them in later.

```powershell
# Run twice. Save each output. Label them clearly.
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

| Label | Used for |
|-------|----------|
| `API_KEY` | The shared secret between Ignition and the AI Service |
| `POSTGRES_PASSWORD` | The Postgres database password |

### 1.4 - From your Ignition Gateway

Open the Ignition Gateway web page (usually `http://<gateway-ip>:8088`). Log in as an admin. Find and write down:

| Item | Where to find in Ignition | Save as |
|------|---------------------------|---------|
| Gateway URL | the URL in your browser's address bar | `IGNITION_BASE_URL` |
| Tag provider name | Status -> Tag Providers (column "Name", e.g. `default` or `UnifiedNamespace`) | `TAG_PROVIDER` |
| Coater 1 tag root path | Browse the tag tree in Designer; copy the folder path that contains your Coater 1 tags | `COATER1_ROOT` |
| Postgres DB connection name (gateway-side) | Config -> Databases -> Connections (you'll create this in Part 4) | `PG_DB_CONNECTION` |
| Work Order DB connection URL (optional, Phase 4 only) | Whatever DB your existing WO system uses | `IGNITION_WO_DB_URL` |

### 1.5 - The repository

```powershell
cd C:\Users\<you>
git clone <your-repo-url> IgnitionChatbot
cd IgnitionChatbot
```

If you already have it (you do), just `cd C:\Users\jtaylo6\IgnitionChatbot`.

---

## Part 2 - Configure the AI Service

### 2.1 - Create the `.env` file

In the **repo root** (`C:\Users\jtaylo6\IgnitionChatbot`), create a file literally named `.env` (no extension, leading dot). Paste this template, then fill in the marked spots with the values you saved in Part 1:

```env
# --- Database (matches docker-compose.yml) ---
POSTGRES_DB=ignition_chatbot
POSTGRES_USER=chatbot
POSTGRES_PASSWORD=PASTE_THE_RANDOM_STRING_FROM_1.3_HERE

# --- Service ---
SERVICE_ENV=production
SERVICE_LOG_LEVEL=INFO
API_KEY=PASTE_THE_OTHER_RANDOM_STRING_FROM_1.3_HERE

# --- LLM (pick ONE block, leave the other blank) ---
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-PASTE_YOUR_OPENAI_KEY
OPENAI_MODEL=gpt-4o-mini
OPENAI_TEMPERATURE=0.1
OPENAI_MAX_TOKENS=1500

# AZURE_OPENAI_ENDPOINT=
# AZURE_OPENAI_API_KEY=
# AZURE_OPENAI_DEPLOYMENT=
# AZURE_OPENAI_API_VERSION=2024-08-01-preview

# --- Embeddings (defaults are fine; do not change unless you know why) ---
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
EMBEDDING_DIMENSION=384

# --- Retrieval tuning (defaults are fine) ---
RETRIEVAL_TOP_K=10
RETRIEVAL_MIN_SCORE=0.30
RETRIEVAL_RECENT_EVENTS_HOURS=72
MEMORY_TOP_K=5

# --- v2.0 nightly integrations (LEAVE OFF for first boot; turn on later in Part 8) ---
SCHEDULER_ENABLED=false
WO_SYNC_ENABLED=false
IGNITION_WO_DB_URL=
SYMPHONY_BACKFILL_ENABLED=false
```

**Triple-check** that `API_KEY` and `POSTGRES_PASSWORD` are NOT the placeholder text.

### 2.2 - Build and start the containers

From the repo root, in PowerShell:

```powershell
docker compose up -d --build
```

This takes 5-15 minutes the first time (it downloads pgvector, Python, and the embedding model). Watch progress:

```powershell
docker compose logs -f ai-service
```

You're done with this step when you see a line like `embedding_model_loaded`. Press `Ctrl+C` to stop tailing logs (the containers keep running).

### 2.3 - Verify the containers are healthy

```powershell
docker compose ps
```

Both `ignition_chatbot_postgres` and `ignition_chatbot_service` should show **(healthy)**. If either says `(unhealthy)` or `Restarting`, jump to **Part 9 - Troubleshooting**.

### 2.4 - Confirm the database schema loaded

```powershell
docker compose exec postgres psql -U chatbot -d ignition_chatbot -c "\dt"
```

You should see ~29 tables listed (documents, document_chunks, defect_events, work_orders, line_memory, audit_log, tag_registry, etc.).

If you see only `audit_log` or nothing, the init scripts didn't run. Fix:

```powershell
docker compose down -v   # WARNING: wipes the DB. Only do this on a fresh install.
docker compose up -d --build
```

### 2.5 - Seed prompts, rules, and starter memory

```powershell
docker compose exec ai-service python -m scripts.seed_initial_data
```

You should see lines like:
```
+ prompt seeded: system_prompt v1 (active=False)
+ prompt seeded: system_prompt v2 (active=True)
+ rule seeded: zone3_overshoot_high_speed
+ rule seeded: oven_imbalance_zone1_zone2
...
```

### 2.6 - Smoke test the API

```powershell
curl http://localhost:8000/api/health
```

Expected: `{"status":"ok",...}`. If you get connection refused, the service didn't start - check `docker compose logs ai-service`.

Now test that the API key works:

```powershell
curl -X POST http://localhost:8000/api/chat `
  -H "Content-Type: application/json" `
  -H "X-API-Key: PASTE_YOUR_API_KEY_FROM_2.1" `
  -d '{\"query\":\"hello\",\"session_id\":\"test\",\"user_id\":\"test\",\"line_id\":\"coater1\",\"live_context\":{\"snapshot_time\":\"2026-04-23T12:00:00Z\",\"line_id\":\"coater1\",\"key_tags\":[],\"tag_summaries\":[],\"deviations\":[],\"active_alarms\":[]}}'
```

Expected: a JSON response with `confidence: "insufficient_evidence"` and a polite refusal explaining no grounding evidence is available. **That is success** - it means the pipeline ran end to end.

If you get `401 Unauthorized`, your `X-API-Key` header doesn't match `.env`'s `API_KEY`. Fix it.

**The AI Service is now installed.** Phase 1 done.

---

## Part 3 - Ingest your first documents

The chatbot needs grounding documents to give useful answers. Start with a small set so you can see the pipeline work end to end.

### 3.1 - Put files where the container can read them

```powershell
mkdir ingestion\sample_data\maintenance
# Drop a few .txt or .md files in there - any maintenance reports or
# shift handoffs you have. 5-10 files is plenty for the first test.
```

### 3.2 - Mount the folder into the container

The compose file already mounts `./service` to `/app`. The simplest path is to put files under `service/data/incoming/`:

```powershell
mkdir service\data\incoming\maintenance
copy ingestion\sample_data\maintenance\*.txt service\data\incoming\maintenance\
```

### 3.3 - Run the ingester

```powershell
docker compose exec ai-service python -m scripts.ingest `
    --source-type maintenance_report `
    --line-id coater1 `
    --path /app/data/incoming/maintenance/
```

Expected output: `+ ingested: <filename> -> N chunks` for each file.

### 3.4 - Verify the data landed

```powershell
docker compose exec postgres psql -U chatbot -d ignition_chatbot -c `
  "SELECT source_type, document_role, document_weight, COUNT(*) FROM documents GROUP BY 1,2,3;"
```

You should see your `maintenance_report` row with `document_role = maintenance_history` and `document_weight = 1.15`.

---

## Part 4 - Wire Ignition to Postgres (one-time)

Ignition needs a database connection so the discovery script can write to `tag_registry`.

### 4.1 - In the Ignition Gateway web UI

1. Log in as admin.
2. Click **Config** (left sidebar) -> **Databases** -> **Connections** -> **Create new Database Connection**.
3. Choose **PostgreSQL** as the driver.
4. Fill in:
   - **Name**: `ai_chatbot_pg` *(must match `PG_DB_CONNECTION` in the Ignition config; you can change either)*
   - **Connect URL**: `jdbc:postgresql://<HOST_IP_OF_AI_SERVER>:5432/ignition_chatbot`
     - Replace `<HOST_IP_OF_AI_SERVER>` with the IP of the box running Docker. NOT `localhost` - the Ignition Gateway is a different host.
   - **Username**: `chatbot`
   - **Password**: the `POSTGRES_PASSWORD` from your `.env`
5. Click **Create New Database Connection**.
6. The status should turn **Valid** within a few seconds. If it says **Faulted**, the most common causes are:
   - Firewall blocking port 5432 inbound on the AI host
   - The Postgres container only listens on localhost (it doesn't, by default in this compose file - port 5432 is mapped to all interfaces)
   - Wrong password

---

## Part 5 - Install the Ignition Gateway scripts

These are Jython 2.7 scripts that run inside the Ignition Gateway. They live under a Project Library package called `ai`.

### 5.1 - Open Designer

1. Launch Ignition Designer.
2. Open your Coater 1 project (or create a new one called `Coater1`).

### 5.2 - Create the `ai` script package

1. In the Project Browser, expand **Scripting** -> **Project Library**.
2. Right-click **Project Library** -> **New Package** -> name it `ai`.

### 5.3 - Add the four scripts

For each file under `ignition/scripts/` in the repo, create a matching script under `ai`:

| Repo file | Designer location | How |
|-----------|-------------------|-----|
| `ignition/scripts/config.py` | `ai.config` | Right-click `ai` -> New Script -> name it `config` -> paste contents |
| `ignition/scripts/context.py` | `ai.context` | Same pattern, name it `context` |
| `ignition/scripts/client.py` | `ai.client` | Same pattern, name it `client` |
| `ignition/scripts/selector.py` | `ai.selector` | Same pattern, name it `selector` |
| `ignition/scripts/discovery.py` | `ai.discovery` | Same pattern, name it `discovery` |

After pasting each one, **press Ctrl+S** in Designer.

### 5.4 - Edit `ai.config` for YOUR environment

Open `ai.config` in Designer. Change these lines to match your environment (use values you saved in Part 1):

```python
AI_SERVICE_URL = "http://<HOST_IP_OF_AI_SERVER>:8000"  # Same IP as Part 4
API_KEY        = "PASTE_THE_SAME_API_KEY_FROM_2.1_ENV"
IGNITION_BASE_URL = "http://<YOUR_GATEWAY_IP>:8088"
LINE_ID = "coater1"
TAG_PROVIDER = "[UnifiedNamespace]"   # whatever you wrote down in 1.4
COATER1_ROOT = TAG_PROVIDER + "Shaw/F0004/Coating/Coater1"  # your tag path
```

Add this line if it's not already there:
```python
PG_DB_CONNECTION = "ai_chatbot_pg"  # must match Part 4 step 4
```

**Save the project** (Ctrl+Shift+S, or File -> Save Project).

### 5.5 - Quick smoke test from the Designer Script Console

Open **Tools -> Script Console** in Designer. Paste:

```python
import ai.client as c
print(c.postJson("/api/health", {}))
```

Expected: `{'ok': True, 'status_code': 200, 'data': {...}}`. If you get `transport: ...` or `401`, fix `AI_SERVICE_URL` or `API_KEY` in `ai.config` and save.

### 5.6 - Run tag discovery once, manually

Still in the Script Console:

```python
import ai.discovery as d
d.runDiscovery()
```

You should see `discovery complete: NNN tags upserted in X.Xs` in the gateway log. Verify:

```powershell
docker compose exec postgres psql -U chatbot -d ignition_chatbot -c `
  "SELECT tag_class, COUNT(*) FROM tag_registry GROUP BY 1;"
```

You should see counts split across `setpoint_tracking`, `process_following`, `oscillating_controlled`, `discrete_state`.

### 5.7 - Schedule discovery to run nightly

1. **Config** -> **Gateway Events** -> **Timer Scripts** -> **Create new**.
2. Name: `ai_discovery_nightly`.
3. Delay: `86400000` (24 hours, in ms).
4. Threading: `Shared`.
5. Script:
   ```python
   import ai.discovery as d
   d.runDiscovery()
   ```
6. Save.

---

## Part 6 - Build the Perspective chat view

Follow [ignition/perspective/CHAT_VIEW_SPEC.md](ignition/perspective/CHAT_VIEW_SPEC.md) end-to-end. The short version:

1. In Designer: **Perspective** -> **Views** -> right-click -> **New View** -> name `Coater1Chat`.
2. Add the components listed in the spec (top bar, message list, source panel, clarification modal, footer).
3. Paste the `_sendQuery` custom method from the spec into `view.custom`.
4. Bind:
   - `view.session.userId` to `{[System]Client/User/Username}`
   - `view.session.lineId` to the constant `coater1`
   - `view.session.sessionId` to a view-load script: `view.session.sessionId = system.util.uuid()`
5. Embed the new view into your existing Coater 1 operator page wherever you want the chat panel.
6. Save the project.

### 6.1 - Test the full loop

1. Open the Perspective session in a browser.
2. Type: **"What's the line status right now?"**
3. You should get a grounded response within 2-5 seconds with a confidence label and at least one citation.

If the answer says "I don't have enough evidence" - that's still a working pipeline. Add more documents (Part 3) and retry.

---

## Part 7 - Test the seven query types

Per design section 8 task 10, run these against your Perspective view to confirm v2.0 behavior:

| # | Query type | Example | What to verify |
|---|-----------|---------|----------------|
| 1 | Current state | "What's wrong right now?" | Live tags shown, recent alarms cited |
| 2 | Past event (explicit) | "Why did R-20240601-03 fail?" | Live tag section says `[NOT APPLICABLE - past_event]` |
| 3 | Past event (relative) | "What happened yesterday morning?" | Anchor banner shows resolved date; clarification offered if ambiguous |
| 4 | Pattern | "How often does delamination happen on style S-1234?" | Failure-mode-matched history shown; no live state |
| 5 | Ambiguous | "delam" | Clarification modal appears with options |
| 6 | Control command | "Set Front2 to 195" | Refusal: "I can read context, not write to the line" |
| 7 | Out of corpus | "What's the weather?" | Refusal: out-of-scope |

For any answer, click the source panel and verify each numbered citation in the answer text matches a row.

---

## Part 8 - (Optional) Enable nightly integrations

Only do this AFTER Parts 1-7 are working.

### 8.1 - Work order sync

1. Get a read-only Postgres/MSSQL/MySQL user on your Ignition WO database.
2. Edit `.env` on the AI service host:
   ```env
   SCHEDULER_ENABLED=true
   WO_SYNC_ENABLED=true
   IGNITION_WO_DB_URL=postgresql+asyncpg://wo_user:secret@wo-host:5432/ignition_wo
   ```
3. Restart: `docker compose up -d ai-service`
4. Watch the next-day log for `wo_sync_complete`.
5. The query in `services/wo_sync.py:_fetch_recent_from_ignition` is for a generic `work_orders` table. If your WO schema differs, edit the SQL there.

### 8.2 - Symphony video clip backfill

1. Edit `.env`:
   ```env
   SCHEDULER_ENABLED=true
   SYMPHONY_BACKFILL_ENABLED=true
   ```
2. Restart `docker compose up -d ai-service`.
3. Currently `services/symphony_capture.py:_request_clip` returns a stub handle. Wire your real Symphony API call in that function (search the file for `# Plant-specific Symphony adapter`).

---

## Part 9 - Troubleshooting

### `docker compose up` fails with "port is already in use"
Something else is using port 5432 or 8000. Either stop that service or change the host port in `docker-compose.yml` (the LEFT side of `5432:5432`).

### `embedding_model_loaded` never appears
First boot downloads ~80 MB. If your network blocks HuggingFace, set up a mirror or pre-stage the model in `model_cache` volume.

### Ignition `postJson` returns `transport: Connection refused`
- AI Service is not reachable from the Gateway host. Test with `curl http://<AI_HOST_IP>:8000/api/health` from the Gateway machine.
- Firewall on the AI host blocking 8000 inbound.

### Ignition `postJson` returns `status_code: 401`
`API_KEY` in `ai.config` does not match `API_KEY` in `.env`. They must be byte-identical.

### `tag_registry` is empty after `runDiscovery()`
- Wrong `COATER1_ROOT` in `ai.config` (the script can't find your tags). Verify by browsing in Designer.
- Wrong `PG_DB_CONNECTION` name. It must match Part 4 step 4 exactly.
- Check the gateway logs: **Status -> Logs**, filter on `ai.discovery`.

### Every chat answer is "insufficient_evidence"
You haven't ingested any documents (Part 3) and there's no event data either. Either ingest more docs or run the system longer so events accumulate.

### Past-event queries still mention live tag values
The v2 prompt isn't active. Verify:
```sql
SELECT prompt_name, version, is_active FROM prompt_versions ORDER BY 1, 2;
```
The `system_prompt v2` row must show `is_active = TRUE`. If not, re-run `python -m scripts.seed_initial_data`.

---

## Part 10 - Ongoing operations

See [docs/runbook.md](docs/runbook.md) for daily/weekly checks, prompt updates, rule updates, API key rotation, and backups.

### Quick daily checks
```powershell
# Health
curl http://localhost:8000/api/health

# Errors in audit log (last 24h)
docker compose exec postgres psql -U chatbot -d ignition_chatbot -c `
  "SELECT event_type, details FROM audit_log WHERE details::text ILIKE '%error%' AND created_at > NOW() - INTERVAL '24 hours';"
```

### Backup
```powershell
docker compose exec postgres pg_dump -U chatbot ignition_chatbot | gzip > backup-$(Get-Date -Format yyyy-MM-dd).sql.gz
```

---

## Appendix A - All variables, in one table

| Variable | Where you set it | Where you find/generate it |
|----------|------------------|----------------------------|
| `POSTGRES_PASSWORD` | `.env` | You generate (Part 1.3) |
| `API_KEY` | `.env` AND `ignition/scripts/config.py` (`API_KEY`) | You generate (Part 1.3); MUST be identical in both places |
| `OPENAI_API_KEY` | `.env` | platform.openai.com -> API Keys |
| `OPENAI_MODEL` | `.env` (default `gpt-4o-mini`) | OpenAI model list |
| `AZURE_OPENAI_*` | `.env` (only if `LLM_PROVIDER=azure_openai`) | Azure portal -> your OpenAI resource -> Keys and Endpoint |
| `AI_SERVICE_URL` | `ignition/scripts/config.py` | `http://<host-running-docker>:8000` |
| `IGNITION_BASE_URL` | `ignition/scripts/config.py` | Your Gateway URL (Part 1.4) |
| `TAG_PROVIDER` | `ignition/scripts/config.py` | Status -> Tag Providers in the Gateway |
| `COATER1_ROOT` | `ignition/scripts/config.py` | Browse the tag tree in Designer |
| `LINE_ID` | `ignition/scripts/config.py` (default `coater1`) | Whatever ID you use everywhere; keep consistent |
| `PG_DB_CONNECTION` | `ignition/scripts/config.py` | Name of the DB connection from Part 4 (default `ai_chatbot_pg`) |
| `IGNITION_WO_DB_URL` | `.env` (Phase 4 only) | DBA / your existing WO system |

---

## Appendix B - Verification checklist

Before declaring "done":

- [ ] `docker compose ps` -> both containers healthy
- [ ] `\dt` in psql -> 29 tables present
- [ ] `seed_initial_data` ran -> v2 prompt active, rules and memory loaded
- [ ] `curl /api/health` -> 200 OK
- [ ] At least one document ingested -> non-zero rows in `documents`
- [ ] Ignition DB connection `ai_chatbot_pg` -> Valid
- [ ] All five `ai.*` scripts in Designer -> saved, no syntax errors
- [ ] `ai.discovery.runDiscovery()` -> tag_registry populated
- [ ] Nightly discovery timer script created
- [ ] Perspective `Coater1Chat` view -> embedded and renders
- [ ] Test query 1 (current state) -> grounded answer with citations
- [ ] Test query 2 (past event) -> live tags marked NOT APPLICABLE
- [ ] Test query 6 (control command) -> refused
- [ ] Backup script ran successfully once

When every box is checked, you are done.
