# Coater 1 Intelligent Operations Advisor — OT/IT Briefing

**One-page leave-behind.** Use with the system boundary diagram.

---

## What this is

A **read-only, grounded operational decision-support system** embedded in
Ignition Perspective for Coater 1. It answers operator and engineer
questions using live tag data, historian aggregates, alarms, recipe and
shift context, SOPs, work orders, MOC packets, line memory, and
deterministic process analytics. Every answer is cited, confidence-labeled,
and fully auditable.

> **Framing:** what we built is a *discovery methodology* — a working
> end-to-end prototype that answered what evidence the system needs, what
> guardrails it needs, and where the boundary belongs. We have OT ways of
> operationalizing this. **We are asking IT for the approved ways to
> operationalize the data plane, the agentic harness, the model endpoint,
> and the tool layer so we can align.**

---

## Three planes (not two zones)

```
┌────────────────────┐    ┌──────────────────────────┐    ┌───────────────────────┐
│  IGNITION (OT)     │    │  AGENTIC HARNESS (IT)    │    │  DATA PLANE (IT)      │
│  read-only         │───►│  orchestration only      │◄──►│  enterprise standard  │
│  - tags / history  │    │  - anchor parsing        │    │  - relational tables  │
│  - alarms          │    │  - phase routing         │    │  - vector search      │
│  - curated context │    │  - RCA chain control     │    │  - ML features        │
│  - identity        │    │  - tool selection (RAG)  │    │  - audit / messages   │
│  - ChatView        │    │  - response validation   │    │  - line memory        │
└────────────────────┘    └────────┬─────────────────┘    └───────────────────────┘
                                   │ calls
                                   ▼
                          ┌──────────────────────────┐    ┌───────────────────────┐
                          │  MCP TOOL SERVERS        │    │  MODEL ENDPOINT       │
                          │  deterministic functions │    │  governed, swappable  │
                          │  - percentile_of         │    └───────────────────────┘
                          │  - compare_distribution  │
                          │  - detect_drift          │
                          │  - nearest_runs          │
                          │  - change_ledger         │
                          │  - rules_eval            │
                          │  - anomaly_check         │
                          └──────────────────────────┘
```

**Two-flavor script split:**
1. **Standard functions/methods** → wrapped as **MCP tool servers**, hosted
   centrally so other Shaw projects can reuse them.
2. **Orchestration / order of operations** → **agentic harness**.

---

## Boundary contract (non-negotiable)

- **OT → Service:** curated, schema-validated context package + query +
  user/session/line ID. HTTPS, API key + per-user signed token, short TTL.
- **Service → OT:** answer + cited sources + confidence label + message ID +
  processing time. No prompt internals, no model name, no uncited content.
- **No reverse path.** No PLC handle, no setpoint API, no alarm-ack API.
  Structurally absent, not toggled off.

---

## Honesty mechanisms

- Curated context only — schema rejects unknown fields.
- Refusal-first — empty retrieval ⇒ no model call.
- Mandatory numbered citations; uncited claims stripped.
- Deterministic math (percentiles, drift, distributions) — model reasons
  about numbers, can't invent them.
- Two-step RCA chain with hard tool-call budget.
- Confidence labels: CONFIRMED / LIKELY / HYPOTHESIS / INSUFFICIENT_EVIDENCE.
- Hash-chained, append-only audit enforced at the database layer.
- Self-grading: every "likely"/"confirmed" RCA graded against actual
  outcome 24 h later. Daily precision view per failure mode.
- Operator feedback never auto-promotes; engineer-reviewed memory candidates.

---

## Capability map — what changes per Shaw stack, what doesn't

| Capability                       | Stays / changes               | Notes                                    |
|----------------------------------|-------------------------------|------------------------------------------|
| Read-only plant context          | **Stays in Ignition**         | Native gateway APIs, Jython              |
| OT→service transport             | Same pattern                  | HTTPS + key + signed token               |
| Agentic harness host             | Approved app host             | Databricks Apps / Azure App Svc / on-prem|
| Deterministic tool layer         | **MCP server(s)**             | Centrally hosted, reusable               |
| Relational + vector + ML feature store | Enterprise platform     | Likely **Databricks** (Delta + Vector)   |
| Embeddings                       | Enterprise endpoint           | Databricks / Azure OpenAI                |
| LLM endpoint                     | Provider-agnostic adapter     | Azure OpenAI / Databricks Serving / on-prem |
| Identity / per-user attribution  | Enterprise SSO                | Entra ID / AD                            |
| Secrets                          | Enterprise vault              | Key Vault / CyberArk                     |
| Audit                            | Immutable + ship to SIEM      | DB-enforced + Splunk/Sentinel            |
| Scheduled jobs                   | Approved scheduler            | Databricks Jobs / Functions / CronJobs   |
| Document ingestion               | Approved pipeline             | Connectors to SharePoint / WO database   |
| Network boundary                 | Existing OT/IT segmentation   | Outbound-only HTTPS from OT              |

**What does not change across stacks:** the boundary, the three-phase
request lifecycle, refusal-first behavior, citation enforcement,
audit immutability.

---

## What we are deliberately deferring

- Memory architecture (Mem0 / Letta methodology choices) — separate
  conversation, not in tomorrow's ask.
- Symphony video integration — schema in place, adapter is a stub.
- Predictive ML models — architecturally provisioned, post-MVP.

---

## The ask, in one sentence

> Tell us which Shaw-approved platform fulfills each capability above, and
> we will rebuild against it. The architecture is the contract; the
> implementations are negotiable.
