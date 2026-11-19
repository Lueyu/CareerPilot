# CareerPilot 协作指引

CareerPilot 帮助 AI 工程师在本地用真实作答找出差距，按失分项学习，再以独立新题验证改进。维护时优先保护个人数据、评分证据和项目归因。

## 选择工作方法

先读 [本地交付工作流](docs/agent/workflow.md)，按当前任务加载对应技能。

- [project-discovery](.agents/skills/project-discovery/SKILL.md) 用于查证项目状态和确定下一项值得验收的改进。
- [project-delivery](.agents/skills/project-delivery/SKILL.md) 用于实现已在授权范围内的改进、运行验收并提供本地预览。
- [workflow-retro](.agents/skills/workflow-retro/SKILL.md) 用于依据交付证据和用户反馈改进后续工作方法。

已明确的任务可直接进入交付，按需要补查证据。启用 poteto-mode 时沿用当前会话的 playbook 和模型路由，以上技能补充 CareerPilot 的项目约束、验收入口和本地交付边界。

处理睡前建议或用户回复的建议编号时，先查[晚间审批账本](docs/agent/nightly.md)。用户采纳后，按依赖和优先级实施。每项创建单独的 PR，验证通过后合入对应仓库的 `dev`。两套本地应用按各自的 `dev` 提交部署，早晨用户体验后统一合入主干。

## 保护工作区和数据

先检查 Git 状态、相关差异和现有文件。共享工作区只写本次获准的路径，保留已有更改和未跟踪文件。自动验收使用独立测试库和本机回环地址，个人实例的进程、运行记录和数据由使用者管理。

默认交付范围是本地改动、验证、预览和记录。提交、推送、部署、外部消息及修改日程须有相应授权。每次运行的报告和证据归入 `.agent-runs/<run>/`。
