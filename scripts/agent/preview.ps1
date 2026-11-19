#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RunDir,
    [ValidateRange(0, 65535)][int]$Port = 0,
    [switch]$Check
)

$node = Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (!$node) {
    [Console]::Error.WriteLine('Node.js 20+ is required for the local evidence preview.')
    exit 2
}
$runPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($RunDir)
$arguments = @("$PSScriptRoot/preview.mjs", $runPath, [string]$Port)
if ($Check) { $arguments += '--check' }
$PSNativeCommandUseErrorActionPreference = $false
$nodeOptions = $env:NODE_OPTIONS
$nodeCoverage = $env:NODE_V8_COVERAGE
try {
    $env:NODE_OPTIONS = $null
    $env:NODE_V8_COVERAGE = $null
    & $node.Source @arguments
} finally {
    $env:NODE_OPTIONS = $nodeOptions
    $env:NODE_V8_COVERAGE = $nodeCoverage
}
exit $LASTEXITCODE
