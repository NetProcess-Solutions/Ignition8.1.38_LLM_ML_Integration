-- Sprint 1 / A2 — Daily chat-performance roll-up.
--
-- Provides a cheap, indexed-query-free way to trend p50/p95 latency,
-- short-circuit rate, and token usage without scraping Prometheus.
-- View is recreated idempotently; safe to re-run.

CREATE OR REPLACE VIEW v_chat_perf_daily AS
SELECT
    date_trunc('day', m.created_at)                                           AS day,
    count(*)                                              FILTER (WHERE role = 'assistant')          AS responses,
    count(*)                                              FILTER (WHERE confidence = 'insufficient_evidence') AS short_circuits,
    count(*)                                              FILTER (WHERE confidence = 'confirmed')             AS confirmed,
    count(*)                                              FILTER (WHERE confidence = 'likely')                AS likely,
    count(*)                                              FILTER (WHERE confidence = 'hypothesis')            AS hypothesis,
    percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms)                  AS p50_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)                  AS p95_ms,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms)                  AS p99_ms,
    sum(COALESCE((token_usage->>'total_tokens')::int, 0))                     AS total_tokens,
    sum(COALESCE((token_usage->>'prompt_tokens')::int, 0))                    AS prompt_tokens,
    sum(COALESCE((token_usage->>'completion_tokens')::int, 0))                AS completion_tokens
FROM messages m
WHERE role = 'assistant'
GROUP BY 1;

COMMENT ON VIEW v_chat_perf_daily IS
    'Daily roll-up of assistant responses for SLO trending. Sprint 1/A2.';
