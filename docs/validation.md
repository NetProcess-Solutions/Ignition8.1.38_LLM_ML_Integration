# Validation Guide

Once the stack is up and seeded, run through these checks to confirm the
MVP grounding behaves as designed.

## 1. Insufficient-evidence short-circuit

With **no documents ingested** and **no key tags supplied**, send:

```bash
curl -s -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -X POST http://localhost:8000/api/chat -d '{
    "query": "Why is Coater 1 down?",
    "session_id": "test-session",
    "user_id": "tester",
    "line_id": "coater1",
    "live_context": {
      "snapshot_time": "2026-04-22T14:00:00Z",
      "line_id": "coater1",
      "key_tags": [], "tag_summaries": [], "deviations": [],
      "active_alarms": [], "recipe": null,
      "historian_window_minutes": 60
    }
  }'
```

Expect: `confidence == "insufficient_evidence"`, response includes the
phrase "I don't have enough evidence", and **no LLM call was made** (verify
in audit log: `details->>'short_circuit' = 'insufficient_evidence'`).

## 2. Curated-context schema enforcement

Send a `live_context` with an unknown field, e.g. `"foo": "bar"`. Expect
HTTP 422. This proves Ignition cannot accidentally forward raw historian
dumps.

## 3. Citation enforcement

Ingest a single test maintenance report mentioning Zone 3. Send a query
about Zone 3. Verify:
- The response cites at least one source by `[N]`
- The `sources` array contains only citations the LLM actually used
- The audit `context_snapshot.all_citations_offered` contains everything
  that was offered (typically more than what was cited)

## 4. Confidence downgrade if no citations

Temporarily replace `service/config/prompts/system_prompt_v1.txt` with a
weaker prompt that doesn't enforce citations, re-seed, and ask a question.
The response parser should:
- Detect zero `[N]` references
- Append the "[NOTE: ... did not include source citations ...]" warning
- Downgrade `confidence=confirmed` to `hypothesis`

(Restore the original prompt after testing.)

## 5. Feedback flow + chunk quality boost

1. Ask a question that retrieves several chunks
2. Submit `signal_type=usefulness, signal_value=positive` on the response
3. Query: `SELECT * FROM chunk_quality_signals;` — verify rows exist for
   the cited chunk_ids with `quality_score > 0`
4. Ask the same question again — verify those chunks rank slightly higher
   in `messages.retrieval_scores`

## 6. Correction flow + memory challenge

1. Seed a memory entry that says something specific (e.g. "Zone 3 always
   overshoots after element replacement")
2. Ask a question that retrieves it
3. Submit a correction with `correction_type=wrong_root_cause`
4. Repeat steps 2-3 three times
5. Query: `SELECT id, status, challenge_count FROM line_memory;` — the
   memory should now have `status='challenged'`

## 7. Rule-match outranks retrieval

1. Configure your test environment so the rule
   `zone3_overshoot_high_speed` would match (e.g. submit a `live_context`
   with `ZoneTemp3=438` and `LineSpeed=262`)
2. Ask "Is there anything I should worry about right now?"
3. Verify the response surfaces the rule's conclusion (zone 3 + line speed
   delam risk) prominently and cites it

## 8. Audit completeness

For any chat message in the `messages` table:

```sql
SELECT
  jsonb_pretty(context_snapshot) AS ctx,
  jsonb_pretty(retrieval_scores) AS retr,
  jsonb_pretty(rules_matched)    AS rules,
  jsonb_pretty(memories_used)    AS mems,
  prompt_version, model_name, latency_breakdown
FROM messages WHERE role='assistant' ORDER BY created_at DESC LIMIT 1;
```

Verify every field is populated and the response could be reconstructed
from the snapshot alone.

## 9. Personalization is presentation-only

1. Set `response_detail_level='brief'` on a test user profile
2. Ask a question that has clear safety implications (e.g. trigger the
   `high_vibration_drive` critical rule)
3. Verify the response is shorter, but the critical warning still appears
   and citations are still present
