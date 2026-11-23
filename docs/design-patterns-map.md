# CareerPilot 设计模式地图

> 目的：把项目里**已经在用**的设计模式集中说明——在哪、怎么用、为什么这么选。面向学习理解与面试复盘。
> 引用基于 2026-11-23 lint 门禁提交（ruff import 排序后）的当前代码；行号会随后续提交漂移，以符号检索为准。
>
> 项目总原则（一切模式的出发点）：**LLM 只承担语义任务（生成/评分/教学内容），一切业务判定（verdict、能力状态、发布风险、进化阈值）都是确定性代码。**

## 1. 适配器（Adapter）

- 位置：`server/app/services/llm.py:75` `class AgentAdapter`（`chat_with` 统一对话接口）；`server/app/services/llm.py:33` `get_llm_config()` 每次调用拉取配置
- 用法：上层服务（study_chat、评分、教学）只依赖 `AgentAdapter.chat_with`，不感知具体供应商/网关；配置可按调用路由覆盖（tier、模型、output_limit）
- 为什么：换模型/换网关不动业务代码；成本与档位策略集中在配置层
- 面试一句话：LLM 适配器 + 每调用拉取配置，供应商可插拔、业务零改动。

## 2. 策略路由（轻/重分级 + 预算分配）

- 位置：`server/app/services/study_routing.py:4` `DEEP_ACTIONS`（哪些动作走 strong 档）、`:9` `decide()`、`:29` `profile()`；`server/app/services/study_chat.py:246` `allowance = min(budget, available, 4500 if selected == "strong" else 2300)`；`:304` 升级下一档的护栏（budget ≥ 256 且剩余时间 ≥ 5s）
- 用法：按动作复杂度分配模型档位与输出预算，重动作（project/improve/diagram…）走 strong，轻动作走 flash
- 为什么：成本与时延预算按需分配，轻问题不值得烧重模型
- 已知坑（2026-11-23 诊断）：`interview_question` 不在 `DEEP_ACTIONS` → flash 档 2300 上限被思考型模型的 reasoning 吃满 → `invalid_response`（修复方案 A/B/C 待定）

## 3. 流式协议 + 严格校验（防腐层）

- 位置：`server/app/services/llm_stream.py:32` `ChatCompletionStream`、`:25` `class StreamFailure(ValueError)`（带 error kind：output_truncated / timeout 等）、`:15` `StreamLimits`
- 用法：SSE 流在统一边界解析、限长、校验；失败分类成结构化 kind，上层按 kind 决定重试/升级/报错
- 为什么：模型输出是不可信输入——在边界处校验一次，业务层不再各自防御
- 面试一句话：把"不可信模型输出"关进防腐层，业务只认结构化结果。

## 4. 超时预算与降级（多段调用共享总时限）

- 位置：`server/app/services/study_chat.py:118` `generate_reply(message_id, attempt)`、`:131` `deadline = time.monotonic() + timeout`（只算一次）、`:250` `remaining = deadline - time.monotonic()`、`:293` `budget -= min(allowance, 实际 completion_tokens)`
- 用法：多次重试/升级共享一个总 deadline 与 token 预算；每段调用按剩余量收缩 allowance
- 已知坑：deadline 只算一次，升级调用继承剩余窗口（上次 55.3s → 升级只剩 64.5s → timeout 的根因之一；修复 C 待定）

## 5. Worker + 租约 + 心跳（长任务出 API 进程）

- 位置：`server/app/services/assessment_worker.py:123` `execute_claim(claim, worker_id, lease_seconds=45, heartbeat_seconds=10)`、`:300` `reconcile(factory)` 对账兜底；同类：`project_jobs.py`
- 用法：评分/项目分析等长任务由 Worker 认领执行；租约防并发抢占、心跳续租、`reconcile` 收敛遗留状态
- 为什么：浏览器与 API 不扛长任务；检查点落 PG（`project_analyses`/`project_analysis_steps`），崩溃可恢复
- 面试一句话：租约式 Worker + 检查点，长任务崩溃恢复不重不漏。

## 6. 状态机 + 阶段 DAG

- 位置：`server/app/services/project_autopilot_engine.py:31` `MODEL_STAGES`（frozenset，哪些阶段需要模型）、`:34` `class StageError(ValueError)`、`:302` stage 携带 `requires` 依赖表
- 用法：分析流水线拆成有依赖的阶段图，模型阶段/纯代码阶段显式区分；每阶段一个检查点，失败定位到阶段
- 为什么：134 个检查点的长流水线可恢复、可观测；阶段错误分类（14 类 StageError）决定重试策略

## 7. 幂等发布 + Outbox

- 位置：`server/app/services/assessment_worker.py:28` `outbox_path`、`:35` `save_outbox`、`:75` `cached_result`、`:187-246` 发布状态机（`_publication_state` / `_begin_publication` / `_publish` / `_stop_publication` / `_record_publication_failure`）
- 用法：评分结果先落 outbox 再原子发布（报告 + Evidence + 画像快照 + 学习任务）；重放先查 outbox 缓存，不重复计分
- 为什么：发布是多写操作，必须幂等；"重试不重复下单"的同源思想

## 8. 证据驱动 + UNKNOWN（能力估计）

- 位置：`server/app/services/competency.py:18` `assessed_observations(evidences, include_assisted=False)`（过滤合格证据）、`:76` `recompute_skill_states(session, workspace_id)`
- 用法：能力状态只从满足条件的真实作答证据重算；证据不足显示 `UNKNOWN`
- 语义红线（不可改）：材料≠能力证据；UNKNOWN ≠ 0；同源重练更新表现但不累加独立覆盖

## 9. 确定性规则兜底

- 位置：`server/app/services/assessment_worker.py:43` `rule_result(payload)`；`server/app/config.py` 关闭特性不因环境变量打开（`PUBLIC_WRITES_ENABLED` 等 monkeypatch 也打不开，有测试守卫）
- 用法：`LLM_PROVIDER` 可空 → 规则引擎跑通全链路（CI 无模型可测）；评分总分=确定性计算，模型只产档位
- 为什么：可测性 + 可审计：业务判定可离线复现

## 10. 配置单源（Settings 单例）

- 位置：`server/app/config.py:13` `class Settings(BaseSettings)`、`:93` `get_settings()`（缓存）
- 用法：一切配置走 Settings + `.env`；测试显式 `_env_file=None` 隔离

## 11. 测试隔离 harness + 模型替身

- 位置：`server/run_isolated_tests.py`（每套件 CREATE DATABASE `careerpilot_suite_*_test` 并注入 `CAREERPILOT_TEST_DATABASE_URL`）；模型替身 = `calls/outcomes` 列表注入（`server/tests/test_study_interview_question.py`）
- 反例教训（2026-11-23 实录）：`test_question_workshop.py:238` 创建 `workspace_id=None` 的 PUBLIC 题泄漏进后续测试 → 全量跑时 `test_study_interview_question` 的 `/bank` 隔离断言失败（单文件跑 18/18 通过）。**规范：测试不得创建跨工作空间全局数据，必须则必清理。**

## 12. 幂等迁移器（刻意取舍）

- 位置：`server/migrate.py`（create_all + 幂等 SQL 迁移；`server/migrate_p6.py` 为 P6 扩展）
- 为什么不用 alembic：单机本地版迁移需求简单，migrate.py 可重复执行零副作用（KNOWN_ISSUES A1 的取舍，不是遗漏）

## 13. 前端集中 API client

- 位置：`web/src/api/client.ts`（417 行单文件）
- 用法：页面组件不自己 fetch，统一走 client（鉴权头、错误归一、类型）
- 为什么：后端契约变更只改一处；类型集中可审计

## 14. 双通道共用审核（幂等草稿）

- 语义：API 显式生成与外部聊天回填走同一审核入口；回填只做校验/排版/保存，不暗中调 LLM；审核前草稿不成为可选考试题；重复提交幂等、canonical 关系不丢

## 已知坏味道（诚实记录，重构候选）

| 坏味道 | 位置 | 状态 |
|---|---|---|
| 巨石路由层 | `server/app/api/v1/bank.py`（1318 行）、`exams.py`（1132 行） | 阶段 3 候选，未动 |
| 巨石服务 | `server/app/services/project_autopilot_engine.py`（1106 行） | 观察中 |
| deadline 单次计算 | `study_chat.py:131` | 修复方案 C 待用户选择 |
| 测试全局数据泄漏 | `test_question_workshop.py:238` | 待修（加 cleanup） |
| flash 档位与思考模型错配 | `study_routing.py:4` + `study_chat.py:246` | 修复方案 A/B 待用户选择 |
