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
            if not isinstance(item, dict):
                errors.append("%s: MCQ#%d 不是对象" % (f, i + 1))
                continue
            opts = item.get("options")
            ans = item.get("answer")
            stem = item.get("q")
            exp = item.get("explain")
            if not isinstance(opts, list) or len(opts) != 4 or \
                    not all(isinstance(o, str) and o.strip() for o in opts) or \
                    len(set(opts)) != 4:
                errors.append("%s: MCQ#%d options 必须为 4 个非空且不重复的字符串" % (f, i + 1))
            if not isinstance(ans, int) or isinstance(ans, bool) or ans not in (0, 1, 2, 3):
                errors.append("%s: MCQ#%d answer 必须为 0..3 整数" % (f, i + 1))
            if not isinstance(stem, str) or not stem.strip():
                errors.append("%s: MCQ#%d 缺少题干" % (f, i + 1))
            if not isinstance(exp, str) or not exp.strip():
                errors.append("%s: MCQ#%d 缺少 explain" % (f, i + 1))
    return errors, warnings


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
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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
    try:
        questions = scan_questions(qdir)
    except ValueError as e:
        print("== parse FAILED ==")
        print("  error: %s" % e)
        raise SystemExit(1)
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


def migrate(old_index=None, out_root=None, id_map=None):
    """从旧 index.html 拆出题目源文件 + top10 片段,并当场校验。
    返回 {旧id: 新id} 映射。id_map 参数供 selftest 注入小映射。"""
    id_map = id_map if id_map is not None else OLD2TOPIC
    old = Path(old_index) if old_index else ROOT / "index.html"
    out_root = Path(out_root) if out_root else CONTENT
    qdir = out_root / "questions"
    if qdir.exists() and any(qdir.iterdir()):
        raise SystemExit("content/questions already exists; --migrate is one-shot (delete the tree to redo)")
    src = old.read_text(encoding="utf-8")
    arts = find_articles(src)
    questions = [a for a in arts if a[1].get("id") in id_map]
    tops = [a for a in arts if "top10" in a[1].get("class", "")]
    if len(questions) != len(id_map):
        found = {a[1]["id"] for a in questions}
        missing = sorted(set(id_map) - found)
        raise SystemExit("old ids mismatch: found=%d map=%d missing=%s"
                         % (len(questions), len(id_map), missing))
    others = [a for a in arts
              if a[1].get("id") not in id_map and "top10" not in a[1].get("class", "")]
    if others:
        raise SystemExit("unmapped articles found: %s" % [a[1].get("id") for a in others])
    # 依文档顺序分配新 id(主题内递增)
    counter, old2new, old2len = {}, {}, {}
    for tag, attrs, body in questions:
        code = id_map[attrs["id"]]
        counter[code] = counter.get(code, 0) + 1
        old2new[attrs["id"]] = "%s-%03d" % (code, counter[code])
        old2len[attrs["id"]] = len(body)
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
    def mkq(qid, topic, diff="3", path=None, mcq=None, title="t", kw="k"):
        return {"file": path or ("content/questions/%s/%s.html" % (topic, qid.split("-")[1])),
                "id": qid, "topic": topic, "title": title, "kw": kw, "difficulty": diff,
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
    e, w = validate([mkq("rag-001", "rag", mcq=["oops"])], topics)
    ok &= assert_eq("validate:mcq-non-dict", any("不是对象" in x for x in e), True)
    nullexp = [{"q": "q?", "options": ["a", "b", "c", "d"], "answer": 0, "explain": None}]
    e, w = validate([mkq("rag-001", "rag", mcq=nullexp)], topics)
    ok &= assert_eq("validate:mcq-null-explain", any("explain" in x for x in e), True)
    nullstem = [{"q": None, "options": ["a", "b", "c", "d"], "answer": 0, "explain": "e"}]
    e, w = validate([mkq("rag-001", "rag", mcq=nullstem)], topics)
    ok &= assert_eq("validate:mcq-null-stem", any("题干" in x for x in e), True)
    dupopts = [{"q": "q?", "options": ["a", "a", "a", "a"], "answer": 0, "explain": "e"}]
    e, w = validate([mkq("rag-001", "rag", mcq=dupopts)], topics)
    ok &= assert_eq("validate:mcq-dup-options", any("options" in x for x in e), True)
    strans = [{"q": "q?", "options": ["a", "b", "c", "d"], "answer": "2", "explain": "e"}]
    e, w = validate([mkq("rag-001", "rag", mcq=strans)], topics)
    ok &= assert_eq("validate:mcq-string-answer", any("answer" in x for x in e), True)
    e, w = validate([mkq("rag-001", "rag", title="   ")], topics)
    ok &= assert_eq("validate:empty-title", any("data-title" in x for x in e), True)
    e, w = validate([mkq("rag-001", "rag", kw="")], topics)
    ok &= assert_eq("validate:empty-kw-warn", any("data-kw" in x for x in w), True)
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
        p = json.loads(js[len("window.QB = "):-2])
        ok &= assert_eq("build:roundtrip", [q["id"] for q in p["questions"]],
                        ["rag-001", "rag-002", "harness-001"])
        ok &= assert_eq("build:difficulty-int", p["questions"][0]["difficulty"], 2)
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
        try:
            migrate(oldf, tdp / "content", m)
            ok &= assert_eq("migrate:rerun-guard", False, True)
        except SystemExit:
            ok &= assert_eq("migrate:rerun-guard", True, True)
    return ok


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    if "--migrate" in sys.argv:
        migrate()
    else:
        build()
