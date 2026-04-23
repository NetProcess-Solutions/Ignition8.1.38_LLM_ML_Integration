-- =============================================================================
-- IgnitionChatbot v2.0 schema
-- All 29 tables across 9 schema groups, created upfront per design §4.1.
-- Idempotent: safe to re-run; uses IF NOT EXISTS / DROP IF EXISTS guards.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =============================================================================
-- Schema Group 1: Document Corpus
-- =============================================================================

CREATE TABLE IF NOT EXISTS documents (
    id                       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_type              VARCHAR(50)  NOT NULL,
    source_id                VARCHAR(255),
    line_id                  VARCHAR(50)  NOT NULL,
    title                    VARCHAR(500),
    author                   VARCHAR(255),
    document_date            TIMESTAMPTZ,
    shift                    VARCHAR(20),
    document_role            VARCHAR(50),
    document_weight          NUMERIC(3,2) NOT NULL DEFAULT 1.0,
    applicable_positions     TEXT[]       NOT NULL DEFAULT '{}',
    applicable_equipment     TEXT[]       NOT NULL DEFAULT '{}',
    applicable_failure_modes TEXT[]       NOT NULL DEFAULT '{}',
    raw_text                 TEXT,
    structured_fields        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    metadata                 JSONB        NOT NULL DEFAULT '{}'::jsonb,
    ingestion_batch_id       UUID,
    is_active                BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_documents_line_active   ON documents (line_id, is_active);
CREATE INDEX IF NOT EXISTS idx_documents_source_type   ON documents (source_type);
CREATE INDEX IF NOT EXISTS idx_documents_document_date ON documents (document_date DESC);
CREATE INDEX IF NOT EXISTS idx_documents_role          ON documents (document_role);

CREATE TABLE IF NOT EXISTS document_chunks (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id  UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index  INTEGER     NOT NULL,
    chunk_text   TEXT        NOT NULL,
    embedding    VECTOR(384),
    token_count  INTEGER,
    metadata     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON document_chunks (document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON document_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_chunks_text_trgm
    ON document_chunks USING gin (chunk_text gin_trgm_ops);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_type           VARCHAR(50) NOT NULL,
    started_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at          TIMESTAMPTZ,
    documents_processed   INTEGER     NOT NULL DEFAULT 0,
    chunks_created        INTEGER     NOT NULL DEFAULT 0,
    errors                JSONB       NOT NULL DEFAULT '[]'::jsonb,
    triggered_by          VARCHAR(255)
);

-- =============================================================================
-- Schema Group 2: Events & Outcomes
-- =============================================================================

CREATE TABLE IF NOT EXISTS production_runs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    line_id         VARCHAR(50)  NOT NULL,
    run_number      VARCHAR(100),
    recipe_id       VARCHAR(100),
    product_style   VARCHAR(100),
    product_family  VARCHAR(100),
    front_step      INTEGER,
    start_time      TIMESTAMPTZ  NOT NULL,
    end_time        TIMESTAMPTZ,
    status          VARCHAR(20)  NOT NULL DEFAULT 'running',
    target_specs    JSONB        NOT NULL DEFAULT '{}'::jsonb,
    metadata        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (line_id, run_number)
);
CREATE INDEX IF NOT EXISTS idx_runs_line_start  ON production_runs (line_id, start_time DESC);
CREATE INDEX IF NOT EXISTS idx_runs_style_step  ON production_runs (product_style, front_step);
CREATE INDEX IF NOT EXISTS idx_runs_status      ON production_runs (status);

CREATE TABLE IF NOT EXISTS downtime_events (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    line_id               VARCHAR(50) NOT NULL,
    run_id                UUID REFERENCES production_runs(id) ON DELETE SET NULL,
    start_time            TIMESTAMPTZ NOT NULL,
    end_time              TIMESTAMPTZ,
    duration_minutes      NUMERIC GENERATED ALWAYS AS
                              (EXTRACT(EPOCH FROM (end_time - start_time)) / 60.0) STORED,
    category              VARCHAR(50),
    subcategory           VARCHAR(100),
    equipment_id          VARCHAR(100),
    description           TEXT,
    root_cause            TEXT,
    root_cause_confirmed  BOOLEAN     NOT NULL DEFAULT FALSE,
    shift                 VARCHAR(20),
    reported_by           VARCHAR(255),
    metadata              JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_downtime_line_time ON downtime_events (line_id, start_time DESC);
CREATE INDEX IF NOT EXISTS idx_downtime_run       ON downtime_events (run_id);
CREATE INDEX IF NOT EXISTS idx_downtime_equipment ON downtime_events (equipment_id);

CREATE TABLE IF NOT EXISTS quality_results (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    line_id         VARCHAR(50) NOT NULL,
    run_id          UUID REFERENCES production_runs(id) ON DELETE SET NULL,
    test_type       VARCHAR(50) NOT NULL,
    test_time       TIMESTAMPTZ NOT NULL,
    sample_id       VARCHAR(100),
    result          VARCHAR(20),
    measurements    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    specification   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    notes           TEXT,
    tested_by       VARCHAR(255),
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_quality_line_time   ON quality_results (line_id, test_time DESC);
CREATE INDEX IF NOT EXISTS idx_quality_run         ON quality_results (run_id);
CREATE INDEX IF NOT EXISTS idx_quality_sample      ON quality_results (sample_id);
CREATE INDEX IF NOT EXISTS idx_quality_test_type   ON quality_results (test_type);

-- failure_mode is a closed enum maintained in failure_modes (Group 9 / seed
-- reference data). We enforce referential integrity via FK so the enum stays
-- closed; adding a new mode requires inserting into failure_modes first.
CREATE TABLE IF NOT EXISTS failure_modes (
    code         VARCHAR(80) PRIMARY KEY,
    label        VARCHAR(255) NOT NULL,
    defect_type  VARCHAR(50)  NOT NULL,
    description  TEXT,
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS defect_events (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    line_id               VARCHAR(50) NOT NULL,
    run_id                UUID REFERENCES production_runs(id) ON DELETE SET NULL,
    defect_type           VARCHAR(50) NOT NULL,
    failure_mode          VARCHAR(80) REFERENCES failure_modes(code) ON DELETE RESTRICT,
    detected_time         TIMESTAMPTZ NOT NULL,
    detection_method      VARCHAR(50),
    severity              VARCHAR(20),
    quantity_affected     NUMERIC,
    description           TEXT,
    root_cause            TEXT,
    root_cause_confirmed  BOOLEAN     NOT NULL DEFAULT FALSE,
    corrective_action     TEXT,
    status                VARCHAR(20) NOT NULL DEFAULT 'open',
    resolved_by           VARCHAR(255),
    resolved_at           TIMESTAMPTZ,
    metadata              JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_defect_line_time     ON defect_events (line_id, detected_time DESC);
CREATE INDEX IF NOT EXISTS idx_defect_run           ON defect_events (run_id);
CREATE INDEX IF NOT EXISTS idx_defect_failure_mode  ON defect_events (failure_mode);
CREATE INDEX IF NOT EXISTS idx_defect_status        ON defect_events (status);

CREATE TABLE IF NOT EXISTS work_orders (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    wo_number            VARCHAR(100) NOT NULL,
    line_id              VARCHAR(50)  NOT NULL,
    run_id               UUID REFERENCES production_runs(id) ON DELETE SET NULL,
    equipment_id         VARCHAR(100),
    wo_type              VARCHAR(40),
    priority             VARCHAR(20),
    status               VARCHAR(20),
    requested_by         VARCHAR(255),
    assigned_to          VARCHAR(255),
    date_opened          TIMESTAMPTZ NOT NULL,
    date_closed          TIMESTAMPTZ,
    labor_hours          NUMERIC,
    parts_used           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    problem_description  TEXT,
    resolution_notes     TEXT,
    source_wo_id         VARCHAR(255),
    last_synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata             JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (wo_number)
);
CREATE INDEX IF NOT EXISTS idx_wo_line_opened  ON work_orders (line_id, date_opened DESC);
CREATE INDEX IF NOT EXISTS idx_wo_equipment    ON work_orders (equipment_id);
CREATE INDEX IF NOT EXISTS idx_wo_status       ON work_orders (status);
CREATE INDEX IF NOT EXISTS idx_wo_run          ON work_orders (run_id);

CREATE TABLE IF NOT EXISTS event_clips (
    id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_id           UUID         NOT NULL,
    event_type         VARCHAR(50)  NOT NULL,
    camera_id          VARCHAR(100) NOT NULL,
    camera_location    VARCHAR(100),
    clip_start         TIMESTAMPTZ  NOT NULL,
    clip_end           TIMESTAMPTZ  NOT NULL,
    storage_handle     VARCHAR(500),
    extraction_status  VARCHAR(20)  NOT NULL DEFAULT 'pending',
    failure_reason     TEXT,
    purged_at          TIMESTAMPTZ,
    captured_via       VARCHAR(50),
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_clips_event       ON event_clips (event_type, event_id);
CREATE INDEX IF NOT EXISTS idx_clips_camera_time ON event_clips (camera_id, clip_start DESC);
CREATE INDEX IF NOT EXISTS idx_clips_status      ON event_clips (extraction_status);

-- =============================================================================
-- Schema Group 3: Conversations, Messages, Feedback-Learning
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_profiles (
    id                              VARCHAR(255) PRIMARY KEY,
    display_name                    VARCHAR(255),
    role_primary                    VARCHAR(50)  NOT NULL DEFAULT 'operator',
    roles_additional                TEXT[]       NOT NULL DEFAULT '{}',
    department                      VARCHAR(100),
    shift_default                   VARCHAR(20),
    lines_primary                   TEXT[]       NOT NULL DEFAULT '{}',
    equipment_focus                 TEXT[]       NOT NULL DEFAULT '{}',
    response_detail_level           VARCHAR(20)  NOT NULL DEFAULT 'standard',
    response_style                  VARCHAR(20)  NOT NULL DEFAULT 'balanced',
    include_tag_values              BOOLEAN      NOT NULL DEFAULT TRUE,
    include_ml_details              BOOLEAN      NOT NULL DEFAULT FALSE,
    include_source_excerpts         BOOLEAN      NOT NULL DEFAULT TRUE,
    default_historian_window_minutes INTEGER     NOT NULL DEFAULT 60,
    auto_include_alarms             BOOLEAN      NOT NULL DEFAULT TRUE,
    preferred_units                 VARCHAR(20)  NOT NULL DEFAULT 'imperial',
    created_at                      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_active_at                  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS user_permissions (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    role        VARCHAR(50)  NOT NULL,
    permission  VARCHAR(100) NOT NULL,
    granted     BOOLEAN      NOT NULL DEFAULT FALSE,
    UNIQUE (role, permission)
);

CREATE TABLE IF NOT EXISTS conversations (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id     VARCHAR(255) NOT NULL,
    user_id        VARCHAR(255) REFERENCES user_profiles(id) ON DELETE SET NULL,
    line_id        VARCHAR(50)  NOT NULL,
    started_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ended_at       TIMESTAMPTZ,
    message_count  INTEGER      NOT NULL DEFAULT 0,
    metadata       JSONB        NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations (session_id);
CREATE INDEX IF NOT EXISTS idx_conv_user    ON conversations (user_id, started_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id    UUID         NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role               VARCHAR(20)  NOT NULL,
    content            TEXT         NOT NULL,
    sources            JSONB        NOT NULL DEFAULT '[]'::jsonb,
    confidence         VARCHAR(20),
    -- The full audit record. Per design §3.10 it includes the parsed anchor,
    -- which buckets were populated AND which were explicitly excluded with
    -- reason, retrieval scores, rules matched, memory ids, clip handles,
    -- and prompt+model pinning.
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
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_messages_conv    ON messages (conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_role    ON messages (role);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages (created_at DESC);

CREATE TABLE IF NOT EXISTS message_feedback (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id   UUID         NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id      VARCHAR(255) REFERENCES user_profiles(id) ON DELETE SET NULL,
    signal_type  VARCHAR(50)  NOT NULL,
    signal_value VARCHAR(20)  NOT NULL,
    comment      TEXT,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_feedback_message     ON message_feedback (message_id);
CREATE INDEX IF NOT EXISTS idx_feedback_signal      ON message_feedback (signal_type, signal_value);

-- Memory candidates is referenced by user_corrections.created_memory_id later.
-- Forward-declare line_memory and memory_candidates with minimal CREATE then
-- add FKs after both exist. (Postgres allows late-bound FKs only via ALTER.)
CREATE TABLE IF NOT EXISTS line_memory (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    line_id             VARCHAR(50)  NOT NULL,
    category            VARCHAR(50)  NOT NULL,
    content             TEXT         NOT NULL,
    source              VARCHAR(255),
    confidence          VARCHAR(20)  NOT NULL DEFAULT 'medium',
    status              VARCHAR(20)  NOT NULL DEFAULT 'draft',
    embedding           VECTOR(384),
    tags                TEXT[]       NOT NULL DEFAULT '{}',
    equipment_ids       TEXT[]       NOT NULL DEFAULT '{}',
    applies_to_products TEXT[]       NOT NULL DEFAULT '{}',
    created_by          VARCHAR(255),
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    reviewed_by         VARCHAR(255),
    review_date         TIMESTAMPTZ,
    approved_by         VARCHAR(255),
    approved_date       TIMESTAMPTZ,
    deprecated_at       TIMESTAMPTZ,
    deprecated_reason   TEXT,
    deprecated_by       VARCHAR(255),
    challenge_count     INTEGER      NOT NULL DEFAULT 0,
    last_challenged_at  TIMESTAMPTZ,
    access_count        INTEGER      NOT NULL DEFAULT 0,
    last_accessed       TIMESTAMPTZ,
    superseded_by       UUID
);
CREATE INDEX IF NOT EXISTS idx_memory_status_line ON line_memory (status, line_id);
CREATE INDEX IF NOT EXISTS idx_memory_category    ON line_memory (category);
CREATE INDEX IF NOT EXISTS idx_memory_embedding
    ON line_memory USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50);
CREATE INDEX IF NOT EXISTS idx_memory_tags        ON line_memory USING gin (tags);
DO $$ BEGIN
    ALTER TABLE line_memory
        ADD CONSTRAINT fk_memory_superseded_by
        FOREIGN KEY (superseded_by) REFERENCES line_memory(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS user_corrections (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id           UUID         NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id              VARCHAR(255) REFERENCES user_profiles(id) ON DELETE SET NULL,
    correction_type      VARCHAR(50)  NOT NULL,
    original_claim       TEXT,
    corrected_claim      TEXT         NOT NULL,
    supporting_evidence  TEXT,
    status               VARCHAR(20)  NOT NULL DEFAULT 'submitted',
    reviewed_by          VARCHAR(255),
    review_date          TIMESTAMPTZ,
    review_notes         TEXT,
    created_memory_id    UUID REFERENCES line_memory(id) ON DELETE SET NULL,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_corrections_message ON user_corrections (message_id);
CREATE INDEX IF NOT EXISTS idx_corrections_status  ON user_corrections (status);

CREATE TABLE IF NOT EXISTS outcome_linkages (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id    UUID         NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    outcome_type  VARCHAR(50)  NOT NULL,
    outcome_id    UUID         NOT NULL,
    outcome_table VARCHAR(50)  NOT NULL,
    alignment     VARCHAR(20)  NOT NULL,
    linked_by     VARCHAR(255),
    notes         TEXT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_outcomes_message ON outcome_linkages (message_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_outcome ON outcome_linkages (outcome_table, outcome_id);

CREATE TABLE IF NOT EXISTS memory_candidates (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_type           VARCHAR(50)  NOT NULL,
    source_message_ids    UUID[]       NOT NULL DEFAULT '{}',
    source_feedback_ids   UUID[]       NOT NULL DEFAULT '{}',
    source_correction_id  UUID REFERENCES user_corrections(id) ON DELETE SET NULL,
    source_outcome_ids    UUID[]       NOT NULL DEFAULT '{}',
    proposed_content      TEXT         NOT NULL,
    proposed_category     VARCHAR(50),
    confidence_score      NUMERIC(3,2) NOT NULL DEFAULT 0.0,
    status                VARCHAR(20)  NOT NULL DEFAULT 'proposed',
    promoted_memory_id    UUID REFERENCES line_memory(id) ON DELETE SET NULL,
    reviewed_by           VARCHAR(255),
    review_date           TIMESTAMPTZ,
    review_notes          TEXT,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON memory_candidates (status, confidence_score DESC);

-- chunk_quality_signals is the v1 retrieval-feedback table; design §5.5 Flow 1
-- explicitly relies on it. Keep it.
CREATE TABLE IF NOT EXISTS chunk_quality_signals (
    chunk_id        UUID PRIMARY KEY REFERENCES document_chunks(id) ON DELETE CASCADE,
    quality_score   NUMERIC      NOT NULL DEFAULT 0.0,
    sample_count    INTEGER      NOT NULL DEFAULT 0,
    last_updated    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- Schema Group 6: ML Foundation (tables now, populated Phase 4)
-- =============================================================================

CREATE TABLE IF NOT EXISTS ml_models (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_name           VARCHAR(100) NOT NULL,
    model_version        VARCHAR(50)  NOT NULL,
    model_type           VARCHAR(50)  NOT NULL,
    feature_set_version  VARCHAR(50),
    training_data_start  TIMESTAMPTZ,
    training_data_end    TIMESTAMPTZ,
    training_row_count   INTEGER,
    holdout_row_count    INTEGER,
    metrics              JSONB        NOT NULL DEFAULT '{}'::jsonb,
    hyperparameters      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    artifact_path        VARCHAR(500),
    is_active            BOOLEAN      NOT NULL DEFAULT FALSE,
    activated_at         TIMESTAMPTZ,
    activated_by         VARCHAR(255),
    notes                TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (model_name, model_version)
);
CREATE INDEX IF NOT EXISTS idx_models_active ON ml_models (model_name, is_active);

CREATE TABLE IF NOT EXISTS ml_predictions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_id            UUID         NOT NULL REFERENCES ml_models(id) ON DELETE CASCADE,
    run_id              UUID REFERENCES production_runs(id) ON DELETE SET NULL,
    event_id            UUID,
    event_type          VARCHAR(50),
    prediction          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    explanation         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    input_features      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    actual_outcome      VARCHAR(50),
    outcome_recorded_at TIMESTAMPTZ,
    message_id          UUID REFERENCES messages(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_predictions_model_run ON ml_predictions (model_id, run_id);

CREATE TABLE IF NOT EXISTS feature_snapshots (
    id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id               UUID REFERENCES production_runs(id) ON DELETE SET NULL,
    event_id             UUID,
    event_type           VARCHAR(50),
    feature_set_version  VARCHAR(50)  NOT NULL,
    features             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    label                VARCHAR(50),
    label_source         VARCHAR(100),
    window_start         TIMESTAMPTZ,
    window_end           TIMESTAMPTZ,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_snapshots_run         ON feature_snapshots (run_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_label       ON feature_snapshots (label);
CREATE INDEX IF NOT EXISTS idx_snapshots_feature_set ON feature_snapshots (feature_set_version);

CREATE TABLE IF NOT EXISTS feature_definitions (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    version       VARCHAR(50) NOT NULL UNIQUE,
    description   TEXT,
    feature_specs JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by    VARCHAR(255)
);

-- =============================================================================
-- Schema Group 7: Configuration & Versioning
-- =============================================================================

CREATE TABLE IF NOT EXISTS prompt_versions (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    prompt_name   VARCHAR(100) NOT NULL,
    version       VARCHAR(50)  NOT NULL,
    content       TEXT         NOT NULL,
    is_active     BOOLEAN      NOT NULL DEFAULT FALSE,
    activated_at  TIMESTAMPTZ,
    notes         TEXT,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by    VARCHAR(255),
    UNIQUE (prompt_name, version)
);
CREATE INDEX IF NOT EXISTS idx_prompts_active ON prompt_versions (prompt_name, is_active);

CREATE TABLE IF NOT EXISTS business_rules (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rule_name   VARCHAR(100) NOT NULL,
    line_id     VARCHAR(50)  NOT NULL,
    condition   JSONB        NOT NULL,
    conclusion  TEXT         NOT NULL,
    severity    VARCHAR(20)  NOT NULL DEFAULT 'info',
    category    VARCHAR(50),
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    version     VARCHAR(50)  NOT NULL DEFAULT 'v1',
    created_by  VARCHAR(255),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (rule_name, version)
);
CREATE INDEX IF NOT EXISTS idx_rules_active_line ON business_rules (line_id, is_active);

-- =============================================================================
-- Schema Group 8: Audit Log (append-only)
-- =============================================================================

CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    event_type  VARCHAR(50)  NOT NULL,
    user_id     VARCHAR(255),
    session_id  VARCHAR(255),
    entity_type VARCHAR(50),
    entity_id   VARCHAR(255),
    details     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_event_time  ON audit_log (event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user_time   ON audit_log (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_entity      ON audit_log (entity_type, entity_id);

-- Enforce append-only at the application layer; also block UPDATE/DELETE here
-- so an out-of-band actor can't tamper.
CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_audit_no_update ON audit_log;
CREATE TRIGGER trg_audit_no_update BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();

-- =============================================================================
-- Schema Group 9: Tag Registry (discovered tag metadata)
-- =============================================================================

CREATE TABLE IF NOT EXISTS tag_registry (
    id                          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    server_item_name            VARCHAR(500) NOT NULL UNIQUE,
    description                 VARCHAR(500),
    data_type                   VARCHAR(50),
    plant_number                VARCHAR(10),
    server_description          VARCHAR(500),
    uns_path                    VARCHAR(500),
    discovered_class            VARCHAR(40),
    classification_rule_matched VARCHAR(100),
    manual_override_class       VARCHAR(40),
    override_reason             TEXT,
    overridden_by               VARCHAR(255),
    overridden_at               TIMESTAMPTZ,
    tier                        VARCHAR(10) NOT NULL DEFAULT 'tier2',
    routing_keywords            TEXT[]      NOT NULL DEFAULT '{}',
    first_seen                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active                   BOOLEAN     NOT NULL DEFAULT TRUE,
    setpoint_partner_item       VARCHAR(500)
);
CREATE INDEX IF NOT EXISTS idx_tagreg_class  ON tag_registry (discovered_class);
CREATE INDEX IF NOT EXISTS idx_tagreg_active ON tag_registry (is_active);
CREATE INDEX IF NOT EXISTS idx_tagreg_tier   ON tag_registry (tier);

-- =============================================================================
-- updated_at triggers
-- =============================================================================

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t TEXT;
BEGIN
    FOR t IN SELECT unnest(ARRAY[
        'documents', 'production_runs', 'downtime_events', 'quality_results',
        'defect_events', 'work_orders', 'event_clips', 'business_rules'
    ]) LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS trg_touch_%I ON %I; '
            'CREATE TRIGGER trg_touch_%I BEFORE UPDATE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION touch_updated_at();',
            t, t, t, t
        );
    END LOOP;
END $$;
