"""Coordinate a single CareerPilot project automation across Codex sessions."""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path, PureWindowsPath
from uuid import UUID

CHECKOUT = Path(__file__).resolve().parents[2]
CHINA = timezone(timedelta(hours=8))
OPEN = ("discovery", "delivery")
GIT_ENV = {key: value for key, value in os.environ.items()
           if key not in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}


def git_bytes(*args, cwd):
    return subprocess.check_output(["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *args],
                                   cwd=cwd, env=GIT_ENV)


def git(*args, cwd):
    return git_bytes(*args, cwd=cwd).decode("utf-8").rstrip("\r\n")


def main_worktree():
    # Git lists the main worktree first, including when invoked from a linked worktree.
    first, _, _ = git("worktree", "list", "--porcelain", "-z", cwd=CHECKOUT).partition("\0")
    root = Path(first.removeprefix("worktree ")).resolve(strict=True)
    if Path(git("rev-parse", "--show-toplevel", cwd=root)).resolve() != root:
        raise ValueError("The main CareerPilot checkout is unavailable.")
    return root


def receipt(row, runs):
    data = dict(row)
    data["scope"] = json.loads(data["scope"])
    data["acceptance"] = json.loads(data["acceptance"])
    directory = runs / ("night-" + data["day"])
    data["run_dir"] = str(directory)
    data["task_workspace"] = str(directory / "worktree")
    return data


def record_versions(directory, owner):
    skills = CHECKOUT / ".agents/skills"
    versions = {name: hashlib.sha256(path.read_bytes()).hexdigest()
                for name in ("project-discovery", "project-delivery", "workflow-retro")
                if (path := skills / name / "SKILL.md").is_file()}
    model = None
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    for index in sorted(home.glob("state_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with closing(sqlite3.connect(index.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
                db.row_factory = sqlite3.Row
                row = db.execute("SELECT model,model_provider,reasoning_effort FROM threads WHERE id=?",
                                 (owner,)).fetchone()
            if row:
                model = dict(row)
                break
        except sqlite3.Error:
            continue
    data = {"thread": owner, "codex_thread_settings": model, "skill_sha256": versions}
    (directory / "versions.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def exact_scope(paths, repo):
    result = []
    for raw in paths:
        path = PureWindowsPath(raw)
        if path.anchor or ".." in path.parts or not path.parts or any(
            part.startswith(".") or ":" in part or any(c in part for c in "*?[]")
            for part in path.parts
        ):
            raise ValueError(f"Expected one repository-relative project file: {raw}")
        resolved = (repo / Path(*path.parts)).resolve()
        if not resolved.is_relative_to(repo) or resolved.is_dir():
            raise ValueError(f"Expected a file inside CareerPilot: {raw}")
        result.append(path.as_posix())
    return list(dict.fromkeys(result))


def candidate_patch(tree, state, repo):
    if (Path(git("rev-parse", "--show-toplevel", cwd=tree)).resolve() != tree.resolve()
            or git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=tree)
            != git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=repo)
            or git("rev-parse", "HEAD", cwd=tree) != state["base"]):
        raise ValueError("The candidate worktree or its base changed.")
    changed = git("diff", "--no-renames", "--name-only", "-z", state["base"], cwd=tree)
    staged = git("diff", "--cached", "--no-renames", "--name-only", "-z", state["base"], cwd=tree)
    unstaged = git("diff", "--no-renames", "--name-only", "-z", cwd=tree)
    added = git("ls-files", "--others", "--exclude-standard", "-z", cwd=tree)
    names = set(filter(None, "\0".join((changed, staged, unstaged, added)).split("\0")))
    if not (changed or added) or names.difference(state["scope"]):
        raise ValueError("The candidate is empty or has files outside the recorded scope.")
    if git("diff", "--name-only", state["base"], "--", *state["scope"], cwd=repo) or git(
        "status", "--porcelain", "--untracked-files=all", "--ignored", "--", *state["scope"], cwd=repo
    ):
        raise ValueError("The selected files in the shared checkout changed. Preserve the candidate.")
    if added:
        git("add", "--intent-to-add", "--", *filter(None, added.split("\0")), cwd=tree)
    return git_bytes("diff", "--no-ext-diff", "--binary", state["base"], cwd=tree)


def accepted_attempt(directory, tree, state, repo):
    latest = (directory / "verify.json").read_bytes()
    verification = json.loads(latest)
    attempt = verification["attempt"]
    if not re.fullmatch(r"attempt-[0-9TZ]+-[0-9a-f]{12}", attempt):
        raise ValueError("Invalid acceptance attempt.")
    attempt_dir = directory / attempt
    if attempt_dir.resolve().parent != directory.resolve() or (attempt_dir / "verify.json").read_bytes() != latest:
        raise ValueError("The latest acceptance receipt differs from its saved attempt.")
    if (verification.get("schema_version") != 1 or verification.get("status") != "passed"
            or verification.get("exit_code") != 0 or verification.get("checked_commit") != state["base"]
            or Path(verification["workspace"]).resolve() != tree.resolve()):
        raise ValueError("Acceptance is missing or does not match the task workspace.")

    patch = (directory / "change.patch").read_bytes()
    digest = hashlib.sha256(patch).hexdigest()
    if (patch != candidate_patch(tree, state, repo)
            or verification.get("patch_mode") != "strict"
            or verification.get("checked_patch_sha256") != digest
            or verification.get("checked_patch_after_sha256") != digest
            or verification.get("patch_base") != state["base"]
            or verification.get("change_patch") != {
                "source": "change.patch", "evidence": "candidate.patch", "sha256": digest}
            or (attempt_dir / "candidate.patch").read_bytes() != patch
            or not (directory / "report.md").stat().st_size):
        raise ValueError("The candidate changed since verification or the report is missing.")

    receipt_hash = hashlib.sha256(latest).hexdigest()
    for manifest in sorted(attempt_dir.glob("preview-*/preview.json")):
        try:
            preview = json.loads(manifest.read_bytes())
            http = preview.get("http", {})
            html = manifest.with_name("preview.html").read_bytes()
            html_hash = hashlib.sha256(html).hexdigest()
            if (preview.get("attempt") == attempt and preview.get("status") == "passed"
                    and preview.get("verify_status") == "passed" and preview.get("verify_sha256") == receipt_hash
                    and http.get("status") == 200 and http.get("body_matches_preview_html") is True
                    and http.get("body_sha256") == preview.get("html_sha256") == html_hash
                    and manifest.with_name("http-body.html").read_bytes() == html):
                return {"attempt": attempt, "verify_sha256": receipt_hash, "patch_sha256": digest,
                        "preview": manifest.relative_to(directory).as_posix(), "html_sha256": html_hash}
        except (OSError, ValueError):
            continue
    raise ValueError("The verified attempt has no matching local preview.")


def transition(db, args, repo, runs):
    db.row_factory = sqlite3.Row
    db.execute("BEGIN IMMEDIATE")
    db.execute("""CREATE TABLE IF NOT EXISTS nights (
        day TEXT PRIMARY KEY, owner TEXT NOT NULL, phase TEXT NOT NULL, base TEXT NOT NULL,
        deadline TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '[]', approval TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '', acceptance TEXT NOT NULL DEFAULT 'null'
    )""")
    if "acceptance" not in {col["name"] for col in db.execute("PRAGMA table_info(nights)")}:
        db.execute("ALTER TABLE nights ADD COLUMN acceptance TEXT NOT NULL DEFAULT 'null'")
    now = datetime.now(CHINA)
    active = db.execute("SELECT * FROM nights WHERE phase IN ('discovery','delivery')").fetchone()
    day = now.date().isoformat()
    latest = db.execute("SELECT * FROM nights ORDER BY day DESC LIMIT 1").fetchone()
    if args.action == "status":
        return receipt(active or latest, runs) if latest else {"phase": "empty"}
    owner = str(UUID(os.environ["CODEX_THREAD_ID"]))

    if args.action == "begin":
        if active or (latest and latest["day"] == day):
            row = active or latest
            return {**receipt(row, runs), "proceed": bool(active and row["owner"] == owner
                    and now < datetime.fromisoformat(row["deadline"]))}
        directory = runs / ("night-" + day)
        directory.mkdir(exist_ok=True)
        record_versions(directory, owner)
        db.execute("INSERT INTO nights(day,owner,phase,base,deadline) VALUES(?,?,?,?,?)",
                   (day, owner, "discovery", git("rev-parse", "HEAD", cwd=repo),
                    (now + timedelta(minutes=30)).isoformat()))
    else:
        if not active:
            raise ValueError("There is no open run.")
        day = active["day"]
        state = receipt(active, runs)
        directory = Path(state["run_dir"])
        tree = Path(state["task_workspace"])
        expired = now >= datetime.fromisoformat(state["deadline"])
        if args.action == "recover":
            if owner == state["owner"] or state["owner"] != args.stopped_thread or not expired:
                raise ValueError("Recovery requires another session to confirm the exact expired owner stopped.")
            if not args.note.strip():
                raise ValueError("Recovery requires a note with the host stop evidence.")
            db.execute("UPDATE nights SET phase=?,note=? WHERE day=?", (args.result, args.note, day))
        elif state["owner"] != owner:
            raise ValueError("Another Codex session owns this run.")
        elif args.action == "prepare":
            if expired:
                raise ValueError("Time budget expired.")
            paths = exact_scope(args.paths, repo)
            if state["phase"] == "delivery":
                if paths != state["scope"] or state["approval"] != args.approval:
                    raise ValueError("This run already has another task.")
            else:
                if db.execute("SELECT 1 FROM nights WHERE approval=? AND base=? AND phase='passed'",
                              (args.approval, state["base"])).fetchone():
                    raise ValueError("This approval already produced a passing candidate at this base.")
                if git("rev-parse", "HEAD", cwd=repo) != state["base"]:
                    raise ValueError("Project HEAD changed since discovery.")
                if git("status", "--porcelain", "--untracked-files=all", "--ignored", "--", *paths, cwd=repo):
                    raise ValueError("Selected paths have local changes. Leave this task for its owner.")
                if tree.exists():
                    raise ValueError("A previous workspace exists. Preserve it for recovery.")
                git("worktree", "add", "--detach", str(tree), state["base"], cwd=repo)
                db.execute("UPDATE nights SET phase='delivery',scope=?,approval=? WHERE day=?",
                           (json.dumps(paths), args.approval, day))
        elif args.action == "checkpoint":
            if expired or state["phase"] != "delivery":
                raise ValueError("Checkpoint requires a delivery inside its time budget.")
            patch = candidate_patch(tree, state, repo)
            pending = directory / "change.patch.pending"
            pending.write_bytes(patch)
            pending.replace(directory / "change.patch")
        elif args.action == "finish":
            if args.result == "no_task" and state["phase"] != "discovery":
                raise ValueError("An implementation workspace already exists.")
            acceptance = None
            if args.result == "passed":
                if expired or state["phase"] != "delivery":
                    raise ValueError("Passing requires a delivery inside its time budget.")
                acceptance = accepted_attempt(directory, tree, state, repo)
            db.execute("UPDATE nights SET phase=?,note=?,acceptance=? WHERE day=?",
                       (args.result, args.note, json.dumps(acceptance), day))
    row = db.execute("SELECT * FROM nights WHERE day=?", (day,)).fetchone()
    return {**receipt(row, runs), "proceed": row["phase"] in OPEN}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("begin")
    actions.add_parser("status")
    actions.add_parser("checkpoint")
    versions = actions.add_parser("versions", help="Capture the current session and skill versions for a daytime run")
    versions.add_argument("--run-dir", type=Path, required=True)
    prepare = actions.add_parser("prepare")
    prepare.add_argument("--approval", required=True, help="Source of the existing task authorization")
    prepare.add_argument("--paths", nargs="+", required=True)
    finish = actions.add_parser("finish")
    finish.add_argument("result", choices=("no_task", "passed", "failed", "blocked"))
    finish.add_argument("--note", required=True)
    recover = actions.add_parser("recover")
    recover.add_argument("--stopped-thread", required=True)
    recover.add_argument("--result", choices=("interrupted", "cancelled"), default="interrupted")
    recover.add_argument("--note", required=True)
    args = parser.parse_args()
    try:
        if args.action == "versions":
            args.run_dir.mkdir(parents=True, exist_ok=True)
            manifest = args.run_dir / "versions.json"
            if not manifest.exists():
                record_versions(args.run_dir, os.environ.get("CODEX_THREAD_ID"))
            print(manifest.resolve())
            return 0
        repo = main_worktree()
        runs = repo / ".agent-runs"
        runs.mkdir(exist_ok=True)
        with closing(sqlite3.connect(runs / "nightly.sqlite3", timeout=10)) as db, db:
            state = transition(db, args, repo, runs)
        print(json.dumps(state, ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, subprocess.CalledProcessError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
