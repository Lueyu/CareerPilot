# 项目自动驾驶/智能/题库工作坊 AI 报告提炼

> 本文档提炼自 D:\agent-interview 仓库内 AI 生成的项目自动驾驶（Project Autopilot）、项目智能（Project Intelligence）、题库工作坊（Question Workshop）三类报告/计划/规格文档。
> 全部内容仅来自下列源文件，未补充外部知识；关键英文术语与文件名保留原文。

## 来源文件

- D:\agent-interview\PROJECT_AUTOPILOT_ACCEPTANCE.md —— 自动驾驶验收记录（T01–T26 对照表、证据类型分级、真实模型观察）
- D:\agent-interview\PROJECT_AUTOPILOT_CHANGELOG.md —— 增量改造清单、代码文件映射、旧行为迁移
- D:\agent-interview\PROJECT_AUTOPILOT_FINAL_REPORT.md —— 最终交付报告（PARTIAL/BLOCKED、A-01~A-04 修复、真实模型尝试）
- D:\agent-interview\PROJECT_AUTOPILOT_OPERATIONS.md —— 启动/状态机/预算/备份恢复操作手册
- D:\agent-interview\PROJECT_AUTOPILOT_TEST_REPORT.md —— 工作包 A 测试报告（57 passed、进程/Git 实测细节）
- D:\agent-interview\PROJECT_INTELLIGENCE_REPORT.md —— 项目智能交付报告（超时、源码边界、迁移）
- D:\agent-interview\QUESTION_WORKSHOP_REPORT.md —— 面经优化与补题工作台交付报告
- D:\agent-interview\QUICK_START_PROJECT_INTELLIGENCE.md —— 项目准备面试快速上手
- D:\agent-interview\QUICK_START_QUESTION_WORKSHOP.md —— 补题工作台快速上手
- D:\agent-interview\QUICK_START_PERSONAL.md —— 个人系统使用说明（学习闭环 8 步、备份恢复）
- D:\agent-interview\docs\project-autopilot\BUDGET_500_CONTINUATION.md —— 扩额续跑诊断时间线（09:52–14:37）
- D:\agent-interview\docs\project-autopilot\CONTINUATION_BACKUP.md —— 续跑前保护备份记录
- D:\agent-interview\docs\project-autopilot\engine-contract.md —— 引擎/Worker 契约（阶段链、错误分类、安全策略 v3）
- D:\agent-interview\docs\project-autopilot\final-ui-verification.md —— 前端最终界面复核与 4 项修复
- D:\agent-interview\docs\project-autopilot\planning-budget-observation.md —— 预算观察（完整报告调用下界 65/66）
- D:\agent-interview\docs\project-autopilot\SOURCE_SPLIT_RECOVERY.md —— 输出截断拆分恢复设计
- D:\agent-interview\docs\project-autopilot\timeout-completion-verifier.md —— 完成验收脚本与固定证据说明
- D:\agent-interview\docs\project-autopilot\TIMEOUT_REPAIR.md —— 超时排查与 10 项代码修复记录
- D:\agent-interview\docs\CareerPilot_Project_Autopilot_Implementation_Prompt.md —— 开发执行 Prompt（需求、架构、验收测试）
- D:\agent-interview\docs\superpowers\plans\2026-11-16-project-intelligence.md —— 项目智能实施计划（含验收状态更新）
- D:\agent-interview\docs\superpowers\plans\2026-11-16-question-workshop.md —— 题库工作坊实施计划
- D:\agent-interview\docs\superpowers\plans\2026-11-17-project-autopilot.md —— 自动驾驶实施计划（5 个任务、数据契约）
- D:\agent-interview\docs\superpowers\specs\2026-11-16-question-workshop-design.md —— 工作台设计规格（模块与固定接口）

## 背景与目标

- **项目背景**：CareerPilot 是本地单机个人求职备考系统，技术栈 FastAPI + SQLAlchemy/PostgreSQL + React/TypeScript + PowerShell 启动器；无云端 SaaS/ECS/RDS/K8s、无 Redis 或外部编排。
- **运行形态**：个人库 personal/runtime 网页 5173、API 8000、独立 Worker 进程（PID 52112）；日志 personal/runtime/{api,web}.{out,err}.log、worker.out.log、worker.err.log、processes.json。
- **三类改造的递进关系**：先是"项目智能"（项目理解→记忆→考核接通），再是"题库工作坊"（面经优化与补题），最后是"项目自动驾驶"（导入即自动分析、可恢复阶段、有来源完整报告、候选记忆审批、仓库变化增量更新）。
- **自动驾驶目标**（Implementation Prompt 终点）："导入即自动理解，结果有来源，中断能续跑，变化能更新，记忆由用户确认"；用户愿意等较长时间。
- **禁止越界**：不运行仓库脚本/不安装依赖/不构建；不自动换模型或降配置；分析/出题不产生个人能力 Evidence；公开发布/简历自动发布/策略自动激活继续关闭。
- **验收口径**："机制通过"（DB 机制/OS 进程/Git 实测/恢复实测/浏览器实测）≠"真实模型链路通过"；fixture 不能冒称真实模型验证；每项先写可复现测试，外部服务未配置时明确 NOT_CONFIGURED。
- **总体交付状态**：代码与恢复机制已实现并回归通过，但完整真实模型闭环（仓库→有效完整报告→候选确认→用户确认）截至各报告记录时未验收通过；真实模型多次超时/BLOCKED，任务曾置 PAUSED_BUDGET。

## 核心发现与结论

1. **超时根因（TIMEOUT_REPAIR.md）**：对原超时 step134 完整原始请求做直连 vs 代理对照——
   请求体 SHA256 61c988c5...、25,090 字符/32,280 UTF-8 bytes、temperature 0.2、json_object；
   直连 41.579s HTTP 200（usage 9,464+6,347）；代理路径 180.016s 超时无响应头，TCP/CONNECT/TLS/POST 发送 1.438s 即完成。
   根因=本机 Veee HTTP(S) 代理，API/Worker 继承该代理而网关此前未配 NO_PROXY；
   修复=仅在本隔离任务新 Worker 子进程为原网关追加精确 scheme+host 的 NO_PROXY。
   但直连也出现过一次超时（step178 首次 180.015s，自动重试第二次 133.640s 成功，历史保留 [REQUEST_TIMEOUT, SUCCEEDED] retry_count=1），故不能全归因代理。

2. **超时诊断修正**：`awaiting_response_headers` 是过早粗粒度标签，已修正为 transport_phase 阶段记录（HTTPcore 白名单事件+相对时间区分 CONNECT 与目标 POST）。
   DeepSeek 文档空行/SSE 注释维持连接、火山网关可配置响应超时仅供解释，不确证根因。

3. **流式（SSE）修复**：可选择有界流式接收 transport_mode=sse-v1——仅该模式下用 SSE，需完整事件+非空可见内容+stop+[DONE] 再过原校验。
   流式诊断重建请求 step191（仅加 stream=true，非精确历史对照）：HTTP 200、4.703s 响应头、107.296s 首可见 content（此前 9470 个 SSE 事件）、126.593s stop+DONE、usage 7,719+12,716=20,435 tokens；事件数≠token 数，不访问隐藏推理正文。

4. **执行总界与空闲超时分离**：14:07 起仅本 run 启用 stage_timeout_seconds=300（代码硬上限 600），I/O 空闲 180s 不变；
   固定凭证 sse-active-deadline-300-step207-attempt4-v1 只允许 step207 attempt4，实际 204.219s 成功（1.464s 响应头、180.380s 首可见 content、204.196s stop+DONE、usage 8,955+20,109=29,064 tokens）。

5. **输出截断（SOURCE_SPLIT_RECOVERY.md）**：step214 两次 finish_reason=length（170.688/140.202s；usage 25,469/22,121 tokens）停 BLOCKED——是实际输出截断而非 300s 总界超时。
   方案 source-split-v1：显式运行记录绑定原父输入哈希/目标提交/完整源码范围/模型版本/失败历史；
   原完整源码块分 2–4 个非空组（不截行/不遗漏/不重叠/不自动递归拆分），新子阶段新 key 正常调模型，合并阶段只在全部子阶段成功后确定性运行；
   原父阶段标记 SUPERSEDED（输入/输出/attempts/重试数不变，普通暂停恢复取消不能重新入队）；
   范围 src/App.tsx L1–61、src/SiteRoutes.tsx L1–104、src/index.css L1–373（前两块一组、CSS 一组）。14:49 已允许对 step214 执行精确两组恢复（非已成功证明）。

6. **预算缺口（planning-budget-observation.md）**：96 文件全读、合计 680,632 字节、脱敏规范化后源码 614,028 字符、21 模块；
   按每批 20,000 字符分组首轮 analyze_module 需 53 次调用；
   完整流程调用下界=模块规划 2 + 首轮分析 53 + 跨模块 1 + 报告章节解释 9 + 候选提取 1 = 最低 65、含候选 66（不含重试/补读）；
   配置最多 32 次、缺口至少 34 次，曾提请提高到 96 未获答复。
   用户最终只批准本隔离任务提高到 1000 次（max_model_calls 32→500→1000，源码上限仍 8 MiB、单请求 180s）；
   12:24 快照 78/500 次、已知工作严格下界 314 次、按平均输出推算约 534–556 次（风险估算），不承诺 500 足够。

7. **引擎/Worker 契约（engine-contract.md）**：引擎一次执行一个对 DB 无副作用的阶段；Worker 拥有租约/重试/预算/取消/持久化/发布；阶段不写 ACTIVE 记忆或个人 Evidence；源码/模型 HTTP 调用只在 execute_stage。
   RuntimeContext(workspace_id, project_id, llm_config, credentials, request_timeout=180, input_chars=40000)；凭据 key=github_token/gitcode_token，无全局凭据查找，凭据与模型配置绝不进入 artifacts。
   阶段链：resolve → manifest_page(续) → manifest → analyze_change_impact → plan_modules → read_source → analyze_module(立即校验) → module(确定性聚合) → cross_module → explain_section → report(确定性组装) → extract_candidates(candidate-part:N) → candidates(确定性聚合)。
   MODEL_STAGES=plan_modules, analyze_module, analyze_change_impact, cross_module, explain_section, extract_candidates；report 需 COMPLETE（manifest 全记账+所有计划模块+有效引用+必需章节）才生成候选，否则显式 PARTIAL；candidates 全部保持 PENDING_REVIEW。

8. **错误分类与修复策略**：StageError 14 类——NOT_CONFIGURED/AUTH_BLOCKED/RATE_LIMITED/NETWORK_TRANSIENT/PROVIDER_5XX/PROVIDER_REJECTED/REQUEST_TIMEOUT/INPUT_TOO_LARGE/OUTPUT_TRUNCATED/INVALID_SCHEMA/INVALID_CITATION/SOURCE_UNAVAILABLE/MANIFEST_INCOMPLETE/BUDGET_EXHAUSTED；
   引擎不重试，Worker 记录 attempts 并应用策略（临时故障 initial+2 次重试、429 尊重 Retry-After+抖动、401/403 明确 BLOCKED）；request_started=False 区分预算预检拒绝与已发出调用；
   允许一次 schema/citation/截断修复（inputs.repair_diagnostic），不改用户模型参数；
   网络失败与内容失败额度分开：总失败上限 3、内容失败上限 2（step202 例：180s 无答案→167.235s length→第三次 33.906s 成功，usage 7,992+4,824=12,816）。

9. **安全策略 v3（NUL 处理）**：严格 UTF-8 解码、不用 replacement 解码；允许文本路径最多 8 个 NUL 且密度 ≤0.1% 时以可见字符串 `\u0000` 渲染（先脱敏后），记录原始行/列/码点/替换映射，行号不变、可逆；高密度 NUL/其他二进制控制字符/非 UTF-8 仍阻塞。
   fc/index.js 真实文件已验证稀疏 UTF-8 NUL 可见规范化（单独隔离读取，不构成完整真模型报告）；policy-v2 检查点不可变，不兼容 run 保留而非改标 v3。

10. **引用/结论校验修复（TIMEOUT_REPAIR 10 项中的关键）**：
    ① 相邻片段引用误拒→接受同路径/同版本/同 source_id 连续已读区间并集（L183–187 例）；
    ② 默认三轮补读硬停→由本模块尚未读取合法清单路径数决定有限上界，每轮必须引入新路径；
    ③ 文档问题升级成代码事实→issue→claim 按 source_id/版本/路径/实际 category 约束，全 documentation 引用才派生 DOC_CLAIM；
    ④ 已知模型误读传播→fc/index.js 四条结论中一条把留言记录 IP 哈希错关联配额计数（L953–955/1041–1042/1075–1077/1121–1122 用原始 IP、L1145 哈希），独立 Coding Agent 复核 + claim_reviews 单条审计记录并隔离该结论；
    ⑤ 报告事实标签不一致→按精确 claim_id 关联的 kind 显示，缺失显"来源类型未确认"；
    ⑥ 网络重试丢失内容修复要求→从最近一次成功后的尝试历史保留所有未解决修复类别（当前类别优先、去重、最多三条），19 项测试通过。

11. **证据独立复核发现（BUDGET_500_CONTINUATION.md）**：独立源码复核发现 claim-agent-wire 把 trimWire"保留窗口内第一条 user"误写"最近一条 user"（src/lib/agent.ts L64–68），06:37:10 UTC 追加第二条 QUARANTINE 审阅（claim-review-agent-wire/sealed-safe.json SHA256 adb896db...），不改写原产物。

12. **增量分析设计（Implementation Prompt 第 7 节）**：默认每 6 小时检查一次；无新提交且配置未变、无待恢复任务时不调用模型；优先条件请求/轻量版本检查（第一版选节制轮询而非 webhook）；compare 有分页边界，不能漏掉第 301 个文件仍声称完整；缓存指纹至少含 workspace/project/模块来源 hash/依赖指纹/分析/Prompt/Schema 版本/模型配置指纹/安全读取策略；强制推送、历史不连续、基线丢失、分支切换、跨模块关系无法确定时自动退回"重新完整分析"并保存原因，不让用户研究 Git 细节。

13. **记忆更新规则（Implementation Prompt 第 7.3 节）**：新报告成功可为"最新项目报告"，但记忆更新仍生成候选，未经确认不替换当前确认记忆；支持证据改变→标记 freshness=NEEDS_REVIEW 不静默改结论；用户编辑过→保留用户版本、显示新证据差异；已忽略且来源内容未变→不反复弹同一候选，新证据实质变化后才允许再次提出并解释；同事实 PENDING 候选在新报告后可标记被新候选取代；已生成试卷/学习任务/历史评分保留当时快照，不因项目更新改写历史。

14. **项目智能交付状态（PROJECT_INTELLIGENCE_REPORT.md）**：功能代码与回归通过，真实模型闭环未通过（11 次隔离分析尝试全部 FAILED，docs/project-intelligence/acceptance/model-attempts.json）；迁移 004_project_intelligence.sql 仅新增分析记录与考试来源字段。
    源码边界：每次最多首读 12 补读 8 个文件、每文件最多 16,000 字符、总计最多 180,000 字符、目录最多 4,000 候选文件；超 60,000 字符按最多 30,000 分组最多 2 组并发；综合阶段输入 ≤60,000 字符、最终综合总时限 300 秒、其他模型阶段 180 秒。
    曾把 20 文件约 15 万字符一次送最终阶段超 21 万字符多次超时→加大输入分组；聚焦 4 文件仍单模块 180s 超时、综合 300s 超时，不能全归因输入长度；对三个精确 DeepSeek 模型名请求 reasoning_effort=low（元数据只记录 requested，provider 行为不假定跟随）；一次新调用约 36 秒返回但结构不完整被校验拒绝。

15. **题库工作坊交付（QUESTION_WORKSHOP_REPORT.md + 设计规格）**：入口 http://127.0.0.1:5173/bank/workshop。
    已实现：GitHub 项目补充个人经历+手工项目（kind='manual'，不可触发仓库扫描）；通用题直接回答/项目题结合资料改写并引用事实（至少一条个人陈述）、资料不足存待补草稿禁止审核；按题库覆盖/缺口/失分任务提批次；两种渠道（API 生成/外部 Prompt 回填）统一保存原始输出/参考答案/追问/评分要点/来源；审核后生成当前空间私有题（标题重复合并、canonical 关联）；"开始练这题"真实作答；不写能力证据。
    输出协议 question_mode='general'|'project'|'needs_context'；快照 snapshot 冻结资料并分别标记用户陈述与仓库观察（扫描指纹不冒充 Git commit SHA）。
    真实评分：考试 #24 提交实际文本答案、AI 评分 1 题、规则评分 0 题、20/100、关联失分学习任务；生成/审核后作答前证据数 0，提交评分后 1。

16. **前端 UI 4 项修复（final-ui-verification.md）**：
    ① 报告先发布候选后生成但旧刷新只看报告 ID→候选空列表，修复为 ProjectAnalysisPanel 以任务 ID+状态+显式刷新轮次为候选刷新条件（串行 5 秒轮询最多 120 次，非活跃立即停）；
    ② 候选失败挡住完整报告历史入口→改以 report_status=VALID 且 coverage_status=COMPLETE 判断历史可读，PARTIAL 不作为完整历史开放；
    ③ 完整报告与候选失败缺少区分→显示"报告已完成，候选记忆尚未完成"+保留任务错误与恢复操作；
    ④ 无限并发轮询→串行 5 秒间隔最多 120 次。4 项回归先失败后通过，45 passed/0 failed/0 skipped。

17. **规划输入瘦身（FINAL_REPORT A-01）**：规划输入含无需推理元数据导致大分片超时（原 14,444 字符多次超时）→ project_autopilot_engine.py 规划输入只投影 path/category/size，Prompt bundle v2，58 passed，真实大分片 6,184 字符/33.829s 首次成功。
    **A-02**：报告先保存后提候选、组件只按 report.id 刷新→ ProjectAnalysisPanel.tsx 结合 run 状态/刷新轮次，45 passed。
    **A-03**：历史入口以整个 run 成功为门槛→按 report 真实完整有效状态开放，PARTIAL 不冒充完整。

18. **候选确认/忽略语义修复（FINAL_REPORT A-04）**：忽略候选也要求旧 base 记忆 ACTIVE 会误阻→ project_candidates.py 仅确认要求 base 版本一致，忽略仍查候选自身 revision/status/工作空间；
    测试 test_obsolete_base_blocks_confirmation_but_not_ignoring_candidate：旧观察 SUPERSEDED、新确认 CONFLICT、忽略 IGNORED；确认时检查 report version 和 memory revision，冲突要求用户重新查看。

19. **运行状态机（OPERATIONS.md）**：QUEUED/RUNNING/RETRY_WAIT/PAUSED_USER/PAUSED_BUDGET（需在设置提高预算再恢复）/BLOCKED/CONFIG_CHANGED（管线/提示词/报告格式/安全规则与旧检查点不兼容）/FAILED_FINAL/CANCELLED/COMPLETED（PARTIAL 不是完整有效报告）。
    默认值：定期检查 6 小时、新任务单次模型阶段时限 600 秒（PROJECT_SCAN_STAGE_TIMEOUT_SECONDS 可调 180–600）、180 秒网络闲置、阶段每 10 秒续租租约 180 秒、评分阶段默认 45 秒（-LeaseSeconds 可覆盖）、全局并发默认 1。

20. **持久化与领取设计（2026-11-17 计划）**：project_analyses 作 runs+不可变报告容器（mode/base/target SHA/generation/pipeline/prompt/model 指纹/预算/usage/覆盖/report 状态）；analysis_steps 唯一 (run_id,step_key)、input hash、lease owner/expiry、单调递增 fencing token；MemoryItem 加 revision/freshness；
    Worker claim SQL：SELECT id FROM analysis_steps WHERE status IN ('QUEUED','RETRY_WAIT','RUNNING') AND (next_retry_at IS NULL OR next_retry_at<=now()) AND (lease_expires_at IS NULL OR lease_expires_at<now()) FOR UPDATE SKIP LOCKED；
    API 重启不恢复或取消 Worker 拥有的 run；正在运行时的新提交合并为后续一个最新待处理目标（coalesce），不追逐每个提交堆满队列。

21. **旧行为迁移（CHANGELOG.md）**：旧报告保留 legacy；旧自动激活无 confirmed_at 记忆迁移为待审核；NUL 字符可逆可见转义为安全策略 v3（v2 验收任务不能冒充兼容续跑）；未改 .env；项目移除改归档；旧系统记忆迁移保留历史来源，不把未经用户确认的 legacy_auto 项伪标成人工确认。

22. **题库工作台限制（QUESTION_WORKSHOP_REPORT.md）**：结构校验/引用/人工审核不能证明答案正确；自动去重不保证识别语义近似改写；补题 Prompt 最多 200 道参考题；同库仅单 API 实例；未对 20 题最大批次压力测试（建议首次 1–3 题）；前端生产构建约 2.4 MB 主包体积警告；历史文件缺原文需补粘；大模型文本仍需用户审核。

## 已落地与已验证

- **自动化回归**：后端逐步 467→489→508→574→579→596→638 passed（stage-deadline-final-backend.junit.xml），最终完整后端 395/395、前端 114/114、typecheck/build 通过（05:16 补记）；工作包 A 57 passed/67.04s（pipeline project-autopilot-v1、prompt bundle v2、schema 2、安全规则 project-source-policy-v3，隔离库 careerpilot_suite_20261117_042037_5bf160_test）；拆分/队列/预算/恢复/兼容/API 联检 100 passed/71.69s；SSE 专项 50/49/53 passed；前端 UI 回归 45 passed、全量 152 passed、typecheck/build 通过；Windows 启动 9 passed、Worker/API 故障 3 passed、真实 Git 变更 2 passed（7 次真实提交读真实 SHA/tree/blob，不连真实 GitHub/GitCode）；项目智能后端 201 passed/33.66s、前端 66 passed；题库后端 144 passed、前端 43 passed。

- **进程与恢复实测**：进程测试每例独立库+真实 Worker+uvicorn（模型阶段为夹具）；真实 Worker 强杀恢复（step147 attempt1 活跃时终止→attempt2/fencing 13 在 29.516s 成功，146 成功产物不变，旧 attempt 记 OUTCOME_UNKNOWN）；A API 重启前后同 input_hash/attempt/fencing/lease、135 成功产物一致（evidence personal/runtime/a-project-report-preview/api-restart-verification-20261117-104943-safe.json）；浏览器关闭期间 step134 继续完成。

- **恢复验证（T25）**：39 表/36 sequence/72 FK 逐表一致，孤儿租约恢复，resolve/fast attempts 仍 1、slow 变 2；CONTINUATION_BACKUP 恢复目标 careerpilot_autopilot_restore_20261117102335_aced_test：46 表全行哈希/43 序列/94 FK 无孤儿，134 个 SUCCEEDED 检查点输入/输出/全行哈希保留，AppSetting 全量相同；Autopilot 阶段输出在 project_analysis_steps 表，无文件 outbox；项目智能夹具链备份恢复 37 表/35 sequence/69 FK、0 模型调用。

- **真实模型部分成功**：run1 首轮 62 成功阶段后因 fc/index.js 稀疏 NUL 阻塞（3 次调用 1 超时，约 363 秒）；v3 full 36 成功阶段终态 BLOCKED；恢复续跑后 214 成功检查点/81 批/123 调用，step191/attempt4 33.313s（usage 11,891）、step192/attempt3 24.186s（usage 11,749）、step207 attempt4 204.219s 成功；规划输入瘦身后真实大分片 6,184 字符/33.829s 首次成功。

- **题库工作坊真实验证**：真实模型 deepseek-v4-flash 两次 API 生成成功（2 道+1 道草稿）、外部回填 1 道完整 JSON（先坏格式失败再修正成功）、三次生成调用 20,975 tokens（9,944+4,197+6,834）；真实案例 Q-G1-104（模型输出 needs_context 被禁批准，编辑后批准）、Q-G2-106（外部回填）、Q-G3-105（引用 7 条个人陈述）；考试 #24 得 1/5 分、1 表现证据、3 学习任务、RAG 保持 UNKNOWN。

- **备份链完整**：project-autopilot-before/20261117-025942-c559b758（source-worktree.zip 441 文件 4,380,934 字节、private 298 文件、DB dump 1,570,349 字节 SHA256 cdf56f8e...，37 表/35 sequence/69 FK 一致）；project-and-assessment-before/20261117-041657-67db3046（源码 483 文件、private 705 文件、DB dump SHA256 da7df636...，考试 #25 快照一致）；autopilot-continuation-before/20261117-102335-e235fe90（source-worktree.zip 724 文件 SHA256 e89209ff...、private 1,155 文件、DB dump 642,024 字节 SHA256 cbd6a9da...）；resume-500-20261117-092200/（before.dump SHA256 0850b469...、source-split-v1-step214/before.dump 996,005 字节 SHA256 f1c2cec4...）；question-workshop 三份备份及恢复库校验。

- **验收证据固定**：budget-1000-before-stream/authorization-safe.json（SHA256 fc54911b...，04:40:55 UTC 授权 500→1000，授权时调用 86、成功阶段 190）、sse-v1 启用 enabled-safe.json（SHA256 c7611f9e...，05:05:19 UTC，启用前 190 成功步骤哈希、预算 1000/模型/配置/180s/40000 字符不变）、stage-window-300-one-shot sealed-safe.json（SHA256 b0541d54...，06:07:25 UTC）与 result-safe.json（SHA256 1102fa72...）、claim-review-agent-wire/sealed-safe.json（SHA256 adb896db...，双条 ledger e198c563.../46bbc539...）；
  最终输出 PROJECT_REPORT.md 由数据库原文生成（九章节/原 claims/GitHub 固定提交行号链接/待确认候选，不调模型补写），写入 personal/backups/autopilot-timeout-completion/<timestamp>/。

## 遗留风险与待办

- **真实模型完整闭环未通过**：完整真实模型报告→候选确认→用户确认、真实模型 GitHub 增量分析、真实 GitCode 整次验收均未完成；当前网关仍阻塞时只能交付可恢复任务和准确原因，不能把"稍后重试"假装完整真实验收；小样本只证明已跑通，不声称可靠率。
- **超时问题未根绝**：直连也偶发超时（step178/step207 首次）；无法区分代理隧道后段/网关等待/上游等待；SSE 需完整事件才通过校验；单请求 180s 限制与大块输出（step214 length 截断）仍可能触发 BLOCKED；SOURCE_SPLIT 分离上下文可能降低跨文件观察能力，不保证不再截断。
- **预算与费用**：剩余至少 63 次调用（52 批+跨模块+9 章节+候选提取），不承诺 500 足够；外部模型请求不保证 exactly-once，断线可能已被计费，需记录未知结果和 request_id；无法准确分词时用保守估计并记录，不能把字符数当 Token 数；价格配置未知时显示调用量或 Token，不伪造精确费用。
- **已知改进项（TIMEOUT_REPAIR 末尾）**：补读计划丢失原补读理由及待核实结论可能重复泛化分析；报告每章遍历所有结论批次可能冗长；跨模块检查以 claim 批次为单位不证明所有跨批模块关系正确。
- **未验证边界（ACCEPTANCE/TEST_REPORT 明确列出）**：GitCode live、force-push、供应商全类别故障、多主机压力、长时稳定性、prompt v2 新 dump/restore、多仓库并发、20 题最大批次补题压力；考试 #25 根因属工作包 B 未查；历史证据不改标（309 passed 是 prompt v2 之前、restore-verification.json 是 prompt v1）。
- **能力与校准**：岗位标准/评分项来自种子数据未校准；记忆判断准确性/能力标尺校准/长期学习收益未验证；无真实时间延迟复测不能声称长期记忆保持；同题重练不算新独立覆盖；数字能力矩阵已移除改覆盖信息，文件数不算能力分。
- **运行与维护注意**：同库只运行一个 API 实例；无 Windows 自动启动服务（关电脑/睡眠后需重启本地服务自动恢复）；关闭浏览器不停止任务；备份/恢复以 [F6 加密备份与隔离恢复手册](../../../deploy/BACKUP_RECOVERY.md) 为准，需外置age公钥/identity与G5主密钥；恢复只向新演练库或计划确认的新备用库，保持权限和付费隔离；测试隔离 server/run_isolated_tests.py tests -q（每次新建 careerpilot_suite_*_test）。
- **使用边界（QUICK_START 系列）**：仓库扫描不能证明工作由你完成；项目记忆由使用者确认，只有实际作答及有效评分进入学习证据；未确认个人贡献始终不写成"你实现了"；"开始练这题"提交自己新答案才发生评分；学习闭环 8 步（确认空间模型→诊断作答→阅读报告→打开失分任务→记录学习完成（1/3/7 天复测，不加分）→即时新题验证→核对新结果→到期延迟复测）；已关闭公开发布/远程写入/PR/合并/远程回滚，Evolution 禁止激活策略。
