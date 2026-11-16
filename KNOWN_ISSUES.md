# 已知问题与边界（Known Issues）

> 版本：v3.2 · 2026-11-16 · 诚实记录，不隐瞒
> 分两类：**A 刻意取舍**（local-first 单机版的设计决定）/**B 待改进**（后续版本可做）。

## A. 刻意取舍（本版本不修）

| # | 问题 | 理由 |
|---|---|---|
| A1 | 无 alembic：用 `create_all + 幂等 SQL 迁移器` 替代 | 单机本地版，迁移需求简单；migrate.py 可重复执行零副作用 |
| A2 | 单用户体系：无登录/会话/密码，API Token 为静态共享令牌 | local-first 威胁模型下本机用户可信（SECURITY_REPORT） |
| A3 | 无 skill_dependencies 依赖图表 | 种子标准未使用依赖图；加表无消费方 |
| A4 | 无 conversation_sources/messages 独立表 | 对话文本最小版直存记忆来源字段，避免过度建模 |
| A5 | 公开草稿不支持图片/附件上传 | JSONB meta 可承载 URL；公开站纯文本先跑通 |
| A6 | 无诊断式动态插题 | 以「报告内追问建议 + 复测链（1/3/7 天）」实现任务书语义，不打断答卷流 |
| A7 | 成本为估算（¥2/M tokens 常量） | 无计费 API 对账；口径在 OBSERVABILITY_REPORT 声明 |
| A8 | TraceSpan 无独立 created_at | 以 trace.started_at + duration 推算，单机足够 |

## B. 待改进（低频影响，记录在案）

| # | 问题 | 现象 | 建议 |
|---|---|---|---|
| B1 | 选题去重为会话内而非全局 | 连续多次诊断可能重复遇到同题 | 增加最近 N 次考试题排除集 |
| B2 | 复测任务依赖 plan_tasks 幂等键 | 手动删任务后 1/3/7 链可能不再生成本轮后续 | 删除任务时保留 retest 链头 |
| B3 | JD 解析为关键词规则 | 生僻表述可能漏命中 | 已预留 LLM 增强（llm.py 可直接挂） |
| B4 | 构建产物 >500kB 警告（bank.json 内联） | 首屏加载稍慢 | 动态 import 或拆分 chunk |
| B5 | 考试题目来源仅本地题库 | 无外部题源接入 | 留 API 兼容（question source_type 可扩展） |
| B6 | web/nginx 不代理 /api | 浏览器直连 `127.0.0.1:8000`；跨机访问需改 VITE_API_BASE 构建 | 提供 nginx proxy 示例即可支持局域网访问 |
| B7 | `exam 17` 等测试过程数据保留 | DB 内有少量测试生成的考试/trace | 不影响功能；`GET /settings/export` 可导出查看，或删库重 seed |
| B8 | 无 OpenTelemetry/结构化日志导出 | 单机 stdout + DB Trace 已足够 | 多实例化时再接 OTel |

## B9. 中国网络环境备注（部署实操）

- Docker 拉镜像偶发 `wsarecv: connection forcibly closed`（auth.docker.io 抖动）→ 重试即可；`server/Dockerfile` 已内置 apt/pip 国内镜像源 + 自动回落。
- 首次 `docker compose build` 若 apt 报 exit 100，重跑 build 通常成功。

## 复现环境

Windows 11 + Docker Desktop 29.9.2 + PostgreSQL 16；Node 24.16（本地 dev 用）；Python 3.12。
