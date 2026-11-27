# ADR 0003: 不引入 LLM 厂商 SDK / 可观测性 SDK

- 状态: `ACCEPTED`（记录既有实现）
- 日期: 2026-11-27
- 关联: `KNOWN_ISSUES.md` B7 / G2 / G1；代码 `../../server/app/services/llm.py`（文件头）、`../../server/app/services/public_model.py`
- 决策者: Lueyu

## 背景

系统需要多厂商 LLM 兼容、可降级、可观测，但不希望被厂商 SDK 的重试策略、隐式计费、
版本行为或采集侧依赖绑定。`llm.py` 文件头把出口定义为「OpenAI 兼容 `/chat/completions`
（httpx）」，配置运行时动态解析（DB `AppSetting(key='llm')` 优先、env 兜底），
并把规则引擎降级写进 PRD。指标侧同样自建 `AgentMetric`/`Trace` 落 PostgreSQL（G2）。

## 决定

- **LLM 出口用 `httpx` 直连 OpenAI 兼容 `/chat/completions`，不引入 `openai` / 厂商 SDK**
  （`llm.py:1-11`）。厂商差异由本项目的适配层显式处理，不由 SDK 隐式吞掉。
- **指标与追踪自建，落 PostgreSQL**，不引入 OTel / Prometheus SDK（G2）。
- **结构化日志用 `structlog` 的 JSON renderer**（0-B / G1 的观测性改造），
  但采集侧仍不引入 SDK：stdout + 按大小轮转的文件由容器/运维侧采集。
- 依赖面变化必须走 `server/requirements.txt` 与独立 PR，不因「方便」顺手加 SDK。

## 备选方案

| 方案 | 放弃理由 |
|---|---|
| 各厂商官方 SDK | 隐式重试/超时/计费行为不可控；多厂商要维护多套 SDK 语义；与规则降级路径耦合 |
| LangChain 等编排框架 | 引入大量间接层，掩盖本项目刻意保留的「确定性 workflow vs LLM 节点」边界 |
| OTel SDK + 外部时序库 | 需要额外服务与运维面；第 0 波先把日志与关联 ID 打通，导出是后续独立决策（G2） |

## 后果

**收益**：依赖面小、行为可预测、降级路径明确；请求级关联 ID（G1）与自建指标口径
可以自己定义，不受 SDK 版本升级影响；面试可完整讲出自建网关的设计取舍。

**代价与已知短板**：

- 厂商私有字段必须自己适配，`KNOWN_ISSUES.md` B7 已记录由此产生的问题：
  `thinking` 字段硬塞进通用 payload、`reasoning_content` 只认一种、finish 后 usage 尾帧
  过严、流式 `tool_calls` 分片组装未实现、`httpx.AsyncClient` 每次新建无连接池复用。
  这些是「无 SDK」决策的直接成本，不是疏忽。
- 无 `/metrics` 端点、无标准化导出、无 API 层 HTTP 延迟/错误率直方图（G2）。

## 复核触发条件

- B7 落地 Provider Dialect 适配层与流式 `tool_calls` 组装后，更新本文对协议层的描述；
- G2 明确决定引入 `prometheus_client` / OTel 导出时，新建 ADR 取代本文的「不引入 SDK」结论。
