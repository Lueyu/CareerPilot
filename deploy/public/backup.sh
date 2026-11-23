#!/usr/bin/env bash
set -euo pipefail
umask 077
deploy_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
: "${SEED_BACKUP_RECIPIENT:?set the off-host age public recipient}"
: "${SEED_BACKUP_TARGET:?set user@backup-host:/absolute/path/}"
if [[ ! "$SEED_BACKUP_TARGET" =~ ^[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+:/[a-zA-Z0-9_./-]*/$ ]]; then
    echo 'Use a dedicated SSH destination, user@backup-host:/absolute/path/.' >&2
    exit 2
fi

staging=$(mktemp -d)
cleanup() {
    if [[ -n "${archive:-}" ]]; then
        rm -f -- "$archive"
    fi
    rmdir -- "$staging"
}
trap cleanup EXIT
archive="$staging/careerpilot-seed-$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 8).pgdump.age"

"$deploy_dir/compose.sh" exec -T db sh -eu -c '
    exec pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
        --format=custom --no-owner --no-privileges
' | age --encrypt --recipient "$SEED_BACKUP_RECIPIENT" --output "$archive"

rsync --chmod=F600 --rsh='ssh -o BatchMode=yes -o StrictHostKeyChecking=yes' \
    -- "$archive" "$SEED_BACKUP_TARGET"
printf 'Uploaded %s\n' "${archive##*/}"
