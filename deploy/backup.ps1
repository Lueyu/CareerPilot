# CareerPilot 数据备份脚本(Windows PowerShell)
# 用法: .\deploy\backup.ps1 [输出目录]
# 默认输出到 .\backups\careerpilot-YYYYMMDD-HHmmss.sql

param([string]$OutDir = ".\backups")

$ErrorActionPreference = "Stop"
if (-not $OutDir.StartsWith('.\')) { $OutDir = ".\$OutDir" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outFile = Join-Path $OutDir "careerpilot-$stamp.sql"

# 连接参数(与 docker-compose.yml / .env 默认一致)
$host_ = if ($env:PGHOST) { $env:PGHOST } else { "localhost" }
$port  = if ($env:PGPORT) { $env:PGPORT } else { "5432" }
$user  = if ($env:PGUSER) { $env:PGUSER } else { "careerpilot" }
$db    = if ($env:PGDATABASE) { $env:PGDATABASE } else { "careerpilot" }

Write-Host ">> 备份 $db@$host_`:$port -> $outFile"

# 优先使用 docker 容器内 pg_dump(免装本机 psql)
$inDocker = docker ps --format "{{.Names}}" 2>$null | Select-String -Quiet "^careerpilot-db$"
if ($inDocker) {
    docker exec careerpilot-db pg_dump -U $user -d $db > $outFile
} else {
    # 本机已安装 PostgreSQL 时直接调用 pg_dump
    $env:PGPASSWORD = if ($env:PGPASSWORD) { $env:PGPASSWORD } else { "careerpilot_dev" }
    pg_dump -h $host_ -p $port -U $user -d $db -f $outFile
}

if ((Get-Item $outFile).Length -lt 1KB) { throw "备份文件异常($outFile 为空), 请检查连接参数" }
Write-Host ">> 完成: $outFile ($([math]::Round((Get-Item $outFile).Length/1KB,1)) KB)"
