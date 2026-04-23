# Architecture Overview

This document is the canonical short reference. The full design rationale
lives in the planning conversation that produced this repo.

## Core principles

1. **Read-only.** No writes to PLCs or process tags.
2. **RAG-first grounding.** Every response cites real evidence; insufficient
   evidence triggers explicit refusal, not guessing.
3. **Curated context.** Ignition pre-aggregates live plant data into a
   structured `CuratedContextPackage` before sending it to the LLM. Raw
   historian dumps never reach the prompt.
4. **Human-in-the-loop.** Memory candidates and rule changes require
   engineer review.
5. **Auditable.** Every response logs the exact context used to produce it.

## Components

```
Ignition 8.1
├── Perspective ChatView
└── Gateway scripts (Jython 2.7)
    ├── ai.context  → builds CuratedContextPackage
    ├── ai.client   → HTTPS to FastAPI
    └── ai.config   → tag paths, secrets

         │  HTTPS, X-API-Key
         ▼
FastAPI service (Python 3.11)
├── /api/chat        → RAG orchestrator (retrieve + assemble + LLM + audit)
├── /api/feedback    → message_feedback + chunk quality signals
├── /api/corrections → user_corrections + memory challenge
├── /api/outcomes    → outcome_linkages
└── /api/health

         │  asyncpg
         ▼
PostgreSQL 16 + pgvector
└── 27 tables across 8 schema groups (see data_model.md)
```

## Anti-hallucination mechanisms in this codebase

| Mechanism | Location |
|-----------|----------|
| Curated context as the only ingress for plant data | [`service/models/schemas.py`](../service/models/schemas.py) `CuratedContextPackage` (`extra="forbid"`) |
| Section-delimited prompt with numbered citations | [`service/services/context_assembler.py`](../service/services/context_assembler.py) |
| System prompt mandates citations + confidence labels | [`service/config/prompts/system_prompt_v1.txt`](../service/config/prompts/system_prompt_v1.txt) |
| Insufficient-evidence short-circuit (no LLM call when nothing retrieved) | [`service/services/rag.py`](../service/services/rag.py) `is_evidence_insufficient` |
| Confidence parsing with fallback downgrade if no citations | [`service/services/response_parser.py`](../service/services/response_parser.py) |
| Filter response sources to only those the LLM actually cited | [`service/services/rag.py`](../service/services/rag.py) `extract_cited_ids` |
| Full audit trail of every query | `messages.context_snapshot` + `audit_log` table |
| Bounded retrieval re-ranking from feedback (max ±30%) | [`service/services/retrieval.py`](../service/services/retrieval.py) |
| Memory challenge mechanism (3 challenges → status="challenged") | [`service/routers/corrections.py`](../service/routers/corrections.py) |

## Data flow per chat query

1. User types in Perspective → `onActionPerformed` calls `ai.client.sendQuery`
2. `ai.context.buildCuratedContext` reads tags, queries historian aggregates,
   computes deviations vs window mean, queries active alarms, reads recipe
3. `ai.client.sendQuery` POSTs `CuratedContextPackage` + query + identity to
   `POST /api/chat`
4. FastAPI auto-provisions a `user_profile` if first time, opens or creates
   a `conversation`, persists the user `messages` row
5. Embedding model embeds the query
6. `retrieval.retrieve_chunks` runs pgvector cosine search on `document_chunks`,
   blends with `chunk_quality_signals` (bounded ±30%)
7. `retrieval.retrieve_recent_events` queries the structured event tables
8. `retrieval.retrieve_memories` finds approved/reviewed `line_memory` entries
9. `rules.evaluate_rules` runs `business_rules` against the curated tags
10. `context_assembler.assemble_prompt` builds 9 labeled sections; each item
    gets a numbered citation id
11. If everything came back empty → short-circuit response with
    `confidence=insufficient_evidence`, no LLM call, audit logged
12. Otherwise: LLM called with the active system prompt + assembled user block
13. Response parsed: confidence label extracted, citations extracted; if no
    citations were used, confidence is downgraded
14. Sources are filtered to those actually cited
15. Assistant message persisted with full `context_snapshot` (all evidence
    that was offered to the LLM, not just what it cited)
16. Audit row written with summary stats
17. Response returned to Ignition; UI renders message + feedback buttons

## What lives where

| Concern | Lives in | Why |
|--------|---------|-----|
| Live plant integration (tags, alarms, historian) | Ignition | Native APIs |
| User identity & session | Ignition (Perspective IdP) | Already integrated |
| HTTP client to AI service | Ignition gateway script | Reuses `system.net.httpClient` |
| Curated context construction | Ignition gateway script | Plant data is already there |
| AI/ML/embeddings/retrieval | FastAPI service | Jython 2.7 cannot run numpy/sklearn/sentence-transformers |
| Documents / events / memory | PostgreSQL | One DB, transactional, vector-capable |
| Audit log | PostgreSQL `audit_log` | Append-only |
| Prompt versions, rule versions | PostgreSQL | Versioned, queryable |
