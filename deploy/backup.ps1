# Encrypted PostgreSQL custom archive. Secrets come from external files/PG*.
[CmdletBinding()]
param(
    [string]$OutDir = (Join-Path $PSScriptRoot '..\personal\backups'),
    [string]$Database = $env:PGDATABASE,
    [string]$HostName = $(if ($env:PGHOST) { $env:PGHOST } else { '127.0.0.1' }),
    [int]$Port = $(if ($env:PGPORT) { [int]$env:PGPORT } else { 5432 }),
    [string]$Username = $env:PGUSER,
    [string]$PgBin = '', [string]$AgeBin = '',
    [string]$RecipientsFile = $env:CAREERPILOT_BACKUP_RECIPIENTS,
    [string]$OffsiteConfig = '', [switch]$ApplyRetention, [switch]$Pin
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'backup-common.ps1')
if (-not $RecipientsFile) { throw 'An external age recipients file is required before pg_dump' }
if (-not $Database -or -not $Username) { throw 'Database and Username or PGDATABASE/PGUSER are required' }
$recipient = (Resolve-Path -LiteralPath $RecipientsFile).Path
$age = Resolve-Age $AgeBin
$dumpTool = Resolve-PgTool 'pg_dump' $PgBin
$restoreTool = Resolve-PgTool 'pg_restore' $PgBin
$targetDir = Assert-SafeDirectory $OutDir
if ($recipient.StartsWith($targetDir + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Recipients file must be outside backup storage' }
if (-not (Test-Path -LiteralPath $targetDir)) { New-PrivateDirectory $targetDir }
$lock = [IO.File]::Open((Join-Path $targetDir '.backup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
$id = [guid]::NewGuid().ToString('N')
$work = Join-Path $targetDir ".partial-$id"
$bundle = Join-Path $targetDir "cpbackup-$id"
try {
    $markerPath = Join-Path $targetDir '.careerpilot-backups.json'
    if (Test-Path -LiteralPath $markerPath) {
        $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
        if ($marker.owner -ne $script:BackupOwner -or $marker.owner_id -notmatch '^[a-f0-9]{32}$') { throw 'Invalid owned backup directory marker' }
    } else {
        $foreign = @(Get-ChildItem -LiteralPath $targetDir -Force | Where-Object Name -ne '.backup.lock')
        if ($foreign.Count) { throw 'Refuses to adopt a nonempty unowned backup directory' }
        $marker = @{ owner = $script:BackupOwner; owner_id = [guid]::NewGuid().ToString('N') }
        Write-JsonFile $marker $markerPath
    }
    New-PrivateDirectory $work
    $dump = Join-Path $work 'database.dump'
    & $dumpTool --host=$HostName --port=$Port --username=$Username --no-password --dbname=$Database --format=custom --file=$dump
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $dump) -or (Get-Item $dump).Length -eq 0) { throw 'pg_dump failed; no valid backup published' }
    $listing = & $restoreTool --list $dump
    if ($LASTEXITCODE -ne 0 -or -not $listing) { throw 'pg_restore TOC validation failed' }
    $manifest = @{ owner = $script:BackupOwner; owner_id = $marker.owner_id; version = 1; backup_id = $id; pinned = [bool]$Pin
        source_database = $Database; created_at = [DateTimeOffset]::UtcNow.ToString('o'); files = @{}
        server_version = (($listing | Select-String 'Dumped from database version:').ToString().Trim())
        application_revision = (& git -C (Join-Path $PSScriptRoot '..') rev-parse HEAD 2>$null)
        recipient_fingerprint = (Get-FileHash -LiteralPath $recipient -Algorithm SHA256).Hash
        tools = @{ pg_dump = (& $dumpTool --version); age = (& $age --version) } }
    $cipher = Join-Path $work 'database.dump.age'
    & $age --encrypt --recipients-file $recipient --output $cipher -- $dump
    if ($LASTEXITCODE -ne 0) { throw 'age encryption failed' }
    $meta = @{} + $manifest
    $meta.Remove('files')
    $meta.dump_sha256 = (Get-FileHash -LiteralPath $dump -Algorithm SHA256).Hash
    $meta.cipher_sha256 = (Get-FileHash -LiteralPath $cipher -Algorithm SHA256).Hash
    $metaPath = Join-Path $work 'metadata.json'
    Write-JsonFile $meta $metaPath
    & $age --encrypt --recipients-file $recipient --output (Join-Path $work 'metadata.age') -- $metaPath
    if ($LASTEXITCODE -ne 0) { throw 'age metadata encryption failed' }
    Remove-Item -LiteralPath $dump, $metaPath -Force
    foreach ($name in @('database.dump.age', 'metadata.age')) {
        $path = Join-Path $work $name
        $manifest.files[$name] = @{ sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash; bytes = (Get-Item $path).Length }
    }
    Write-JsonFile $manifest (Join-Path $work 'manifest.json')
    # Publish protection with the bundle, before releasing the storage lock.
    Write-JsonFile @{ version = 1; status = $(if ($OffsiteConfig) { 'PENDING' } else { 'NOT_CONFIGURED' }); remote_restore_verified = $false } (Join-Path $work 'offsite-receipt.json')
    Move-Item -LiteralPath $work -Destination $bundle
    [void](Read-BackupBundle $bundle)
    Write-Host "Encrypted backup verified: $bundle"
} finally {
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath (Assert-SafeDirectory $work) -Recurse -Force }
    $lock.Dispose()
}
if ($OffsiteConfig) {
    try {
        $receipt = Copy-BackupOffsite $bundle $OffsiteConfig
        Write-JsonFile $receipt (Join-Path $bundle 'offsite-receipt.json')
    } catch {
        Write-JsonFile @{ version = 1; status = 'FAILED_OR_UNKNOWN'; prune_suppressed = $true; remote_restore_verified = $false } (Join-Path $bundle 'offsite-receipt.json')
        throw
    }
} else {
    Write-Host 'Offsite status: NOT_CONFIGURED; no actual offsite copy claimed'
}
if ($ApplyRetention) { & (Join-Path $PSScriptRoot 'backup-retention.ps1') -OutDir $targetDir -Apply | Out-Host }
Write-Output $bundle
