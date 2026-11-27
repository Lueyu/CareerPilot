CREATE TABLE IF NOT EXISTS api_rate_buckets (
    subject VARCHAR(80) NOT NULL,
    category VARCHAR(8) NOT NULL CHECK (category IN ('read', 'write', 'llm')),
    burst_tokens DOUBLE PRECISION NOT NULL,
    quota_day DATE NOT NULL,
    daily_count INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (subject, category),
    CHECK (burst_tokens >= 0 AND daily_count >= 0)
);
CREATE INDEX IF NOT EXISTS ix_api_rate_buckets_expires_at ON api_rate_buckets (expires_at);
