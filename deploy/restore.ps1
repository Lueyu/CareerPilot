# CareerPilot 数据恢复脚本(Windows PowerShell)
# 用法: .\deploy\restore.ps1 .\backups\careerpilot-XXXXXXXX-XXXXXX.sql
# 警告: 恢复会覆盖目标库中的现有数据, 操作前请确认目标库与备份时间点!

param([Parameter(Mandatory=$true)][string]$BackupFile)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $BackupFile)) { throw "备份文件不存在: $BackupFile" }

$host_ = if ($env:PGHOST) { $env:PGHOST } else { "localhost" }
$port  = if ($env:PGPORT) { $env:PGPORT } else { "5432" }
$user  = if ($env:PGUSER) { $env:PGUSER } else { "careerpilot" }
$db    = if ($env:PGDATABASE) { $env:PGDATABASE } else { "careerpilot" }

Write-Host ">> 恢复 $BackupFile -> $db@$host_`:$port"
Write-Host ">> 此操作会覆盖现有数据! 5 秒内按 Ctrl+C 取消..."
Start-Sleep -Seconds 5

$inDocker = docker ps --format "{{.Names}}" 2>$null | Select-String -Quiet "^careerpilot-db$"
if ($inDocker) {
    Get-Content $BackupFile -Raw | docker exec -i careerpilot-db psql -U $user -d $db -q
} else {
    $env:PGPASSWORD = if ($env:PGPASSWORD) { $env:PGPASSWORD } else { "careerpilot_dev" }
    psql -h $host_ -p $port -U $user -d $db -q -f $BackupFile
}

if ($LASTEXITCODE -ne 0) { throw "恢复失败(exit $LASTEXITCODE)" }
Write-Host ">> 恢复完成"
