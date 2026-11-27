# 加密备份与隔离恢复操作手册

本入口生成标准 age 加密的 PostgreSQL custom 归档。恢复只创建全新数据库，并停在验证后的隔离备用库。它不覆盖、删除、改名或切换现有数据库，不启用旧 token，不释放预算。所有恢复目标在导入之前必须没有应用路由与消费者。

## 操作前提

使用 PowerShell 7、官方 age 与 age-keygen、原生 PostgreSQL pg_dump/pg_restore/psql 和具有建库权限的维护账号。pg_dump 不得早于服务器主版本，pg_restore 至少支持 dump 版本；本次隔离演练实际为客户端18.6、服务器18.6。优先使用一致主版本。恢复验证还需要安装项目依赖的 Python，包含 asyncpg、SQLAlchemy 和 cryptography。Linux 同样通过 `pwsh` 使用本套脚本，原生工具放在 PATH 或用 `-PgBin` 指定目录。

只在操作终端设置 PGHOST、PGPORT、PGUSER、PGDATABASE、PGPASSWORD 或使用受保护的 PGPASSFILE。不要把密码、G5 主密钥或 age identity 值写进命令参数、日志、Git 或归档。无开发默认密码回退。G5 的 `CAREERPILOT_SECRETS_KEY` 用于解密数据库字段；age identity 用于解密备份文件，二者相互独立，必须分别保存在数据库与备份目录以外。公钥文件也放在归档目录之外。

选择安全目录，先离线生成 age identity，再导出公钥。

```powershell
$env:AGE_BIN = 'D:\tools\age\age.exe'
$env:AGE_KEYGEN_BIN = 'D:\tools\age\age-keygen.exe'
& $env:AGE_KEYGEN_BIN -o 'D:\secure\backup-identity.txt'
& $env:AGE_KEYGEN_BIN -y 'D:\secure\backup-identity.txt' | Set-Content 'D:\secure\backup-recipients.txt' -Encoding ascii
$env:CAREERPILOT_BACKUP_RECIPIENTS = 'D:\secure\backup-recipients.txt'
$env:CAREERPILOT_BACKUP_IDENTITY = 'D:\secure\backup-identity.txt'
```

identity 目录与文件应仅允许运维用户读取。备份脚本只读取公钥。脚本为新建输出和原文临时目录设置私有 ACL，Unix 使用0700；既有输出目录权限由操作员核验。进程被强行结束或断电时 finally 无法执行，遗留 `.partial-*`/`cp-restore-*` 可能含原文，应由操作员确认没有活进程后在维护窗口检查并清理。不能把常规 finally 清理说成断电后的保证。SSD/快照上的安全擦除也不由此脚本保证。

## 备份、复制与保留

```powershell
$archive = .\deploy\backup.ps1 -Database $env:PGDATABASE -Username $env:PGUSER -OutDir 'D:\private-backups\careerpilot'
.\deploy\backup-retention.ps1 -OutDir 'D:\private-backups\careerpilot'
.\deploy\backup-retention.ps1 -OutDir 'D:\private-backups\careerpilot' -Apply
```

`$archive` 是目录，不是单个 dump 文件。目录包含 database.dump.age、metadata.age 和 manifest.json。清单记录版本、owner、随机 backup_id、UTC 时间、源数据库名、服务器/客户端版本、代码提交、接收者公钥文件指纹、字节数及密文 SHA256。加密元数据另存原文 dump SHA256，并绑定外层操作字段和密文摘要。恢复先检查 SHA，再验证 age 完整性，比较内外元数据，最后验证 pg_restore 目录。SHA 防传输损坏，age 防密文篡改；持有公钥的人也能生成新归档，因此它不证明发送者身份。只恢复来源受信任的备份，维护账号运行的 PostgreSQL dump 可能包含 SQL 代码。

保留规则是最新7个 UTC 日、4个 ISO 周、3个月的归档并集。同一归档可以覆盖多个桶。始终保留最新有效归档、`pinned` 安全备份和带 recovery-source.pin 的恢复源。默认只输出计划，`-Apply` 才删除。只处理当前 owner_id 的完整目录，忽略不完整/损坏目录与无关文件，不接受根目录或 reparse/symlink 路径；发现不同 owner_id 时拒绝整次清理。它不会自动删除受保护备份，解除 pin 需要另一次明确维护决定。

复制 `deploy/backup-offsite.example.json` 到安全配置目录，填真实目标、预先核验的 known_hosts 和专用 SSH identity 路径。需要 OpenSSH、rsync 和远端 GNU mkdir/mv/sha256sum，目标父目录须存在。目标语法只支持 `user@host:/absolute/path/`，不支持 IPv6 字面量或带 shell 特殊符号的目录。

```powershell
$archive = .\deploy\backup.ps1 -Database $env:PGDATABASE -Username $env:PGUSER -OutDir 'D:\private-backups\careerpilot' -OffsiteConfig 'D:\secure\backup-offsite.json' -ApplyRetention
```

SSH 使用 BatchMode、StrictHostKeyChecking、IdentitiesOnly 和明确 known_hosts。先上传到远端唯一 partial 目录，远端读回3个文件的 SHA256 后发布，不覆盖已有名字。失败或无法确认时抛错，保留本地密文并跳过保留清理。传输断开后远端可能已有部分或全部文件，状态按失败/未知处理，不能靠重试宣称已上传。成功回执证明配置适配器读回摘要，不证明真实异地灾难恢复。没有配置时输出 NOT_CONFIGURED。本次没有授权真实远端，只有模拟 SSH/rsync 失败证据，真实异地存储与远端恢复均未验证。

## 真实隔离演练

先在全新的 `_test` 源库创建合成工作空间、题目、答卷、原答、证据和秘密，禁止用个人库当测试数据源。先保存原答ID及完整行指纹。将解密的 G5 master key 通过受保护进程环境提供给离线验证器，随后执行。

```powershell
$target = 'careerpilot_dr_' + (Get-Date -Format 'yyyyMMddHHmmss') + '_test'
.\deploy\restore.ps1 -BackupFile $archive -TargetDatabase $target -ConsumersIsolated -ReceiptFile 'D:\dr-evidence\drill.json' -PythonBin '.\server\.venv\Scripts\python.exe'
```

ReceiptFile 的父目录必须已存在且由本次运行拥有；原文临时目录建在其旁边并使用私有 ACL。脚本校验归档、解密、TOC，检查目标不存在，然后创建 template0 新库并撤销 PUBLIC CONNECT。维护owner/超级用户仍可绕过该 ACL，因此隔离的实际前提是目标不接入应用且无消费者，而不是把 REVOKE 冒充绝对屏障。

为使导入和隔离同事务提交，pg_restore 生成 SQL，psql --single-transaction --set=ON_ERROR_STOP=1 执行导入与新的 recovery_control。恢复覆盖继承的恢复控制记录，生成全新 restore_id；不恢复任何旧审批。离线验证器检查所需业务表、有效外键、答题关联、原始证据行指纹及全部 G5 秘密字段。纯 G5 解码不调用模型或仓库，也不放行在线 credential resolver。验证结束仅将 validation_state 改为 VALIDATED，access 仍 QUARANTINED，paid/credentials 仍 BLOCKED。

脚本的通用验证器生成行指纹；演练测试必须把它与源库保存的预期指纹和ID比较。实际生产恢复还需操作员对指定业务清单核对，不能只凭表数量或“没有异常”放行业务。旧版缺少所需业务表的归档会保留新目标但拒绝成功，须在隔离维护环境先制定兼容迁移，不能临时绕过。

## 生产恢复计划与新备用库

以下是操作能力说明，本次只对合成测试库演练。停用目标消费者、准备维护权限后，先生成可以阅读的计划。

```powershell
.\deploy\restore.ps1 -Mode Plan -BackupFile $archive -TargetDatabase 'careerpilot_recovery_standby' -CurrentDatabase $env:PGDATABASE -SafetyBackupDir 'D:\private-backups\pre-recovery' -PlanFile 'D:\dr-evidence\plan.json' -ReceiptFile 'D:\dr-evidence\plan-receipt.json'
```

逐项核对 host、port、username、源归档摘要、当前库、新库和安全备份目录。回执提供绑定整个计划 SHA256 的确认短语。将它完整传给执行命令，并保持全部计划参数一致。

```powershell
$confirmation = (Get-Content 'D:\dr-evidence\plan-receipt.json' -Raw | ConvertFrom-Json).confirmation
.\deploy\restore.ps1 -Mode Standby -BackupFile $archive -TargetDatabase 'careerpilot_recovery_standby' -CurrentDatabase $env:PGDATABASE -SafetyBackupDir 'D:\private-backups\pre-recovery' -PlanFile 'D:\dr-evidence\plan.json' -ConfirmPlan $confirmation -ConsumersIsolated -ReceiptFile 'D:\dr-evidence\standby.json' -PythonBin '.\server\.venv\Scripts\python.exe'
```

执行先校验归档，确认目标不存在，再为当前数据库生成新的、校验通过且 pinned 的加密安全备份。失败则不创建目标。计划配置远端时安全备份必须获得适配器确认，否则拒绝；无远端时明确只有本地安全副本。最后创建新备用库、导入、安装隔离并验证。没有 DROP、--clean、覆盖、切换连接或通用 approve 参数。

若客户端退出非零，不能推断事务一定回滚。可能数据库已 COMMIT，只是确认响应丢失。回执标记 UNKNOWN/验证失败并保留新目标，禁止同名重试导入。只读检查使用相同连接与目标参数。

```powershell
.\deploy\restore.ps1 -Mode Inspect -BackupFile $archive -TargetDatabase $target -ReceiptFile 'D:\dr-evidence\inspect.json'
```

## 恢复后权限与预算

缺少、损坏或未知版本的 recovery_control 都拒绝在线业务。两种 API 的业务路由包括注册/登录/恢复入口，require_auth、_session_account、私有与公共付费准入和实际 LLM/仓库凭据解析均检查持久记录。私有准入在原有计费锁内复查；公共准入在站点锁内检查。私有启动不会恢复任务或启动播客 worker。进程重启、换 token 或换天都不会解除隔离。

已知全新安装或经操作员确认的现有正常安装，先执行迁移，再在维护窗口显式登记。不要对任何恢复库运行此命令。已标识为 RESTORE 的库会拒绝登记。

```powershell
# DATABASE_URL 指向已经审核的已知安装，密码仅通过受保护环境提供。
cd server
python migrate.py
python provision_recovery_control.py --confirm 'PROVISION KNOWN INSTALLATION 这里填审核过的实际数据库名'
```

不能根据一个没有恢复标记的旧数据库自动判断它是原实例还是 pre-F6 恢复，因此该步骤依赖操作员对来源的确认。普通启动和 create_all 不会补 NORMAL。本次没有在个人库运行任何登记或迁移。部署预览/首次安装必须完成这项显式前提，否则 API 返回503。

H1 的后续集成合同要求将离线审批绑定新 restore_id、目标服务器/数据库、归档摘要、审批人和时间。旧静态token、会话、密码和恢复码均不可信；不能只清会话却让旧密码重新登录。必须由 H1 实现本 generation 的新凭据签发或独立核对，保持 owner/workspace 关联，不认识的未来认证表使放行继续阻塞。G5 密钥解密成功仅证明能读，不证明供应商 token 仍被授权。

权限审批和付费审批必须分开。恢复上午快照会丢失下午账单，恢复当日同样危险。保留 UNKNOWN、RESERVED 和所有历史费用，不能制造零费用或退款记录。付费需外部账单对账，或独立离线批准下一 UTC+8 自然日才生效，并先隔离旧消费者；两个库的锁不能限制两个实例共同花钱。日期变化本身不是批准。F6 不实现解除隔离、H1 全生命周期、KMS 或个人实例切换。

回执 elapsed_seconds 使用单调计时器，覆盖预检、解密、导入、隔离及离线验证。它是小规模合成数据在本机的“到已验证隔离库”的实测耗时。人工作业、服务恢复、真实异地拉取、真实生产数据量与生产 RTO/RPO 均未测量。调度与报警由部署侧另行配置，本次没有修改任何实际日程。
