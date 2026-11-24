# CareerPilot 本地部署指南

第一次安装请从[本机试用指南](docs/getting-started.md)开始。指南从空数据库出发，包含启动、打开题库、停止和下次继续的步骤。要修改代码和运行测试，请看[贡献指南](CONTRIBUTING.md)。

## 数据备份与恢复

个人工作空间的题库、作答、笔记和项目记录保存在 PostgreSQL 中。升级前先备份，再用新的测试库检查能否恢复。以下脚本在 Windows PowerShell 中运行，需要与数据库主版本一致的 `pg_dump`、`pg_restore` 和 `psql`。例如数据库是 PostgreSQL 16，就使用 16 版客户端。如果装了多个版本，两个脚本都可传入 `-PgBin` 指定工具目录。

在仓库根目录运行备份。先设置 `PGHOST`、`PGPORT`、`PGUSER`、`PGDATABASE` 和 `PGPASSWORD`，使它们指向要备份的本机数据库。归档默认写入 `personal/backups/`，脚本会打印完整路径，同时保存 SHA-256 校验文件和清单。

```powershell
.\deploy\backup.ps1
```

将备份脚本输出的 `.dump` 路径粘贴到提示中。恢复使用同一个 PostgreSQL 实例，需要当前账号有建库权限。目标名称必须以 `_test` 结尾且尚未使用。恢复完成后脚本会查询新库中的表。

```powershell
$archive = Read-Host '输入刚生成的 .dump 文件路径'
$target = 'careerpilot_restore_' + (Get-Date -Format 'yyyyMMddHHmmss') + '_test'
.\deploy\restore.ps1 -BackupFile $archive -TargetDatabase $target
```

[备份脚本](deploy/backup.ps1) · [恢复脚本](deploy/restore.ps1)

## 静态知识门户

如果只需要发布仓库中面向读者的文章和公开题目，在仓库根目录运行以下命令。它读取 `portal/content/` 和题库数据，在 `portal/dist/` 生成静态文件。云端工作空间的访问地址见 [README](README.md#cloud)。

```shell
node portal/build.mjs
```

## 更新已有实例

先记下当前 Git 提交并验证备份可以恢复。拉取新版本后，使用原来的数据库连接参数，按[本机试用指南](docs/getting-started.md)中对应方式执行初始化和迁移，再启动服务。进入原工作空间，打开一份已有答卷或笔记，检查数据和页面。
