# 后台凭据加密与存量迁移

LLM、GitHub、GitCode 的 AppSetting 凭据字段以 Fernet 信封落库。密文中认证设置 key 和完整字段路径，包括 workspace id。把合法密文复制到另一行或字段也会拒绝。API 继续返回 configured、active 和原有 token_hint，不导出密钥。工作空间数据导出不包含 AppSetting。

## 部署密钥

使用 Python cryptography 的 `Fernet.generate_key()` 在安全的部署密钥管理环境生成一次密钥。它是 32 字节随机数据的 URL-safe base64 编码，不是自选口令。通过 secret manager 将结果注入 API 和所有 worker 的进程环境 `CAREERPILOT_SECRETS_KEY`。应用不从数据库或 Settings/.env 文件读取主密钥。不要把结果粘贴到聊天、工单、日志、shell 历史、Git、API 请求或数据库。不要在每次启动时重新生成。

主密钥需要独立保管和恢复演练。数据库与归档里只有密文。丢失主密钥后不能恢复凭据，只能通过显式清除覆盖后重新录入。应用内存与已获部署环境访问权的攻击者不在静态加密的保护范围。旧备份和 PostgreSQL 旧页/WAL/历史快照可能仍有迁移前明文，需按凭据轮换与存储介质策略处置。G5 不声称安全擦除历史明文，也不替代 F6 的归档加密。

根 docker-compose.yml 的共享后端 environment 已把宿主部署环境中的 CAREERPILOT_SECRETS_KEY 传给 server 与 worker，不传给 postgres 或 web。不要把值写进 Compose 文件或 .env。原生 Python 进程继承操作员注入的环境。其他定制容器/租户编排需要操作员用其 secret 注入机制提供同一个主密钥；未配置时只能使用原有 env-only 凭据，保存或读取 DB 中的非空秘密会拒绝。

## 显式离线转换

1. 在获准的维护窗口停止该目标数据库的 API、scheduler 和 worker 写入。本次自动交付只在新建 `_test` 数据库验证，没有操作个人实例。
2. 从部署密钥管理器注入主密钥。单独设置 `G5_TARGET_DATABASE_URL` 为操作员确认的完整 `postgresql+asyncpg://...` DSN。工具不会读取应用默认数据库。DSN 通过环境提供，避免出现在命令参数中。
3. 在与目标相同版本的 `server/` 运行 `python migrate_secrets.py --database-url-env G5_TARGET_DATABASE_URL --apply`。Docker 镜像也包含该文件，使用相同版本的维护命令。不要把命令加到自动启动流程。
4. 成功只输出 `checked_rows` 与 `changed_rows` 数量。重复执行应为 `changed_rows: 0`。已有密文会验证当前密钥、版本和上下文后原样保留。
5. 非零退出表示命令未确认完成。行损坏、错钥或未知版本等提交前校验错误会回滚整笔事务，包括已 flush 的前面行；提交响应或清理阶段异常时，需要查证实际数据状态，不能仅凭退出码断定已经回滚。修复前置后用同一密钥重跑，已转换字段验证后保持不变。工具不会打印 DSN、token、密文、key 或底层异常。
6. 先确认迁移结果，再用同一主密钥启动 API 与 worker。运行设置读取与一次受控连接测试。实际付费调用仍需要 E2 可信价格和预算，不能用关闭账本来检查加密。

迁移获取 app_settings 表 EXCLUSIVE 锁，持有到一次事务提交，阻止同时写入造成半迁移。表很大时要为维护窗口和锁等待留出时间。没有在线明文兼容期。未转换的非空秘密会返回安全的 503，调用不会回退到环境凭据。缺钥、错钥、篡改、搬运、未知版本同样拒绝。新密钥不是换上就能读旧数据，本次未实现自动轮换工具。

## 日常设置语义

LLM 更新中省略、null、空或空白 api_key 都保留原密钥；修改 provider/base_url 时仍需对应新密钥。DELETE 明确删除 DB 覆盖并允许既有部署环境默认值恢复。GitHub/GitCode 的空 token 仍拒绝，DELETE 移除对应覆盖。损坏记录不会通过普通 PUT 被悄悄改写；操作员需要先查证并显式清除或离线修复。

异步 LLM/GitHub 读取每次查询并认证当前存储，不再信任旧 TTL 明文缓存。同步 active 只是界面/调度提示，不授予网络调用。一次已经取得凭据的在途请求不会被撤销，这不是 H1 token 撤销系统。strong 模型当前凭据来自部署环境；存量 llm JSON 内 api_key 后缀、token/password/secret 类字段及嵌套 profile 同样加密和验证，但本项不新增强模型设置 UI 或路由行为。
