# Shaw IT Meeting Packet — Coater 1 Intelligent Operations Advisor

**Date:** 2026-05-01
**Duration:** 45 minutes
**Audience:** Two IT directors + supporting IT engineers
**Posture:** **Decision today.** We leave the room with named owners on Ask 1
and Ask 2.
**Presenter:** Jordan Taylor, Process Engineer, Plant 4

---

## A. 90-Second Director Opener

> "I'm a process engineer at Plant 4. Over the last several months I've
> built and shipped a working prototype of a grounded operational AI
> advisor for Coater 1 — operator-facing chat, every response cited, every
> claim labeled by confidence, full audit log, read-only with respect to
> plant control. The OT side is in Ignition where it belongs. The
> outside-Ignition side is the conversation today.
>
> There are three planes in the target architecture: the **agentic
> harness**, which orchestrates everything; the **data plane**, which
> holds the relational, vector, and audit substrate; and a **deterministic
> tool layer** that we'd want to make Shaw-reusable later. I have specific
> asks against each.
>
> The architecture is documented. The boundary contract — what stays in
> Ignition, what crosses, and what never crosses back — is non-negotiable.
> Everything inside that contract is negotiable on platform.
>
> I'm here to walk through the three planes, name the two decisions I need
> today, and leave with owners attached to each. The third ask is
> forward-looking and is *not* a decision for this meeting."

---

## B. 45-Minute Meeting Structure

| #  | Block                                          | Min | Audience          | What happens |
|----|------------------------------------------------|-----|-------------------|--------------|
| 1  | Boundary contract + three-plane framing        | 10  | Directors-focused | System boundary diagram on screen. What stays in Ignition. What leaves Ignition. The three planes outside Ignition. Boundary contract as load-bearing commitment. |
| 2  | The three asks                                 | 15  | Both              | Ask 1: agentic harness host (priority, decision today). Ask 2: data plane onto Databricks — timeline + ownership (decision today). Ask 3: MCP server layer (forward-looking, not for decision today). |
| 3  | Governance, audit, security posture            | 7   | Directors-focused | Read-only enforced architecturally. Immutable audit log (DB-layer trigger, SHA-256 hash chain, full reconstructibility). Identity flow. Refusal-first. Mandatory citations + downgrade-on-no-citation. |
| 4  | Migration path and timeline                    | 7   | Both              | Today: collapsed for prototype velocity. Phase 1 (4–6 wks): harness on approved app host. Phase 2 (4–8 wks): data plane to Databricks. Phase 3 (later): MCP split. Each phase reversible until cutover. |
| 5  | Q&A + **named decision-owners**                | 6   | Both              | Names attached to harness-host decision. Names attached to data-plane ownership and timeline. Confirm follow-up cadence. |

**Total:** 45 minutes. The 6-minute Q&A buffer absorbs overruns.

**Close discipline:** the meeting does not end without (a) a named owner for
the harness-host decision and (b) a named owner for the Databricks data-plane
build-out, with a date for the kickoff conversation. If either is unresolved,
the closing action is to name *who names the owner*, with a date.

---

## C. Capability Mapping Table

Databricks is treated as the confirmed Shaw enterprise standard for the data
plane. The question is **timeline and ownership**, not platform fit.

| Plane                | Ask                              | What it is today                                                                                          | What it needs to operationalize                                                                                                  | Decision owner (to be named in room)         |
|----------------------|----------------------------------|-----------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------|
| **Agentic harness**  | **Ask 1 — primary, decision today** | Stateless FastAPI service, three-phase request lifecycle, no DB session held during LLM calls, provider-agnostic model adapter | Approved Shaw app host with outbound HTTPS to data plane + model endpoint. ~4 GB RAM, 2 vCPU per instance. Horizontally scalable. Container-friendly. | IT director(s) + app platform team           |
| **Data plane**       | **Ask 2 — Databricks, decision today**       | Local Postgres 16 + pgvector, 30 tables across 9 groups, ivfflat/hnsw retrieval, monthly partitioning on high-volume tables, append-only audit | Databricks workspace: Delta tables for relational, Databricks Vector Search for retrieval, ML feature store for snapshots, SIEM egress for audit. | Data-platform owner + IT director(s)         |
| **MCP tool layer**   | **Ask 3 — forward-looking, NOT for decision today** | Python module in-process. Five deterministic, read-only tools (`percentile_of`, `compare_to_distribution`, `nearest_historical_runs`, `detect_drift`, `defect_events_in_window`) plus orchestration helpers (`hybrid_retrieve`, `change_ledger`, `anomaly_check`, `rules_eval`, `memory_search`). | Eventually: MCP server host(s), reachable from harness via stdio or HTTP/SSE. Read-only data-plane credentials. Architecture-council conversation when the time comes. | Architecture council, future                 |
| **Model endpoint**   | Note in passing                  | Provider-agnostic adapter, OpenAI-compatible interface, no model name leaks to clients                    | Shaw-governed endpoint: Databricks Model Serving / Azure OpenAI / on-prem vLLM. Whatever model governance has approved.          | Model governance + IT security               |
| **Identity / SSO**   | Note in passing                  | Gateway-issued HS256 JWT, sub/role/scope claims, TTL ≤ 120 s, verified per request                        | Enterprise SSO drop-in (Entra ID / AD). Adapter swap, no harness rewrite.                                                        | IT security                                  |
| **OT (Ignition)**    | Already done                     | Native gateway integration, Jython 2.7, read-only, no PLC writes, no reverse path                          | No change. Existing Plant 4 gateway.                                                                                              | No change                                    |

**The architecture is the contract. The implementations behind it are
negotiable. The meeting is to align Asks 1 and 2 on Shaw-approved
implementations.**

---

## F. Anticipated Objections (with Pre-Rehearsed Responses)

### Director-facing

**Q: "What exactly are we hosting if we say yes to the harness?"**
A stateless Python service. ~4 GB RAM, 2 vCPU per instance. Horizontally
scalable. Outbound HTTPS only (to the data plane and the model endpoint).
**No inbound traffic from outside the Plant 4 network.** No PLC handles. No
control authority. Code is in source control and reviewable end-to-end.

**Q: "What's our compliance exposure if we host this?"**
The audit posture is built for it. DB-layer immutable audit log enforced by
trigger (no UPDATE/DELETE possible at the platform level). SHA-256 hash chain
across audit rows. Every response is fully reconstructible from a single
`messages.context_snapshot` row. ISO 9001 and 21 CFR Part 11 alignment is
documented in TDD §14.9. **Hosting decision inherits the existing posture; it
does not create new exposure.**

**Q: "Why Databricks for the data plane — couldn't we use what we already
have?"**
Databricks *is* what Shaw has standardized on. Ask 2 isn't "is Databricks the
right home" — it's "what's the timeline and who owns the build-out." The
data-plane adapter (`service/db/data_plane.py`) is the abstraction seam;
pointing the existing service at a Databricks-backed implementation
(Delta + Vector Search + feature store) is a deliberate migration, not a
rewrite of harness logic.

**Q: "What happens if we say no — or defer — on the harness host?"**
The system continues running on prototype infrastructure. **Every week of
pilot on prototype infrastructure is rework debt at migration time.** The
longer the pilot runs without an approved host, the more decisions accumulate
against the prototype rather than the production target. We don't lose the
pilot; we lose alignment compounding.

**Q: "What if we don't have an approved app host that fits today?"**
Then the conversation is which existing platform comes closest and what gaps
need closing. The requirements are modest — outbound HTTPS, ~4 GB RAM,
2 vCPU, container-friendly, horizontally scalable. Most enterprise app
platforms qualify (Databricks Apps, Azure App Service, OpenShift, internal
Kubernetes).

### Engineer-facing

**Q: "What's the harness actually doing that justifies a separate host?"**
Three-phase orchestration brain.
- **Phase 1 (pre-LLM):** anchor parsing, hybrid retrieval, change ledger,
  anomaly score, rules evaluation. Owns a DB session.
- **Phase 2 (LLM):** model call with bounded tool budget. **No DB session
  held during model calls.** RCA chain runs here when the anchor is
  `past_event` with causal intent — two-step hypothesise-and-adjudicate flow
  with shared 15-call tool budget and 5-minute step-1 cache.
- **Phase 3 (persist):** fresh DB session, write `messages.context_snapshot`,
  append `audit_log` row with hash chain.

**Q: "How does identity flow end-to-end?"**
Operator authenticates against Ignition Perspective (existing IdP). Gateway
issues HS256 JWT with `sub`/`role`/`scope` claims, TTL ≤ 120 s. Harness
verifies JWT every request via `require_attributed_user`. `user_id`
propagates to MCP tool calls so audit attribution is preserved end-to-end.
Enterprise SSO is a drop-in once Shaw names the IdP.

**Q: "Can we start with the data plane on Databricks before we lock the
harness host?"**
Yes. The data-plane adapter is the abstraction seam. Pointing the existing
service at a Databricks-backed data plane is a config change, not a rewrite.
The two asks are sequenceable in either order; we recommend harness-host
first only because it's the cheaper, faster decision.

**Q: "What's MCP actually buying us?"**
Reusability, version isolation, central governance. Today's tools are
deterministic, read-only, schema-typed Python functions — already
MCP-compatible with a thin wrapper. Project N+1 inherits them for free.
Versioning becomes a server release, not a harness release. Worth doing
eventually; **not the urgent ask today.**

**Q: "What's the audit substrate, concretely?"**
- `audit_log` table with append-only constraint enforced at DB layer
  (`audit_log_immutable` trigger blocks UPDATE/DELETE).
- SHA-256 hash chain: each audit row hashes its predecessor.
- Full reconstructibility of any past response from
  `messages.context_snapshot` — the exact evidence offered to the model is
  retained, not just what it cited.
- SIEM-egressable from day one.

**Q: "How was this built? What's the dev environment?"**
*[Use 80/20 answer; do not volunteer.]*
80%: Built with corporate tools where they fit — GitHub Copilot, Ignition,
Plant 4 infrastructure for everything plant-data-adjacent. Boundary contract
respected throughout: no plant data left Shaw infrastructure or the local
machine, read-only stance, no PLC writes.
20% (only if pressed): Some development on a personal machine via GitHub for
parallel work on schema design and prompt iteration. Postgres installed via
the binary distribution locally to bypass package-manager friction. Data
hygiene was airtight.

---

## G. Demo Decision

**Skip.** No live demo for this meeting.

Reasoning: directors won't extract value from a chat-window walkthrough;
engineers can read the TDD. The IP framing is stronger without a live demo
competing for attention, and a live LLM call introduces a failure surface
(latency, model availability, transient errors) that adds zero credibility
upside in a 45-minute decision meeting.

**Fallback if an engineer specifically asks to see something working:** walk
a single `messages.context_snapshot` row from a recent prototype session.
That demonstrates audit reconstructibility tangibly — the exact evidence,
the exact prompt sections, the exact tool calls, the cited sources, the
confidence label, the audit hash — without needing a live LLM call. ~2
minutes; recoverable to the agenda immediately.
