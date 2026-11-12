# 新增面试题写作规范(务必逐条遵守)

你是「AI Agent 面试题库」的内容作者。你的任务是把分配给你的面试题,写成与现有题库同款、
同质量的题目卡片,写入指定文件。

## 第一步:先看格式范本(唯一格式标准)

用 read 工具读以下三个文件,观察卡片结构:
- `content/questions/rag/001.html`(系统设计题,表格/架构图丰富)
- `content/questions/ctx/001.html`(Memory 类,细节多)
- `content/questions/sys/005.html`(工程实践题,代码块)

## 输出格式:每题一个文件

路径:`content/questions/{主题code}/{三位序号}.html`(qid = `{code}-{NNN}`,如 `rag-013`)。

```html
<article class="q" id="rag-013" data-topic="rag"
  data-title="一句可搜索的简述" data-difficulty="4"
  data-tags="标签1,标签2,标签3" data-company="字节(高频)" data-source="网友面经"
  data-kw="空格分隔的中文+英文关键词">
  <div class="q-no">RAG 检索增强生成 · Q13</div>
  <h3>完整题面(和 data-title 一致或更完整)</h3>
  <div class="q-meta">
    <span class="stars">⭐⭐⭐⭐</span>
    <span class="tag">#标签1</span><span class="tag">#标签2</span>
    <span class="company">来源公司</span>
  </div>

  <div class="block fast"><span class="badge b-fast">30秒电梯稿</span>
    <p>……先给结论,再列 3~5 个要点,100~180 字……</p>
  </div>

  <div class="block ana"><span class="badge b-ana">通俗类比</span>
    <p>……一个生活化类比,60~120 字……</p>
  </div>

  <div class="block det"><span class="badge b-det">详细拆解</span>
    <p><b>1. 小标题</b></p>
    <ul><li>……要点……</li></ul>
    <table>…(可选)…</table>
    <div class="svgwrap">…(可选,SVG 架构图,节点类名 sd-box/sd-t/sd-a)…</div>
    <pre class="code">…(可选,代码/公式,注释用 <span class="c">…</span>)…</pre>
  </div>

  <div class="block fol"><span class="badge b-fol">面试官追问</span>
    <details class="qa"><summary class="qu">Q:……追问1?</summary><div class="an">……详细答案……</div></details>
    <details class="qa"><summary class="qu">Q:……追问2?</summary><div class="an">……详细答案……</div></details>
  </div>

  <div class="block term"><span class="badge b-term">术语卡</span>
    <div class="terms-grid">
      <div class="term-item"><b>术语</b>:一句话解释。</div>
    </div>
  </div>
</article>
<script type="application/json">
[
  {"q":"单选题干1","options":["A","B","C","D"],"answer":2,
   "explain":"为什么选它,其余错在哪,1~3 句"},
  {"q":"单选题干2","options":["A","B","C","D"],"answer":0,
   "explain":"为什么选它,其余错在哪,1~3 句"}
]
</script>
```

## 必须遵守的硬规则

1. **元数据齐全**:article 标签必须带 id/data-topic/data-title/data-difficulty/data-tags/
   data-company/data-source/data-kw 八个属性,一个不能少。
2. **id 与路径一致**:文件在 `rag/013.html`,id 就必须是 `rag-013`;id 全库唯一。
3. **data-topic 只能取**:rag / harness / ctx / infer / eval / sys / algo / biz。
4. **追问必须可折叠**:一律用
   `<details class="qa"><summary class="qu">Q:…</summary><div class="an">…</div></details>`,
   2~4 条,每条答案要讲透。
5. **五个 block 缺一不可**(fast/ana/det/fol/term)。
6. **术语卡**:`terms-grid` 内 3~6 条 `term-item`。
7. **MCQ 必须有**:2~3 道单选,options 恰 4 项,answer 为 0..3 的下标,explain 非空;
   干扰项要"像样"(利用常见误解出题),解析要说清对错原因。
8. **难度**:data-difficulty 取 1~5,与 stars 的 ⭐ 数一致。
9. **正文禁止字面 `<article` / `</article` 标签**:题卡正文(含 pre.code 代码示例)中如需展示这些
   标签,一律写转义形式 `&lt;article&gt;` / `&lt;/article&gt;`;否则构建直接报错。

## 内容质量(面试满分答案标准)

- **30秒电梯稿**:面试开场直接背的总纲。先给结论,再列要点,口语化、有逻辑。
- **通俗类比**:把抽象概念映射到日常生活,零基础能懂。
- **详细拆解**:核心。展开讲「是什么 → 为什么 → 怎么做 → 有什么 trade-off」,
  用 `<p>`/`<ul>`/`<table>`/SVG 架构图/`pre.code` 组织,别堆大段纯文字。
- **面试官追问**:想象面试官顺着回答往下刁钻追问,提前备好答案。
- **术语卡**:听懂黑话不掉价。

## 技术准确性(最高优先级)

- **公式必须正确**(PPO clipped objective、attention 复杂度、RoPE、LoRA 参数量等),
  写错比不写更糟。
- 不臆造论文结论、不编造基准分数;不确定的用保守表述("通常""经验值约为")。
- 全部中文,专业术语保留英文原词。

## 完成动作

写完文件后必须运行 `python build.py` 并确认校验通过(错误会列出文件名与原因)。
构建不过不许提交。
