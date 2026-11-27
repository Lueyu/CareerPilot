---
name: issue-solver
description: 当用户要开工修复 CareerPilot 工业级短板清单中的某项（如"开工 E1"、"做 D1"、"解决 A4"、提到 KNOWN_ISSUES 条目编号或波次），或宣布某项完工要记录时使用。加载排程上下文与依赖状态，给出针对该条目的实施建议，完工后更新清单状态、记日志并同步看板。
---

# 短板逐项补全（issue-solver）

围绕 `KNOWN_ISSUES.md`（60 项短板清单 + 附录开工计划）逐项施工。本技能保证新对话立即知道：用户在按 6 波排程补全工业级差距，当前条目是什么、前置是否就绪、该怎么做、做完怎么记。

## 事实源与工具

- 清单与排程：仓库根 `KNOWN_ISSUES.md`（唯一事实源，条目 ID 如 `E1`/`D1`）。
- 领域实施要点：[references/playbook.md](references/playbook.md)——只读当前条目相关段落。
- 配套脚本（仓库根执行）：
  ```bash
  python .agents/skills/issue-solver/scripts/issue_tool.py context <ID>   # 条目详情+波次+依赖状态+历史
  python .agents/skills/issue-solver/scripts/issue_tool.py progress      # 各波完成度
  python .agents/skills/issue-solver/scripts/issue_tool.py done <ID> --summary "结论" [--pr URL] [--sha SHA] [--evidence 路径] [--followup "遗留"] [--accepted]
  ```
- 交互看板：`docs/known-issues/index.html`；施工时间线：`docs/known-issues/timeline.html`（甘特总览+波次轨道+依赖链，两页互链）。均由 `scripts/build_known_issues_page.py` 生成，done 命令自动重跑。
- 完工日志：`docs/agent/issue-log.md`（done 命令追加）。

## 开工流程（用户给出条目 ID 或描述时）

1. 运行 `issue_tool.py context <ID>` 获取条目四段详情、所属波次/轨道、依赖链及前置状态。用户只描述问题未给 ID 时，先按标题在 KNOWN_ISSUES.md 定位 ID。
2. 检查前置：依赖链上前置项非 DONE/ACCEPTED 时，明确告知用户"按计划应先做 X"，由用户决定是否越序；同轨道 `[串行]` 项按序推进。E1 特殊：第 1 波只做并发闸，队列化属第 5 波，勿提前重构。
3. 读 playbook.md 对应领域段落，结合条目"目标方案"给出**本次施工建议**：复用哪些既有基建、分几步、每步验收标准、已知坑。建议须具体到文件与测试，不空谈。
4. 遵守 AGENTS.md 与 project-delivery 纪律：从最新上游 dev 建独立分支；隔离 `_test` 库验证；每项单开 PR 到 dev；合入需 Lueyu 明确授权。涉及评分/证据/归因语义时保留 playbook"全局设计哲学"五条边界。
5. 实现与验证走 project-delivery 技能流程；本技能不替代它，只补充"这一项该怎么做"的上下文。

## 完工流程（用户宣布完成/验收通过时）

1. 确认验证真实发生：只把实际运行过的检查写为通过；质量类条目（D1/A4/A1/C2/C3/E4 等）必须有前后对比数字或评测回执，否则不算 DONE。
2. 运行 `issue_tool.py done <ID> --summary ... --pr ... --sha ... --evidence ...`；有意接受取舍不实现的用 `--accepted`（须先写 ADR，见 playbook I2）。有遗留用 `--followup` 记录。
3. 脚本会改 KNOWN_ISSUES.md 状态、追加 issue-log.md、重建看板。核对脚本输出无误。
4. 若施工中发现新短板或排程需调整：在 KNOWN_ISSUES.md 增补条目（沿用 `### ID. 标题 \`P级\` \`状态\`` + 四段格式）并重跑 builder；排程变更同步改附录开工计划。
5. 提醒用户下一项：按 `progress` 输出与依赖速查，指出当前波剩余项或已解锁的下一波轨道。

## 边界

- 不主动批量施工；一次聚焦用户点名的条目（同波并行轨道由用户显式要求才同时开）。
- 不修改个人实例数据/进程；验证一律隔离库+回环地址。
- 清单状态以 KNOWN_ISSUES.md 为准；网页 localStorage 里的个人笔记不回写清单。