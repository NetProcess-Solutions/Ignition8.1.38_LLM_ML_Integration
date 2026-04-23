# API Spec

Base URL: `http://<service-host>:8000`

All endpoints except `/api/health` require the `X-API-Key` header matching
`API_KEY` in the service environment.

---

## `GET /api/health`

No auth. Returns `{status, database, embedding_model, llm_provider, version}`.

---

## `POST /api/chat`

Request body — see [`service/models/schemas.py`](../service/models/schemas.py) `ChatRequest`:

```json
{
  "query": "Why is Coater 1 running slow?",
  "session_id": "perspective-session-uuid",
  "user_id": "jtaylor",
  "line_id": "coater1",
  "live_context": {
    "snapshot_time": "2026-04-22T14:35:00Z",
    "line_id": "coater1",
    "key_tags":      [...],
    "tag_summaries": [...],
    "deviations":    [...],
    "active_alarms": [...],
    "recipe":        {...},
    "historian_window_minutes": 60
  },
  "conversation_id": null
}
```

The `live_context` is a `CuratedContextPackage`. The schema is
**`extra="forbid"`** — any unexpected field causes a 422. Adding a field
requires updating both Ignition's `ai/context.py` and `service/models/schemas.py`.

Response:

```json
{
  "message_id": "uuid",
  "conversation_id": "uuid",
  "response": "...",
  "sources": [
    { "id": "1", "type": "live_tag", "title": "ZoneTemp3", "excerpt": "438 F", ... }
  ],
  "confidence": "likely",
  "context_summary": {
    "key_tags": 10, "documents": 3, "events": 2,
    "rules_matched": 1, "memories_used": 2, "total_citations": 18
  },
  "processing_time_ms": 3210,
  "prompt_version": "v1",
  "model_name": "gpt-4o-mini-2024-07-18"
}
```

---

## `POST /api/feedback`

```json
{
  "message_id": "uuid",
  "user_id": "jtaylor",
  "signal_type": "usefulness",
  "signal_value": "positive",
  "comment": null
}
```

`signal_type` is one of:
`usefulness`, `correctness`, `completeness`, `source_relevance`,
`root_cause_confirmed`, `root_cause_rejected`,
`recommendation_acted_on`, `recommendation_ignored`,
`recommendation_helped`, `recommendation_did_not_help`.

`signal_value`: `positive` | `negative` | `neutral`.

For `usefulness`/`source_relevance`/`correctness`, also updates
`chunk_quality_signals` for any document chunks cited in that message
(bounded `quality_score` ∈ [-0.5, 0.5]).

---

## `POST /api/corrections`

```json
{
  "message_id": "uuid",
  "user_id": "jtaylor",
  "correction_type": "wrong_root_cause",
  "original_claim": "...",
  "corrected_claim": "...",
  "supporting_evidence": "..."
}
```

Side effects:
- Inserts `user_corrections` with `status='submitted'`
- Increments `challenge_count` on every `line_memory` referenced by the
  message; if a memory crosses the threshold (`challenge_count >= 3`), its
  status is moved from `approved` to `challenged` for engineer re-review

---

## `POST /api/outcomes`

```json
{
  "message_id": "uuid",
  "outcome_type": "quality_result",
  "outcome_id": "uuid",
  "outcome_table": "quality_results",
  "alignment": "confirmed",
  "linked_by": "jtaylor",
  "notes": "..."
}
```

Validates that the message and the referenced outcome row both exist before
inserting `outcome_linkages`.
