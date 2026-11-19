# Shared local process ownership checks. Never stop a process by PID alone.
function Resolve-CareerPilotPython {
    param([string]$ServerDir, [string]$PythonPath = '')
    $candidates = @()
    if ($PythonPath) { $candidates += $PythonPath }
    else {
        $candidates += Join-Path $ServerDir '.venv\Scripts\python.exe'
        $globalPython = Get-Command python -ErrorAction SilentlyContinue
        if ($globalPython) { $candidates += $globalPython.Source }
    }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        & $candidate -c 'import uvicorn,fastapi,sqlalchemy,asyncpg,pydantic_settings' 2>$null
        if ($LASTEXITCODE -eq 0) { return [IO.Path]::GetFullPath($candidate) }
    }
    throw 'No Python with server requirements found; install server/requirements.txt or pass -PythonPath'
}

function Write-CareerPilotState {
    param([string]$Path, $State)
    $temporaryPath = $Path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    $State | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporaryPath -Encoding utf8
    Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
}

function New-CareerPilotProcessIdentity {
    param([System.Diagnostics.Process]$Process, [string]$ProjectDir, [string]$Module)
    $Process.Refresh()
    if ($Process.HasExited) { throw "New $Module process exited before startup; inspect personal/runtime logs" }
    $null = $Process.Handle
    $details = Get-CimInstance Win32_Process -Filter "ProcessId = $($Process.Id)"
    if (-not $details -or -not $details.CommandLine) { throw 'Could not verify new process identity' }
    return @{ pid = $Process.Id; started_utc_ticks = $Process.StartTime.ToUniversalTime().Ticks;
        executable_path = $Process.Path; command_line = $details.CommandLine;
        project_dir = $ProjectDir; module = $Module }
}

function Get-CareerPilotOwnedProcess {
    param($Identity, [string]$ProjectDir)
    if (-not $Identity.pid -or -not $Identity.started_utc_ticks -or -not $Identity.executable_path -or
        -not $Identity.command_line -or $Identity.project_dir -ne $ProjectDir) {
        throw 'Runtime process record lacks a verifiable identity; refusing to stop or reuse any PID'
    }
    if ($Identity.module -in @('app.project_worker', 'uvicorn')) {
        $expectedCommand = '(?:^|\s)-m\s+' + [regex]::Escape($Identity.module) + '(?:\s|$)'
    } elseif ($Identity.module -eq 'vite') {
        $expectedCommand = [regex]::Escape((Join-Path $ProjectDir 'web\node_modules\vite\bin\vite.js'))
    } else { throw 'Unknown runtime module; refusing process operation' }
    if ($Identity.command_line -notmatch $expectedCommand) { throw 'Runtime command is not the recorded module; refusing process operation' }
    $process = Get-Process -Id ([int]$Identity.pid) -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    $null = $process.Handle
    $details = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)"
    if ($process.StartTime.ToUniversalTime().Ticks -ne [long]$Identity.started_utc_ticks -or
        $process.Path -ne $Identity.executable_path -or -not $details -or
        $details.CommandLine -ne $Identity.command_line) {
        throw 'Runtime PID belongs to a different process; refusing to stop or reuse it'
    }
    return $process
}

function Stop-CareerPilotOwnedProcess {
    param($Identity, [string]$ProjectDir)
    $process = Get-CareerPilotOwnedProcess $Identity $ProjectDir
    if (-not $process) { return }
    # Windows venv python.exe is a redirector with a real Python child. Stop
    # only children with the same module command and a verified creation time.
    $children = @()
    if ($Identity.module -in @('app.project_worker', 'uvicorn')) {
        $pattern = '(?:^|\s)-m\s+' + [regex]::Escape($Identity.module) + '(?:\s|$)'
        foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($process.Id)")) {
            if ($child.CommandLine -notmatch $pattern) { continue }
            $childProcess = Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
            if ($childProcess -and $childProcess.StartTime.ToUniversalTime().Ticks -ge [long]$Identity.started_utc_ticks) {
                $children += New-CareerPilotProcessIdentity $childProcess $ProjectDir $Identity.module
            }
        }
    }
    foreach ($childIdentity in $children) {
        $childProcess = Get-CareerPilotOwnedProcess $childIdentity $ProjectDir
        if ($childProcess -and -not $childProcess.HasExited) { $childProcess.Kill(); [void]$childProcess.WaitForExit(5000) }
    }
    $process.Refresh()
    if (-not $process.HasExited) { $process.Kill(); [void]$process.WaitForExit(5000) }
}

function Sync-CareerPilotWorkerState {
    param([string]$RuntimeDir, $Identity)
    $path = Join-Path $RuntimeDir 'processes.json'
    if (-not (Test-Path -LiteralPath $path)) { return }
    $state = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    foreach ($name in @('worker_pid', 'worker_health', 'worker_out_log', 'worker_err_log')) { $state.PSObject.Properties.Remove($name) }
    if ($Identity) {
        $state | Add-Member -NotePropertyName worker_pid -NotePropertyValue $Identity.pid
        $state | Add-Member -NotePropertyName worker_health -NotePropertyValue 'process_alive'
        $state | Add-Member -NotePropertyName worker_out_log -NotePropertyValue (Join-Path $RuntimeDir 'worker.out.log')
        $state | Add-Member -NotePropertyName worker_err_log -NotePropertyValue (Join-Path $RuntimeDir 'worker.err.log')
    }
    Write-CareerPilotState $path $state
}

function Enter-CareerPilotWorkerLock {
    param([string]$ProjectDir)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $key = ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($ProjectDir.ToLowerInvariant())))).Replace('-', '') }
    finally { $hash.Dispose() }
    $mutex = New-Object Threading.Mutex($false, ('Local\CareerPilotWorker-' + $key))
    try { $locked = $mutex.WaitOne(15000) }
    catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { $mutex.Dispose(); throw 'Another Worker start/stop is in progress' }
    return $mutex
}

function Start-CareerPilotWorker {
    param([string]$ProjectDir, [string]$PythonPath, [double]$PollSeconds = 2,
        [Nullable[int]]$LeaseSeconds, [double]$HeartbeatSeconds = 10)
    $shortestLease = if ($null -eq $LeaseSeconds) { 45 } else { $LeaseSeconds }
    if ($HeartbeatSeconds -ge $shortestLease) { throw 'HeartbeatSeconds must be smaller than LeaseSeconds' }
    $runtimeDir = Join-Path $ProjectDir 'personal\runtime'
    New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
    $statePath = Join-Path $runtimeDir 'worker.json'
    $mutex = Enter-CareerPilotWorkerLock $ProjectDir
    $process = $null
    $identity = $null
    try {
        if (Test-Path -LiteralPath $statePath) {
            $existing = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
            if ($existing.module -ne 'app.project_worker') { throw 'Worker record identifies another module; refusing to reuse it' }
            if (Get-CareerPilotOwnedProcess $existing $ProjectDir) {
                Sync-CareerPilotWorkerState $runtimeDir $existing
                return @{ Identity = $existing; StartedNew = $false }
            }
        }
        $culture = [Globalization.CultureInfo]::InvariantCulture
        $arguments = @('-m', 'app.project_worker', '--poll-seconds', $PollSeconds.ToString($culture),
            '--heartbeat-seconds', $HeartbeatSeconds.ToString($culture))
        if ($null -ne $LeaseSeconds) { $arguments += @('--lease-seconds', $LeaseSeconds) }
        $process = Start-Process -FilePath $PythonPath -ArgumentList $arguments -WorkingDirectory (Join-Path $ProjectDir 'server') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir 'worker.out.log') -RedirectStandardError (Join-Path $runtimeDir 'worker.err.log')
        $identity = New-CareerPilotProcessIdentity $process $ProjectDir 'app.project_worker'
        Start-Sleep -Milliseconds 750
        $process.Refresh()
        if ($process.HasExited) { throw 'Worker exited; inspect personal/runtime/worker.err.log' }
        Write-CareerPilotState $statePath $identity
        Sync-CareerPilotWorkerState $runtimeDir $identity
        return @{ Identity = $identity; StartedNew = $true }
    } catch {
        if ($identity) { Stop-CareerPilotOwnedProcess $identity $ProjectDir }
        elseif ($process -and -not $process.HasExited) { $process.Kill() }
        throw
    } finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
}

function Stop-CareerPilotWorker {
    param([string]$ProjectDir, $ExpectedIdentity = $null)
    $runtimeDir = Join-Path $ProjectDir 'personal\runtime'
    $statePath = Join-Path $runtimeDir 'worker.json'
    $mutex = Enter-CareerPilotWorkerLock $ProjectDir
    try {
        if (-not (Test-Path -LiteralPath $statePath)) { return }
        $identity = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        if ($identity.module -ne 'app.project_worker') { throw 'Worker record identifies another module; refusing to stop it' }
        if ($ExpectedIdentity -and ($identity.pid -ne $ExpectedIdentity.pid -or $identity.started_utc_ticks -ne $ExpectedIdentity.started_utc_ticks)) {
            throw 'Worker identity changed during startup; refusing to stop its replacement'
        }
        Stop-CareerPilotOwnedProcess $identity $ProjectDir
        Remove-Item -LiteralPath $statePath
        Sync-CareerPilotWorkerState $runtimeDir $null
    } finally { $mutex.ReleaseMutex(); $mutex.Dispose() }
}
