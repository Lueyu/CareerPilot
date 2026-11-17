-- Additive only: keep scans, source projects, examinations and prior memories.
ALTER TABLE exams ADD COLUMN IF NOT EXISTS project_assessment JSONB;
CREATE TABLE IF NOT EXISTS project_analyses (
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    request_key VARCHAR(120) NOT NULL,
    focus TEXT NOT NULL DEFAULT '',
    status VARCHAR(16) NOT NULL DEFAULT 'READY',
    source_revision VARCHAR(64),
    snapshot JSONB,
    report JSONB,
    result_meta JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    CONSTRAINT uq_project_analysis_request UNIQUE (workspace_id, request_key)
);
CREATE INDEX IF NOT EXISTS ix_project_analyses_project_id ON project_analyses(project_id);
CREATE INDEX IF NOT EXISTS ix_project_analyses_workspace_id ON project_analyses(workspace_id);
CREATE INDEX IF NOT EXISTS ix_memory_project_analysis ON memory_items ((meta->>'project_id'), status)
    WHERE mtype IN ('ProjectUnderstanding', 'ProjectIssue');
