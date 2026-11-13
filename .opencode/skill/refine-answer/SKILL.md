---
name: refine-answer
description: Use when the user wants to 美化/改写/升级 an existing question's answer, pastes a better answer from another AI (Claude/GPT etc.), says 处理草稿/refine answer, or drops files into drafts/. Rewrites the full question card (five modules + MCQs) around the new answer, generates SVG diagrams/tables, keeps metadata intact, validates with build.py, and commits. Never touches personal data.
---

# 答案美化(refine-answer)

用户对某道题的现有答案不满意,提供了别的 AI 给的更好回答(聊天粘贴,或存到 `drafts/` 的文件)。
把新回答结构化重写成本题库同款、同质量的题卡,**全量替换**旧内容;元数据原样保留。

## 隐私红线(最高优先级)

- **绝不提交个人数据**:`notes.json`、`notes-export-*.json` 已 gitignore;git add 只加
  题目文件与 `data.js`,禁止 `git add -A` / `git add .`
- `drafts/` 目录也已 gitignore(草稿是用户私料),处理完成的草稿文件**删除**,不入库
- 提交前必须 `git status --short` 确认暂存区;不 push

## 触发条件

- 用户在聊天里粘贴一段答案并指明题目:"美化 rag-003 的答案:…" / "这题的答案换成下面这个"
- 用户说"处理草稿 / 处理 drafts"→ 扫描 `drafts/` 下所有 `.txt`/`.md` 文件逐个处理
- 多题批量时逐题走完流程,最后可合并为一个 commit

## 流程(每道题依次执行)

### 1. 收集输入

- 聊天粘贴 → 直接作为新答案文本
- `drafts/*.txt|md` → 读文件;文件名通常含题号(如 `rag-003-gpt.txt`),以此定位

### 2. 定位目标题

- 文件名/口述含 qid(形如 `rag-003` 或 `rag/003`)→ 直接定位 `content/questions/{topic}/{NNN}.html`
- 无题号 → 用新答案的核心论述与题库 160 题的 `data-title`/`data-kw` 匹配(grep 关键词);
  **多个候选时列出让用户选,不静默决定**;找不到 → 问用户是哪题
- 定位后读旧题卡,**保留 article 标签上全部 8 个元数据属性**
  (id/data-topic/data-title/data-difficulty/data-tags/data-company/data-source/data-kw,
  这是在"美化",不是新建题)

### 3. 重写题卡(全量,五个模块都换)

**先读 `content/SPEC.md` + 2~3 个现有题作范本**(必读 `content/questions/rag/001.html`,
它含表格与 SVG 架构图的完整用法)。新答案为权威内容来源:

- **30秒电梯稿**:从新答案提炼结论+要点,100~180 字
- **通俗类比**:新答案自带类比则润色使用,否则按新内容重新构思
- **详细拆解**:核心工程量最大的模块——
  - 流程/链路/架构性描述 → **SVG 架构图**(`.svgwrap` 包裹,类名 `.sdiagram/.sd-box/
    .sd-box2/.sd-box-teal/.sd-box-amber/.sd-box-rose/.sd-layer/.sd-t/.sd-ts/.sd-l/.sd-a/
    .sd-an`,箭头引用 `marker-end="url(#sd-arr)"`,页面已内置该 marker;先照抄范本题的
    图结构改内容)
  - 对比/选型内容 → HTML `<table>`
  - 公式/代码 → `pre.code`(注释用 `<span class="c">`)
  - 别堆大段纯文字,用小标题 + `<ul>` 组织
- **面试官追问**:基于新内容生成 2~4 条 `<details class="qa">`,答案讲透
- **术语卡**:3~6 条 `term-item`
- 正文禁止字面 `<article` / `</article`(示例中写 `&lt;article&gt;`,违反会构建报错)

### 4. 技术准确性把关

新答案**不是照搬照抄**:发现明显的事实/公式错误,以正确内容写入,并在最终回复里明确告知
用户"新答案中 X 处有误,已按 Y 修正"。不确定的细节用保守表述。

### 5. MCQ 同步重写

题解变了,题卡尾部的 `<script type="application/json">` 块必须按新内容重写:
2~3 道单选,options 4 项非空不重复,answer 0..3 下标,explain 说清对错,干扰项用常见误解。

### 6. 构建

```bash
python build.py
```

0 error 才继续;该题不得出现在"无 MCQ"警告里。

### 7. 提交

```bash
git add content/questions/<题文件> data.js
git status --short   # 确认没有 notes.json / drafts 混入
git commit -m "feat: 美化{主题名}题目{qid}答案(全量重写,原版见git历史)"
```

批量处理合并 commit 可用:`feat: 美化{N}道题答案(全量重写)`。

### 8. 回复用户

- 题目链接:`index.html#/q/{qid}`
- 改动摘要:哪些模块重写、新增了什么图表(表格/SVG 图)、修正了新答案里的哪些错误
- 提醒:旧版在 git 历史,`git revert <commit>` 可整体回退

## 常见错误处理

- `build.py` 报"嵌套/字面 <article 检出" → 正文里标签转义
- 报"无 MCQ"警告含该题 → 补写 script 块后重跑
- 草稿文件内容与题号对不上 → 找用户确认,不要猜
- 新答案与旧答案讲的是两回事(用户可能想新建题而非美化)→ 停下来问:美化这题,还是走
  add-question 新增一题?
