# Always restore into a NEW database. No clean/drop/cutover/release operation exists.
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$BackupFile,
    [string]$TargetDatabase,
    [ValidateSet('Drill','Plan','Standby','Inspect')][string]$Mode = 'Drill',
    [string]$HostName = $(if ($env:PGHOST) { $env:PGHOST } else { '127.0.0.1' }),
    [int]$Port = $(if ($env:PGPORT) { [int]$env:PGPORT } else { 5432 }),
    [string]$Username = $env:PGUSER,
    [string]$PgBin = '', [string]$AgeBin = '',
    [string]$IdentityFile = $env:CAREERPILOT_BACKUP_IDENTITY,
    [string]$CurrentDatabase, [string]$SafetyBackupDir,
    [string]$RecipientsFile = $env:CAREERPILOT_BACKUP_RECIPIENTS,
    [string]$OffsiteConfig = '',
    [Parameter(Mandatory)][string]$ReceiptFile,
    [string]$PlanFile, [string]$ConfirmPlan,
    [switch]$ConsumersIsolated,
    [string]$PythonBin = 'python'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'backup-common.ps1')
if ($TargetDatabase -notmatch '^[a-z][a-z0-9_]{0,54}_(test|standby)$') { throw 'Target must be a NEW *_test or *_standby database' }
if (-not $Username) { throw 'Explicit maintenance Username or PGUSER required' }
if ($Mode -eq 'Drill' -and -not $TargetDatabase.EndsWith('_test')) { throw 'Drill requires a new *_test database' }
$psql = Resolve-PgTool 'psql' $PgBin
$restore = Resolve-PgTool 'pg_restore' $PgBin
$argsPg = @('--no-psqlrc', "--host=$HostName", "--port=$Port", "--username=$Username", '--no-password', '--set=ON_ERROR_STOP=1')
function Invoke-Pg([string]$Database, [string]$Sql) {
    $output = & $psql @argsPg --dbname=$Database -Atc $Sql
    if ($LASTEXITCODE -ne 0) { throw 'Maintenance database operation failed; reconcile retained target if one was created' }
    return ($output -join "`n").Trim()
}
$receipt = @{ version = 1; mode = $Mode; target_database = $TargetDatabase; host = $HostName; port = $Port
    restore_id = [guid]::NewGuid().ToString('N'); status = 'PREFLIGHT'; actual_offsite = 'NOT_CONFIGURED'
    isolation = 'operator-confirmed no consumers/routing; maintenance owner may bypass CONNECT ACL'
    rto_scope = 'validated quarantined database only; excludes service activation, human release and real offsite retrieval' }
$timer = [Diagnostics.Stopwatch]::StartNew()
$savedEnvironment = @{}
foreach ($name in @('PGHOST', 'PGPORT', 'PGUSER', 'F6_VALIDATION_DATABASE')) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$work = $null
$created = $false
try {
    if ($Mode -eq 'Inspect') {
        $receipt.control = Invoke-Pg $TargetDatabase 'SELECT row_to_json(r) FROM recovery_control r'
        $receipt.public_tables = Invoke-Pg $TargetDatabase "SELECT count(*) FROM pg_tables WHERE schemaname='public'"
        $receipt.status = 'INSPECTED_ONLY_NO_ACTIVATION'
    } else {
        $manifest = Read-BackupBundle $BackupFile
        $digest = $manifest.files.'database.dump.age'.sha256
        $receipt.archive_sha256 = $digest
        if ($Mode -eq 'Plan') {
            if (-not $CurrentDatabase -or -not $SafetyBackupDir -or -not $PlanFile) { throw 'Plan requires CurrentDatabase, SafetyBackupDir and PlanFile' }
            if ($TargetDatabase -eq $CurrentDatabase -or $TargetDatabase -eq $manifest.source_database) { throw 'Standby target must differ from current and source databases' }
            $plan = @{ version = 1; plan_id = [guid]::NewGuid().ToString('N'); archive_sha256 = $digest
                backup = [IO.Path]::GetFullPath($BackupFile); host = $HostName; port = $Port; username = $Username
                current_database = $CurrentDatabase; target_database = $TargetDatabase
                safety_backup_dir = [IO.Path]::GetFullPath($SafetyBackupDir)
                offsite_config = $(if ($OffsiteConfig) { [IO.Path]::GetFullPath($OffsiteConfig) } else { '' })
                operations = @('verify archive and decrypt offline', 'fresh encrypted pinned backup of current database', 'create NEW unrouted standby', 'atomic import plus fresh quarantine', 'offline G5 and FK validation')
                release = @{ access = 'BLOCKED until H1 fresh restore generation credential reconciliation'; paid = 'BLOCKED until independent spend reconciliation or explicit next-day fenced approval'; automatic_rollover = $false; cutover = 'not implemented' } }
            Write-JsonFile $plan $PlanFile
            $receipt.plan_sha256 = (Get-FileHash -LiteralPath $PlanFile -Algorithm SHA256).Hash
            $receipt.confirmation = "RESTORE $($receipt.plan_sha256)"
            $receipt.status = 'PLAN_ONLY'
        } else {
            if (-not $ConsumersIsolated) { throw 'Confirm ConsumersIsolated: target is unrouted and all target consumers stopped; owner/superuser bypasses CONNECT ACL' }
            if (-not $IdentityFile) { throw 'External age IdentityFile required' }
            $age = Resolve-Age $AgeBin
            $work = Join-Path (Assert-SafeDirectory ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($ReceiptFile)))) ('cp-restore-' + $receipt.restore_id)
            [void](Assert-SafeDirectory $work)
            New-PrivateDirectory $work
            $opened = Open-BackupBundle $BackupFile $IdentityFile $age $work $PgBin
            Write-JsonFile @{ restore_id = $receipt.restore_id; reason = 'recovery source; unpin only after external recovery review' } (Join-Path $BackupFile 'recovery-source.pin')
            $receipt.preflight_seconds = $timer.Elapsed.TotalSeconds
            if ((Invoke-Pg 'postgres' "SELECT count(*) FROM pg_database WHERE datname='$TargetDatabase'") -ne '0') { throw 'Restore refuses an existing target database' }
            if ($Mode -eq 'Standby') {
                if (-not $PlanFile -or -not $ConfirmPlan) { throw 'Standby requires reviewed PlanFile and exact confirmation' }
                $plan = Get-Content -LiteralPath $PlanFile -Raw | ConvertFrom-Json
                $planHash = (Get-FileHash -LiteralPath $PlanFile -Algorithm SHA256).Hash
                if ($ConfirmPlan -cne "RESTORE $planHash" -or $plan.version -ne 1 -or
                    $plan.archive_sha256 -ne $digest -or $plan.backup -ne [IO.Path]::GetFullPath($BackupFile) -or
                    $plan.target_database -ne $TargetDatabase -or $plan.host -ne $HostName -or $plan.port -ne $Port -or $plan.username -ne $Username -or
                    -not $CurrentDatabase -or $plan.current_database -ne $CurrentDatabase -or
                    $plan.safety_backup_dir -ne [IO.Path]::GetFullPath($SafetyBackupDir) -or
                    $plan.offsite_config -ne $(if ($OffsiteConfig) { [IO.Path]::GetFullPath($OffsiteConfig) } else { '' })) { throw 'Plan confirmation or target parameters mismatch' }
                if ($TargetDatabase -eq $CurrentDatabase -or $TargetDatabase -eq $manifest.source_database) { throw 'Standby must be a different database' }
                $safety = & (Join-Path $PSScriptRoot 'backup.ps1') -Database $CurrentDatabase -OutDir $SafetyBackupDir -HostName $HostName -Port $Port -Username $Username -PgBin $PgBin -AgeBin $age -RecipientsFile $RecipientsFile -OffsiteConfig $OffsiteConfig -Pin
                $receipt.safety_backup = $safety
                $receipt.plan_sha256 = $planHash
                if ($OffsiteConfig) { $receipt.actual_offsite = 'ADAPTER_CONFIRMED_READBACK' }
            }
            [void](Invoke-Pg 'postgres' "CREATE DATABASE $TargetDatabase TEMPLATE template0")
            $created = $true
            [void](Invoke-Pg 'postgres' "REVOKE CONNECT ON DATABASE $TargetDatabase FROM PUBLIC")
            # pg_restore --file emits SQL; append quarantine BEFORE the one transaction commits.
            $sql = Join-Path $work 'restore.sql'
            & $restore --no-owner --no-privileges --file=$sql $opened.dump
            if ($LASTEXITCODE -ne 0) { throw 'Could not prepare restore SQL' }
            $controlSchema = "SET search_path = public, pg_catalog;`n" + (Get-Content -LiteralPath (Join-Path $PSScriptRoot '../server/migrations/20261128_recovery_control.sql') -Raw)
            Add-Content -LiteralPath $sql -Encoding utf8 -Value ("`n" + $controlSchema + "`nDELETE FROM recovery_control;`nINSERT INTO recovery_control(id,version,restore_id,archive_sha256,target_database,server_address,server_port,validation_state,access_state,paid_state,credentials_state,origin) VALUES(1,1,'$($receipt.restore_id)','$digest',current_database(),inet_server_addr()::text,inet_server_port(),'PENDING','QUARANTINED','BLOCKED','BLOCKED','RESTORE');")
            & $psql @argsPg --dbname=$TargetDatabase --single-transaction --file=$sql 1>$null
            if ($LASTEXITCODE -ne 0) { throw 'Restore acknowledgement failed: completion UNKNOWN; retain target and use Mode Inspect before any retry' }
            $receipt.import_seconds = $timer.Elapsed.TotalSeconds - $receipt.preflight_seconds
            $env:F6_VALIDATION_DATABASE = $TargetDatabase
            $env:PGHOST = $HostName; $env:PGPORT = [string]$Port; $env:PGUSER = $Username
            $validationFile = Join-Path $work 'validation.json'
            & $PythonBin (Join-Path $PSScriptRoot '../server/validate_recovery.py') --output $validationFile
            if ($LASTEXITCODE -ne 0) { throw 'Offline business/credential validation failed; new target retained and quarantined' }
            $receipt.validation = Get-Content -LiteralPath $validationFile -Raw | ConvertFrom-Json
            $validatedId = Invoke-Pg $TargetDatabase "UPDATE recovery_control SET validation_state='VALIDATED' WHERE restore_id='$($receipt.restore_id)' AND origin='RESTORE' AND paid_state='BLOCKED' AND access_state='QUARANTINED' RETURNING restore_id"
            if (-not $validatedId.StartsWith($receipt.restore_id)) { throw 'Quarantine generation changed during validation; target retained' }
            $receipt.status = 'VALIDATED_QUARANTINED'
            $receipt.release_plan = @{ restore_id = $receipt.restore_id; archive_sha256 = $digest; target = "$HostName`:$Port/$TargetDatabase"
                access = 'BLOCKED; H1 must invalidate inherited sessions, passwords/recovery credentials and static fallback; reconcile or reissue for this generation'
                credentials = 'BLOCKED; G5 decryption does not prove provider/repository authorization'
                paid = 'BLOCKED; preserve UNKNOWN and reservations; reconcile external missing spend, or explicitly approve after next UTC+8 day AND fence old consumers'
                approval = 'Separate offline receipts bound to restore_id, target, digest, actor/time and reconciliation; no F6 release command'
                unknown_future_auth_schema = 'BLOCKED'; cutover = 'NOT_PERFORMED' }
        }
    }
} catch {
    $receipt.status = $(if ($created) { 'UNKNOWN_OR_VALIDATION_FAILED_QUARANTINED_TARGET_RETAINED' } else { 'REFUSED_NO_NEW_TARGET' })
    $receipt.error = $_.Exception.Message
    throw
} finally {
    foreach ($name in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
    $receipt.elapsed_seconds = $timer.Elapsed.TotalSeconds
    $receipt.target_created = $created
    $receipt.raw_temp_cleaned = $true
    if ($work -and (Test-Path -LiteralPath $work)) { Remove-Item -LiteralPath (Assert-SafeDirectory $work) -Recurse -Force }
    Write-JsonFile $receipt $ReceiptFile
}
