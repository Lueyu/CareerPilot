---
title: 工具调用失败重试的工程实践
slug: tool-retry-idempotency
description: 重试、退避、抖动、幂等键——让 Agent 的工具调用从「碰运气」变成「可预期」。
category: guides
tags: [tool-calling, reliability]
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

工具调用的可靠性 = **分类错误 + 指数退避重试 + 幂等键**，三者缺一不可；只加重试不加幂等，等于把一次错误放大成多次扣款。

## 生活类比

给外卖小哥打电话占线：等 2 秒再打，还占线等 4 秒，再占线等 8 秒——最多打 3 次，不行就换人（转人工）。而**取餐号**就是幂等键：同一单不管你问几次，都指向同一份餐，不会因为你多问两遍就多做两份。

## 具体例子

支付工具偶发超时：

- **不带幂等键**：调用超时 → 重试 3 次 → 服务端实际都收到了 → 用户被扣 3 次钱。
- **带幂等键** `pay_20261116_001`：第 2、3 次重试时服务端识别「同一单」，直接返回首次的结果，只扣 1 次钱。

## 原理与术语

- **错误分类**：超时 / 限流（429/503）→ 可重试；参数错误（400）/ 鉴权失败（401）→ 不重试，直接失败或转人工；未知错误 → 保守处理，最多重试 1 次。
- **指数退避（Exponential Backoff）**：`delay = base × 2^n + jitter`，base 取 1-2s，jitter 是随机抖动，防止所有客户端同步重试打垮服务端（惊群）。
- **重试预算**：单次调用超时 5s，总预算 30s——预算烧完就停，别让用户等 2 分钟。
- **幂等键（Idempotency Key）**：客户端生成的唯一请求标识；服务端见到重复的键，返回缓存的结果而不是再执行一遍。
- **重试风暴**：上游故障时下游全部重试，流量放大 N 倍把上游彻底压死——退避 + 抖动 + 熔断是解药。

## 常见误区

- **误区一：所有失败都重试。** 400 参数错误重试 100 次还是 400，只会浪费钱和时间。
- **误区二：重试不用幂等。** 对写操作（支付、下单、发消息）这是事故级错误。
- **误区三：固定间隔重试。** 没有抖动的重试在并发场景会形成同步波。
- **误区四：重试次数不设上限。** 一次卡死的调用会拖垮整个任务预算。
- **误区五：重试不打日志。** 排查「为什么这笔订单慢」时，你需要知道重试了几次、每次多久。

## 实践步骤

1. 把工具调用的错误统一捕获，按状态码/错误类型分成「可重试 / 不可重试」两桶。
2. 实现通用重试包装器：指数退避 + 抖动，最多 3 次，总预算 30s。
3. 给所有**写操作**工具加幂等键参数；服务端没有幂等支持时，用本地「请求-结果」表先查后发。
4. 每次重试写结构化日志：`{trace_id, attempt, delay_ms, error}`。
5. 压测：模拟 50% 失败率，验证最终成功率和总耗时符合预期。

```python
async def call_with_retry(tool, payload, *, max_retries=3, budget_s=30):
    key = make_idempotency_key(payload)   # 写操作必带
    deadline = now() + budget_s
    for attempt in range(max_retries + 1):
        try:
            return await tool(payload, idem_key=key, timeout=5)
        except RetryableError as e:        # 超时/429/503
            if attempt == max_retries or now() > deadline:
                raise
            delay = min(16, 2 ** attempt) + random.uniform(0, 1)  # 退避+抖动
            log(attempt, delay, e)
            await sleep(delay)
        except FatalError:                 # 400/401 → 不重试
            raise
```

## 自测问题

- 为什么抖动（jitter）是必须的？没有它会出什么问题？
- 哪些 HTTP 状态码应该重试，哪些不应该？
- 服务端不支持幂等，你如何在客户端降低重复执行的风险？
- 单次调用超时和总预算是什么关系？

## 延伸阅读

- [Agent 评估入门](/guides/agent-evaluation/)：重试逻辑也要进评测集。
- [部署与可观测性](/guides/observability-for-agents/)：重试日志怎么变成告警。
