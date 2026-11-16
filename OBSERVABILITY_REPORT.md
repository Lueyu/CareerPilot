# 可观测性报告

> 版本：v3.2 · 2026-11-16
> 实现：Trace + Span + DecisionRecord + Event 四层，全部落 PostgreSQL（单机无需外部 APM）。

## 数据模型

| 实体 | 说明 | 关键字段 |
|---|---|---|
| `traces` | 一次业务运行（导入/评分/诊断/发布/扫描） | short_code TR-xxxx（可自动生成），kind，status RUNNING/SUCCESS/FAILED，duration_ms（可省略自动计算），cost_cny，fail_reason，trigger/trigger_ref（触发来源） |
| `trace_spans` | 运行内步骤 | name（llm:* 为 Agent Run），status OK/ERROR，duration_ms，cost_cny，detail JSONB |
| `decision_records` | 决策解释 | short_code DC-xxxx，question/chosen/alternatives[{option,reason}]/evidence_refs/rationale |
| `events` | append-only 事件流 | kind（exam.completed/project_scanned/benchmark_updated…），payload JSONB，ref_type/ref_id |

## Agent Run 观测（P0-7）

LLM 调用统一经 `llm.py chat_with(..., trace, session, span_name)`：每次调用自动写 span `llm:{model}`，detail = `{agent:true, provider, model, total_tokens, prompt_tokens, completion_tokens, retry, ok, cost_note:"estimate ¥2/M tokens"}`；异常也记录（status=ERROR）且不影响主流程。

`GET /api/v1/observatory/agents` 聚合最近 500 条 llm:* span → 按 model 分组 `{model, provider, calls, errors, avg_ms, total_tokens, estimated_cost, last_used}`（实测：deepseek-v4-flash · gateway · 2 calls · 7851 tokens · est ¥0.0157）。

## 已覆盖的 Trace 点（真实埋点）

| kind | 触发 | spans |
|---|---|---|
| bank_import | 面经导入（后台任务） | 解析文档/提取实体/技能映射/去重/生成草稿 |
| diagnosis_scoring | 考试提交评分 | 逐题评分（judge 失败→trace FAILED） |
| diagnosis_deep | AI 深度诊断 | llm:deep_diagnosis(+_fix 重试) |
| project_scan | 仓库扫描 | 拉取/分析 |
| asset_update / benchmark_jd | 资产更新/JD 对标 | 校验/生成 |
| content_publish | 公开内容发布（含 dry-run） | 校验/PR/构建/发布，失败记 fail_reason |
| teaching/help | 学习内容生成与求助 | llm 调用 |

## 前端呈现

「系统观测台」页：通俗摘要卡（最近发生了什么/成本/失败原因）+「高级数据」开关（Trace 列表/决策记录/证据源/Agent 统计）+引擎健康卡。失败 Trace 带红色状态与 fail_reason（如 git diff timeout）。

## 已知边界

- TraceSpan 无独立 created_at（沿用 trace.started_at + duration 推算）；无 OpenTelemetry 导出（单机自足）；成本为估算口径（¥2/M tokens，可按实际计费调整常量）；日志为 stdout（容器可 docker logs 采集），未做结构化日志。
