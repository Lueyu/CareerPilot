"""Coordinate local, explicitly approved project tasks across Codex sessions."""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from contextlib import closing
from pathlib import Path, PureWindowsPath

CHECKOUT = Path(__file__).resolve().parents[2]
OPEN = ("discovery", "delivery")
GIT_ENV = {key: value for key, value in os.environ.items()
           if key not in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                          "GIT_NAMESPACE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")}
GIT_ENV.update(GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1", GIT_LITERAL_PATHSPECS="1",
               GIT_ALLOW_PROTOCOL="", GIT_LFS_SKIP_SMUDGE="1")


def git_bytes(*args, cwd, env=None, input=None):
    result = subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
         "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd, env=GIT_ENV if env is None else env, input=input, capture_output=True,
    )
    if result.returncode:
        reason = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Git {args[0]} failed: {reason or 'exit ' + str(result.returncode)}")
    return result.stdout


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


def check_workspace(tree, state, repo, *, allow_commits=False):
    if (Path(git("rev-parse", "--show-toplevel", cwd=tree)).resolve() != tree.resolve()
            or git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=tree)
            != git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=repo)):
        raise ValueError("The candidate worktree or its base changed.")
    head = git("rev-parse", "HEAD", cwd=tree)
    if allow_commits:
        valid = is_ancestor(repo, state["base"], head)
    else:
        valid = head == state["base"] and git("rev-parse", "--abbrev-ref", "HEAD", cwd=tree) == "HEAD"
    if not valid:
        raise ValueError("The candidate worktree or its base changed.")


def in_scope(name, scope):
    return any(name == path or (path.endswith("/") and name.startswith(path)) for path in scope)


def candidate_patch(tree, state, repo):
    check_workspace(tree, state, repo)
    changed = git("diff", "--no-renames", "--name-only", "-z", state["base"], cwd=tree)
    staged = git("diff", "--cached", "--no-renames", "--name-only", "-z", state["base"], cwd=tree)
    unstaged = git("diff", "--no-renames", "--name-only", "-z", cwd=tree)
    added = git("ls-files", "--others", "--exclude-standard", "-z", cwd=tree)
    names = set(filter(None, "\0".join((changed, staged, unstaged, added)).split("\0")))
    if not (changed or added) or any(not in_scope(name, state["scope"]) for name in names):
        raise ValueError("The candidate is empty or has files outside the recorded scope.")
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


ACTIVE_TASKS = ("claimed", "blocked", "failed", "publishing")


def initialize_tasks(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS agile_tasks (
            id TEXT PRIMARY KEY COLLATE NOCASE, original TEXT NOT NULL, proposal TEXT NOT NULL,
            phase TEXT NOT NULL DEFAULT 'pending', owner TEXT, effective_base TEXT,
            note TEXT, patch_sha256 TEXT, candidate_commit TEXT, delivery TEXT,
            accepted_patch BLOB
        );
        CREATE TABLE IF NOT EXISTS agile_batches (
            batch TEXT NOT NULL, task_id TEXT NOT NULL, PRIMARY KEY (batch, task_id)
        );
        CREATE TABLE IF NOT EXISTS agile_history (
            seq INTEGER PRIMARY KEY, task_id TEXT NOT NULL, at TEXT NOT NULL,
            event TEXT NOT NULL, details TEXT NOT NULL
        );
    """)


def nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string.")
    return value.strip()


def task_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("Task IDs must contain 1–64 ASCII letters, digits, underscores or hyphens.")
    return value


def scope_paths(entries):
    if not isinstance(entries, list) or not entries:
        raise ValueError("Scope must be a nonempty list.")
    paths = []
    for entry in entries:
        path = nonempty(entry, "scope path").replace("\\", "/")
        parts = path.removesuffix("/").split("/")
        if any(not part or part in (".", "..") or part.endswith((".", " "))
               or part.casefold() == ".git" or PureWindowsPath(part).is_reserved()
               or any(ord(char) < 32 or char in ':*?"<>|' for char in part) for part in parts):
            raise ValueError(f"Invalid repository-relative scope: {entry}")
        paths.append(path)
    return sorted(set(paths))


def check_scope_at(repo, base, scope):
    entries = git("ls-tree", "-t", "-r", "-z", base, cwd=repo)
    modes = {}
    for entry in filter(None, entries.split("\0")):
        metadata, path = entry.split("\t", 1)
        modes[path] = metadata.split(" ", 1)[0]
    for path in scope:
        parts = path.removesuffix("/").split("/")
        for depth in range(1, len(parts)):
            ancestor = "/".join(parts[:depth])
            if modes.get(ancestor, "040000") != "040000":
                raise ValueError(f"Scope traverses a non-directory at {base}: {ancestor}")
        mode = modes.get(path.removesuffix("/"))
        if mode and (path.endswith("/") != (mode == "040000") or mode == "160000"):
            raise ValueError(f"Scope type does not match the pinned tree: {path}")


def parse_proposal(data):
    fields = {"id", "project", "repo", "base", "title", "scope", "acceptance", "depends_on", "evidence"}
    display = {"benefit", "approach", "difficulty", "kind"}
    if not isinstance(data, dict) or not fields <= data.keys() or data.keys() - fields - display - {
        "priority", "sources"
    }:
        raise ValueError("Expected a complete task object with the documented fields.")
    proposal = {key: nonempty(data[key], key) for key in fields - {"scope", "depends_on", "id"}}
    proposal.update({key: nonempty(data[key], key) for key in display & data.keys()})
    if "sources" in data:
        if not isinstance(data["sources"], list):
            raise ValueError("sources must be a list of strings.")
        proposal["sources"] = [nonempty(value, "source") for value in data["sources"]]
    proposal["id"] = task_id(data["id"])
    proposal["scope"] = scope_paths(data["scope"])
    if not isinstance(data["depends_on"], list):
        raise ValueError("depends_on must be a list of task IDs.")
    proposal["depends_on"] = sorted(set(map(task_id, data["depends_on"])))
    proposal["priority"] = data.get("priority", 0)
    if isinstance(proposal["priority"], str) and re.fullmatch(r"P[0-9]{1,3}", proposal["priority"]):
        proposal["priority"] = -int(proposal["priority"][1:])
    if type(proposal["priority"]) is not int:
        raise ValueError("priority must be an integer.")
    repo = Path(proposal["repo"])
    if not repo.is_absolute():
        raise ValueError("repo must be an absolute local repository path.")
    repo = repo.resolve(strict=True)
    first = git("worktree", "list", "--porcelain", "-z", cwd=repo).split("\0", 1)[0]
    repo = Path(first.removeprefix("worktree ")).resolve(strict=True)
    base = proposal["base"]
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", base) or git(
        "cat-file", "-t", base, cwd=repo
    ) != "commit":
        raise ValueError("base must be the full SHA of an existing local commit.")
    check_scope_at(repo, base, proposal["scope"])
    proposal["repo"] = str(repo)
    return proposal


def task_rows(db):
    return {row["id"]: row for row in db.execute("""
        SELECT id,proposal,phase,owner,effective_base,note,patch_sha256,candidate_commit,delivery,
               (SELECT details FROM agile_history WHERE task_id=t.id AND event='feedback') AS feedback,
               (SELECT details FROM agile_history WHERE task_id=t.id AND event='claimed'
                ORDER BY seq LIMIT 1) AS claim
        FROM agile_tasks AS t
    """)}


def definitions(rows):
    return {key: json.loads(row["proposal"]) for key, row in rows.items()}


def validate_dependencies(proposals):
    visiting, done = set(), set()

    def visit(key):
        if key in visiting:
            raise ValueError(f"Dependency cycle includes {key}.")
        if key in done or key not in proposals:
            return
        visiting.add(key)
        proposal = proposals[key]
        for dep in proposal["depends_on"]:
            visit(dep)
        visiting.remove(key)
        done.add(key)

    for key in proposals:
        visit(key)


def record_event(db, key, event, **details):
    db.execute("INSERT INTO agile_history(task_id,at,event,details) VALUES(?,?,?,?)",
               (key, datetime.now(timezone.utc).isoformat(timespec="seconds"), event, json.dumps(details)))


def import_batch(db, document):
    if not isinstance(document, dict) or set(document) != {"batch", "tasks"}:
        raise ValueError("Expected a batch and tasks.")
    batch = nonempty(document["batch"], "batch")
    if not isinstance(document["tasks"], list):
        raise ValueError("tasks must be a list.")
    imported = []
    for raw in document["tasks"]:
        proposal = parse_proposal(raw)
        key = proposal["id"]
        existing = db.execute("SELECT * FROM agile_tasks WHERE id=?", (key,)).fetchone()
        if existing is None:
            encoded = json.dumps(proposal, sort_keys=True)
            db.execute("INSERT INTO agile_tasks(id,original,proposal) VALUES(?,?,?)", (key, encoded, encoded))
            record_event(db, key, "proposed", batch=batch, proposal=proposal)
        elif proposal not in (json.loads(existing["original"]), json.loads(existing["proposal"])):
            raise ValueError(f"{key} already exists with different content. Use decide --decision revise.")
        db.execute("INSERT OR IGNORE INTO agile_batches VALUES(?,?)", (batch, key))
        if key not in imported:
            imported.append(key)
    validate_dependencies(definitions(task_rows(db)))
    return {"batch": batch, "tasks": imported}


def decide_task(db, row, args, document):
    key = row["id"]
    reason = nonempty(args.reason, "decision reason")
    decision = args.decision
    if (decision == "revise") != (document is not None):
        raise ValueError("A complete --file is required only for revise.")
    proposal = json.loads(row["proposal"])
    if decision == "revise":
        proposal = parse_proposal(document)
        if proposal["id"] != key:
            raise ValueError("A revision must preserve the task ID.")
    if args.priority is not None:
        proposal["priority"] = args.priority
    phase = {"approve": "approved", "reject": "rejected", "defer": "deferred", "revise": "pending"}[decision]
    reply = args.reply if args.reply is not None else os.environ.get("CODEX_HUMAN_REPLY")
    if reply is not None:
        nonempty(reply, "original human reply")
    chat_id = args.chat_id or os.environ.get("CODEX_THREAD_ID")
    details = {"reason": reason, "proposal": proposal, "reply": reply, "chat_id": chat_id}
    previous = db.execute("SELECT event,details FROM agile_history WHERE task_id=? ORDER BY seq DESC LIMIT 1",
                          (key,)).fetchone()
    if previous["event"] == decision and json.loads(previous["details"]) == details:
        return
    if row["phase"] in (*ACTIVE_TASKS, "passed"):
        raise ValueError("Decisions are closed after a claim.")
    if row["phase"] == "rejected" and decision != "revise":
        raise ValueError("Revise a rejected proposal before deciding again.")
    db.execute("UPDATE agile_tasks SET phase=?,proposal=?,note=NULL WHERE id=?",
               (phase, json.dumps(proposal), key))
    validate_dependencies(definitions(task_rows(db)))
    record_event(db, key, decision, **details)


def reading_nights(db, runs, day=None):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='nights'").fetchone():
        return []
    rows = db.execute("SELECT * FROM nights WHERE (? IS NULL OR day=?) ORDER BY day DESC", (day, day))
    return [receipt({"acceptance": "null", **dict(row)}, runs) for row in rows]


def reconcile_legacy(db, args, runs):
    db.execute("BEGIN IMMEDIATE")
    owner = nonempty(args.owner, "historical owner")
    reason = nonempty(args.reason, "reconciliation reason")
    nights = reading_nights(db, runs, args.night)
    if not args.confirm_stopped or not nights or nights[0]["owner"] != owner:
        raise ValueError("Reconciliation requires the historical night and its exact confirmed stopped owner.")
    night = nights[0]
    if night["phase"] == "interrupted" and night["note"] == reason:
        return night
    if night["phase"] not in OPEN:
        raise ValueError("Only an open historical night can be reconciled.")
    db.execute("UPDATE nights SET phase='interrupted',note=? WHERE day=?", (reason, args.night))
    return reading_nights(db, runs, args.night)[0]


def review_ref(key):
    # Prefix the component so every validated ID is also a valid ref and Windows directory name.
    return "refs/candidates/agile/task-" + key.lower()


def revision_at_ref(repo, ref):
    matches = git("for-each-ref", "--format=%(refname) %(objectname) %(symref)", ref, cwd=repo)
    for match in matches.splitlines():
        name, revision, symbolic = match.split(" ", 2)
        if name == ref:
            if symbolic:
                raise ValueError(f"Local review ref {ref} is symbolic.")
            return revision
    return ""


def is_ancestor(repo, older, newer):
    result = subprocess.run(["git", "merge-base", "--is-ancestor", older, newer], cwd=repo, env=GIT_ENV,
                            capture_output=True)
    if result.returncode not in (0, 1):
        raise ValueError(f"Cannot compare local commits {older} and {newer}.")
    return result.returncode == 0


def fetched_dev_contains(repo, commit):
    tip = revision_at_ref(repo, "refs/remotes/origin/dev")
    return bool(tip) and is_ancestor(repo, commit, tip)


def overlaps(left, right):
    for first in left:
        for second in right:
            first_key, second_key = first.rstrip("/").casefold(), second.rstrip("/").casefold()
            if (first_key == second_key or first_key.startswith(second_key + "/")
                    or second_key.startswith(first_key + "/")):
                return True
    return False


def claim_plan(key, rows, proposals, legacy, controller):
    task = proposals[key]
    repo = Path(task["repo"])
    reasons = []
    predecessors = {}
    stale = []
    withheld = {other for other, row in rows.items() if row["feedback"]
                and json.loads(row["feedback"])["decision"] in ("return", "decline")}
    checked = {}

    def withheld_predecessor(candidate):
        if candidate not in checked:
            row = rows[candidate]
            candidate_repo = proposals[candidate]["repo"]
            blocked_by = next((other for other in sorted(withheld)
                               if other == candidate or (
                                   proposals[other]["repo"] == candidate_repo
                                   and is_ancestor(Path(candidate_repo), rows[other]["candidate_commit"],
                                                   row["candidate_commit"]))), None)
            if withheld and blocked_by is None and row["claim"]:
                for predecessor in json.loads(row["claim"])["predecessors"]:
                    blocked_by = withheld_predecessor(predecessor["id"])
                    if blocked_by:
                        break
            checked[candidate] = blocked_by
        return checked[candidate]

    for dep in task["depends_on"]:
        dependency = rows.get(dep)
        if not dependency or dependency["phase"] != "passed":
            reasons.append(f"Dependency {dep} has no verified local candidate.")
            continue
        blocked_by = withheld_predecessor(dep)
        if blocked_by:
            reasons.append(f"Dependency {dep} is unavailable because of morning feedback on {blocked_by}.")
            continue
        upstream = proposals[dep]
        local = upstream["repo"] == task["repo"] and upstream["project"].casefold() == task["project"].casefold()
        predecessors[dep] = "explicit" if local else "cross-project"
    for other in sorted(rows):
        row = rows[other]
        proposal = proposals[other]
        if other == key or task["repo"] != proposal["repo"] or not overlaps(task["scope"], proposal["scope"]):
            continue
        if row["phase"] in ACTIVE_TASKS:
            reasons.append(f"Scope overlaps {other}, held by {row['owner']}.")
        elif (row["phase"] == "passed" and predecessors.get(other) != "explicit"
              and not withheld_predecessor(other)):
            candidate = row["candidate_commit"]
            local = proposal["project"].casefold() == task["project"].casefold()
            if local and proposal["base"] == task["base"]:
                predecessors[other] = "overlap"
            elif not is_ancestor(repo, candidate, task["base"]):
                stale.append((other, candidate))
    for other, candidate in stale:
        if not any(kind != "cross-project" and is_ancestor(
            repo, candidate, rows[dep]["candidate_commit"]
        ) for dep, kind in predecessors.items()):
            reasons.append(f"Scope overlaps verified {other} from a different base; declare a local "
                           "dependency or approve an inheriting base.")
    integration = []
    for dep, kind in sorted(predecessors.items()):
        commit = rows[dep]["candidate_commit"]
        ref = review_ref(dep)
        if revision_at_ref(Path(proposals[dep]["repo"]), ref) != commit:
            reasons.append(f"Verified local ref for predecessor {dep} is missing or changed.")
        integration.append({"id": dep, "kind": kind, "commit": commit, "ref": ref})
    if repo == controller:
        for night in legacy:
            if night["phase"] in OPEN and overlaps(task["scope"], night["scope"]):
                reasons.append(f"Scope overlaps legacy night {night['day']}, held by {night['owner']}.")
    return reasons, integration


def task_directory(runs, key):
    return runs / "agile-tasks" / ("task-" + key.lower())


def task_receipt(db, row, proposal, runs, reasons=()):
    key = row["id"]
    directory = task_directory(runs, key)
    state = dict(proposal)
    state.update(approved_base=proposal["base"], base=row["effective_base"], permissions="local-only",
                 batches=[batch[0] for batch in db.execute(
                     "SELECT batch FROM agile_batches WHERE task_id=? ORDER BY batch", (key,))],
                 phase=row["phase"], owner=row["owner"], run_dir=str(directory),
                 workspace=str(directory / "worktree"), blockers=list(reasons),
                 note=row["note"], patch_sha256=row["patch_sha256"], commit=row["candidate_commit"],
                 ref=review_ref(key) if row["candidate_commit"] else None,
                 delivery=json.loads(row["delivery"]) if row["delivery"] else None,
                 history=[{"at": event["at"], "event": event["event"], **json.loads(event["details"])}
                          for event in db.execute("SELECT * FROM agile_history WHERE task_id=? ORDER BY seq", (key,))])
    claim = next((event for event in state["history"] if event["event"] == "claimed"), {})
    state["predecessors"] = claim.get("predecessors", [])
    state["claimed_at"] = claim.get("at")
    target = next((event for event in reversed(state["history"]) if event["event"] == "target-changed"), claim)
    if target.get("target") == "dev" or (row["phase"] == "passed" and (state["delivery"] or {}).get("dev_commit")):
        state["permissions"] = "verified-pr-to-dev"
    state["review"] = next(({name: value for name, value in event.items() if name != "event"}
                            for event in state["history"] if event["event"] == "feedback"), None)
    return state


def agile_status(db, runs, controller, batch=None):
    rows = task_rows(db)
    proposals = definitions(rows)
    legacy = reading_nights(db, runs)
    available = set(rows) if batch is None else {row[0] for row in db.execute(
        "SELECT task_id FROM agile_batches WHERE batch=?", (batch,))}
    tasks, ready = [], []
    for key in sorted(available, key=lambda key: (-proposals[key]["priority"], key)):
        row = rows[key]
        reasons = []
        if row["phase"] == "approved":
            try:
                reasons, _ = claim_plan(key, rows, proposals, legacy, controller)
            except (OSError, ValueError) as error:
                reasons = [str(error)]
            if not reasons and not row["note"]:
                ready.append(key)
            if row["note"]:
                reasons.append(row["note"])
        elif row["phase"] in ("blocked", "failed") and row["note"]:
            reasons = [row["note"]]
        tasks.append(task_receipt(db, row, proposals[key], runs, reasons))
    return {"tasks": tasks, "ready": ready, "legacy": legacy}


def local_commit(repo, tree, parents, message, timestamp):
    env = dict(GIT_ENV, GIT_AUTHOR_NAME="Local task coordinator", GIT_COMMITTER_NAME="Local task coordinator",
               GIT_AUTHOR_EMAIL="coordinator@localhost", GIT_COMMITTER_EMAIL="coordinator@localhost",
               GIT_AUTHOR_DATE=timestamp, GIT_COMMITTER_DATE=timestamp)
    parent_args = [part for parent in parents for part in ("-p", parent)]
    return git_bytes("-c", "commit.gpgSign=false", "commit-tree", tree, *parent_args, "-m", message,
                     cwd=repo, env=env).decode("ascii").strip()


def integrate_dependencies(repo, proposal, predecessors, timestamp):
    base = proposal["base"]
    for predecessor in predecessors:
        dep, candidate = predecessor["id"], predecessor["commit"]
        if predecessor["kind"] == "cross-project" or is_ancestor(repo, candidate, base):
            continue
        try:
            tree = git("merge-tree", "--write-tree", base, candidate, cwd=repo)
        except ValueError as error:
            raise ValueError(f"Cannot integrate dependency {dep} above the approved base: {error}") from error
        base = local_commit(repo, tree, (base, candidate), f"Integrate {dep} for {proposal['id']}", timestamp)
    check_scope_at(repo, base, proposal["scope"])
    return base


def ensure_task_workspace(directory, repo, state, *, allow_commits=False):
    if directory.is_symlink() or directory.resolve() != directory:
        raise ValueError("The task run directory has been redirected.")
    directory.mkdir(parents=True, exist_ok=True)
    tree = directory / "worktree"
    if not tree.exists():
        git("worktree", "add", "--detach", str(tree), state["base"], cwd=repo)
    if tree.resolve() != tree:
        raise ValueError("The task workspace has been redirected.")
    check_workspace(tree, state, repo, allow_commits=allow_commits)
    return tree


def write_checkpoint(directory, patch):
    destination = directory / "change.patch"
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as pending:
        path = Path(pending.name)
        pending.write(patch)
    try:
        path.replace(destination)
    finally:
        path.unlink(missing_ok=True)


def commit_patch(repo, directory, base, key, patch, timestamp):
    with tempfile.TemporaryDirectory(dir=directory) as scratch:
        env = dict(GIT_ENV, GIT_INDEX_FILE=str(Path(scratch) / "index"))
        git_bytes("read-tree", base, cwd=repo, env=env)
        git_bytes("apply", "--cached", "--binary", "-", cwd=repo, env=env, input=patch)
        tree = git_bytes("write-tree", cwd=repo, env=env).decode("ascii").strip()
    return local_commit(repo, tree, (base,), f"Verified candidate {key}", timestamp)


def stage_publication(db, row, state, directory, tree, repo):
    patch = candidate_patch(tree, state, repo)
    digest = hashlib.sha256(patch).hexdigest()
    if digest != row["patch_sha256"] or (directory / "change.patch").read_bytes() != patch:
        raise ValueError("The candidate changed since checkpoint. Checkpoint and verify the current patch.")
    try:
        from candidate_delivery import verify_delivery
    except ImportError as error:
        raise ValueError(f"Project delivery verifier is unavailable: {error}") from error
    project = state["project"].casefold()
    binding = verify_delivery(directory, tree, state["base"], digest, project)
    if not isinstance(binding, dict) or not binding:
        raise ValueError("The project verifier did not return a delivery binding.")
    delivery = dict(binding)
    if project == "careerpilot":
        delivery["strict_receipt"] = accepted_attempt(directory, tree, state, repo)
    if (directory / "change.patch").read_bytes() != patch or candidate_patch(tree, state, repo) != patch:
        raise ValueError("The candidate changed during delivery verification.")
    key = row["id"]
    commit = commit_patch(repo, directory, state["base"], key, patch, state["claimed_at"])
    db.execute("""UPDATE agile_tasks SET phase='publishing',candidate_commit=?,delivery=?,accepted_patch=?
                  WHERE id=?""", (commit, json.dumps(delivery), patch, key))
    record_event(db, key, "verified", owner=row["owner"], commit=commit, patch_sha256=digest)
    db.commit()


def publish_task(db, row, repo, directory, claimed_at):
    key, commit = row["id"], row["candidate_commit"]
    ref = review_ref(key)
    existing = revision_at_ref(repo, ref)
    if existing != commit:
        if existing:
            raise ValueError(f"Local review ref {ref} already points at another commit.")
        patch = db.execute("SELECT accepted_patch FROM agile_tasks WHERE id=?", (key,)).fetchone()[0]
        if hashlib.sha256(patch).hexdigest() != row["patch_sha256"]:
            raise ValueError("The stored accepted patch no longer matches verification.")
        actual = commit_patch(repo, directory, row["effective_base"], key, patch, claimed_at)
        if actual != commit:
            raise ValueError("The stored accepted patch no longer reconstructs its candidate.")
        git("update-ref", "--no-deref", ref, commit, "0" * len(commit), cwd=repo)
    db.execute("UPDATE agile_tasks SET phase='passed',note=NULL WHERE id=?", (key,))
    record_event(db, key, "passed", owner=row["owner"], commit=commit, ref=ref)


def record_dev_delivery(db, row, state, args, repo, directory):
    if state["permissions"] != "verified-pr-to-dev":
        raise ValueError("Run claim --dev as the current owner before recording a dev delivery.")
    slug = {"careerpilot": "Lueyu/CareerPilot", "squabble": "Lueyu/squabble"}.get(state["project"].casefold())
    match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/pull/([1-9][0-9]*)", args.pr)
    if not slug or not match or match[1].casefold() != slug.casefold():
        raise ValueError("The pull request must belong to the approved project's GitHub repository.")
    fields = "url,state,baseRefName,headRefOid,mergeCommit,isCrossRepository"
    raw = subprocess.check_output(
        ["gh", "pr", "view", match[2], "--repo", slug, "--json", fields], cwd=repo, encoding="utf-8")
    pr = json.loads(raw)
    commit = (pr.get("mergeCommit") or {}).get("oid")
    if (pr["state"] != "MERGED" or pr["baseRefName"] != "dev" or pr["isCrossRepository"]
            or pr["headRefOid"] != args.head or not commit):
        raise ValueError("GitHub did not confirm the verified head merged into dev.")
    if not fetched_dev_contains(repo, commit):
        raise ValueError("Fetch origin/dev and confirm it contains the merged PR.")
    if not is_ancestor(repo, state["base"], args.head):
        raise ValueError("The PR head does not include the task's claimed base.")
    parent = git("rev-parse", f"{commit}^1", cwd=repo)
    changed = git("diff", "--no-renames", "--name-only", "-z", parent, commit, cwd=repo).split("\0")
    paths = [path for path in changed if path]
    if not paths or any(not in_scope(path, state["scope"]) for path in paths):
        raise ValueError("The merged commit is empty or changes files outside the approved scope.")
    tree = git("merge-tree", "--write-tree", parent, args.head, cwd=repo)
    if tree != git("rev-parse", f"{commit}^{{tree}}", cwd=repo):
        raise ValueError("The merged tree differs from a clean merge of the verified PR head.")
    if not (directory / "report.md").is_file():
        raise ValueError("The task's verification report is missing.")
    delivery = {"pr": pr["url"], "head": args.head, "dev_commit": commit}
    if row["phase"] == "passed":
        if row["candidate_commit"] != commit or json.loads(row["delivery"]) != delivery:
            raise ValueError("A different delivery is already recorded for this task.")
        return
    ref = review_ref(row["id"])
    existing = revision_at_ref(repo, ref)
    if existing and existing != commit:
        raise ValueError("The task ref already points at another commit.")
    if not existing:
        git("update-ref", "--no-deref", ref, commit, "0" * len(commit), cwd=repo)
    db.execute("UPDATE agile_tasks SET phase='passed',candidate_commit=?,delivery=?,note=NULL WHERE id=?",
               (commit, json.dumps(delivery), row["id"]))
    record_event(db, row["id"], "landed-dev", **delivery)


def agile_transition(db, args, controller, runs, document):
    db.execute("BEGIN IMMEDIATE")
    if args.action == "propose":
        return import_batch(db, document)

    key = task_id(args.id)
    rows = task_rows(db)
    if key not in rows:
        raise ValueError(f"Unknown task {key}.")
    row = rows[key]
    proposals = definitions(rows)
    proposal = proposals[key]
    repo = Path(proposal["repo"])
    state = task_receipt(db, row, proposal, runs)
    directory = Path(state["run_dir"])
    action = args.action
    phase = row["phase"]
    if action == "decide":
        decide_task(db, row, args, document)
    elif action == "feedback":
        if phase != "passed":
            raise ValueError("Morning feedback requires a passed local candidate.")
        reason = nonempty(args.reason, "human feedback")
        details = {"decision": args.decision, "reason": reason, "reply": args.reason,
                   "chat_id": args.chat_id or os.environ.get("CODEX_THREAD_ID")}
        if row["feedback"] is None:
            record_event(db, key, "feedback", **details)
        elif json.loads(row["feedback"]) != details:
            raise ValueError("Morning feedback is already recorded for this candidate.")
    else:
        owner = nonempty(args.owner, "owner")
        if action == "claim" and phase == "approved":
            if args.dev:
                tip = revision_at_ref(repo, "refs/remotes/origin/dev")
                if not tip or not is_ancestor(repo, proposal["base"], tip):
                    raise ValueError("Fetch origin/dev and confirm it contains the approved baseline.")
                proposals[key] = {**proposal, "base": tip}
            reasons, predecessors = claim_plan(key, rows, proposals, reading_nights(db, runs), controller)
            if reasons:
                raise ValueError(" ".join(reasons))
            try:
                approval = next(event for event in reversed(state["history"]) if event["event"] == "approve")
                base = integrate_dependencies(repo, proposals[key], predecessors, approval["at"])
                if args.dev and base != proposals[key]["base"]:
                    raise ValueError("The local prerequisites must land in dev before work starts.")
            except ValueError as error:
                db.execute("UPDATE agile_tasks SET note=? WHERE id=?", (str(error), key))
                db.commit()
                raise
            db.execute("UPDATE agile_tasks SET phase='claimed',owner=?,effective_base=?,note=NULL WHERE id=?",
                       (owner, base, key))
            record_event(db, key, "claimed", owner=owner, base=base, predecessors=predecessors,
                         **({"target": "dev"} if args.dev else {}))
            db.commit()
            # Reacquire ownership after durably reserving scope and before creating the checkout.
            db.execute("BEGIN IMMEDIATE")
            row = task_rows(db)[key]
            phase = row["phase"]
            state = task_receipt(db, row, proposal, runs)
        if action == "recover":
            new_owner = nonempty(args.new_owner, "new owner")
            reason = nonempty(args.reason, "recovery reason")
            if (phase in ACTIVE_TASKS and row["owner"] == new_owner and owner != new_owner
                    and args.confirm_stopped):
                event = db.execute("SELECT event,details FROM agile_history WHERE task_id=? ORDER BY seq DESC LIMIT 1",
                                   (key,)).fetchone()
                if event["event"] == "recovered" and json.loads(event["details"]) == {
                    "previous_owner": owner, "owner": new_owner, "reason": reason
                }:
                    return task_receipt(db, row, proposal, runs)
            if not args.confirm_stopped or new_owner == owner or phase not in ACTIVE_TASKS or row["owner"] != owner:
                raise ValueError("Recovery requires confirming the exact stopped owner and naming a different owner.")
            db.execute("UPDATE agile_tasks SET owner=? WHERE id=?", (new_owner, key))
            record_event(db, key, "recovered", previous_owner=owner, owner=new_owner, reason=reason)
        elif row["owner"] != owner:
            raise ValueError("The task requires explicit approval and the matching owner.")
        elif action == "record-dev" and phase in ("claimed", "passed"):
            record_dev_delivery(db, row, state, args, repo, directory)
        elif action == "claim" and phase == "claimed":
            already_dev = state["permissions"] == "verified-pr-to-dev"
            if args.dev and not already_dev and not fetched_dev_contains(repo, state["base"]):
                raise ValueError("Fetch origin/dev and confirm it contains the claimed base.")
            ensure_task_workspace(directory, repo, state, allow_commits=args.dev or already_dev)
            if args.dev and not already_dev:
                record_event(db, key, "target-changed", owner=owner, target="dev", base=state["base"])
        elif action in ("resume", "retry") and phase in ("claimed", {"resume": "blocked", "retry": "failed"}[action]):
            ensure_task_workspace(directory, repo, state, allow_commits=state["permissions"] == "verified-pr-to-dev")
            if phase != "claimed":
                db.execute("UPDATE agile_tasks SET phase='claimed',note=NULL,patch_sha256=NULL WHERE id=?", (key,))
                record_event(db, key, action, owner=owner)
        elif action == "block" or (action == "finish" and args.result == "failed"):
            target = "blocked" if action == "block" else "failed"
            reason = nonempty(args.reason, "attempt reason")
            if phase == target and row["note"] == reason:
                return task_receipt(db, row, proposal, runs, [reason])
            if phase != "claimed":
                raise ValueError(f"Cannot record {target} in phase {phase}.")
            db.execute("UPDATE agile_tasks SET phase=?,note=? WHERE id=?", (target, reason, key))
            record_event(db, key, target, owner=owner, reason=reason, patch_sha256=row["patch_sha256"])
        elif action == "checkpoint" and phase == "claimed":
            tree = ensure_task_workspace(directory, repo, state)
            patch = candidate_patch(tree, state, repo)
            digest = hashlib.sha256(patch).hexdigest()
            write_checkpoint(directory, patch)
            db.execute("UPDATE agile_tasks SET patch_sha256=? WHERE id=?", (digest, key))
        elif action == "finish" and phase in ("claimed", "publishing", "passed"):
            if state["permissions"] == "verified-pr-to-dev":
                raise ValueError("Use record-dev to complete a dev claim after its verified PR merges.")
            if phase == "claimed":
                tree = ensure_task_workspace(directory, repo, state)
                stage_publication(db, row, state, directory, tree, repo)
                db.execute("BEGIN IMMEDIATE")
                row = task_rows(db)[key]
                if row["owner"] != owner:
                    raise ValueError("Another owner now holds the task.")
            if row["phase"] == "publishing":
                publish_task(db, row, repo, directory, state["claimed_at"])
            elif revision_at_ref(repo, review_ref(key)) != row["candidate_commit"]:
                raise ValueError("The published local candidate ref is missing or changed.")
        else:
            raise ValueError(f"Cannot {action} a task in phase {phase}.")
    row = task_rows(db)[key]
    reasons = [row["note"]] if row["phase"] in ("blocked", "failed") and row["note"] else []
    return task_receipt(db, row, json.loads(row["proposal"]), runs, reasons)


def read_status(ledger, args, runs, repo):
    if not ledger.exists():
        return {"legacy": []} if args.action == "legacy" else {
            "tasks": [], "ready": [], "legacy": [], "phase": "empty"
        }
    with closing(sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True, timeout=30)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        if args.action == "legacy":
            return {"legacy": reading_nights(db, runs, args.night)}
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agile_tasks'").fetchone():
            status = agile_status(db, runs, repo, args.batch)
        else:
            status = {"tasks": [], "ready": [], "legacy": reading_nights(db, runs)}
        nights = status["legacy"]
        latest = next((night for night in nights if night["phase"] in OPEN), nights[0] if nights else None)
        return {**(latest or {"phase": "empty"}), **status}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    status = actions.add_parser("status")
    status.add_argument("--batch")
    legacy = actions.add_parser("legacy")
    legacy.add_argument("--night")
    propose = actions.add_parser("propose")
    propose.add_argument("--file", type=Path, required=True)
    decide = actions.add_parser("decide")
    decide.add_argument("--id", required=True)
    decide.add_argument("--decision", choices=("approve", "reject", "defer", "revise"), required=True)
    decide.add_argument("--reason", required=True)
    decide.add_argument("--file", type=Path)
    decide.add_argument("--priority", type=int)
    decide.add_argument("--reply", help="Original human reply, preserved verbatim")
    decide.add_argument("--chat-id", help="Source Codex chat ID; defaults to CODEX_THREAD_ID")
    feedback = actions.add_parser("feedback", help="Record human review of a passed local candidate")
    feedback.add_argument("--id", required=True)
    feedback.add_argument("--decision", choices=("accept", "return", "decline"), required=True)
    feedback.add_argument("--reason", required=True, help="Original human reply")
    feedback.add_argument("--chat-id", help="Source Codex chat ID; defaults to CODEX_THREAD_ID")
    for name in ("claim", "checkpoint", "block", "resume", "retry", "finish", "recover", "record-dev"):
        command = actions.add_parser(name)
        command.add_argument("--id", required=True)
        command.add_argument("--owner", required=True)
        if name == "claim":
            command.add_argument("--dev", action="store_true",
                                 help="Claim from fetched origin/dev or convert your existing claim")
        if name == "record-dev":
            command.add_argument("--pr", required=True)
            command.add_argument("--head", required=True, help="Full SHA independently verified before merge")
        if name in ("block", "recover", "finish"):
            command.add_argument("--reason", required=name != "finish")
        if name == "finish":
            command.add_argument("--result", choices=("passed", "failed"), default="passed")
        if name == "recover":
            command.add_argument("--new-owner", required=True)
            command.add_argument("--confirm-stopped", action="store_true")
    versions = actions.add_parser("versions", help="Capture the current session and skill versions for a daytime run")
    versions.add_argument("--run-dir", type=Path, required=True)
    reconcile = actions.add_parser("legacy-recover")
    reconcile.add_argument("--night", required=True)
    reconcile.add_argument("--owner", required=True)
    reconcile.add_argument("--reason", required=True)
    reconcile.add_argument("--confirm-stopped", action="store_true")
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
        ledger = runs / "nightly.sqlite3"
        if args.action in ("status", "legacy"):
            state = read_status(ledger, args, runs, repo)
        else:
            if args.action == "legacy-recover" and not ledger.is_file():
                raise ValueError("There is no historical ledger.")
            runs.mkdir(exist_ok=True)
            path = getattr(args, "file", None)
            document = json.loads(path.read_text(encoding="utf-8-sig")) if path else None
            with closing(sqlite3.connect(ledger, timeout=30)) as db, db:
                db.row_factory = sqlite3.Row
                if args.action == "legacy-recover":
                    state = reconcile_legacy(db, args, runs)
                else:
                    initialize_tasks(db)
                    state = agile_transition(db, args, repo, runs, document)
        print(json.dumps(state, ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, subprocess.CalledProcessError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
