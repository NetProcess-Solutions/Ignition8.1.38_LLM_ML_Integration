# Appendix D — Open Questions

Design decisions still genuinely outstanding at the v3.0 cut. None of
these are blockers for go-live; all are forward-looking. They are
recorded here so the maintainer two years from now sees the explicit
list rather than discovering them by archaeology.

## D.1 Retrieval

### Per-tag drift threshold tuning

`drift_threshold_sigma = 5.0` is the global default. A more honest
implementation would tune per-tag based on the historical noise floor.
**Open**: do we tune per-tag manually, or fit a per-tag threshold
nightly off the previous 90 days? The latter requires labeled drift
events to validate against.

### ivfflat → hnsw cutover automation

Migration 003 documents the cutover procedure, including measurement
of when to perform it. **Open**: should we wire automated detection
("rows > 250K AND p95 retrieval latency > 100ms"), or keep this as a
human decision? The argument for human: the cutover is a one-line
config change with brief downtime risk; not worth automating.

### Hybrid retrieval weight measurement

RRF weights vector and BM25 equally via the `1/(k+rank)` formula.
**Open**: should we measure per-query whether vector or BM25 is
contributing more, and re-weight? The literature is mixed; equal
weighting via RRF is a defensible default. Revisit if pilot
measurement shows recall is bound by one or the other.

### Step-back / HyDE hybrid

If we eventually build B5 (HyDE), should it be a preliminary tool
call (deterministic) rather than an internal LLM step (non-deterministic)?
A deterministic step-back would be more auditable; an LLM-driven
HyDE is more adaptive. **Open** — argument for both is real.

## D.2 LLM and tools

### Per-claim citation enforcement

Today: response-level validation that any `[N]` reference resolves to
a real chunk. **Open**: should we go further — validate that *every
factual claim* has a citation? The verifier needed (extracting claims
from prose) is itself an LLM call; this is precision vs cost. Likely
deferred until the eval harness can show it actually moves the
precision dashboard.

### Adversarial prompt-injection corpus

We document the prompt-injection mitigations (chapter 14). We do not
have a labeled corpus of attempted injections to test against.
**Open**: do we author one synthetically (LLM-generated attacks), or
collect from production traffic? The latter is more realistic but
slower to gather.

### Provider parity validation in CI

Tests assert provider parity at unit-test level. **Open**: should we
also run the integration suite against all three providers in CI? The
cost (cloud API calls) makes this unattractive; running against
`local` (vLLM) only is a defensible compromise.

### Tool budget per-query vs per-day

Today: per-query budget (15 calls). **Open**: should we also enforce
a per-user per-day budget to bound runaway-cost scenarios? The
absence has not bitten in pilot prep but is a risk for wider rollout.

## D.3 Distributional grounding

### Anomaly false-positive rate calibration

`anomaly_p95_threshold` is fit from baseline data. **Open**: what's
the acceptable false-positive rate, and how do we measure it in
production? This requires operator labeling of "this anomaly was
real" vs "this was noise." Probably needs a UI affordance in the
chat panel.

### Non-Gaussian feature handling

Mahalanobis assumes approximate Gaussianity. Several tags
(motor amperage, especially) have heavy-tailed distributions.
**Open**: do we transform these features (log, Box-Cox) at fit time?
The transformation must then be inverted for the top-K-attribution
output to be interpretable.

### Page-Hinkley vs CUSUM vs more recent change-point detectors

Page-Hinkley is venerable but limited. **Open**: is the marginal
detection performance of more recent change-point algorithms (BOCPD,
NEWMA) worth the implementation cost? Probably not without a
labeled drift corpus.

## D.4 Schema and storage

### `tag_registry` cutover playbook

The forward plan (chapter 15 §15.4) is documented but the cutover
playbook itself isn't written. **Open**: do we cut over while keeping
KEY_TAGS as a fallback, or is there an atomic switch?

### Embedding-model upgrade replay corpus

Migration approach: sibling-column backfill (chapter 13). **Open**:
the backfill cost grows linearly with corpus size. At what corpus
size do we re-embed in batches vs hold off?

### Outcome-linkages backfill on prompt-version switch

When we activate a new prompt version, the precision dashboard
restarts from zero. **Open**: do we also backfill the new prompt's
projected behavior on historical outcomes? That's only possible if
we re-run the LLM on historical queries, which is expensive.

## D.5 Operations

### Multi-instance HA

Today: single-VM Docker Compose. **Open**: at what concurrent-user
count does single-instance stop being sufficient? Likely the embedding-
provider call latency dominates well past 50 concurrent users. The
Postgres tier is also single-instance — adding a read replica is the
first step.

### Per-instance vs centralized rate limiting

`slowapi` rate limits are per-process. **Open**: when we go
multi-instance, do we use Redis-backed centralized rate limits?
Adds operational complexity; needed only at multi-VM scale.

### Backup and disaster recovery cadence

We rely on Postgres native backups. **Open**: what RPO / RTO are we
committing to? This is partly a business question, partly a
DBA-skills question for the deployment team.

### Logical-replication consumer for analytics

Today: analytics is via materialized views in the same Postgres.
**Open**: at what query-volume does the analytics workload need its
own replica? Very pilot-dependent.

## D.6 Phase 4 ML

### B13 labeled-corpus sourcing

Eval harness blocked on this. **Open**: who labels (operators,
engineers, both)? How many cases per failure mode? What's the gold
standard for "correct response"?

### Synthetic vs real correction-corpus for fine-tuning

Phase 4 §18.4.3 fine-tuning. **Open**: do we augment the real
correction corpus with synthetic-but-realistic LLM-generated cases?
Augmentation expands the dataset but risks teaching the model
artifacts.

### Cross-LLM ensembling

Considered for high-stakes RCA. **Open**: do we build an explicit
"second opinion" LLM call against a different provider, and surface
disagreements? Costs 2× per query in that path; only justifiable for
true safety incidents.

### Per-failure-mode predictive models

Phase 4 §18.4.2. **Open**: how do we surface model output to operators
without overloading them? The "scrap risk in next 30 min" surface
needs UX that doesn't induce alert fatigue.

### Distillation horizon

Phase 4 §18.4.4. **Open**: what's the minimum acceptable performance
gap between the distilled edge model and the cloud model for
production deployment? 90% of cloud quality at 10% of latency? The
exact tradeoff depends on which queries we route to which.

## D.7 RLHF / continuous learning

### Survey design for memory candidates

When the system surfaces a memory candidate to an engineer, what's
the UX? **Open**: full prose review, or thumbs-up/thumbs-down on a
distilled summary? The former is high-quality but creates engineer
workload; the latter is lower-friction but loses nuance.

### Memory expiration policy

Today: memories are flipped to `challenged` after 3 challenges. There
is no time-based expiration. **Open**: should we add "memory hasn't
been retrieved in 6 months → flag for re-review"?

### Personalization opt-out

Per chapter 10, personalization is substrate-shipped. **Open**: do
operators have an opt-out toggle? Privacy-by-default would say yes;
substrate today doesn't expose one.

## D.8 Governance

### Access-control granularity

Today: API-key + JWT, per-`user_id` rate limits. **Open**: do we add
per-user access-control rules ("operator X can only ask about line
N")? Substrate supports it; not exposed today.

### Audit-log retention beyond 24 months

`pg_partman` retention default is 24 months hot. **Open**: do we
archive older partitions to cold storage (S3) or drop? Compliance
posture (chapter 14) says archive; cost analysis required.

### External corpus inclusion

Today: corpus is internal documents only. **Open**: do we ever
include vendor manuals, supplier specifications, or industry-standard
references? Each requires a licensing review.

---

This list will be revisited at the end of the pilot. Any item moved
to "decided" gets recorded in a future TDD revision. Any item moved
to "deferred indefinitely" likewise.
