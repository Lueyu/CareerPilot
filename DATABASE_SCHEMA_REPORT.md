# 数据库 Schema 报告

> 版本：v3.2 · 2026-11-16 · PostgreSQL 16 · SQLAlchemy 2.0(DeclarativeBase, asyncpg)
> 建表方式：`Base.metadata.create_all`（新库建全）+ `server/migrations/*.sql` 幂等迁移（旧库补列，`ADD COLUMN IF NOT EXISTS`）；执行器 `server/migrate.py`，可重复执行零副作用。刻意不引入 alembic（单机本地版，见 KNOWN_ISSUES）。

## 表清单（22 张）

### 平台与租户
| 表 | 模型 | 说明 |
|---|---|---|
| users | `models/workspace.py` | 单用户本地部署（email unique） |
| workspaces | 同上 | 多工作空间，slug unique；preferences JSONB |
| workspace_members | 同上 | 复合主键 (workspace_id,user_id)，role owner/admin/member |
| app_settings | `models/app_setting.py` | 运行时配置 KV（llm/github 连接，密钥仅本地） |

### 能力域与证据
| 表 | 模型 | 说明 |
|---|---|---|
| skills | `models/skill.py` | 8 能力域种子 |
| skill_states | 同上 | estimated_level/confidence/evidence_coverage/status 五态；UNKNOWN≠0 |
| evidence | `models/evidence.py` | 统一证据底座（EXAM_ANSWER/IMPORT/PROJECT 等，grade + support weight） |
| competency_snapshots | `models/competency.py` | 画像历史快照（追加式，trigger 来源标注，skills JSONB） |
| jd_sources | `models/jd.py` | JD 原文+解析结果（parsed JSONB），RAW/PARSED/APPLIED |
| job_standards / job_standard_targets | `models/benchmark.py` | 岗位标准（version unique，is_seed 种子标记）+ 每域权重/目标 |

### 考试与面经
| 表 | 模型 | 说明 |
|---|---|---|
| exams / exam_questions / exam_answers | `models/exam.py` | 状态机 CREATED→…→COMPLETED/ABORTED/EXPIRED/SCORING_FAILED；frozen_snapshot 冻结快照；judge_version（rule-v1/judge-v1） |
| import_sessions / import_items | `models/question.py` | 导入会话（meta.progress 真实进度）+ 题目草稿（DRAFT/APPROVED/REJECTED/MERGED，is_duplicate_of） |
| questions | 同上 | 公开+私有题库（source_type，canonical_question_id 变体合并） |

### 学习与记忆
| 表 | 模型 | 说明 |
|---|---|---|
| learning_tasks | `models/learning.py` | UNDERSTAND/PRACTICE/RETEST，retest_of/retest_round 复测链，content JSONB 缓存教学内容 |
| memory_items | `models/memory.py` | CANDIDATE→ACTIVE→SUPERSEDED/DISMISSED 生命周期 |

### 项目与资产
| 表 | 模型 | 说明 |
|---|---|---|
| projects / project_scans / project_findings | `models/project.py` | 仓库+扫描（commit 指纹去重）+ 证据级发现（VERIFIED/HIGH/UNVERIFIED_CLAIM） |
| career_assets / asset_update_runs | 同上 | 线上资产指纹（sha256）+ 更新流水线（PENDING_DIFF→…→PUBLISHED/ROLLED_BACK，risk_level） |

### 公开内容发布
| 表 | 模型 | 说明 |
|---|---|---|
| public_content_drafts / public_content_sources | `models/public_content.py` | 草稿（license/privacy/fact 状态机）+ 来源引用 |
| content_publication_runs | 同上 | 发布运行（幂等键 unique，dry_run，pr_number，build_status，rollback_commit_sha） |
| content_publication_rules / content_release_audit | 同上 | 发布规则（auto_merge）+ 审计流水（append-only） |

### 可观测
| 表 | 模型 | 说明 |
|---|---|---|
| traces / trace_spans | `models/trace.py` | 短码 TR-xxxx（可空自动生成）；trigger/trigger_ref；spans 含 llm:* Agent Run 明细 |
| decision_records | 同上 | 决策记录 DC-xxxx（question/chosen/alternatives/evidence_refs） |
| events | 同上 | append-only 事件流 |

## 隔离与级联
- 所有业务表 `workspace_id` FK → workspaces（`ondelete=CASCADE`）+ 索引。
- 例外（无 workspace_id 的子表）：exam_questions/exam_answers（经 exam 级联）、import_items（经 session 级联）、trace_spans（经 trace 级联）——导出/清理时经父表 `in_()` 查询。

## 索引要点
- 全部 workspace_id idx；skill_states(skill_id) unique per workspace；questions short_code unique；content_publication_runs.idempotency_key unique（幂等发布闸门）；traces.short_code unique；job_standards unique(role,level,version)；competency_snapshots/jd_sources workspace idx；traces.trigger idx。

## 已知简化
- 无 alembic 版本表（migrate.py 顺序执行，幂等）；无外键 ON DELETE SET NULL 之外的复杂约束；枚举用 String 列 + 应用层校验（保持 SQLite 兼容预留）。
