-- Sprint 2 / A3.3 — pgvector index migration trigger + monitoring view.
--
-- Today: ivfflat (lists=100). Becomes lossy/slow above ~100k chunks.
-- Future: hnsw (m=16, ef_construction=64) when row count > 100k.
--
-- This script does NOT migrate the index automatically. Index swaps are
-- locking operations that should happen in a maintenance window.
-- Instead, it provides:
--   * a view to trend chunk counts and warn at 80k / 100k thresholds,
--   * the exact DDL the operator runs during the window.

CREATE OR REPLACE VIEW v_pgvector_index_status AS
SELECT
    (SELECT count(*) FROM document_chunks)               AS chunk_count,
    (SELECT count(*) FROM document_chunks
        WHERE embedding IS NOT NULL)                     AS embedded_chunks,
    CASE
        WHEN (SELECT count(*) FROM document_chunks) > 100000 THEN 'migrate_now'
        WHEN (SELECT count(*) FROM document_chunks) >  80000 THEN 'plan_migration'
        ELSE 'ok'
    END                                                  AS recommendation,
    'ivfflat lists=100 → hnsw (m=16, ef_construction=64)' AS planned_migration;

COMMENT ON VIEW v_pgvector_index_status IS
    'Sprint 2 / A3.3 — alert helper for pgvector index migration.';

-- -----------------------------------------------------------------------------
-- Operator-run DDL (DO NOT auto-execute):
--
--   BEGIN;
--   DROP INDEX IF EXISTS idx_chunks_embedding_ivfflat;
--   CREATE INDEX CONCURRENTLY idx_chunks_embedding_hnsw
--       ON document_chunks USING hnsw (embedding vector_cosine_ops)
--       WITH (m = 16, ef_construction = 64);
--   ANALYZE document_chunks;
--   COMMIT;
--
-- After the swap, set the runtime parameter for query-time accuracy:
--   SET hnsw.ef_search = 40;     -- per-session
--   ALTER SYSTEM SET hnsw.ef_search = 40;  -- persistent
-- -----------------------------------------------------------------------------
