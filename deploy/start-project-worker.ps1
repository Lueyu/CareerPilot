# Start or reuse the independent Worker/Scheduler; API/browser lifetime is separate.
[CmdletBinding()]
param([string]$PythonPath = '', [ValidateRange(0.05, 3600)][double]$PollSeconds = 2,
    [ValidateRange(5, 86400)][Nullable[int]]$LeaseSeconds, [ValidateRange(0.1, 3600)][double]$HeartbeatSeconds = 10)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'personal-processes.ps1')
$projectDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtimePython = Resolve-CareerPilotPython (Join-Path $projectDir 'server') $PythonPath
$savedWorkspace = $env:DEFAULT_WORKSPACE_SLUG
$savedEncoding = $env:PYTHONIOENCODING
try {
    $env:DEFAULT_WORKSPACE_SLUG = 'personal'
    $env:PYTHONIOENCODING = 'utf-8'
    $worker = Start-CareerPilotWorker $projectDir $runtimePython $PollSeconds $LeaseSeconds $HeartbeatSeconds
    Write-Host "Worker process alive: $($worker.Identity.pid). Check task/heartbeat status in CareerPilot; process startup does not mean analysis completed."
} finally {
    $env:DEFAULT_WORKSPACE_SLUG = $savedWorkspace
    $env:PYTHONIOENCODING = $savedEncoding
}
