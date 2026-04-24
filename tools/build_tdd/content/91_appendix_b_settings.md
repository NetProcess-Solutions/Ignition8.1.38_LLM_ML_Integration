# Appendix B — Settings Reference

Every tunable setting in `service/config/settings.py`, grouped by
subsystem. Defaults shown are the as-built v3.0 values.

## B.1 Core service

| Setting              | Default          | Effect |
|----------------------|------------------|--------|
| `service_env`        | `"development"`  | `development | production`. Production strips dev-only logging |
| `api_key`            | `"dev-key-change-me"` | The shared secret Ignition uses; **must be replaced** |
| `database_url`       | `"postgresql+asyncpg://chatbot:change_me_in_production@localhost:5432/ignition_chatbot"` | Connection string |
| `db_pool_size`       | 20               | asyncpg pool size |
| `db_pool_timeout_seconds` | 30          | Pool acquire timeout |
| `gateway_jwt_secret` | (env)            | HS256 secret for gateway JWT validation |
| `gateway_jwt_audience` | `"coater1-svc"` | Expected JWT `aud` claim |

## B.2 LLM

| Setting                  | Default          | Effect |
|--------------------------|------------------|--------|
| `llm_provider`           | `"openai"`       | `openai | azure_openai | local` |
| `llm_model`              | `"gpt-4o-mini"`  | Model name |
| `llm_temperature`        | 0.1              | Lower = more deterministic |
| `llm_max_tokens_response`| 1500             | Per-response cap |
| `llm_concurrency`        | 4                | In-process semaphore |
| `llm_request_timeout_seconds` | 60          | Per-call timeout |
| `openai_api_key`         | (env)            | OpenAI |
| `azure_openai_endpoint`  | (env)            | Azure OpenAI |
| `azure_openai_api_key`   | (env)            | Azure OpenAI |
| `azure_openai_deployment`| (env)            | Azure deployment name |
| `azure_openai_api_version`| `"2024-08-01-preview"` | Azure API version |
| `local_llm_endpoint`     | `""`             | Empty = off; e.g. `http://vllm-host:8000/v1` |
| `local_llm_model`        | `""`             | Local model identifier |

## B.3 Embeddings

| Setting               | Default                       | Effect |
|-----------------------|-------------------------------|--------|
| `embedding_model`     | `"text-embedding-3-small"`    | Model name |
| `embedding_dimensions`| 1536                          | Must match `document_chunks.embedding` schema |
| `embedding_batch_size`| 100                           | Per-API-call batch |

## B.4 Retrieval

| Setting                          | Default | Effect |
|----------------------------------|---------|--------|
| `retrieval_vector_top_k`         | 50      | Stage 1 ANN candidate count |
| `retrieval_keyword_top_k`        | 50      | Stage 2 BM25 candidate count |
| `retrieval_rrf_top_k`            | 30      | Stage 3 fused list size |
| `retrieval_top_k`                | 10      | Stage 5 MMR final size |
| `retrieval_rrf_k`                | 60      | RRF constant (do not tune) |
| `retrieval_mmr_lambda`           | 0.7     | MMR relevance/diversity |
| `retrieval_boost_failure_mode`   | 1.5     | FM scope match boost |
| `retrieval_boost_equipment`      | 1.3     | Equipment scope match boost |
| `retrieval_role_weight_min`      | 0.5     | document_role weight floor |
| `retrieval_role_weight_max`      | 2.5     | document_role weight ceiling |
| `feedback_re_rank_help_weight`   | 0.05    | per-helpful-vote weight |
| `feedback_re_rank_outcome_weight`| 0.10    | per-correct-citation weight |
| `feedback_re_rank_clamp`         | 0.30    | ±30% bound (non-negotiable) |

## B.5 RCA chain

| Setting                          | Default | Effect |
|----------------------------------|---------|--------|
| `rca_chain_enabled`              | `true`  | Master toggle |
| `rca_max_hypotheses`             | 3       | Step-1 output cap |
| `rca_max_evidence_per_hypothesis`| 5       | Per-hypothesis evidence cap |
| `rca_max_total_tool_calls`       | 15      | Shared step1+step2 budget |
| `rca_step1_max_iters`            | 2       | LLM ↔ tools loop iters in step 1 |
| `rca_step2_max_iters`            | 2       | Same in step 2 |
| `rca_step_timeout_seconds`       | 30      | Per-step wall clock |
| `rca_cache_ttl_seconds`          | 300     | Step-1 cache TTL |

## B.6 Tools

| Setting                | Default | Effect |
|------------------------|---------|--------|
| `tool_sql_timeout_ms`  | 5000    | Per-tool hard timeout |
| `tool_max_result_rows` | 25      | Per-tool result-size cap |

## B.7 Distributional grounding

| Setting                          | Default | Effect |
|----------------------------------|---------|--------|
| `percentile_cache_ttl_seconds`   | 600     | Per-CDF in-process cache TTL |
| `percentile_min_samples`         | 30      | Below this, CDF marked insufficient_data |
| `drift_window_days`              | 90      | Page-Hinkley window |
| `drift_delta_sigma`              | 0.5     | Tolerance below which we don't care |
| `drift_threshold_sigma`          | 5.0     | PH alarm threshold |

## B.8 Anomaly detection

| Setting                          | Default | Effect |
|----------------------------------|---------|--------|
| `anomaly_fit_interval_seconds`   | 14400   | Re-fit cadence (4 h) |
| `anomaly_baseline_window_days`   | 90      | Fit window |
| `anomaly_p95_threshold`          | auto    | From fit; configurable override |
| `anomaly_feature_min_overlap`    | 8       | Min features in live snapshot to score |
| `anomaly_top_contributing_tags`  | 5       | K in top-K attribution |

## B.9 Change ledger

| Setting                              | Default | Effect |
|--------------------------------------|---------|--------|
| `change_ledger_baseline_pct_min`     | 0.5     | Min recipe dominance for clean baseline |
| `change_ledger_sigma_threshold`      | 2.0     | Tag-delta noise floor |
| `change_ledger_max_tag_deltas`       | 10      | Top-K sigma-ranked tags surfaced |

## B.10 Feedback & outcomes

| Setting                       | Default       | Effect |
|-------------------------------|---------------|--------|
| `outcome_closure_enabled`     | `true`        | Master toggle |
| `outcome_closure_window_hours`| 24            | Sweep window |
| `outcome_closure_cron`        | `"0 4 * * *"` | Nightly at 04:00 UTC |
| `memory_challenge_threshold`  | 3             | Independent challenges before flip |
| `memory_approved_boost`       | 1.5           | Retrieval multiplier on approved memory |

## B.11 Rate limits

| Setting                  | Default                    | Effect |
|--------------------------|----------------------------|--------|
| `chat_rate_limits`       | `"10/minute, 200/hour"`    | Per-user `/api/chat` |
| `feedback_rate_limits`   | `"60/minute, 1000/hour"`   | Per-user `/api/feedback` |
| `corrections_rate_limits`| `"5/minute, 50/hour"`      | Per-user `/api/corrections` |

## B.12 Tag selection

| Setting                  | Default | Effect |
|--------------------------|---------|--------|
| `tag_catalog_source`     | `"key_tags_jsonblob"` | `key_tags_jsonblob | tag_registry` (forward-compatible) |
| `tag_selector_max_tier2` | 25      | Cap on tier-2 routed tags |

## B.13 Observability

| Setting                | Default        | Effect |
|------------------------|----------------|--------|
| `log_level`            | `"INFO"`       | Standard Python log levels |
| `log_format`           | `"json"`       | `json | console` |
| `metrics_enabled`      | `true`         | Exposes `/metrics` |
| `metrics_path`         | `"/metrics"`   | Endpoint path |
| `health_check_deep_timeout_seconds` | 5 | Per-leg deep-check timeout |
