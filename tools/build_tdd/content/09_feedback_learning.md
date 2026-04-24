# 9. Feedback & Learning Loop

The system gets better at answering coater 1 questions over time, in
five distinct, bounded ways. This chapter documents the feedback
substrate, the learning paths it drives, the bounds on each path, and
the precision dashboard that exposes the system's own track record to
the operators relying on it.

The substrate lives in five tables (chapter 5 §5.8). The intake
endpoints are
[service/routers/feedback.py](service/routers/feedback.py),
[service/routers/corrections.py](service/routers/corrections.py),
and [service/routers/outcomes.py](service/routers/outcomes.py). The
re-rank consumer lives in
[service/services/retrieval.py](service/services/retrieval.py)
(chapter 6 §6.7); the outcome closure job in
[service/services/outcome_closure.py](service/services/outcome_closure.py).

## 9.1 The Ten Feedback Signal Types

Operators can submit ten distinct signal types via the feedback API.
The signal type drives both how the signal is consumed and how it
weights into re-ranking and reporting.

| Signal type            | Operator action                                    | Consumer |
|------------------------|----------------------------------------------------|----------|
| `helpful`              | 👍 on the response                                 | Chunk re-rank (positive) |
| `unhelpful`            | 👎 on the response                                 | Chunk re-rank (negative) |
| `wrong_anchor`         | "this isn't about the event I meant"               | Anchor classifier review queue |
| `wrong_failure_mode`   | "this isn't a delam, it's a sag"                   | FM classifier review queue |
| `wrong_citation`       | "[3] doesn't say that"                             | Per-chunk demotion + LLM-prompt audit |
| `missed_evidence`      | "you should have shown me WO-88214"                | Memory-candidate proposal |
| `actionable`           | "this told me what to do"                          | Reporting only |
| `not_actionable`       | "this told me what but not what to do"            | Reporting only; flags engagement-posture mis-tuning |
| `confirmed_outcome`    | "the cause it identified was right"                | Outcome closure (positive) |
| `refuted_outcome`      | "the cause was wrong, the real cause was X"        | Outcome closure (negative) + memory-candidate |

The 10-value enum is wider than v2.0's three-value (👍/👎/refute). The
additional codes are what make the re-ranker meaningful and what makes
the precision dashboard a useful trust signal rather than a popularity
contest.

## 9.2 The Four Learning Flows

### Flow 1 — Bounded chunk re-ranking

The most direct loop. `helpful`, `unhelpful`, `wrong_citation`,
`confirmed_outcome`, and `refuted_outcome` signals update
`chunk_quality_signals` row counters. The retrieval re-rank stage
(chapter 6 §6.7) applies a multiplier:

```
quality_multiplier = 1 + clamp(
    (helpful − unhelpful) · 0.05
  + (cited_in_correct − cited_in_incorrect) · 0.10,
    −0.30, +0.30
)
```

The clamp at ±30% is non-negotiable. A single bad rating cannot bury
a useful chunk. A coordinated brigade of 100 bad ratings cannot bury
a useful chunk either — it just floors at −30%. The clamp protects
the system from noisy operator feedback, accidental misclicks, and
adversarial gaming.

### Flow 2 — Memory-candidate intake

`missed_evidence` and `refuted_outcome` signals create a row in
`memory_candidates`. Engineers review candidates via the Perspective
admin panel; on approval they become `line_memory` rows with `status =
'approved'`. Approved memory entries get a 1.5× boost in retrieval
scoring and are explicitly rendered as section J of the prompt.

The flow is **strictly human-in-the-loop**: the LLM never promotes a
memory candidate on its own. Operator → engineer → approval. This is
the slowest learning path but the most durable; it is also the only
path that ever inserts new content into the corpus.

### Flow 3 — Memory challenge

If three independent operators submit `wrong_citation` or
`refuted_outcome` against the same `line_memory` entry, the entry's
status flips to `challenged`. Challenged entries are excluded from
retrieval until an engineer reviews and either restores or deprecates
them. This is the safety valve against stale or obsoleted memory —
process knowledge that was true in 2024 may be false post-rebuild in
2026.

### Flow 4 — Outcome closure (B10)

`outcome_closure` runs nightly, sweeping `messages` from the last
24 h with `confidence_label IN ('confirmed', 'likely_contributor')`
and joining each to the `defect_events` and `quality_results` that
followed. Each match populates an `outcome_linkages` row with one of:

- `confirmed` — the cause the assistant identified was confirmed by
  subsequent investigation
- `partial` — the cause was a contributor but not the root cause
- `refuted` — the cause was wrong; a different cause was confirmed
- `inconclusive` — investigation did not reach a conclusion within
  the closure window (24 h default, configurable)

The materialized view `v_rca_precision_daily` (chapter 5 §5.10)
aggregates these. `precision_strict = confirmed / (confirmed + refuted)`;
`precision_lenient = (confirmed + partial) / (confirmed + partial + refuted)`.

The precision dashboard is the **honesty mechanism** that lets the
operators trust the system. If precision drops, the system loses
trust mechanically — the operator-facing UI surfaces the dashboard so
people can decide for themselves how much weight to give a `LIKELY
CONTRIBUTOR` label this month vs. last.

## 9.3 The Closure Endpoints

[service/routers/outcomes.py](service/routers/outcomes.py) exposes:

- `GET /api/outcomes/pending_followups` — assistant turns awaiting
  closure (engineer review queue)
- `POST /api/outcomes/{message_id}` — engineer files a structured
  outcome (`outcome_type`, optional `outcome_event_id`, narrative)
- `GET /api/outcomes/precision?window=30d&line=coater1` — read the
  precision rollup directly

The precision endpoint feeds the Perspective trust panel and is also
the basis of the per-prompt-version A/B analysis described in §9.6.

## 9.4 The Correction Path

`POST /api/corrections` is for explicit operator corrections — not the
fast +/- thumbs but the slower "the answer was wrong, here is what it
should have said." Stored in `user_corrections`:

```json
{
  "correction_id": "...",
  "message_id": "...",
  "correction_type": "wrong_citation | wrong_failure_mode | wrong_anchor | wrong_recommendation",
  "before": "the assistant said: zone 3 element drift",
  "after": "actual: tillitson roller calibration; root cause was confirmed via WO-89001",
  "engineer_reviewed": false,
  "applied_to_memory": false
}
```

Corrections do not directly modify the corpus. They flow into the
engineer review queue. On review, the engineer can:

1. Approve as a memory candidate (creates `line_memory` row)
2. Mark as a one-off (no further action)
3. File as a prompt-tuning datum (added to the eval harness corpus
   for B13 — see chapter 16)

The `applied_to_memory` flag is set true when path (1) is taken.

## 9.5 What Is Deliberately NOT Auto-Updated

The system does not, by design, do any of the following without
engineer review:

- Insert new chunks into `document_chunks`
- Modify existing chunk text
- Change failure-mode classifications on past defects
- Promote memory candidates to active memory
- Update `business_rules`
- Update prompts in `prompt_versions`

All six are gated behind explicit engineer action. The principle is
**bounded, reversible, slow** — fast feedback loops affect ranking,
slow human review affects content. The two never blend.

This is also why the precision dashboard matters: it is the
forward-looking signal that tells engineers when a slower change is
warranted (a new memory entry, a prompt-version bump, a chunk re-tag)
rather than the system silently degrading or "improving" on its own.

## 9.6 Per-Prompt-Version A/B Analysis

Because every `messages` row carries `prompt_version`, and every
outcome rolls up by message, the system can answer "did
`system_prompt_v3` improve precision over `system_prompt_v2`?" with
a one-line SQL:

```sql
SELECT prompt_version,
       count(*) AS n,
       avg(case when ol.outcome_type = 'confirmed' then 1.0 else 0.0 end) AS confirmed_rate,
       avg(case when ol.outcome_type = 'refuted'   then 1.0 else 0.0 end) AS refuted_rate
FROM messages m
LEFT JOIN outcome_linkages ol USING (message_id)
WHERE m.role = 'assistant'
  AND m.created_at >= now() - interval '30 days'
GROUP BY prompt_version
ORDER BY confirmed_rate DESC;
```

This is the substrate the B13 evaluation harness will eventually
automate, but the manual-SQL path works today and has been used to
sanity-check `system_prompt_v2` against the deprecated v1.

## 9.7 Settings Reference

| Setting                              | Default      | Effect |
|--------------------------------------|--------------|--------|
| `feedback_re_rank_help_weight`       | 0.05         | Per-helpful vote weight |
| `feedback_re_rank_outcome_weight`    | 0.10         | Per-correct citation weight |
| `feedback_re_rank_clamp`             | 0.30         | ±30% bound (non-negotiable) |
| `outcome_closure_enabled`            | `true`       | Master toggle |
| `outcome_closure_window_hours`       | 24           | Sweep window |
| `outcome_closure_cron`               | `0 4 * * *`  | Nightly at 04:00 UTC |
| `memory_challenge_threshold`         | 3            | Independent challenges before flip |
| `memory_approved_boost`              | 1.5          | Retrieval multiplier on approved memory |

<div class="delta-box">
<p class="delta-title">Δ vs v2.0 — Feedback & Learning</p>
<p><span class="label">Stayed:</span> The architectural commitment to
operator-driven learning. The substrate tables (memory_candidates,
line_memory, message_feedback, user_corrections, outcome_linkages) are
all v2.0-spec.</p>
<p><span class="label">Changed:</span> The 10-signal enum (was 3 in
v2.0) — the additional codes are what make ranking and reporting
useful. Bounded ±30% chunk re-rank with explicit clamp. Outcome
closure scaffolding with materialized view <code>v_rca_precision_daily</code>.
Memory challenge threshold (3 independent operators flip status to
challenged). Per-prompt-version A/B analysis is now a one-line SQL
because <code>messages.prompt_version</code> is populated.</p>
<p><span class="label">Considering:</span> Active-learning trainer (B11
proper) — currently scaffolded; consumer of the signals exists in
retrieval.py but the scheduled "look for retraining-worthy patterns"
job is not built. Auto-promotion of high-confidence memory candidates
(would require careful guardrails and explicit operator opt-in).
Per-operator personalization weights (some users want more terse
responses, some want longer narratives — currently global).
Anonymous reciprocal-comparison surveys ("which of these two responses
do you prefer?") to feed RLHF-style ranking data.</p>
</div>
