-- Additive: historical questions/imports remain untouched.
ALTER TABLE questions ADD COLUMN IF NOT EXISTS generation_meta JSONB;
ALTER TABLE import_items ADD COLUMN IF NOT EXISTS generation_meta JSONB;
CREATE TABLE IF NOT EXISTS question_generations (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    kind VARCHAR(24) NOT NULL,
    mode VARCHAR(24) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'READY',
    request_key VARCHAR(120) NOT NULL,
    request_json JSONB NOT NULL,
    input_snapshot JSONB NOT NULL,
    prompt_text TEXT NOT NULL DEFAULT '',
    raw_response TEXT,
    error TEXT,
    result_meta JSONB,
    import_session_id INTEGER REFERENCES import_sessions(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_generation_request UNIQUE (workspace_id, request_key)
);
