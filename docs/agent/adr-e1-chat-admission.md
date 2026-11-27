# E1 第 1 波聊天生成准入

日期为 2026-11-28。此决定只覆盖临时并发闸，持久队列迁移留在第 5 波。

## 背景

聊天入口先保存用户消息和 assistant 占位，再通过 FastAPI BackgroundTasks 执行生成。这个历史设计让回复在浏览器断线后继续，并以消息快照支持 SSE 重连。消息已有 attempt fencing、短租约和 heartbeat。它没有跨会话容量限制，项目 worker 的串行规则也不适用于 API 进程。

## 选项

复用 StudyMessage 的活租约能够在原事务内判断 accepted 容量。独立 generation reservation 表能记录更详细的 attempt 占位，但需要新的迁移、租约和恢复状态机。当前选择复用消息，避免提前实现持久队列。

## 决定

入口保持现有 Workspace 行锁，再用事务 advisory lock `20261128,1101` 串行检查同库活消息总数。只计算 assistant、generating 且 expires_at 未过期的消息。锁一直保持到用户和 assistant 消息一起提交。锁内不调用网络，不跨工作区更新消息，避免与其他请求持有的行锁形成倒序。

`STUDY_GENERATION_WORKSPACE_LIMIT` 默认 2，`STUDY_GENERATION_GLOBAL_LIMIT` 默认 4。前者限制一个工作区的活 accepted 生成，后者限制同一数据库里的活 accepted 生成。所有 API 进程必须配置一致。幂等重放先于容量检查。同会话正在生成仍返回 409。容量不足返回 429 和 Retry-After 2 秒，拒绝时不保存消息、改变会话、记录帮助暴露或递增 retry attempt。确定性的题库清单不调用 provider，不占生成容量。

进程内 Semaphore 的上限复用 global limit，许可覆盖检索、路由、provider、备用调用和所有 finally 清理。许可等待最多 `STUDY_GENERATION_WAIT_SECONDS`，默认 5 秒且不超过接受时剩余租约。取得许可后重新读取 status、attempt 和 expires_at。等待超时将仍有效的消息标为 failed；旧 attempt 和已取消消息不会启动 provider。排队等待者不续租，没有后台持久队列。租约到期后由原恢复逻辑收敛为 interrupted。

BackgroundTasks 注册移动到提交前。注册抛错时，请求事务整体回滚。进程在提交后消失仍依赖短租约恢复。trace 沿用消息 request_id，增加 accepted、running、completed、failed、cancelled、retry、expired 的生命周期记录，保留原 provider calls。

## 后果与边界

DB accepted 容量跨进程成立。Semaphore 约束单 API 进程的实际执行，不是所有 API 和 worker 的全局 provider 许可。取消或过期可以先释放 DB accepted 名额，旧 provider 清理完成之前仍占本进程 Semaphore。多个进程的实际调用可以在这个时间窗口重叠，不能把 accepted 上限宣传为跨进程实时 provider 上限。

Semaphore 在首次执行时读取配置，修改配置需要重启进程。许可等待有时间限制，同时用进程内计数将实际等待者限制到 global limit，避免取消与重试反复替换活消息时积累失效等待者。这个计数在同一事件循环内先增加再 await，并在 finally 中减少。这一波不提供公平调度、持久积压、自动重放、outbox 或进程排空。第 5 波应让持久任务拥有 accepted/running 状态，并结合 outbox、worker 租约和优雅停机替换 BackgroundTasks。

验证使用隔离 PostgreSQL 和合成 provider。并发请求检验原子性，取消、过期、重试和 provider 异常检验许可释放及 fencing。真实供应商计费、断网后远端是否停止计算和多进程实际调用总数未在本项验证。
