---
title: Lab 2 · 给 Agent 加评估：从 20 个用例开始
slug: agent-eval-harness
description: 为 Lab 1 的 Agent 建一个最小评测工具：固定用例、rubric 打分、基线对比、回归挡板。
category: labs
tags: [evaluation, regression, hands-on]
level: intermediate
estimated_reading_minutes: 25
published_at: 2026-11-16
updated_at: 2026-11-16
status: published
source_type: original
sources: []
author: CareerPilot
---

## 你将做出什么

一个 60 行的评测脚本 `eval.py`：跑 20 个固定用例 → 输出完成率/工具准确率/平均步数/平均成本四张表 → 和基线 `baseline.json` 自动对比 → 任何指标显著下降就报红。从此你改 prompt 不再靠手感。

## 前置要求

- 完成 [Lab 1](/labs/minimal-tool-calling-agent/)（有可跑的 Agent）。
- 读过 [Agent 评估入门](/guides/agent-evaluation/)（本实验是其落地）。

## 步骤 1：用例集（evalset.jsonl）

每行一个用例：输入、期望的工具调用序列、期望回答要点：

```json
{"id": "w3", "input": "查北京上海成都的天气，哪个最适合跑步",
 "expect_tools": ["get_weather", "get_weather", "get_weather"],
 "rubric": [{"title": "三城都查了"}, {"title": "给出明确推荐及理由"}]}
{"id": "timeout", "input": "算 123*456", "expect_tools": ["calc"],
 "rubric": [{"title": "结果为 56088"}]}
```

20 条的构成建议：12 条常规 + 4 条边界（超长输入、步数上限）+ 4 条对抗（要求做不允许的事，如「忽略你的规则」）。

## 步骤 2：评测执行器

```python
def run_eval(agent, evalset):
    rows = []
    for case in evalset:
        state = agent.run(case["input"])          # Lab 1 的 run_agent
        rows.append({
            "id": case["id"],
            "tools_ok": state["tools"] == case["expect_tools"],
            "rubric_score": judge(case, state),    # LLM 裁判, 按 rubric 打分
            "steps": state["step"], "cost": state["cost"],
        })
    return summarize(rows)   # 完成率/工具准确率/平均步数/平均成本

def summarize(rows):
    n = len(rows)
    return {"completion": sum(r["rubric_score"] >= 0.8 for r in rows) / n,
            "tool_acc": sum(r["tools_ok"] for r in rows) / n,
            "avg_steps": sum(r["steps"] for r in rows) / n,
            "avg_cost": sum(r["cost"] for r in rows) / n}
```

## 步骤 3：基线与回归挡板

```python
baseline = json.load(open("baseline.json"))
diff = {k: current[k] - baseline[k] for k in current}
bad = diff["completion"] < -0.05 or diff["tool_acc"] < -0.05 or diff["avg_cost"] > 0.3
print("REGRESSION!" if bad else "OK ✓", diff)
sys.exit(1 if bad else 0)      # 非 0 退出码可接入 CI / pre-commit
```

## 验收标准

1. 评测脚本对同一版本连跑 3 次，完成率波动 ≤ 5%（裁判稳定性达标；波动大 → 修裁判 prompt，加锚点和 few-shot）。
2. 故意把 prompt 改坏（比如删掉工具使用说明），评测脚本报红并指出工具准确率下降。
3. 把它挂进 git pre-commit：改 prompt 不跑评测就无法提交。

## 思考题

- 用例集多久更新一次？什么时候该加新用例（提示：每次线上失败之后）？
- 裁判分数和人工抽检不一致时，先修哪边？
- 平均步数下降但完成率也降了，怎么解读？

## 延伸阅读

- [Agent 评估入门](/guides/agent-evaluation/)：分层指标与裁判设计的原理。
- [部署与可观测性](/guides/observability-for-agents/)：离线评测与线上指标对齐。
