CREATE TABLE IF NOT EXISTS project_resume_drafts (
    workspace_id integer NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    project_id integer NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    revision integer NOT NULL CONSTRAINT ck_project_resume_revision CHECK (revision > 0),
    contribution text NOT NULL,
    outcome text NOT NULL DEFAULT '',
    content text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, project_id)
);

-- An earlier local build may already have created the table at startup.
ALTER TABLE project_resume_drafts ADD COLUMN IF NOT EXISTS outcome text NOT NULL DEFAULT '';
