-- Additive upgrade; legacy reports remain explicitly legacy.
ALTER TABLE project_analyses ADD COLUMN IF NOT EXISTS mode varchar(16) NOT NULL DEFAULT 'full';
ALTER TABLE project_analyses ADD COLUMN IF NOT EXISTS base_sha varchar(64);
ALTER TABLE project_analyses ADD COLUMN IF NOT EXISTS target_sha varchar(64);
ALTER TABLE project_analyses ADD COLUMN IF NOT EXISTS generation integer NOT NULL DEFAULT 0;
ALTER TABLE project_analyses ADD COLUMN IF NOT EXISTS pipeline_version varchar(64) NOT NULL DEFAULT 'legacy';
ALTER TABLE project_analyses ADD COLUMN IF NOT EXISTS prompt_bundle_version varchar(64) NOT NULL DEFAULT 'legacy';
ALTER TABLE project_analyses ADD COLUMN IF NOT EXISTS model_config_fingerprint varchar(64) NOT NULL DEFAULT '';
ALTER TABLE project_analyses ADD COLUMN IF NOT EXISTS budget jsonb NOT NULL DEFAULT '{}';
ALTER TABLE project_analyses ADD COLUMN IF NOT EXISTS usage jsonb NOT NULL DEFAULT '{}';
ALTER TABLE project_analyses ADD COLUMN IF NOT EXISTS coverage_status varchar(16) NOT NULL DEFAULT 'UNASSESSED';
ALTER TABLE project_analyses ADD COLUMN IF NOT EXISTS report_status varchar(16) NOT NULL DEFAULT 'NONE';
ALTER TABLE project_analyses ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE project_analyses ADD COLUMN IF NOT EXISTS started_at timestamptz;
ALTER TABLE project_analyses ADD COLUMN IF NOT EXISTS report_published_at timestamptz;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS generation integer NOT NULL DEFAULT 0;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS latest_valid_analysis_id integer;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS last_seen_sha varchar(64);
ALTER TABLE projects ADD COLUMN IF NOT EXISTS last_checked_at timestamptz;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS next_check_at timestamptz;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS pending_update jsonb;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS archived_at timestamptz;
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS revision integer NOT NULL DEFAULT 1;
ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS freshness varchar(24) NOT NULL DEFAULT 'CURRENT';
-- Never retroactively fabricate consent for automatically activated observations.
UPDATE memory_items SET status='PENDING_REVIEW', revision=revision+1,
 meta=coalesce(meta,'{}'::jsonb)||'{"legacy_auto_activated":true}'::jsonb
WHERE mtype IN ('ProjectUnderstanding','ProjectIssue') AND source='SYSTEM'
 AND status='ACTIVE' AND confirmed_at IS NULL;
