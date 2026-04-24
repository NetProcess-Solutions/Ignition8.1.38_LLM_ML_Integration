-- Sprint 2 / A3.1 — Convert messages + audit_log to monthly partitions.
--
-- Idempotent and safe to run on an already-partitioned schema (it only
-- attempts the conversion when the table is not already partitioned).
--
-- Backout: see scripts/migrations/001_partition_messages_BACKOUT.sql.
--
-- Steps:
--   1. Install pg_partman in its own schema if not already present.
--   2. For each table:
--      a. Rename the existing table to <table>_legacy.
--      b. Create a partitioned table with the same columns + indexes.
--      c. Create the parent partition + a default partition.
--      d. Run pg_partman maintenance to create monthly partitions for
--         the legacy table's full date range.
--      e. INSERT … SELECT from the legacy table; pg_partman routes rows.
--      f. ATTACH constraints, swap PK back to the parent, drop legacy.
--   3. Schedule pg_partman maintenance via pg_cron OR document the
--      external maintenance call (see runbook).
--
-- Why monthly: at projected 100k messages/month + audit_log multiplier,
-- monthly partitions keep individual partition sizes ≤ 2 GB and let us
-- detach old months for cold storage without VACUUM FULL on the parent.

\set ON_ERROR_STOP on

CREATE SCHEMA IF NOT EXISTS partman;
CREATE EXTENSION IF NOT EXISTS pg_partman SCHEMA partman;

-- ---------------------------------------------------------------------------
-- messages
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    is_partitioned boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM pg_partitioned_table p
        JOIN   pg_class c ON c.oid = p.partrelid
        WHERE  c.relname = 'messages'
    ) INTO is_partitioned;

    IF is_partitioned THEN
        RAISE NOTICE 'messages already partitioned, skipping';
        RETURN;
    END IF;

    EXECUTE 'ALTER TABLE messages RENAME TO messages_legacy';

    EXECUTE $ddl$
        CREATE TABLE messages (
            id                 UUID         NOT NULL DEFAULT uuid_generate_v4(),
            conversation_id    UUID         NOT NULL,
            role               VARCHAR(20)  NOT NULL,
            content            TEXT         NOT NULL,
            sources            JSONB        NOT NULL DEFAULT '[]'::jsonb,
            confidence         VARCHAR(20),
            context_snapshot   JSONB        NOT NULL DEFAULT '{}'::jsonb,
            prompt_version     VARCHAR(50),
            model_name         VARCHAR(100),
            model_params       JSONB        NOT NULL DEFAULT '{}'::jsonb,
            token_usage        JSONB        NOT NULL DEFAULT '{}'::jsonb,
            retrieval_scores   JSONB        NOT NULL DEFAULT '{}'::jsonb,
            rules_matched      JSONB        NOT NULL DEFAULT '[]'::jsonb,
            memories_used      JSONB        NOT NULL DEFAULT '[]'::jsonb,
            latency_ms         INTEGER,
            latency_breakdown  JSONB        NOT NULL DEFAULT '{}'::jsonb,
            created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            -- created_at MUST be in the PK for native range partitioning.
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
    $ddl$;

    -- Bring back FK + indexes on the new parent.
    EXECUTE $ddl$
        ALTER TABLE messages
            ADD CONSTRAINT messages_conv_fk
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    $ddl$;
    EXECUTE 'CREATE INDEX idx_messages_conv    ON messages (conversation_id, created_at)';
    EXECUTE 'CREATE INDEX idx_messages_role    ON messages (role)';
    EXECUTE 'CREATE INDEX idx_messages_created ON messages (created_at DESC)';

    -- Default partition catches anything that slips outside the maintained range.
    EXECUTE $ddl$
        CREATE TABLE messages_default PARTITION OF messages DEFAULT
    $ddl$;

    -- Register with pg_partman (monthly), pre-create 6 future + 12 past.
    PERFORM partman.create_parent(
        p_parent_table   => 'public.messages',
        p_control        => 'created_at',
        p_type           => 'native',
        p_interval       => 'monthly',
        p_premake        => 6,
        p_start_partition => to_char(date_trunc('month', NOW() - interval '12 months'), 'YYYY-MM-DD')
    );

    -- Backfill from legacy (pg_partman routes rows to correct partition).
    EXECUTE 'INSERT INTO messages SELECT * FROM messages_legacy';

    -- Sanity check before we drop the legacy table.
    PERFORM 1 FROM messages_legacy
        WHERE NOT EXISTS (SELECT 1 FROM messages m WHERE m.id = messages_legacy.id)
        LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'Backfill incomplete; not dropping messages_legacy';
    END IF;
    EXECUTE 'DROP TABLE messages_legacy';
END
$$;

-- ---------------------------------------------------------------------------
-- audit_log — same recipe, retention enforced at 24 months below.
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    is_partitioned boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM pg_partitioned_table p
        JOIN   pg_class c ON c.oid = p.partrelid
        WHERE  c.relname = 'audit_log'
    ) INTO is_partitioned;

    IF is_partitioned THEN
        RAISE NOTICE 'audit_log already partitioned, skipping';
        RETURN;
    END IF;

    EXECUTE 'ALTER TABLE audit_log RENAME TO audit_log_legacy';

    EXECUTE $ddl$
        CREATE TABLE audit_log (
            id           UUID         NOT NULL DEFAULT uuid_generate_v4(),
            event_type   VARCHAR(50)  NOT NULL,
            user_id      VARCHAR(100),
            session_id   VARCHAR(100),
            entity_type  VARCHAR(50),
            entity_id    VARCHAR(100),
            details      JSONB        NOT NULL DEFAULT '{}'::jsonb,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
    $ddl$;

    EXECUTE 'CREATE INDEX idx_audit_event_time ON audit_log (event_type, created_at DESC)';
    EXECUTE 'CREATE INDEX idx_audit_user_time  ON audit_log (user_id, created_at DESC)';
    EXECUTE 'CREATE INDEX idx_audit_entity     ON audit_log (entity_type, entity_id)';

    EXECUTE 'CREATE TABLE audit_log_default PARTITION OF audit_log DEFAULT';

    -- Re-attach the immutability trigger.
    EXECUTE $ddl$
        DROP TRIGGER IF EXISTS trg_audit_no_update ON audit_log;
        CREATE TRIGGER trg_audit_no_update BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
    $ddl$;

    PERFORM partman.create_parent(
        p_parent_table   => 'public.audit_log',
        p_control        => 'created_at',
        p_type           => 'native',
        p_interval       => 'monthly',
        p_premake        => 6,
        p_start_partition => to_char(date_trunc('month', NOW() - interval '24 months'), 'YYYY-MM-DD')
    );

    -- Retention: drop partitions older than 24 months on next maintenance run.
    UPDATE partman.part_config
        SET retention            = '24 months',
            retention_keep_table = false,
            retention_keep_index = false
        WHERE parent_table = 'public.audit_log';

    EXECUTE 'INSERT INTO audit_log SELECT * FROM audit_log_legacy';

    PERFORM 1 FROM audit_log_legacy
        WHERE NOT EXISTS (SELECT 1 FROM audit_log a WHERE a.id = audit_log_legacy.id)
        LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'Backfill incomplete; not dropping audit_log_legacy';
    END IF;
    EXECUTE 'DROP TABLE audit_log_legacy';
END
$$;

-- ---------------------------------------------------------------------------
-- pg_partman maintenance entry point.
-- Run this from a cron / pg_cron job once per day:
--
--    SELECT partman.run_maintenance(p_analyze := false);
--
-- It will (a) create new monthly partitions per `premake`, and
--          (b) drop expired audit_log partitions per the retention rule.
-- ---------------------------------------------------------------------------
