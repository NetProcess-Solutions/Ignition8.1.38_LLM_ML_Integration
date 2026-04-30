# Coater 1 Intelligent Operations Advisor — Next-Steps Document

**Companion to the one-pager and the 2026-05-01 meeting packet.**
**Audience:** Shaw IT directors and engineers who want depth after the room.
**Author:** Jordan Taylor, Process Engineer, Plant 4.

This document is the post-meeting reference. It expands on every claim made
in the room. It is organized so a director can read sections 1, 5, 6, 8 and
have the picture; an engineer can read sections 2, 3, 4, 7 and have the
substrate.

---

## 1. The Architectural Contract

Three commitments are load-bearing. They do not change across hosting
choices, platform migrations, or model changes. Everything else is
negotiable.

### 1.1 Boundary

The system has a single, structurally-enforced boundary between Ignition (OT)
and the FastAPI service (IT). The contract is documented in
[`docs/system_boundary.md`](../../system_boundary.md) §4.

- The **only** payload crossing OT → IT is a `CuratedContextPackage` plus
  query and identity. The schema rejects unknown fields
  (Pydantic `extra="forbid"`). Raw historian dumps cannot reach the prompt.
- The **only** payload crossing IT → OT is a structured response (answer,
  cited sources, confidence label, message ID, processing time). No prompt
  internals, no model name, no uncited content.
- **There is no reverse path.** No PLC handle, no setpoint API, no
  alarm-ack API exists in the IT-side code. Read-only is not a toggle —
  there is nothing to toggle.

### 1.2 Read-Only

Read-only is enforced at three layers:

1. **Code:** there is no write client, no PLC handle, no setpoint method
   anywhere in the FastAPI service.
2. **Network:** OT → IT is HTTPS outbound from Ignition. IT → OT does not
   exist; the service does not call back into the gateway.
3. **Identity:** the per-user JWT issued by the Ignition gateway carries
   read-only scope claims. The service refuses any control verb in user
   intent (anchor parser, Phase 1).

### 1.3 Audit-Immutable

- `audit_log` table append-only by **DB-layer trigger**
  (`audit_log_immutable`). UPDATE and DELETE are blocked at the platform
  level, not at the application level.
- **SHA-256 hash chain** across audit rows: each row hashes its predecessor.
  Tamper detection is structural.
- **Full reconstructibility:** every assistant response can be reconstructed
  from a single `messages.context_snapshot` row plus the corresponding
  `audit_log` entry. The exact evidence offered to the model — not just what
  it cited — is retained.
- **SIEM-egressable** from day one.

These three commitments survive the migration to Shaw infrastructure
unchanged. The platforms underneath them change; the contracts do not.

---

## 2. The Agentic Harness, in Detail

The harness is the orchestration brain. It is **stateless**, runs as a
standard Python web service, and holds **no DB session during LLM calls**.
That last property is what makes it cheap to host and safe to scale
horizontally.

### 2.1 Three-Phase Request Lifecycle

Implemented in [`service/services/rag.py`](../../../service/services/rag.py).

```
handle_chat(curated_context, query, user)
  ├── Phase 1 — pre-LLM (owns DB session)
  │     ├── parse_anchor(query)             ← in-process, cheap, no IO
  │     ├── if control verb        → refuse + audit + return
  │     ├── if ambiguous           → clarify + audit + return
  │     ├── retrieval              → hybrid_retrieve (vector + BM25 + RRF + MMR)
  │     ├── change_ledger          → what changed since baseline
  │     ├── anomaly_check          → multivariate Mahalanobis on tag block
  │     ├── rules_eval             → declarative YAML rules
  │     ├── line_memory_lookup     → approved memory entries by similarity
  │     └── if insufficient        → templated refusal + audit + return
  │           (no LLM call)
  │
  ├── Phase 2 — LLM (NO DB session held)
  │     ├── if anchor=past_event with causal intent → RCA chain
  │     │     ├── step 1: hypothesise (LLM, no tools)
  │     │     └── step 2: adjudicate (LLM + tools, bounded budget)
  │     │     budget: shared 15-call ceiling, 5-min step-1 cache
  │     ├── else → single-shot RAG with tool budget
  │     └── response_parser
  │           ├── enforce numbered citations
  │           ├── strip uncited claims
  │           └── apply confidence label (or downgrade-on-no-citation)
  │
  └── Phase 3 — persist (fresh DB session)
        ├── data_plane.write_message(snapshot)
        ├── data_plane.append_audit(hash_chain)
        └── data_plane.intake_feedback_hooks()
```

The DB-session discipline is deliberate. Phase 2 is the long phase (model
latency + tool calls). Holding a DB session across that window is the most
common failure mode in chat services at scale. The harness avoids it
structurally.

### 2.2 Tool Budget Enforcement

Every Phase-2 path runs under a hard tool-call ceiling. The RCA chain shares
a 15-call budget across both steps; the single-shot path has its own ceiling.
Budgets are enforced inside `_run_tool_loop` in
[`service/services/llm.py`](../../../service/services/llm.py). The model
cannot bleed cost or latency by looping on tool calls.

### 2.3 Response Parser

[`service/services/response_parser.py`](../../../service/services/response_parser.py).

- Extracts numbered citations from model output.
- Strips claims that lack a citation.
- Computes the final confidence label
  (`high` / `medium` / `low` / `insufficient_evidence`).
- **Downgrade-on-no-citation:** if the model produced no citations, the
  confidence is forced to `insufficient_evidence` regardless of what the
  model claimed.

### 2.4 RCA Chain

[`service/services/rca.py`](../../../service/services/rca.py).

- Two-step: hypothesise then adjudicate.
- Step 1: pure model reasoning, no tool access. Generates candidate causes.
- Step 2: model + bounded tool access. Validates candidates against the data
  plane.
- 5-minute step-1 cache: identical anchors within 5 minutes reuse the same
  hypothesis set. Reduces cost on rapid-fire operator questions about the
  same event.

### 2.5 Why a Separate Host

The harness is the only component that:
- Needs Python 3.11 + the FastAPI/Pydantic v2/asyncpg stack.
- Calls the model endpoint (HTTPS outbound).
- Calls the data plane (HTTPS or asyncpg outbound).
- Needs to be horizontally scalable.

The Ignition gateway cannot host it (Jython 2.7, no modern ML stack). The
data plane should not host it (separation of concerns; the data plane is
a substrate, not a runtime). It needs its own home.

**Resource shape per instance:** ~4 GB RAM, 2 vCPU. Stateless, so scaling is
adding instances behind a load balancer. Container-friendly. Outbound HTTPS
only — no inbound from outside the Plant 4 network.

---

## 3. The Data Plane, in Detail

The data plane is the substrate for everything the harness needs to retain
across requests: the corpus, the vectors, the messages, the audit log, the
ML feature snapshots, the line memory, and the operational reference data.

### 3.1 Today's Implementation

PostgreSQL 16 + pgvector. **30 tables across 9 schema groups**, documented
in [`docs/data_model.md`](../../data_model.md).

The 9 groups (summary):
1. **Conversations & messages** — `conversations`, `messages`,
   `message_feedback`. Monthly partitioning on high-volume tables.
2. **Audit** — `audit_log`, append-only with DB-layer trigger and SHA-256
   hash chain.
3. **Document corpus** — `documents`, `document_chunks`,
   `chunk_quality_signals`. pgvector ivfflat/hnsw indexes for retrieval.
4. **Line memory** — `line_memory`, `memory_challenges`,
   `memory_review_queue`. Engineer-reviewed; no auto-promotion.
5. **Rules** — `business_rules` with versioning.
6. **Reference data** — `tag_registry` (SCAFFOLD; see §7), recipe metadata,
   crew rosters, equipment registry.
7. **Outcomes & corrections** — `outcome_linkages`, `user_corrections`.
8. **ML feature snapshots** — for anomaly model refit; periodic.
9. **Failure-mode history** — `defect_events`, classifier outputs,
   precision tracking.

### 3.2 Vector + Keyword Retrieval

`hybrid_retrieve` blends:
- **Vector search** via pgvector (ivfflat or hnsw depending on table size).
- **BM25-style keyword search** via PostgreSQL trigram + ts_vector indexes.
- **RRF (Reciprocal Rank Fusion)** to merge the two ranked lists.
- **MMR (Maximal Marginal Relevance)** to diversify the top-k.

Scoring is then nudged ±30% (bounded) by `chunk_quality_signals` derived from
operator feedback. The bound prevents feedback-loop drift.

### 3.3 Failure-Mode-Matched History

For RCA Step 2, the harness asks for "nearest historical runs by failure
fingerprint." The data plane indexes defect events by failure-mode classifier
output and supports k-nearest queries scoped by recipe and equipment.

### 3.4 Why Databricks Fits

Shaw is standardized on Databricks. The mapping is direct and natural —
**this is operationalization, not migration onto an unfamiliar stack:**

| Today (PostgreSQL + pgvector)         | Databricks                          |
|---------------------------------------|-------------------------------------|
| Relational tables (groups 1, 4–9)     | Delta tables                        |
| pgvector embedding columns + ivfflat  | Databricks Vector Search            |
| Trigram / BM25 keyword search         | Vector Search + Delta full-text, or hybrid via SQL |
| ML feature snapshots (group 8)        | Databricks ML feature store         |
| Append-only audit + DB trigger        | Delta append-only + Unity Catalog ACLs + SIEM egress |
| Monthly partitioning                  | Delta partitioning + OPTIMIZE/VACUUM |
| Scheduled jobs (outcome closure, anomaly refit, ingestion) | Databricks Jobs |

**The data-plane adapter** ([`service/db/data_plane.py`](../../../service/db/data_plane.py))
is the abstraction seam. Today it has one implementation
(`PostgresDataPlane`). Adding `DatabricksDataPlane` is an additive change
behind the same Protocol — the harness does not change.

If the chosen Databricks topology splits OLTP-style writes from analytical
storage (e.g., a small relational front for `messages` writes, Delta for
everything else), the adapter handles the fan-out. The harness does not
know.

### 3.5 What the Data Plane Must Support, Regardless of Platform

This is the contract — anything that satisfies these requirements is a valid
data-plane implementation:

1. Relational tables matching `data_model.md`.
2. k-NN vector similarity over chunk and memory embeddings.
3. Trigram or BM25-style keyword search.
4. Append-only audit table with platform-enforced UPDATE/DELETE block.
5. Partitioning + retention on high-volume tables (`messages`, `audit_log`,
   `feature_snapshots`).
6. Scheduled job runtime.

Databricks satisfies all six. That is why Ask 2 is "timeline + ownership,"
not "is Databricks the right home."

---

## 4. The MCP Tool Layer, Hypothetically

This is **forward-looking**. It is not a decision today. It is named in the
meeting so it is not a surprise in 6 months.

### 4.1 What Today's Tools Do

Today, in-process Python module
([`service/services/tools.py`](../../../service/services/tools.py)):

| Tool                          | What it computes                                         |
|-------------------------------|----------------------------------------------------------|
| `percentile_of`               | Empirical CDF lookup for a tag value, scoped by recipe   |
| `compare_to_distribution`     | Percentile + nearest runs + outcome summary              |
| `nearest_historical_runs`     | k nearest production runs by failure fingerprint         |
| `detect_drift`                | Page-Hinkley CUSUM drift check on a tag's recent series  |
| `defect_events_in_window`     | Bounded read of defect events with grouping              |
| `change_ledger`               | What changed since baseline (recipe / crew / equipment)  |
| `anomaly_check`               | Multivariate Mahalanobis check on the curated tag block  |
| `rules_eval`                  | Evaluate declarative YAML rules against a tag snapshot   |
| `hybrid_retrieve`             | Vector + BM25 + RRF + MMR over the document corpus       |
| `memory_search`               | Read approved line-memory entries by similarity          |

All ten are deterministic, read-only, schema-typed.

### 4.2 Why MCP Servers Make Them Shaw-Reusable

Wrapped as MCP servers, each tool inherits:

- **Discoverability** — any model client enumerates the tool list and JSON
  schemas.
- **Cross-project reuse** — Project N+1 (any other Shaw AI initiative) can
  call `percentile_of` against the historical distribution without
  re-implementing it.
- **Independent versioning** — bumping a tool's schema is a server release,
  not a harness release.
- **Independent observability** — each tool server emits its own metrics.
- **Independent governance** — Shaw can audit, allowlist, or sandbox each
  tool server separately.

### 4.3 Hosting Topology, Whenever the Time Comes

Three valid options, all the harness needs is the URL(s) and credentials:

1. One MCP server per tool (extreme separation; high ops overhead).
2. One MCP server per *family* of tools (likely the right balance).
3. One MCP server hosting all tools (simplest; least separation).

Transport: stdio (local development) or HTTP/SSE (hosted deployment).

The scaffold already exists at
[`service/mcp_server/`](../../../service/mcp_server/). The split is real, not
a slide.

### 4.4 Why It's Not Today's Ask

Operationalizing the harness on a Shaw host and the data plane on Databricks
delivers the entire pilot value. MCP-ifying the tool layer delivers
*reusability* value, which only matters once Project N+1 is in scope. Pulling
that decision forward couples three platform conversations into one and
delays Decisions 1 and 2.

---

## 5. The Migration Path

| Phase | Duration  | Action                                                                | Reversibility                |
|-------|-----------|-----------------------------------------------------------------------|------------------------------|
| 0     | now       | Prototype: collapsed FastAPI + local Postgres + Docker Compose        | n/a                          |
| 1     | 4–6 weeks | Harness deployed to Shaw-approved app host; data plane still local Postgres in approved environment | Reversible until cutover     |
| 2     | 4–8 weeks | `DatabricksDataPlane` adapter implemented; data plane cut over to Databricks; Postgres decommissioned | Reversible until cutover     |
| 3     | later     | MCP tool layer split out to its own server(s); architecture-council conversation | Reversible (in-process fallback exists) |

**Reversibility is preserved at each phase.** The data-plane adapter is the
seam for Phase 2; the in-process tool implementations remain available as
fallback for Phase 3.

**Destructive operations are minimal and well-bounded:**
- Decommissioning the local Postgres is destructive only after Databricks
  parity is verified by the eval harness ([`service/eval/harness.py`](../../../service/eval/harness.py)).
- No prompt-history loss (audit log migrates row-for-row).
- No model-output behavior changes (provider-agnostic adapter).

---

## 6. Anticipated Questions

See the meeting-packet section F for the full pre-rehearsed Q&A. The
director-facing questions and the engineer-facing questions are distinguished
there. This document does not duplicate them; the meeting packet is the
authoritative source.

---

## 7. What's Deferred and Why

These items are documented as deferred so they don't surprise an engineer
reading the codebase post-meeting. **None of them change the hosting
shape** of Asks 1 or 2.

- **B2 / B5 / B6 / B11 backlog items** — deferred behind pilot measurement.
  These are operational refinements (see TDD §17 / implementation reality)
  whose priority will be set by the precision/recall numbers from
  `v_rca_precision_daily`. Not gating.
- **Symphony video integration** — schema is in place; the adapter is a
  documented stub. See `ignition/perspective/B11_SYMPHONY_TODO.md`. When
  Symphony is wired, it becomes another curated-context source; no harness
  or data-plane shape change.
- **`tag_registry` SCAFFOLD** — table exists; the canonical population path
  is the next sprint's work. Today's queries fall back to direct tag-path
  references in `curated_context`.
- **Memory architecture choices** (Mem0 / Letta methodology) — separate
  conversation; line memory works today via the engineer-review workflow.
- **Predictive ML models** — architecturally provisioned in
  `feature_snapshots`; post-MVP scope.

These exist because **honest deferral is a credibility move**. Hiding them
would be a tell.

---

## 8. The Boundary Contract Restated

In one paragraph for the director who skipped to the end:

> The Coater 1 advisor is read-only with respect to plant control. There is
> no path from the AI service back into PLCs or process tags. The only
> data crossing OT → IT is a curated, schema-validated context package; the
> only data crossing IT → OT is a structured, citation-enforced response.
> Every response is fully auditable from a single database row. The audit
> log is append-only at the database-platform layer, with a SHA-256 hash
> chain across rows. These properties survive any choice of hosting,
> platform, or model. **They are the contract you are agreeing to host.**

---

## 9. Reference Documents

For deeper context, in order of relevance:

- [`docs/system_boundary.md`](../../system_boundary.md) — boundary contract.
- [`docs/THREE_PLANE_ARCHITECTURE.md`](../../THREE_PLANE_ARCHITECTURE.md) —
  operationalization target.
- [`docs/architecture.md`](../../architecture.md) — canonical short
  reference.
- [`docs/BRIEFING_HANDOUT.md`](../../BRIEFING_HANDOUT.md) — capability map
  and prior leave-behind.
- [`docs/data_model.md`](../../data_model.md) — the 30-table schema.
- [`docs/TDD_v3.0.md`](../../TDD_v3.0.md) — full technical design,
  especially §3 (architecture), §7 (tools), §14 (security/audit/compliance),
  §17 (implementation reality).
