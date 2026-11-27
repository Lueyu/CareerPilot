#!/usr/bin/env python3
"""issue-solver 配套脚本：读排程上下文 / 完工记账。

用法（在仓库根目录执行）：
    python .agents/skills/issue-solver/scripts/issue_tool.py context <ID>
    python .agents/skills/issue-solver/scripts/issue_tool.py done <ID> \
        --summary "一句话结论" [--pr URL] [--sha SHA] [--evidence 路径] \
        [--followup "遗留事项"] [--accepted]
    python .agents/skills/issue-solver/scripts/issue_tool.py progress

context：输出条目四段详情 + 所属波次/轨道/依赖 + 依赖项当前状态 + 历史日志。
done：KNOWN_ISSUES.md 状态改 DONE（--accepted 改 ACCEPTED）、追加 docs/agent/issue-log.md、
      重跑 scripts/build_known_issues_page.py 同步网页。
progress：按波次统计完成情况。
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
KI = ROOT / "KNOWN_ISSUES.md"
LOG = ROOT / "docs" / "agent" / "issue-log.md"
BUILDER = ROOT / "scripts" / "build_known_issues_page.py"

ITEM_RE = re.compile(r"^### ([A-J]\d+)\. (.+?) `(P\d)` `(\w+)`\s*$", re.M)
FIELD_KEYS = ("现状", "代码", "为什么是问题", "目标方案")


def read_items(md: str) -> dict[str, dict]:
    items: dict[str, dict] = {}
    matches = list(ITEM_RE.finditer(md))
    for idx, m in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(md)
        body = md[m.end():end]
        fields = {}
        for key in FIELD_KEYS:
            fm = re.search(rf"^- \*\*{key}\*\*：(.*)$", body, re.M)
            fields[key] = fm.group(1).strip() if fm else ""
        items[m.group(1)] = {
            "title": m.group(2), "priority": m.group(3),
            "status": m.group(4), "fields": fields,
        }
    return items


def plan_section(md: str) -> str:
    part = md.split("## 附录：开工计划", 1)
    if len(part) < 2:
        return ""
    # 截到下一个附录，避免把"依赖速查/里程碑"里的 ID 误算进波次
    return re.split(r"^### 依赖速查", part[1], maxsplit=1, flags=re.M)[0]


def wave_info(plan: str, issue_id: str) -> list[str]:
    """返回条目所在的波次标题与表格行（含轨道/依赖）。"""
    hits: list[str] = []
    wave = ""
    for line in plan.splitlines():
        if line.startswith("### "):
            wave = line[4:].strip()
        if line.strip().startswith("|") and re.search(rf"\b{issue_id}\b", line):
            hits.append(f"[{wave}] {line.strip()}")
    return hits


def dep_status(md: str, items: dict[str, dict], issue_id: str) -> list[str]:
    """从依赖速查段找包含本条目的链，报告链上其他项状态。"""
    lines = []
    sec = md.split("### 依赖速查", 1)
    if len(sec) < 2:
        return lines
    for line in sec[1].splitlines():
        if line.startswith("### ") or line.startswith("## "):
            break
        if line.startswith("- ") and re.search(rf"\b{issue_id}\b", line):
            refs = re.findall(r"\b([A-J]\d+)\b", line)
            states = ", ".join(
                f"{r}={items[r]['status']}" for r in dict.fromkeys(refs) if r in items
            )
            lines.append(f"{line.strip()}\n  状态: {states}")
    return lines


def cmd_context(issue_id: str) -> int:
    md = KI.read_text(encoding="utf-8")
    items = read_items(md)
    if issue_id not in items:
        print(f"未找到条目 {issue_id}；有效 ID 见 KNOWN_ISSUES.md")
        return 1
    it = items[issue_id]
    print(f"== {issue_id} [{it['priority']}] {it['title']}  状态={it['status']}")
    for key in FIELD_KEYS:
        print(f"\n【{key}】{it['fields'][key]}")
    print("\n== 排程位置")
    for row in wave_info(plan_section(md), issue_id) or ["（未在排程表中找到）"]:
        print(row)
    print("\n== 依赖链与前置状态")
    for row in dep_status(md, items, issue_id) or ["（无显式依赖链）"]:
        print(row)
    if LOG.exists():
        entries = re.findall(
            rf"^## {issue_id} ·.*?(?=^## |\Z)", LOG.read_text(encoding="utf-8"),
            re.M | re.S,
        )
        if entries:
            print("\n== 历史完工记录")
            for e in entries:
                print(e.strip())
    print("\n提示：动手前读 references/playbook.md 中本领域段落；"
          "遵守 AGENTS.md 分支/隔离库/PR 纪律。")
    return 0


def cmd_done(issue_id: str, args: argparse.Namespace) -> int:
    md = KI.read_text(encoding="utf-8")
    items = read_items(md)
    if issue_id not in items:
        print(f"未找到条目 {issue_id}")
        return 1
    new_status = "ACCEPTED" if args.accepted else "DONE"
    pattern = re.compile(
        rf"^(### {issue_id}\. .+? `P\d` `)\w+(`)\s*$", re.M
    )
    if not pattern.search(md):
        print("条目头格式不匹配，未修改")
        return 1
    md = pattern.sub(rf"\g<1>{new_status}\g<2>", md, count=1)
    KI.write_text(md, encoding="utf-8")

    today = dt.date.today().isoformat()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if not LOG.exists():
        LOG.write_text(
            "# 短板补全日志\n\n"
            "> 由 issue-solver 技能追加；每项完工记一笔。清单事实源见 KNOWN_ISSUES.md。\n",
            encoding="utf-8",
        )
    entry = [f"\n## {issue_id} · {today} · {new_status}",
             f"- 结论：{args.summary}"]
    if args.pr:
        entry.append(f"- PR：{args.pr}")
    if args.sha:
        entry.append(f"- 验证 SHA：{args.sha}")
    if args.evidence:
        entry.append(f"- 证据：{args.evidence}")
    if args.followup:
        entry.append(f"- 遗留/后续：{args.followup}")
    with LOG.open("a", encoding="utf-8") as f:
        f.write("\n".join(entry) + "\n")

    if BUILDER.exists():
        r = subprocess.run([sys.executable, str(BUILDER)], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8")
        print(r.stdout.strip() or r.stderr.strip())
    print(f"{issue_id} → {new_status}；日志已记入 {LOG.relative_to(ROOT)}")
    return 0


def cmd_progress() -> int:
    md = KI.read_text(encoding="utf-8")
    items = read_items(md)
    plan = plan_section(md)
    wave = "（未排程）"
    buckets: dict[str, list[str]] = {}
    assigned = set()
    for line in plan.splitlines():
        if line.startswith("### "):
            wave = line[4:].strip()
        if line.strip().startswith("|"):
            for r in re.findall(r"\b([A-J]\d+)\b", line):
                if r in items and r not in assigned:
                    buckets.setdefault(wave, []).append(r)
                    assigned.add(r)
    total_done = 0
    for w, ids in buckets.items():
        done = [r for r in ids if items[r]["status"] in ("DONE", "ACCEPTED")]
        total_done += len(done)
        mark = "✅" if len(done) == len(ids) else ("🔶" if done else "⬜")
        print(f"{mark} {w}: {len(done)}/{len(ids)}"
              + (f"  已完成: {','.join(done)}" if done else ""))
    print(f"\n总计 {total_done}/{len(items)}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("context")
    c.add_argument("issue_id")
    d = sub.add_parser("done")
    d.add_argument("issue_id")
    d.add_argument("--summary", required=True)
    d.add_argument("--pr", default="")
    d.add_argument("--sha", default="")
    d.add_argument("--evidence", default="")
    d.add_argument("--followup", default="")
    d.add_argument("--accepted", action="store_true")
    sub.add_parser("progress")
    args = p.parse_args()
    if args.cmd == "context":
        return cmd_context(args.issue_id.upper())
    if args.cmd == "done":
        return cmd_done(args.issue_id.upper(), args)
    return cmd_progress()


if __name__ == "__main__":
    raise SystemExit(main())
