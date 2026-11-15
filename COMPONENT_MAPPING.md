# COMPONENT_MAPPING — v3.1 导航收敛与页面归并映射（2026-11-16）

> 现有组件/路由 → v3.1 目标结构。原则：页面组件不删除、不重写，只改变入口层级与组织方式。

## 1. 路由与入口映射

| v3.0 入口（侧栏 10 项） | v3.1 入口（5 + 高级区） | 页面组件 | 说明 |
|---|---|---|---|
| 01 总览 `/` | 首页 `/` | `pages/Dashboard.tsx` | 首屏重构为行动聚焦（M2） |
| 02 岗位对标 `/benchmark` | 能力与岗位 `/benchmark`（Tab: 岗位对标） | `pages/Benchmark.tsx` | 默认 Tab=岗位对标 |
| 03 能力画像 `/profile` | 能力与岗位 `/benchmark`（Tab: 能力画像） | `pages/Profile.tsx` | 由 `/profile` 重定向/嵌套承载 |
| 04 考试中心 `/exam` | 考试与面经 `/exam`（Tab: 考试中心） | `pages/ExamCenter.tsx` | 默认 Tab=考试中心 |
| 06 面经库 `/bank` | 考试与面经 `/exam`（Tab: 面经库） | `pages/Bank.tsx` | 由 `/bank` 重定向/嵌套承载 |
| 05 学习中心 `/learning` | 学习计划 `/learning` | `pages/Learning.tsx` | M4 五步教学流 |
| 07 项目与职业资产 `/assets` | 项目与简历 `/assets` | `pages/Assets.tsx` | 含 ProjectReview/Pipeline |
| 08 记忆中心 `/memory` | 高级区 > 记忆中心 | `pages/Memory.tsx` | 不变 |
| 09 系统观测台 `/observatory` | 高级区 > 系统观测台 | `pages/Observatory.tsx` | M6 分级视图 |
| 10 进化实验室 `/evolution` | 高级区 > 进化实验室 | `pages/Evolution.tsx` | M7 空态诚实化 |
| 设置 `/settings` | 高级区 > 设置与工作空间 | `pages/Settings.tsx` | 不变 |

## 2. 新增/调整的共享组件

| 组件 | 文件 | 作用 |
|---|---|---|
| `plainLevel(level)` | `components/ui.tsx` | L0-L5 → 通俗语言（M3） |
| `plainConfidence(c)` | `components/ui.tsx` | 置信度 → 一句话解释（M3） |
| `TabPage` 容器 | `components/ui.tsx` | 岗位/考试两个合并页的 Tab 头 |
| 学习五步流 | `pages/LearningTask.tsx` | 步骤+帮助按钮（M4） |
| 原文预览分栏 | `pages/Bank.tsx` | 导入审核左栏（M5） |
| 执行摘要 | `pages/Observatory.tsx` | 通俗摘要 + 技术详情 Tab（M6） |

## 3. 数据边界（v3.1 Part A P0-3）

- Demo 数据源：`data/demo.ts` + `data/bank.json`（161 题静态导入）——所有展示处带 DEMO 徽章。
- 真实数据源：`api/client.ts useApi()` —— 后端不可用时降级提示，不用演示数据冒充真实结果。

## 4. 公共门户（独立子项目）

| 产物 | 位置 |
|---|---|
| 门户源码与构建脚本 | `portal/`（build.mjs 零依赖构建） |
| 门户内容 | `portal/content/**`（frontmatter Markdown） |
| 门户站点 | 输出至 `portal/dist`，独立发布到 agent-interview.icu |
