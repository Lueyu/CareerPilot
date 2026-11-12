# 题库 v2 · P1 地基 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把单文件 160 题面试题库重构为「每题一个源文件 + build.py 构建 + 单文件 SPA(一页一题/笔记/熟练度/notes.json 同步)」,交付 P1 地基(设计文档 2026-11-13-interview-bank-redesign-design.md 的 P1 阶段)。

**Architecture:** 方案 A。`content/questions/{code}/{NNN}.html` 每题一源文件(article 标签属性即元数据 + 紧跟 MCQ JSON 块);`build.py` 扫描校验后原子生成 `data.js`;新 `index.html` 是纯静态 SPA 壳,经 `<script src="data.js">` 加载数据,hash 路由渲染视图;笔记/熟练度先写 localStorage 镜像,再经 File System Access API 防抖 3 秒写回仓库根目录 `notes.json`。

**Tech Stack:** Python 3.12(仅标准库)、原生 HTML/CSS/JS(零依赖零框架)、浏览器 localStorage/IndexedDB/File System Access API。**不引入 Node。**

**范围说明:** 本计划只覆盖设计文档的 **P1 地基**。P2(四种考题模式 + #/notes 总览 + #/top10 视图)、P3(add-question skill + MCQ 补写)、P4(README 重写 + .fragments 清理)在 P1 验收后另写计划。build.py 在 P1 就会把 top10 数据嵌进 data.js(P2 只是加视图)。

---

## 执行者须知(先读,否则会踩坑)

1. **工作目录**:全程在 `D:\agent-interview`。Windows + PowerShell 5.1 + Python 3.12。
2. **控制台中文乱码**:PowerShell 控制台显示 UTF-8 中文可能乱码,**不影响程序正确性**。若要看好中文输出,先执行:
   `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8`
   或让脚本把结果写入 UTF-8 文件后用 Read 工具看。
3. **不要在 PowerShell 里内联含中文/复杂引号的 python -c**,统一写成临时 .py 文件再运行(或直接运行 build.py)。
4. **文件编码**:所有新文件一律 UTF-8 无 BOM。写文件用 write 工具,不要用重定向。
5. **当前 index.html 结构**(执行中反复参考):
   - 第 7–192 行:`<style>` 完整设计系统(含深浅色 CSS 变量、SVG 架构图 .sd-* 系列、details.qa 折叠追问)
   - 第 193–195 行:主题预加载脚本
   - 第 198 行:SVG 箭头 marker 定义(`<marker id="sd-arr">`,**全部架构图都引用它,新壳必须保留**)
   - 第 201–213 行:顶栏(菜单按钮/品牌/搜索框/计数/导出/主题切换)
   - 第 248 行起:7 个板块标题 + 160 个 `<article class="q">` + t1 Top10 卡(第 13305 行起)
   - 第 13329–13448 行:旧版应用 JS(被 P1 整体替换,git 历史保留旧版)
6. **旧内容两个已验证的解析坑**:
   - p2q4 的 `data-title` 含 `>` 字符(`如何处理Agent的长对话场景(>10轮)`),标签扫描必须跳过引号内的 `>`
   - 追问的"展开全部"按钮(`.fol-toggle`)是旧 JS 动态注入的,题目 HTML 里没有,新壳渲染题目后要重新注入
7. **git 提交风格**:沿用仓库现有风格,`type: 中文描述`,如 `feat: build.py:data.js生成与原子写入`。不要 push。
8. **验收用的浏览器测试**:涉及浏览器的步骤标注了「主会话执行」——由主持的 agent 用 playwright MCP 工具完成(先 `python -m http.server 8761` 起本地服务,`file://` 被 playwright 拦截)。其余步骤任何执行者可用 bash 完成。
9. **build.py 的自测即测试**:没有 pytest,`--selftest` 是唯一自动化回归,每改 build.py 必须跑。

## 文件地图(本计划涉及的文件)

| 文件 | 动作 | 职责 |
|---|---|---|
| `content/topics.json` | 新建 | 8 主题配置(code/name/icon/order/desc) |
| `content/SPEC.md` | 新建 | 题目写作规范(由 .fragments/SPEC.md 升级) |
| `build.py` | 新建 | 扫描/校验/生成 data.js/--migrate/--selftest |
| `content/questions/{code}/{NNN}.html` | 迁移生成 | 每题一源文件(article 属性元数据+题卡 HTML+MCQ JSON) |
| `content/extra/top10.html` | 迁移生成 | Top10 速记卡(链接重写为 #/q/{qid}) |
| `data.js` | 构建生成 | window.QB = {topics, top10, questions[...]} |
| `index.html` | 整体替换 | 旧长文页 → v2 SPA 壳 |
| `notes.json` | 运行时生成 | 笔记+熟练度(用户浏览器写入,git 提交) |
| `docs/superpowers/specs/2026-11-13-interview-bank-redesign-design.md` | 不动 | 设计文档 |

---

### Task 1: content/topics.json(8 主题配置)

**Files:**
- Create: `content/topics.json`

- [ ] **Step 1: 写入 topics.json**

用 write 工具创建 `content/topics.json`,内容如下(与设计文档第 4 节一致):

```json
[
  {"code": "rag",     "name": "RAG 检索增强生成",   "icon": "🔍", "order": 1, "desc": "高并发RAG架构、检索优化、向量索引、混合检索、增量更新、多语言、GraphRAG"},
  {"code": "harness", "name": "Agent 编排与 Harness", "icon": "🤖", "order": 2, "desc": "Agent定义与组件、规划、工具调用、Multi-Agent协作、工作流引擎、反思与自我修正"},
  {"code": "ctx",     "name": "上下文工程与记忆",   "icon": "🧠", "order": 3, "desc": "Memory系统、长对话压缩、记忆衰退遗忘、个性化、向量记忆检索"},
  {"code": "infer",   "name": "模型推理与部署",     "icon": "⚙️", "order": 4, "desc": "LLM Gateway、统一API、vLLM推理吞吐、量化、LLM与Embedding选型"},
  {"code": "eval",    "name": "评估与可观测",       "icon": "📊", "order": 5, "desc": "评估平台、实验设计、LLM-as-judge、红队测试、线上监控、AB测试、灰度"},
  {"code": "sys",     "name": "系统设计与高可用",   "icon": "🏗️", "order": 6, "desc": "分布式调度、多租户SaaS、语音多模态Agent、SSE流式、异常重试、并发一致性"},
  {"code": "algo",    "name": "算法与训练原理",     "icon": "🧮", "order": 7, "desc": "RLHF/DPO/PPO、微调蒸馏、Transformer推导、MoE、Attention优化、前沿论文"},
  {"code": "biz",     "name": "选型、安全与业务落地", "icon": "💼", "order": 8, "desc": "框架选型、权限与隐私、幻觉治理、ROI、成本优化、Prompt管理、开放讨论"}
]
```

- [ ] **Step 2: 校验 JSON 合法**

Run(PowerShell): `python -c "import json;print(len(json.load(open('content/topics.json',encoding='utf-8'))))"`
Expected: `8`

- [ ] **Step 3: Commit**

```bash
git add content/topics.json
git commit -m "feat: v2内容基座:8主题配置topics.json"
```

---

### Task 2: content/SPEC.md(题目写作规范,由 .fragments/SPEC.md 升级)

**Files:**
- Create: `content/SPEC.md`
- 参考: `.fragments/SPEC.md`(gitignore 的脚手架,P4 才删除,本任务只读取)

- [ ] **Step 1: 写入 content/SPEC.md**

新文件基于旧规范,变更点:① 范本改为指向 `content/questions/rag/001.html` 等源文件;② id/路径规范改为 `{code}/{NNN}.html` + 完整元数据属性;③ 新增 MCQ JSON 块要求。完整内容:

```markdown
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
  {"q":"单选题干","options":["A","B","C","D"],"answer":2,
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
```

- [ ] **Step 2: Commit**

```bash
git add content/SPEC.md
git commit -m "feat: v2内容基座:题目写作规范SPEC升级(元数据+MCQ)"
```

---

### Task 3: build.py 骨架(标签扫描器 + selftest 框架)

**Files:**
- Create: `build.py`

- [ ] **Step 1: 写入 build.py 初始版本**

用 write 工具创建 `build.py`,完整内容:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题库构建工具 v2

用法:
  python build.py             扫描 content/ -> 校验 -> 生成 data.js
  python build.py --selftest  内置自测(坏样例回归)
  python build.py --migrate   一次性:从旧 index.html 拆分出 content/questions/
"""
import re
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONTENT = ROOT / "content"
QDIR = CONTENT / "questions"
EXTRA = CONTENT / "extra"
OUT = ROOT / "data.js"
TOPIC_CODES = ["rag", "harness", "ctx", "infer", "eval", "sys", "algo", "biz"]

# ==== 标签扫描(HTML 子集,只服务 <article> 与其属性) ====


def scan_tag(src, start):
    """扫描从 src[start]('<')开始的标签,返回 (标签文本, 结束下标)。
    引号内的 '>' 不视为标签结束(处理 data-title="...(>10轮)" 这类值)。"""
    quote = None
    i = start
    n = len(src)
    while i < n:
        c = src[i]
        if quote:
            if c == quote:
                quote = None
        else:
            if c in ('"', "'"):
                quote = c
            elif c == ">":
                return src[start:i + 1], i + 1
        i += 1
    raise ValueError("unterminated tag at offset %d" % start)


def parse_attrs(tag):
    """解析标签属性为 dict(只支持双引号属性,值保持原样)。"""
    return {m.group(1): m.group(2) for m in re.finditer(r'([\w-]+)="([^"]*)"', tag)}


def find_articles(src):
    """返回 [(tag, attrs, body)],按文档顺序;article 必须显式 </article> 闭合。"""
    out = []
    pos = 0
    while True:
        s = src.find("<article", pos)
        if s == -1:
            return out
        tag, after = scan_tag(src, s)
        e = src.find("</article>", after)
        if e == -1:
            raise ValueError("article 未闭合: %s" % tag[:80])
        body = src[after:e]
        out.append((tag, parse_attrs(tag), body))
        pos = e + len("</article>")


def load_topics():
    data = json.loads((CONTENT / "topics.json").read_text(encoding="utf-8"))
    return sorted(data, key=lambda t: t["order"])


# ==== selftest ====


def assert_eq(name, got, want):
    if got == want:
        print("PASS %s" % name)
        return True
    print("FAIL %-30s got=%r want=%r" % (name, got, want))
    return False


def selftest():
    ok = True
    # T3-1 引号内的 > 不截断标签(p2q4 真实用例)
    raw = '<article id="p2q4" data-title="如何处理Agent的长对话场景(>10轮)">正文'
    tag, end = scan_tag(raw, 0)
    ok &= assert_eq("scan_tag:quote-gt", tag.endswith(')">') and "长对话场景(>10轮)" in tag, True)
    ok &= assert_eq("scan_tag:end", end, len(raw))
    # T3-2 find_articles + parse_attrs
    src = ('<article class="q" id="a-001" data-topic="rag" data-title="测试题" data-kw="a b">'
           '正文<details class="qa"><summary class="qu">Q:x</summary>'
           '<div class="an">y</div></details></article>')
    arts = find_articles(src)
    ok &= assert_eq("find_articles:count", len(arts), 1)
    ok &= assert_eq("find_articles:attrs-id", arts[0][1]["id"], "a-001")
    ok &= assert_eq("find_articles:attrs-title", arts[0][1]["data-title"], "测试题")
    ok &= assert_eq("find_articles:body", "正文" in arts[0][2], True)
    # T3-3 多 article 顺序与嵌套 details 不受影响
    src2 = src + '<article id="b-002" data-topic="sys">B</article>'
    arts2 = find_articles(src2)
    ok &= assert_eq("find_articles:order", [a[1]["id"] for a in arts2], ["a-001", "b-002"])
    # T3-4 未闭合报错
    try:
        find_articles('<article id="x">没有闭合')
        ok &= assert_eq("find_articles:unclosed-raises", False, True)
    except ValueError:
        ok &= assert_eq("find_articles:unclosed-raises", True, True)
    # T3-5 topics.json 加载
    topics = load_topics()
    ok &= assert_eq("load_topics:count", len(topics), 8)
    ok &= assert_eq("load_topics:order", [t["code"] for t in topics], TOPIC_CODES)
    return ok


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    print("build/migrate 未接线(后续任务实现);当前可用:python build.py --selftest")
    sys.exit(2)
```

- [ ] **Step 2: 运行自测**

Run: `python build.py --selftest`
Expected: 全部 `PASS`(T3-1~T3-5 共 10 行 PASS),退出码 0。
若 T3-5 失败,先确认 Task 1 的 topics.json 已存在且顺序正确。

- [ ] **Step 3: Commit**

```bash
git add build.py
git commit -m "feat: build.py骨架:引号感知标签扫描器+selftest框架"
```

---

### Task 4: build.py:题目文件解析(article 属性 + MCQ JSON)

**Files:**
- Modify: `build.py`(在 `# ==== selftest ====` 行之前插入解析函数;在 selftest() 内追加用例)

- [ ] **Step 1: 在 `# ==== selftest ====` 行之前插入以下代码**

```python
# ==== 题目源文件解析 ====


MCQ_RE = re.compile(r'<script\s+type="application/json"\s*>\s*(\[.*?\])\s*</script>', re.S)


def parse_question_file(path, text):
    """解析单个题目源文件,返回 dict;失败抛 ValueError(带文件名)。"""
    arts = find_articles(text)
    if len(arts) != 1:
        raise ValueError("%s: 文件必须恰好包含 1 个 <article>,实际 %d 个" % (path, len(arts)))
    tag, attrs, body = arts[0]
    for need in ("id", "data-topic", "data-title", "data-kw"):
        if need not in attrs:
            raise ValueError("%s: article 缺少 %s 属性" % (path, need))
    mcq = []
    m = MCQ_RE.search(text)
    if m:
        try:
            mcq = json.loads(m.group(1))
        except json.JSONDecodeError as e:
            raise ValueError("%s: MCQ JSON 解析失败: %s" % (path, e))
    tags = [t.strip() for t in attrs.get("data-tags", "").split(",") if t.strip()]
    return {
        "file": str(path),
        "id": attrs["id"],
        "topic": attrs["data-topic"],
        "title": attrs.get("data-title", ""),
        "kw": attrs.get("data-kw", ""),
        "difficulty": attrs.get("data-difficulty", ""),
        "tags": tags,
        "company": attrs.get("data-company", ""),
        "source": attrs.get("data-source", ""),
        "html": body.strip("\n"),
        "mcq": mcq,
    }


def scan_questions(qdir):
    """扫描目录下所有 .html,返回题目 dict 列表(按路径排序)。"""
    out = []
    for f in sorted(qdir.rglob("*.html")):
        out.append(parse_question_file(f, f.read_text(encoding="utf-8")))
    return out
```

- [ ] **Step 2: 在 selftest() 里 `return ok` 之前追加用例**

```python
    # ---- T4 题目文件解析 ----
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        good = ('<article class="q" id="rag-001" data-topic="rag" data-title="题一" '
                'data-difficulty="3" data-tags="RAG,缓存" data-company="字节" '
                'data-source="P1Q1" data-kw="rag 缓存">\n<body>内容\n</article>\n'
                '<script type="application/json">\n[{"q":"x?","options":["a","b","c","d"],'
                '"answer":1,"explain":"e"}]\n</script>\n')
        (tdp / "rag-001.html").write_text(good, encoding="utf-8")
        q = parse_question_file(tdp / "rag-001.html", good)
        ok &= assert_eq("parse:id", q["id"], "rag-001")
        ok &= assert_eq("parse:tags", q["tags"], ["RAG", "缓存"])
        ok &= assert_eq("parse:html", "内容" in q["html"], True)
        ok &= assert_eq("parse:mcq", q["mcq"][0]["answer"], 1)
        # 无 MCQ 的文件允许(返回空列表)
        nomcq = good.split("<script")[0]
        q2 = parse_question_file(tdp / "rag-001.html", nomcq)
        ok &= assert_eq("parse:no-mcq", q2["mcq"], [])
        # 两个 article 报错
        try:
            parse_question_file(tdp / "bad.html", good + good)
            ok &= assert_eq("parse:two-articles-raises", False, True)
        except ValueError:
            ok &= assert_eq("parse:two-articles-raises", True, True)
        # 坏 JSON 报错
        bad = good.replace('"answer":1', '"answer":1,')
        try:
            parse_question_file(tdp / "bad.html", bad)
            ok &= assert_eq("parse:bad-json-raises", False, True)
        except ValueError:
            ok &= assert_eq("parse:bad-json-raises", True, True)
        # 缺属性报错
        try:
            parse_question_file(tdp / "bad.html", '<article id="x">y</article>')
            ok &= assert_eq("parse:missing-attr-raises", False, True)
        except ValueError:
            ok &= assert_eq("parse:missing-attr-raises", True, True)
```

注意:`import tempfile` 只允许出现在 selftest 函数内部(保持 build() 主流程零多余依赖观感),`Path` 已在文件顶部导入。

- [ ] **Step 3: 运行自测**

Run: `python build.py --selftest`
Expected: 之前的用例 + T4 新用例(7 个)全部 PASS,退出码 0。

- [ ] **Step 4: Commit**

```bash
git add build.py
git commit -m "feat: build.py:题目源文件解析(元数据属性+MCQ JSON)"
```

---

### Task 5: build.py:全量校验规则

**Files:**
- Modify: `build.py`(在 `# ==== selftest ====` 之前插入 validate;selftest 追加用例)

- [ ] **Step 1: 在 `# ==== selftest ====` 行之前插入**

```python
# ==== 校验 ====


def validate(questions, topics):
    """返回 (errors, warnings)。errors 非空则拒绝构建。"""
    errors, warnings = [], []
    codes = {t["code"] for t in topics}
    seen = {}
    for q in questions:
        f = q["file"]
        if q["id"] in seen:
            errors.append("%s: id '%s' 重复(已出现在 %s)" % (f, q["id"], seen[q["id"]]))
        else:
            seen[q["id"]] = f
        m = re.match(r"^(%s)-(\d{3})$" % "|".join(TOPIC_CODES), q["id"])
        if not m:
            errors.append("%s: id '%s' 不符合 {主题}-{三位序号} 规范" % (f, q["id"]))
        else:
            expect_tail = "%s/%s.html" % (m.group(1), m.group(2))
            if not str(f).replace("\\", "/").endswith(expect_tail):
                errors.append("%s: id 与文件路径不符(期望位于 %s)" % (f, expect_tail))
            if m.group(1) != q["topic"]:
                errors.append("%s: id 前缀 %s 与 data-topic %s 不一致" % (f, m.group(1), q["topic"]))
        if q["topic"] not in codes:
            errors.append("%s: 未知主题 '%s'" % (f, q["topic"]))
        if q["difficulty"] not in ("1", "2", "3", "4", "5"):
            errors.append("%s: data-difficulty 必须为 1..5,实际 %r" % (f, q["difficulty"]))
        if not q["title"].strip():
            errors.append("%s: data-title 为空" % f)
        if not q["kw"].strip():
            warnings.append("%s: data-kw 为空" % q["id"])
        if not q["mcq"]:
            warnings.append("%s: 无 MCQ" % q["id"])
        for i, item in enumerate(q["mcq"]):
            opts = item.get("options")
            ans = item.get("answer")
            if not isinstance(opts, list) or len(opts) != 4 or \
                    not all(isinstance(o, str) and o.strip() for o in opts):
                errors.append("%s: MCQ#%d options 必须为 4 个非空字符串" % (q["id"], i))
            if not isinstance(ans, int) or ans not in (0, 1, 2, 3):
                errors.append("%s: MCQ#%d answer 必须为 0..3 整数" % (q["id"], i))
            if not str(item.get("q", "")).strip():
                errors.append("%s: MCQ#%d 缺少题干" % (q["id"], i))
            if not str(item.get("explain", "")).strip():
                errors.append("%s: MCQ#%d 缺少 explain" % (q["id"], i))
    return errors, warnings
```

- [ ] **Step 2: 在 selftest() 的 `return ok` 之前追加**

```python
    # ---- T5 校验规则 ----
    def mkq(qid, topic, diff="3", path=None, mcq=None):
        return {"file": path or ("content/questions/%s/%s.html" % (topic, qid.split("-")[1])),
                "id": qid, "topic": topic, "title": "t", "kw": "k", "difficulty": diff,
                "tags": [], "company": "", "source": "", "html": "x",
                "mcq": mcq if mcq is not None else
                [{"q": "q?", "options": ["a", "b", "c", "d"], "answer": 0, "explain": "e"}]}
    topics = load_topics()
    base = [mkq("rag-001", "rag"), mkq("harness-001", "harness")]
    e, w = validate(base, topics)
    ok &= assert_eq("validate:clean", e, [])
    e, w = validate([mkq("rag-001", "rag"), mkq("rag-001", "rag", path="other.html")], topics)
    ok &= assert_eq("validate:dup-id", any("重复" in x for x in e), True)
    e, w = validate([mkq("rag-001", "wrongtopic")], topics)
    ok &= assert_eq("validate:bad-topic", any("未知主题" in x for x in e), True)
    e, w = validate([mkq("rag-001", "rag", diff="6")], topics)
    ok &= assert_eq("validate:bad-diff", any("difficulty" in x for x in e), True)
    e, w = validate([mkq("rag-99", "rag")], topics)
    ok &= assert_eq("validate:bad-id-format", any("三位序号" in x for x in e), True)
    e, w = validate([mkq("sys-001", "rag")], topics)
    ok &= assert_eq("validate:id-topic-mismatch", any("不一致" in x for x in e), True)
    badmcq = [{"q": "q?", "options": ["a", "b", "c"], "answer": 0, "explain": "e"}]
    e, w = validate([mkq("rag-001", "rag", mcq=badmcq)], topics)
    ok &= assert_eq("validate:mcq-options", any("options" in x for x in e), True)
    badans = [{"q": "q?", "options": ["a", "b", "c", "d"], "answer": 9, "explain": "e"}]
    e, w = validate([mkq("rag-001", "rag", mcq=badans)], topics)
    ok &= assert_eq("validate:mcq-answer", any("answer" in x for x in e), True)
    e, w = validate([mkq("rag-001", "rag", mcq=[])], topics)
    ok &= assert_eq("validate:mcq-warn", any("无 MCQ" in x for x in w), True)
```

- [ ] **Step 3: 运行自测**

Run: `python build.py --selftest`
Expected: 全部 PASS(累计 20+ 行),退出码 0。

- [ ] **Step 4: Commit**

```bash
git add build.py
git commit -m "feat: build.py:全量校验(id/主题/难度/MCQ规则)"
```

---

### Task 6: build.py:data.js 生成 + 原子写入 + 构建摘要

**Files:**
- Modify: `build.py`(插入生成函数与 build();__main__ 接线 --selftest 之外的默认分支)
- Create: `data.js`(由 build() 生成,git 提交)

- [ ] **Step 1: 在 `# ==== selftest ====` 之前插入**

```python
# ==== 生成与构建 ====

from datetime import datetime, timezone


def topic_rank(code):
    return TOPIC_CODES.index(code)


def sort_questions(questions):
    return sorted(questions, key=lambda q: (topic_rank(q["topic"]), q["id"]))


def generate_js(questions, topics, top10_html):
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    payload = {
        "version": 1,
        "builtAt": now,
        "topics": [{"code": t["code"], "name": t["name"], "icon": t.get("icon", ""),
                    "order": t["order"], "desc": t.get("desc", "")} for t in topics],
        "top10": top10_html,
        "questions": [
            {"id": q["id"], "topic": q["topic"], "title": q["title"], "kw": q["kw"],
             "difficulty": int(q["difficulty"]), "tags": q["tags"], "company": q["company"],
             "source": q["source"], "html": q["html"], "mcq": q["mcq"]}
            for q in questions
        ],
    }
    return "window.QB = " + json.dumps(payload, ensure_ascii=False,
                                       separators=(",", ":")) + ";\n"


def write_atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def load_top10(extra_dir):
    p = extra_dir / "top10.html"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def summary(questions, warnings, out_path):
    counts, mcq_total = {}, 0
    for q in questions:
        counts[q["topic"]] = counts.get(q["topic"], 0) + 1
        if q["mcq"]:
            mcq_total += 1
    print("== build summary ==")
    for code in TOPIC_CODES:
        print("  %-8s %3d" % (code, counts.get(code, 0)))
    print("  TOTAL    %3d | MCQ %d/%d (%.0f%%) | data.js %.1f KB | warnings %d"
          % (len(questions), mcq_total, len(questions),
             100.0 * mcq_total / max(1, len(questions)),
             out_path.stat().st_size / 1024, len(warnings)))
    for w in warnings:
        print("  warn: %s" % w)


def build(out=OUT, qdir=QDIR, extra=EXTRA):
    if not qdir.exists():
        raise SystemExit("missing %s ; run --migrate first" % qdir)
    topics = load_topics()
    questions = scan_questions(qdir)
    errors, warnings = validate(questions, topics)
    if errors:
        print("== validate FAILED (%d) ==" % len(errors))
        for e in errors:
            print("  error: %s" % e)
        raise SystemExit(1)
    questions = sort_questions(questions)
    write_atomic(out, generate_js(questions, topics, load_top10(extra)))
    summary(questions, warnings, out)
    print("wrote %s" % out)
```

- [ ] **Step 2: 替换 `__main__` 块**(接上 build 默认分支)

把文件末尾的:

```python
if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    print("build/migrate 未接线(后续任务实现);当前可用:python build.py --selftest")
    sys.exit(2)
```

替换为:

```python
if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    build()
```

(`--migrate` 在 Task 7 接线。)

- [ ] **Step 3: 在 selftest() 的 `return ok` 之前追加**

```python
    # ---- T6 生成与原子写 ----
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        qdir = tdp / "questions"
        (qdir / "rag").mkdir(parents=True)
        (qdir / "harness").mkdir(parents=True)
        (qdir / "rag" / "001.html").write_text(
            '<article class="q" id="rag-001" data-topic="rag" data-title="R1" data-kw="k" '
            'data-difficulty="2" data-tags="a" data-source="P1Q1">A</article>', encoding="utf-8")
        (qdir / "rag" / "002.html").write_text(
            '<article class="q" id="rag-002" data-topic="rag" data-title="R2" data-kw="k" '
            'data-difficulty="5" data-tags="a" data-source="P1Q2">B</article>', encoding="utf-8")
        (qdir / "harness" / "001.html").write_text(
            '<article class="q" id="harness-001" data-topic="harness" data-title="H1" '
            'data-kw="k" data-difficulty="1" data-tags="a" data-source="P2Q1">C</article>',
            encoding="utf-8")
        out = tdp / "data.js"
        build(out=out, qdir=qdir, extra=tdp / "extra")
        js = out.read_text(encoding="utf-8")
        ok &= assert_eq("build:prefix", js.startswith("window.QB = "), True)
        ok &= assert_eq("build:order", js.index('"rag-001"') < js.index('"rag-002"') <
                        js.index('"harness-001"'), True)
        ok &= assert_eq("build:tmp-cleaned", (tdp / "data.js.tmp").exists(), False)
```

注意:selftest 里调用 `build(out=..., qdir=..., extra=...)` 会打印 summary 到控制台(中文可能乱码,不影响断言)。

- [ ] **Step 4: 运行自测**

Run: `python build.py --selftest`
Expected: 全部 PASS,退出码 0。

- [ ] **Step 5: Commit**

```bash
git add build.py
git commit -m "feat: build.py:data.js生成/原子写入/构建摘要"
```

---

### Task 7: build.py:--migrate(160 题主题映射 + 旧站拆分 + 零丢失校验)

**Files:**
- Modify: `build.py`(插入 OLD2TOPIC 映射与 migrate();__main__ 接线 --migrate)
- Create: `content/questions/**`(迁移产物)、`content/extra/top10.html`

- [ ] **Step 1: 在 `# ==== selftest ====` 之前插入主题映射表**

映射依据:每题的标题+关键词做内容归类(设计文档第 4 节的 8 主题;160 题全量)。
预计分布:rag 12 / harness 32 / ctx 8 / infer 7 / eval 30 / sys 10 / algo 37 / biz 24。

```python
# ==== 旧站题目 → v2 主题映射(160 题,按内容归类) ====


OLD2TOPIC = {
    # P1 系统设计(15)
    "p1q1": "rag", "p1q2": "harness", "p1q3": "rag", "p1q4": "harness",
    "p1q5": "sys", "p1q6": "infer", "p1q7": "ctx", "p1q8": "algo",
    "p1q9": "eval", "p1q10": "sys", "p1q11": "eval", "p1q12": "sys",
    "p1q13": "rag", "p1q14": "biz", "p1q15": "rag",
    # P2 工程实践(12)
    "p2q1": "rag", "p2q2": "biz", "p2q3": "rag", "p2q4": "ctx",
    "p2q5": "infer", "p2q6": "sys", "p2q7": "rag", "p2q8": "harness",
    "p2q9": "sys", "p2q10": "infer", "p2q11": "harness", "p2q12": "rag",
    # P3 框架选型(10)
    "p3q1": "biz", "p3q2": "biz", "p3q3": "biz", "p3q4": "infer",
    "p3q5": "infer", "p3q6": "biz", "p3q7": "biz", "p3q8": "biz",
    "p3q9": "biz", "p3q10": "biz",
    # P4 业务落地(8)
    "p4q1": "eval", "p4q2": "eval", "p4q3": "eval", "p4q4": "biz",
    "p4q5": "biz", "p4q6": "biz", "p4q7": "ctx", "p4q8": "biz",
    # P5 Agent核心(52)
    "p5q1": "harness", "p5q2": "harness", "p5q3": "harness", "p5q4": "ctx",
    "p5q5": "harness", "p5q6": "algo", "p5q7": "biz", "p5q8": "biz",
    "p5q9": "harness", "p5q10": "harness", "p5q11": "harness", "p5q12": "harness",
    "p5q13": "biz", "p5q14": "rag", "p5q15": "algo", "p5q16": "sys",
    "p5q17": "sys", "p5q18": "harness", "p5q19": "harness", "p5q20": "ctx",
    "p5q21": "ctx", "p5q22": "harness", "p5q23": "algo", "p5q24": "infer",
    "p5q25": "harness", "p5q26": "biz", "p5q27": "sys", "p5q28": "harness",
    "p5q29": "harness", "p5q30": "sys", "p5q31": "infer", "p5q32": "harness",
    "p5q33": "eval", "p5q34": "sys", "p5q35": "harness", "p5q36": "harness",
    "p5q37": "eval", "p5q38": "harness", "p5q39": "algo", "p5q40": "harness",
    "p5q41": "ctx", "p5q42": "eval", "p5q43": "algo", "p5q44": "harness",
    "p5q45": "harness", "p5q46": "biz", "p5q47": "eval", "p5q48": "eval",
    "p5q49": "biz", "p5q50": "algo", "p5q51": "harness", "p5q52": "algo",
    # P6 算法岗(45)
    "p6q1": "rag", "p6q2": "ctx", "p6q3": "rag", "p6q4": "harness",
    "p6q5": "rag", "p6q6": "algo", "p6q7": "algo", "p6q8": "algo",
    "p6q9": "harness", "p6q10": "harness", "p6q11": "algo", "p6q12": "algo",
    "p6q13": "algo", "p6q14": "algo", "p6q15": "algo", "p6q16": "algo",
    "p6q17": "algo", "p6q18": "algo", "p6q19": "algo", "p6q20": "algo",
    "p6q21": "algo", "p6q22": "algo", "p6q23": "algo", "p6q24": "algo",
    "p6q25": "algo", "p6q26": "algo", "p6q27": "algo", "p6q28": "eval",
    "p6q29": "eval", "p6q30": "eval", "p6q31": "eval", "p6q32": "eval",
    "p6q33": "eval", "p6q34": "eval", "p6q35": "eval", "p6q36": "eval",
    "p6q37": "eval", "p6q38": "algo", "p6q39": "algo", "p6q40": "algo",
    "p6q41": "algo", "p6q42": "algo", "p6q43": "algo", "p6q44": "algo",
    "p6q45": "algo",
    # P7 评估与开放讨论(18)
    "p7q1": "eval", "p7q2": "eval", "p7q3": "eval", "p7q4": "eval",
    "p7q5": "eval", "p7q6": "eval", "p7q7": "eval", "p7q8": "eval",
    "p7q9": "eval", "p7q10": "eval", "p7q11": "harness", "p7q12": "harness",
    "p7q13": "algo", "p7q14": "biz", "p7q15": "harness", "p7q16": "biz",
    "p7q17": "biz", "p7q18": "biz",
}
```

- [ ] **Step 2: 在 OLD2TOPIC 之后插入 migrate 函数**

```python
def migrate(old_index=None, out_root=None, id_map=None):
    """从旧 index.html 拆出题目源文件 + top10 片段,并当场校验。
    返回 {旧id: 新id} 映射。id_map 参数供 selftest 注入小映射。"""
    id_map = id_map if id_map is not None else OLD2TOPIC
    old = Path(old_index) if old_index else ROOT / "index.html"
    out_root = Path(out_root) if out_root else CONTENT
    src = old.read_text(encoding="utf-8")
    arts = find_articles(src)
    questions = [a for a in arts if a[1].get("id") in id_map]
    tops = [a for a in arts if "top10" in a[0]]
    if len(questions) != len(id_map):
        found = {a[1]["id"] for a in questions}
        missing = sorted(set(id_map) - found)
        raise SystemExit("old ids mismatch: found=%d map=%d missing=%s"
                         % (len(questions), len(id_map), missing))
    # 依文档顺序分配新 id(主题内递增)
    counter, old2new, old2len = {}, {}, {}
    for tag, attrs, body in questions:
        code = id_map[attrs["id"]]
        counter[code] = counter.get(code, 0) + 1
        old2new[attrs["id"]] = "%s-%03d" % (code, counter[code])
        old2len[attrs["id"]] = len(body)
    qdir = out_root / "questions"
    for tag, attrs, body in questions:
        oid = attrs["id"]
        code = id_map[oid]
        nid = old2new[oid]
        stars = re.search(r'class="stars"[^>]*>([^<]*)<', body)
        diff = (stars.group(1).count("⭐") if stars else 0) or 3
        tags = [t.lstrip("#").strip() for t in
                re.findall(r'class="tag">([^<]*)</span>', body)]
        comp = re.search(r'class="company">([^<]*)</span>', body)
        company = comp.group(1) if comp else ""
        part, num = oid[1:].split("q")
        new_tag = ('<article class="q" id="%s" data-topic="%s" data-title="%s" '
                   'data-difficulty="%d" data-tags="%s" data-company="%s" '
                   'data-source="P%sQ%s" data-kw="%s">') % (
            nid, code, attrs.get("data-title", ""), diff, ",".join(tags), company,
            part, num, attrs.get("data-kw", ""))
        p = qdir / code / (nid.split("-")[1] + ".html")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(new_tag + "\n" + body.rstrip() + "\n</article>\n", encoding="utf-8")
    # top10:重写旧锚点链接为 SPA 路由
    extra_dir = out_root / "extra"
    if tops:
        tag, attrs, body = tops[0]
        for oid, nid in old2new.items():
            body = body.replace('href="#%s"' % oid, 'href="#/q/%s"' % nid)
        extra_dir.mkdir(parents=True, exist_ok=True)
        (extra_dir / "top10.html").write_text(
            '<article class="q top10" id="top10" data-title="%s" data-kw="%s">\n%s\n</article>\n'
            % (attrs.get("data-title", "高频Top10速记"), attrs.get("data-kw", ""), body.strip()),
            encoding="utf-8")
    # 产物校验 + 零丢失校验(标题清单、正文长度)
    topics = load_topics()
    qs = scan_questions(qdir)
    errors, warnings = validate(qs, topics)
    if errors:
        for e in errors:
            print("  error: %s" % e)
        raise SystemExit("migrate output validate FAILED")
    old_titles = sorted(a[1].get("data-title", "") for a in questions)
    new_titles = sorted(q["title"] for q in qs)
    if old_titles != new_titles:
        raise SystemExit("migrate title parity FAILED")
    for q in qs:
        expect = old2len["p%sq%s" % (q["source"][1], q["source"].split("Q")[1])] \
            if q["source"].startswith("P") else None
        if expect is not None and abs(len(q["html"]) - expect) > 16:
            raise SystemExit("migrate body-length parity FAILED at %s" % q["id"])
    counts = {}
    for q in qs:
        counts[q["topic"]] = counts.get(q["topic"], 0) + 1
    print("== migrate done ==")
    for code in TOPIC_CODES:
        print("  %-8s %3d" % (code, counts.get(code, 0)))
    print("  TOTAL %d | top10 %d | parity ok" % (len(qs), 1 if tops else 0))
    return old2new
```

- [ ] **Step 3: __main__ 接线 --migrate**

```python
if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    if "--migrate" in sys.argv:
        migrate()
    else:
        build()
```

- [ ] **Step 4: 在 selftest() 的 `return ok` 之前追加**

```python
    # ---- T7 migrate(小型夹具:含 p2q4 式标题、stars、company、top10 链接重写) ----
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        mini = (
            '<article class="q" id="p1q1" data-title="设计RAG系统" data-kw="rag">'
            '<span class="stars">⭐⭐⭐⭐⭐</span><span class="tag">#系统设计</span>'
            '<span class="company">字节(高频)</span>正文A</article>'
            '<article class="q" id="p1q2" data-title="设计MultiAgent" data-kw="ma">'
            '<span class="stars">⭐⭐</span>正文B</article>'
            '<article class="q" id="p2q4" data-title="如何处理Agent的长对话场景(>10轮)" '
            'data-kw="长对话">正文C</article>'
            '<article class="q top10" id="t1" data-title="高频Top10速记" data-kw="top10">'
            '<a href="#p1q1">完整版</a></article>'
        )
        oldf = tdp / "old.html"
        oldf.write_text(mini, encoding="utf-8")
        m = {"p1q1": "rag", "p1q2": "harness", "p2q4": "ctx"}
        old2new = migrate(oldf, tdp / "content", m)
        ok &= assert_eq("migrate:newids", old2new,
                        {"p1q1": "rag-001", "p1q2": "harness-001", "p2q4": "ctx-001"})
        q1 = (tdp / "content" / "questions" / "rag" / "001.html").read_text(encoding="utf-8")
        ok &= assert_eq("migrate:attrs", 'data-difficulty="5"' in q1 and
                        'data-source="P1Q1"' in q1 and 'data-company="字节(高频)"' in q1, True)
        ok &= assert_eq("migrate:tags", 'data-tags="系统设计"' in q1, True)
        ok &= assert_eq("migrate:gt-title", "(>10轮)" in
                        (tdp / "content" / "questions" / "ctx" / "001.html").read_text(encoding="utf-8"), True)
        t10 = (tdp / "content" / "extra" / "top10.html").read_text(encoding="utf-8")
        ok &= assert_eq("migrate:top10-link", 'href="#/q/rag-001"' in t10, True)
```

- [ ] **Step 5: 运行自测**

Run: `python build.py --selftest`
Expected: 全部 PASS(含 migrate 用例),退出码 0。

- [ ] **Step 6: Commit**

```bash
git add build.py
git commit -m "feat: build.py:--migrate旧站拆分(160题主题映射/零丢失校验/top10链接重写)"
```

---

### Task 8: 执行真实迁移 + 构建 data.js

**Files:**
- Create: `content/questions/`(160 个文件)、`content/extra/top10.html`、`data.js`

- [ ] **Step 1: 确认工作树干净**

Run: `git status --short`
Expected: 空输出(有未提交内容先处理再继续)。

- [ ] **Step 2: 跑迁移**

Run: `python build.py --migrate`
Expected 输出(数字必须一致,否则停下来排查):
```
== migrate done ==
  rag         12
  harness     32
  ctx          8
  infer        7
  eval        30
  sys         10
  algo        37
  biz         24
  TOTAL 160 | top10 1 | parity ok
```

- [ ] **Step 3: 跑构建**

Run: `python build.py`
Expected: 同样的分主题计数;`MCQ 0/160 (0%)`(正常,P3 才补 MCQ);`data.js` 约 1100~1400 KB;`warnings 160`(全部为"无 MCQ",预期内);`wrote data.js`。

- [ ] **Step 4: 抽查迁移产物**

用 Read 工具看 `content/questions/rag/001.html` 开头,确认:
- article 属性齐全:`id="rag-001" data-topic="rag" data-title="设计日均100万查询的企业级RAG系统" data-difficulty="5" ... data-source="P1Q1"`
- 五个 block(fast/ana/det/fol/term)都在
- `content/questions/ctx/001.html` 的 data-title 完整含 `(>10轮)`

- [ ] **Step 5: Commit**

```bash
git add content/questions content/extra/top10.html data.js
git commit -m "feat: 旧站160题迁移至content/questions(8主题)+首次构建data.js"
```

---

### Task 9: v2 SPA 壳(index.html 整体替换:骨架 + 样式迁移 + 启动守卫)

**Files:**
- Modify: `index.html`(整体替换)

- [ ] **Step 1: 提取旧样式块**

用 Read 工具读当前 index.html 第 7–192 行(从 `<style>` 到 `</style>`),记为 **STYLE_BLOCK**。此块包含:深浅色变量、卡片/徽章/表格/代码块/details.qa/SVG 架构图全套样式、响应式与打印规则。**先复制留存,下一步用。**

- [ ] **Step 2: 整体替换 index.html**

用 write 工具写入新 index.html,结构如下;`<style>` 中先原样粘贴 **STYLE_BLOCK**,再追加"应用层新增"代码块:

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Agent 面试题库 v2 · 一页一题</title>
<style>
/* =====▼ 此处粘贴 STYLE_BLOCK(旧 7–192 行全部样式,原样) ▼===== */

/* ===== 应用层新增(v2) ===== */
.nav-count{float:right;color:var(--muted);font-size:11px}
.sync-badge{font-size:11.5px;padding:2px 10px;border-radius:999px;border:1px solid var(--border);cursor:pointer;white-space:nowrap;background:var(--card);color:var(--muted)}
.sb-local{color:var(--amber);border-color:var(--amber)}
.sb-dirty{color:var(--amber);border-color:var(--amber)}
.sb-synced{color:var(--teal);border-color:var(--teal)}
.sb-error{color:var(--rose);border-color:var(--rose)}
.sync-menu{position:absolute;top:52px;right:20px;z-index:60;background:var(--card);border:1px solid var(--border);border-radius:12px;padding:8px;display:none;box-shadow:0 8px 24px rgba(15,23,42,.12);min-width:190px}
.sync-menu.open{display:block}
.sync-menu button{display:block;width:100%;text-align:left;padding:8px 12px;border:none;background:none;color:var(--text);font-size:13px;border-radius:8px;cursor:pointer;font-family:inherit}
.sync-menu button:hover{background:var(--accent-light)}
.sync-menu .sep{height:1px;background:var(--border);margin:6px 0}
#view{padding:26px 0 100px 30px;min-width:0;max-width:900px}
.crumb{font-size:13px;color:var(--muted);margin-bottom:10px}
.crumb a{color:var(--accent);text-decoration:none}
.qnav{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:18px 0}
.qnav button{padding:8px 16px;border:1.5px solid var(--border);background:var(--card);color:var(--text);border-radius:999px;cursor:pointer;font-size:13.5px;font-family:inherit}
.qnav button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
.qnav button:disabled{opacity:.4;cursor:default}
.qpos{color:var(--muted);font-size:13px}
.study-panel{position:sticky;bottom:0;margin-top:20px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:16px 18px;box-shadow:0 -4px 20px rgba(15,23,42,.08)}
.level-btns{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.lv-btn{padding:7px 16px;border-radius:999px;border:1.5px solid var(--border);background:var(--card);color:var(--muted);font-size:13.5px;cursor:pointer;font-family:inherit}
.lv-btn.on-0{background:var(--term-bg);border-color:var(--slate);color:var(--slate)}
.lv-btn.on-1{background:var(--rose-bg);border-color:var(--rose);color:var(--rose)}
.lv-btn.on-2{background:var(--amber-bg);border-color:var(--amber);color:var(--amber)}
.lv-btn.on-3{background:var(--teal-bg);border-color:var(--teal);color:var(--teal)}
.kbd{font-size:11px;color:var(--muted);opacity:.7}
.notebox{width:100%;min-height:96px;border:1.5px solid var(--border);border-radius:10px;background:var(--bg);color:var(--text);padding:10px 12px;font-size:14px;font-family:inherit;line-height:1.7;resize:vertical;outline:none}
.notebox:focus{border-color:var(--accent)}
.note-meta{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-top:6px}
.ring-wrap{display:flex;align-items:center;gap:18px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:18px 22px;margin:18px 0}
.ring-num{font-size:28px;font-weight:800}
.ring-lbl{font-size:13px;color:var(--muted)}
.topic-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.topic-card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:16px 18px;text-decoration:none;color:var(--text);display:block;transition:border-color .15s}
.topic-card:hover{border-color:var(--accent)}
.topic-card h3{font-size:16px;margin-bottom:6px}
.topic-card .desc{font-size:12.8px;color:var(--muted);margin-bottom:10px;line-height:1.6;min-height:44px}
.tbar{height:6px;border-radius:3px;background:var(--term-bg);overflow:hidden;margin:8px 0 6px}
.tbar>i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),#a855f7)}
.topic-card .meta-row{display:flex;justify-content:space-between;font-size:12px;color:var(--muted)}
.qlist{display:flex;flex-direction:column;gap:8px}
.qrow{display:flex;align-items:center;gap:12px;padding:11px 14px;background:var(--card);border:1px solid var(--border);border-radius:12px;text-decoration:none;color:var(--text)}
.qrow:hover{border-color:var(--accent)}
.qrow .no{font-size:12px;color:var(--accent);font-weight:700;min-width:44px}
.qrow .t{flex:1;font-size:14.5px;line-height:1.5}
.qrow .lvdot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.lv0{background:#c3c8d4}.lv1{background:var(--rose)}.lv2{background:var(--amber)}.lv3{background:var(--teal)}
.filter-row{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 16px}
.fchip{padding:5px 14px;border-radius:999px;border:1.5px solid var(--border);background:var(--card);color:var(--muted);font-size:12.5px;cursor:pointer;font-family:inherit}
.fchip.on{border-color:var(--accent);color:var(--accent);background:var(--accent-light)}
#search-pop{position:absolute;top:52px;left:50%;transform:translateX(-50%);width:min(560px,90vw);background:var(--card);border:1px solid var(--border);border-radius:12px;box-shadow:0 10px 30px rgba(15,23,42,.15);display:none;z-index:70;max-height:60vh;overflow-y:auto;padding:6px}
#search-pop.open{display:block}
.sr-item{display:block;padding:9px 12px;border-radius:8px;text-decoration:none;color:var(--text);font-size:13.8px}
.sr-item:hover{background:var(--accent-light)}
.sr-item small{color:var(--muted);margin-right:8px;font-size:11.5px}
.empty{color:var(--muted);text-align:center;padding:40px 0}
.continue-row{display:block;margin:14px 0;text-decoration:none}
@media (max-width:960px){#view{padding-left:0}}
</style>
<script>
(function(){try{var t=localStorage.getItem('ib-theme');if(!t){t=(window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light';}if(t==='dark'){document.documentElement.setAttribute('data-theme','dark');}}catch(e){}})();
</script>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs><marker id="sd-arr" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#9aa0ae"/></marker></defs></svg>
<div id="progress"></div>

<header class="topbar">
  <div class="topbar-inner">
    <button id="btn-menu" title="目录">☰</button>
    <a class="brand" href="#/">AI Agent 面试题库 v2<small>160题 · 8主题 · 一页一题</small></a>
    <input id="search" type="text" placeholder="搜索:如 缓存 / LangGraph / 幻觉 / PPO …">
    <span id="sync-badge" class="sync-badge" title="连接笔记文件">连接笔记</span>
    <span id="count"></span>
    <button class="btn-print" id="btn-print">导出PDF</button>
    <button class="btn-theme" id="btn-theme" title="切换深色/浅色模式" aria-label="切换深色/浅色模式">
      <svg class="icon-moon" viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>
      <svg class="icon-sun" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4"/></svg>
    </button>
  </div>
</header>

<div class="layout">
  <nav id="sidenav"></nav>
  <main id="view"></main>
</div>

<div id="sync-menu" class="sync-menu">
  <button data-a="connect">连接 notes.json(仓库文件)</button>
  <button data-a="write">立即写入 notes.json</button>
  <div class="sep"></div>
  <button data-a="export">导出备份(JSON 下载)</button>
  <button data-a="import">导入备份(JSON 文件)</button>
</div>
<input type="file" id="file-import" accept=".json" style="display:none">
<div id="search-pop"></div>
<button id="backtop" title="回到顶部">↑</button>
<div id="boot-error" style="display:none;background:#fff;border:1px solid #e5e7eb;max-width:540px;margin:80px auto;padding:30px;border-radius:14px;text-align:center;font-family:sans-serif">
  <h2 style="margin-bottom:10px">data.js 未加载</h2>
  <p style="color:#6b7280;line-height:1.8">题库数据缺失或损坏。请在项目根目录运行<br>
  <code style="background:#eef2ff;color:#4f46e5;padding:2px 8px;border-radius:6px">python build.py</code><br>
  重新生成后刷新本页。</p>
</div>

<script src="data.js"></script>
<script>
(function(){
'use strict';
if(!window.QB || !window.QB.questions || !window.QB.topics){
  document.getElementById('boot-error').style.display = 'block';
  return;
}
var QB = window.QB;
function $(s, r){ return (r || document).querySelector(s); }
function $$(s, r){ return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
function el(tag, cls, html){ var e = document.createElement(tag); if(cls) e.className = cls; if(html != null) e.innerHTML = html; return e; }
function esc(s){ return String(s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
function isoNow(){ return new Date().toISOString(); }
function nn(qid){ return parseInt(qid.split('-')[1], 10); }
var byId = {}, topicByCode = {}, byTopic = {};
QB.questions.forEach(function(q){ byId[q.id] = q; });
QB.topics.forEach(function(t){ topicByCode[t.code] = t; });
QB.questions.forEach(function(q){ (byTopic[q.topic] = byTopic[q.topic] || []).push(q); });
var TOTAL = QB.questions.length;
/* ===== APPEND-STORE ===== */
/* ===== APPEND-ROUTER ===== */
/* ===== APPEND-BOOT ===== */
})();
</script>
</body>
</html>
```

说明:brand 从 div 改为 `<a href="#/">`;`#sync-badge` 文案由 FileSync 状态接管(初始"连接笔记");boot-error 用内联样式保证无数据时也可见;`#search-pop`/`#sync-menu`/`#file-import` 静态放置。

- [ ] **Step 3: 静态自检**

Run: `python build.py --selftest`(确保没动 build)
再检查新 index.html 存在且包含关键锚点:

```bash
git diff --stat index.html
```
Expected: 显示 index.html 大幅缩减(约 13400 行 → 约 500 行,不含 data.js 内容)。
用 grep 确认新文件同时含有:`id="sd-arr"`、`APPEND-STORE`、`boot-error`、`class="notebox"`。

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "feat: v2 SPA壳:index.html整体替换(样式迁移/启动守卫/应用层骨架)"
```

---

### Task 10: 数据层(Store localStorage 镜像 + FileSync 文件同步)

**Files:**
- Modify: `index.html`(在 `/* ===== APPEND-STORE ===== */` 行处替换为以下代码)

- [ ] **Step 1: 用以下内容替换 `/* ===== APPEND-STORE ===== */` 行**

```js
/* ---------- Store:localStorage 全量镜像 ---------- */
var LS_KEY = 'ib-notes-v2';
var Store = {
  data: { version: 1, updatedAt: null, last: null, items: {} },
  load: function(){
    try {
      var raw = localStorage.getItem(LS_KEY);
      if(!raw) return;
      var d = JSON.parse(raw);
      if(d && d.items){ d.last = d.last || null; d.version = d.version || 1; this.data = d; }
    } catch(e){}
  },
  persist: function(){
    this.data.updatedAt = isoNow();
    try { localStorage.setItem(LS_KEY, JSON.stringify(this.data)); } catch(e){}
    FileSync.onStoreChange();
  },
  item: function(qid){
    if(!this.data.items[qid]) this.data.items[qid] = { level: 0, note: '', updated: null, history: [] };
    return this.data.items[qid];
  },
  level: function(qid){ var it = this.data.items[qid]; return it ? it.level : 0; },
  setLevel: function(qid, level, mode){
    var it = this.item(qid);
    it.level = level; it.updated = isoNow();
    it.history.push({ t: isoNow(), mode: mode || 'manual', level: level });
    if(it.history.length > 60) it.history = it.history.slice(-60);
    this.persist();
  },
  setNote: function(qid, note){ var it = this.item(qid); if(it.note === note) return; it.note = note; it.updated = isoNow(); this.persist(); },
  visit: function(qid){ this.data.last = qid; this.persist(); },
  merge: function(other){
    var changed = false, a = this.data.items, b = (other && other.items) || {};
    Object.keys(b).forEach(function(k){
      var na = a[k] && a[k].updated, nb = b[k] && b[k].updated;
      if(!a[k] || (nb && (!na || na < nb))){ a[k] = b[k]; changed = true; }
    });
    if(other && other.last){ if(!this.data.last) { this.data.last = other.last; changed = true; } }
    return changed;
  }
};

/* ---------- FileSync:notes.json 直写(File System Access API) ---------- */
var FileSync = {
  available: ('showOpenFilePicker' in window),
  handle: null, status: 'off', timer: null,
  setStatus: function(s){
    this.status = s;
    var b = $('#sync-badge'); if(!b) return;
    b.className = 'sync-badge' + (s === 'off' ? '' : ' sb-' + s);
    var tips = {
      off: '未连接笔记文件 · 点击连接 notes.json',
      local: '当前浏览器不支持文件直写 · 用导出/导入备份',
      synced: '笔记已同步到 notes.json',
      dirty: '改动将在 3 秒后写入 notes.json',
      error: '写入/读取失败 · 点击徽标打开菜单重试',
      pending: '正在连接…'
    };
    b.title = tips[s] || s;
    b.textContent = ({ off: '连接笔记', local: '本地模式', synced: '已同步', dirty: '未同步', error: '同步出错', pending: '连接中' })[s] || s;
  },
  idb: function(mode, k, v){
    return new Promise(function(res, rej){
      var rq = indexedDB.open('ib-notes', 1);
      rq.onupgradeneeded = function(){ rq.result.createObjectStore('kv'); };
      rq.onerror = function(){ rej(rq.error); };
      rq.onsuccess = function(){
        var db = rq.result;
        try {
          var tx = db.transaction('kv', mode);
          var st = tx.objectStore('kv');
          var op = (mode === 'readonly') ? st.get(k) : st.put(v, k);
          op.onsuccess = function(){ res(op.result); };
          op.onerror = function(){ rej(op.error); };
        } catch(e){ rej(e); }
      };
    });
  },
  connect: function(){
    var self = this;
    if(!this.available){ this.setStatus('local'); return; }
    this.setStatus('pending');
    showOpenFilePicker({ types: [{ description: '题库笔记', accept: { 'application/json': ['.json'] } }] })
      .then(function(arr){ self.handle = arr[0]; return self.idb('readwrite', 'notes-handle', arr[0]); })
      .then(function(){ return self.readMerge(); })
      .then(function(){ self.setStatus('synced'); })
      .catch(function(e){ self.setStatus(e && e.name === 'AbortError' ? 'off' : 'error'); });
  },
  autoAttach: function(){
    var self = this;
    if(!this.available){ this.setStatus('local'); return Promise.resolve('local'); }
    return this.idb('readonly', 'notes-handle', undefined)
      .then(function(h){
        if(!h){ self.setStatus('off'); return 'off'; }
        self.handle = h;
        return h.queryPermission({ mode: 'readwrite' }).then(function(p){
          if(p === 'granted'){ return self.readMerge().then(function(){ self.setStatus('synced'); return 'synced'; }); }
          self.setStatus('off'); return 'off';
        });
      })
      .catch(function(){ self.setStatus('off'); return 'off'; });
  },
  readMerge: function(){
    var self = this;
    if(!this.handle) return Promise.resolve(false);
    return this.handle.getFile()
      .then(function(f){ return f.text(); })
      .then(function(txt){
        var d = {};
        try { d = txt ? JSON.parse(txt) : {}; } catch(e){ self.setStatus('error'); return false; }
        var changed = Store.merge(d);
        if(changed){ Store.persist(); refreshAfterStore(); }
        return true;
      })
      .catch(function(){ self.setStatus('error'); return false; });
  },
  onStoreChange: function(){
    if(!this.handle){ this.setStatus(this.available ? 'off' : 'local'); return; }
    this.setStatus('dirty');
    var self = this;
    clearTimeout(this.timer);
    this.timer = setTimeout(function(){ self.writeNow(); }, 3000);
  },
  writeNow: function(){
    var self = this;
    if(!this.handle) return;
    this.handle.createWritable()
      .then(function(w){ return w.write(JSON.stringify(Store.data)).then(function(){ return w.close(); }); })
      .then(function(){ self.setStatus('synced'); })
      .catch(function(){ self.setStatus('error'); });
  },
  flush: function(){ if(this.timer){ clearTimeout(this.timer); this.timer = null; this.writeNow(); } },
  export: function(){
    var blob = new Blob([JSON.stringify(Store.data)], { type: 'application/json' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'notes-export-' + Date.now() + '.json';
    a.click();
    setTimeout(function(){ URL.revokeObjectURL(a.href); }, 2000);
  },
  importFile: function(file){
    var self = this;
    file.text().then(function(txt){
      var d;
      try { d = JSON.parse(txt); } catch(e){ throw e; }
      if(!d || !d.items) throw new Error('bad');
      if(Store.merge(d)){ Store.persist(); refreshAfterStore(); }
      self.setStatus(self.handle ? 'dirty' : 'local');
    }).catch(function(){ alert('导入失败:文件不是有效的 notes.json 备份'); });
  }
};
```

- [ ] **Step 2: 浏览器冒烟(主会话执行)**

1. `python -m http.server 8761`(workdir=D:\agent-interview,后台)
2. playwright 打开 `http://127.0.0.1:8761/index.html`——此时无路由(boot 后 view 为空),但**控制台无 JS 报错**(用 Console Messages 确认)
3. evaluate:`typeof Store + '/' + typeof FileSync + '/' + FileSync.available`(期望 `object/object/true`,Chromium 下)
4. evaluate:`Store.data.version`(期望 1)

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat: v2数据层:Store镜像+FileSync文件同步(File System Access API)"
```

---

### Task 11: 路由 + 侧边栏 + 仪表盘

**Files:**
- Modify: `index.html`(替换 `/* ===== APPEND-ROUTER ===== */` 行)

- [ ] **Step 1: 用以下内容替换 `/* ===== APPEND-ROUTER ===== */` 行**

```js
/* ---------- 侧边栏 ---------- */
function buildNav(){
  var h = '<div class="nav-part">导航</div><a href="#/" data-nav="#/">仪表盘</a>'
        + '<div class="nav-part">主题</div>';
  QB.topics.forEach(function(t){
    var qs = byTopic[t.code] || [];
    var done = qs.filter(function(q){ return Store.level(q.id) >= 2; }).length;
    h += '<a href="#/topic/' + t.code + '" data-nav="#/topic/' + t.code + '"'
       + ' data-topicnav="' + t.code + '">' + t.icon + ' ' + esc(t.name)
       + '<span class="nav-count">' + done + '/' + qs.length + '</span></a>';
  });
  $('#sidenav').innerHTML = h;
}
function setActiveNav(hash){
  var topicHash = hash;
  var mq = hash.match(/^#\/q\/([\w-]+)$/);
  if(mq && byId[mq[1]]) topicHash = '#/topic/' + byId[mq[1]].topic;
  $$('#sidenav a').forEach(function(a){
    a.classList.toggle('active', a.getAttribute('data-nav') === topicHash);
  });
}
function refreshNav(){
  $$('#sidenav a[data-topicnav]').forEach(function(a){
    var c = a.getAttribute('data-topicnav');
    var qs = byTopic[c] || [];
    var done = qs.filter(function(q){ return Store.level(q.id) >= 2; }).length;
    var s = a.querySelector('.nav-count');
    if(s) s.textContent = done + '/' + qs.length;
  });
}

/* ---------- 视图:仪表盘 ---------- */
function renderHome(){
  var mastered = QB.questions.filter(function(q){ return Store.level(q.id) >= 2; }).length;
  var lv = [0, 0, 0, 0];
  QB.questions.forEach(function(q){ lv[Store.level(q.id)]++; });
  var pct = TOTAL ? mastered / TOTAL : 0, C = 2 * Math.PI * 52;
  var h = '<section class="hero"><h1>AI Agent 面试题库 v2</h1>'
        + '<p>8 主题 · ' + TOTAL + ' 题 · 一页一题,笔记与熟练度自动同步到 notes.json。</p>'
        + '<div class="pill-row"><span class="pill">每天刷几题</span><span class="pill">错题标模糊</span>'
        + '<span class="pill">考前看 Top10</span></div></section>';
  h += '<div class="ring-wrap"><svg width="120" height="120" viewBox="0 0 120 120">'
     + '<circle cx="60" cy="60" r="52" fill="none" stroke="var(--term-bg)" stroke-width="10"/>'
     + '<circle cx="60" cy="60" r="52" fill="none" stroke="var(--accent)" stroke-width="10" stroke-linecap="round" stroke-dasharray="' + (C * pct).toFixed(1) + ' ' + C.toFixed(1) + '" transform="rotate(-90 60 60)"/>'
     + '<text x="60" y="66" text-anchor="middle" font-size="22" font-weight="800" fill="var(--accent)">' + Math.round(pct * 100) + '%</text></svg>'
     + '<div><div class="ring-num">' + mastered + ' / ' + TOTAL + '</div>'
     + '<div class="ring-lbl">已掌握(熟练 + 精通)</div>'
     + '<div class="ring-lbl" style="margin-top:6px">未学 ' + lv[0] + ' · 模糊 ' + lv[1] + ' · 熟练 ' + lv[2] + ' · 精通 ' + lv[3] + '</div></div></div>';
  if(Store.data.last && byId[Store.data.last]){
    var lq = byId[Store.data.last];
    h += '<a class="qrow continue-row" href="#/q/' + lq.id + '"><span class="no">继续</span>'
       + '<span class="t">上次看到:' + esc(lq.title) + '</span>'
       + '<span class="lvdot lv' + Store.level(lq.id) + '"></span></a>';
  }
  h += '<div class="topic-cards">';
  QB.topics.forEach(function(t){
    var qs = byTopic[t.code] || [];
    var done = qs.filter(function(q){ return Store.level(q.id) >= 2; }).length;
    var p = qs.length ? done / qs.length : 0;
    h += '<a class="topic-card" href="#/topic/' + t.code + '"><h3>' + t.icon + ' ' + esc(t.name) + '</h3>'
       + '<div class="desc">' + esc(t.desc) + '</div>'
       + '<div class="tbar"><i style="width:' + Math.round(p * 100) + '%"></i></div>'
       + '<div class="meta-row"><span>已掌握 ' + done + ' / ' + qs.length + '</span><span>' + Math.round(p * 100) + '%</span></div></a>';
  });
  h += '</div>';
  view.innerHTML = h;
}

/* ---------- 视图:404 ---------- */
function render404(){
  view.innerHTML = '<div class="empty">页面不存在<br><br><a style="color:var(--accent)" href="#/">回仪表盘</a></div>';
}

/* ---------- 路由 ---------- */
var view = $('#view');
function route(){
  window.__ibKeyHandler = null;
  FileSync.flush();
  var h = location.hash || '#/';
  if(h === '#/' || h === '#' || h === '') renderHome();
  else if(h.indexOf('#/topic/') === 0) renderTopic(h.slice('#/topic/'.length));
  else if(h.indexOf('#/q/') === 0) renderQuestion(h.slice('#/q/'.length));
  else render404();
  setActiveNav(h);
  window.scrollTo(0, 0);
  updateProgress();
}
```

- [ ] **Step 2: 用以下内容替换 `/* ===== APPEND-BOOT ===== */` 行**(渲染函数齐全前不启动)

```js
/* ---------- 公共 ---------- */
function refreshAfterStore(){
  refreshNav();
  var h = location.hash || '#/';
  if(h === '#/' || h === '#' || h === ''){ renderHome(); return; }
}
function updateProgress(){
  var d = document.documentElement;
  var p = (d.scrollTop / Math.max(1, d.scrollHeight - d.clientHeight)) * 100;
  $('#progress').style.width = p + '%';
  $('#backtop').classList.toggle('show', d.scrollTop > 600);
}
function refreshCount(n){ $('#count').textContent = n + ' / ' + TOTAL + ' 题'; }

/* ---------- 启动 ---------- */
Store.load();
buildNav();
route();
refreshCount(TOTAL);
FileSync.autoAttach();
$('#btn-theme').onclick = function(){
  var r = document.documentElement;
  var toDark = r.getAttribute('data-theme') !== 'dark';
  if(toDark) r.setAttribute('data-theme', 'dark'); else r.removeAttribute('data-theme');
  try { localStorage.setItem('ib-theme', toDark ? 'dark' : 'light'); } catch(e){}
};
$('#btn-print').onclick = function(){ window.print(); };
$('#backtop').onclick = function(){ window.scrollTo({ top: 0, behavior: 'smooth' }); };
$('#btn-menu').onclick = function(){ $('#sidenav').classList.toggle('open'); };
$('#sidenav').addEventListener('click', function(e){
  if(e.target.tagName === 'A' && window.innerWidth <= 960) $('#sidenav').classList.remove('open');
});
window.addEventListener('hashchange', route);
window.addEventListener('scroll', updateProgress);
window.addEventListener('pagehide', function(){ FileSync.flush(); });
document.addEventListener('keydown', function(e){ if(window.__ibKeyHandler) window.__ibKeyHandler(e); });
```

- [ ] **Step 3: 临时接线占位视图(本步为让站点可打开)**

在 route() 的 topic/question 分支未实现前,先在 route() 前面临时插入:

```js
function renderTopic(code){ view.innerHTML = '<div class="empty">主题视图(Task 12)</div>'; }
function renderQuestion(qid){ view.innerHTML = '<div class="empty">题目视图(Task 13)</div>'; }
```

这两个函数是可提交的中间态(站点能打开、无报错),在 Task 12/13 会被完整实现替换。

- [ ] **Step 4: 浏览器冒烟(主会话执行)**

1. 刷新 `http://127.0.0.1:8761/index.html`
2. 期望:仪表盘渲染——进度环 0%,8 张主题卡(12/32/8/7/30/10/37/24 计数正确),侧边栏 8 主题带 `0/N`
3. 点击"RAG 检索增强生成"→ URL `#/topic/rag`,显示临时占位;浏览器后退回仪表盘正常
4. 控制台无报错

- [ ] **Step 5: Commit**

```bash
git add index.html
git commit -m "feat: v2路由/侧边栏/仪表盘(进度环+主题卡+继续上次)"
```

---

### Task 12: 主题视图(过滤/列表) + 全局搜索

**Files:**
- Modify: `index.html`(替换 Task 11 的临时 renderTopic;新增搜索逻辑)

- [ ] **Step 1: 用以下内容替换临时 renderTopic**

```js
function renderTopic(code){
  var t = topicByCode[code];
  if(!t){ render404(); return; }
  var qs = byTopic[code] || [];
  var h = '<div class="crumb"><a href="#/">仪表盘</a> › ' + t.icon + ' ' + esc(t.name) + '</div>'
        + '<h2 class="part-title">' + t.icon + ' ' + esc(t.name)
        + '<span style="font-size:14px;color:var(--muted);font-weight:400">' + qs.length + ' 题</span></h2>'
        + '<p class="part-sub">' + esc(t.desc) + '</p>'
        + '<div class="filter-row" id="filter-row"></div>'
        + '<div class="qlist" id="qlist"></div>';
  view.innerHTML = h;
  buildTopicList(code, 'all');
  var defs = [['all', '全部'], ['unseen', '未学'], ['1', '模糊'], ['2', '熟练'], ['3', '精通'], ['seen', '已学']];
  $('#filter-row').innerHTML = defs.map(function(d){
    return '<button class="fchip' + (d[0] === 'all' ? ' on' : '') + '" data-f="' + d[0] + '">' + d[1] + '</button>';
  }).join('');
  $('#filter-row').addEventListener('click', function(e){
    var b = e.target.closest('.fchip'); if(!b) return;
    buildTopicList(code, b.getAttribute('data-f'));
  });
}
function buildTopicList(code, filter){
  var qs = (byTopic[code] || []).slice();
  if(filter === 'unseen') qs = qs.filter(function(q){ return Store.level(q.id) === 0; });
  else if(filter === 'seen') qs = qs.filter(function(q){ return Store.level(q.id) > 0; });
  else if(filter !== 'all') qs = qs.filter(function(q){ return Store.level(q.id) === +filter; });
  qs.sort(function(a, b){ return nn(a.id) - nn(b.id); });
  var row = $('#qlist'); if(!row) return;
  row.innerHTML = qs.length ? '' : '<div class="empty">该筛选下没有题目</div>';
  qs.forEach(function(q){
    var lv = Store.level(q.id);
    var a = el('a', 'qrow');
    a.href = '#/q/' + q.id;
    a.innerHTML = '<span class="no">' + ('' + nn(q.id)).padStart(3, '0') + '</span>'
                + '<span class="t">' + esc(q.title) + '</span>'
                + '<span style="color:#f59e0b;font-size:12px;white-space:nowrap">' + '⭐'.repeat(q.difficulty) + '</span>'
                + '<span class="lvdot lv' + lv + '"></span>';
    row.appendChild(a);
  });
  $$('#filter-row .fchip').forEach(function(c){ c.classList.toggle('on', c.getAttribute('data-f') === filter); });
}
```

- [ ] **Step 2: 在 APPEND-BOOT 区块末尾追加搜索逻辑**

```js
/* ---------- 全局搜索 ---------- */
var pop = $('#search-pop');
$('#search').addEventListener('input', function(){
  var kw = this.value.trim().toLowerCase();
  if(!kw){ pop.classList.remove('open'); pop.innerHTML = ''; refreshCount(TOTAL); return; }
  var hits = QB.questions.filter(function(q){
    return (q.title + ' ' + q.kw + ' ' + q.source + ' ' + q.id).toLowerCase().indexOf(kw) !== -1;
  }).slice(0, 20);
  pop.innerHTML = hits.length ? '' : '<div class="empty" style="padding:18px 0">没有匹配的题目</div>';
  hits.forEach(function(q){
    var a = el('a', 'sr-item');
    a.href = '#/q/' + q.id;
    a.innerHTML = '<small>' + q.id + '</small>' + esc(q.title);
    pop.appendChild(a);
  });
  pop.classList.add('open');
  refreshCount(hits.length);
});
pop.addEventListener('click', function(e){
  if(e.target.closest('a')){ pop.classList.remove('open'); $('#search').value = ''; refreshCount(TOTAL); }
});
document.addEventListener('click', function(e){
  if(!e.target.closest('#search-pop') && e.target !== $('#search')) pop.classList.remove('open');
});
```

- [ ] **Step 3: 浏览器冒烟(主会话执行)**

1. 刷新 → 进 `#/topic/rag`:列表 12 行,编号 001–012,难度星正确
2. 点"未学"过滤→12 行(全未学);点"精通"→空态文案
3. 顶栏搜"幻觉"→浮层出现若干条(应含 biz 主题的幻觉治理题);点一条→进入题目占位视图
4. 控制台无报错

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "feat: v2主题视图(熟练度过滤/列表)+全局搜索"
```

---

### Task 13: 题目视图(一页一题 + 熟练度 + 笔记 + 键盘切题)

**Files:**
- Modify: `index.html`(替换临时 renderQuestion;新增 studyPanel 与 fol-toggle 注入)

- [ ] **Step 1: 用以下内容替换临时 renderQuestion**

```js
function renderQuestion(qid){
  var q = byId[qid];
  if(!q){ render404(); return; }
  Store.visit(q.id);
  var t = topicByCode[q.topic];
  var qs = byTopic[q.topic];
  var i = qs.indexOf(q);
  var h = '<div class="crumb"><a href="#/">仪表盘</a> › <a href="#/topic/' + q.topic + '">'
        + t.icon + ' ' + esc(t.name) + '</a> › 第 ' + nn(q.id) + ' 题</div>'
        + '<article class="q">' + q.html + '</article>'
        + '<div class="qnav"><button id="btn-prev"' + (i === 0 ? ' disabled' : '') + '>← 上一题</button>'
        + '<span class="qpos">' + (i + 1) + ' / ' + qs.length + '</span>'
        + '<button id="btn-next"' + (i === qs.length - 1 ? ' disabled' : '') + '>下一题 →</button></div>'
        + studyPanelHTML(q);
  view.innerHTML = h;
  injectFolToggles();
  $('#btn-prev').onclick = function(){ if(i > 0) location.hash = '#/q/' + qs[i - 1].id; };
  $('#btn-next').onclick = function(){ if(i < qs.length - 1) location.hash = '#/q/' + qs[i + 1].id; };
  bindStudyPanel(q);
}

function injectFolToggles(){
  /* 复刻旧站:为每个"面试官追问"块注入 展开/收起全部 按钮 */
  $$('#view .block.fol').forEach(function(fol){
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'fol-toggle';
    btn.textContent = '展开全部追问';
    btn.addEventListener('click', function(){
      var ds = $$('.qa', fol);
      var anyClosed = ds.some(function(d){ return !d.open; });
      ds.forEach(function(d){ d.open = anyClosed; });
      btn.textContent = anyClosed ? '收起全部追问' : '展开全部追问';
    });
    var badge = fol.querySelector('.badge');
    if(badge) badge.insertAdjacentElement('afterend', btn);
  });
}

function studyPanelHTML(q){
  var it = Store.data.items[q.id] || { level: 0, note: '' };
  var names = ['未学', '模糊', '熟练', '精通'];
  var btns = names.map(function(n, ix){
    return '<button class="lv-btn' + (it.level === ix ? ' on-' + ix : '') + '" data-lv="' + ix + '">'
         + n + ' <span class="kbd">' + (ix + 1) + '</span></button>';
  }).join('');
  return '<div class="study-panel"><div class="level-btns" id="level-btns">' + btns + '</div>'
       + '<textarea class="notebox" id="notebox" placeholder="记下你的理解、口播要点、踩过的坑…(自动保存,同步到 notes.json)">' + esc(it.note || '') + '</textarea>'
       + '<div class="note-meta"><span>←/→ 切题 · 数字键 1-4 评熟练度</span><span id="note-status"></span></div></div>';
}

function bindStudyPanel(q){
  $('#level-btns').addEventListener('click', function(e){
    var b = e.target.closest('.lv-btn'); if(!b) return;
    setLevelUI(q, +b.getAttribute('data-lv'), 'manual');
  });
  var box = $('#notebox'), timer = null;
  box.addEventListener('input', function(){
    clearTimeout(timer);
    timer = setTimeout(function(){ Store.setNote(q.id, box.value); noteStatus(); }, 400);
  });
  window.__ibKeyHandler = function(e){
    if(e.target && (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT')) return;
    if(e.key === 'ArrowLeft'){ var p = $('#btn-prev'); if(p && !p.disabled) p.click(); }
    else if(e.key === 'ArrowRight'){ var nx = $('#btn-next'); if(nx && !nx.disabled) nx.click(); }
    else if(e.key >= '1' && e.key <= '4'){ setLevelUI(q, +e.key - 1, 'key'); }
  };
  noteStatus();
}

function setLevelUI(q, lv, mode){
  Store.setLevel(q.id, lv, mode);
  $$('#level-btns .lv-btn').forEach(function(b, i){
    b.className = 'lv-btn' + (i === lv ? ' on-' + lv : '');
  });
  noteStatus(); refreshNav();
}
function noteStatus(){
  var s = $('#note-status'); if(!s) return;
  s.textContent = ({ synced: '笔记已同步', dirty: '保存中…' })[FileSync.status] || '本地保存';
}
```

- [ ] **Step 2: 扩充 refreshAfterStore(主题页色点/题目页按钮随外部合并刷新)**

用以下内容替换 BOOT 区块里的 refreshAfterStore:

```js
function refreshAfterStore(){
  refreshNav();
  var h = location.hash || '#/';
  if(h === '#/' || h === '#' || h === ''){ renderHome(); return; }
  if(h.indexOf('#/topic/') === 0){
    $$('#qlist .qrow').forEach(function(r){
      var id = (r.getAttribute('href') || '').split('/q/')[1];
      if(!id || !byId[id]) return;
      var dot = r.querySelector('.lvdot');
      if(dot) dot.className = 'lvdot lv' + Store.level(id);
    });
    return;
  }
  var mq = h.match(/^#\/q\/([\w-]+)$/);
  if(mq && byId[mq[1]]){
    var q = byId[mq[1]];
    var it = Store.data.items[q.id];
    $$('#level-btns .lv-btn').forEach(function(b, i){
      b.className = 'lv-btn' + (it && i === it.level ? ' on-' + it.level : '');
    });
    noteStatus();
  }
}
```

- [ ] **Step 3: 浏览器冒烟(主会话执行)**

1. `#/q/rag-001`:题卡完整渲染(五模块、SVG 架构图箭头正常、追问可折叠、"展开全部追问"按钮工作)
2. 面包屑可点回主题页;上一题禁用/下一题→`#/q/rag-002`
3. 键盘:←/→ 切题;按 `3` → 熟练按钮高亮、侧边栏 RAG 计数 +1、徽标变"未同步"
4. 在笔记框输入"我会先算容量账"→ 等 1 秒,`note-status` 显示"保存中…";localStorage(`ib-notes-v2`)含该笔记
5. 刷新页面重进 `#/q/rag-001`:熟练度"熟练"仍高亮、笔记文字仍在(数据未丢)
6. 控制台无报错

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "feat: v2题目视图:一页一题+熟练度四档+笔记+键盘切题+追问折叠注入"
```

---

### Task 14: 同步徽标菜单 + 导入导出兜底

**Files:**
- Modify: `index.html`(BOOT 区块末尾追加菜单交互)

- [ ] **Step 1: 在 BOOT 区块末尾追加**

```js
/* ---------- 同步菜单 ---------- */
var menu = $('#sync-menu');
$('#sync-badge').onclick = function(e){ e.stopPropagation(); menu.classList.toggle('open'); };
document.addEventListener('click', function(e){
  if(!e.target.closest('#sync-menu') && e.target !== $('#sync-badge')) menu.classList.remove('open');
});
menu.addEventListener('click', function(e){
  var b = e.target.closest('button'); if(!b) return;
  menu.classList.remove('open');
  var a = b.getAttribute('data-a');
  if(a === 'connect') FileSync.connect();
  else if(a === 'write') FileSync.writeNow();
  else if(a === 'export') FileSync.export();
  else if(a === 'import') $('#file-import').click();
});
$('#file-import').addEventListener('change', function(){
  if(this.files && this.files[0]) FileSync.importFile(this.files[0]);
  this.value = '';
});
```

- [ ] **Step 2: 浏览器验证文件直写闭环(主会话执行;file:// 直写需真人点一次授权,或按下方路径)**

自动化可测的部分:
1. evaluate:`FileSync.available === true`(Chromium)
2. evaluate 注入 mock 句柄验证写回管线:
   ```js
   (async () => {
     let written = '';
     const fake = { getFile: async () => new File([JSON.stringify({version:1,items:{}})], 'notes.json'),
                     createWritable: async () => ({ write: async (t) => { written = t; }, close: async () => {} }),
                     queryPermission: async () => 'granted' };
     FileSync.handle = fake;
     Store.setLevel('rag-001', 3, 'test');
     return new Promise(r => setTimeout(() => r({ status: FileSync.status, hasNote: JSON.parse(written).items['rag-001'] !== undefined }), 3600));
   })()
   ```
   期望:`{status:"synced", hasNote:true}`(3 秒防抖后 mock 句柄收到完整 JSON)
3. 真人路径(留给用户):双击打开 index.html → 点徽标 → 连接 notes.json → 授权 → 徽标变"已同步";改动任意笔记后 4 秒,磁盘上 `notes.json` 出现且内容含该笔记(用 Read 工具确认后,`git add notes.json && git commit`)

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat: v2同步菜单(连接/立即写入/导出/导入)+mock句柄直写验证"
```

---

### Task 15: P1 端到端验收 + 收尾提交

**Files:**
- Modify: `notes.json`(用户首次连接后产生;若本轮未产生,跳过)

- [ ] **Step 1: 全量自动化回归**

Run: `python build.py --selftest && python build.py`
Expected: selftest 全 PASS;build 摘要 8 主题计数正确、`parity ok` 输出同 Task 8。

- [ ] **Step 2: 端到端浏览器验收(主会话执行,逐项打勾)**

1. 打开 `#/`——仪表盘进度环、8 主题卡、继续上次
2. `#/topic/harness` 32 行;过滤"未学"→32;评 2 档后回过滤"已学"→1
3. `#/q/harness-032`:SVG 图渲染、追问展开/收起、`←/→`、`1-4` 评档、笔记自动保存、同步徽标流转(off→dirty→synced)
4. 搜索"LangGraph"→ 浮层命中多题;点入正常
5. 深色切换正常且刷新保持;移动端 390px 宽布局不破(汉堡菜单可开合)
6. `#/q/不存在` → 404 视图;`#/topic/xxx` → 404 视图
7. 直接删掉 data.js 再刷新 → boot-error 提示可见(测完 `python build.py` 重建)
8. 随机抽 5 题对比旧版内容(git show HEAD~N:index.html 或旧 data)确认无内容缺失

- [ ] **Step 3: 收尾提交**

```bash
git add -A
git status --short   # 人工确认无意外文件(如 .playwright-mcp/ 应删除不提交)
git commit -m "chore: v2 P1地基端到端验收通过"
```

验收标准(设计文档 P1):双击可用、160 题内容+SVG 零丢失、笔记/熟练度/notes.json 同步链路全部工作。

---

## 计划外事项(明确不做 / 留给后续计划)

- P2:四种考题模式、#/notes 笔记总览、#/top10 视图(data.js 已含 top10 数据)
- P3:add-question skill(`.opencode/skill/add-question/SKILL.md`)、160 题 MCQ 补写(rag→harness→infer→其余,每批 15~25 题)
- P4:README 重写、.fragments/ 与 assemble.ps1 删除、旧版说明
- 不做:Node 工具链、部署 Pages、多用户/云同步、SM-2 间隔重复
- 与设计文档 12 节的一处有意偏差:notes.json 解析失败时**不自动写 notes.json.bak**
  (浏览器无法在未授权情况下写第二个文件)。兜底不变:localStorage 全量镜像仍在、
  导出备份可用、菜单"立即写入"可用 localStorage 内容覆盖坏文件——数据不会丢。
