# ADR 0001: 后台任务全局单并发（advisory lock 串行认领）

- 状态: `ACCEPTED`（记录既有实现）
- 日期: 2026-11-27
- 关联: `KNOWN_ISSUES.md` F2 / E1；代码 `../../server/app/services/project_jobs.py`（`claim_step`）、`../../server/app/services/assessment_jobs.py`、`../../server/app/services/podcast_jobs.py`
- 决策者: Lueyu

## 背景

LLM 调用是主要成本来源与限流风险点。后台评分、项目分析由 worker 从持久队列消费；
多个 worker 进程可能同时认领，需要保证「同一时刻只有一个 RUNNING 步骤」，避免重复调用
provider、重复计费和租约竞争。`project_jobs.py` 的注释写明决策意图：
「Serialise the very short claim decision, not provider execution. Global concurrency is
deliberately one, including multiple Worker processes.」

## 决定

用 PostgreSQL 事务级 advisory lock `pg_advisory_xact_lock(20261117, 0)` 串行化**认领决策**：
锁只覆盖极短的认领事务，不覆盖 provider 执行窗口；认领时检查是否存在
`status == 'RUNNING'` 且租约未过期的步骤，存在则本次认领返回 `None`。
跨进程并发度因此固定为 1，且无需引入额外的分布式协调组件。

## 备选方案

| 方案 | 放弃理由 |
|---|---|
| per-workspace 分片并发 | 需要分片键、竞争消费正确性与 fencing 实测（F2 尚未做），现阶段会把成本风险放大 |
| 全局 `asyncio.Semaphore` | 只约束单进程，多 worker 下失效；进程重启后不持久 |
| 无锁乐观认领（唯一约束 + 冲突重试） | 冲突时仍需判定谁生效，语义复杂度高于 advisory lock，且无法表达「全局仅一个」 |

## 后果

**收益**：成本可控、无并发账单雪崩；认领路径短、锁竞争小；崩溃后靠租约过期恢复，
与项目已有的租约/fencing 基建一致。

**代价与已知短板**：

- 吞吐被锁死在单步串行，无水平扩展能力，也没有多 worker 竞争消费的实测证据（F2）。
- 队列语义不统一：`assessment_jobs.py`、`project_jobs.py`、`podcast_jobs.py` 各有一套租约实现，
  存在语义漂移与维护面扩大（F2）。
- **不得对外宣称「全局单并发控制所有 LLM 成本」**：`KNOWN_ISSUES.md` E1 已指出聊天生成走
  FastAPI `BackgroundTasks` 绕过队列，单会话虽有 409 保护，但一个用户开 100 个会话可并发
  100 个生成（每个最长 30 分钟），且 API 进程重启会丢在飞生成。

## 复核触发条件

- E1 落地「聊天生成并入持久队列」或至少「per-workspace 并发闸 + 全局 Semaphore」；
- F2 落地统一队列抽象并补齐多 worker 竞争消费 / fencing 正确性测试。

任一发生时必须回来更新本 ADR 的并发边界描述，或新建 ADR 把本文标为 `SUPERSEDED`。
