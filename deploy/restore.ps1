# Restore exclusively to a NEW isolated test database. Never clean/overwrite a DB.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BackupFile,
    [Parameter(Mandatory = $true)][string]$TargetDatabase,
    [string]$HostName = $(if ($env:PGHOST) { $env:PGHOST } else { '127.0.0.1' }),
    [int]$Port = $(if ($env:PGPORT) { [int]$env:PGPORT } else { 5432 }),
    [string]$Username = $(if ($env:PGUSER) { $env:PGUSER } else { 'careerpilot' }),
    [string]$PgBin = ''
)
$ErrorActionPreference = 'Stop'
if ($TargetDatabase -notmatch '^[a-z][a-z0-9_]{0,57}_test$') {
    throw 'Restore target must be a NEW isolated database ending in _test; the personal database is forbidden'
}
$archivePath = (Resolve-Path -LiteralPath $BackupFile).Path
if (Test-Path -LiteralPath "$archivePath.sha256") {
    $expected = (Get-Content -LiteralPath "$archivePath.sha256" -Raw).Trim()
    if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash -ne $expected) { throw 'Backup SHA256 mismatch' }
}
. (Join-Path $PSScriptRoot 'postgres-tools.ps1')
$restoreTool = Resolve-PgTool 'pg_restore' $PgBin
$psqlTool = Resolve-PgTool 'psql' $PgBin
$archiveListing = & $restoreTool --list $archivePath
if ($LASTEXITCODE -ne 0 -or -not $archiveListing) { throw 'Invalid custom-format archive; target database was not created' }
$savedPassword = $env:PGPASSWORD
try {
    if (-not $env:PGPASSWORD) { $env:PGPASSWORD = 'careerpilot_dev' }
    $connectionArgs = @("--host=$HostName", "--port=$Port", "--username=$Username", '--no-password', '--set=ON_ERROR_STOP=1')
    $exists = & $psqlTool @connectionArgs --dbname=postgres -Atc "SELECT count(*) FROM pg_database WHERE datname = '$TargetDatabase'"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot check restore target; nothing restored' }
    if (($exists -join '').Trim() -ne '0') { throw "Restore refuses existing database '$TargetDatabase'; choose a NEW _test name" }
    & $psqlTool @connectionArgs --dbname=postgres -c "CREATE DATABASE $TargetDatabase"
    if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated restore target' }
    & $restoreTool --host=$HostName --port=$Port --username=$Username --no-password --dbname=$TargetDatabase --single-transaction --exit-on-error --no-owner --no-privileges $archivePath
    if ($LASTEXITCODE -ne 0) { throw "Restore failed atomically (exit $LASTEXITCODE); '$TargetDatabase' retained for inspection, original database untouched" }
    & $psqlTool @connectionArgs --dbname=$TargetDatabase -Atc 'SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = ''public'''
    if ($LASTEXITCODE -ne 0) { throw 'Restore completed but verification query failed' }
    Write-Host "Restore verified in NEW database: $TargetDatabase (original database untouched)"
} finally {
    $env:PGPASSWORD = $savedPassword
}
