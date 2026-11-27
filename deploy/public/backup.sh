#!/usr/bin/env bash
set -euo pipefail
# F6 has one shared age bundle/retention/quarantine implementation.
printf '%s\n' 'Legacy seed backup is disabled. Use pwsh deploy/backup.ps1 with external age recipients and explicit PG* maintenance connection; see deploy/BACKUP_RECOVERY.md. No staged file was deleted.' >&2
exit 78
