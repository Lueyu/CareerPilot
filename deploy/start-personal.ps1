# Initialize and run from explicit directories; .env is never edited.
[CmdletBinding()]
param([int]$Port = 8000, [int]$WebPort = 5173, [switch]$NoWeb, [switch]$NoWorker, [switch]$InitializeOnly, [string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'personal-processes.ps1')
$projectDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$serverDir = Join-Path $projectDir 'server'
$webDir = Join-Path $projectDir 'web'
$pythonPath = Resolve-CareerPilotPython $serverDir $PythonPath
$runtimeDir = Join-Path $projectDir 'personal\runtime'
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
$savedWorkspace = $env:DEFAULT_WORKSPACE_SLUG
$savedEncoding = $env:PYTHONIOENCODING
$savedApiBase = $env:VITE_API_BASE
$webApiKey = "VITE_DEV_API_$WebPort"
$savedWebApi = [Environment]::GetEnvironmentVariable($webApiKey, "Process")
$savedApiToken = $env:VITE_API_TOKEN
$savedViteWorkspace = $env:VITE_WORKSPACE
$createdProcesses = @()
$worker = $null
try {
    $env:DEFAULT_WORKSPACE_SLUG = 'personal'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:VITE_API_BASE = "http://127.0.0.1:$Port/api/v1"
    [Environment]::SetEnvironmentVariable($webApiKey, $env:VITE_API_BASE, "Process")
    $env:VITE_WORKSPACE = 'personal'
    if (-not $InitializeOnly) {
        $ports = @($Port)
        if (-not $NoWeb) { $ports += $WebPort }
        foreach ($listenPort in $ports) {
            if (Get-NetTCPConnection -State Listen -LocalPort $listenPort -ErrorAction SilentlyContinue) {
                throw "Port $listenPort is already in use; inspect the running service or choose another port"
            }
        }
        if (-not $NoWeb) {
            $vitePath = Join-Path $webDir 'node_modules\vite\bin\vite.js'
            if (-not (Test-Path -LiteralPath $vitePath)) { throw 'Frontend dependencies missing; run npm ci in web/' }
            $nodePath = (Get-Command node -ErrorAction Stop).Source
        }
    }
    Push-Location $serverDir
    try {
        & $pythonPath bootstrap.py
        if ($LASTEXITCODE -ne 0) { throw 'Database bootstrap failed' }
        & $pythonPath migrate.py
        if ($LASTEXITCODE -ne 0) { throw 'Database migration failed' }
        & $pythonPath -m app.seed
        if ($LASTEXITCODE -ne 0) { throw 'Personal workspace initialization failed' }
        # Capture privately; no credentials are printed or written to launcher logs.
        $resolvedToken = & $pythonPath -c 'from app.config import get_settings; print(get_settings().api_token)'
        if ($LASTEXITCODE -ne 0) { throw 'Could not resolve local API authentication' }
        $env:VITE_API_TOKEN = ($resolvedToken -join '').Trim()
    } finally { Pop-Location }
    if ($InitializeOnly) { Write-Host 'Personal initialization verified'; return }
    $apiProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', $Port) -WorkingDirectory $serverDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir 'api.out.log') -RedirectStandardError (Join-Path $runtimeDir 'api.err.log')
    $createdProcesses += New-CareerPilotProcessIdentity $apiProcess $projectDir 'uvicorn'
    $state = @{ api_pid = $apiProcess.Id; api_url = "http://127.0.0.1:$Port"; started_at = (Get-Date).ToString('o') }
    if (-not $NoWorker) {
        $worker = Start-CareerPilotWorker $projectDir $pythonPath
        $state.worker_pid = $worker.Identity.pid
        $state.worker_health = 'process_alive'
        $state.worker_out_log = Join-Path $runtimeDir 'worker.out.log'
        $state.worker_err_log = Join-Path $runtimeDir 'worker.err.log'
    }
    if (-not $NoWeb) {
        $viteArgument = '"' + $vitePath + '"'
        $webProcess = Start-Process -FilePath $nodePath -ArgumentList @($viteArgument, '--host', '127.0.0.1', '--port', $WebPort, '--strictPort') -WorkingDirectory $webDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $runtimeDir 'web.out.log') -RedirectStandardError (Join-Path $runtimeDir 'web.err.log')
        $createdProcesses += New-CareerPilotProcessIdentity $webProcess $projectDir 'vite'
        $state.web_pid = $webProcess.Id
        $state.web_url = "http://127.0.0.1:$WebPort"
    }
    $healthy = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $apiProcess.Refresh()
        if ($apiProcess.HasExited) { throw 'API exited; inspect personal/runtime/api.err.log' }
        if ($worker -and -not (Get-CareerPilotOwnedProcess $worker.Identity $projectDir)) { throw 'Worker exited; inspect personal/runtime/worker.err.log' }
        try {
            $health = Invoke-RestMethod -Uri "$($state.api_url)/healthz" -TimeoutSec 2
            if ($health.ok) { $healthy = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $healthy) { throw 'API did not become healthy; inspect personal/runtime logs' }
    if (-not $NoWeb -and $webProcess.HasExited) { throw 'Frontend exited; inspect personal/runtime/web.err.log' }
    Write-CareerPilotState (Join-Path $runtimeDir 'processes.json') $state
    Write-Host "Personal API ready: $($state.api_url)"
    if (-not $NoWeb) { Write-Host "Personal app: $($state.web_url)" }
    if ($worker) { Write-Host "Worker process alive: $($worker.Identity.pid); task completion is reported separately." }
} catch {
    $startupError = $_
    if ($worker -and $worker.StartedNew) {
        try { Stop-CareerPilotWorker $projectDir $worker.Identity } catch { Write-Warning 'Could not verify Worker cleanup; inspect personal/runtime/worker.json before manual action' }
    }
    foreach ($identity in $createdProcesses) {
        try { Stop-CareerPilotOwnedProcess $identity $projectDir } catch { Write-Warning 'Could not verify startup process cleanup; no unverified PID was stopped' }
    }
    throw $startupError
} finally {
    $env:DEFAULT_WORKSPACE_SLUG = $savedWorkspace
    $env:PYTHONIOENCODING = $savedEncoding
    [Environment]::SetEnvironmentVariable($webApiKey, $savedWebApi, "Process")
    $env:VITE_API_BASE = $savedApiBase
    $env:VITE_API_TOKEN = $savedApiToken
    $env:VITE_WORKSPACE = $savedViteWorkspace
}
