-- Old fixed-price estimates have unknown provenance. Do not relabel them as actual costs.
ALTER TABLE agent_metrics ADD COLUMN IF NOT EXISTS cost_basis varchar(32) NOT NULL DEFAULT 'unknown';
ALTER TABLE agent_metrics ADD COLUMN IF NOT EXISTS billing_call_id varchar(36);
ALTER TABLE agent_metrics ALTER COLUMN cost_cny DROP NOT NULL;
UPDATE agent_metrics SET cost_cny = NULL WHERE billing_call_id IS NULL;
CREATE TABLE IF NOT EXISTS private_model_calls (
    id varchar(36) PRIMARY KEY, workspace_id bigint NOT NULL, operation varchar(160) NOT NULL,
    day date NOT NULL, provider varchar(120) NOT NULL, endpoint varchar(1024) NOT NULL, model varchar(200) NOT NULL,
    input_price_nano_per_million bigint NOT NULL, output_price_nano_per_million bigint NOT NULL,
    daily_limit_nano_cny bigint NOT NULL, input_ceiling bigint NOT NULL, max_tokens bigint NOT NULL,
    reserved_nano_cny bigint NOT NULL CHECK (reserved_nano_cny >= 0),
    charged_nano_cny bigint NOT NULL CHECK (charged_nano_cny >= 0), actual_nano_cny bigint,
    prompt_tokens bigint, completion_tokens bigint,
    state varchar(16) NOT NULL CHECK (state IN ('RESERVED','SETTLED','UNKNOWN')),
    created_at timestamptz NOT NULL DEFAULT now(), settled_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_private_model_workspace_day ON private_model_calls (workspace_id, day);
CREATE INDEX IF NOT EXISTS ix_private_model_day ON private_model_calls (day);
