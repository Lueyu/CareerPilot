-- Additive only. New tables are registered by bootstrap/create_all, like 005.
-- Backward script is intentionally outside the automatic migrations directory.
ALTER TABLE exams ADD COLUMN IF NOT EXISTS scoring_generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE exams ADD COLUMN IF NOT EXISTS active_scoring_run_id INTEGER;
ALTER TABLE exam_questions ADD COLUMN IF NOT EXISTS creation_snapshot JSONB;
ALTER TABLE exam_answers ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 0;
