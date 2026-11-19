CREATE TABLE IF NOT EXISTS study_sessions (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    title VARCHAR(160) NOT NULL,
    status VARCHAR(16) NOT NULL CONSTRAINT ck_study_session_status CHECK (status IN ('active','ended')),
    context JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_study_session_workspace ON study_sessions(workspace_id,updated_at);
CREATE TABLE IF NOT EXISTS study_messages (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES study_sessions(id) ON DELETE CASCADE,
    request_id VARCHAR(120) NOT NULL,
    role VARCHAR(16) NOT NULL CONSTRAINT ck_study_message_role CHECK (role IN ('user','assistant')),
    project_id INTEGER,
    content TEXT NOT NULL,
    status VARCHAR(16) NOT NULL CONSTRAINT ck_study_message_status CHECK (status IN ('generating','completed','interrupted','failed')),
    action VARCHAR(24) NOT NULL,
    sources JSONB NOT NULL,
    error VARCHAR(120),
    model VARCHAR(200),
    attempt INTEGER NOT NULL,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_study_message_request UNIQUE(session_id,request_id,role)
);
CREATE INDEX IF NOT EXISTS ix_study_message_history ON study_messages(session_id,id);
