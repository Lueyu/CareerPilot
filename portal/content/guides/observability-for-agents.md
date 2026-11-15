---
title: Agent 的部署与可观测性
slug: observability-for-agents
description: 灰度、回滚、trace、成本核算——上线之后你才真正开始认识你的系统。
category: guides
tags: [deployment, observability, cost]
level: intermediate
estimated_reading_minutes: 11
published_at: 2026-11-16
updated_at: 2026-11-16
status: published
source_type: original
sources: []
author: CareerPilot
---

## 一句话结论

Agent 上线三件套：**全链路 trace（每次执行可回放）、成本核算（每次调用可计价）、灰度回滚（每次改动可撤退）**；没有这三样，你只是在用户环境里做不可复现的实验。

## 生活类比

像开一家餐厅：后厨每道菜谁做的、用了什么料（trace）、每道菜成本多少（成本核算）、新菜先小范围试卖不好就下架（灰度回滚）——没有这些，亏了都不知道亏在哪道菜。

## 具体例子

新版本 Agent 灰度 10% 流量：任务完成率持平，但平均成本 +40%。展开 trace 发现：新 prompt 让模型多调了 2 次「自我检查」工具，每次检查又各跑一轮检索——**不看 span 明细，你只会看到「成本涨了」而永远找不到「谁涨的」**。

## 原理与术语

- **Trace / Span**：一次用户请求 = 一条 trace；内部每步（LLM 调用、检索、工具）= 一个 span，记录状态/耗时/成本。
- **关键指标**：任务完成率（业务）、P95 延迟（体验）、单位成本（钱）、失败率（稳定性）——四个一起看，缺一不可。
- **灰度发布**：新版本先放 5-10% 流量，观察核心指标无回归再逐步放大。
- **回滚（Rollback）**：指标劣化 → 一键回上一版本；回滚是正常操作，不是事故。
- **决策记录**：Agent 选了动作 A 而不是 B，为什么？决策 + 候选 + 证据落库，事后可审计。

## 常见误区

- **误区一：只记最终成败。** 中间哪步开始错的？不知道。span 级记录才是排查入口。
- **误区二：成本月底才算账。** 等月底账单爆炸，问题已经跑了四周。按 trace 实时累计。
- **误区三：灰度看一天就全量。** 长尾场景（低频工具、复杂多步任务）一周后才暴露。
- **误区四：告警只看错误率。** 错误率没动但成本翻倍，也是事故。

## 实践步骤

1. 每次执行生成唯一 trace_id，贯穿 LLM 调用、检索、工具执行，span 记 `{名称, 状态, 耗时, 成本}`。
2. 指标看板四件套：完成率 / P95 延迟 / 单位成本 / 失败率，按版本分维度。
3. 发布流程固化为：灰度 10% → 观察 48h → 指标无回归 → 全量；回归 → 自动回滚。
4. 决策落库：Agent 的每次重要选择（选题策略、路由、工具选择）记录候选与依据。
5. 失败可重放：输入+状态快照落库，失败 trace 一键本地复现。

```python
with trace("import_interview") as tr:
    with tr.span("parse_file") as s:
        text = extract(file); s.set(cost=0, dur=...)
    with tr.span("llm_extract") as s:
        questions = llm(...); s.set(cost=usage.cny)
    tr.set(status="SUCCESS", cost=sum(sp.cost for sp in tr.spans))
```

## 自测问题

- 成本上涨 40%，你的排查路径是什么？先看哪张表？
- 为什么灰度观察期至少要覆盖一个完整使用周期？
- 决策记录解决什么问题？和 trace 的区别？
- 哪些指标劣化应该触发自动回滚？

## 延伸阅读

- [Agent 评估入门](/guides/agent-evaluation/)：离线评测是灰度判断的标尺。
- [工具调用失败重试](/guides/tool-retry-idempotency/)：span 里的重试日志怎么用。
