# Revision History & How To Read This Document

## Document Lineage

| Version | Date         | Author        | Status                          | Summary                                                                   |
|--------:|--------------|---------------|---------------------------------|---------------------------------------------------------------------------|
|     1.0 | January 2026 | Jordan Taylor | Superseded                      | Initial proposal: schema sketch, RAG pipeline, conversation logging.      |
|     2.0 | April 2026   | Jordan Taylor | Superseded by v3.0              | 59-page design specification with 29-table schema and anchor-conditional context assembly. |
| **3.0** | **April 23, 2026** | **Jordan Taylor** | **Current — As-Built**     | **This document. Reflects shipped code at 145 passing tests / 0 failing.** |

Version 2.0 was an *aspirational design document*. It described the system the way
it should be built. Version 3.0 — this document — is the *as-built reference*. It
describes the system the way it actually exists in source control today. Where
v2.0 said "we will," v3.0 says "we did," "we did differently," or "we deferred."

## Why a New Document Instead of Patching v2.0

The shipped MVP added six structural capabilities that v2.0 did not contemplate
in detail:

1. **Hybrid retrieval** (vector + BM25 trigram, fused via Reciprocal Rank Fusion,
   diversified with MMR, conditionally boosted by failure-mode and equipment
   metadata). v2.0 specified pgvector cosine retrieval only.
2. **Deterministic tool layer** (`services/tools.py`) with five typed read-only
   tools the LLM can call to ground its hypotheses in distributional facts —
   percentile, distribution comparison, nearest historical runs, drift detection,
   defect-events-in-window.
3. **Two-step RCA reasoning chain** (`services/rca.py`) that replaces one-shot
   RAG when the query has causal intent against a past event, with a hard tool-call
   budget and a TTL-cached step-1 hypothesis set.
4. **Distributional grounding** (`services/percentiles.py`) using Page-Hinkley
   CUSUM for drift detection and empirical CDF lookups scoped by
   (style, front_step, equipment, recipe).
5. **Multivariate Mahalanobis anomaly detection**
   (`services/anomaly.py`) on live tag snapshots vs. fitted per-cluster history.
6. **Change ledger** (`services/change_ledger.py`) that surfaces "what changed
   since baseline" deltas (tag sigma, recipe drift, crew/shift, equipment WO)
   as a labeled evidence section before the LLM call.

Patching v2.0 in place would have buried these as parenthetical addenda. They
deserve their own chapters (§6, §7, §8, §15) and they materially change the
shape of §11's end-to-end walkthrough.

## How This Document Is Organized

Eighteen chapters plus an appendix. The first eleven chapters mirror the
v2.0 structure so a reader familiar with the original can see deltas in
context. Chapters 12–18 are largely new and document operations,
implementation reality, and the updated phased roadmap.

Each chapter ends with a coloured `Δ vs v2.0` callout box. The box has
three sub-blocks:

- **Stayed** — what matched the v2.0 design, verbatim or close to it.
- **Changed** — what diverged from the v2.0 design, with the reason.
- **Considering** — what is on the table for a future iteration but not
  in scope today.

Chapter 17 (*Implementation Reality*) consolidates every Δ into one place
for readers who want the deltas without the surrounding prose.

## Authoritative Source for Every Claim

This document is generated from the source repository at the commit in the
footer. Every shipped behavior cited here is backed by code I can point to;
deferred work is labeled <span class="status-deferred">DEFERRED</span> or
<span class="status-stub">STUB</span>; future work is labeled
<span class="status-considering">CONSIDERING</span>. Claims that refer to
the original design without an implementation are explicitly tagged so the
reader is never asked to take an unsupported assertion on faith.

A spot-check policy was applied during authoring: every code reference in
this document was verified against the actual file before publication. If
you find a discrepancy, the source code wins; this document is wrong and
should be regenerated.

## Audience

- **Engineers** continuing the project should read 3 (architecture), 5 (schema),
  6–8 (retrieval, tools/RCA, anomaly), 11 (end-to-end walkthrough), and 17
  (implementation reality).
- **Operators and shift supervisors** should read the executive summary and
  10 (role-based personalization) — those are the chapters that explain
  what the chatbot will and will not do for them.
- **Reviewers and auditors** should read 4 (anti-hallucination), 9
  (feedback-learning), 14 (security/audit/compliance), and the test catalog
  in the appendix.
- **Future contributors evaluating new ML or model-hosting decisions** should
  start with 17 (implementation reality) and 18 (phased roadmap) before
  diving into the technical chapters.

## Notation

- File references use repo-relative paths in `code font`, e.g.
  `service/services/rag.py` — open the file from the repo root.
- Database tables use `lower_snake_case`, e.g. `messages`, `defect_events`.
- Class and function names use the repo's actual casing, e.g.
  `CuratedContextPackage`, `handle_chat`.
- Settings keys are referenced as they appear in `service/config/settings.py`
  (e.g. `retrieval_mmr_lambda`, `rca_max_total_tool_calls`).
- A `[T-N]` annotation references the original v2.0 task (Task 1 through
  Task 11). The mapping from those eleven tasks to actually-executed
  sprints lives in chapter 12.

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Document structure</p>
<p><span class="label">Stayed:</span> Eighteen chapters preserve the topical
shape of v2.0 plus an appendix.</p>
<p><span class="label">Changed:</span> Five new chapters added (Retrieval Layer,
Tool Layer &amp; RCA Chain, Distributional Grounding &amp; Anomaly, Tag
Selection &amp; Gateway Integration, Implementation Reality). Walk-through
and build-plan chapters rewritten against shipped code.</p>
<p><span class="label">Considering:</span> A "Performance &amp; Scaling"
chapter once we have one shift of real production traffic to characterize.</p>
</div>
