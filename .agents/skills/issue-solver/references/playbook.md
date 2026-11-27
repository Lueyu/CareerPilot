# 分领域实施要点（2026-11-27 二轮代码复审与上海 AI Agent 社招聚焦版）

> 本文件是 issue-solver 的领域参考：动手前只读与当前条目相关的段落。
> 条目四段详情（现状/代码/为什么/目标方案）在 KNOWN_ISSUES.md，此处只放**跨条目的设计决策、复用基建与坑**。
> **聚焦原则**：第 0–3 波（约 5 周）是上海 AI Agent 社招核心竞争力主战场（评测校准 + LLM 网关 + Hybrid RAG + Tool-Calling Loop + Memory 闭环 + 上下文/成本）；第 4 波打磨分布式可靠性；第 5 波为通用 Web/SaaS 运维与进阶项，冲刺期不抢占主战场时间。

## 全局设计哲学（所有改动必须遵守）

1. **确定性节点 vs LLM 节点**：路由、检索、评分聚合、能力更新、状态机全是代码；LLM 只做受控生成/判档。LLM 永远不直接改状态、不算总分、不推进工作流。新增能力不得破坏此边界。
2. **fail-closed**：评分类失败不给假分；证据不足保持 UNKNOWN；策略激活默认熔断。新增闸门宁可拒绝不可放水。
3. **来源边界**：user_statement ≠ repository_observation ≠ 能力证据。材料入库/学习完成/点击都不产生能力证据。
4. **先评测再升级**：D1（judge golden set）与 A4（检索评测集）是一切质量类升级的前提；没有基线数字的"优化"不算完成。
5. **测试纪律**：conftest 强制 `_test` 库+清空 LLM 凭据；三层 mock（FakeAdapter / httpx.MockTransport 字节级 SSE / 假 provider+真进程 kill-restart）；真实模型验证必须 opt-in 显式声明。新测试沿用此分层。

## 可复用的既有基建（新代码优先复用，不要另起炉灶）

| 基建 | 位置 | 复用场景 |
|---|---|---|
| 租约+fencing+advisory lock 队列 | `assessment_jobs.py:126-195`、`project_jobs.py:158-173` | E1 队列化、F1/F2、任何新异步任务 |
| outbox 原子落盘 | `assessment_worker.py:31-45` | F1 改事务性 outbox 表时保留语义 |
| 预留-结算成本账本（唯一真计价） | `public_model.py:119-188`（advisory lock 20261123,7401） | E2 推广到私有路径的模板 |
| prompt 版本+sha256 落库 | `llm_judge.py:145-197`、`project_prompts/__init__.py` | D4 统一所有 prompt 的模板 |
| 记忆审核状态机+级联失效 | `models/memory.py`、`learning_memory.py:43-88` | J3 映射候选审核、C4 整理、任何"候选→人工确认"流程 |
| 优雅释放租约范式 | `podcast_jobs.py:263-271`（CancelledError→5s 内 release） | F4 推广到 project_worker/API |
| 注册版解析闸（子进程/rlimit/zip bomb/页数） | 注册版 text_extract 路径 | H4 抽共享模块的源头 |
| 注册版启动弱口令断言 | `public_main.py:32-33` | G6 个人版照抄 |
| TTFT 四口径+配对 A/B 基准 | `llm_stream.py:144`、`bench_study_chat.py`（--kind fixture\|real）、`acceptance/study_visible_latency.cjs` | E4/C3 收益量化直接用，不要新写计时 |
| 路由冻结评测集 | `acceptance/auto_quality_v1.json`（SHA-256 固定）+ `auto_quality.py` | B2 长尾扩展、A4 评测脚本风格参照 |
| 幂等迁移执行器 | `server/migrate.py` + `migrations/*.sql` | 新表/列走 SQL 幂等文件；F5 前不要引入 alembic 双轨 |

## A 检索（A1–A6）
- 升级顺序锁死：A2 查询理解修复（PR #37 工作树在 `.agent-runs/20261126-chat-library-ops/library/`）→ A4 基线 → BM25（jieba 离线分词存 tsvector，**不装 PG 中文扩展**）→ pgvector+bge-small-zh → RRF 融合 → 可选 rerank。每步跑 A4 评测出 recall@12/MRR 增量。
- A3 契约：sources 摘要必须带 total_hits/shown/truncated；清单类请求确定性直出绕过 LLM（PR #37 已含范式）。
- 坑：`library_terms` 停用词必须按长度降序替换；`study_chat_context.py` 是 A1/A3/A6/B1 共享文件，同波多人需明确分工。
- A5 用 tree-sitter 而非正则猜符号；保留 `study_evidence_search.py` 字面分作 fallback。

## B 编排（B1–B6）
- B1 受限工具环：白名单工具+≤5 轮+token/时间预算+逐轮 trace；外层代码兜底越界。工具 schema 设计先行，检索实现等 A1。**不要**顺手把主链路改成全自主 agent——workflow 主链路是产品正确性边界。
- B2 三级路由：正则→小模型→低置信度问用户；决策+置信度落 trace（复用 `models/trace.py` DecisionRecord）。
- B3/B4：条件边由**确定性评分信号**驱动；保持 expected_revision 乐观锁与"LLM 只读状态"。
- B5 灰度激活前置：守护指标（得分率/复测率）+自动回滚+小流量；评估目前是投影不是实验，激活前必须有真实对照。

## C 上下文（C1–C4）
- C1：tiktoken 对 OpenAI 系；未知模型保留字节估算但**改注释**（"conservative upper bound"对英文不成立）+安全系数；D6 溢出恢复依赖它。
- C2 摘要必须 schema 化保数字/边界/决策（产品题库 `answer_gen.py:96` 自己写着反面案例）；pinned facts 永不截断；摘要调用计入预算。
- C3 缓存键：评分=(input_hash, rubric_version, model_fingerprint)；prompt 排布稳定前缀前置。缓存正确性用 D1 golden set 验证。

## D 评估（D1–D8）
- D1 是全计划杠杆最大的一项：30–50 题人工逐项判档（复用 rubric 5 档锚点格式），指标=逐 criterion 一致率+Cohen's kappa+总分 MAE；评测脚本风格照 `auto_quality.py`（冻结夹具+SHA+结果落 docs/benchmarks/）。
- D5 影子复核：把 `acceptance/coach_shadow_judge.py` 的 READ_ONLY_SHADOW 产品化为任务；同时修 `locales/zh-CN.json:272` 的文案（当前承诺了不存在的功能）。
- D2 先只对 project 类回答启用（有代码证据可核对）；用 cheap 模型抽检，统计违规率再扩面。
- D7 若做沙箱：一次性容器+网络禁用+资源限额+输出限额；执行证据进同一 rubric 链。不做就写 ACCEPTED ADR，不许静默搁置。

## E 成本性能（E1–E5）
- E1 分两步：第 1 波只加 per-workspace 并发闸+全局 Semaphore（**不动队列**）；第 5 波与 F1/F4 一起正式队列化。写 ADR 记录 BackgroundTasks 的历史原因。
- E2 照抄 public_model 账本模式；按模型配输入/输出单价；项目分析 DEFAULT_BUDGET 给非空默认。
- E4 收益量化用 bench_study_chat 配对 A/B（candidate_minus_baseline_ttft_ms），报 p50/p95 差不报均值。

## F 基建（F1–F8）
- F1 事务性 outbox：结果与状态同库同事务；保留"接管者重放不再付费"语义；补跨进程重放测试。
- F3 CI：PG service container + `run_isolated_tests.py`；**不要**在 CI 里连个人库或带真实凭据。
- F4：worker 信号处理照 podcast_jobs 范式；compose 加 stop_grace_period；uvicorn --timeout-graceful-shutdown。
- F6：restore.ps1 当前只允许 *_test 库——加"恢复到生产"受控流程时先备份现库+显式确认；同步修 runbook 矛盾（`ECS_DEPLOY_RUNBOOK.md:517`）。
- F7/F8：统一错误信封带 request_id（依赖 G1）；静默吞错改 warning+失败计数，降级语义保留。

## G 观测运维（G1–G7）
- G1：request_id 中间件生成→响应头回传→落 AgentMetric/Trace→worker 任务携带；AgentMetric 补 workspace_id（当前从不填）。
- G3 最小告警走已有 SMTP 通道（feedback 配置）；阈值：心跳年龄/队列积压/日成本环比/错误率/备份新鲜度。
- G4：liveness=进程活着，readiness=DB SELECT 1；compose healthcheck 改打 readiness。
- G5：Fernet 信封加密，主密钥进 env 不进 DB；迁移存量明文行；备份加密（F6）兜底。

## H 安全（H1–H8）
- H2：URL 解析后校验私网/链路本地段（防 DNS rebinding）；/llm/test 不回显内部错误细节。
- H3：所有消费外部内容的 prompt 统一注入防护合同（参照 `locales/zh-CN.generation.json:109` study_chat.context_contract）；面经提取 `bank.py:642-700` 是 current 缺口。
- H4：抽注册版解析闸为共享模块，个人版/注册版共用——这也是 I4 共享层的第一块。

## I/J 工程与测量
- I2 ADR：每决策一页（背景/选项/决定/后果）；ACCEPTED 项必须有 ADR。
- J1：先信度（间隔重测相关）后效度（vs 真实面试自报）；样本不足保持 UNKNOWN 语义。
- J3→J2 顺序锁死：映射覆盖是复测运转前提；J2 简化 SM-2 即可，与 B5 策略候选机制打通。

## 面试叙事提醒（完工记录时顺手积累）
每项完工在 issue-log.md 记"数字或回执"：基线→改后对比、故障注入结果、一致率。这些是 M1–M5 里程碑素材（见 KNOWN_ISSUES.md 附录）。红线：不声称 RAG/自主 agent/校准画像/沙箱执行/策略生效——除非对应条目真正 DONE 且有证据。