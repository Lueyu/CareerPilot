<div align="center">

# CareerPilot

**证据驱动的 AI 工程师职业进阶平台**
Evidence-driven career growth platform for AI engineers

首个目标岗位：**AI Agent 应用开发工程师（对标 P5）**

</div>

---

## 更多文档

- [后端审计与架构](BACKEND_AUDIT.md) · [API 对照](API_GAP_REPORT.md) · [数据模型](DATABASE_GAP_REPORT.md)
- [公开内容发布设计与安全红线](PUBLIC_CONTENT_BACKEND_PLAN.md) · [部署指南](LOCAL_DEPLOYMENT_GUIDE.md)

---

## 这是什么

CareerPilot 不做"刷题工具"。它围绕一条核心闭环工作：

> **岗位对标 → 独立诊断 → 能力画像 → 差距驱动学习 → 证据沉淀 → 画像更新**

每一步都由**证据（Evidence）**支撑：你的答卷、导入的面经、项目仓库扫描、对话中提取的记忆，都会写入统一证据底座，再由能力引擎（Competency Engine）估计你在 8 个能力域上的水平与置信度。没有证据的能力不是 0 分，而是 `UNKNOWN`——不装懂。

北极星指标：**有效岗位能力差距缩小率**（加权 target-current 的下降），而不是完成题数或自评。

## 能力域模型（v1.0 种子假设）

| 能力域 | 权重 | 目标等级 |
|---|---|---|
| Agent 架构与规划 | 18% | 4.0 |
| Agent Evaluation | 16% | 4.5 |
| 工具调用与 MCP | 14% | 4.0 |
| 状态管理与 Memory | 12% | 3.5 |
| 后端与系统设计 | 12% | 3.5 |
| 可靠性与安全 | 12% | 3.5 |
| RAG / 上下文工程 | 10% | 3.5 |
| 部署与可观测性 | 6% | 3.0 |

> 权重是 3 份 JD 的种子假设，非统计结论；benchmark 版本化（v1.0），随 JD 样本滚动更新。

## 仓库结构

```
web/                  前端（React 18 + Vite + TypeScript）
  src/pages/          11 个页面：总览/岗位对标/能力画像/考试中心/学习中心/
                      面经库/项目与职业资产/记忆中心/系统观测台/进化实验室/设置
  src/api/client.ts   后端 API 封装（请求失败时报告错误）
  src/data/bank.json  161 道公开题库（从旧版题库迁移）

server/               后端（FastAPI + PostgreSQL + SQLAlchemy async）
  app/api/v1/         dashboard / benchmark / profile / exams / bank /
                      learning / memory / observatory
  app/services/       competency(能力引擎) / planner(学习规划) / scorer(评分) /
                      selection(自适应选题) / tracing(运行追踪) / llm(适配层)
  app/models/         SQLAlchemy 模型：Skill/SkillState/Evidence/Exam/Question/
                      ImportSession/MemoryItem/Trace/TraceSpan/Event/Decision...
  app/seed.py         幂等种子：能力域+161题+demo工作空间+示例证据

content/              面经题源内容（旧版静态站遗留，题库的原始来源）
deploy/               agent-interview.icu 发布脚本（OSS 静态托管链路）
```

## 快速开始

**方式 A · Docker Compose（推荐）**

```powershell
cp .env.example .env    # 按需填入 LLM / GitHub Token（均可留空，功能自动降级）
docker compose up -d --build
# 打开 http://localhost:5173 ，API 文档 http://localhost:8000/api/v1/docs
```

**方式 B · 本地裸跑（开发）**

```bash
# 1) 数据库（一次性, 本地 PostgreSQL 或只起一个 DB 容器）
createuser careerpilot --createdb 2>/dev/null || true
psql -c "ALTER USER careerpilot PASSWORD 'careerpilot_dev';"
createdb careerpilot -O careerpilot

# 2) 后端
cd server
pip install -r requirements.txt
python migrate.py           # 幂等 SQL 迁移（可重复执行）
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
#    首次启动自动 create_all + 种子（8 能力域 / 题库映射 / demo 工作空间）

# 3) 前端
cd web
npm install
npm run dev                 # http://localhost:5173
```

后端不可用时，依赖 API 的页面会显示错误或数据不可用状态。启动后端后刷新页面。演示工作空间需要手动切换。

### 环境变量（.env，均可省略）

```
DATABASE_URL=postgresql+asyncpg://careerpilot:careerpilot_dev@localhost:5432/careerpilot
API_TOKEN=cp-dev-token
LLM_PROVIDER=          # 空 = 确定性规则引擎（不依赖外部 LLM）
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=
GITHUB_TOKEN=          # 空 = 公开内容发布功能停用（页面诚实标注）
```

数据备份/恢复（`deploy/backup.ps1` · `restore.ps1`）、公开门户构建与完整部署说明见
**[LOCAL_DEPLOYMENT_GUIDE.md](LOCAL_DEPLOYMENT_GUIDE.md)**。

## 界面主题

深色（护眼）主题为默认，右上角 ☀️/🌙 一键切换，偏好存于 `localStorage`（`cp-theme`）。
深色配色完全由 `web/src/styles/tokens.css` 的 CSS 变量驱动，浅色主题保持 Figma 设计稿原样。

## 核心机制

- **考试状态机**：`CREATED → ACTIVE → SUBMITTED → TRANSCRIPT_CONFIRMED → FROZEN → SCORING → COMPLETED`，答卷冻结后评分，报告为不可变快照。
- **按要点覆盖度评分**：开放题按 rubric 要点关键词命中率给分（CORRECT / PARTIAL / WRONG），每个要点命中与否在报告中逐条可见。
- **自适应选题**：诊断考试优先选「差距大 × 权重高 × 证据少」的能力域抽题，选题理由（selection_reason）随卷返回。
- **面经导入流水线**：Upload → 解析/提取 → 公司/职级/日期 → 技能映射 → **查重**（重复问法合并为 canonical 的 variants）→ 人工审核 → 入库（`USER_PRIVATE` 私有，不进公共题库）。
- **记忆中心**：对话文本按规则提取候选记忆（CareerGoal / Preference / KnowledgeGapSignal / ProjectDecision / Feedback），确认后驱动学习规划；纠正走 SUPERSEDED 链。
- **系统观测台**：每次引擎运行写 Trace + TraceSpan（含耗时/成本/失败原因），决策记录（DecisionRecord）说明"为什么选这个行动"，证据源（Evidence）可溯源。
- **多租户就位**：Workspace 隔离已建模，MVP 单工作空间演示。

## 演示模式声明

`demo` 工作空间内的种子数据（示例证据 / 记忆 / trace）全部显式标记 `[DEMO]`；页面顶部的 DEMO 横幅常驻。**演示数据不是真实能力评估或行业统计。**

## 路线图

- [x] 11 页前端（Figma 设计稿 1:1 实现）
- [x] 后端核心 API + 规则评分引擎（rule-v1）
- [x] 面经导入流水线（文本解析 + 查重 + 审核）
- [x] 记忆中心（规则提取 + 确认/纠正/忽略）
- [ ] LLM 评分与提取（LLM_PROVIDER 接入后自动升级）
- [ ] 项目与职业资产（仓库扫描 / 证据提取 / 职业展示自动更新）
- [ ] 进化实验室（策略 A/B 评估与批准）
- [ ] 部署链路（OSS 静态前端 + API 服务上线 agent-interview.icu）

## License

MIT
