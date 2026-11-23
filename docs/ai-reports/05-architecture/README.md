# 架构/后端/数据库/安全/部署 AI 报告提炼

> 对象：CareerPilot（agent-interview）本地优先的面试/学习工作台。本文件汇总仓库根目录 15 份 AI 生成报告的审计与实施结论。时间线：2026-11-16 基线 `0c65882`（master）审计 → v3.2「Backend Completion」实施完成。

## 来源文件

- API_GAP_REPORT.md、API_IMPLEMENTATION_MATRIX.md、BACKEND_AUDIT.md、COMPONENT_MAPPING.md
- DATABASE_GAP_REPORT.md、DATABASE_SCHEMA_REPORT.md、E2E_TEST_REPORT.md、IMPLEMENTATION_PLAN.md
- IMPLEMENTATION_REPORT.md、MOCK_REMOVAL_PLAN.md、MOCK_REMOVAL_REPORT.md、OBSERVABILITY_REPORT.md
- PUBLIC_CONTENT_BACKEND_PLAN.md、PUBLIC_SITE_DESIGN.md、SECURITY_REPORT.md

## 背景与目标

1. **前端栈**：Vite 5 + React 18.3 + react-router-dom 6 + TypeScript 5.6（证据 web/package.json）。
2. **后端栈**：FastAPI + SQLAlchemy 2 async + asyncpg + pydantic-settings；数据库 PostgreSQL（`postgresql+asyncpg://localhost:5432/careerpilot`，见 app/config.py）。
3. **集成栈**：LLM 走 OpenAI 兼容 httpx 直调（deepseek/glm，未配置时规则引擎降级）；OCR 用本地 `rapidocr-onnxruntime` + Pillow（无云依赖）；GitHub 用 httpx 直调 REST（无 PyGithub，支持 dry_run）。
4. **公开站**：`portal/build.mjs` 纯静态构建 → OSS（agent-interview.icu），与本地应用职责分离。
5. **任务书目标**：「Backend Completion & Real Business Logic Implementation」——六大引擎（Competency / Assessment / Interview Intelligence / Memory / Learning / Project Intelligence & Career Assets）+ 平台运维（Observatory / Evolution / JD 导入 / 设置 / 本地部署）。
6. **执行纪律**：不推翻前端设计、不 Mock、确定性引擎用代码、LLM 只做语义任务、每模块完成即测。
7. **审计基线（2026-11-16，commit `0c65882`）**：server/app 41 个 py 文件 5905 行、web/src 21 个页面；部署为本地 uvicorn（系统 Python 3.12）+ `npm run dev`，无 Docker、无 .env.example、无迁移脚本、无备份脚本。状态口径：`NOT_IMPLEMENTED` / `MOCK` / `PARTIAL` / `IMPLEMENTED` / `VERIFIED`。

## 核心发现与结论

### 路由与接口面

1. **路由面规模（按行数/端点数）**：bank.py 839/9（最重，面经导入审核）、public_content.py 688/15、exams.py 370/6、settings.py 296/4、memory.py 226/5、observatory.py 197/5、dashboard.py 147/1、learning.py 134/4、profile.py 106/2、benchmark.py 61/1。
   - 全部挂在 `/api/v1`，鉴权 `Authorization: Bearer <API_TOKEN>`（或 `X-Api-Token`），工作空间 `X-Workspace: <slug>`（默认 `demo`）。
   - 所有业务路由经 `get_workspace` 依赖强制 workspace 过滤。
2. **API 采用「兼容优先」映射**：任务书建议路径尽量落成现有契约，避免破坏前端。
   - `/dashboard/summary·tasks·recent-activity` → `GET /dashboard`（单端点聚合 stats/today_tasks/pending_imports/recent_activity）。
   - `/assessments*` → `/exams*`；`/interviews*` → `/bank*`；`/competency*` → `/profile` + `/benchmark`；`/publications*` → `/public-content/*`；`/agents*` → `/observatory/agents`。
3. **审计时完全不存在的路由面**：projects（0）、career-assets（0）、evolution（0）、jobs/JD 导入（0）、learning content 生成（0）。

### 接口与前端缺口

4. **审计真实缺口共 4 大块**：Projects + CareerAssets（前后端全无）、Evolution（全无）、JD 导入（无）、Learning 内容生成（无）。
   - 小缺口 5 项：PATCH 题目、reject 项、abort 考试、AgentRun 观测、手动复测。
5. **前端按钮级假动作（必须消灭或接真）**：
   - Assets.tsx:17/51「导入仓库/重新分析」toast「Demo 未接入 GitHub」；Assets.tsx:63-72 流水线 8 步静态渲染。
   - ProjectReview.tsx / Pipeline.tsx / Evolution.tsx 整页 demo（PROJECT_FINDINGS、DRAFT_CONTENT、PIPELINE_STEPS、PRS、EVOS 全假）。
   - Overlays 快捷操作部分假；Bank.tsx「编辑/合并重复」假；Diagnosis.tsx 降级本地 demo 试卷（有明确 banner）；LearningTask.tsx:275「正式版由大模型实时生成」声明与实现不符。
6. **考试状态机审计缺口**：真实链为 CREATED→ACTIVE→SUBMITTED→TRANSCRIPT_CONFIRMED→FROZEN→SCORING→COMPLETED，提交后写证据→重算能力→生成学习任务且全程 trace。
   - 缺 `ABORTED` / `EXPIRED` 异常态（无 abort 接口、无超时判定）、缺 `SCORING_FAILED` 兜底。
   - 开放题仅关键词覆盖度规则评分（非假分），无 LLM Judge、无 judge_version 落库。
7. **能力画像缺历史**：competency.py 真实实现 `estimated_level=clamp(基线2.5+Σ证据支撑)` + confidence + evidence_coverage + 5 态判定（UNKNOWN≠0），但无 `competency_snapshots` 历史表，画像演进不可追溯；证据只来自 EXAM_ANSWER/导入。
8. **学习中心缺内容生成**：planner.py 真实做「差距加权选题域→任务模板→1/3/7 天复测链（幂等）」，但任务内容为静态 `TASK_TEMPLATES`，无 7 步通俗教学、无「再简单一点/换个类比/结合我的项目」交互。
9. **Memory 缺状态与检索**：真实为 CANDIDATE→ACTIVE 确认、纠正=新条目+旧条目 SUPERSEDED+superseded_by、删除=DISMISSED（软删）、对话导入（规则+LLM 双路径、去重）；缺 EXPIRED/RETRACTED 态，无独立检索/过滤接口。
10. **面经导入是真实流水线**：上传→解析（PDF/DOCX/TXT/MD/JPG/PNG OCR）→提取（LLM/规则）→技能映射→去重→生成草稿（电梯稿/类比/追问带答案）→人工审核→入题库 `USER_PRIVATE`。
    - 后台任务 + `meta.progress` 真实进度落库 + 幂等防重；审核后部分通过不误关会话；原始文件存 ImportSession.files。
    - 缺 PATCH 题目编辑与手动合并接口（去重自动做）。
11. **公开内容发布已达任务书第八阶段要求**：5 表 + 完整状态机 + 四类校验（privacy/fact/license/markdown lint）+ GitHub REST（建分支/写文件/PR/查 Actions 构建/合并）+ dry_run + 幂等键 + 审计 + 回滚。
    - 唯一缺图片/附件上传（记录待补，现有 JSONB meta 可承载 URL）。
12. **可观测性缺 AgentRun 维度**：Trace/Span/DecisionRecord/Event 全链路落库、trace+cost 记录、workspace 隔离；但 LLM 调用未单独成 span 记录 provider/model/token_usage/重试次数。
13. **部署审计缺口**：无 docker-compose/Dockerfile、无 .env.example、无 alembic、无「可重复执行」迁移脚本、无 pg_dump 备份恢复。
    - 后台任务（导入流水线）为 asyncio，无重启恢复——未完成任务停在 UPLOADED（可接受但需记录）。

### 数据库

14. **DB 缺口**：审计时约 15 张表（按实体合并计 20+）；必建 **9 张新表**：
    - `jd_sources`（JD 导入）、`projects`、`project_scans`、`project_findings`（GitHub 项目/扫描/发现=证据）、`career_assets`、`asset_update_runs`（资产与更新流水线）。
    - `competency_snapshots`（画像历史）、`evolution_candidates`、`evolution_runs`（策略候选与评估）。
15. **列扩展与索引**：`workspaces.preferences JSONB`、`trace_spans.detail` 增 agent 字段（provider/model/duration_ms/tokens_in/tokens_out/retry_count）、`exam_answers.judge_version`；索引补齐 8 处（exams/evidence/learning_tasks/memory_items/questions/import_items/traces/skill_states）。
16. **迁移策略（明确不引入 alembic）**：开发态沿用 `Base.metadata.create_all`（新表重启即建）；新增 `server/migrations/*.sql` 幂等 SQL（IF NOT EXISTS / DO 块）+ `server/migrate.py` 顺序执行器（可重复执行、零副作用）。
    - 备份/恢复：`deploy/backup.ps1`（pg_dump 自定义格式）+ `restore.ps1`，写入 LOCAL_DEPLOYMENT_GUIDE。
    - 不做 SQLite 迁移、不做 alembic 全量引入（单机本地版成本>收益）。
17. **Schema v3.2 最终态**：22 张表（PostgreSQL 16），所有业务表 `workspace_id` FK → workspaces（ondelete=CASCADE）+ 索引。
    - 无 workspace_id 的子表靠父表级联：exam_questions/exam_answers（经 exam）、import_items（经 session）、trace_spans（经 trace）。
    - 枚举用 String 列 + 应用层校验；关键唯一约束：`content_publication_runs.idempotency_key`、`traces.short_code`、`job_standards(role,level,version)`、`skill_states(skill_id)` per workspace。

### 安全

18. **已实现控制（local-first 威胁模型）**：API Token（`Bearer` 或 `X-Api-Token`，可配 `API_TOKEN`，默认 `cp-dev-token` 仅限本机开发），`/healthz` 外全部业务路由需鉴权。
    - 跨 workspace 资源访问 404/403（私有题库 `_own_question` 归属校验）；危险删除双确认（workspace 删除需输入 slug，不匹配 422）。
    - 密钥存本地 DB（app_settings）、API 只回显掩码 `****尾4`，`.env` 不入库（.gitignore），对话/日志不回显。
19. **安全边界（诚实声明）**：单用户本地部署无完整用户体系，API Token 是共享静态令牌，不能防御本机其他使用者；CORS 允许 5173（前端 dev 端口）；`DEFAULT_WORKSPACE_SLUG` 未做多用户区分（多工作空间=多套数据，非多用户权限）。
    - GitHub token 权限取决于用户授予范围（建议 fine-grained 限定内容仓库）。
    - 隐私扫描是正则/关键词匹配，对语义级隐私无识别能力；事实核查只做「量化声明必须有引用」结构规则，不判断真伪。

### 公开内容与公开站

20. **公开内容后端设计（PUBLIC_CONTENT_BACKEND_PLAN）**：5 表（public_content_sources / drafts / content_publication_runs / content_publication_rules / content_release_audit）。
    - 状态机 `DRAFT→VALIDATING→NEEDS_REVIEW→APPROVED→PR_CREATED→BUILDING→PUBLISHED`，失败分支 `VALIDATION_FAILED`/`REJECTED`/`BUILD_FAILED`，`PUBLISHED→ROLLED_BACK`。
    - 校验四件套：隐私 PII/私有实体、事实量化声明、许可 CLEARED、Markdown lint；`GitHubPublisher` 全部 REST + dry-run（零真实写入，返回 planned_operations）。
    - 13 个 `/public-content/*` 端点；红线：公开唯一入口是 `PublicContentDraft.markdown_body`，未 privacy=CLEAN 且 approval=APPROVED 不可 publish。
21. **公开站设计（PUBLIC_SITE_DESIGN）**：agent-interview.icu 从私人工作台转为公开知识门户，与 CareerPilot 视觉同源但职责分离——无登录、无用户库、不读私有数据、不存 GitHub Token。
    - 导航 `/paths` `/guides` `/questions` `/labs` `/cases` `/about`；统一 frontmatter（title/slug/category/tags/level/status/source_type 等）。
    - `portal/build.mjs` 零 npm 依赖渲染静态 HTML + sitemap + robots + `search.json`；文章固定 8 段结构；首期 1 条学习路线 + 6-8 篇知识库 + 约 20 题公开子集 + 2 个 Lab。
22. **导航 v3.1 收敛**：侧栏 10 项 → 5 项 + 高级区；`/benchmark`+`/profile` 合并为「能力与岗位」双 Tab，`/exam`+`/bank` 合并为「考试与面经」双 Tab，组件不删不重写只改入口层级。
    - demo 数据边界=`data/demo.ts` + `data/bank.json`（161 题静态导入），全部展示带 DEMO 徽章；`portal/` 为独立子项目。
23. **量化与提交链**：后端 41→52 个 py 文件、5905→约 7500 行；API 端点 46→62 个；新增服务 7 个（benchmark_jd / llm_judge / followup / teaching / github_repo / project_analyzer / asset_pipeline）+ evolution。
    - 提交链：`7653baf`（审计五文档）→ `e91a747`（M0 数据层 9 表+迁移器）→ `0276361`+`0a83624`（Project）→ `4a37443`+`16ee7e3`（Evolution）→ `54abffb`（Assessment）→ `22c9c27`（Learning）→ `db182f4`（小补）→ 部署+文档提交。

## 已落地与已验证

### 数据与项目/资产

1. **M0 数据层**：9 张新表 + 3 列扩展 + 幂等迁移器 `migrate.py` + `001_backend_completion.sql`，`create_all` 新库建全、旧库补列。
2. **M-A Project Intelligence + Career Assets**：`github_repo.py` 真实 GitHub REST 只读分析（tree/README/languages/commits/关键文件探测）+ `project_analyzer.py` 文件模式→能力域确定性映射（规则引擎，非大 prompt）+ `asset_pipeline.py`（diff→风险分级→PublicContentDraft，复用 public_content 管线）。
   - 实测一次扫描 294 文件/111 commits → 104 项 findings + 能力矩阵（VERIFIED/HIGH/UNVERIFIED_CLAIM）。

### Evolution 与 Assessment

3. **M-B Evolution**：真实信号（考试均分/复测完成率/任务完成率）→ 确定性候选（降难度/提难度/复测节奏）→ 投影评估（样本 <5 → INSUFFICIENT_EVIDENCE，<3 次考试直接拒绝造候选）→ 批准生效 → 回滚。
   - `EVO-0001` 生效后新考试 `selection_reason` 含 EVO-0001，选题偏移实测。
4. **M-C Assessment 补强**：`llm_judge.py` 严格 JSON Schema（criterion/max/actual/reasoning/excerpt/missing/judge_version），verdict 由 points/max 确定性判定，失败→`SCORING_FAILED` 不给假分。
   - `followup.py` 7 类 Gap（Concept/Mechanism/Application/Boundary/Trade-off/Expression/Verification）每题 ≤2 条追问；`abort`（幂等 409）、`EXPIRED` 懒判定、`rescore` 安全重试；修复 StrictMode 每次进页双建考试。

### Learning 与 Observatory

5. **M-D Learning**：`teaching.py` 7 步通俗教学（类比→例子→原理→术语→结合项目→实践→验证）+ 7 种求助（simpler/analogy/example/project/code/diagram/unclear），LLM 路径 + 规则兜底 + 双层缓存；实测生成 26.8s、二次命中 0.08s。
6. **M-E 小补**：JD 对标 `POST /benchmark/jd`（关键词命中→目标确定性上调，永不下调）；`POST /learning/tasks` 手动/复测任务；`PATCH /bank/questions/{short_code}`、`merge`、单题 `reject`。
   - `llm.py` 的 `chat_with(trace, span_name)` 自动写 `llm:{model}` span（provider/model/tokens/耗时/成本/ok/retry），`GET /observatory/agents` 聚合最近 500 条 llm:* span（实测 deepseek-v4-flash · gateway · 2 calls · 7851 tokens · est ¥0.0157）。

### 部署与测试

7. **M-F 部署**：docker-compose（postgres 16 + api + web nginx）+ 双 Dockerfile + bootstrap/migrate/seed 启动链 + `.env.example` + `deploy/backup.ps1`/`restore.ps1` + LOCAL_DEPLOYMENT_GUIDE；`cp .env.example .env && docker compose up -d` 一键三容器，实测新库 bootstrap→migrate→seed→uvicorn 全链路、healthz/dashboard 200。
8. **测试**：pytest `server/tests/test_engines.py` **19/19 通过**（benchmark_jd 解析/上调规则、scorer MCQ+开放题、feedback 结构、followups 上限、llm_judge 确定性 verdict+空作答短路+坏 JSON 拒绝、teaching JSON 解析+规则兜底、隐私扫描、低风险闸门、evolution 四分支决策）。
   - E2E **8/8 场景通过**（API 级 curl/DB 断言 + 浏览器级 Chromium 实操作 UI，真实链路无 Mock）。
9. **E2E 要点**：E1 无/错 token→`401 {"detail":"invalid token"}`、不存在 workspace→`404 workspace 'xxx' not found`；E2 图片 OCR 5 阶段真实进度（`meta.progress.step/note` 落库、前端 600ms 轮询）。
   - E3 状态机 + judge-v1 不可用回落 rule-v1 并标注；E4 深度解答 LLM 实测 26-50s；E5 深度报告缓存于 exam.summary、缺题 `retried` 补齐、`_sanitize_pq` 丢弃占位键。
   - E6 dry-run PR（`PB-x DRY_RUN`）→ 真实 PR → Actions 构建 → PUBLISHED → ROLLED_BACK；E7 样本不足诚实拒绝；E8 今日任务卡 + 7 步内容 + 7 种求助。

### Mock 清理

10. **① 替换 14 项**：导入假进度、待审核假列表、Assets/Pipeline/Evolution 全 demo、DiagnosisReport 假小结、LearningTask 假声明、Settings 5 tab 硬编码、StrictMode 双建考试等。
11. **② 保留 6 处 DEMO 且显式标注**：种子证据 15 条/记忆 9 条/trace 示例（`Evidence.is_demo=true` 或「示例」文案），导入流程 banner 标注示例。
12. **③ 诚实降级**：全部 API 页统一「加载骨架 → 后端不可用红横幅+静态演示数据 → LLM 未配置提示配置路径」；规则兜底一律标注「规则生成/rule-v1」。后端全局 grep mock/dummy/fake **0 命中**；前端 `setTimeout` 仅轮询/防抖，无假进度。

## 遗留风险与待办

1. **明确不做（KNOWN_ISSUES 记录）**：`skill_dependencies` 表（种子标准未用依赖图）；`conversation_sources/messages` 独立表（对话文本最小版直存 memory 源）；alembic；图片/附件上传进公开草稿；诊断式追问的「动态插题」（以报告内追问建议 + 每题 ≤2 次约束实现语义，不打断答卷流）。
2. **后台任务无重启恢复**：asyncio 导入流水线进程重启后未完成任务停在 UPLOADED，需人工重试（已记录，不阻塞）。
3. **LLM 降级路径**：LLM 未配置时开放题走 rule-v1、教学内容走规则兜底、深度解答回落规则引擎——均为「诚实标注」而非假结果，但能力上限受限；LLM key 与 GitHub token 需用户在设置页/`.env` 配置后才启用对应功能（未配置时接口返回 NOT_CONFIGURED/400，不静默 mock）。
4. **可观测性边界**：TraceSpan 无独立 `created_at`（用 trace.started_at + duration 推算）；无 OpenTelemetry 导出（单机自足）；成本为估算口径（¥2/M tokens，可调常量）；日志为 stdout，未做结构化日志；`GET /observatory/agents` 只聚合最近 500 条 span。
5. **公开内容发布待确认（阻塞真实 publish 端到端联测，不阻塞开发）**：① 独立内容仓库 `TARGET_REPO` 用哪个 `owner/repo`（新建如 `Lueyu/agent-interview-content`、复用现有仓库子目录、或另建）；② GitHub PAT 是否在手（需 repo + workflow 权限，建议写入 `server/.env` 由程序读取，勿贴对话）；③ 是否允许低风险内容配置 `auto_merge=True`（默认关闭，保持人工合并 PR）。
6. **公开站首期范围**：仅落静态产物与目录结构；发布流状态机在后端阶段实现（v3.1 Part C）；门户反向拉回 CareerPilot 的内容包 JSON 尚未实现。
7. **安全取舍不可消除**：单机共享静态 token、无登录体系；CORS 写死 5173；多工作空间非多用户权限；隐私扫描正则可被语义隐私绕过；事实核查不判断真伪。均需在「localhost + 本机可信用户」前提下接受。
8. **数据库后续动作**：新环境首次启动依赖 `create_all` + `migrations/*.sql` 顺序执行；旧库补列需人工跑 `migrate.py`；无 alembic 版本表，复杂 schema 演进需自行维护幂等 SQL 与回滚脚本（destructive 部分需单独标注）。
