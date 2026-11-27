# CareerPilot 设计模式地图

> 目的：把项目里**已经在用**的设计模式集中说明——在哪、怎么用、为什么这么选。面向学习理解与面试复盘。
> 引用基于 2026-11-23 lint 门禁提交（ruff import 排序后）的当前代码；行号会随后续提交漂移，以符号检索为准。2026-11-27 依据本地 `agent_metrics` 的只读诊断与已合入修复更新了「已知坑」结论。
>
> 项目总原则（一切模式的出发点）：**LLM 只承担语义任务（生成/评分/教学内容），一切业务判定（verdict、能力状态、发布风险、进化阈值）都是确定性代码。**

## 1. 适配器（Adapter）

- 位置：`server/app/services/llm.py:75` `class AgentAdapter`（`chat_with` 统一对话接口）；`server/app/services/llm.py:33` `get_llm_config()` 每次调用拉取配置
- 用法：上层服务（study_chat、评分、教学）只依赖 `AgentAdapter.chat_with`，不感知具体供应商/网关；配置可按调用路由覆盖（tier、模型、output_limit）
- 为什么：换模型/换网关不动业务代码；成本与档位策略集中在配置层
- 面试一句话：LLM 适配器 + 每调用拉取配置，供应商可插拔、业务零改动。

## 2. 策略路由（轻/重分级 + 预算分配）

- 位置：`server/app/services/study_routing.py` 的 `POLICY_VERSION`、`tutoring_intent()`、`tutoring_profiles()`、`profile()`；`server/app/services/study_chat.py` 的 `_generate()` / `fit()`（额度取 `min(remaining_budget, available, candidate.output_tokens)`）；换档护栏为 `allowance >= 256` 且「正文已发布就不切换」
- 用法：按动作复杂度分配模型档位与输出预算，重动作（project/improve/diagram…）走 strong，轻动作走 flash；`study_output_budget=16000`，档位上限 `study_flash_output_tokens=6000` / `study_strong_output_tokens=12000`（`server/app/config.py`）
- 为什么：成本与时延预算按需分配，轻问题不值得烧重模型
- 结论修正（2026-11-27 实测）：旧记录把 `invalid_response` 归因于「`interview_question` 走 flash 档、2300 上限被 reasoning 吃满导致输出截断」。对本地 `agent_metrics` 29 行的只读诊断推翻了这一假设：7 次 `invalid_response` 全部是 `kind=podcast_script`、`model=deepseek-v4-pro`、`finish_reason='stop'`，没有一次是 `length`；2 次约 150 秒的超时同样落在播客讲稿路径。因此没有证据支持「调高输出预算」或「盲切快档」，这两个方向已撤回；`llm_stream.py` 中 `output_truncated` 的判定仍在，但本批样本不构成它的证据。

## 3. 流式协议 + 严格校验（防腐层）

- 位置：`server/app/services/llm_stream.py:32` `ChatCompletionStream`、`:25` `class StreamFailure(ValueError)`（带 error kind：output_truncated / timeout 等）、`:15` `StreamLimits`
- 用法：SSE 流在统一边界解析、限长、校验；失败分类成结构化 kind，上层按 kind 决定重试/升级/报错
- 为什么：模型输出是不可信输入——在边界处校验一次，业务层不再各自防御
- 面试一句话：把"不可信模型输出"关进防腐层，业务只认结构化结果。

## 4. 超时预算与降级（多段调用共享总时限）

- 位置：`server/app/services/study_chat.py` 的 `deadline = time.monotonic() + timeout`（外层总时限，仍只算一次）、`attempt_deadline()`（单次尝试子截止）、`_generate()` 内 `remaining = attempt_deadline(...)`；`server/app/config.py` 的 `study_timeout_seconds`（默认/上限 1800s）、`study_attempt_timeout_seconds`（默认 150s）、`study_first_content_timeout_seconds`（默认 60s）
- 用法：多次重试/升级共享一个总 deadline 与 token 预算；每次尝试另有独立子截止，额度按剩余量收缩
- 已修（2026-11-27）：旧记录说「deadline 只算一次，升级调用继承剩余窗口，首档卡死后几乎没有余量」。现由 `attempt_deadline()` 给每次尝试独立子截止（两次机会时首次不超过剩余时间的一半），并在流式调用上加了首个可见正文超时。总 deadline、「正文已发布不切换」与 token 预算三条边界保持不变。

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
| 总 deadline 与子截止并存 | `study_chat.py` `attempt_deadline()` | 已修（2026-11-27），外层仍保留总时限护栏 |
| 测试全局数据泄漏 | `test_question_workshop.py:238` | 待修（加 cleanup） |
| flash 档位与思考模型错配 | `study_routing.py` `tutoring_profiles()` | 截断假设已被 2026-11-27 实测推翻，不再列为待修；改档须先做配对 A/B |
