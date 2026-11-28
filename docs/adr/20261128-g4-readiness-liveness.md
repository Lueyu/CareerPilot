# G4 存活与就绪探针

目标是让仍能响应 HTTP 的 API 在数据库故障时保持存活，同时停止报告就绪。

`/healthz` 保留原有响应，个人版的 scorer_model 字段也保留。它不查询数据库、恢复状态、API token 或限流账本。公开版沿用既有 Host 校验，所以调用者必须提供配置的公开 Host。

`/readyz` 使用共享的 `app.readiness.readiness_response(service)`。个人版和公开版只传各自服务名。返回固定的 `{ok: bool, service: str}`，成功为 200，依赖不可用为 503，并发送 `Cache-Control: no-store`。探针不返回数据库连接串、地址、用户名或异常详情。

每次请求独占一个数据库 session。一个 1 秒 asyncio 超时包含获取连接、`SELECT 1`、在同一 session 上执行 `require_normal` 和正常退出。恢复控制行缺失、隔离、来源不匹配都不能就绪。探针不写恢复控制状态，不自动登记新安装，不缓存上次成功，也不把 worker 刚启动作为失败条件。正常返回前会释放 session。取消属于协作式取消，清理可能短暂超过截止时间，不能把 1 秒解释成操作系统级硬期限。

可选方案是把查询、恢复检查和异常策略复制到两个入口。这样少一个模块，却增加两套政策漂移的机会。这里选择共享的小函数，复用既有 SessionLocal 和恢复闸，不另建健康状态缓存。仅预期的 SQLAlchemy、asyncpg、操作系统连接、超时和 RecoveryBlocked 异常转换为安全 503。任务取消和程序错误继续传播。

个人 Compose、公开 Compose 和租户生成 API 探针改用 readiness。worker 继续依赖 API 的 service_healthy，不反向成为 API 就绪条件。公开探针仍携带正确 Host，客户端超时 2 秒，Docker 超时 3 秒。个人 Docker 超时保持 3 秒。本地 Nginx、候选预览 Nginx 和公开 Caddy 为探针精确反代，避免 SPA 的 HTML 200 冒充健康。候选预览仅改变 CareerPilot 检查路径，Squabble 仍用原路径。

数据库可查询只证明本次会话能够完成两项检查，不证明全部业务表、worker、队列、供应商、SMTP 或付费额度健康。Docker unhealthy 是状态；unless-stopped 不会因此自动重启正在运行的容器。depends_on 主要约束启动顺序，不是持续摘流。正式环境还需由反代或编排器消费 readiness，并为 liveness 单独配置适当的恢复策略。

源码验收使用新建 *_test PostgreSQL 和合成身份，覆盖两入口匿名请求、拒绝连接、耗尽连接池、锁住恢复表、隔离/缺行、并发存活、故障解除、取消和错误脱敏。配置断言只证明源码契约；父运行另对冻结镜像和真实反代执行数据库停止/恢复观察。报告必须分别引用这两类证据。未做生产部署，也不自动解除恢复隔离。
