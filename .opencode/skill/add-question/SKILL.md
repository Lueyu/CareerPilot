---
name: add-question
description: Use when the user sends an interview question (screenshot, photo, or pasted text) to add to this question bank, or asks to 贡献题目/加题/入库/补充题库/新增面试题. Parses the question, deduplicates, classifies it into one of 8 topics, writes a full question card with MCQs per content/SPEC.md, validates with build.py, and commits. Never commits personal data (notes.json).
---

# 面试题入库(add-question)

把用户提供的面试题(截图/照片/文本)写成本题库同款、同质量的题目卡片,入库并提交。

## 隐私红线(最高优先级)

- **绝不读写或提交个人数据**:`notes.json`、`notes-export-*.json` 是用户的私人笔记与熟练度,
  已被 `.gitignore` 忽略。git add 时只加 `content/` 与 `data.js`,禁止 `git add -A` /
  `git add .`
- 提交前必须检查 `git status --short`,确认暂存区里只有题目内容与构建产物
- 不 push(用户/贡献者自行 push 并开 PR)

## 触发条件

用户在本项目目录里:发送题目截图或照片;粘贴面试题文本;说"加题 / 入库 / 补充题库 /
贡献题目 / 新增面试题"。一次发多张图 = 批量入库,逐张走完整流程。

## 流程(每道题依次执行)

### 1. 解析输入

- 图片 → 用 read 工具读取,提取题面文字(含公司来源、岗位方向,如有)
- 文本 → 直接使用
- 图片模糊/题面残缺 → 向用户追问,不要瞎猜

### 2. 查重

用题目标题 + 关键词与现有题库比对(读 `content/questions/` 同主题文件的
`data-title` / `data-kw`,或 grep 关键词):

- **高度相似** → 进入"扩充模式":不新建文件,为已有题目追加 1~2 条追问(details.qa)
  和/或补足 MCQ(该题的 `<script type="application/json">` 块),跳到第 5 步
- **不同题** → 继续

### 3. 归类

映射到 8 主题之一(读 `content/topics.json`):

`rag` / `harness` / `ctx` / `infer` / `eval` / `sys` / `algo` / `biz`

置信度低时列出 2~3 个候选主题及理由让用户选,**不要静默决定**。
新文件路径:`content/questions/{主题code}/{该主题现有最大序号+1,三位}.html`。

### 4. 写题卡

**必须先读 `content/SPEC.md` 和 2~3 个现有题目文件作范本**(如 `content/questions/rag/001.html`)。
硬规则(SPEC 有完整清单,此处强调最易错项):

- article 标签带全部 8 个属性:id/data-topic/data-title/data-difficulty/data-tags/
  data-company/data-source/data-kw;id 与文件路径一致且全库唯一
- 五个 block 缺一不可:30秒电梯稿(fast)/ 通俗类比(ana)/ 详细拆解(det)/
  面试官追问(fol,用 details.qa 折叠,2~4 条)/ 术语卡(term,3~6 条)
- **正文禁止字面 `<article` / `</article` 标签**(示例代码写 `&lt;article&gt;`,否则构建直接报错)
- **MCQ 必须有**:紧跟 `</article>` 写 `<script type="application/json">` 块,
  2~3 道单选,options 恰 4 项非空不重复,answer 为 0..3 下标,explain 说清对错原因;
  干扰项要利用常见误解出题
- 技术准确性最高优先级:公式必须正确,不臆造论文结论/基准分数;
  来源公司已知时写 data-company(如"字节·真题"),不知道写"网友面经"

### 5. 构建

```bash
python build.py
```

- 校验**必须 0 error**(错误会列出文件名与原因,修复后重跑)
- 新增题不允许出现在"无 MCQ"警告里
- `data.js` 自动重新生成

### 6. 提交

```bash
git add content/questions/<新文件路径> data.js
git status --short   # 确认暂存区没有其他文件(尤其没有 notes.json!)
git commit -m "feat: 新增{主题名}题目:{一句话标题}"
```

多题批量入库可以合并为一个 commit(`feat: 新增{N}道{主题}面试题`)。

### 7. 回复用户

- 新题链接:`index.html#/q/{qid}`(如 `#/q/rag-013`)
- 一句话摘要:主题、难度、题目问什么
- 提醒:**个人笔记(notes.json)不会被提交**;如果要发布到 GitHub,push 并开 PR

## 贡献者协作流程

本仓库欢迎任何人贡献真题。贡献者(克隆了 fork 的开发者)在本目录使用 opencode 时,
本 skill 同样生效。完整贡献路径:

1. Fork & 克隆仓库,启动 opencode
2. 发题目截图/文本 → 本 skill 完成入库(commit)
3. `git push` 到自己的 fork,向上游仓库提 Pull Request
4. PR 内容应只有 `content/questions/**` 与 `data.js`

无 opencode 的贡献者走手动路径(见仓库 README「方式 B」):读 content/SPEC.md →
手写题目文件 → `python build.py` 校验通过 → 提 PR。

## 常见错误处理

- `build.py` 报"id 与文件路径不符" → 检查文件目录/文件名与 id 的 {code}-{NNN} 是否一致
- 报"嵌套/字面 <article 检出" → 题卡正文里把标签写成转义形式
- 报"MCQ JSON 解析失败" → 检查 script 块里的 JSON 逗号/引号
- id 冲突 → 重新确认该主题下最大序号(文件名与 id 都要改)
