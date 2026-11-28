-- No credential is inserted by migration or startup.
CREATE TABLE IF NOT EXISTS local_workspace_boundaries (
    slug varchar(64) PRIMARY KEY,
    workspace_id integer UNIQUE
);
CREATE TABLE IF NOT EXISTS local_credentials (
    id varchar(32) PRIMARY KEY,
    token_hash varchar(64) NOT NULL UNIQUE,
    kind varchar(16) NOT NULL CHECK (kind IN ('OWNER', 'WORKSPACE')),
    workspace_id integer,
    generation varchar(32) NOT NULL,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    last_used_at timestamptz,
    revoked_at timestamptz,
    CHECK ((kind = 'OWNER' AND workspace_id IS NULL) OR (kind = 'WORKSPACE' AND workspace_id IS NOT NULL)),
    CHECK (expires_at > created_at)
);
CREATE TABLE IF NOT EXISTS local_auth_failures (
    id integer PRIMARY KEY CHECK (id = 1),
    tokens double precision NOT NULL CHECK (tokens >= 0 AND tokens <= 10),
    updated_at timestamptz NOT NULL
);
