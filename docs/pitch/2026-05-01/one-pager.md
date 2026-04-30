# Coater 1 Intelligent Operations Advisor — One-Pager

**For:** Shaw IT Directors + Engineers · **Meeting:** 2026-05-01 · **From:** Jordan Taylor, Plant 4

---

## Three Planes Outside Ignition

```
┌────────────────────┐    ┌──────────────────────────┐    ┌───────────────────────┐
│  IGNITION (OT)     │    │  AGENTIC HARNESS (IT)    │    │  DATA PLANE (IT)      │
│  read-only         │───►│  orchestration only      │◄──►│  Databricks (Shaw std)│
│  - tags / history  │    │  - anchor parsing        │    │  - Delta tables       │
│  - alarms          │    │  - phase routing         │    │  - Vector Search      │
│  - curated context │    │  - RCA chain control     │    │  - feature store      │
│  - identity (JWT)  │    │  - tool selection / RAG  │    │  - immutable audit    │
│  - Perspective UI  │    │  - response validation   │    │  - line memory        │
└────────────────────┘    └────────┬─────────────────┘    └───────────────────────┘
                                   │ calls
                                   ▼
                          ┌──────────────────────────┐    ┌───────────────────────┐
                          │  MCP TOOL SERVERS        │    │  MODEL ENDPOINT       │
                          │  (forward-looking)       │    │  governed, swappable  │
                          │  deterministic, read-only│    └───────────────────────┘
                          └──────────────────────────┘
```

---

## Capability Map (the two decisions and the rest)

| Plane                 | Today (prototype)                                | Operationalization target                                  | Decision today? |
|-----------------------|--------------------------------------------------|------------------------------------------------------------|:---------------:|
| **Agentic harness**   | Stateless FastAPI, three-phase lifecycle         | **Shaw-approved app host** (~4 GB / 2 vCPU, container)     | **Yes — Ask 1** |
| **Data plane**        | Local Postgres 16 + pgvector, 30 tables          | **Databricks** — Delta + Vector Search + feature store     | **Yes — Ask 2** |
| **MCP tool layer**    | In-process Python, 5 deterministic tools         | MCP server host(s), stdio or HTTP/SSE, read-only creds      | No — later      |
| **Model endpoint**    | Provider-agnostic adapter, OpenAI-compatible     | Shaw-governed (Databricks Serving / Azure OpenAI / on-prem) | Note in passing |
| **Identity / SSO**    | Gateway-issued HS256 JWT, TTL ≤ 120 s            | Enterprise SSO drop-in (Entra ID / AD)                      | Note in passing |
| **OT (Ignition)**     | Native gateway, read-only, no PLC writes         | No change                                                   | Already done    |

---

## Boundary Contract (non-negotiable)

- **OT → Service:** curated, schema-validated context package + query +
  user/session/line ID. HTTPS, X-API-Key + per-user HMAC JWT (TTL ≤ 120 s).
  Pydantic `extra="forbid"` rejects unknown fields.
- **Service → OT:** answer + cited sources + confidence label + message ID +
  processing time. **No** prompt internals, **no** model name, **no** uncited
  content.
- **No reverse path.** No PLC handle, no setpoint API, no alarm-ack API.
  Structurally absent, not toggled off.
- **Audit immutability** enforced at the DB layer (trigger blocks
  UPDATE/DELETE). SHA-256 hash chain across audit rows. Every response is
  fully reconstructible from `messages.context_snapshot`.

---

## Closing Ask

> The architecture is the contract. The implementations are negotiable.
>
> **Decision 1 (today):** Which Shaw-approved host fulfills the agentic
> harness plane, and who owns the kickoff?
>
> **Decision 2 (today):** Who owns the Databricks data-plane build-out, and
> on what timeline does it land?
>
> **Decision 3 (later):** The MCP tool-server layer is a 6-month
> conversation, not today's. We name it now so it isn't a surprise then.
>
> Tell us the names attached to Decisions 1 and 2 and we start the rebuild.
