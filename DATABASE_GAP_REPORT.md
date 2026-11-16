# DATABASE_GAP_REPORT.md — 数据库模型缺口报告

> 2026-11-16 · 基线 0c65882。现有模型: 15 张表(workspaces/users/workspace_members/skills/skill_states/evidence/questions/import_sessions/import_items/exams/exam_questions/exam_answers/learning_tasks/memory_items/job_standards+job_standard_targets/traces+trace_spans+decision_records+events/public_content_5 表 ≈ 合计 20+)。核心事实: **PostgreSQL 真实持久化已成立**, 本报告只列缺口, 不推倒重来(任务书 5.1: 已用 PostgreSQL 不强行迁移)。

## 1. 任务书实体清单 → 现有表映射(合理合并, 不机械建表)

| 任务书逻辑实体 | 现有表 | 判定 |
|---|---|---|
| users/workspaces/workspace_members | 同名 3 表(+workspace_members role) | ✓ 已有 |
| user_preferences | 无(单用户本地版, workspace 上已有 target_role/level; 偏好存 workspace.meta) | 合并处理: **新增 preferences JSONB 列**, 不建新表 |
| job_profiles/jd_sources | 无 | **缺口 → 新表 jd_sources**(JD 导入解析记录) |
| skills | skills(含 dimensions JSONB) | ✓ |
| skill_dependencies | 无(种子标准未用依赖图) | 暂缓(记录为 KNOWN_ISSUES, 不阻塞) |
| job_requirements/benchmark_versions | job_standards + job_standard_targets(version 字段已有) | ✓ 合并覆盖 |
| questions/question_variants/question_sources | questions(单表; source 字段+import_items 承担 sources; variants 由去重 meta 承担) | ✓ 合并覆盖 |
| rubrics | questions.rubric JSONB(含 points/max_points/answer_index/advice) | ✓ |
| assessment_sessions/assessment_questions | exams + exam_questions | ✓ 改名级差异(兼容保留) |
| assessment_turns | 无(**诊断式追问未实现**) | 缺口 → 追问最小版存 answer.feedback, 不建表(≤2 次/题) |
| assessment_scores | exam_answers.points/verdict/feedback + exam.score | ✓ |
| evidence | evidence(kind/grade/support/evidence_refs) | ✓ |
| skill_states | skill_states(5 态/confidence/coverage/model_version) | ✓ |
| competency_snapshots/competency_history | **无历史表** | **缺口 → 新表 competency_snapshots** |
| learning_tasks/learning_interventions/learning_progress | learning_tasks(status/retest_of/retest_round/due_date; content JSONB) | ✓ 合并(intervention=kind, progress=status+completed_at) |
| learning_contents | 无(模板静态) | 缺口 → learning_tasks.content 承载生成内容(不建新表) |
| retest_schedules | learning_tasks(retest_round+due_date) | ✓ 合并 |
| interview_materials/files/extracted_questions/question_reviews/question_tags | import_sessions(+files JSONB) + import_items + questions(tags JSONB) | ✓ 合并覆盖 |
| conversation_sources/messages | 无独立表(对话文本入 memory_items.source_ref) | 最小版不建表(记录取舍) |
| memory_events/facts/revisions | memory_items(mtype/content/status/superseded_by/meta) | ✓ 合并(事件流=events 表) |
| projects/project_scans/project_findings/project_evidence | **全无** | **缺口 → 新表 projects/project_scans/project_findings**(finding 即证据, 不单拆 project_evidence) |
| career_assets/asset_claims/asset_update_runs | **全无** | **缺口 → 新表 career_assets + asset_update_runs**(claim 存 runs.findings JSONB) |
| automation_rules/automation_runs | public_content_rules + content_publication_runs(发布自动化已覆盖) + events | ✓ 合并覆盖 |
| workflow_traces/spans/decision_records | traces/trace_spans/decision_records | ✓ |
| agent_runs | **无**(LLM 调用无用量落库) | 缺口 → trace_spans.detail 扩展 agent 字段(不建表, 见 GAP3) |
| strategy_versions/evolution_candidates/evolution_runs | **全无** | **缺口 → 新表 evolution_candidates + evolution_runs**(strategy_version 存 candidate.body) |

## 2. 必建新表(本轮开发范围)

| 表 | 用途 | 关键列 |
|---|---|---|
| jd_sources | JD 导入 | workspace_id FK, company, title, level, raw_text, extracted JSONB, standard_id FK nullable, created_at |
| projects | GitHub 项目 | workspace_id FK, name, repo_url, branch, default_branch, last_commit_sha, last_scanned_at, status, meta JSONB |
| project_scans | 扫描运行 | project_id FK, status(RUNNING/SUCCESS/FAILED), commit_sha, stats JSONB, error, trace_id FK, created_at/finished_at |
| project_findings | 发现(=证据) | scan_id FK, project_id FK, skill_id FK nullable, level(VERIFIED/HIGH/UNVERIFIED_CLAIM), title, detail, file_path, evidence_ref, domain |
| career_assets | 职业资产(简历/网站页面) | workspace_id FK, kind(RESUME/WEBSITE_PAGE), name, source_url, content_hash, synced_at, meta |
| asset_update_runs | 资产更新流水线 | workspace_id FK, project_id FK nullable, status, diff_markdown, risk_level, draft_id FK(public_content_drafts), publication_id FK, trace_id FK |
| competency_snapshots | 画像历史 | workspace_id FK, skill_id FK, estimated_level, confidence, status, evidence_coverage, taken_at |
| evolution_candidates | 策略候选 | workspace_id FK, short_code, title, kind, status(PROPOSED/TESTING/EVALUATED/APPROVED/REJECTED/ACTIVE/ROLLED_BACK), body JSONB(baseline/candidate), rationale, approved_at, created_at |
| evolution_runs | 候选评估 | candidate_id FK, metrics JSONB, sample_size, regression JSONB, verdict, evaluated_at |

全部: workspace_id FK ondelete CASCADE; UTC DateTime(timezone=True); JSONB 结构化; 常用查询列加索引(workspace_id+status, project_id, candidate_id)。

## 3. 现有表需要的小改动

1. workspaces 加 `preferences JSONB`(偏好合并列)。
2. trace_spans.detail 增加可选字段 `{"agent": {"provider","model","duration_ms","tokens_in","tokens_out","retry_count"}}`(AgentRun 维度, 不建表)。
3. exam_answers 增加 `judge_version String(32) nullable`(rule-v1 / llm-judge-v1)。
4. 索引补齐: exams(workspace_id,status), evidence(workspace_id,skill_id), learning_tasks(workspace_id,status), memory_items(workspace_id,status), questions(skill_id), import_items(session_id,status), traces(workspace_id,started_at), skill_states(workspace_id)。

## 4. 迁移与运维策略

- 开发态沿用 `Base.metadata.create_all`(新表重启即建, 现状已验证)。
- 新增 `server/migrations/` 存放幂等 SQL(带 IF NOT EXISTS / DO 块), 提供 `migrate.py` 顺序执行器(可重复执行), 满足"可重复执行"; 回滚脚本单独提供( destructive 部分标注)。
- 备份/恢复: `deploy/backup.ps1`(pg_dump 自定义格式) + `deploy/restore.ps1`, 写入 LOCAL_DEPLOYMENT_GUIDE。
- 时间列核查: 现有模型全部 DateTime(timezone=True)+server_default=func.now() ✓, API 层统一 isoformat ✓。
- 事务: 现有写路径全部 async session 事务提交 ✓; 后台任务独立 session ✓。
- **不做**: SQLite 迁移(违反 5.1 既有 PostgreSQL 原则)、alembic 全量引入(成本>收益, create_all+幂等 SQL 已满足单机本地版)。
