# Stops only the recorded, identity-verified Worker; no API/web process is stopped.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'personal-processes.ps1')
$projectDir = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
Stop-CareerPilotWorker $projectDir
Write-Host 'Recorded Worker stopped (or already absent). Queued tasks stay in PostgreSQL and can resume after Worker startup.'
