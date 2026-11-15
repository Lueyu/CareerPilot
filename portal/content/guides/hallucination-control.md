---
title: 幻觉治理：让 Agent 敢说「我不知道」
slug: hallucination-control
description: 证据约束、引用校验、拒答机制——幻觉不能消灭，但可以被工程化地压到可接受。
category: guides
tags: [reliability, hallucination, safety]
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

幻觉无法靠 prompt「求你别编」解决；可行的是**证据约束（有据才答）+ 引用校验（答案必须指向证据）+ 拒答机制（允许说不知道）+ 回归测试（新幻觉不过夜）**。

## 生活类比

一个不靠谱的实习生：不懂也硬答，还编得像模像样。治理方法不是骂他「认真点」，而是改规矩：**回答必须附出处，说不出出处就说不知道**——出处制度一立，硬答自然收敛。

## 具体例子

用户问「贵司产品支持 MongoDB 吗」：

- 无约束：模型自信地列出支持的版本矩阵（编的）。
- 有约束：检索知识库 → 无命中 → 触发拒答模板「文档中未找到该信息，已转人工确认」→ 同时记录这条 query 进「知识盲区」列表，下周补文档。

## 原理与术语

- **证据约束（Grounding）**：答案只能由检索到的证据支撑；prompt 里明确「仅使用以下材料，材料中没有就说不知道」。
- **引用校验（Citation Check）**：要求每句关键论断带 [来源 id]，后处理校验 id 是否真实存在且语义相关。
- **拒答（Abstention）**：检索置信度低于阈值或证据互相矛盾时，输出「不知道/转人工」，而不是猜。
- **置信度路由**：低置信问题走保守路径（拒答/人工），高置信走自动回答——用分层策略换整体可信度。
- **知识盲区清单**：拒答的 query 是产品金矿，直接告诉你该补什么文档。

## 常见误区

- **误区一：把幻觉当模型问题等下一代。** 工程约束现在就能压一个数量级。
- **误区二：只在前台加免责声明。** 免责声明不减少幻觉，只减少诉讼。
- **误区三：拒答率越高越好。** 拒答太多等于产品不可用；要的是「该拒的拒，该答的答」。
- **误区四：幻觉 case 不沉淀。** 每个线上幻觉都该进评测集，否则同一个坑反复踩。

## 实践步骤

1. 回答前先检索：无证据命中的问题走拒答模板，别让模型自由发挥。
2. 输出强制带引用：每个关键论断标 [doc_id]，后处理校验引用真实性。
3. 设置拒答阈值：检索 top-1 相似度 < 阈值 → 拒答；阈值用评测集调，不是拍脑袋。
4. 幻觉登记簿：线上发现的每条幻觉 → 记录 → 加进评测集 → 下次回归必测。
5. 周期性抽检：按比例人工复查自动回答，估计真实幻觉率。

```python
def answer(query):
    docs = retriever.top_k(query, k=3)
    if docs[0].score < ABSTAIN_THRESHOLD:
        log_blind_spot(query)                 # 知识盲区
        return REFUSAL_TEMPLATE
    draft = llm(prompt=EVIDENCE_ONLY, context=docs, query=query)
    return verify_citations(draft, docs)      # 引用不实 → 重生成或拒答
```

## 自测问题

- 拒答阈值设太高和太低分别有什么代价？
- 引用校验为什么要在后处理做，而不是只靠 prompt 要求？
- 线上发现一条幻觉，完整的处理流程是什么？
- 幻觉率应该怎么测才不是自欺欺人？

## 延伸阅读

- [RAG 召回排查](/guides/rag-recall-debug/)：拒答的一半原因是检索没命中。
- [Agent 评估入门](/guides/agent-evaluation/)：幻觉率是评测集的核心指标之一。
