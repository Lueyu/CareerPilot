#!/usr/bin/env bash
set -euo pipefail
deploy_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec docker compose --env-file "${SEED_ENV_FILE:-/etc/careerpilot-seed/runtime.conf}" \
    -f "$deploy_dir/compose.yaml" "$@"
