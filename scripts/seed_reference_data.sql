-- =============================================================================
-- IgnitionChatbot v3.0 reference data seed
-- Runs once at first DB init via docker-entrypoint-initdb.d/02_*.
-- Application-data seeds (memories, prompts, rules) live in
-- service/scripts/seed_initial_data.py and run after the embedding model
-- is available.
-- =============================================================================

-- ----------------------------------------------------------------------------
-- failure_modes (closed enum, design §4.3 taxonomy discipline note)
-- Adding a new mode here is the only way defect_events.fm_code can
-- accept it (FK is RESTRICT). Initial set drawn from the design's
-- examples; expand via Task 9 from real DELAM/QR records.
-- ----------------------------------------------------------------------------
INSERT INTO failure_modes (fm_code, label, defect_type, description) VALUES
    ('delam_hotpull',       'Delamination (hot pull)',         'delamination',         'Bond failure detected on hot-pull lab test'),
    ('delam_cold',          'Delamination (cold)',             'delamination',         'Bond failure visible at ambient temperature'),
    ('off_tenter_edge_fold','Off-tenter, edge fold',           'off_tenter',           'Edge fold under tenter clip'),
    ('bubble_adhesive',     'Bubble (adhesive layer)',         'bubbling',             'Air entrapment in adhesive (Tillitson) layer'),
    ('bubble_precoat',      'Bubble (precoat layer)',          'bubbling',             'Air entrapment in precoat (DirectApplicator) layer'),
    ('streak_frontback',    'Streak (front-back)',             'discoloration',        'Front-to-back streak in coating'),
    ('cw_out_of_spec',      'Coating weight out of spec',      'thickness_deviation',  'Inline or lab CW measurement outside spec'),
    ('contamination_other', 'Contamination (other)',           'contamination',        'Contamination not classified elsewhere'),
    ('other',               'Other / unclassified',            'other',                'Catch-all; should be re-classified by engineer review')
ON CONFLICT (fm_code) DO NOTHING;

-- ----------------------------------------------------------------------------
-- user_permissions matrix (design §4.6)
-- ----------------------------------------------------------------------------
INSERT INTO user_permissions (role, permission, granted) VALUES
    -- chat.ask: everyone
    ('operator',    'chat.ask',               TRUE),
    ('engineer',    'chat.ask',               TRUE),
    ('maintenance', 'chat.ask',               TRUE),
    ('quality',     'chat.ask',               TRUE),
    ('supervisor',  'chat.ask',               TRUE),
    ('manager',     'chat.ask',               TRUE),
    ('admin',       'chat.ask',               TRUE),
    -- feedback.usefulness: everyone
    ('operator',    'feedback.usefulness',    TRUE),
    ('engineer',    'feedback.usefulness',    TRUE),
    ('maintenance', 'feedback.usefulness',    TRUE),
    ('quality',     'feedback.usefulness',    TRUE),
    ('supervisor',  'feedback.usefulness',    TRUE),
    ('manager',     'feedback.usefulness',    TRUE),
    ('admin',       'feedback.usefulness',    TRUE),
    -- feedback.correctness: not operators
    ('engineer',    'feedback.correctness',   TRUE),
    ('maintenance', 'feedback.correctness',   TRUE),
    ('quality',     'feedback.correctness',   TRUE),
    ('supervisor',  'feedback.correctness',   TRUE),
    ('manager',     'feedback.correctness',   TRUE),
    ('admin',       'feedback.correctness',   TRUE),
    -- feedback.root_cause
    ('engineer',    'feedback.root_cause',    TRUE),
    ('maintenance', 'feedback.root_cause',    TRUE),
    ('quality',     'feedback.root_cause',    TRUE),
    ('admin',       'feedback.root_cause',    TRUE),
    -- correction.submit: everyone
    ('operator',    'correction.submit',      TRUE),
    ('engineer',    'correction.submit',      TRUE),
    ('maintenance', 'correction.submit',      TRUE),
    ('quality',     'correction.submit',      TRUE),
    ('supervisor',  'correction.submit',      TRUE),
    ('manager',     'correction.submit',      TRUE),
    ('admin',       'correction.submit',      TRUE),
    -- correction.review: engineer/quality/admin
    ('engineer',    'correction.review',      TRUE),
    ('quality',     'correction.review',      TRUE),
    ('admin',       'correction.review',      TRUE),
    -- memory
    ('operator',    'memory.view_approved',   TRUE),
    ('engineer',    'memory.view_approved',   TRUE),
    ('maintenance', 'memory.view_approved',   TRUE),
    ('quality',     'memory.view_approved',   TRUE),
    ('supervisor',  'memory.view_approved',   TRUE),
    ('manager',     'memory.view_approved',   TRUE),
    ('admin',       'memory.view_approved',   TRUE),
    ('engineer',    'memory.view_drafts',     TRUE),
    ('admin',       'memory.view_drafts',     TRUE),
    ('engineer',    'memory.create',          TRUE),
    ('maintenance', 'memory.create',          TRUE),
    ('quality',     'memory.create',          TRUE),
    ('admin',       'memory.create',          TRUE),
    ('engineer',    'memory.approve',         TRUE),
    ('quality',     'memory.approve',         TRUE),
    ('admin',       'memory.approve',         TRUE),
    ('engineer',    'memory.deprecate',       TRUE),
    ('admin',       'memory.deprecate',       TRUE),
    -- ml.view_predictions
    ('engineer',    'ml.view_predictions',    TRUE),
    ('quality',     'ml.view_predictions',    TRUE),
    ('supervisor',  'ml.view_predictions',    TRUE),
    ('manager',     'ml.view_predictions',    TRUE),
    ('admin',       'ml.view_predictions',    TRUE),
    -- admin
    ('admin',       'admin.ingest',           TRUE),
    ('admin',       'admin.retrain',          TRUE),
    ('engineer',    'admin.audit_view',       TRUE),
    ('supervisor',  'admin.audit_view',       TRUE),
    ('admin',       'admin.audit_view',       TRUE)
ON CONFLICT (role, permission) DO NOTHING;
