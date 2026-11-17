-- Run only after stopping the isolated upgraded app. Never automatically downgrade
-- a personal DB with new revisions/results. Restore the verified full backup instead.
BEGIN;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM exam_answer_revisions) OR EXISTS (SELECT 1 FROM assessment_scoring_runs)
     OR EXISTS (SELECT 1 FROM exam_questions WHERE creation_snapshot IS NOT NULL)
  THEN RAISE EXCEPTION 'Refuse destructive downgrade: export new data and obtain approval or restore a separate backup database';
  END IF;
END $$;
DROP TABLE assessment_scoring_steps;
DROP TABLE assessment_scoring_runs;
DROP TABLE exam_answer_revisions;
ALTER TABLE exams DROP COLUMN IF EXISTS scoring_generation;
ALTER TABLE exams DROP COLUMN IF EXISTS active_scoring_run_id;
ALTER TABLE exam_questions DROP COLUMN IF EXISTS creation_snapshot;
ALTER TABLE exam_answers DROP COLUMN IF EXISTS revision;
COMMIT;
