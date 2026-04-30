-- Sprint 1 / A2 — Daily chat-performance roll-up.
-- Migration: 005_v_chat_perf_daily
--
-- Provides a cheap, indexed-query-free way to trend p50/p95 latency,
-- short-circuit rate, and token usage without scraping Prometheus.
-- Materialized so dashboards don't pay the percentile_cont cost at
-- query time. Refreshed nightly by pg_cron (or external cron) via:
--    REFRESH MATERIALIZED VIEW CONCURRENTLY v_chat_perf_daily;
-- The CONCURRENTLY refresh requires a UNIQUE index, provided below.
--
-- Audit ref: F-09 (resolves "view should be materialized for dashboards").

-- Drop a prior plain-VIEW form if it exists before redefining.
DROP VIEW IF EXISTS v_chat_perf_daily;

CREATE MATERIALIZED VIEW IF NOT EXISTS v_chat_perf_daily AS
SELECT
    date_trunc('day', m.created_at)                                                              AS day,
    count(*)                                                                                     AS responses,
    count(*) FILTER (WHERE confidence_label = 'insufficient_evidence')                           AS short_circuits,
    count(*) FILTER (WHERE confidence_label = 'confirmed')                                       AS confirmed,
    count(*) FILTER (WHERE confidence_label = 'likely')                                          AS likely,
    count(*) FILTER (WHERE confidence_label = 'hypothesis')                                      AS hypothesis,
    percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms)                                     AS p50_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)                                     AS p95_ms,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms)                                     AS p99_ms,
    sum(COALESCE((token_usage->>'total_tokens')::int, 0))                                        AS total_tokens,
    sum(COALESCE((token_usage->>'prompt_tokens')::int, 0))                                       AS prompt_tokens,
    sum(COALESCE((token_usage->>'completion_tokens')::int, 0))                                   AS completion_tokens
FROM messages m
WHERE role = 'assistant'
GROUP BY 1
WITH NO DATA;

-- UNIQUE index required for REFRESH MATERIALIZED VIEW CONCURRENTLY.
CREATE UNIQUE INDEX IF NOT EXISTS idx_v_chat_perf_daily_day
    ON v_chat_perf_daily (day);

COMMENT ON MATERIALIZED VIEW v_chat_perf_daily IS
    'Daily roll-up of assistant responses for SLO trending. '
    'Sprint 1/A2 + F-09. Refresh nightly via pg_cron.';

INSERT INTO schema_migrations (version) VALUES ('005_v_chat_perf_daily')
    ON CONFLICT DO NOTHING;
