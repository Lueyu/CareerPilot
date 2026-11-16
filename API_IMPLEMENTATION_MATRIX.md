# API 实现矩阵（任务书 → 实际实现）

> 对照「Backend Completion & Real Business Logic Implementation」任务书要求的接口面与实际落地。全部端点挂在 `/api/v1` 下，鉴权 `Authorization: Bearer <API_TOKEN>`（或 `X-Api-Token`），工作空间 `X-Workspace: <slug>`（默认 demo）。

## 六大引擎

### Competency Engine（能力画像）
| 任务书要求 | 实现 | 状态 |
|---|---|---|
| 能力估计+置信度+5 态 | `services/competency.py` 确定性重算（证据等级加权，UNKNOWN≠0） | ✅ |
| 画像历史快照 | `competency_snapshots` 表 + 触发器引用（考试完成/导入通过/手动） | ✅ |
| 查询接口 | `GET /profile`、`GET /benchmark`、`GET /dashboard`（聚合） | ✅ |

### Assessment Engine（诊断考试）
| 要求 | 实现 | 状态 |
|---|---|---|
| 自适应选题（信息增益） | `services/selection.py`（差距大×权重高×证据少优先，reason 可解释）+ Evolution 候选策略覆盖 | ✅ |
| 考试状态机 | CREATED→ACTIVE→SUBMITTED→TRANSCRIPT_CONFIRMED→FROZEN→SCORING→COMPLETED；ABORTED/EXPIRED/SCORING_FAILED | ✅ |
| MCQ 确定性评分 | `services/scorer.py` | ✅ |
| 开放题 LLM 评分官 | `services/llm_judge.py`（verdict 确定性判定，失败→SCORING_FAILED 不给假分；judge_version 标注） | ✅ |
| 追问建议 | `services/followup.py`（7 类 Gap，≤2 条带 why） | ✅ |
| 诊断报告 | `GET /exams/{id}/report` + `POST /exams/{id}/report/deep`（能力域得分+确定性结论+AI 逐题剖析，缓存+缺题补齐重试） | ✅ |
| 放弃/超时/重评分 | `POST /exams/{id}/abort`、EXPIRED 懒判定、`POST /exams/{id}/rescore` | ✅ |

### Interview Intelligence（面经库）
| 要求 | 实现 | 状态 |
|---|---|---|
| 多格式导入 | PDF/DOCX/MD/TXT/JPG/PNG（RapidOCR 本地 OCR）+ 粘贴文本；真实后台流水线+进度落库 | ✅ |
| 提取→映射→查重→草稿→审核 | `services/text_extract.py` + `bank.py` 流水线；题目来源 USER_PRIVATE/PUBLIC/GENERATED | ✅ |
| 深度解答生成 | `services/answer_gen.py`（电梯稿/类比/脚本/追问带答案；LLM/规则双路径） | ✅ |
| 题目编辑/合并/驳回 | `PATCH /bank/questions/{sc}`、`POST /bank/questions/{sc}/merge`、`POST .../items/{id}/reject` | ✅ |

### Memory Engine（记忆中心）
| 要求 | 实现 | 状态 |
|---|---|---|
| 对话导入→候选记忆→确认 | `memory.py`（规则 5 类 + LLM 提取，CANDIDATE→ACTIVE） | ✅ |
| 纠正/取代/删除 | correct=新条目+旧 SUPERSEDED 链；delete=DISMISSED | ✅ |

### Learning Engine（学习中心）
| 要求 | 实现 | 状态 |
|---|---|---|
| 差距驱动任务生成 | `services/planner.py`（考试完成后自动，1/3/7 复测链，幂等） | ✅ |
| 教学内容生成 | `services/teaching.py`（7 步通俗教学+7 种求助，LLM 按题库材料+缓存） | ✅ |
| 手动/复测任务创建 | `POST /learning/tasks` | ✅ |

### Project Intelligence & Career Assets（项目与职业资产）
| 要求 | 实现 | 状态 |
|---|---|---|
| 真实仓库扫描 | `services/github_repo.py` + `project_analyzer.py`（路径/内容/README 三级证据，能力矩阵） | ✅ |
| 资产更新流水线 | `services/asset_pipeline.py`（草稿+风险分级+diff） | ✅ |
| 发布与回滚 | 复用公开内容模块（校验→dry-run→PR→发布→回滚） | ✅ |

## 平台与运维

| 要求 | 实现 | 状态 |
|---|---|---|
| Observatory 真实 Trace | Trace/TraceSpan/DecisionRecord/Event 全真实写入；`GET /observatory/agents`（LLM Agent Run：model/provider/tokens/耗时/成本估算） | ✅ |
| LLM 调用观测 | `chat_with(trace, span_name)` 自动写 `llm:{model}` span（token/耗时/成本/ok） | ✅ |
| Evolution 最小实现 | `services/evolution.py` + `api/v1/evolution.py`（PROPOSED→EVALUATED→ACTIVE/REJECTED/ROLLED_BACK；样本不足=INSUFFICIENT_EVIDENCE；默认人工批准；可回滚） | ✅ |
| JD 对标导入 | `POST /benchmark/jd`（关键词命中→确定性上调目标，不下调） | ✅ |
| 设置全面可交互 | `settings.py`（账号/工作空间/LLM/GitHub 连接 CRUD+真实连通性测试+导出+删除） | ✅ |
| 本地部署 | docker-compose（postgres+api+web）/Dockerfile×2/.env.example/backup.ps1/restore.ps1/LOCAL_DEPLOYMENT_GUIDE.md | ✅ |

## 兼容映射（任务书建议路径 → 实际路径）

| 任务书建议 | 实际 | 说明 |
|---|---|---|
| `/api/competency/*` | `/api/v1/profile` + `/api/v1/benchmark` | 沿用现有前端契约，避免破坏性改动 |
| `/api/publications/*` | `/api/v1/public-content/*` | 同上（公开内容发布） |
| `/api/agents/*` | `/api/v1/observatory/agents` | 观测口径并入 Observatory |
