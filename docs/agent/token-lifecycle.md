# 管理本地访问凭据

本地 API 只接受已登记且属于当前安装代次的凭据。`API_TOKEN` 是 G6 启动检查和显式迁移的输入，不再是运行时鉴权后门。`WORKSPACE_TOKEN_HASHES` 只供离线迁移读取。API、worker、建表、迁移 SQL 和重启均不登记凭据。

## 首次启用或升级

先停止旧应用的业务访问。核对目标环境、数据库身份和现有租户配置，保留当前配置。用 `server/` 的项目 Python 执行已有建表和 SQL 迁移。按 F6 流程显式登记已审核的正常安装。以下 `<database>` 必须替换为你已核对的实际库名；恢复库不能执行正常安装登记。

```powershell
python bootstrap.py
python migrate.py
python provision_recovery_control.py --confirm "PROVISION KNOWN INSTALLATION <database>"
python token_lifecycle.py enroll-env --confirm "ENROLL <database>"
```

最后一个命令明确把当前强 `API_TOKEN` 登记为本地 owner，默认有效期 30 天。它同时持久保存配置中**所有**租户归属，包括尚未登记 token、尚未创建工作空间的 slug。配置中只有哈希的租户不会自动获得访问权。重复登记相同 token 会拒绝，已撤销或到期的 token 也不能重新登记。

若只迁移租户归属，不登记任何 token，运行下列命令。它可重试，且不改变已有凭据状态。

```powershell
python token_lifecycle.py migrate-scopes --confirm "ENROLL <database>"
```

旧租户 token 的原文从受保护文件读取，命令行参数和历史中不传原文。文件应只含一个强随机 token。明确指定已存在工作空间的 slug。

```powershell
python token_lifecycle.py enroll --input-file C:\private\old.token --workspace tenant-slug --confirm "ENROLL <database>"
```

独立租户安装若要使用已有 `API_TOKEN`，对该安装执行 `enroll-env --workspace tenant-slug --confirm "ENROLL <database>"`，不能遗漏 `--workspace`。正常运行时的注册网关仍按已验证的账号选择目标；目标安装必须已完成此显式登记。HTTP 请求不会替目标完成登记。

确认归属迁移成功后才移除旧配置映射。当前安装不能通过编辑映射、撤销最后一个 token 或删除工作空间取消已保存的租户隔离。`personal` 一旦已有租户归属，两条 owner 创建工作空间入口均拒绝借用它，且不会新增本地 owner 成员。修复此类历史配置需要独立离线审核，本 CLI 不提供转为 owner 的命令。

## 签发、轮换、撤销

签发和两种登记命令在同一事务中保留全部旧租户映射。即使只签发一个新 owner token，也不会让配置里尚未登记凭据的租户变为 owner 可见。

输出目录须由操作员拥有。输出文件必须不存在。命令复用 G6 的私有文件创建及 Windows ACL 验证，在写入原文前完成权限保护。

```powershell
python token_lifecycle.py issue --output C:\private\owner.token --days 30
python token_lifecycle.py issue --workspace tenant-slug --output C:\private\tenant.token --days 7
python token_lifecycle.py list
python token_lifecycle.py rotate <credential-id> --output C:\private\replacement.token --days 30
python token_lifecycle.py revoke <credential-id>
```

省略 `--workspace` 明确签发 owner 凭据。`--days` 支持 1 至 365 天。签发和轮换只生成新随机值。`--show-token` 可替代 `--output`，表示操作员明确要求在当前终端显示新秘密；不要在会保存终端内容的共享日志中使用它。默认输出只有 ID、scope 和时间元数据，没有原文、哈希或提示片段。

轮换在一个数据库事务内撤销旧行并登记同 scope 的新行。并发轮换只有一个成功者。输出文件创建失败时旧凭据保持有效。提交成功后，用新文件的 token 更新客户端。无需重启 API。已配置但撤销的旧 `API_TOKEN` 可以继续通过 G6 的格式检查，它不会因此重新获得访问权。

撤销可重试。哈希记录保留为永久去重记录。删除工作空间不删除凭据哈希，也不把 workspace ID 改为空。旧 token 不能认证同 slug 的新工作空间。普通重启既不签发也不换 token。

## 处理拒绝

401 表示凭据未知、撤销、到期、工作空间消失或安装代次不符。403 表示已认证 workspace 凭据调用 owner 接口。404 隐藏不属于主体的工作空间。失败鉴权使用整个安装共享的数据库桶，容量 10，每 6 秒恢复一次机会。429 含 `Retry-After`。代理转发头、替换 token、换工作空间和新进程均不建立新桶。有效凭据可继续认证；失败请求不消耗 E3 的 workspace 配额。

503 `AUTH_REGISTRY_UNAVAILABLE` 表示注册表不可用，系统不会退回 `API_TOKEN`。F6 的 `RECOVERY_QUARANTINED` 会继续阻止恢复后的访问和付费。当前没有恢复库放行命令。离线验证、新访问凭据和独立付费批准尚须完成后续恢复协议，不能把恢复状态改为 NORMAL 来启用旧归档。

`deploy/ops` 的独立 `OPS_TOKEN` 与短期只读 proof 保持原有独立协议。它不接受本地 API token 作为替代。注册网关的 cookie、Origin、来源和学习证据协议保持不变。

隔离候选预览由 `candidate_delivery.py start` 在新建且独占的预览安装完成 known-install provisioning 后，单独运行一次 `enroll-env`。API/worker 的启动命令和预览回读验证均不包含登记。预览不会修改个人 `.env`、数据库或进程。
