# IgnitionChatbot

Production-ready, line-specific AI assistant for Coater 1 in Ignition 8.1.

Read-only advisory system with strong RAG grounding, anti-hallucination
controls, structured feedback-learning, and an architecture that supports
future predictive ML.

See [docs/architecture.md](docs/architecture.md) for the full plan.

## Repo layout

```
IgnitionChatbot/
├── docker-compose.yml          # PostgreSQL + pgvector + FastAPI service
├── .env.example                # Copy to .env, fill in secrets
├── service/                    # FastAPI AI service (Python 3.11+)
├── ignition/                   # Ignition project resources
│   ├── scripts/                # Gateway library scripts (Jython 2.7)
│   ├── perspective/            # Perspective view JSON exports
│   └── tags/                   # Tag exports
├── ingestion/                  # Document parsers + ingest pipeline
├── ml/                         # ML training pipeline (Phase 4+)
├── scripts/                    # SQL setup, seeders, utilities
└── docs/                       # Architecture, data model, runbooks
```

## Quick start

1. Copy `.env.example` to `.env` and fill in `OPENAI_API_KEY` (or Azure
   equivalents) and a strong `API_KEY`.
2. Start the stack:
   ```
   docker compose up -d --build
   ```
3. Verify the service is up:
   ```
   curl http://localhost:8000/api/health
   ```
4. Ingest your initial data (see [docs/ingestion.md](docs/ingestion.md)).
5. Install Ignition gateway scripts (see [ignition/scripts/README.md](ignition/scripts/README.md)).
6. Import Perspective views (see [ignition/perspective/README.md](ignition/perspective/README.md)).

## Key design principles

- **Read-only.** No control writes to PLCs or process tags.
- **RAG-first.** Every answer cites real sources; insufficient evidence
  triggers explicit refusal, not guessing.
- **Curated context.** The LLM never sees raw historian dumps; it sees
  structured, pre-digested context with clear section delimiters.
- **Human-in-the-loop.** All memory and recommendations require engineer
  review before being treated as durable knowledge.
- **Auditable.** Every response logs the exact context used to produce it.

## Documentation

- [Architecture overview](docs/architecture.md)
- [Data model](docs/data_model.md)
- [API spec](docs/api_spec.md)
- [Deployment guide](docs/deployment.md)
- [Runbook](docs/runbook.md)
