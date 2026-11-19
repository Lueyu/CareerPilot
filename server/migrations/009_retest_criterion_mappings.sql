CREATE TABLE IF NOT EXISTS retest_criterion_mappings (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    rubric_version VARCHAR(100) NOT NULL,
    criterion_id VARCHAR(64) NOT NULL,
    behavior_key VARCHAR(128) NOT NULL,
    behavior_version INTEGER NOT NULL,
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_retest_criterion_mapping UNIQUE (workspace_id, question_id, rubric_version, criterion_id),
    CONSTRAINT uq_retest_rubric_behavior UNIQUE (workspace_id, question_id, rubric_version, behavior_key, behavior_version),
    CONSTRAINT ck_retest_behavior_version_positive CHECK (behavior_version > 0)
);
CREATE INDEX IF NOT EXISTS ix_retest_criterion_mappings_question_id ON retest_criterion_mappings(question_id);
