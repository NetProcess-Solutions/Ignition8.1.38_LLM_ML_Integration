-- =============================================================================
-- IgnitionChatbot - Full schema
-- Creates all tables across 8 schema groups in one migration.
-- See docs/data_model.md for narrative.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- =============================================================================
-- GROUP 1: DOCUMENT CORPUS
-- =============================================================================

CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_type     VARCHAR(50)  NOT NULL,
    source_id       VARCHAR(255),
    line_id         VARCHAR(50)  NOT NULL,
    title           VARCHAR(500),
    author          VARCHAR(255),
    document_date   TIMESTAMPTZ,
    shift           VARCHAR(20),
    raw_text        TEXT,
    structured_fields JSONB DEFAULT '{}'::jsonb,
    metadata        JSONB DEFAULT '{}'::jsonb,
    ingestion_batch_id UUID,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT documents_source_type_chk
      CHECK (source_type IN ('maintenance_report','downtime_report','quality_report',
                             'sop','procedure','note','defect_report','other'))
);
CREATE INDEX idx_documents_line_date     ON documents (line_id, document_date DESC);
CREATE INDEX idx_documents_source_type   ON documents (source_type);
CREATE INDEX idx_documents_active        ON documents (is_active) WHERE is_active = TRUE;
CREATE INDEX idx_documents_metadata_gin  ON documents USING gin (metadata);

CREATE TABLE document_chunks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    chunk_text      TEXT NOT NULL,
    embedding       VECTOR(384),
    token_count     INTEGER,
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);
CREATE INDEX idx_chunks_document   ON document_chunks (document_id);
CREATE INDEX idx_chunks_embedding  ON document_chunks
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_chunks_text_trgm  ON document_chunks USING gin (chunk_text gin_trgm_ops);

CREATE TABLE ingestion_runs (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_type         VARCHAR(50) NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    documents_processed INTEGER DEFAULT 0,
    chunks_created      INTEGER DEFAULT 0,
    errors              JSONB DEFAULT '[]'::jsonb,
    triggered_by        VARCHAR(255),
    notes               TEXT
);

-- =============================================================================
-- GROUP 2: EVENTS & OUTCOMES
-- =============================================================================

CREATE TABLE production_runs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    line_id         VARCHAR(50) NOT NULL,
    run_number      VARCHAR(100),
    recipe_id       VARCHAR(100),
    product_style   VARCHAR(100),
    product_family  VARCHAR(100),
    start_time      TIMESTAMPTZ NOT NULL,
    end_time        TIMESTAMPTZ,
    status          VARCHAR(20) NOT NULL DEFAULT 'running',
    target_specs    JSONB DEFAULT '{}'::jsonb,
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT production_runs_status_chk
      CHECK (status IN ('running','completed','aborted'))
);
CREATE INDEX idx_runs_line_time   ON production_runs (line_id, start_time DESC);
CREATE INDEX idx_runs_product     ON production_runs (product_family, product_style);

CREATE TABLE downtime_events (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    line_id             VARCHAR(50) NOT NULL,
    run_id              UUID REFERENCES production_runs(id) ON DELETE SET NULL,
    start_time          TIMESTAMPTZ NOT NULL,
    end_time            TIMESTAMPTZ,
    duration_minutes    NUMERIC GENERATED ALWAYS AS
        (EXTRACT(EPOCH FROM (end_time - start_time)) / 60.0) STORED,
    category            VARCHAR(50),
    subcategory         VARCHAR(100),
    equipment_id        VARCHAR(100),
    description         TEXT,
    root_cause          TEXT,
    root_cause_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    shift               VARCHAR(20),
    reported_by         VARCHAR(255),
    metadata            JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_downtime_line_time  ON downtime_events (line_id, start_time DESC);
CREATE INDEX idx_downtime_category   ON downtime_events (category);
CREATE INDEX idx_downtime_equipment  ON downtime_events (equipment_id);

CREATE TABLE quality_results (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    line_id         VARCHAR(50) NOT NULL,
    run_id          UUID REFERENCES production_runs(id) ON DELETE SET NULL,
    test_type       VARCHAR(50) NOT NULL,
    test_time       TIMESTAMPTZ NOT NULL,
    sample_id       VARCHAR(100),
    result          VARCHAR(20) NOT NULL,
    measurements    JSONB DEFAULT '{}'::jsonb,
    specification   JSONB DEFAULT '{}'::jsonb,
    notes           TEXT,
    tested_by       VARCHAR(255),
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT quality_results_result_chk
      CHECK (result IN ('pass','fail','marginal','retest'))
);
CREATE INDEX idx_quality_line_time  ON quality_results (line_id, test_time DESC);
CREATE INDEX idx_quality_run        ON quality_results (run_id);
CREATE INDEX idx_quality_type_res   ON quality_results (test_type, result);

CREATE TABLE defect_events (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    line_id             VARCHAR(50) NOT NULL,
    run_id              UUID REFERENCES production_runs(id) ON DELETE SET NULL,
    defect_type         VARCHAR(50) NOT NULL,
    detected_time       TIMESTAMPTZ NOT NULL,
    detection_method    VARCHAR(50),
    severity            VARCHAR(20),
    quantity_affected   NUMERIC,
    description         TEXT,
    root_cause          TEXT,
    root_cause_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    corrective_action   TEXT,
    status              VARCHAR(20) NOT NULL DEFAULT 'open',
    resolved_by         VARCHAR(255),
    resolved_at         TIMESTAMPTZ,
    metadata            JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT defect_events_status_chk
      CHECK (status IN ('open','investigating','resolved','unresolved','closed')),
    CONSTRAINT defect_events_severity_chk
      CHECK (severity IS NULL OR severity IN ('minor','major','critical'))
);
CREATE INDEX idx_defects_line_time  ON defect_events (line_id, detected_time DESC);
CREATE INDEX idx_defects_type       ON defect_events (defect_type);
CREATE INDEX idx_defects_status     ON defect_events (status);

-- =============================================================================
-- GROUP 5: USER PROFILES & PERMISSIONS  (created before conversations FK)
-- =============================================================================

CREATE TABLE user_profiles (
    id                              VARCHAR(255) PRIMARY KEY,
    display_name                    VARCHAR(255),
    role_primary                    VARCHAR(50),
    roles_additional                TEXT[] DEFAULT '{}',
    department                      VARCHAR(100),
    shift_default                   VARCHAR(20),
    lines_primary                   TEXT[] DEFAULT '{}',
    equipment_focus                 TEXT[] DEFAULT '{}',
    response_detail_level           VARCHAR(20) NOT NULL DEFAULT 'standard',
    response_style                  VARCHAR(20) NOT NULL DEFAULT 'balanced',
    include_tag_values              BOOLEAN NOT NULL DEFAULT TRUE,
    include_ml_details              BOOLEAN NOT NULL DEFAULT FALSE,
    include_source_excerpts         BOOLEAN NOT NULL DEFAULT TRUE,
    default_historian_window_minutes INTEGER NOT NULL DEFAULT 60,
    auto_include_alarms             BOOLEAN NOT NULL DEFAULT TRUE,
    preferred_units                 VARCHAR(20) NOT NULL DEFAULT 'imperial',
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at                  TIMESTAMPTZ,
    CONSTRAINT user_profiles_role_chk
      CHECK (role_primary IS NULL OR role_primary IN
        ('operator','engineer','maintenance','quality','supervisor','manager','admin')),
    CONSTRAINT user_profiles_detail_chk
      CHECK (response_detail_level IN ('brief','standard','detailed','technical')),
    CONSTRAINT user_profiles_style_chk
      CHECK (response_style IN ('direct','balanced','explanatory'))
);

CREATE TABLE user_permissions (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    role        VARCHAR(50) NOT NULL,
    permission  VARCHAR(100) NOT NULL,
    granted     BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (role, permission)
);

-- =============================================================================
-- GROUP 3: CONVERSATIONS, MESSAGES & FEEDBACK-LEARNING LAYER
-- =============================================================================

CREATE TABLE conversations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      VARCHAR(255),
    user_id         VARCHAR(255) REFERENCES user_profiles(id) ON DELETE SET NULL,
    line_id         VARCHAR(50) NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    message_count   INTEGER NOT NULL DEFAULT 0,
    metadata        JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX idx_conv_user_time     ON conversations (user_id, started_at DESC);
CREATE INDEX idx_conv_session       ON conversations (session_id);

CREATE TABLE messages (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id     UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role                VARCHAR(20) NOT NULL,
    content             TEXT NOT NULL,
    sources             JSONB DEFAULT '[]'::jsonb,
    confidence          VARCHAR(30),
    context_snapshot    JSONB DEFAULT '{}'::jsonb,
    prompt_version      VARCHAR(50),
    model_name          VARCHAR(100),
    model_params        JSONB DEFAULT '{}'::jsonb,
    token_usage         JSONB DEFAULT '{}'::jsonb,
    retrieval_scores    JSONB DEFAULT '[]'::jsonb,
    rules_matched       JSONB DEFAULT '[]'::jsonb,
    memories_used       JSONB DEFAULT '[]'::jsonb,
    latency_ms          INTEGER,
    latency_breakdown   JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT messages_role_chk
      CHECK (role IN ('user','assistant','system')),
    CONSTRAINT messages_confidence_chk
      CHECK (confidence IS NULL OR confidence IN
        ('confirmed','likely','hypothesis','insufficient_evidence'))
);
CREATE INDEX idx_messages_conv_time ON messages (conversation_id, created_at);

CREATE TABLE message_feedback (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id      UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id         VARCHAR(255) REFERENCES user_profiles(id) ON DELETE SET NULL,
    signal_type     VARCHAR(50) NOT NULL,
    signal_value    VARCHAR(20) NOT NULL,
    comment         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT message_feedback_signal_type_chk
      CHECK (signal_type IN (
        'usefulness','correctness','completeness','source_relevance',
        'root_cause_confirmed','root_cause_rejected',
        'recommendation_acted_on','recommendation_ignored',
        'recommendation_helped','recommendation_did_not_help')),
    CONSTRAINT message_feedback_signal_value_chk
      CHECK (signal_value IN ('positive','negative','neutral'))
);
CREATE INDEX idx_feedback_message ON message_feedback (message_id);
CREATE INDEX idx_feedback_user    ON message_feedback (user_id, created_at DESC);
CREATE INDEX idx_feedback_signal  ON message_feedback (signal_type, signal_value);

CREATE TABLE user_corrections (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id          UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id             VARCHAR(255) REFERENCES user_profiles(id) ON DELETE SET NULL,
    correction_type     VARCHAR(50) NOT NULL,
    original_claim      TEXT,
    corrected_claim     TEXT NOT NULL,
    supporting_evidence TEXT,
    status              VARCHAR(20) NOT NULL DEFAULT 'submitted',
    reviewed_by         VARCHAR(255),
    review_date         TIMESTAMPTZ,
    review_notes        TEXT,
    created_memory_id   UUID,                 -- FK added after line_memory exists
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT corrections_type_chk
      CHECK (correction_type IN
        ('factual_error','wrong_root_cause','missing_context',
         'wrong_equipment','outdated_info','misleading_conclusion','other')),
    CONSTRAINT corrections_status_chk
      CHECK (status IN ('submitted','reviewed','accepted','rejected'))
);
CREATE INDEX idx_corrections_message ON user_corrections (message_id);
CREATE INDEX idx_corrections_status  ON user_corrections (status);

CREATE TABLE outcome_linkages (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id      UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    outcome_type    VARCHAR(50) NOT NULL,
    outcome_id      UUID NOT NULL,
    outcome_table   VARCHAR(50) NOT NULL,
    alignment       VARCHAR(20) NOT NULL,
    linked_by       VARCHAR(255),
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT outcome_linkages_table_chk
      CHECK (outcome_table IN ('quality_results','defect_events','downtime_events')),
    CONSTRAINT outcome_linkages_alignment_chk
      CHECK (alignment IN ('confirmed','contradicted','partial','unrelated'))
);
CREATE INDEX idx_outcome_message ON outcome_linkages (message_id);
CREATE INDEX idx_outcome_target  ON outcome_linkages (outcome_table, outcome_id);

-- =============================================================================
-- GROUP 4: DURABLE LINE MEMORY  (and memory_candidates staging)
-- =============================================================================

CREATE TABLE line_memory (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    line_id             VARCHAR(50) NOT NULL,
    category            VARCHAR(50) NOT NULL,
    content             TEXT NOT NULL,
    source              VARCHAR(255),
    confidence          VARCHAR(20) NOT NULL DEFAULT 'low',
    status              VARCHAR(20) NOT NULL DEFAULT 'draft',
    embedding           VECTOR(384),
    tags                TEXT[] DEFAULT '{}',
    equipment_ids       TEXT[] DEFAULT '{}',
    applies_to_products TEXT[] DEFAULT '{}',
    created_by          VARCHAR(255),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_by         VARCHAR(255),
    review_date         TIMESTAMPTZ,
    approved_by         VARCHAR(255),
    approved_date       TIMESTAMPTZ,
    deprecated_at       TIMESTAMPTZ,
    deprecated_reason   TEXT,
    deprecated_by       VARCHAR(255),
    challenge_count     INTEGER NOT NULL DEFAULT 0,
    last_challenged_at  TIMESTAMPTZ,
    access_count        INTEGER NOT NULL DEFAULT 0,
    last_accessed       TIMESTAMPTZ,
    superseded_by       UUID REFERENCES line_memory(id) ON DELETE SET NULL,
    CONSTRAINT line_memory_category_chk
      CHECK (category IN
        ('equipment_fact','process_fact','failure_pattern',
         'troubleshooting_heuristic','unresolved_investigation',
         'user_correction','operating_tip')),
    CONSTRAINT line_memory_confidence_chk
      CHECK (confidence IN ('low','medium','high')),
    CONSTRAINT line_memory_status_chk
      CHECK (status IN ('draft','reviewed','approved','deprecated','challenged'))
);
CREATE INDEX idx_memory_line_status   ON line_memory (line_id, status);
CREATE INDEX idx_memory_embedding     ON line_memory
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 50);
CREATE INDEX idx_memory_tags          ON line_memory USING gin (tags);
CREATE INDEX idx_memory_equipment     ON line_memory USING gin (equipment_ids);

ALTER TABLE user_corrections
    ADD CONSTRAINT user_corrections_memory_fk
    FOREIGN KEY (created_memory_id) REFERENCES line_memory(id) ON DELETE SET NULL;

CREATE TABLE memory_candidates (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_type         VARCHAR(50) NOT NULL,
    source_message_ids  UUID[] DEFAULT '{}',
    source_feedback_ids UUID[] DEFAULT '{}',
    source_correction_id UUID REFERENCES user_corrections(id) ON DELETE SET NULL,
    source_outcome_ids  UUID[] DEFAULT '{}',
    proposed_content    TEXT NOT NULL,
    proposed_category   VARCHAR(50) NOT NULL,
    confidence_score    NUMERIC(3,2) NOT NULL DEFAULT 0.00,
    status              VARCHAR(20) NOT NULL DEFAULT 'proposed',
    promoted_memory_id  UUID REFERENCES line_memory(id) ON DELETE SET NULL,
    reviewed_by         VARCHAR(255),
    review_date         TIMESTAMPTZ,
    review_notes        TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT memory_candidates_source_type_chk
      CHECK (source_type IN
        ('repeated_pattern','confirmed_correction',
         'confirmed_outcome','engineer_nominated')),
    CONSTRAINT memory_candidates_status_chk
      CHECK (status IN ('proposed','under_review','promoted','rejected'))
);
CREATE INDEX idx_candidates_status ON memory_candidates (status);

-- =============================================================================
-- GROUP 6: ML FOUNDATION (created now, populated Phase 4+)
-- =============================================================================

CREATE TABLE feature_definitions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    version         VARCHAR(50) NOT NULL UNIQUE,
    description     TEXT,
    feature_specs   JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(255)
);

CREATE TABLE ml_models (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_name              VARCHAR(100) NOT NULL,
    model_version           VARCHAR(50) NOT NULL,
    model_type              VARCHAR(50) NOT NULL,
    feature_set_version     VARCHAR(50),
    training_data_start     TIMESTAMPTZ,
    training_data_end       TIMESTAMPTZ,
    training_row_count      INTEGER,
    holdout_row_count       INTEGER,
    metrics                 JSONB DEFAULT '{}'::jsonb,
    hyperparameters         JSONB DEFAULT '{}'::jsonb,
    artifact_path           VARCHAR(500),
    is_active               BOOLEAN NOT NULL DEFAULT FALSE,
    activated_at            TIMESTAMPTZ,
    activated_by            VARCHAR(255),
    notes                   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (model_name, model_version)
);
CREATE INDEX idx_models_active ON ml_models (model_name, is_active) WHERE is_active = TRUE;

CREATE TABLE ml_predictions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_id            UUID NOT NULL REFERENCES ml_models(id) ON DELETE CASCADE,
    run_id              UUID REFERENCES production_runs(id) ON DELETE SET NULL,
    event_id            UUID,
    event_type          VARCHAR(50),
    prediction          JSONB NOT NULL,
    explanation         JSONB DEFAULT '{}'::jsonb,
    input_features      JSONB DEFAULT '{}'::jsonb,
    actual_outcome      VARCHAR(50),
    outcome_recorded_at TIMESTAMPTZ,
    message_id          UUID REFERENCES messages(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_predictions_model ON ml_predictions (model_id, created_at DESC);
CREATE INDEX idx_predictions_run   ON ml_predictions (run_id);

CREATE TABLE feature_snapshots (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id              UUID REFERENCES production_runs(id) ON DELETE SET NULL,
    event_id            UUID,
    event_type          VARCHAR(50),
    feature_set_version VARCHAR(50) NOT NULL,
    features            JSONB NOT NULL,
    label               VARCHAR(50),
    label_source        VARCHAR(100),
    window_start        TIMESTAMPTZ,
    window_end          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_features_set      ON feature_snapshots (feature_set_version);
CREATE INDEX idx_features_event    ON feature_snapshots (event_type, event_id);

-- =============================================================================
-- GROUP 7: CONFIGURATION & VERSIONING
-- =============================================================================

CREATE TABLE prompt_versions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    prompt_name     VARCHAR(100) NOT NULL,
    version         VARCHAR(50) NOT NULL,
    content         TEXT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    activated_at    TIMESTAMPTZ,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(255),
    UNIQUE (prompt_name, version)
);
CREATE INDEX idx_prompts_active ON prompt_versions (prompt_name, is_active) WHERE is_active = TRUE;

CREATE TABLE business_rules (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    rule_name       VARCHAR(100) NOT NULL,
    line_id         VARCHAR(50) NOT NULL,
    condition       JSONB NOT NULL,
    conclusion      TEXT NOT NULL,
    severity        VARCHAR(20) NOT NULL DEFAULT 'info',
    category        VARCHAR(50),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    version         VARCHAR(50),
    created_by      VARCHAR(255),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT business_rules_severity_chk
      CHECK (severity IN ('info','warning','critical'))
);
CREATE INDEX idx_rules_line_active ON business_rules (line_id, is_active) WHERE is_active = TRUE;

-- =============================================================================
-- GROUP 8: AUDIT (append-only)
-- =============================================================================

CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    event_type      VARCHAR(50) NOT NULL,
    user_id         VARCHAR(255),
    session_id      VARCHAR(255),
    entity_type     VARCHAR(50),
    entity_id       VARCHAR(255),
    details         JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_audit_event_time ON audit_log (event_type, created_at DESC);
CREATE INDEX idx_audit_user_time  ON audit_log (user_id, created_at DESC);
CREATE INDEX idx_audit_entity     ON audit_log (entity_type, entity_id);

-- =============================================================================
-- AUXILIARY: chunk_quality_signals (rolling aggregate for retrieval re-ranking)
-- =============================================================================

CREATE TABLE chunk_quality_signals (
    chunk_id            UUID PRIMARY KEY REFERENCES document_chunks(id) ON DELETE CASCADE,
    positive_count      INTEGER NOT NULL DEFAULT 0,
    negative_count      INTEGER NOT NULL DEFAULT 0,
    cited_count         INTEGER NOT NULL DEFAULT 0,
    quality_score       NUMERIC(4,3) NOT NULL DEFAULT 0.000,
    last_updated        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- AUXILIARY: updated_at trigger
-- =============================================================================

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    t TEXT;
    tables TEXT[] := ARRAY[
        'documents','production_runs','downtime_events','quality_results',
        'defect_events','user_profiles','business_rules'
    ];
BEGIN
    FOREACH t IN ARRAY tables LOOP
        EXECUTE format(
            'CREATE TRIGGER trg_%I_updated_at BEFORE UPDATE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION set_updated_at()', t, t);
    END LOOP;
END $$;
