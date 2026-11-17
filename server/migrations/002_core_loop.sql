-- Additive only: preserve historical workspaces, answers and assessments.
ALTER TABLE learning_tasks ADD COLUMN IF NOT EXISTS source_exam_id INTEGER REFERENCES exams(id) ON DELETE SET NULL;
ALTER TABLE learning_tasks ADD COLUMN IF NOT EXISTS source_exam_question_id INTEGER REFERENCES exam_questions(id) ON DELETE SET NULL;
ALTER TABLE learning_tasks ADD COLUMN IF NOT EXISTS source_evidence_id INTEGER REFERENCES evidence(id) ON DELETE SET NULL;
ALTER TABLE learning_tasks ADD COLUMN IF NOT EXISTS source_criterion TEXT;
ALTER TABLE learning_tasks ADD COLUMN IF NOT EXISTS gap_type VARCHAR(32);
ALTER TABLE exams ADD COLUMN IF NOT EXISTS retest_task_id INTEGER REFERENCES learning_tasks(id) ON DELETE SET NULL;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(120);
CREATE UNIQUE INDEX IF NOT EXISTS uq_exams_workspace_request ON exams(workspace_id, idempotency_key);
CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_observation_gap
    ON learning_tasks(workspace_id, source_exam_question_id, source_criterion)
    WHERE kind <> 'RETEST' AND source_exam_question_id IS NOT NULL;
