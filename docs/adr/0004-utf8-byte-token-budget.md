# ADR 0004: 用 UTF-8 字节估算 token 预算

- 状态: `ACCEPTED`（记录既有实现）
- 日期: 2026-11-27
- 关联: `KNOWN_ISSUES.md` C1 / D6；代码 `../../server/app/services/public_model.py`（输入上限计算）、`../../server/app/services/project_autopilot_engine.py`（自报估算口径）、`../../server/app/services/study_chat.py`
- 决策者: Lueyu

## 背景

上下文工程需要在发送前裁剪输入，避免超出模型窗口。但本项目按 ADR 0003 不引入
厂商 SDK，也就没有配套 tokenizer；同时要支持运行时切换多种模型，不可能为每种模型
内置精确计数。`public_model.py` 的做法是对消息内容按 UTF-8 字节累加并加上固定余量：
`input_ceiling = byte_count + 64 * len(clean) + 256`（容纳 chat template 开销）。

## 决定

- **预算按 UTF-8 字节数估算**，不引入 `tiktoken` 或任何 tokenizer。
- **估算口径必须自报**：产物里标注 `conservative_utf8_bytes_not_actual_tokens`
  （`project_autopilot_engine.py`），明确这是字节估算而非 token 精确计数，
  不把估算伪装成精确值。
- **保留固定余量**：`byte_count + 64 * 每条消息 + 256` 覆盖模板开销；
  单条内容还受 `_MAX_INPUT_BYTES` 硬上限约束。
- 预算裁剪与上限校验是**确定性节点**，不由 LLM 决定。

## 备选方案

| 方案 | 放弃理由 |
|---|---|
| `tiktoken` | 只覆盖 OpenAI 系；多厂商需要适配表，与「无 SDK / 轻依赖」和第 0 波范围冲突 |
| 各 provider tokenizer 适配表 | 需要维护模型→tokenizer 映射与版本，属于 C1 的目标方案而非当前状态 |
| 按字符数估算 | 对中文与 ASCII 混合文本的偏差比字节数更大，且无法表达多字节开销 |

## 后果

**收益**：零新依赖、跨模型统一、计算可复现；上限与余量都是可查证的常量，
不依赖外部服务；估算口径自报让下游不会误读精度。

**代价与已知短板**：`KNOWN_ISSUES.md` C1 已明确指出「保守上界」并不严格成立——

- 中文高估（3 字节 ≈ 1–2 token），白白丢弃可用上下文；
- 英文/罕见符号串可能低估（BPE 对符号串会膨胀），导致 provider 返回
  `400 context_length_exceeded`。

因此本文记录的是**当前实现**，不是「该方案足够好」的结论。

## 复核触发条件

- C1 引入 `tiktoken` + provider tokenizer 适配表，未知模型保留字节估算但加安全系数；
- D6 落地「识别 400 溢出 → 精确计数重拟合 → 降档重试一次」路径与 MockTransport 演练用例。

任一落地后，必须更新本文的估算口径描述，或新建 ADR 把本文标为 `SUPERSEDED`。
