CREATE TABLE IF NOT EXISTS study_interview_questions (
    question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    session_id INTEGER NOT NULL REFERENCES study_sessions(id) ON DELETE CASCADE,
    snapshot_hash VARCHAR(64) NOT NULL,
    CONSTRAINT uq_study_interview_question_snapshot UNIQUE (session_id, snapshot_hash)
);
