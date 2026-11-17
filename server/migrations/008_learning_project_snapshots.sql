-- Additive: immutable selected-source previews. bootstrap creates missing tables;
-- this idempotent migration also supports explicit SQL-first upgrade checks.
CREATE TABLE IF NOT EXISTS learning_project_snapshots (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    goal_id INTEGER NOT NULL REFERENCES learning_goals(id) ON DELETE CASCADE,
    activity_id INTEGER NOT NULL REFERENCES learning_activities(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    content_hash VARCHAR(64) NOT NULL,
    activity_version INTEGER NOT NULL,
    snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_learning_project_snapshot UNIQUE(workspace_id, activity_id, content_hash)
);
CREATE INDEX IF NOT EXISTS ix_learning_project_snapshots_workspace_id ON learning_project_snapshots(workspace_id);
CREATE INDEX IF NOT EXISTS ix_learning_project_snapshots_goal_id ON learning_project_snapshots(goal_id);
CREATE INDEX IF NOT EXISTS ix_learning_project_snapshots_activity_id ON learning_project_snapshots(activity_id);
CREATE INDEX IF NOT EXISTS ix_learning_project_snapshots_project_id ON learning_project_snapshots(project_id);
