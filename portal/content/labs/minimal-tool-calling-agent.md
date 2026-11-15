---
title: Lab 1 · 搭建最小 Tool-Calling Agent
slug: minimal-tool-calling-agent
description: 100 行代码跑通 ReAct 循环：工具注册、超时重试、显式状态，跑 100 次不出崩溃。
category: labs
tags: [tool-calling, react, hands-on]
level: beginner
estimated_reading_minutes: 20
published_at: 2026-11-16
updated_at: 2026-11-16
status: published
source_type: original
sources: []
author: CareerPilot
---

## 你将做出什么

一个能查天气、算数、读文件的命令行 Agent：输入「查北京上海成都的天气，然后告诉我哪个最适合跑步」，它自主分解任务、按序调用工具、汇总回答。麻雀虽小，ReAct 的骨架全有。

## 前置要求

- Python 3.10+，一个大模型 API Key（任何支持 function calling 的都行）。
- 读过 [工具调用失败重试](/guides/tool-retry-idempotency/)（本实验直接使用其中的重试包装器）。

## 步骤 1：工具注册表

工具 = schema + 实现。schema 给模型看，实现给运行时用：

```python
TOOLS = [{
    "name": "get_weather",
    "description": "查询城市当前天气",
    "parameters": {"city": {"type": "string", "description": "城市名"}},
}, {
    "name": "calc",
    "description": "四则运算",
    "parameters": {"expr": {"type": "string"}},
}]

def get_weather(city):
    return {"city": city, "temp_c": 18, "condition": "多云"}  # 先用假数据

def calc(expr):
    return eval(expr, {"__builtins__": {}}, {})  # 仅实验用
```

## 步骤 2：ReAct 主循环（带显式状态）

```python
def run_agent(goal, max_steps=8):
    state = {"goal": goal, "step": 0, "findings": []}   # 显式状态
    messages = [{"role": "user", "content": goal}]
    for i in range(max_steps):
        resp = llm(messages, tools=TOOLS)                # 带超时重试
        state["step"] = i + 1
        if resp.tool_calls:
            for call in resp.tool_calls:
                result = call_with_retry(TOOLS_IMPL[call.name], call.args)
                state["findings"].append({call.name: result})
                messages.append(tool_message(call.id, result))
        else:
            return resp.content, state                   # 最终回答
    return "达到步数上限", state
```

## 步骤 3：加上限与日志

- 单工具超时 5s、总预算 30s（直接抄指南里的 `call_with_retry`）。
- 每步打印 `{step, tool, args, result, elapsed}`——这就是你第一个 trace。

## 验收标准

1. 「查三城市天气并推荐」任务 20 次运行成功率 ≥ 95%（假数据下失败只该来自模型输出格式）。
2. 把 `get_weather` 改成 50% 概率超时，验证重试生效且不重复副作用（本实验天气是读操作；想象它是下单，你就要加幂等键了）。
3. 把 max_steps 设为 2，观察 Agent 如何在受限步数内调整策略（或明确放弃）。

## 思考题

- 为什么状态要显式存 `findings` 而不是只靠 messages 历史回放？
- 如果工具返回超大文本（10k token），你要在哪一层截断？
- 多工具并发调用（一步调 3 个城市）怎么改？

## 延伸阅读

- [Lab 2 · 给 Agent 加评估](/labs/agent-eval-harness/)：给这个 Agent 建评测集。
- [记忆压缩](/guides/memory-compaction/)：对话变长之后的下一步。
