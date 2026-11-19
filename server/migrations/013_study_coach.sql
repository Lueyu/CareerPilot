-- Additive migration. Old messages have an empty envelope and render as plain text.
ALTER TABLE study_messages ADD COLUMN IF NOT EXISTS coach jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE study_sessions ADD COLUMN IF NOT EXISTS hidden_at timestamptz;
