# Custom-format backup. Credentials are read from PG* environment variables.
[CmdletBinding()]
param(
    [string]$OutDir = (Join-Path $PSScriptRoot '..\personal\backups'),
    [string]$Database = $(if ($env:PGDATABASE) { $env:PGDATABASE } else { 'careerpilot' }),
    [string]$HostName = $(if ($env:PGHOST) { $env:PGHOST } else { '127.0.0.1' }),
    [int]$Port = $(if ($env:PGPORT) { [int]$env:PGPORT } else { 5432 }),
    [string]$Username = $(if ($env:PGUSER) { $env:PGUSER } else { 'careerpilot' }),
    [string]$PgBin = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'postgres-tools.ps1')
$dumpTool = Resolve-PgTool 'pg_dump' $PgBin
$restoreTool = Resolve-PgTool 'pg_restore' $PgBin
$targetDir = [IO.Path]::GetFullPath($OutDir)
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$archivePath = Join-Path $targetDir "careerpilot-$stamp.dump"
$partialPath = "$archivePath.partial"
$savedPassword = $env:PGPASSWORD
try {
    if (-not $env:PGPASSWORD) { $env:PGPASSWORD = 'careerpilot_dev' }
    & $dumpTool --host=$HostName --port=$Port --username=$Username --no-password --dbname=$Database --format=custom --file=$partialPath
    if ($LASTEXITCODE -ne 0) { throw "pg_dump failed (exit $LASTEXITCODE); no valid backup was created" }
    if (-not (Test-Path -LiteralPath $partialPath) -or (Get-Item -LiteralPath $partialPath).Length -eq 0) {
        throw 'pg_dump produced no archive'
    }
    $archiveListing = & $restoreTool --list $partialPath
    if ($LASTEXITCODE -ne 0 -or -not $archiveListing) { throw 'pg_restore could not validate the archive directory' }
    Move-Item -LiteralPath $partialPath -Destination $archivePath
    $digest = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
    $digest | Set-Content -LiteralPath "$archivePath.sha256" -Encoding ascii
    @{ format = 'postgresql-custom'; source_database = $Database; created_at = (Get-Date).ToUniversalTime().ToString('o'); sha256 = $digest } |
        ConvertTo-Json | Set-Content -LiteralPath "$archivePath.manifest.json" -Encoding utf8
    Write-Host "Backup verified: $archivePath ($((Get-Item -LiteralPath $archivePath).Length) bytes)"
    Write-Output $archivePath
} finally {
    $env:PGPASSWORD = $savedPassword
    if (Test-Path -LiteralPath $partialPath) { Remove-Item -LiteralPath $partialPath -Force }
}
