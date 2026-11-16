# BACKEND_AUDIT.md — CareerPilot 全项目后端审计

> 审计时间: 2026-11-16 · 基线 commit `0c65882` (master) · 审计方式: 全量阅读 server/app 41 个 py 文件(5905 行) + web/src 21 个页面 + 部署文件盘点
> 状态定义: `NOT_IMPLEMENTED`(无代码) / `MOCK`(假数据或假动作) / `PARTIAL`(部分真实) / `IMPLEMENTED`(代码真实) / `VERIFIED`(本轮实测通过)

## 1. 技术栈(以真实代码为准, 非假设)

| 层 | 技术 | 证据 |
|---|---|---|
| 前端 | Vite 5 + React 18.3 + react-router-dom 6 + TypeScript 5.6 | web/package.json |
| 后端 | FastAPI + SQLAlchemy 2 async + asyncpg + pydantic-settings | server/app |
| 数据库 | PostgreSQL (postgresql+asyncpg://localhost:5432/careerpilot) | app/config.py |
| LLM | OpenAI 兼容 httpx 直调(deepseek/glm), 未配置时规则引擎降级 | app/services/llm.py |
| OCR | rapidocr-onnxruntime + Pillow(本地, 无云依赖) | app/services/text_extract.py |
| GitHub | httpx 直调 REST(无 PyGithub), 支持 dry_run | app/services/github_publish.py |
| 部署 | 本地 uvicorn(系统 Python312) + npm run dev; **无 Docker, 无 .env.example** | 本轮盘点 |
| 公开站 | portal/build.mjs 纯静态 → OSS(agent-interview.icu) | portal/ |

## 2. 后端路由面(全部经 get_workspace 强制 Workspace 隔离, prefix /api/v1)

| 路由文件 | 行数 | 端点数 | 覆盖 |
|---|---|---|---|
| bank.py | 839 | 9 | 面经导入/审核/题库(最重) |
| public_content.py | 688 | 15 | 公开内容草稿/校验/发布/回滚(P0-8) |
| exams.py | 370 | 6 | 考试状态机/评分/报告 |
| settings.py | 296 | 4 | 设置真实化 |
| memory.py | 226 | 5 | 记忆时间线/确认/纠正/删除/对话导入 |
| observatory.py | 197 | 5 | Trace/Decision/Events |
| dashboard.py | 147 | 1 | 总览聚合 |
| learning.py | 134 | 4 | 任务列表/详情/开始/完成 |
| profile.py | 106 | 2 | 能力画像/技能详情 |
| benchmark.py | 61 | 1 | 岗位对标(只读) |

**未存在的路由面**: projects(0), career-assets(0), evolution(0), jobs/JD 导入(0), learning content 生成(0)。

## 3. 页面级功能映射与状态

| 页面 | 前端组件 | API | 数据表 | 业务逻辑 | 当前状态 |
|---|---|---|---|---|---|
| Dashboard 总览 | Dashboard.tsx | GET /dashboard | evidence/learning_tasks/import_sessions/exams | dashboard.summary 聚合 | PARTIAL(真实+诚实 demo 降级横幅; quick-actions 部分假) |
| Career Benchmark | Benchmark.tsx | GET /benchmark | job_standards/targets/skill_states | competency 重算 | PARTIAL(**无 POST /jobs、无 JD 导入**) |
| 考试中心 | ExamCenter.tsx | GET/POST /exams | exams | exams.list/create | IMPLEMENTED |
| 考试作答 | Diagnosis.tsx | POST /exams/{id}/answers·submit | exam_answers | 状态机 CREATED→…→COMPLETED | PARTIAL(见 4.1) |
| 考试报告 | DiagnosisReport.tsx | GET /exams/{id}/report | exam_answers/evidence | scorer 规则评分 | PARTIAL(开放题无 LLM Judge 层) |
| Competency Profile | Profile.tsx / ProfileDetail.tsx | GET /profile, /profile/{key} | skill_states/evidence | competency.py 规则引擎 | PARTIAL(无画像历史快照) |
| Learning Center | Learning.tsx | GET /learning | learning_tasks | planner.py 1/3/7 复测链 | IMPLEMENTED |
| 学习任务详情 | LearningTask.tsx | GET /learning/tasks/{id}, start/complete | learning_tasks | 任务模板(静态步骤) | PARTIAL(**无教学内容生成/交互帮助**) |
| Interview Bank | Bank.tsx | 9 端点 | import_sessions/items/questions | 导入流水线(真实 OCR/LLM/规则) | IMPLEMENTED |
| Projects | Assets.tsx | **无** | **无** | **无** | NOT_IMPLEMENTED(纯 demo.ts) |
| 项目审查 | ProjectReview.tsx | **无** | **无** | **无** | NOT_IMPLEMENTED(纯 demo) |
| Career Assets 流水线 | Pipeline.tsx | **无** | **无** | **无** | NOT_IMPLEMENTED(纯 demo; 但 public_content 管线可复用) |
| Memory Center | Memory.tsx | 5 端点 | memory_items | 提取(规则+LLM)/确认/纠正(SUPERSEDED)/删除 | IMPLEMENTED |
| Observatory | Observatory.tsx | 5 端点 | traces/spans/decision_records/events | tracing.py 全链路 | IMPLEMENTED(**Agent Run 维度缺失**) |
| Evolution Lab | Evolution.tsx | **无** | **无** | **无** | NOT_IMPLEMENTED(纯 demo) |
| Settings | Settings.tsx | 4 端点 + rules/audit | workspaces/users/public_content_* | settings.py | IMPLEMENTED |

## 4. 关键业务逻辑审计

### 4.1 考试状态机(exams.py)
- 真实: CREATED→ACTIVE→SUBMITTED→TRANSCRIPT_CONFIRMED→FROZEN→SCORING→COMPLETED, 提交后写证据→重算能力→生成学习任务, 全程 trace。
- 缺失: `ABORTED`/`EXPIRED` 异常态(无 abort 接口, 无超时判定); `SCORING_FAILED` 态(评分异常无兜底路径); 诊断式追问(7 类 Gap 分类, 每题≤2 次追问)未实现; 重复提交保护有(status 检查)。
- 评分(scorer.py): MCQ 确定性判分 ✓; 开放题为**关键词覆盖度规则评分**(非假分数, 但不满足任务书"开放题用真实 LLM Judge + Rubric Schema 校验"); 无 judge_version 落库(规则版应记 rule-v1)。

### 4.2 Competency Engine(competency.py)
- 真实: estimated_level=clamp(基线2.5+Σ证据支撑), confidence, evidence_coverage, 5 态判定(UNKNOWN≠0 ✓), dimension_scores。
- 缺失: 无 competency_snapshots 历史表(画像演进不可追溯); 证据只来自 EXAM_ANSWER/导入(项目/复测证据类型已定义未产出)。

### 4.3 Learning(planner.py/learning.py)
- 真实: 差距加权选题域→任务模板→1/3/7 天复测链(幂等: 活跃域不重复生成); 任务完成/开始真实落库。
- 缺失: 任务内容为静态模板(TASK_TEMPLATES), 无"通俗教学 7 步内容"生成, 无"再简单一点/换个类比/结合我的项目"交互; LearningTask.tsx:275 自己声明"正式版由大模型按你的画像实时生成"。

### 4.4 Memory(memory.py)
- 真实: CANDIDATE→ACTIVE 确认、纠正=新条目+旧条目 SUPERSEDED+superseded_by、删除=DISMISSED(软删)、对话导入(规则+LLM 双路径, 去重)。
- 缺失: 状态机无 EXPIRED/RETRACTED(最小版可不加, 记录在 GAP 报告); 无检索/过滤接口(前端按类型过滤即可, 可接受)。

### 4.5 面经导入(bank.py)
- 真实: 上传→解析(PDF/DOCX/TXT/MD/JPG/PNG OCR)→提取(LLM/规则)→技能映射→去重→生成草稿(电梯稿/类比/追问带答案)→人工审核→入题库; 后台任务+真实进度落库; 幂等防重; 审核后部分通过不误关会话。
- 缺失: 无 PATCH 题目编辑接口(前端编辑按钮待查); 无合并重复接口(去重自动做, 手动合并缺); 原始文件已存 ImportSession.files ✓。

### 4.6 公开内容发布(public_content.py, P0-8)
- 真实: 5 表+完整状态机+四类校验(privacy/fact/license/markdown lint)+GitHub REST(建分支/写文件/PR/查 Actions 构建/合并)+dry_run+幂等键+审计+回滚。**已达到任务书第八阶段要求**。缺: 图片/附件上传(可后补, 记录)。

### 4.7 Observability(tracing.py)
- 真实: Trace/Span/DecisionRecord/Event 全链路落库, trace+cost 记录, workspace 隔离。
- 缺失: AgentRun 维度(provider/model/token_usage/重试)未记录——LLM 调用未单独成 span 记录用量。

## 5. 部署与运维

| 项 | 状态 |
|---|---|
| 数据持久化 | PostgreSQL 真实持久 ✓(重启不丢) |
| 迁移 | 无 alembic; 开发用 create_all 自动建表; **无重复执行/回滚的迁移脚本** |
| 备份恢复 | 无(需 pg_dump 脚本) |
| Docker | 无 docker-compose/Dockerfile |
| .env.example | 无 |
| 健康检查 | GET /healthz ✓ |
| 密钥管理 | .env 服务端读取, 不下发前端 ✓; settings 接口不回显 ✓ |
| 后台任务 | asyncio 后台任务(导入流水线) ✓, 无重启恢复(未完成任务停在 UPLOADED, 可接受需记录) |

## 6. 结论(审计后直接开发的优先级依据)

1. **P0-5 Project Intelligence + Career Assets 是最大空白**(前后端皆无, 必须从模型到 API 到前端全建, 并复用 public_content 管线做资产发布)。
2. **P0-7 Evolution 第二空白**(全建, 最小真实实现: 候选生成/评估/版本管理/回滚)。
3. **P0-2 Assessment 补强**: LLM Judge 层(schema 校验+judge_version+SCORING_FAILED 安全重试)+ABORTED/EXPIRED+诊断式追问反馈。
4. **P0-3 Learning 补强**: 教学内容生成 + 交互帮助端点(LLM/规则双路径, 材料复用题库)。
5. **P0-1 DB 补齐**: 新增实体表(projects/evolution/competency_snapshots)+索引+迁移脚本+备份脚本。
6. **P0-6 Observability 补 AgentRun 维度**。
7. **部署**: docker-compose + Dockerfile×2 + .env.example + LOCAL_DEPLOYMENT_GUIDE。
8. P0-4/P0-8 基本达标, 小补即可。
