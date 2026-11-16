# API_GAP_REPORT.md — 前端功能 ↔ API 缺口报告

> 与 BACKEND_AUDIT.md 同一轮审计(2026-11-16, 基线 0c65882)。任务书第六节的接口建议清单与本项目现有命名不同处, 按"优先兼容现有接口"原则处理, 映射在下表第 3 列。

## 1. 任务书建议接口 → 现有实现映射

| 任务书建议 | 本项目现有接口(兼容优先) | 状态 | 缺口说明 |
|---|---|---|---|
| GET /dashboard/summary·tasks·recent-activity | GET /api/v1/dashboard(单端点聚合三块) | IMPLEMENTED | 无缺口(结构已含 stats/today_tasks/pending_imports/recent_activity) |
| GET·POST /jobs; POST /jobs/{id}/import-jd | GET /api/v1/benchmark(只读种子标准) | **NOT_IMPLEMENTED** | 无自定义岗位、无 JD 导入解析、无 jd_sources 表 |
| GET /jobs/{id}/requirements | GET /api/v1/benchmark(rows 即 requirements) | PARTIAL | 依赖上一行 |
| POST /assessments | POST /api/v1/exams | IMPLEMENTED | — |
| GET /assessments·{id} | GET /api/v1/exams·{id} | IMPLEMENTED | — |
| POST /assessments/{id}/start | 创建即 ACTIVE(exams.py create 直接置 ACTIVE) | PARTIAL | 语义差异(创建即开始), 前端一致, 保留 |
| POST /assessments/{id}/answers | POST /api/v1/exams/{id}/answers(草稿保存) | IMPLEMENTED | — |
| POST /assessments/{id}/submit | POST /api/v1/exams/{id}/submit | PARTIAL | 缺 ABORTED/EXPIRED/SCORING_FAILED 态 |
| GET /assessments/{id}/report | GET /api/v1/exams/{id}/report | PARTIAL | 缺 LLM Judge 维度(judge_version/criterion 结构) |
| GET /competency/profile·gaps | GET /api/v1/profile, /benchmark(gaps=BELOW_TARGET 行) | IMPLEMENTED | — |
| GET /competency/skills/{id} | GET /api/v1/profile/{skill_key} | IMPLEMENTED | — |
| GET /competency/evidence | GET /api/v1/profile(含 evidence) | IMPLEMENTED | — |
| GET /learning/tasks | GET /api/v1/learning | IMPLEMENTED | — |
| POST /learning/tasks | 由 planner 自动生成(无手动创建) | PARTIAL | 任务书允许; 手动建任务后补 |
| POST /learning/tasks/{id}/complete | POST /api/v1/learning/tasks/{id}/complete | IMPLEMENTED | — |
| GET /learning/tasks/{id}/content | **无独立端点**(详情里返回静态模板 steps) | **NOT_IMPLEMENTED** | 教学内容生成(7 步通俗结构)缺失 |
| POST /learning/tasks/{id}/retest | 复测任务由 planner 1/3/7 自动生成(RETEST kind) | IMPLEMENTED(自动版) | 手动"安排复测"快捷操作在 Overlays 里是假 |
| POST /interviews/import | POST /api/v1/bank/imports·upload | IMPLEMENTED | — |
| GET /interviews/imports/{id} | GET /api/v1/bank/imports/{id}(+ /pending) | IMPLEMENTED | — |
| GET /interviews/questions | GET /api/v1/bank(161+题, 过滤) | IMPLEMENTED | — |
| PATCH /interviews/questions/{id} | **无** | **NOT_IMPLEMENTED** | 前端"编辑"按钮无后端 |
| POST .../questions/{id}/approve·reject | POST /api/v1/bank/imports/{id}/approve(部分通过) | PARTIAL | reject 端点缺(可加 reject 项) |
| POST /memory/import | POST /api/v1/memory/import-dialog | IMPLEMENTED | — |
| GET /memory·timeline | GET /api/v1/memory | IMPLEMENTED | — |
| PATCH·DELETE /memory/{id} | POST /memory/{id}/confirm·correct, DELETE | IMPLEMENTED | PATCH 语义由 confirm/correct 覆盖 |
| GET /projects | **无** | **NOT_IMPLEMENTED** | 整个 Projects 模块前后端皆无 |
| POST /projects·{id}/scan | **无** | **NOT_IMPLEMENTED** | 同上(真实 GitHub 分析) |
| GET /projects/{id}/findings·evidence | **无** | **NOT_IMPLEMENTED** | 同上 |
| GET /career-assets | **无** | **NOT_IMPLEMENTED** | 整个 Career Assets 模块无 |
| POST /career-assets/sync·approve·publish·rollback | **无**; 但 public_content/drafts 全管线可复用 | **NOT_IMPLEMENTED** | 计划: 资产更新运行→生成 diff→建 PublicContentDraft→复用既有 approve/publish/rollback |
| GET /observability/traces·{id}·decisions | GET /api/v1/observatory/traces·{id}·decisions·events | IMPLEMENTED | — |
| GET /observability/agents | **无**(AgentRun 维度未记录) | **NOT_IMPLEMENTED** | llm.py 调用无用量/耗时/重试落库 |
| GET /observability/automations | 部分(自动化规则=public_content/rules) | PARTIAL | 最小版映射到 rules+audit |
| GET /evolution/candidates 等 5 端点 | **无** | **NOT_IMPLEMENTED** | Evolution 模块前后端皆无 |

## 2. 前端按钮级假动作清单(必须消灭或接真)

| 位置 | 动作 | 现状 |
|---|---|---|
| Assets.tsx:17 | ＋导入仓库 | toast "Demo 未接入 GitHub"(假) |
| Assets.tsx:51 | 重新分析 | toast "Demo"(假) |
| Assets.tsx:63-72 | 流水线 8 步 | 静态渲染"最近运行 11-15 09:58(成功)"(假) |
| ProjectReview.tsx | 整页 | demo.ts PROJECT_FINDINGS(假) |
| Pipeline.tsx | 整页 | DRAFT_CONTENT/PIPELINE_STEPS/PRS 全假 |
| Evolution.tsx | 整页 | EVOS 全假(演示实验) |
| Overlays 快捷操作 | 部分项 | 导入面经/开始诊断是真路由; "开始一次实践/安排复测/创建学习任务/发起项目扫描"待接真 |
| Bank.tsx q-card 操作 | 编辑/加入考试/合并重复 | 加入考试已真; 编辑/合并重复待接真 |
| Diagnosis.tsx | 考试降级 | API 失败时降级本地 demo 试卷(有明确 banner, 保留但标注) |
| LearningTask.tsx:275 | "正式版由大模型实时生成" | 声明与实现不符 → P0-3 实现后删除声明 |

## 3. 结论

- 真实缺口共 **4 大块**: Projects+CareerAssets(全无)、Evolution(全无)、JD 导入(无)、Learning 内容生成(无); 小缺口: PATCH 题目/reject 项/abort 考试/AgentRun 观测/手动复测。
- 前端已有 12 页真实接 API 且为"API 优先+诚实降级横幅"模式, 本次开发沿用该模式补齐 4 页, 不推翻前端。
