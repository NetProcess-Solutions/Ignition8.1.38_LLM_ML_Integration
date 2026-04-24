# Appendix C — Test Catalog

The 145 passing + 2 skipped tests in `service/tests/`, grouped by the
service area they cover. Each entry: file → brief purpose. See
chapter 16 for the testing strategy and mocked-vs-real boundary.

## C.1 Anchor resolution

[service/tests/test_anchor.py](service/tests/test_anchor.py)
- past-event anchor with QR-id resolves to single run
- past-event anchor with run-id resolves to single run
- past-event anchor with bare timestamp resolves with confirmation flag
- current-state anchor recognises "rn", "right now", "currently"
- pattern anchor when no specific time/run/event reference present
- ambiguity → clarification-first response, no retrieval performed

[service/tests/test_anchor_regression.py](service/tests/test_anchor_regression.py)
- regression suite of historical anchor parsing failures with frozen
  inputs/outputs

## C.2 Anomaly detection

[service/tests/test_anomaly.py](service/tests/test_anomaly.py)
- Mahalanobis baseline fits on synthetic correlated data
- ridge stabilization handles singular covariance
- p95 threshold computed correctly
- top-K contributing tags ranked by attribution magnitude
- sparse snapshot rejected (below `anomaly_feature_min_overlap`)
- re-fit cadence updates `ml_models.is_active` flag
- old baselines archived not deleted

## C.3 Change ledger

[service/tests/test_change_ledger.py](service/tests/test_change_ledger.py)
- TagDelta computed sigma-ranked against baseline
- top-K cap respected when more deltas than `change_ledger_max_tag_deltas`
- RecipeDelta surfaces setpoint changes between current and baseline run
- CrewDelta when shifts differ
- EquipmentChangeover when equipment_id mismatch
- empty ledger when no significant deltas (defensive against noise)

## C.4 Chunker

[service/tests/test_chunker.py](service/tests/test_chunker.py)
- text-only chunker respects token budget
- overlap policy applied between adjacent chunks
- chunk metadata propagated (doc_id, position, role)

[service/tests/test_chunker_structured.py](service/tests/test_chunker_structured.py)
- markdown headings preserved as chunk boundaries
- tables emitted as single chunks (don't split mid-table)
- bullet lists preserved as single chunks
- chunk_type column populated correctly per chunk variety

## C.5 Context assembler

[service/tests/test_context_assembler.py](service/tests/test_context_assembler.py)
- v1: 5 buckets assembled in canonical order
- past-event anchor includes failure-mode-matched history bucket
- current-state anchor excludes that bucket, includes recent-window
- pattern anchor includes neither, includes broad-corpus
- token budget enforced; over-budget chunks dropped from lowest-priority bucket first

[service/tests/test_context_assembler_v2.py](service/tests/test_context_assembler_v2.py)
- v2 layered assembly with role-weight clamps
- conditional inclusion based on anchor.failure_mode presence
- change-ledger section L appended when anomaly score above threshold
- outcome-history section M appended when outcome_linkages exist

## C.6 Deviation / drift

[service/tests/test_deviation.py](service/tests/test_deviation.py)
- Page-Hinkley CUSUM detects step change in synthetic series
- below `drift_delta_sigma` no alarm
- above `drift_threshold_sigma` raises alarm
- window respects `drift_window_days`

## C.7 Integrations (E2E with mocked LLM)

[service/tests/test_integrations_v2.py](service/tests/test_integrations_v2.py)
- end-to-end past-event causal chat with mocked LLM tool calls
- end-to-end current-state diagnostic chat
- citations present and resolve to real chunk IDs
- audit_log row written with hash chain extended
- `messages.tool_calls` populated with full trace
- refusal path on out-of-corpus query
- refusal path on control-command query

## C.8 LLM tool loop

[service/tests/test_llm_tool_loop.py](service/tests/test_llm_tool_loop.py)
- single tool call → result → response cycle
- multi-iteration tool loop respects `rca_step1_max_iters`
- tool budget exhaustion stops the loop and forces a response
- malformed tool-call JSON triggers re-prompt
- tool exception caught, surfaced as "tool failed" structured response
- provider parity: OpenAI / Azure / local produce equivalent loops

## C.9 Local LLM client

[service/tests/test_local_llm_client.py](service/tests/test_local_llm_client.py)
- chat completion against mocked OpenAI-compatible endpoint
- tool-calling parameter shaped per OpenAI spec
- streaming response handled (skipped: not enabled in v3.0)
- timeout honoured per `llm_request_timeout_seconds`

## C.10 Percentiles

[service/tests/test_percentiles.py](service/tests/test_percentiles.py)
- per-scope CDF computed correctly on synthetic distributions
- insufficient_samples flagged below `percentile_min_samples`
- TTL cache returns identical CDF within window
- TTL cache invalidates after window
- `compare_to_distribution` returns the right percentile + bucket label

## C.11 Prompt regression

[service/tests/test_prompt_regression.py](service/tests/test_prompt_regression.py)
- frozen system_prompt_v2 unchanged from baseline hash (catches accidental edits)
- per-prompt-version comparison harness logic
- A/B routing via `prompt_versions.is_active` respected

## C.12 RCA

[service/tests/test_rca.py](service/tests/test_rca.py)
- step 1 (hypothesise) produces ≤ `rca_max_hypotheses` hypotheses
- step 1 cache hit on identical anchor + prompt version
- step 1 cache miss on different prompt version
- step 2 (adjudicate) consumes step-1 hypotheses + tool results
- final confidence label assigned per the rules
- two-step trace persisted to `messages.rca_summary`

[service/tests/test_rca_e2e.py](service/tests/test_rca_e2e.py)
- end-to-end RCA path with mocked LLM and real tools
- tool budget shared across both steps respected
- step timeout enforced per `rca_step_timeout_seconds`

## C.13 Skipped tests (2)

- streaming-response test in `test_local_llm_client.py` —
  not enabled in v3.0
- per-claim citation-validator test — feature is in the considering
  bucket (chapter 17 §17.4), not built

## C.14 Coverage gaps (transparent)

Areas without dedicated unit tests in v3.0:

- `services/symphony_capture.py` — stub, returns `extraction_status: "stub"`
- `services/wo_sync.py` — read-only sync; covered by integration test
- `services/audit.py` hash chain — covered by integration test, not unit
- bounded-rerank consumer in `services/rag.py` — covered by integration test
- `routers/select_tags.py` — manual smoke test only
