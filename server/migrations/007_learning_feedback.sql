-- Add learning links; grading remains in assessment_scoring_* and exam_answer_revisions.

CREATE TABLE IF NOT EXISTS learning_goals (
	id SERIAL NOT NULL,
	workspace_id INTEGER NOT NULL,
	source_task_id INTEGER,
	origin_key VARCHAR(64) NOT NULL,
	origin_refs JSONB NOT NULL,
	skill_ids JSONB NOT NULL,
	gap_type VARCHAR(64) NOT NULL,
	target_behavior TEXT NOT NULL,
	benchmark_version JSONB NOT NULL,
	success_criteria JSONB NOT NULL,
	success_criteria_version VARCHAR(64) NOT NULL,
	evidence_status VARCHAR(32) NOT NULL,
	status VARCHAR(24) NOT NULL,
	plan_revision INTEGER NOT NULL,
	policy_version VARCHAR(64) NOT NULL,
	verification JSONB NOT NULL,
	meta JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_learning_goal_origin UNIQUE (workspace_id, origin_key),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
	FOREIGN KEY(source_task_id) REFERENCES learning_tasks (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_learning_goals_source_task_id ON learning_goals (source_task_id);

CREATE INDEX IF NOT EXISTS ix_learning_goals_workspace_id ON learning_goals (workspace_id);

CREATE TABLE IF NOT EXISTS learning_activities (
	id SERIAL NOT NULL,
	workspace_id INTEGER NOT NULL,
	goal_id INTEGER NOT NULL,
	kind VARCHAR(24) NOT NULL,
	title TEXT NOT NULL,
	status VARCHAR(24) NOT NULL,
	position INTEGER NOT NULL,
	version INTEGER NOT NULL,
	content_version VARCHAR(64) NOT NULL,
	snapshot JSONB NOT NULL,
	question_id INTEGER,
	latest_submission_id INTEGER,
	meta JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_learning_activity_content UNIQUE (goal_id, content_version),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
	FOREIGN KEY(goal_id) REFERENCES learning_goals (id) ON DELETE CASCADE,
	FOREIGN KEY(question_id) REFERENCES questions (id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS ix_learning_activities_goal_id ON learning_activities (goal_id);

CREATE INDEX IF NOT EXISTS ix_learning_activities_workspace_id ON learning_activities (workspace_id);

CREATE TABLE IF NOT EXISTS learning_submissions (
	id SERIAL NOT NULL,
	workspace_id INTEGER NOT NULL,
	goal_id INTEGER NOT NULL,
	activity_id INTEGER NOT NULL,
	revision INTEGER NOT NULL,
	parent_submission_id INTEGER,
	request_key VARCHAR(120) NOT NULL,
	payload JSONB NOT NULL,
	assistance JSONB NOT NULL,
	input_hash VARCHAR(64) NOT NULL,
	activity_version INTEGER NOT NULL,
	exam_id INTEGER,
	answer_revision_id INTEGER,
	scoring_run_id INTEGER,
	status VARCHAR(24) NOT NULL,
	meta JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_learning_submission_request UNIQUE (workspace_id, activity_id, request_key),
	CONSTRAINT uq_learning_submission_revision UNIQUE (activity_id, revision),
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
	FOREIGN KEY(goal_id) REFERENCES learning_goals (id) ON DELETE CASCADE,
	FOREIGN KEY(activity_id) REFERENCES learning_activities (id) ON DELETE CASCADE,
	FOREIGN KEY(parent_submission_id) REFERENCES learning_submissions (id) ON DELETE SET NULL,
	UNIQUE (exam_id),
	FOREIGN KEY(exam_id) REFERENCES exams (id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
	FOREIGN KEY(answer_revision_id) REFERENCES exam_answer_revisions (id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
	UNIQUE (scoring_run_id),
	FOREIGN KEY(scoring_run_id) REFERENCES assessment_scoring_runs (id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS ix_learning_submissions_activity_id ON learning_submissions (activity_id);

CREATE INDEX IF NOT EXISTS ix_learning_submissions_goal_id ON learning_submissions (goal_id);

CREATE INDEX IF NOT EXISTS ix_learning_submissions_workspace_id ON learning_submissions (workspace_id);
