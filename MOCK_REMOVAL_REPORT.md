# Mock 移除报告

> 版本：v3.2 · 2026-11-16 · 全量代码审计后的处置记录
> 原则：不删除合理使用的演示数据（必须显式标注 DEMO），只消灭「假接口真数据」与「假进度假动作」。

## 处置结果统计

| 处置 | 数量 | 说明 |
|---|---|---|
| ① 假数据被真实实现替换 | 14 项 | 见下表 |
| ② 保留但显式标注 DEMO/演示 | 6 处 | 种子证据/记忆/trace（is_demo 或文案标注） |
| ③ 诚实降级而非伪造 | 全部 API 页 | 后端不可用→「后端不可用…静态演示数据」横幅，绝不把 demo 伪装成真数据 |

## ① 已替换清单

| 位置（原假实现） | 现状 | 提交 |
|---|---|---|
| Bank.tsx 导入进度 setInterval 假计时 | 后台任务 5 阶段真实进度落库+轮询 | 4bb722f |
| Bank.tsx 待审核列表读 `d.items` 键名不匹配→永远假数据 | `/bank/imports/pending` 真实列表+通过按钮真调 API | 0c65882 |
| Bank.tsx PENDING demo 常量/审核弹窗假兜底 | 删除，诚实空态 | 0c65882 |
| Assets.tsx 三页全 demo | /projects 扫描+findings+pipeline 真实 API | 0276361/0a83624 |
| Pipeline.tsx 8 步假流水线+假 PR 列表 | AssetUpdateRun 真实运行+发布/回滚 | 0276361 |
| Evolution.tsx 假实验 EVO-018/016 | 真实候选生成/评估/批准/回滚（样本不足诚实拒绝） | 16ee7e3 |
| DiagnosisReport.tsx 一行式假小结 | 能力域得分+确定性结论+AI 深度诊断 | 62d8699 |
| LearningTask.tsx 假声明「正式版由大模型生成」+ HELP_TEXT 常量 | 真实 LLM 生成（AI/规则来源标注）+ 7 种求助 | 22c9c27 |
| Settings.tsx 5 tab 全硬编码 | overview/account/llm/github/export/delete/rules 真接口 | 679e24e/2edbfdf |
| Diagnosis.tsx 每次进页 StrictMode 双建考试 | 恢复进行中考试→无才新建 | 54abffb |
| Dashboard.tsx 「benchmark benchmark」重复文案 | 修正 demo.ts 值 | 1d3786d |
| Learning.tsx duplicate key t3 | 唯一 id | 22c9c27 |
| ExamCenter 历史 status 展示 | ABORTED/EXPIRED/SCORING_FAILED 实态 | 54abffb |
| 导入仓库 409 报错体验 | 「已导入过」友好提示+创建即刷新 | 539a21c |

## ② 保留的 DEMO 数据（合规）

- 种子证据 15 条 / 记忆 9 条 / trace 示例：seed.py 创建，Evidence.is_demo=true / 文案含「示例」。
- 导入流程 banner「DEMO · 导入、审核与合并为示例流程；公开题库为本地静态数据」——公开题库 161 题为真实静态内容（非伪造评估结果）。
- 演示横幅统一由 DemoBanner 组件渲染，删除/未加载后端时可见。

## ③ 诚实降级（API-first 页面统一行为）

12 个业务页面：加载中骨架 / 后端不可用→红横幅+静态演示数据（明示）/ LLM 未配置→按钮提示配置路径，绝不静默伪造结果。规则引擎兜底输出一律标注「规则生成」或「规则评分（rule-v1）」，与「AI 生成（judge-v1）」区分。
