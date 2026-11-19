"""Render the evening choices and morning handoff from the local task ledger."""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

from candidate_delivery import verify_delivery


HERE = Path(__file__).resolve().parent
CHINA = timezone(timedelta(hours=8))
LABEL = {
    "pending": "待你审核", "approved": "已批准", "deferred": "暂缓", "rejected": "已拒绝",
    "claimed": "开发中", "publishing": "保存候选中", "passed": "候选已验证",
    "failed": "本次尝试失败", "blocked": "阻塞",
}
MORNING_DECISION = {"accept": "已接纳", "return": "要求修改", "decline": "放弃本候选"}


def local_link(label, value):
    return f"[{label}](<{Path(value).resolve().as_posix()}>)"


def application_link(task, saved_url):
    """A port can be reassigned after validation. Check the recorded deployment identity."""
    try:
        current = verify_delivery(Path(task["run_dir"]), Path(task["workspace"]),
                                  task["base"], task["patch_sha256"], task["project"].casefold())
        if current["preview_url"] == saved_url:
            return f"[打开本地应用](<{saved_url}>)（候选身份与 HTTP 回读通过）"
    except (ValueError, OSError, subprocess.SubprocessError):
        pass
    return "预览需要恢复，先检查本轮部署回执。"


def evening(tasks, catalog):
    lines = ["# 睡前建议单", "", "直接在 Codex 回复编号即可，也可以改目标、调顺序、暂缓或拒绝。"]
    proposals = [t for t in tasks if t["phase"] in ("pending", "approved")]
    lanes = {}
    shared = {}
    for task in proposals:
        if not task["depends_on"]:
            lanes.setdefault(task["project"], task)
        for path in task["scope"]:
            shared.setdefault((task["project"], path), []).append(task["id"])
    if lanes:
        lines += ["", "**推荐先做**", ""]
        lines.extend(f"- {t['project']}　{t['id']} {t['title']}" for t in lanes.values())
        lines += ["", "这两项若均获批，可以在各自仓库并行。" if len(lanes) == 2
                  else "各项目可在各自仓库开工。"]
    conflicts = [(path, ids) for (_, path), ids in shared.items() if len(ids) > 1]
    if conflicts:
        lines += ["", "**同时采纳时的顺序**", ""]
        lines.extend(f"- {' → '.join(ids)} 共用 `{path}`，按当前优先级先后处理。"
                     for path, ids in conflicts)
    for task in tasks:
        item = {**catalog.get(task["id"], {}), **task}
        dependencies = "、".join(task["depends_on"]) or "可独立安排"
        lines += ["", f"## {task['id']} · {task['title']}", "",
                  f"{task['project']} · {LABEL[task['phase']]} · {item.get('kind', '建议')} · "
                  f"工作量 {item.get('difficulty', '待估')} · 前置 {dependencies}", "",
                  f"**现状依据**　{task['evidence']}"]
        for label, key in (("预期收益", "benefit"), ("实施方式", "approach")):
            if item.get(key):
                lines += ["", f"**{label}**　{item[key]}"]
        lines += ["", f"**验收**　{task['acceptance']}", "",
                  f"基线 `{task['approved_base'][:12]}`。范围 "
                  + "、".join(f"`{p}`" for p in task["scope"]) + "。"]
        sources = item.get("sources", [])
        if sources:
            lines += ["", " · ".join(local_link(path, Path(task["repo"]) / path) for path in sources)]
    return lines


def morning(tasks, deployments=()):
    lines = ["# 本地候选晨报", ""]
    counts = Counter(LABEL[t["phase"]] for t in tasks)
    lines.append("，".join(f"{name} {n} 项" for name, n in counts.items()) or "本次没有登记的建议。")
    for directory in deployments:
        receipt = json.loads((directory / "candidate-delivery.json").read_bytes())
        project = {"careerpilot": "CareerPilot", "squabble": "Squabble"}[receipt["project"]]
        url = receipt.get("preview_url")
        link = (application_link({"run_dir": directory, **receipt}, url)
                if url else "预览未就绪。")
        lines += ["", f"- {project} `dev` 提交 `{receipt['base'][:12]}`。{link}"]
    for project in sorted({t["project"] for t in tasks}):
        lines += ["", f"## {project}"]
        for task in (t for t in tasks if t["project"] == project):
            lines += ["", f"- **{task['id']} · {task['title']}**　{LABEL[task['phase']]}"]
            if task.get("ref") and task.get("commit"):
                lines += [f"  - 本地候选 `{task['ref']}`，提交 `{task['commit'][:12]}`。",
                          f"  - 体验目标　{task['acceptance']}"]
                review = task.get("review")
                lines.append("  - 晨间反馈　" + (
                    f"{MORNING_DECISION[review['decision']]}。{review['reason']}"
                    if review else "等你试用后决定。"))
            predecessors = task.get("predecessors") or []
            if predecessors:
                lines.append("  - 开工基于　" + "、".join(
                    f"{p['id']}（{p['kind']}，`{p['commit'][:12]}`）" for p in predecessors))
            delivery = task.get("delivery") or {}
            if delivery.get("dev_commit"):
                lines.append(f"  - 已合入 `dev`，提交 `{delivery['dev_commit'][:12]}`。"
                             f" [功能 PR]({delivery['pr']})")
            binding = delivery.get("binding") or delivery
            url = binding.get("preview_url")
            for key, label in (("regression", "回归验收"), ("ui", "浏览器验收")):
                check = binding.get(key)
                if check and check.get("exit_code") == 0:
                    lines.append(f"  - {label}通过。")
            if url:
                lines.append("  - " + application_link(task, url))
            if task.get("run_dir") and Path(task["run_dir"]).is_dir():
                report = Path(task["run_dir"]) / "report.md"
                if report.is_file():
                    lines.append("  - " + local_link("改动、体验步骤与已知限制", report))
                lines.append("  - " + local_link("本轮日志与验收证据", task["run_dir"]))
            reasons = task.get("blockers") or []
            if reasons:
                lines.append("  - 等待条件　" + "、".join(map(str, reasons)))
            if task.get("note"):
                lines.append(f"  - 记录　{task['note']}")
    lines += ["", "正式合入由你体验后决定。模型用量以执行日志或宿主账单的实际记录为准。"]
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    choices = commands.add_parser("evening")
    choices.add_argument("--file", required=True, type=Path, help="the imported proposal batch")
    results = commands.add_parser("morning")
    results.add_argument("--batch", help="limit to one discovery batch; default covers the full ledger")
    results.add_argument("--ids", nargs="+", help="include specific tasks across batches")
    results.add_argument("--dev-preview", action="append", type=Path, default=[],
                         help="Include a deployed dev run directory. Repeat for both projects.")
    for command in (choices, results):
        command.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    catalog = {}
    if args.command == "evening":
        document = json.loads(args.file.read_text(encoding="utf-8-sig"))
        batch = document["batch"]
        catalog = {t["id"]: t for t in document["tasks"]}
    else:
        batch = args.batch
    select = ["--batch", batch] if batch else []
    raw = subprocess.check_output([sys.executable, str(HERE / "nightly.py"), "status", *select])
    tasks = json.loads(raw)["tasks"]
    if args.command == "morning" and args.ids:
        requested = set(args.ids)
        tasks = [t for t in tasks if t["id"] in requested]
        if requested.difference(t["id"] for t in tasks):
            parser.error("Unknown task ID in the selected ledger or batch.")
    lines = evening(tasks, catalog) if args.command == "evening" else morning(tasks, args.dev_preview)
    lines += ["", "生成时间　" + datetime.now(CHINA).isoformat(timespec="seconds"), ""]
    args.output.parent.mkdir(exist_ok=True, parents=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
