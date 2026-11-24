CREATE TABLE IF NOT EXISTS feedback_submissions (
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    submission_id UUID NOT NULL,
    message_digest VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,
    PRIMARY KEY (workspace_id, submission_id),
    CONSTRAINT feedback_submission_status
        CHECK (status IN ('in_progress', 'sent', 'rejected', 'retryable', 'unknown'))
);

CREATE TABLE IF NOT EXISTS feedback_attempts (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS ix_feedback_attempts_created_at ON feedback_attempts(created_at);
