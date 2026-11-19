CREATE TABLE IF NOT EXISTS study_practices (
    id INTEGER PRIMARY KEY REFERENCES learning_activities(id) ON DELETE CASCADE,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    session_id INTEGER NOT NULL REFERENCES study_sessions(id) ON DELETE CASCADE,
    card_id INTEGER REFERENCES study_cards(id) ON DELETE SET NULL,
    skill_id INTEGER REFERENCES skills(id),
    kind VARCHAR(16) NOT NULL,
    knowledge_point VARCHAR(180) NOT NULL,
    request_key VARCHAR(120) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    source JSONB NOT NULL,
    retest_exam_id INTEGER UNIQUE REFERENCES exams(id) ON DELETE SET NULL,
    CONSTRAINT uq_study_practice_request UNIQUE (session_id, request_key)
);
CREATE INDEX IF NOT EXISTS ix_study_practices_workspace_id ON study_practices(workspace_id);
CREATE INDEX IF NOT EXISTS ix_study_practices_session_id ON study_practices(session_id);
