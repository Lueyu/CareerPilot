# PUBLIC_SITE_DESIGN — agent-interview.icu 公共知识门户设计（2026-11-16）

> 依据 v3.1 Part B。agent-interview.icu 从「私人工作台」转为**公开知识门户**；CareerPilot 本体继续作为本地私人应用。两者视觉同源（深海军蓝 + 靛紫），职责完全分离。

## 1. 定位与红线

- **是**：公开学习资料门户 —— 学习路线、技术知识库、公开面试题、Hands-on Labs、工程案例、CareerPilot 介绍。
- **不是**：CareerPilot 的 Web 版；无登录、无用户数据库、无私有数据。
- 红线：不访问本地 CareerPilot 数据库；不读私有记忆/答卷/画像；不存 GitHub Token；未审核内容不得自动公开。

## 2. 信息架构（首期导航）

```
首页 /
├─ 学习路线 /paths/          （AI Agent 工程师成长路线）
├─ 技术知识库 /guides/        （按主题组织的深度文章）
├─ 面试题库 /questions/       （公开/授权面试题）
├─ 实践实验室 /labs/          （Hands-on Labs）
├─ 工程案例 /cases/           （案例研究）
└─ 关于 /about/               （CareerPilot 介绍 + 下载指引）
```

## 3. 首页区块（自上而下）

1. **Hero**：主标题「从理解 Agent，到构建、评估和交付可靠的 Agent 系统」+ 副题 + CTA（开始学习路线 / 浏览知识库）。
2. **三入口卡**：学习路线 / 技术知识库 / 面试题库。
3. **精选教程**：3-6 篇最新/精选文章卡。
4. **热门主题**：Memory、Tool Calling、RAG、Evaluation、Reliability、MCP 标签云。
5. **最新更新**：时间线式最近发布列表。
6. **CareerPilot CTA**：介绍证据驱动的私人工作台 + 本地下载指引。
7. **Footer**：版权、内容许可说明、源码链接、CareerPilot 链接。

## 4. 内容模型

统一 frontmatter（Markdown 头部 YAML）：

```yaml
title: 工具调用失败重试的工程实践
slug: tool-retry
description: 一句话摘要
category: guides        # paths|guides|questions|labs|cases|changelog
tags: [tool-calling, reliability]
level: intermediate     # beginner|intermediate|advanced
estimated_reading_minutes: 12
published_at: 2026-11-16
updated_at: 2026-11-16
status: published       # draft|published
source_type: original   # original|public|licensed
sources: []             # 引用来源
author: CareerPilot
```

文章统一 8 段结构：一句话结论 → 生活类比 → 具体例子 → 原理与术语 → 常见误区 → 实践步骤 → 自测问题 → 延伸阅读。

## 5. 技术方案

- 纯静态：`portal/build.mjs`（零 npm 依赖，Node 内置模块）将 `portal/content/**/*.md` 渲染为 `portal/dist/**/*.html` + 列表页 + 首页 + sitemap.xml + robots.txt + 搜索索引 JSON（`/search.json`，前端轻量过滤）。
- 部署沿用现有 OSS 静态发布链（deploy/publish.mjs 适配门户产物目录），无新后端。
- 响应式单栏为主，桌面双栏（正文 + 目录侧栏），代码块带浅色复制按钮。
- 主题：默认深色（与 CareerPilot 同 tokens），支持浅色切换。

## 6. 内容发布流（与 CareerPilot 的连接）

```
CareerPilot 生成候选内容 → 隐私扫描 → 本地预览 → 人工确认
→ 写公开仓库分支 → PR → CI 校验构建 → OSS 发布
```

反向：门户公开内容可拉回 CareerPilot 作学习资料（内容包 JSON）。首期仅落静态产物与目录结构，发布流状态机在后端阶段实现（v3.1 Part C）。

## 7. 首期内容清单（示例）

- 学习路线 1 条：AI Agent 应用开发工程师 0→1（含 8 能力域阶段目标）。
- 知识库文章 6-8 篇：工具重试与幂等、记忆压缩、RAG 召回排查、Agent 评估、多智能体协作、可观测性、幻觉治理、MCP 入门。
- 面试题库：从现有 161 题静态库中选公开授权子集（约 20 题）做 HTML 化展示（摘要 + 跳转）。
- 实践实验室 2 个：搭建最小 Tool-Calling Agent、给 Agent 加评估。
