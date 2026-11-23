#!/usr/bin/env bash
(
set -euo pipefail

if [[ ! "$SEED_DB_PASSWORD" =~ ^[a-f0-9]{64}$ ||
      ! "$POSTGRES_PASSWORD" =~ ^[a-f0-9]{64}$ ||
      "$SEED_DB_PASSWORD" == "$POSTGRES_PASSWORD" ]]; then
    echo 'Generate two distinct PostgreSQL passwords with openssl rand -hex 32.' >&2
    exit 1
fi
if [[ "$POSTGRES_DB" != careerpilot_seed ]]; then
    echo 'Initialize a new volume with SEED_DB_NAME=careerpilot_seed first.' >&2
    exit 1
fi

psql --no-psqlrc --set ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv app_password SEED_DB_PASSWORD
CREATE ROLE seed_app LOGIN PASSWORD :'app_password';
ALTER DATABASE careerpilot_seed OWNER TO seed_app;
REVOKE CONNECT ON DATABASE careerpilot_seed FROM PUBLIC;
SQL
)
