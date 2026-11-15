---
title: RAG 召回率差怎么排查
slug: rag-recall-debug
description: 从「检索不到」到「排不上来」：一条五步排查路线，Recall@k 和 MRR 各管一段。
category: guides
tags: [rag, retrieval, evaluation]
level: intermediate
estimated_reading_minutes: 12
published_at: 2026-11-16
updated_at: 2026-11-16
status: published
source_type: original
sources: []
author: CareerPilot
---

## 一句话结论

RAG 回答差先别怪大模型——**先测检索**：Recall@k 低是「没召回」（改分块/索引），MRR 低是「召回但排序差」（加重排）；两个指标定位两类完全不同的病。

## 生活类比

Recall@k 像考试「划的重点覆盖了多少」——重点没划全，答得再漂亮也丢分；MRR 像「正确答案排第几」——答案在材料里，但排在第 10 条，模型只看前 3 条，等于没找到。

## 具体例子

知识库 100 条资料，某问题相关的有 5 条。召回 top-10 里命中 4 条 → Recall@10 = 80%；命中的那条排第 3 → MRR = 1/3 ≈ 0.33。诊断：覆盖还行，排序拖后腿 → 优先加重排（reranker），而不是继续改 embedding。

## 原理与术语

- **Recall@k**：top-k 结果里命中的相关文档数 ÷ 全部相关文档数。管「找没找到」。
- **MRR（Mean Reciprocal Rank）**：第一条相关结果排名倒数的均值。管「排得好不好」。
- **分块（Chunking）**：文档切成多大送进索引。太大稀释语义，太小丢上下文；常见 256-512 token + 重叠。
- **混合检索**：向量（语义）+ BM25（关键词）并联，再融合（RRF）。专治「术语精确匹配」场景。
- **重排（Rerank）**：用交叉编码器对 top-50 精排，取 top-5 进上下文。性价比最高的单点优化。

## 常见误区

- **误区一：不看指标直接换 embedding 模型。** 换完好了还是坏了？没人知道。
- **误区二：分块大小一刀切。** 表格、代码、FAQ 应该用不同策略。
- **误区三：只测端到端回答质量。** 回答差可能是检索差也可能是生成差，不拆开永远在猜。
- **误区四：评测集只有简单问题。** 多跳问题、同义改写、长尾术语才是拉开差距的地方。

## 实践步骤

1. 建检索评测集：50-100 条「问题 → 相关文档 id 列表」（从真实 query 日志和失败 case 里挑）。
2. 跑基线：记录 Recall@5/@10 和 MRR，这是你所有优化的对照面。
3. Recall 低 → 检查分块（命中段落被切断？）、加混合检索（BM25 补精确匹配）、扩同义改写。
4. MRR 低 → 加 reranker（对 top-50 精排）、query 改写（把口语 query 扩成检索友好的表述）。
5. 每次改动跑全量评测集，回退变差的改动。

```python
def evaluate(eval_set, retriever):
    recalls, mrrs = [], []
    for q in eval_set:                       # {"query":..., "relevant":[ids]}
        hits = retriever.top_k(q["query"], k=10)
        hit_ranks = [i + 1 for i, d in enumerate(hits) if d.id in q["relevant"]]
        recalls.append(len(hit_ranks) / len(q["relevant"]))
        mrrs.append(1 / hit_ranks[0] if hit_ranks else 0)
    return {"recall@10": mean(recalls), "mrr": mean(mrrs)}
```

## 自测问题

- Recall@10 = 95% 但 MRR = 0.2，下一步优化什么？
- 为什么 BM25 和向量检索要并联而不是二选一？
- 分块重叠（overlap）解决什么问题？
- 你的检索评测集应该从哪里收集样本？

## 延伸阅读

- [Agent 评估入门](/guides/agent-evaluation/)：检索指标是分层评估的第一层。
- [幻觉治理](/guides/hallucination-control/)：检索不到时模型会怎么编。
