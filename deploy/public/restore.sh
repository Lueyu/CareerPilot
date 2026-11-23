#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 || ( "$1" != --dry-run && "$1" != --keep ) ]]; then
    echo 'Usage: restore.sh --dry-run|--keep backup.pgdump.age /path/to/age-identity' >&2
    exit 2
fi
mode=$1
archive=$2
identity=$3
if [[ ! -f "$archive" || ! -r "$archive" || ! -f "$identity" || ! -r "$identity" ]]; then
    echo 'Provide a readable encrypted archive and age identity.' >&2
    exit 2
fi

deploy_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
compose="$deploy_dir/compose.sh"
target="seed_restore_$(date -u +%Y%m%d%H%M%S)_$(openssl rand -hex 8)"
drop_target() {
    "$compose" exec -T db dropdb --username=seed_admin --maintenance-db=postgres "$target"
}

# createdb must succeed before the cleanup trap owns this name.
"$compose" exec -T db createdb --username=seed_admin --maintenance-db=postgres \
    --template=template0 --owner=seed_app "$target"
trap drop_target EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"$compose" exec -T db psql --no-psqlrc --quiet --username=seed_admin --dbname=postgres \
    --set ON_ERROR_STOP=1 --command "REVOKE CONNECT ON DATABASE $target FROM PUBLIC"
age --decrypt --identity "$identity" -- "$archive" |
    "$compose" exec -T db pg_restore --username=seed_admin --dbname="$target" --role=seed_app \
        --no-owner --no-privileges --exit-on-error --single-transaction

tables=$("$compose" exec -T db psql --no-psqlrc --username=seed_admin --dbname="$target" \
    --tuples-only --no-align --set ON_ERROR_STOP=1 \
    --command "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
if [[ ! "$tables" =~ ^[1-9][0-9]*$ ]]; then
    echo 'Restored database has no public tables.' >&2
    exit 1
fi

if [[ "$mode" == --dry-run ]]; then
    drop_target
    trap - EXIT
    printf 'Verified restore of %s tables and removed %s.\n' "$tables" "$target"
else
    trap - EXIT
    printf '%s\n' "$target"
fi
