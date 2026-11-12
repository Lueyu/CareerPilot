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
    return ok


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    print("build/migrate 未接线(后续任务实现);当前可用:python build.py --selftest")
    sys.exit(2)
