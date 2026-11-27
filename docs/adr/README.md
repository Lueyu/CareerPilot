# 架构决策记录（ADR）

本目录沉淀 CareerPilot 的「为什么这样设计」，与 `KNOWN_ISSUES.md` 的「还差什么」互补。

- **事实源分工**：`../../KNOWN_ISSUES.md` 是短板清单的唯一事实源；本目录只记录**已做出的取舍**，不新增待办、不改写短板状态。
- **来源**：第 0 波 0-C / `I2`（见 `KNOWN_ISSUES.md` 的 I2 条目与波次表）。本批把清单里已标 `ACCEPTED` 的项和散落在代码注释/文件头里的核心取舍收敛为一页一决策。
- **约束**：一决策一页；已生效的 ADR 正文不回改，需要推翻时新建 ADR 并把旧文标 `SUPERSEDED` 并互相链接。

## 状态取值

| 状态 | 含义 |
|---|---|
| `PROPOSED` | 已提出，尚未落地 |
| `ACCEPTED` | 决定已生效，或明确接受当前取舍（含已知代价） |
| `DEPRECATED` | 决定不再适用，保留记录 |
| `SUPERSEDED` | 被后续 ADR 取代，须在文件头标注取代者编号 |

本批 7 篇状态均为 `ACCEPTED`：**前 4 篇记录代码中已生效的核心设计**，**后 3 篇记录清单中已明确接受代价的取舍**。
「记录既有决策」不等于「宣称没有缺口」——每篇都列出已知短板与「复核触发条件」，并链接到对应待办。

## 索引

| 编号 | 决策 | 状态 | 关联条目 |
|---|---|---|---|
| [0001](0001-global-single-worker-concurrency.md) | 后台任务全局单并发（advisory lock） | `ACCEPTED` | F2 / E1 |
| [0002](0002-fail-closed-scoring-and-evidence.md) | 评分与证据链 fail-closed | `ACCEPTED` | B5 / D7 / C5 |
| [0003](0003-no-vendor-sdk.md) | 不引入 LLM 厂商 SDK / 可观测性 SDK | `ACCEPTED` | B7 / G2 / G1 |
| [0004](0004-utf8-byte-token-budget.md) | UTF-8 字节估算 token 预算 | `ACCEPTED` | C1 / D6 |
| [0005](0005-accepted-evolution-activation-fuse.md) | evolution 策略激活默认熔断 | `ACCEPTED` | B5 |
| [0006](0006-accepted-rule-based-followup-and-planner.md) | 追问建议与学习计划保持确定性规则 | `ACCEPTED` | B6 / D3 |
| [0007](0007-accepted-no-code-sandbox.md) | 代码题不执行用户代码，静态评审 | `ACCEPTED` | D7 |

## 新增 ADR 的模板字段

编号递增，文件命名 `NNNN-kebab-case-title.md`，包含以下字段：

- 状态 / 日期 / 关联（`KNOWN_ISSUES` 条目编号 + 代码路径）/ 决策者
- 背景：当时的约束与风险，只写可查证的事实
- 决定：明确、可执行的单一结论
- 备选方案：至少列出被放弃的方案与放弃理由
- 后果：正面收益 + 负面代价 + 已知短板（不得因写了 ADR 就隐去缺口）
- 复核触发条件：什么条件下必须回来更新或取代本文
- 表述口径（仅 `ACCEPTED` 取舍）：面向面试/复盘的诚实表述

## 与交付纪律的关系

第 0 波门禁要求「PR 不绿不能合」。本目录是文档，改动不进入 CI 的硬性应用检查；
格式与链接由本次交付的本地校验覆盖。ADR 不替代 `KNOWN_ISSUES.md` 的状态流转，
某项转为 `DONE` 仍以清单为准。
