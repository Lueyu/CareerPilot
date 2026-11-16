# MOCK_REMOVAL_PLAN.md — Demo/Mock 清理计划

> 2026-11-16 · 基线 0c65882。原则(任务书 4.4): 每个 Demo 功能三选一 —— ①替换真实实现 ②保留为明确标记的开发演示数据 ③暂时隐藏。**真实 UI 不得把 Demo 伪装成真实结果**(现有 12 页的"API 优先+失败降级静态数据+红色横幅"模式符合该原则, 保留)。

## 1. 全局搜索结果汇总(grep mock/demo/dummy/placeholder/TODO/fake/setTimeout)

| 文件 | 命中性质 | 处置(①/②/③) |
|---|---|---|
| web/src/data/demo.ts(唯一假数据源, 21 页中 16 页引用) | 静态演示数据 | ②大部分保留(降级用), ①被 4 个新真实页替换引用 |
| web/src/pages/Assets.tsx | 纯假(3 个假按钮) | ① 接真: GET /projects, POST /projects/{id}/scan |
| web/src/pages/ProjectReview.tsx | 纯假 | ① 接真: GET /projects/{id}/findings |
| web/src/pages/Pipeline.tsx | 纯假 | ① 接真: GET /career-assets/updates(资产更新运行) |
| web/src/pages/Evolution.tsx | 纯假 | ① 接真: GET /evolution/candidates(无数据时诚实空态) |
| web/src/components/Overlays.tsx 快捷操作 | 部分假(安排复测/创建任务/项目扫描) | ① 接真或 ③ 暂时隐藏 |
| Bank.tsx q-card 操作: 编辑/合并重复 | 假按钮 | ① PATCH /bank/questions/{short_code} + merge |
| Diagnosis.tsx 考试降级 demo 试卷 | 有红色横幅标注 | ② 保留(诚实降级) |
| LearningTask.tsx:275 "正式版由大模型生成" | 声明不符 | ① P0-3 实现内容生成后删除 |
| DiagnosisReport.tsx 降级静态报告 | 有横幅 | ② 保留 |
| Dashboard/Benchmark/ExamCenter/Learning/Memory/Observatory/Profile 降级横幅 | 有横幅 | ② 保留 |
| ui.tsx DemoBanner 组件 | 显式 DEMO 标签 | ② 保留(诚实标记机制本身) |
| server 全局 grep mock/dummy/fake | **0 命中**(后端无 mock) | — |
| bank.py "todo" 变量 | 普通命名非 mock | — |
| setTimeout | 前端仅轮询/防抖用途, 无假进度 | — |

## 2. 替换实施顺序(与 IMPLEMENTATION_PLAN 一致)

1. **M-A(P0-5)**: Assets/ProjectReview/Pipeline → 真实 projects/findings/asset_update_runs API; "导入仓库"接 POST /projects(真实 GitHub 只读扫描); "重新分析"接 POST /projects/{id}/scan; 流水线列表接 GET /career-assets/updates。删除 PROJECTS/PROJECT_FINDINGS/DRAFT_CONTENT/PIPELINE_STEPS/PRS 对这些页的引用(demo.ts 保留供降级横幅)。
2. **M-B(P0-7)**: Evolution.tsx → GET/POST /evolution/candidates + evaluate/approve/reject/rollback; 数据不足时显示 INSUFFICIENT_EVIDENCE 空态(不再渲染演示实验卡, demo.ts EVOS 引用移除)。
3. **M-C(P0-3)**: LearningTask.tsx 接 GET /learning/tasks/{id}/content + POST .../help(7 类交互); 删除 275 行假声明。
4. **M-D(P0-2)**: DiagnosisReport 增 judge_version 展示; Diagnosis 提交后 SCORING_FAILED 重试入口。
5. **M-E**: Overlays 快捷操作: 安排复测→POST /learning/tasks(手动复测); 项目扫描→接 /projects; 创建学习任务→POST /learning/tasks。
6. **M-F**: Bank 编辑/合并重复按钮接真。

## 3. 保留的演示数据(明确标记, 不伪装)

- demo.ts 降级兜底(仅后端不可达时, 一律红色横幅"后端不可用…以下为静态演示数据")。
- Onboarding 特性卡(纯营销文案, 非业务数据)。
- 各页 DemoBanner(PRD 要求演示数据显式标记)。

## 4. 禁止事项复述

- 不新增任何把假数据当真结果的功能;
- 外部服务未配置(GITHUB_TOKEN/LLM key)时接口返回 NOT_CONFIGURED 明确状态, 不假装成功;
- public_content 已是 dry_run+幂等+审计真实现, 本轮不引入任何 mock 发布。
