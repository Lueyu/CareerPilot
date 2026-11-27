-- No implicit NORMAL row. Known installations require explicit offline provisioning.
CREATE TABLE IF NOT EXISTS recovery_control (
    id integer PRIMARY KEY CHECK (id = 1),
    version integer NOT NULL,
    restore_id text NOT NULL,
    archive_sha256 text,
    target_database text NOT NULL,
    server_address text,
    server_port integer,
    validation_state text NOT NULL,
    access_state text NOT NULL,
    paid_state text NOT NULL,
    credentials_state text NOT NULL,
    origin text NOT NULL,
    restored_at timestamptz NOT NULL DEFAULT now()
);
