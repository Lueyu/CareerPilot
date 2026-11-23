# AI 报告归档（ai-reports）总索引

> 2026-11-23 建立。仓库历史上由 AI 过程产生的验收 / 审计 / 计划 / 报告类文档（约 200 份）已**提炼归档**到本目录，原文已从仓库移除，逐字备份保留在 `.tmp/ai-slop-backup/`（gitignored，不入库）。
> 各子文档只记录源文件中真实存在的事实，不补充外部知识；源文中标注「未验证 / BLOCKED / PARTIAL」的内容不被视为已验证结论。

## 目录结构（按主题分层）

| # | 主题 | 子目录 | 提炼来源 | 内容要点 |
|---|---|---|---|---|
| 01 | 评估体系（Assessment） | [01-assessment/README.md](01-assessment/README.md) | 19 份评估/审计/验收报告 | 评分可靠性、状态词（A-LIVE / B-CALIBRATION / B-RETEST）、考试冻结与 revision、数据表与迁移、真实模型验收数字 |
| 02 | 学习闭环（Learning Loop） | [02-learning-loop/README.md](02-learning-loop/README.md) | 15 份学习闭环报告 | 目标/活动/提交/项目快照数据模型、学习助手与教练的模型回放结果、遗留边界 |
| 03 | 项目自动驾驶与题库工作坊 | [03-project-autopilot/README.md](03-project-autopilot/README.md) | 23 份项目 Autopilot / 智能 / 题库工作坊文档 | 阶段链与 StageError、预算与超时控制、安全策略 v3、修复项 A-01~A-04 |
| 04 | 核心闭环（Core Loop） | [04-core-loop/README.md](04-core-loop/README.md) | 7 份报告 + 2 份验收 JSON | 证据与 UNKNOWN 规则、核心闭环数据模型、题库 v2 计划、真实模型全流程 |
| 05 | 架构 / 后端 / 数据库 / 安全 / 部署 | [05-architecture/README.md](05-architecture/README.md) | 15 份审计与实施报告 | 审计基线 0c65882、路由规模、API 兼容优先映射、四大缺口、部署方式 |

## 阅读顺序建议

1. **先看结论与遗留**：每个子 README 末尾的「结论」与「遗留 / 未验证」小节，是提炼后保留的核心信息。
2. **按主题深入**：面试复习 / 代码审计按 01→05 展开；想知道某数字（如测试通过数、耗时、tokens）的来源行，直接读对应子 README。
3. **需要原文逐字内容**：`.tmp/ai-slop-backup/` 保留了全部被移除文件的原始拷贝（含相对路径），按需翻阅。

## 保留与移除说明

- 已从仓库移除：根目录 17 个跟踪报告 + 28 个未跟踪报告、`docs/` 下 9 个未跟踪目录（acceptance / assessment / audit / core-loop / learning-loop / project-autopilot / project-intelligence / question-workshop / superpowers）、`docs/` 根 6 个散落 md、3 个已跟踪 superpowers 计划/规格（已提炼进 04 / 05）。
- 特意保留：`docs/assessment/rubric-candidates.json`（`server/tests` 的 fixture 引用，删除会破坏测试）、`docs/CURRENT_RELEASE.md`、`docs/agent/*`、`docs/architecture.html`、`docs/study-*.md` 等仍被代码或流程引用的文件。
- 仓库根目录仅保留 7 个文档：README.md / AGENTS.md / ARCHITECTURE.md / KNOWN_ISSUES.md / LOCAL_DEPLOYMENT_GUIDE.md / UI_CHANGELOG.md / QUICK_START_PERSONAL.md。
