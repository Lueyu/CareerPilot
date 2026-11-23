#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 || ! "$1" =~ ^[a-f0-9]{7,40}$ ]]; then
    echo 'Usage: local-env.sh <integrated Git commit>' >&2
    exit 2
fi

cat <<EOF
SEED_RELEASE_TAG=$1
SEED_HOST=localhost
SEED_PUBLIC_ORIGIN=https://localhost:8443
SEED_BIND_ADDRESS=127.0.0.1
SEED_HTTP_PORT=8080
SEED_HTTPS_PORT=8443
SEED_DB_ADMIN_PASSWORD=$(openssl rand -hex 32)
SEED_DB_PASSWORD=$(openssl rand -hex 32)
SEED_SESSION_SECRET=$(openssl rand -hex 32)
SEED_MODEL_BASE_URL=https://model.invalid/v1
SEED_MODEL_KEY=fixture-only-$(openssl rand -hex 16)
SEED_MODEL_NAME=fixture
SEED_INPUT_CNY_PER_MILLION=1
SEED_OUTPUT_CNY_PER_MILLION=2
SEED_MONTHLY_CNY=50
SEED_DAILY_USER_CNY=1
SEED_STORAGE_REGION=本机演练
SEED_MODEL_PROVIDER_LABEL=合成配置
EOF
