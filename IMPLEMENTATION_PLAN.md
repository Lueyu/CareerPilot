# IMPLEMENTATION_PLAN.md — Backend Completion 实施计划(文件级)

> 2026-11-16 制定 · 基线 0c65882 · 原则: 不推翻前端、不 Mock、不复写已有真实模块、每模块完成即测。

## 里程碑与文件清单

### M0 数据库与基础(P0-1)
| 文件 | 动作 |
|---|---|
| server/app/models/project.py | **新建**: Project/ProjectScan/ProjectFinding/CareerAsset/AssetUpdateRun |
| server/app/models/evolution.py | **新建**: EvolutionCandidate/EvolutionRun |
| server/app/models/competency.py | **新建**: CompetencySnapshot |
| server/app/models/jd.py | **新建**: JdSource |
| server/app/models/__init__.py | 注册新模型 |
| server/app/models/workspace.py | workspaces 加 preferences JSONB |
| server/app/models/exam.py | exam_answers 加 judge_version |
| server/app/models/trace.py | Trace 加 trigger/trigger_ref(可选) |
| server/migrations/001_backend_completion.sql | **新建**: 幂等 SQL(新表 IF NOT EXISTS+索引) |
| server/migrate.py | **新建**: 迁移顺序执行器(可重复执行) |

### M-A Project Intelligence + Career Assets(P0-5)
| 文件 | 动作 |
|---|---|
| server/app/services/github_repo.py | **新建**: 真实 GitHub REST 只读分析(repo/README/tree/languages/关键文件探测/commit 数) |
| server/app/services/project_analyzer.py | **新建**: 文件模式→能力域确定性映射(规则引擎, 非大 prompt)→findings |
| server/app/services/asset_pipeline.py | **新建**: 资产 diff 生成→风险分级→建 PublicContentDraft(复用 public_content 管线) |
| server/app/api/v1/projects.py | **新建**: GET/POST /projects, POST /projects/{id}/scan, GET /projects/{id}/scans·findings |
| server/app/api/v1/career_assets.py | **新建**: GET /career-assets, POST /career-assets/sync, GET /career-assets/updates, POST /updates/{id}/approve→publish→rollback(透传 public_content) |
| server/app/main.py | 注册 2 router |
| web/src/pages/Assets.tsx | 接真 API(保设计) |
| web/src/pages/ProjectReview.tsx | 接真 findings |
| web/src/pages/Pipeline.tsx | 接真 asset_update_runs |

### M-B Evolution Foundation(P0-7)
| 文件 | 动作 |
|---|---|
| server/app/services/evolution.py | **新建**: 候选生成(确定性: 复测通过率/评分争议/任务完成率)→评估(真实 DB 指标, 样本不足=INSUFFICIENT_EVIDENCE) |
| server/app/api/v1/evolution.py | **新建**: GET/POST /evolution/candidates, POST .../{id}/evaluate·approve·reject·rollback |
| server/app/main.py | 注册 |
| web/src/pages/Evolution.tsx | 接真(空态诚实) |

### M-C Assessment 补强(P0-2)
| 文件 | 动作 |
|---|---|
| server/app/services/llm_judge.py | **新建**: 开放题 LLM Judge(严格 JSON Schema 校验: criterion/max/actual/reasoning/excerpt/missing/judge_version); 失败→SCORING_FAILED 不给假分 |
| server/app/services/scorer.py | 集成 judge 路径(未配置 LLM→rule-v1 诚实标记) |
| server/app/services/followup.py | **新建**: 7 类 Gap 分类(Concept/Mechanism/Application/Boundary/Trade-off/Expression/Verification)规则引擎→每题≤2 追问建议 |
| server/app/api/v1/exams.py | submit 集成 judge+SCORING_FAILED; POST /{id}/abort; EXPIRED 判定; POST /{id}/rescore(安全重试) |
| web/src/pages/DiagnosisReport.tsx | judge_version/追问展示 |

### M-D Learning 内容生成(P0-3)
| 文件 | 动作 |
|---|---|
| server/app/services/teaching.py | **新建**: 7 步通俗教学内容(类比→例子→原理→术语→结合项目→实践→验证); LLM 路径+规则路径(材料=题库同域题目 answer_html); 交互 help(modes: simpler/analogy/example/project/code/diagram/unclear) |
| server/app/api/v1/learning.py | GET /tasks/{id}/content(生成+缓存进 task.content), POST /tasks/{id}/help |
| web/src/pages/LearningTask.tsx | 接真内容+交互按钮; 删除假声明 |

### M-E 小补(P0-4/P0-6/接口尾差)
| 文件 | 动作 |
|---|---|
| server/app/api/v1/bank.py | PATCH /bank/questions/{short_code}(编辑), POST .../merge(手动合并), POST /bank/imports/{id}/items/{item_id}/reject |
| server/app/api/v1/benchmark.py | POST /benchmark/jd(JD 导入: 解析→存 jd_sources→生成对标版本) |
| server/app/api/v1/learning.py | POST /learning/tasks(手动创建/安排复测) |
| server/app/services/llm.py | chat() 增可选 span 记录(provider/model/duration/tokens/retry)供观测 |
| server/app/api/v1/observatory.py | GET /observatory/agents(聚合 agent spans) |

### M-F 本地部署(P0-1 运维面)
| 文件 | 动作 |
|---|---|
| docker-compose.yml | postgres 16 + server(api) + web(nginx 静态) |
| server/Dockerfile / web/Dockerfile | 新建 |
| .env.example | 新建(全部变量+注释) |
| deploy/backup.ps1·restore.ps1 | pg_dump 备份/恢复 |
| LOCAL_DEPLOYMENT_GUIDE.md | 新建 |

### M-G 测试与文档(P0 验收)
| 文件 | 动作 |
|---|---|
| server/tests/test_*.py | pytest: projects/evolution/llm_judge schema/teaching/bank patch/jd(每模块) |
| E2E 手册化验证 | 任务书 8 个 E2E 场景(API 级+浏览器级) |
| 根目录 10 份最终文档 | API_IMPLEMENTATION_MATRIX / DATABASE_SCHEMA_REPORT / MOCK_REMOVAL_REPORT / IMPLEMENTATION_REPORT / E2E_TEST_REPORT / SECURITY_REPORT / OBSERVABILITY_REPORT / KNOWN_ISSUES / LOCAL_DEPLOYMENT_GUIDE / (BACKEND_AUDIT 已有) |

## 明确不做(记录于 KNOWN_ISSUES)
- skill_dependencies 表(种子标准未用依赖图);
- conversation_sources/messages 独立表(对话文本最小版直存记忆源);
- alembic(以 create_all+幂等 SQL 迁移器替代, 单机本地版足够);
- 图片/附件上传进公开草稿(现有 JSONB meta 可承载 URL, 后续版本);
- 诊断式追问的"动态插题"(以报告内追问建议+≤2 次约束实现任务书语义, 不打断答卷流)。

## 执行顺序
M0 → M-A → M-B → M-C → M-D → M-E → M-F → M-G; 每个里程碑: 实现 → pytest/ curl 实测 → 前端 tsc+build → commit。
