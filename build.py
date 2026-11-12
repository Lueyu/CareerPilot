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
    raise ValueError("unterminated tag at offset %d: %s" % (start, src[start:start + 80]))


def parse_attrs(tag):
    """解析标签属性为 dict(只支持双引号属性,值保持原样)。"""
    return {m.group(1): m.group(2) for m in re.finditer(r'([\w-]+)="([^"]*)"', tag)}


def find_articles(src):
    """返回 [(tag, attrs, body)],按文档顺序;article 必须显式 </article> 闭合。
    正文内出现字面 <article 视为错误(嵌套会静默截断内容,必须报错)。"""
    out = []
    pos = 0
    while True:
        s = src.find("<article", pos)
        if s == -1:
            return out
        nxt = src[s + 8:s + 9]
        if nxt not in (" ", "\t", "\r", "\n", ">", "/"):
            pos = s + 1
            continue
        tag, after = scan_tag(src, s)
        e = src.find("</article>", after)
        if e == -1:
            raise ValueError("article 未闭合: %s" % tag[:80])
        body = src[after:e]
        if "<article" in body:
            raise ValueError("嵌套/字面 <article 检出,正文可能被截断: %s" % tag[:80])
        out.append((tag, parse_attrs(tag), body))
        pos = e + len("</article>")


def load_topics():
    data = json.loads((CONTENT / "topics.json").read_text(encoding="utf-8"))
    return sorted(data, key=lambda t: t["order"])


# ==== 题目源文件解析 ====


MCQ_RE = re.compile(r'<script\s+type="application/json"\s*>\s*(\[.*?\])\s*</script>', re.S)


def parse_question_file(path, text):
    """解析单个题目源文件,返回 dict;失败抛 ValueError(带文件名)。
    注意:content/extra/top10.html 是片段不是题目文件,不适用本函数(缺 data-topic 会报错,属预期)。"""
    try:
        arts = find_articles(text)
    except ValueError as e:
        raise ValueError("%s: %s" % (path, e))
    if len(arts) != 1:
        raise ValueError("%s: 文件必须恰好包含 1 个 <article>,实际 %d 个" % (path, len(arts)))
    if text.count("</article>") != 1:
        raise ValueError("%s: 检出字面 </article>(可能截断正文),示例中请写 &lt;/article&gt;" % path)
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
            if not isinstance(ans, int) or isinstance(ans, bool) or ans not in (0, 1, 2, 3):
                errors.append("%s: MCQ#%d answer 必须为 0..3 整数" % (q["id"], i))
            if not str(item.get("q", "")).strip():
                errors.append("%s: MCQ#%d 缺少题干" % (q["id"], i))
            if not str(item.get("explain", "")).strip():
                errors.append("%s: MCQ#%d 缺少 explain" % (q["id"], i))
    return errors, warnings


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
    ok &= assert_eq("scan_tag:end", raw[end:], "正文")
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
        ok &= assert_eq("parse:html", q["html"], "<body>内容")
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
        # 扫描器错误必须带文件路径
        try:
            parse_question_file(tdp / "bad.html",
                                '<article id="a">正文<article id="b">X</article>尾部</article>')
            ok &= assert_eq("parse:scanner-error-path", False, True)
        except ValueError as ex:
            ok &= assert_eq("parse:scanner-error-path",
                            str(ex).startswith(str(tdp / "bad.html")), True)
        # 正文含字面 </article> 拒绝(防静默截断)
        try:
            parse_question_file(tdp / "bad.html", '<article id="c">A</article> 尾巴</article>')
            ok &= assert_eq("parse:literal-close-raises", False, True)
        except ValueError:
            ok &= assert_eq("parse:literal-close-raises", True, True)
        # MCQ 字符串含 ] 不会被截断(lazy 匹配由 </script> 锚定)
        mcqbr = ('<article class="q" id="rag-001" data-topic="rag" data-title="题一" data-kw="k">'
                 '正文</article>\n<script type="application/json">\n[{"q":"x[见]附录?","options":'
                 '["a","b","c","d"],"answer":0,"explain":"e[1]"}]\n</script>\n')
        q3 = parse_question_file(tdp / "rag-001.html", mcqbr)
        ok &= assert_eq("parse:mcq-bracket", q3["mcq"][0]["explain"], "e[1]")
    # ---- T3-6~T3-8 扫描器加固 ----
    arts3 = find_articles('<articlex id="fake">x</articlex><article id="ok">y</article>')
    ok &= assert_eq("find_articles:prefix-skip", [a[1]["id"] for a in arts3], ["ok"])
    try:
        find_articles('<article id="a">正文<pre class="code">&lt;article&gt;</pre></article>')
        ok &= assert_eq("find_articles:escaped-ok", True, True)
    except ValueError:
        ok &= assert_eq("find_articles:escaped-ok", False, True)
    try:
        find_articles('<article id="a">正文<article id="b">X</article>尾部</article>')
        ok &= assert_eq("find_articles:nested-raises", False, True)
    except ValueError:
        ok &= assert_eq("find_articles:nested-raises", True, True)
    try:
        scan_tag('<article id="a" data-x="未', 0)
        ok &= assert_eq("scan_tag:unterminated-raises", False, True)
    except ValueError as ex:
        ok &= assert_eq("scan_tag:unterminated-raises", "article" in str(ex), True)
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
    e, w = validate([mkq("rag-002", "rag", path="content/questions/rag/003.html")], topics)
    ok &= assert_eq("validate:path-mismatch", any("id 与文件路径不符" in x for x in e), True)
    badmcq = [{"q": "q?", "options": ["a", "b", "c"], "answer": 0, "explain": "e"}]
    e, w = validate([mkq("rag-001", "rag", mcq=badmcq)], topics)
    ok &= assert_eq("validate:mcq-options", any("options" in x for x in e), True)
    badans = [{"q": "q?", "options": ["a", "b", "c", "d"], "answer": 9, "explain": "e"}]
    e, w = validate([mkq("rag-001", "rag", mcq=badans)], topics)
    ok &= assert_eq("validate:mcq-answer", any("answer" in x for x in e), True)
    boolans = [{"q": "q?", "options": ["a", "b", "c", "d"], "answer": True, "explain": "e"}]
    e, w = validate([mkq("rag-001", "rag", mcq=boolans)], topics)
    ok &= assert_eq("validate:mcq-bool-answer", any("answer" in x for x in e), True)
    e, w = validate([mkq("rag-001", "rag", mcq=[])], topics)
    ok &= assert_eq("validate:mcq-warn", any("无 MCQ" in x for x in w), True)
    return ok


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    print("build/migrate 未接线(后续任务实现);当前可用:python build.py --selftest")
    sys.exit(2)
