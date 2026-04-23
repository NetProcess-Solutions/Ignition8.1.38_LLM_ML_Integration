-- =============================================================================
-- Reference data: role permissions matrix.
-- Domain data (user profiles, line memory, rules) is seeded by
-- scripts/seed_initial_data.py after the service is up so we can use the
-- embedding model.
-- =============================================================================

INSERT INTO user_permissions (role, permission, granted) VALUES
    ('operator',   'chat.ask',                  TRUE),
    ('operator',   'feedback.usefulness',       TRUE),
    ('operator',   'correction.submit',         TRUE),
    ('operator',   'memory.view_approved',      TRUE),

    ('engineer',   'chat.ask',                  TRUE),
    ('engineer',   'feedback.usefulness',       TRUE),
    ('engineer',   'feedback.correctness',      TRUE),
    ('engineer',   'feedback.root_cause',       TRUE),
    ('engineer',   'correction.submit',         TRUE),
    ('engineer',   'correction.review',         TRUE),
    ('engineer',   'memory.view_approved',      TRUE),
    ('engineer',   'memory.view_drafts',        TRUE),
    ('engineer',   'memory.create',             TRUE),
    ('engineer',   'memory.approve',            TRUE),
    ('engineer',   'memory.deprecate',          TRUE),
    ('engineer',   'ml.view_predictions',       TRUE),
    ('engineer',   'admin.audit_view',          TRUE),

    ('maintenance','chat.ask',                  TRUE),
    ('maintenance','feedback.usefulness',       TRUE),
    ('maintenance','feedback.correctness',      TRUE),
    ('maintenance','feedback.root_cause',       TRUE),
    ('maintenance','correction.submit',         TRUE),
    ('maintenance','memory.view_approved',      TRUE),
    ('maintenance','memory.create',             TRUE),

    ('quality',    'chat.ask',                  TRUE),
    ('quality',    'feedback.usefulness',       TRUE),
    ('quality',    'feedback.correctness',      TRUE),
    ('quality',    'feedback.root_cause',       TRUE),
    ('quality',    'correction.submit',         TRUE),
    ('quality',    'correction.review',         TRUE),
    ('quality',    'memory.view_approved',      TRUE),
    ('quality',    'memory.create',             TRUE),
    ('quality',    'memory.approve',            TRUE),
    ('quality',    'ml.view_predictions',       TRUE),

    ('supervisor', 'chat.ask',                  TRUE),
    ('supervisor', 'feedback.usefulness',       TRUE),
    ('supervisor', 'correction.submit',         TRUE),
    ('supervisor', 'memory.view_approved',      TRUE),
    ('supervisor', 'ml.view_predictions',       TRUE),
    ('supervisor', 'admin.audit_view',          TRUE),

    ('manager',    'chat.ask',                  TRUE),
    ('manager',    'feedback.usefulness',       TRUE),
    ('manager',    'memory.view_approved',      TRUE),
    ('manager',    'ml.view_predictions',       TRUE),

    ('admin',      'chat.ask',                  TRUE),
    ('admin',      'feedback.usefulness',       TRUE),
    ('admin',      'feedback.correctness',      TRUE),
    ('admin',      'feedback.root_cause',       TRUE),
    ('admin',      'correction.submit',         TRUE),
    ('admin',      'correction.review',         TRUE),
    ('admin',      'memory.view_approved',      TRUE),
    ('admin',      'memory.view_drafts',        TRUE),
    ('admin',      'memory.create',             TRUE),
    ('admin',      'memory.approve',            TRUE),
    ('admin',      'memory.deprecate',          TRUE),
    ('admin',      'ml.view_predictions',       TRUE),
    ('admin',      'admin.ingest',              TRUE),
    ('admin',      'admin.retrain',             TRUE),
    ('admin',      'admin.audit_view',          TRUE)
ON CONFLICT (role, permission) DO NOTHING;
