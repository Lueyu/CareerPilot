-- Stop services that use migration 013 before running. Preserves coach metadata in this database.
BEGIN;
CREATE SCHEMA IF NOT EXISTS coach_013_archive;
CREATE TABLE IF NOT EXISTS coach_013_archive.messages (
    id integer PRIMARY KEY, coach jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS coach_013_archive.sessions (
    id integer PRIMARY KEY, hidden_at timestamptz NOT NULL
);
INSERT INTO coach_013_archive.messages SELECT id, coach FROM study_messages WHERE coach <> '{}'::jsonb
ON CONFLICT(id) DO UPDATE SET coach=EXCLUDED.coach;
INSERT INTO coach_013_archive.sessions SELECT id, hidden_at FROM study_sessions WHERE hidden_at IS NOT NULL
ON CONFLICT(id) DO UPDATE SET hidden_at=EXCLUDED.hidden_at;
ALTER TABLE study_messages DROP COLUMN coach;
ALTER TABLE study_sessions DROP COLUMN hidden_at;
COMMIT;
