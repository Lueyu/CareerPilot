# CareerPilot 本地部署指南

第一次安装请从[本机试用指南](docs/getting-started.md)开始。指南从空数据库出发，包含启动、打开题库、停止和下次继续的步骤。要修改代码和运行测试，请看[贡献指南](CONTRIBUTING.md)。

## 数据备份与恢复

数据库备份使用标准 age 加密。需要 PowerShell 7、age、PostgreSQL 工具及外置备份公钥/identity，G5 主密钥另行保管。恢复只创建全新 `_test` 演练库或经计划确认的新 `_standby` 备用库，并保持权限与付费隔离。不会覆盖或切换现库。已知安装首次升级还需离线显式登记 recovery_control，缺失状态不会自动放行。

按[加密备份与隔离恢复操作手册](deploy/BACKUP_RECOVERY.md)执行公钥配置、备份、7日/4周/3月保留、严格 SSH 复制、隔离演练、计划确认及 UNKNOWN 回执核对。旧明文 dump 和 public/restore.sh 不再是已验证恢复入口。

## 静态知识门户

如果只需要发布仓库中面向读者的文章和公开题目，在仓库根目录运行以下命令。它读取 `portal/content/` 和题库数据，在 `portal/dist/` 生成静态文件。云端工作空间的访问地址见 [README](README.md#cloud)。

```shell
node portal/build.mjs
```

## 更新已有实例

先记下当前 Git 提交并验证备份可以恢复。拉取新版本后，使用原来的数据库连接参数，按[本机试用指南](docs/getting-started.md)中对应方式执行初始化和迁移，再启动服务。进入原工作空间，打开一份已有答卷或笔记，检查数据和页面。
