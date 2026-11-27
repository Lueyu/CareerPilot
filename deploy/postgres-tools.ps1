function Resolve-PgTool {
    param([string]$ToolName, [string]$PgBin)
    $candidates = @()
    if ($PgBin) { $candidates += Join-Path $PgBin $(if ($IsWindows) { "$ToolName.exe" } else { $ToolName }) }
    $command = Get-Command $ToolName -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    $candidates += Join-Path $env:USERPROFILE "scoop\apps\postgresql\current\bin\$ToolName.exe"
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    throw "PostgreSQL tool '$ToolName' not found; pass -PgBin with its directory"
}
