# 核心闭环（Core Loop）AI 报告提炼

> 本文件提炼自 2026-11-16 前后「核心学习闭环修复」相关的 7 份报告/计划/规格文档（另 skim 了 2 份验收 JSON）。仅收录源文件中真实存在的内容，按「现象/结论 → 根因 → 修复或决策 → 文件/模块/接口」组织，不补充源文件之外的知识。

## 来源文件

1. `CORE_LOOP_ACCEPTANCE.md` —— 2026-11-16 核心闭环验收记录（15 项场景、真实模型运行、测试与恢复证据）。
2. `CORE_LOOP_FIX_REPORT.md` —— 2026-11-16 核心学习闭环修复报告（交付状态、修复后行为、数据与安全边界）。
3. `docs/CareerPilot_Codex_Core_Loop_Repair_Prompt.md` —— 用户 2026-11-16 确认的本轮范围与 15 项回归场景。
4. `docs/superpowers/plans/2026-11-16-core-loop-repair.md` —— Core Loop Repair 实施计划（架构、任务所有权、模块契约）。
5. `docs/superpowers/plans/2026-11-13-interview-bank-v2-p1-foundation.md` —— 题库 v2 · P1 地基实施计划（160 题拆分、build.py、SPA、notes 同步）。
6. `docs/superpowers/specs/2026-11-13-interview-bank-redesign-design.md` —— 面试题库 v2 重构设计（8 主题、架构方案 A、四种考题模式）。
7. `docs/superpowers/specs/2026-11-17-careerpilot-multica-handoff.md` —— CareerPilot × Multica 任务拆解 Prompt（后续阶段目标与 P0~P2 骨架）。
8. 辅助证据（skim）：`docs/core-loop/2026-11-16/verification-summary.json`、`real-checks.json`。

## 背景与目标

- 仓库为 Lueyu/agent-interview（本机 `D:/agent-interview`），原 `index.html` 是 1.26MB、160 题的单文件题库。
- 核心闭环定义为：**真实个人空间 → 新作答评分 → 本次表现证据 → 具体失分任务 → 学习 → 新题再评分 → 记录更新 → 重启/独立库恢复**。
- 用户于 2026-11-16 确认「本地直接修复」，保留 FastAPI/SQLAlchemy/PostgreSQL/React 与真实模型评分、教学、文本面经和持久化；只修最小闭环，不做校准测量或自动策略进化。
- 硬约束（修复 Prompt，用户原话整理，不扩展需求）：
  - 单题 0 分只代表本题表现、不能推断整域 0 分；整体可为 UNKNOWN，但观察数、缺失评分项、原答引用、下一步必须可见。
  - 跨域任务须有真实前置关系（本轮默认同域），不能凭空虚构前置知识。
  - 重复提交幂等；同题重复练习不增加独立证据覆盖；学习完成只记录进度；复测必须新作答。
  - 正式空间不回退 Demo、不生成固定语音、不以旧报告冒充本次结果；关闭后端及任务层公共写入、禁用策略自动激活，但保留本地草稿/预览与候选/事件。
  - 改前必须备份代码/原库、保留其他未提交文件；先失败测试后最小修复；真实模型验收与夹具验证分开。
  - 只新增兼容字段，不清空旧成绩或重建原库；仅破坏数据/扩大权限/公开发布/重大架构变更才暂停确认。
- 题库 v2（2026-11-13 设计+计划）是核心闭环的题目底座：160 题拆为 `content/questions/{code}/{NNN}.html` 每题一源文件 + `build.py` 生成 `data.js` + 单文件 SPA，8 主题（rag/harness/ctx/infer/eval/sys/algo/biz）；核心闭环真实验收用到的 `rag-001`、`rag-002` 即来自该题库。
- 后续方向（2026-11-17 Multica 交接）：目标 2027-02 面试 AI Agent 业务落地岗；每日训练闭环为周一至五 20 题/天、每周 100 题/40 小时，默认 10 新题 + 6 复习 + 4 未见场景（追问/订正/重复点击不增加主问题计数）；按 P0~P2 任务拆解推进，不恢复已停止的模型分析任务。

## 核心发现与结论

1. **主要根因（修复前的问题清单）**
   - 重复创建/重复提交会重复产生考试与证据（缺请求键与锁）。
   - 过期 GET 详情或「开始学习」请求会把已完成考试/任务覆盖，`DONE` 被写回 `IN_PROGRESS`。
   - 首次画像读取新建 SkillState 关联触发 `MissingGreenlet`；复测永久复用过期考试。
   - 总体题判 CORRECT 时原失分项未通过、具体缺失项被总体正确判定掩盖。
   - 教学真实运行曾混入同域泛化材料，须只取本空间原题冻结材料。
2. **幂等与并发设计（修复决策）**
   - `CreateExamReq` 新增 `skill_key`、`exclude_question_ids`、`retest_task_id`、`idempotency_key`，重复键返回同一考试，并强制工作空间归属与复测不同 canonical。
   - `Exam` 增加可空 `retest_task_id`、`idempotency_key`；提交与评分在数据库锁保护下执行。
   - 开始任务采用数据库条件更新，不能把并发已提交的 `DONE` 写回 `IN_PROGRESS`。
3. **数据模型（迁移 `server/migrations/002_core_loop.sql`）**
   - 仅新增任务来源、考试复测关联、请求键及唯一索引，不清空历史成绩、不重建原库。
   - `LearningTask` 增加 `source_exam_id`、`source_exam_question_id`、`source_evidence_id`、`source_criterion`、`gap_type`；观察细节/产物/复测结果存 `content` JSON。
   - 仅带新来源字段的记录能提供完整逐题关联，历史空字段如实显示未记录。
4. **证据与能力估计规则**
   - 新 Evidence 先在同一事务落盘，再重算画像和规划任务（`planner.plan_tasks_for_exam(session, workspace_id, exam)` 仅在 Evidence flush 后调用）。
   - 单题零分只是一条本次观察；少于 3 道独立有效题目的能力域保留 `UNKNOWN`。
   - 重复题及已关联变体保留历史表现，但独立覆盖按同一 canonical 去重、采用最近表现，历史 canonical 合并关系也参与去重。
   - 三题阈值、分数→等级映射、置信度上限只是保守规则，不是校准测量模型。
5. **学习闭环接口**
   - `POST /learning/tasks/{id}/complete`（接受可选 `artifact_text`）：只记录进度、不生成掌握证据、不能代替复测（RETEST 不能经此路由完成），从完成日起安排 1/3/7 天延迟任务。
   - `POST /learning/tasks/{id}/retest`：返回 `{exam_id}`，已完成主任务创建即时新题检查，到期前请求被拒绝（409）；前端打开 `/exam/diagnosis?id={exam_id}`。
6. **新题复测规则**
   - 复测选同域、不同 canonical 新题，优先覆盖原失分项；无可比较新题时原评分项仍显示未验证。
   - 复测记录分开展示「本题整体结果 / 原评分项是否覆盖 / 原评分项是否通过」。
   - 评分失败归属本次考试（`SCORING_FAILED`、`score=null`、无伪造总分、页面保留原答），可针对原考试重评；过期或放弃的复测可重新开题且旧记录保留。
7. **教学**
   - 教学接收原题、原答与失分项，使用本空间原题冻结参考材料，有来源时不混入同域其他题；私人材料也限定工作空间。
   - `source_context_version=2` 会刷新旧教学缓存；最终结果 `content.source=llm`、`llm_active=true`。
8. **安全边界**
   - 公开写入在发布 API、资产发布入口和 `GitHubPublisher._request` 两层关闭（远程非 GET 即使有 token 也拒绝，且不可由环境变量静默打开）。
   - Evolution 候选/评估/历史保留，但批准服务拒绝激活、消费方不采用已有 `ACTIVE` 策略。
   - 默认工作空间改为 `personal` 并幂等创建独立账号/成员关系，不复制 Demo 证据/记忆/答卷/任务；演示种子须显式 `--demo`。
9. **题库 v2 关键结论**
   - `build.py` 是唯一构建入口：`--selftest` 内置坏样例回归（P1 落地 54 个断言），`--migrate` 一次性迁移并做零丢失校验（标题清单一致 + 正文长度 ±16 字节、top10 链接重写为 `#/q/{qid}`）。
   - 解析坑一：`p2q4` 的 `data-title` 含 `>`（`如何处理Agent的长对话场景(>10轮)`），标签扫描必须跳过引号内的 `>`。
   - 解析坑二：追问「展开全部」按钮由旧 JS 动态注入，新壳渲染题目后要重新注入。
   - `notes.json` 解析失败时**有意不自动写 `.bak`**（浏览器无法在未授权时写第二个文件），兜底为 localStorage 全量镜像 + 导出/导入 + 菜单「立即写入」。
10. **Multica 交接确认的现状边界**
    - `OFFICIAL` / `PRACTICE` 评分已分流：练习反馈不更新掌握判断，正式发布才进入合格证据与画像历史。
    - `project_worker.py` 同时承担评分与项目分析（暂停重分析不能停掉所有评分服务）。
    - 发布快照中后端 678 项、前端 152 项测试通过只是历史机制/夹具证据，非本轮结果，也不证明评分准确或学习有效。
    - 长时间真实模型全仓分析已被用户停止，不恢复、不作为开始学习的前置条件；过去隔离任务的 1000 次模型调用许可不适用于本轮。

## 已落地与已验证

1. **测试结果（最终完整运行）**
   - 后端 **86 passed / 24.12 秒**；前端 **32 passed**；`tsc --noEmit` 与 Vite 生产构建通过。
   - 构建改用 `web/node_modules/.cache/esbuild-temp`，因为本机系统临时目录曾使 esbuild 报 Access denied；仍有约 2.37 MB 大包提示，不影响本轮本地闭环验收。
   - 阶段结果不并入最终计数：`test_core_scoring.py` 23 passed（含冻结评分、并发提交、过期读取竞争、首次画像读取、canonical 合并）；运维阶段 `test_local_safety.py` + `test_engines.py` 44 passed；并发开始任务 1 passed（真实 PostgreSQL 锁等待）。
2. **红绿证据**
   - 公共写入及激活防护原有 16 项失败→通过；个人工作区原有 2 项失败→通过；自定义 loopback 端口 CORS 400→200。
   - 审查定位并修复：过期 GET 覆盖完成考试、首次画像 `MissingGreenlet`、永久复用过期复测、原失分项未通过、具体缺失项被总体正确判定掩盖。
3. **真实模型链路**（独立库 `careerpilot_core_acceptance` + 示例答案；模型调用 4 次成功、1 次实际失败）
   - 考试 1 题目 `rag-001`「设计日均100万查询的企业级RAG系统」评分 0.5/5=10/100 判 `WRONG`（报告含原答摘录与「要点: 系统设计 / 高并发 / RAG」三个缺失项）。
   - Evidence `EV-X1-1`、RAG 观察数 1、等级 `UNKNOWN`；产生三任务、选任务 1「系统设计」（`source_exam_id=1`、`source_exam_question_id=1`、`source_evidence_id=1`，能力域仍 `rag`）。
   - 教学 `source=llm` → 完成 `DONE`、`mastery_updated=false`、延迟任务排 2026-11-17 / 11-19 / 11-23。
   - 考试 2 题目 `rag-002`「设计支持流式输出的RAG系统」首次 `SCORING_FAILED` 无总分、页面保留答案 → 原场重评 5/5=100/100 写 `EV-X2-1`。
   - 复测任务 7 `DONE`、原评分项覆盖并通过；Evidence 总数 2、RAG 独立观察 2、置信度 0.30、整体仍 `UNKNOWN`。
4. **幂等/并发实测**（`real-checks.json`）
   - 重复创建仍返回考试 1；重复提交后仍 2 场考试、2 条有效观察；复测重复点击仍返回考试 2。
   - 学习完成不更新掌握（`mastery_updated=false`）；提前启动延迟任务返回 409；教学 `source=llm`、`source_context_version=2`。
5. **备份与恢复**
   - 改前备份 `personal/backups/core-loop-20261116-155251/careerpilot.dump`（1,512,133 字节；另有 HEAD 源码、本地配置、原审计文件副本）。
   - 验收库备份 `personal/backups/core-acceptance/careerpilot-20261116-162634-845.dump`（1,403,046 字节，SHA256 `8BF9DB2D…26439`）恢复至全新 `careerpilot_core_acceptance_restore_test`。
   - **35 张 public 表全部行数与逐行内容摘要一致，答卷/证据/任务/复测外键链一致**；原备份恢复至 `careerpilot_pre_repair_restore_test` 同样一致。
   - `deploy/restore.ps1` 只向尚不存在、名称以 `_test` 结尾的测试库恢复，拒绝覆盖（`--single-transaction --exit-on-error`）；`deploy/backup.ps1` 校验退出码、归档目录与 SHA256。
6. **最终启动收尾**
   - 原库完成兼容迁移并启动于 **8000 / 5173**，personal 为 0 答卷、0 证据、0 任务、整体 `UNKNOWN`，原库全部既有行保留。
   - 重启后 profile、两份报告、任务 7、learning 五个接口内容逐项一致（考试/证据/任务/复测 ID 及结果）。
7. **交付物**：`CORE_LOOP_FIX_REPORT.md`、`CORE_LOOP_ACCEPTANCE.md`、`QUICK_START_PERSONAL.md`（分别标注实现 / 夹具验证 / 真实模型验证 / 未验证 / 阻塞）。

## 遗留风险与待办

1. **明确未证明**：评分者一致性、岗位标准校准、不同难度可比性、跨领域迁移、1/3/7 天保持效果及长期职业收益；题库不少 rubric 仍由种子标签生成，任务虽可追溯，粒度与教学质量仍受这些标准限制。
2. **长期保持未验证**：本轮没有等待真实 1/3/7 天，也没有用调整时间或直接标记完成冒充延迟效果；到期前只验证到 409。
3. **仍关闭的能力**：公共写入、自动策略激活、语音输入继续关闭；原始附件上传二进制文件的长期归档不是本轮能力（仅数据库中的原文本、答案、快照、任务产物与关联恢复比较）。
4. **未覆盖边界**：真实模型链路未注入网络保存失败；未用重复作答测量置信度变化；真实验收未修改来源题库；本轮没有真实发布或启用策略。
5. **题库 v2 后续阶段**：P2 四种考题模式（闪卡自评 / 追问挑战 / 弱项抽考 / MCQ 测验，抽考权重 未学:模糊:熟练:精通=10:5:2:1）+ `#/notes` + `#/top10`；P3 `add-question` skill（`.opencode/skill/add-question`）+ 160 题 MCQ 补写；P4 README 重写与 `.fragments` 清理；明确不做多用户/云同步/Pages/SM-2/Node 工具链。
6. **Multica 后续骨架**：T0 现状核对 → T1 当前/目标架构图 → T2 考核/练习/曝光/证据契约 → T3 题库质量与供给 → T4 每日 20 题编排与恢复 → T5 连续教学/实践/复测/来源追踪 → T6 第一条真实闭环（P0）；T7 记忆增强、T8 观测、T9 一条 Multica 异步回填集成（P1）；T10 策略候选比较、T11 简历/网站草稿（P2）。T1 不作为 T6 前置但应尽早交付；T3/T4 在 T2 后并行，T7/T8 可并行，T9 不阻塞最初学习。
7. **其他约束**：不要让 Multica 代写代码充当用户实践能力证据；候选记忆未确认不得暗中生效；外部文本回填只校验/排版/保存、不触发隐藏模型调用；未审核草稿不能参加正式考试；不批量生成上千道题；成本未知显示未知而非零；后续模型任务须写明调用量/并发/重试/预算/停止条件，不能沿用旧隔离任务的 1000 次调用许可。
