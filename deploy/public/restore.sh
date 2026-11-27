#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' 'Legacy seed restore is disabled. Use pwsh deploy/restore.ps1 and deploy/BACKUP_RECOVERY.md. Legacy single-file archives need an isolated offline conversion; they are not accepted as verified recovery bundles. No database was created, dropped or activated.' >&2
exit 78
