Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'postgres-tools.ps1')
$script:BackupOwner = 'careerpilot-encrypted-backup-v1'

function Assert-SafeDirectory([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -eq [IO.Path]::GetPathRoot($full)) { throw 'Filesystem root is not an owned backup directory' }
    $cursor = $full
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Reparse points/symlinks are forbidden in backup paths'
            }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    return $full
}

function New-PrivateDirectory([string]$Path) {
    New-Item -ItemType Directory -Path $Path -ErrorAction Stop | Out-Null
    if ($IsWindows) {
        $acl = [Security.AccessControl.DirectorySecurity]::new()
        $acl.SetAccessRuleProtection($true, $false)
        $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($user, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
        Set-Acl -LiteralPath $Path -AclObject $acl
    } else {
        & chmod 700 -- $Path
        if ($LASTEXITCODE -ne 0) { throw 'Cannot protect raw temporary directory' }
    }
}

function Resolve-Age([string]$AgeBin) {
    if (-not $AgeBin) { $AgeBin = $env:AGE_BIN }
    if ($AgeBin) {
        if (-not (Test-Path -LiteralPath $AgeBin -PathType Leaf)) { throw 'Configured age executable missing' }
        return (Resolve-Path -LiteralPath $AgeBin).Path
    }
    $tool = Get-Command age -ErrorAction SilentlyContinue
    if (-not $tool) { throw 'Install age or set AGE_BIN to an operator verified executable' }
    return $tool.Source
}

function Write-JsonFile($Value, [string]$Path) {
    $Value | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Path -Encoding utf8
}

function Read-BackupBundle([string]$Bundle) {
    $dir = Assert-SafeDirectory $Bundle
    foreach ($name in @('manifest.json', 'database.dump.age', 'metadata.age')) {
        $file = Get-Item -LiteralPath (Join-Path $dir $name) -Force
        if ($file.PSIsContainer -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Invalid backup member' }
    }
    $manifest = Get-Content -LiteralPath (Join-Path $dir 'manifest.json') -Raw | ConvertFrom-Json
    if ($manifest.owner -ne $script:BackupOwner -or $manifest.version -ne 1 -or
        $manifest.backup_id -notmatch '^[a-f0-9]{32}$' -or $manifest.owner_id -notmatch '^[a-f0-9]{32}$' -or
        (Split-Path $dir -Leaf) -ne "cpbackup-$($manifest.backup_id)") { throw 'Not an owned backup bundle' }
    foreach ($name in @('database.dump.age', 'metadata.age')) {
        $entry = $manifest.files.$name
        $path = Join-Path $dir $name
        if ($entry.sha256 -notmatch '^[a-fA-F0-9]{64}$' -or (Get-Item $path).Length -ne $entry.bytes -or
            (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.sha256) { throw 'Backup SHA256/size mismatch' }
    }
    [void][DateTimeOffset]::Parse($manifest.created_at)
    return $manifest
}

function Open-BackupBundle([string]$Bundle, [string]$IdentityFile, [string]$Age, [string]$WorkDir, [string]$PgBin) {
    $manifest = Read-BackupBundle $Bundle
    $identity = (Resolve-Path -LiteralPath $IdentityFile).Path
    $bundlePath = [IO.Path]::GetFullPath($Bundle)
    if ($identity.StartsWith($bundlePath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Identity must be external to the archive' }
    $metaPath = Join-Path $WorkDir 'metadata.json'
    $dumpPath = Join-Path $WorkDir 'database.dump'
    & $Age --decrypt --identity $identity --output $metaPath -- (Join-Path $bundlePath 'metadata.age') 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'age decryption failed; no target created' }
    $meta = Get-Content -LiteralPath $metaPath -Raw | ConvertFrom-Json
    foreach ($field in @('owner', 'owner_id', 'version', 'backup_id', 'created_at', 'source_database', 'pinned', 'server_version', 'application_revision', 'recipient_fingerprint')) {
        if ($meta.$field -cne $manifest.$field) { throw 'Authenticated metadata mismatch' }
    }
    if ($meta.cipher_sha256 -ne $manifest.files.'database.dump.age'.sha256) { throw 'Authenticated ciphertext mismatch' }
    & $Age --decrypt --identity $identity --output $dumpPath -- (Join-Path $bundlePath 'database.dump.age') 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'age archive authentication failed; no target created' }
    if ((Get-FileHash -LiteralPath $dumpPath -Algorithm SHA256).Hash -ne $meta.dump_sha256) { throw 'Decrypted archive SHA256 mismatch' }
    $restore = Resolve-PgTool 'pg_restore' $PgBin
    $listing = & $restore --list $dumpPath
    if ($LASTEXITCODE -ne 0 -or -not $listing) { throw 'Invalid PostgreSQL custom archive' }
    return @{ manifest = $manifest; dump = $dumpPath }
}

function Copy-BackupOffsite([string]$Bundle, [string]$ConfigFile) {
    $config = Get-Content -LiteralPath $ConfigFile -Raw | ConvertFrom-Json
    if ($config.adapter -ne 'ssh-rsync' -or
        $config.target -notmatch '^[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+:/[a-zA-Z0-9_/-]+/$' -or
        $config.target -match '(^|/)\.\.?(/|$)') { throw 'Invalid strict SSH/rsync remote configuration' }
    $known = (Resolve-Path -LiteralPath $config.known_hosts).Path
    $key = (Resolve-Path -LiteralPath $config.identity_file).Path
    foreach ($path in @($known, $key)) {
        if ($path -notmatch '^[a-zA-Z0-9_:/\\ .-]+$' -or $path.StartsWith([IO.Path]::GetFullPath($Bundle), [StringComparison]::OrdinalIgnoreCase)) { throw 'SSH files must be external and have safe paths' }
    }
    $rsync = Get-Command rsync -ErrorAction SilentlyContinue
    if (-not $rsync) { throw 'Configured offsite copy requires rsync and OpenSSH; local verified backup retained' }
    $ssh = (Get-Command ssh -ErrorAction Stop).Source
    $sshArgs = @('-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=15', '-o', "UserKnownHostsFile=$known", '-i', $key)
    $remoteHost, $remoteRoot = $config.target -split ':', 2
    $remoteFinal = $remoteRoot + (Split-Path $Bundle -Leaf)
    $remotePartial = $remoteFinal + '.partial-' + [guid]::NewGuid().ToString('N')
    & $ssh @sshArgs $remoteHost "umask 077; mkdir -- $remotePartial"
    if ($LASTEXITCODE -ne 0) { throw 'Offsite preparation failed/unknown; local backup retained, no pruning' }
    $rsh = 'ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o ConnectTimeout=15 -o UserKnownHostsFile="' + $known + '" -i "' + $key + '"'
    $files = @('database.dump.age', 'metadata.age', 'manifest.json')
    $sources = @($files | ForEach-Object { Join-Path $Bundle $_ })
    & $rsync.Source --chmod=F600 --rsh=$rsh -- @sources ($remoteHost + ':' + $remotePartial + '/')
    if ($LASTEXITCODE -ne 0) { throw 'Offsite transfer failed/unknown; local backup retained, no pruning' }
    $checks = $files | ForEach-Object { "$remotePartial/$_" }
    $remoteHashes = & $ssh @sshArgs $remoteHost ('sha256sum -- ' + ($checks -join ' '))
    if ($LASTEXITCODE -ne 0 -or @($remoteHashes).Count -ne 3) { throw 'Offsite verification UNKNOWN; local backup retained, no pruning' }
    for ($index = 0; $index -lt 3; $index++) {
        if (($remoteHashes[$index] -split '\s+')[0] -ne (Get-FileHash -LiteralPath $sources[$index] -Algorithm SHA256).Hash) { throw 'Remote SHA256 mismatch; local backup retained, no pruning' }
    }
    & $ssh @sshArgs $remoteHost "test ! -e $remoteFinal && mv -T -n -- $remotePartial $remoteFinal && test ! -e $remotePartial"
    if ($LASTEXITCODE -ne 0) { throw 'Offsite publication UNKNOWN; local backup retained, no pruning' }
    return @{ version = 1; adapter = 'ssh-rsync'; status = 'confirmed_sha256'; verified_remote_restore = $false }
}
