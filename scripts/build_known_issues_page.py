#!/usr/bin/env python3
"""从 KNOWN_ISSUES.md 生成 docs/known-issues/index.html 交互看板。

KNOWN_ISSUES.md 是唯一事实源；修改清单后重跑：
    python scripts/build_known_issues_page.py
页面为纯静态单文件，浏览器直接打开，状态进度存 localStorage。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "KNOWN_ISSUES.md"
OUT = ROOT / "docs" / "known-issues" / "index.html"
OUT_TIMELINE = ROOT / "docs" / "known-issues" / "timeline.html"

FIELD_KEYS = {"现状": "state", "代码": "code", "为什么是问题": "why", "目标方案": "fix"}


def parse(md: str) -> tuple[list[dict], str]:
    categories: list[dict] = []
    items: list[dict] = []
    current_cat = None
    current_item = None
    appendix_lines: list[str] = []
    in_appendix = False
    for line in md.splitlines():
        if line.startswith("## 附录"):
            in_appendix = True
        if in_appendix:
            appendix_lines.append(line)
            continue
        m = re.match(r"^## ([A-J])\. (.+)$", line)
        if m:
            current_cat = {"key": m.group(1), "name": m.group(2).strip()}
            categories.append(current_cat)
            current_item = None
            continue
        m = re.match(r"^### ([A-J]\d+)\. (.+?) `(P\d)` `(\w+)`\s*$", line)
        if m and current_cat:
            current_item = {
                "id": m.group(1),
                "cat": current_cat["key"],
                "catName": current_cat["name"],
                "title": m.group(2).strip(),
                "priority": m.group(3),
                "status": m.group(4),
                "state": "",
                "code": "",
                "why": "",
                "fix": "",
            }
            items.append(current_item)
            continue
        m = re.match(r"^- \*\*(现状|代码|为什么是问题|目标方案)\*\*：(.*)$", line)
        if m and current_item:
            current_item[FIELD_KEYS[m.group(1)]] = m.group(2).strip()
    return items, "\n".join(appendix_lines)


def md_inline(text: str) -> str:
    html = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    html = re.sub(r"`([^`]+)`", r"<code>\1</code>", html)
    html = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", html)
    return html


def appendix_html(md: str) -> str:
    out: list[str] = []
    in_list = False
    in_table = False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ol>")
            in_list = False

    def close_table():
        nonlocal in_table
        if in_table:
            out.append("</tbody></table>")
            in_table = False

    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue  # separator row
            close_list()
            if not in_table:
                out.append("<table class='plan'><thead><tr>"
                           + "".join(f"<th>{md_inline(c)}</th>" for c in cells)
                           + "</tr></thead><tbody>")
                in_table = True
            else:
                out.append("<tr>" + "".join(f"<td>{md_inline(c)}</td>" for c in cells) + "</tr>")
            continue
        close_table()
        if line.startswith("### "):
            close_list()
            out.append(f"<h3>{md_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            close_list()
            out.append(f"<h2>{md_inline(line[3:])}</h2>")
        elif line.startswith("> "):
            close_list()
            out.append(f"<p class='quote'>{md_inline(line[2:])}</p>")
        elif re.match(r"^\d+\. ", line):
            if not in_list:
                out.append("<ol>")
                in_list = True
            out.append(f"<li>{md_inline(re.sub(r'^[0-9]+\. ', '', line))}</li>")
        elif line.startswith("- "):
            close_list()
            out.append(f"<p class='bullet'>• {md_inline(line[2:])}</p>")
        elif line.strip():
            close_list()
            out.append(f"<p>{md_inline(line)}</p>")
    close_list()
    close_table()
    return "\n".join(out)


def parse_plan(md: str) -> tuple[list[dict], list[str], list[str]]:
    """解析附录开工计划：波次(含轨道行)、依赖速查、里程碑。"""
    part = md.split("## 附录：开工计划", 1)
    if len(part) < 2:
        return [], [], []
    plan = part[1]
    waves: list[dict] = []
    deps: list[str] = []
    miles: list[str] = []
    section = "waves"
    wave = None
    for line in plan.splitlines():
        if line.startswith("### 依赖速查"):
            section = "deps"
            continue
        if line.startswith("### 里程碑"):
            section = "miles"
            continue
        if section == "waves":
            m = re.match(r"^### (第 \d 波 · .+)$", line)
            if m:
                wave = {"title": m.group(1).strip(), "tracks": [], "notes": []}
                waves.append(wave)
                continue
            if wave is None:
                continue
            s = line.strip()
            if s.startswith("|"):
                cells = [c.strip() for c in s.strip("|").split("|")]
                if all(re.fullmatch(r":?-{2,}:?", c) for c in cells) or cells[0] == "轨道":
                    continue
                if len(cells) >= 4:
                    wave["tracks"].append({
                        "track": cells[0], "ids": cells[1],
                        "work": cells[2], "dep": cells[3],
                    })
            elif s.startswith("**门禁**") or s.startswith("**注意**") or s.startswith("**为什么"):
                wave["notes"].append(s)
        elif section == "deps" and line.startswith("- "):
            deps.append(line[2:].strip())
        elif section == "miles" and line.startswith("- "):
            miles.append(line[2:].strip())
    return waves, deps, miles


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CareerPilot 短板看板 · 工业级差距清单</title>
<style>
:root{--bg:#0f1420;--panel:#171e2e;--panel2:#1d2639;--line:#2a3550;--text:#dbe4f5;--dim:#8b98b8;
--p0:#ff5d5d;--p1:#ffb020;--p2:#4da3ff;--ok:#3ecf8e;--acc:#7c6cff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.65 "Segoe UI","Microsoft YaHei",sans-serif}
header{position:sticky;top:0;z-index:5;background:rgba(15,20,32,.95);backdrop-filter:blur(6px);
border-bottom:1px solid var(--line);padding:14px 22px}
h1{margin:0 0 4px;font-size:18px}
.sub{color:var(--dim);font-size:12px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:10px}
.chip{border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:999px;
padding:4px 12px;cursor:pointer;font-size:12px}
.chip.on{border-color:var(--acc);background:#262a45}
.chip .n{color:var(--dim);margin-left:4px}
input[type=search]{background:var(--panel);border:1px solid var(--line);color:var(--text);
border-radius:8px;padding:6px 12px;min-width:220px;font-size:13px}
.progress{height:8px;background:var(--panel2);border-radius:99px;overflow:hidden;margin-top:10px}
.progress>div{height:100%;background:linear-gradient(90deg,var(--ok),var(--acc));width:0;transition:width .3s}
main{max-width:1080px;margin:0 auto;padding:18px 22px 80px}
.cat{margin:26px 0 10px;font-size:15px;color:var(--dim);letter-spacing:.5px}
.cat b{color:var(--text)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;margin:10px 0;overflow:hidden}
.card.done{opacity:.55}
.head{display:flex;gap:10px;align-items:flex-start;padding:12px 16px;cursor:pointer}
.head:hover{background:var(--panel2)}
.id{font-family:Consolas,monospace;color:var(--dim);min-width:34px}
.title{flex:1;font-weight:600}
.badge{font-size:11px;border-radius:6px;padding:1px 8px;white-space:nowrap}
.P0{background:#3a1520;color:var(--p0);border:1px solid var(--p0)}
.P1{background:#3a2c10;color:var(--p1);border:1px solid var(--p1)}
.P2{background:#12253a;color:var(--p2);border:1px solid var(--p2)}
select{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:6px;
padding:2px 6px;font-size:12px}
.body{display:none;border-top:1px solid var(--line);padding:12px 16px}
.card.open .body{display:block}
.field{margin:8px 0}
.field .k{font-size:12px;color:var(--acc);font-weight:700;margin-bottom:2px}
code{background:#0c1120;border:1px solid var(--line);border-radius:4px;padding:0 5px;
font-family:Consolas,monospace;font-size:12px;color:#9fd0ff;word-break:break-all}
.note{width:100%;background:var(--panel2);border:1px solid var(--line);color:var(--text);
border-radius:8px;padding:8px;font-size:13px;min-height:52px;resize:vertical}
details.app{margin-top:36px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 18px}
details.app summary{cursor:pointer;font-weight:700}
details.app h2{font-size:15px;margin:16px 0 6px}
details.app h3{font-size:14px;margin:14px 0 4px;color:var(--acc)}
details.app .bullet{margin:4px 0}
details.app .quote{color:var(--dim);border-left:3px solid var(--line);padding-left:10px;margin:6px 0}
table.plan{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}
table.plan th,table.plan td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
table.plan th{background:var(--panel2);color:var(--acc);white-space:nowrap}
table.plan tr:hover td{background:var(--panel2)}
.empty{color:var(--dim);text-align:center;padding:40px}
footer{color:var(--dim);font-size:12px;text-align:center;padding:20px}
a{color:var(--p2)}
</style>
</head>
<body>
<header>
  <h1>CareerPilot 短板看板 <span class="sub">目标：工业级 Agent 系统 · 审计 2026-11-27 · 事实源 KNOWN_ISSUES.md</span></h1>
  <div class="sub" id="stats"></div>
  <div class="progress"><div id="pbar"></div></div>
  <div class="bar" id="filters"></div>
  <div class="bar">
    <a class="chip" href="timeline.html" style="text-decoration:none">📅 施工时间线 →</a>
    <input type="search" id="q" placeholder="搜索标题 / 代码路径 / 关键词…">
    <button class="chip" id="expandAll">全部展开</button>
    <button class="chip" id="collapseAll">全部收起</button>
    <button class="chip" id="resetState">重置本地状态</button>
  </div>
</header>
<main id="list"></main>
<footer>状态与笔记保存在本浏览器 localStorage；清单内容修改请编辑 KNOWN_ISSUES.md 后重跑 scripts/build_known_issues_page.py</footer>
<script id="data" type="application/json">__DATA__</script>
<script>
const ITEMS = JSON.parse(document.getElementById('data').textContent);
const APPENDIX = __APPENDIX__;
const KEY = 'careerpilot-known-issues-v1';
let store = {};
try { store = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch(e) {}
const save = () => localStorage.setItem(KEY, JSON.stringify(store));
const st = id => (store[id] && store[id].status) || ITEMS.find(i=>i.id===id).status;
const STATUSES = ['OPEN','IN_PROGRESS','DONE','ACCEPTED'];
const S_LABEL = {OPEN:'未动',IN_PROGRESS:'进行中',DONE:'已完成',ACCEPTED:'接受取舍'};
let fPriority = 'ALL', fStatus = 'ALL', fCat = 'ALL', query = '';

function counts(){
  const c = {P0:0,P1:0,P2:0,OPEN:0,IN_PROGRESS:0,DONE:0,ACCEPTED:0};
  ITEMS.forEach(i=>{c[i.priority]++;c[st(i.id)]++;});
  return c;
}
function renderFilters(){
  const c = counts();
  const el = document.getElementById('filters');
  const mk = (group,val,label,n) =>
    `<button class="chip ${(group==='p'?fPriority:group==='s'?fStatus:fCat)===val?'on':''}" data-g="${group}" data-v="${val}">${label}<span class="n">${n??''}</span></button>`;
  let h = mk('p','ALL','全部优先级',ITEMS.length)+mk('p','P0','P0',c.P0)+mk('p','P1','P1',c.P1)+mk('p','P2','P2',c.P2)+'<span style="width:12px"></span>';
  h += mk('s','ALL','全部状态')+STATUSES.map(s=>mk('s',s,S_LABEL[s],c[s])).join('')+'<span style="width:12px"></span>';
  const cats = [...new Set(ITEMS.map(i=>i.cat))];
  h += mk('c','ALL','全部领域')+cats.map(k=>mk('c',k,k+' '+ITEMS.find(i=>i.cat===k).catName)).join('');
  el.innerHTML = h;
  el.querySelectorAll('.chip').forEach(b=>b.onclick=()=>{
    const g=b.dataset.g,v=b.dataset.v;
    if(g==='p')fPriority=v;else if(g==='s')fStatus=v;else fCat=v;
    render();
  });
  const done = c.DONE + c.ACCEPTED;
  document.getElementById('stats').textContent =
    `共 ${ITEMS.length} 项 · P0 ${c.P0} / P1 ${c.P1} / P2 ${c.P2} · 已解决/接受 ${done} · 进行中 ${c.IN_PROGRESS} · 未动 ${c.OPEN}`;
  document.getElementById('pbar').style.width = (100*done/ITEMS.length)+'%';
}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function inline(s){return esc(s).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\\*\\*([^*]+)\\*\\*/g,'<strong>$1</strong>');}
function render(){
  renderFilters();
  const list = document.getElementById('list');
  const q = query.toLowerCase();
  const shown = ITEMS.filter(i=>
    (fPriority==='ALL'||i.priority===fPriority)&&
    (fStatus==='ALL'||st(i.id)===fStatus)&&
    (fCat==='ALL'||i.cat===fCat)&&
    (!q||(i.id+i.title+i.state+i.code+i.why+i.fix).toLowerCase().includes(q)));
  if(!shown.length){list.innerHTML='<div class="empty">没有匹配的条目</div>';return;}
  let h='',lastCat='';
  for(const i of shown){
    if(i.cat!==lastCat){lastCat=i.cat;h+=`<div class="cat"><b>${i.cat}</b> · ${esc(i.catName)}</div>`;}
    const s = st(i.id);
    const note = (store[i.id]&&store[i.id].note)||'';
    h+=`<div class="card ${s==='DONE'?'done':''}" id="card-${i.id}">
      <div class="head" data-id="${i.id}">
        <span class="id">${i.id}</span>
        <span class="badge ${i.priority}">${i.priority}</span>
        <span class="title">${esc(i.title)}</span>
        <select data-id="${i.id}" onclick="event.stopPropagation()">
          ${STATUSES.map(x=>`<option value="${x}" ${x===s?'selected':''}>${S_LABEL[x]}</option>`).join('')}
        </select>
      </div>
      <div class="body">
        <div class="field"><div class="k">现状</div>${inline(i.state)}</div>
        <div class="field"><div class="k">代码位置</div>${inline(i.code)}</div>
        <div class="field"><div class="k">为什么是问题</div>${inline(i.why)}</div>
        <div class="field"><div class="k">工业级目标方案</div>${inline(i.fix)}</div>
        <div class="field"><div class="k">我的思考 / 补全笔记</div>
          <textarea class="note" data-id="${i.id}" placeholder="记录你的方案思考、进展、证据链接…">${esc(note)}</textarea>
        </div>
      </div>
    </div>`;
  }
  h+=`<details class="app"><summary>附录：面试红线 与 建议补齐顺序</summary>${APPENDIX}</details>`;
  list.innerHTML=h;
  list.querySelectorAll('.head').forEach(el=>el.onclick=()=>el.parentElement.classList.toggle('open'));
  list.querySelectorAll('select').forEach(el=>el.onchange=()=>{
    const id=el.dataset.id;store[id]=store[id]||{};store[id].status=el.value;save();render();
  });
  list.querySelectorAll('.note').forEach(el=>el.oninput=()=>{
    const id=el.dataset.id;store[id]=store[id]||{};store[id].note=el.value;save();
  });
}
document.getElementById('q').oninput=e=>{query=e.target.value;render();};
document.getElementById('expandAll').onclick=()=>document.querySelectorAll('.card').forEach(c=>c.classList.add('open'));
document.getElementById('collapseAll').onclick=()=>document.querySelectorAll('.card').forEach(c=>c.classList.remove('open'));
document.getElementById('resetState').onclick=()=>{if(confirm('清空本浏览器保存的所有状态与笔记？')){store={};save();render();}};
render();
function jumpToHash(){
  const m = location.hash.match(/^#card-([A-J]\\d+)$/);
  if(!m) return;
  const card = document.getElementById('card-'+m[1]);
  if(card){card.classList.add('open');card.scrollIntoView({behavior:'smooth',block:'start'});}
}
window.addEventListener('hashchange', jumpToHash);
jumpToHash();
</script>
</body>
</html>
"""


TIMELINE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CareerPilot 施工时间线 · AI Agent 短板补全排程</title>
<style>
:root{--bg:#0f1420;--panel:#171e2e;--panel2:#1d2639;--line:#2a3550;--text:#dbe4f5;--dim:#8b98b8;
--p0:#ff5d5d;--p1:#ffb020;--p2:#4da3ff;--ok:#3ecf8e;--acc:#7c6cff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.65 "Segoe UI","Microsoft YaHei",sans-serif}
header{position:sticky;top:0;z-index:5;background:rgba(15,20,32,.95);backdrop-filter:blur(6px);
border-bottom:1px solid var(--line);padding:14px 22px}
h1{margin:0 0 4px;font-size:18px}
.sub{color:var(--dim);font-size:12px}
a{color:var(--p2)}
.chip{display:inline-block;border:1px solid var(--line);background:var(--panel);color:var(--text);
border-radius:999px;padding:4px 12px;font-size:12px;text-decoration:none;margin-top:8px}
.chip:hover{border-color:var(--acc)}
main{max-width:1100px;margin:0 auto;padding:24px 22px 80px}
/* 甘特总览 */
.gantt{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:28px}
.gantt h2{margin:0 0 10px;font-size:15px}
.grow{display:flex;align-items:center;gap:10px;margin:6px 0}
.glabel{min-width:150px;font-size:12px;color:var(--dim);text-align:right}
.gtrack{flex:1;position:relative;height:22px;background:var(--panel2);border-radius:6px}
.gbar{position:absolute;top:2px;bottom:2px;border-radius:5px;background:linear-gradient(90deg,#3b3f66,#5a4fa8);
display:flex;align-items:center;padding:0 8px;font-size:11px;color:#cfd6ff;white-space:nowrap;overflow:hidden}
.gbar .fill{position:absolute;left:0;top:0;bottom:0;background:rgba(62,207,142,.35);border-radius:5px}
.gbar span{position:relative}
.gaxis{display:flex;margin:4px 0 0 160px;font-size:11px;color:var(--dim)}
.gaxis div{flex:1}
/* 垂直时间线 */
.timeline{position:relative;margin-left:14px;padding-left:34px;border-left:2px solid var(--line)}
.wave{position:relative;margin-bottom:34px}
.wave::before{content:"";position:absolute;left:-43px;top:4px;width:16px;height:16px;border-radius:50%;
background:var(--panel);border:3px solid var(--acc)}
.wave.complete::before{border-color:var(--ok);background:var(--ok)}
.wave.active::before{border-color:var(--p1)}
.wtitle{font-size:16px;font-weight:700;margin-bottom:2px}
.wmeta{font-size:12px;color:var(--dim);margin-bottom:10px}
.wprog{height:6px;background:var(--panel2);border-radius:99px;overflow:hidden;max-width:320px;margin:6px 0 12px}
.wprog>div{height:100%;background:var(--ok)}
.tracks{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:10px}
.track{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.track.serial{border-left:3px solid var(--p1)}
.thead{display:flex;gap:8px;align-items:center;margin-bottom:4px}
.tname{font-family:Consolas,monospace;color:var(--acc);font-weight:700}
.serial-tag{font-size:10px;color:var(--p1);border:1px solid var(--p1);border-radius:4px;padding:0 5px}
.parallel-tag{font-size:10px;color:var(--ok);border:1px solid var(--ok);border-radius:4px;padding:0 5px}
.tids{margin:4px 0}
.item-link{display:inline-flex;align-items:center;gap:4px;margin:2px 4px 2px 0;text-decoration:none}
.item-link .badge{font-size:11px;border-radius:6px;padding:1px 7px;font-family:Consolas,monospace}
.P0{background:#3a1520;color:var(--p0);border:1px solid var(--p0)}
.P1{background:#3a2c10;color:var(--p1);border:1px solid var(--p1)}
.P2{background:#12253a;color:var(--p2);border:1px solid var(--p2)}
.item-link.done .badge{opacity:.5;text-decoration:line-through}
.item-link .st{font-size:10px;color:var(--dim)}
.item-link .st.DONE,.item-link .st.ACCEPTED{color:var(--ok)}
.item-link .st.IN_PROGRESS{color:var(--p1)}
.arrow{color:var(--dim);font-size:12px;margin:0 2px}
.twork{font-size:12.5px;color:var(--text);margin:4px 0}
.tdep{font-size:11.5px;color:var(--dim)}
.tdep b{color:var(--p1);font-weight:600}
.wnotes{margin-top:10px;font-size:12.5px;color:var(--dim)}
.wnotes p{margin:3px 0}
code{background:#0c1120;border:1px solid var(--line);border-radius:4px;padding:0 5px;
font-family:Consolas,monospace;font-size:12px;color:#9fd0ff;word-break:break-all}
/* 依赖与里程碑 */
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin-top:26px}
.panel h2{margin:0 0 8px;font-size:15px}
.dep-line{font-family:Consolas,monospace;font-size:12.5px;margin:5px 0;color:var(--text)}
.mile{margin:6px 0;font-size:13px}
.mile b{color:var(--ok)}
footer{color:var(--dim);font-size:12px;text-align:center;padding:20px}
.legend{font-size:11.5px;color:var(--dim);margin-top:8px}
</style>
</head>
<body>
<header>
  <h1>CareerPilot 施工时间线 <span class="sub">60 项短板 · 7 波排程 · 同波可并行 / 跨波硬依赖 · 事实源 KNOWN_ISSUES.md</span></h1>
  <a class="chip" href="index.html">← 返回短板看板</a>
  <span class="sub" id="stats" style="margin-left:12px"></span>
</header>
<main>
  <div class="gantt">
    <h2>总览（按单人专注工时估算，波内并行可压缩实际历时）</h2>
    <div id="gantt"></div>
    <div class="gaxis" id="gaxis"></div>
    <div class="legend">条形位置=累计工时窗口；绿色填充=该波已完成比例。估算仅供排期参考，非承诺工期。</div>
  </div>
  <div class="timeline" id="timeline"></div>
  <div class="panel"><h2>关键依赖链（防走弯路）</h2><div id="deps"></div></div>
  <div class="panel"><h2>里程碑（面试叙事 checkpoints）</h2><div id="miles"></div></div>
</main>
<footer>条目状态与看板共用浏览器 localStorage；点击条目徽章跳转看板对应卡片。修改排程请编辑 KNOWN_ISSUES.md 后重跑 scripts/build_known_issues_page.py</footer>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const {items, waves, deps, miles} = DATA;
const BY_ID = Object.fromEntries(items.map(i=>[i.id,i]));
const KEY = 'careerpilot-known-issues-v1';
let store = {};
try { store = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch(e) {}
const st = id => (store[id] && store[id].status) || (BY_ID[id] ? BY_ID[id].status : 'OPEN');
const isDone = id => ['DONE','ACCEPTED'].includes(st(id));
const S_LABEL = {OPEN:'未动',IN_PROGRESS:'进行中',DONE:'完成',ACCEPTED:'接受'};
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function inline(s){return esc(s).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\\*\\*([^*]+)\\*\\*/g,'<strong>$1</strong>');}
// 从轨道 ids 单元格提取有序 ID 序列（保留 → 串行关系）
function idSeq(cell){
  const plain = cell.replace(/\\*\\*/g,'');
  const parts = plain.split(/→/).map(s=>s.trim());
  return parts.map(p=>{
    const m = p.match(/([A-J]\\d+)/);
    return m ? {id:m[1], label:p} : null;
  }).filter(Boolean);
}
// 工时估算（天）：从波次标题提取，缺省 7
function waveDays(title){
  const m = title.match(/≈\\s*([\\d.]+)(?:–([\\d.]+))?\\s*(天|周)/);
  if(!m) return 7;
  const lo = parseFloat(m[1]), hi = m[2]?parseFloat(m[2]):lo;
  const mid = (lo+hi)/2;
  return m[3]==='周' ? mid*5 : mid;
}
function render(){
  // 统计
  const total = items.length, done = items.filter(i=>isDone(i.id)).length;
  document.getElementById('stats').textContent = `总进度 ${done}/${total}`;
  // 甘特
  const days = waves.map(w=>waveDays(w.title));
  const totalDays = days.reduce((a,b)=>a+b,0);
  let acc = 0, gh = '';
  waves.forEach((w,idx)=>{
    const ids = [...new Set(w.tracks.flatMap(t=>idSeq(t.ids).map(x=>x.id)))].filter(id=>BY_ID[id]);
    const d = ids.filter(isDone).length;
    const left = 100*acc/totalDays, width = 100*days[idx]/totalDays;
    const fill = ids.length ? 100*d/ids.length : 0;
    const short = w.title.split('·')[0].trim() + ' ' + (w.title.split('·')[1]||'').split('（')[0].trim();
    gh += `<div class="grow"><div class="glabel">${esc(short)}</div>
      <div class="gtrack"><div class="gbar" style="left:${left}%;width:${width}%">
      <div class="fill" style="width:${fill}%"></div><span>${d}/${ids.length} · ≈${days[idx]}天</span></div></div></div>`;
    acc += days[idx];
  });
  document.getElementById('gantt').innerHTML = gh;
  let ax = '', cum = 0;
  waves.forEach((w,idx)=>{cum+=days[idx];ax+=`<div style="flex:${days[idx]}">第${idx}波末 ≈${Math.round(cum)}天</div>`;});
  document.getElementById('gaxis').innerHTML = ax;
  // 时间线
  let h = '';
  let firstOpen = true;
  waves.forEach((w,idx)=>{
    const ids = [...new Set(w.tracks.flatMap(t=>idSeq(t.ids).map(x=>x.id)))].filter(id=>BY_ID[id]);
    const d = ids.filter(isDone).length;
    const complete = ids.length>0 && d===ids.length;
    const active = !complete && firstOpen;
    if(!complete) firstOpen = false;
    const pct = ids.length ? 100*d/ids.length : 0;
    h += `<div class="wave ${complete?'complete':''} ${active?'active':''}">
      <div class="wtitle">${esc(w.title)}</div>
      <div class="wmeta">${ids.length} 项 · 完成 ${d} · ${w.tracks.length} 条轨道${active?' · ⬅ 当前应开工':''}</div>
      <div class="wprog"><div style="width:${pct}%"></div></div>
      <div class="tracks">`;
    for(const t of w.tracks){
      const serial = t.track.includes('串行');
      const seq = idSeq(t.ids);
      let chips = '';
      seq.forEach((x,i)=>{
        if(i>0) chips += '<span class="arrow">→</span>';
        const it = BY_ID[x.id];
        const s = st(x.id);
        const pr = it ? it.priority : 'P2';
        const title = it ? it.title : x.label;
        chips += `<a class="item-link ${isDone(x.id)?'done':''}" href="index.html#card-${x.id}" title="${esc(title)}">
          <span class="badge ${pr}">${x.id}</span><span class="st ${s}">${S_LABEL[s]||s}</span></a>`;
      });
      h += `<div class="track ${serial?'serial':''}">
        <div class="thead"><span class="tname">${esc(t.track.replace(/`\\[串行\\]`/,'').trim())}</span>
          <span class="${serial?'serial-tag':'parallel-tag'}">${serial?'轨道内串行':'可并行'}</span></div>
        <div class="tids">${chips}</div>
        <div class="twork">${inline(t.work)}</div>
        <div class="tdep"><b>依赖</b> ${inline(t.dep)}</div>
      </div>`;
    }
    h += '</div>';
    if(w.notes.length){
      h += '<div class="wnotes">' + w.notes.map(n=>`<p>${inline(n)}</p>`).join('') + '</div>';
    }
    h += '</div>';
  });
  document.getElementById('timeline').innerHTML = h;
  document.getElementById('deps').innerHTML =
    deps.map(d=>`<div class="dep-line">${inline(d)}</div>`).join('');
  document.getElementById('miles').innerHTML =
    miles.map(m=>`<div class="mile">${inline(m)}</div>`).join('');
}
render();
</script>
</body>
</html>
"""


def main() -> None:
    items, appendix = parse(SRC.read_text(encoding="utf-8"))
    if not items:
        raise SystemExit("未解析到任何条目，检查 KNOWN_ISSUES.md 格式")
    html = TEMPLATE.replace("__DATA__", json.dumps(items, ensure_ascii=False)).replace(
        "__APPENDIX__", json.dumps(appendix_html(appendix), ensure_ascii=False)
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"生成 {OUT.relative_to(ROOT)}：{len(items)} 项，"
          f"P0={sum(i['priority']=='P0' for i in items)} "
          f"P1={sum(i['priority']=='P1' for i in items)} "
          f"P2={sum(i['priority']=='P2' for i in items)}")

    waves, deps, miles = parse_plan(SRC.read_text(encoding="utf-8"))
    if waves:
        tdata = {"items": items, "waves": waves, "deps": deps, "miles": miles}
        thtml = TIMELINE_TEMPLATE.replace("__DATA__", json.dumps(tdata, ensure_ascii=False))
        OUT_TIMELINE.write_text(thtml, encoding="utf-8")
        n_tracks = sum(len(w["tracks"]) for w in waves)
        print(f"生成 {OUT_TIMELINE.relative_to(ROOT)}：{len(waves)} 波 / {n_tracks} 轨道 / "
              f"{len(deps)} 依赖链 / {len(miles)} 里程碑")
    else:
        print("警告：未解析到开工计划，跳过时间线页面")


if __name__ == "__main__":
    main()