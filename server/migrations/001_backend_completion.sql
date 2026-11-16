-- 001_backend_completion.sql — Backend Completion 新表/新列(幂等, 可重复执行)
-- 说明: 单机本地版不用 alembic; create_all 负责新表, 本文件负责新列与索引(幂等)。

-- ============ 新列 ============
ALTER TABLE workspaces   ADD COLUMN IF NOT EXISTS preferences JSONB;
ALTER TABLE exam_answers ADD COLUMN IF NOT EXISTS judge_version VARCHAR(32);
ALTER TABLE traces       ADD COLUMN IF NOT EXISTS trigger VARCHAR(24);
ALTER TABLE traces       ADD COLUMN IF NOT EXISTS trigger_ref VARCHAR(64);

-- ============ 新表(create_all 已覆盖, 此处兜底独立执行场景) ============
CREATE TABLE IF NOT EXISTS competency_snapshots (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    trigger VARCHAR(48) NOT NULL DEFAULT 'manual',
    trigger_ref VARCHAR(64),
    weighted_gap DOUBLE PRECISION,
    overall_level DOUBLE PRECISION,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    avg_confidence DOUBLE PRECISION,
    skills JSONB,
    model_version VARCHAR(32) NOT NULL DEFAULT 'rule-v1',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jd_sources (
    id SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    company VARCHAR(160),
    role VARCHAR(160),
    level VARCHAR(16),
    source_type VARCHAR(24) NOT NULL DEFAULT 'paste',
    raw_text TEXT NOT NULL,
    parsed JSONB,
    status VARCHAR(16) NOT NULL DEFAULT 'RAW',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============ 索引 ============
CREATE INDEX IF NOT EXISTS ix_competency_snapshots_workspace_id ON competency_snapshots (workspace_id);
CREATE INDEX IF NOT EXISTS ix_jd_sources_workspace_id ON jd_sources (workspace_id);
CREATE INDEX IF NOT EXISTS ix_traces_trigger ON traces (trigger);
