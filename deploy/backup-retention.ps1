[CmdletBinding()]
param([Parameter(Mandatory)][string]$OutDir, [ValidateRange(1,3650)][int]$Daily = 7,
    [ValidateRange(1,520)][int]$Weekly = 4, [ValidateRange(1,120)][int]$Monthly = 3, [switch]$Apply)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'backup-common.ps1')
$root = Assert-SafeDirectory $OutDir
$markerPath = Join-Path $root '.careerpilot-backups.json'
if (-not (Test-Path -LiteralPath $markerPath)) { throw 'No owned backup directory marker; nothing deleted' }
$marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
if ($marker.owner -ne $script:BackupOwner -or $marker.owner_id -notmatch '^[a-f0-9]{32}$') { throw 'Invalid owned backup directory marker' }
$lock = [IO.File]::Open((Join-Path $root '.backup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
try {
    $valid = @()
    foreach ($dir in Get-ChildItem -LiteralPath $root -Directory -Force) {
        if ($dir.Name -notmatch '^cpbackup-[a-f0-9]{32}$') { continue }
        try { $m = Read-BackupBundle $dir.FullName } catch { Write-Warning "Skipping invalid/incomplete backup: $($dir.Name)"; continue }
        if ($m.owner_id -ne $marker.owner_id) { throw 'Foreign owner archive found; nothing deleted' }
        $offsitePending = $true
        $copyReceipt = Join-Path $dir.FullName 'offsite-receipt.json'
        if (Test-Path -LiteralPath $copyReceipt) {
            try {
                $copyState = Get-Content -LiteralPath $copyReceipt -Raw | ConvertFrom-Json
                $offsitePending = -not ($copyState.version -eq 1 -and $copyState.status -in @('NOT_CONFIGURED', 'confirmed_sha256'))
            }
            catch { $offsitePending = $true }
        }
        $valid += [pscustomobject]@{ path = $dir.FullName; time = [DateTimeOffset]::Parse($m.created_at).UtcDateTime; pinned = ($m.pinned -or $offsitePending -or (Test-Path -LiteralPath (Join-Path $dir.FullName 'recovery-source.pin'))) }
    }
    if (-not $valid.Count) { throw 'No verified owned backup; nothing deleted' }
    $valid = @($valid | Sort-Object time -Descending)
    $keep = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    [void]$keep.Add($valid[0].path)
    foreach ($item in $valid | Where-Object pinned) { [void]$keep.Add($item.path) }
    foreach ($policy in @(@{ count = $Daily; format = 'yyyy-MM-dd' }, @{ count = $Weekly; format = 'week' }, @{ count = $Monthly; format = 'yyyy-MM' })) {
        $buckets = [Collections.Generic.HashSet[string]]::new()
        foreach ($item in $valid) {
            $bucket = if ($policy.format -eq 'week') { '{0}-{1}' -f [Globalization.ISOWeek]::GetYear($item.time), [Globalization.ISOWeek]::GetWeekOfYear($item.time) } else { $item.time.ToString($policy.format) }
            if ($buckets.Contains($bucket)) { continue }
            if ($buckets.Count -ge $policy.count) { break }
            [void]$buckets.Add($bucket)
            [void]$keep.Add($item.path)
        }
    }
    $delete = @($valid | Where-Object { -not $keep.Contains($_.path) })
    foreach ($item in $delete) {
        $safe = Assert-SafeDirectory $item.path
        if ([IO.Path]::GetDirectoryName($safe) -ne $root) { throw 'Retention path escaped owned directory' }
        if ($Apply) { Remove-Item -LiteralPath $safe -Recurse -Force }
    }
    @{ applied = [bool]$Apply; daily = $Daily; weekly = $Weekly; monthly = $Monthly; kept = @($keep); deleted = @($delete | ForEach-Object path) } | ConvertTo-Json -Depth 5
} finally { $lock.Dispose() }
