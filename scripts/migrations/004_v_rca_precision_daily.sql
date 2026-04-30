-- Sprint 6 / B10 — daily RCA precision view.
--
-- Aggregates outcome_linkages (confirmed/rejected) per day per failure
-- mode for trend visualization in dashboards. The chat service exposes
-- ad-hoc slicing via /api/outcomes/precision; this view is for daily
-- BI export and slow-changing reporting.
--
-- Rebuilt nightly by pg_cron (or by an external cronjob calling
-- `REFRESH MATERIALIZED VIEW v_rca_precision_daily;`).

CREATE MATERIALIZED VIEW IF NOT EXISTS v_rca_precision_daily AS
SELECT
    date_trunc('day', ol.created_at)                                AS day,
    COALESCE(m.context_snapshot #>> '{parsed_anchor,failure_mode_scope}',
             '(unspecified)')                                       AS failure_mode,
    c.line_id                                                       AS line_id,
    COUNT(*)                                                        AS n_messages,
    SUM(CASE WHEN ol.alignment = 'confirmed' THEN 1 ELSE 0 END)     AS n_confirmed,
    SUM(CASE WHEN ol.alignment = 'rejected'  THEN 1 ELSE 0 END)     AS n_rejected,
    CASE
        WHEN SUM(CASE WHEN ol.alignment IN ('confirmed','rejected')
                      THEN 1 ELSE 0 END) > 0
        THEN SUM(CASE WHEN ol.alignment = 'confirmed' THEN 1 ELSE 0 END)::float
             / SUM(CASE WHEN ol.alignment IN ('confirmed','rejected')
                        THEN 1 ELSE 0 END)
        ELSE NULL
    END                                                             AS precision
FROM outcome_linkages ol
JOIN messages       m ON m.id = ol.message_id
JOIN conversations  c ON c.id = m.conversation_id
GROUP BY 1, 2, 3
WITH NO DATA;

CREATE UNIQUE INDEX IF NOT EXISTS idx_v_rca_precision_daily_pk
    ON v_rca_precision_daily (day, failure_mode, line_id);

CREATE INDEX IF NOT EXISTS idx_v_rca_precision_daily_fm
    ON v_rca_precision_daily (failure_mode);

INSERT INTO schema_migrations (version) VALUES ('004_v_rca_precision_daily')
    ON CONFLICT DO NOTHING;
