-- Sprint 2 / A3 — feature_snapshots TTL for the percentile/baseline cache rows.
--
-- The B0 percentile service caches CDFs in-process by default, but if a
-- future refresh job materializes them under
-- feature_snapshots.feature_set_version='percentile_cdf_v1' or
-- 'baseline_cache_v1', this view + delete keeps them bounded.
--
-- Run nightly from cron:
--    DELETE FROM feature_snapshots WHERE id IN (SELECT id FROM v_feature_snapshots_expired);

CREATE OR REPLACE VIEW v_feature_snapshots_expired AS
SELECT id, feature_set_version, created_at
FROM   feature_snapshots
WHERE
    (feature_set_version = 'baseline_cache_v1'   AND created_at < NOW() - INTERVAL '30 days')
 OR (feature_set_version = 'percentile_cdf_v1'   AND created_at < NOW() - INTERVAL '30 days')
 OR (feature_set_version = 'rca_cache_v1'        AND created_at < NOW() - INTERVAL '24 hours');

COMMENT ON VIEW v_feature_snapshots_expired IS
    'Sprint 2 / A3.2 — rows eligible for retention sweep.';

INSERT INTO schema_migrations (version) VALUES ('002_feature_snapshots_retention')
    ON CONFLICT DO NOTHING;
