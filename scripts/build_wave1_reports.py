"""Build portable Chinese teaching reports from reviewed Wave 1 JSON records."""
from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "docs/known-issues/reports/wave1"
REPO_URL = "https://github.com/Lueyu/CareerPilot"
ORDER = ["E1", "E2", "E3", "G5", "F6", "G6", "H1", "H2", "H8", "H3", "H4", "A2", "G4", "F7", "F8", "G3"]
STYLE = """
:root{color-scheme:light;--ink:#17243b;--muted:#526178;--line:#dce5ed;--accent:#17664c;--paper:#fff;--bg:#f3f6f8}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.85 system-ui,'Microsoft YaHei',sans-serif}main{max-width:1100px;margin:auto;padding:32px 28px 80px}a{color:#165c98;overflow-wrap:anywhere}nav{font-size:15px;margin-bottom:28px}header{background:#123b37;color:#fff;padding:38px;border-radius:18px}header p{color:#d7ebe4;max-width:850px}h1{font-size:clamp(28px,4vw,42px);line-height:1.35;margin:8px 0 18px}h2{font-size:26px;line-height:1.5;margin:42px 0 16px}h3{font-size:20px}p{margin:12px 0}section,.card{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:24px;margin:20px 0}.tag{display:inline-block;background:#dff2e8;color:#174b36;border-radius:6px;padding:2px 10px;font-size:14px}.small{font-size:14px;color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}.grid .card{margin:0}.flow{display:flex;gap:12px;flex-wrap:wrap;align-items:stretch;counter-reset:step}.step{flex:1;min-width:150px;padding:18px;background:#eef5f3;border-top:4px solid var(--accent);border-radius:8px}.step:before{counter-increment:step;content:counter(step);display:block;color:var(--accent);font-weight:bold;font-size:23px}.step span{display:block;font-size:14px;color:var(--muted)}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;min-width:560px}td,th{padding:13px 16px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}th{background:#edf3f6}code{font:14px/1.6 Consolas,monospace;background:#edf2f5;padding:2px 5px;overflow-wrap:anywhere}pre{white-space:pre-wrap;background:#142b39;color:#e5eff6;padding:18px;border-radius:8px;overflow-wrap:anywhere;font:14px/1.65 Consolas,monospace}details{border-top:1px solid var(--line);padding:16px 0}summary{cursor:pointer;font-weight:bold}.boundary{border-left:5px solid #b2731d;background:#fff8eb;padding:15px 20px}.check{border-left:5px solid var(--accent);padding-left:18px}footer{margin-top:36px;color:var(--muted);font-size:14px}@media(max-width:600px){main{padding:16px 12px}header{padding:24px}section{padding:18px}body{font-size:16px}}@media print{body{background:white}main{max-width:none;padding:0}header{background:white;color:var(--ink);border:1px solid var(--line)}header p{color:var(--muted)}section{break-inside:avoid}nav{display:none}details{display:block}}
 p,summary,.step{overflow-wrap:anywhere}section{min-width:0}
"""


def e(value: object) -> str:
    return escape(str(value), quote=True)


def paragraphs(values: list[str]) -> str:
    return "".join(f"<p>{e(value)}</p>" for value in values)


def table(headers: list[str], rows: list[list[str]]) -> str:
    cells = "".join("<tr>" + "".join(f"<td>{e(v)}</td>" for v in row) + "</tr>" for row in rows)
    return '<div class="table-wrap"><table><thead><tr>' + "".join(f"<th>{e(v)}</th>" for v in headers) + "</tr></thead><tbody>" + cells + "</tbody></table></div>"


def page(title: str, body: str) -> str:
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><title>' + e(title) + '</title><style>' + STYLE + '</style><main>' + body + '</main></html>\n'


def render(record: dict) -> str:
    issue = record["id"]
    body = '<nav><a href="index.html">第 1 波报告目录</a> · <a href="../../timeline.html">施工时间线</a></nav>'
    body += f'<header><span class="tag">{e(issue)} · {e(record["status"])}</span><h1>{e(record["title"])}</h1><p>{e(record["goal"])}</p></header>'
    body += '<section><h2>先理解你会遇到什么</h2>' + paragraphs(record["scenario"]) + '</section>'
    body += '<section><h2>修改前后有什么区别</h2>' + table(["观察点", "修改前", "修改后"], record["before_after"]) + '</section>'
    body += '<section><h2>系统现在如何处理</h2><div class="flow">' + ''.join(f'<div class="step"><strong>{e(x[0])}</strong><span>{e(x[1])}</span></div>' for x in record["flow"]) + '</div>' + paragraphs(record["mechanism"]) + '</section>'
    body += '<section><h2>读懂这些概念就能读懂代码</h2>' + table(["概念", "人话解释", "在这里的作用"], record["concepts"]) + '</section>'
    body += '<section><h2>为什么选择这个方案</h2>' + paragraphs(record["rationale"]) + table(["方案", "收益", "代价与决定"], record["alternatives"]) + '</section>'
    body += '<section><h2>具体改了哪些代码</h2>'
    for item in record["files"]:
        path = item["path"]
        body += f'<p><a href="{REPO_URL}/blob/{e(record.get("source_ref", "dev"))}/{e(path)}"><code>{e(path)}</code></a></p><p>{e(item["why"])}</p>'
    body += '</section><section><h2>如何证明修改有效</h2>' + paragraphs(record["verification"]["explanation"])
    body += table(["检查", "实际观察", "证据"], record["verification"]["checks"])
    for command in record["verification"]["commands"]:
        body += f'<pre>{e(command)}</pre>'
    body += '</section><section><h2>仍要知道的边界</h2><div class="boundary">' + paragraphs(record["limits"]) + '</div></section>'
    body += '<section><h2>面试时可以怎样解释</h2>' + paragraphs(record["interview"]) + '</section><section><h2>检验自己是否理解</h2>'
    for item in record["questions"]:
        body += f'<details><summary>{e(item[0])}</summary><p>{e(item[1])}</p></details>'
    body += '</section><section><h2>版本、审核与回退</h2>' + paragraphs(record["delivery"])
    for link in record.get("links", []):
        if not link["href"].startswith(REPO_URL + "/"):
            raise ValueError("Report links must point to this repository")
        body += f'<p><a href="{e(link["href"])}">{e(link["label"])}</a></p>'
    body += '</section>'
    body += '<footer>这是一份修改原理说明。已运行的检查、受控夹具、未验证的真实供应商或部署状态分别记录。报告源 JSON 与生成脚本一起保留，便于更新和复核。</footer>'
    return page(f'{issue} {record["title"]}', body)


def build(check: bool = False, require_all: bool = False) -> None:
    records = []
    for issue in ORDER:
        source = REPORTS / f"{issue}.json"
        if source.exists():
            records.append(json.loads(source.read_text(encoding="utf-8")))
        elif require_all:
            raise SystemExit(f"Missing teaching report: {issue}")
    outputs = {REPORTS / f'{record["id"]}.html': render(record) for record in records}
    cards = ''.join(f'<article class="card"><span class="tag">{e(r["id"])} · {e(r["status"])}</span><h2><a href="{e(r["id"])}.html">{e(r["title"])}</a></h2><p>{e(r["goal"])}</p></article>' for r in records)
    outputs[REPORTS / 'index.html'] = page('CareerPilot 第 1 波教学报告', '<nav><a href="../../timeline.html">施工时间线</a></nav><header><h1>第 1 波 · 止血</h1><p>逐项理解并发、预算、认证、输入安全与运维保障。每篇从事故场景讲到原理、修改、验证和边界。</p></header><div class="grid">' + cards + '</div>')
    for destination, content in outputs.items():
        if check:
            if not destination.exists() or destination.read_text(encoding="utf-8") != content:
                raise SystemExit(f"Stale report: {destination.relative_to(ROOT)}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
    print(f"{'Checked' if check else 'Built'} {len(records)} teaching reports and index")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--require-all', action='store_true')
    args = parser.parse_args()
    build(args.check, args.require_all)
