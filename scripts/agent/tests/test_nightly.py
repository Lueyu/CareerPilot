"""Exercise the scheduler through its CLI against an isolated Git repository."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from uuid import uuid4


class NightlyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.entry = self.repo / "scripts/agent/nightly.py"
        self.entry.parent.mkdir(parents=True)
        for name in ("nightly.py", "preview.mjs"):
            shutil.copyfile(Path(__file__).resolve().parents[1] / name, self.entry.with_name(name))
        self.owner = str(uuid4())
        self.env = dict(os.environ, CODEX_HOME=str(self.repo / "dummy-codex-home"))
        (self.repo / ".gitignore").write_text(".agent-runs/\n")
        (self.repo / "intro.md").write_text("first\n")
        for args in (["init", "-q"], ["add", "."], ["-c", "user.name=Pilot",
                     "-c", "user.email=pilot@localhost", "commit", "-qm", "pilot fixture"]):
            subprocess.run(["git", *args], cwd=self.repo, capture_output=True, check=True)

    def run_cli(self, *args, owner=None, code=0, checkout=None):
        env = dict(self.env, CODEX_THREAD_ID=owner or self.owner)
        root = checkout or self.repo
        result = subprocess.run([sys.executable, root / self.entry.relative_to(self.repo), *args],
                                cwd=root, env=env, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, code, (result.stdout, result.stderr))
        return json.loads(result.stdout)

    def test_concurrent_starts_allow_only_one_owner_and_one_daily_task(self):
        owners = [str(uuid4()), str(uuid4())]
        linked = self.repo / ".agent-runs/linked"
        subprocess.run(["git", "worktree", "add", "--detach", linked], cwd=self.repo,
                       check=True, capture_output=True)
        with ThreadPoolExecutor(2) as pool:
            requests = zip(owners, (self.repo, linked))
            rows = list(pool.map(lambda pair: self.run_cli("begin", owner=pair[0], checkout=pair[1]), requests))
        self.assertEqual(sorted(row["proceed"] for row in rows), [False, True])
        self.assertEqual(rows[0]["owner"], rows[1]["owner"])
        self.assertEqual(rows[0]["run_dir"], rows[1]["run_dir"])
        self.assertFalse((linked / ".agent-runs").exists())
        winner, loser = sorted(rows, key=lambda row: not row["proceed"])
        loser_id = owners[rows.index(loser)]
        prepare = ("prepare", "--approval", "fixture:shared-entry", "--paths", "intro.md")
        self.assertIn("owns", self.run_cli(*prepare, owner=loser_id, checkout=linked, code=2)["reason"])
        row = self.run_cli(*prepare, owner=winner["owner"], checkout=linked)
        self.assertEqual(Path(row["task_workspace"]).parent, Path(winner["run_dir"]))
        self.assertEqual(self.run_cli(*prepare, owner=winner["owner"])["task_workspace"], row["task_workspace"])
        self.run_cli("finish", "blocked", "--note", "fixture complete", owner=winner["owner"])
        self.assertFalse(self.run_cli("begin", checkout=linked)["proceed"])

    def test_dirty_scoped_file_blocks_preparation_and_allows_blocked_finish(self):
        self.run_cli("begin")
        args = ("prepare", "--approval", "fixture-1", "--paths", "intro.md")
        (self.repo / "intro.md").write_text("another editor\n")
        self.assertIn("local changes", self.run_cli(*args, code=2)["reason"])
        self.assertEqual(self.run_cli("finish", "blocked", "--note", "dirty path")["phase"], "blocked")
        self.assertEqual((self.repo / "intro.md").read_text(), "another editor\n")

    def test_no_candidate_finishes_without_a_worktree(self):
        row = self.run_cli("begin")
        self.assertEqual(self.run_cli("finish", "no_task", "--note", "no current evidence")["phase"], "no_task")
        self.assertFalse(Path(row["task_workspace"]).exists())

    @unittest.skipUnless(shutil.which("node"), "Node is required to run the preview check")
    def test_acceptance_belongs_to_the_recorded_workspace_patch_and_preview(self):
        self.run_cli("begin")
        row = self.run_cli("prepare", "--approval", "fixture-2", "--paths", "intro.md")
        tree, run = Path(row["task_workspace"]), Path(row["run_dir"])
        (tree / "intro.md").write_text("candidate\n")
        self.run_cli("checkpoint")
        (run / "report.md").write_text("Synthetic test receipt\n")
        attempt = "attempt-20261119T000000000Z-123456abcdef"
        pinned = run / attempt
        pinned.mkdir()
        patch = (run / "change.patch").read_bytes()
        (pinned / "candidate.patch").write_bytes(patch)
        digest = hashlib.sha256(patch).hexdigest()
        data = {"schema_version": 1, "status": "passed", "exit_code": 0, "attempt": attempt,
                "checked_commit": row["base"], "workspace": str(self.repo), "checks": [], "limitations": [],
                "patch_mode": "strict", "patch_base": row["base"], "checked_patch_sha256": digest,
                "checked_patch_after_sha256": digest, "change_patch": {"sha256": digest,
                "evidence": "candidate.patch", "source": "change.patch"}}
        receipt = run / "verify.json"
        original = pinned / "verify.json"

        def save_receipts():
            original.write_bytes(json.dumps(data).encode("utf-8"))
            shutil.copyfile(original, receipt)

        save_receipts()
        finish = ("finish", "passed", "--note", "fixture only")
        self.assertIn("workspace", self.run_cli(*finish, code=2)["reason"])
        data["workspace"] = str(tree)
        save_receipts()
        receipt.write_bytes(original.read_bytes() + b"\n")
        self.assertIn("receipt", self.run_cli(*finish, code=2)["reason"])
        shutil.copyfile(original, receipt)
        (tree / "intro.md").write_text("changed since verification\n")
        self.assertIn("changed", self.run_cli(*finish, code=2)["reason"])
        (tree / "intro.md").write_text("candidate\n")
        self.assertIn("local preview", self.run_cli(*finish, code=2)["reason"])
        subprocess.run(["node", self.entry.with_name("preview.mjs"), run, "0", "--check"],
                       env=self.env, check=True, capture_output=True)
        preview, = pinned.glob("preview-*/preview.json")
        data["reason"] = "Changed after preview"
        save_receipts()
        self.assertIn("local preview", self.run_cli(*finish, code=2)["reason"])
        del data["reason"]
        save_receipts()
        body = preview.with_name("http-body.html")
        html = body.read_bytes()
        body.write_bytes(b"changed HTTP capture")
        self.assertIn("local preview", self.run_cli(*finish, code=2)["reason"])
        body.write_bytes(html)
        (pinned / "candidate.patch").write_bytes(b"changed saved patch")
        self.assertIn("changed", self.run_cli(*finish, code=2)["reason"])
        (pinned / "candidate.patch").write_bytes(patch)
        row = self.run_cli(*finish)
        self.assertEqual(row["phase"], "passed")
        self.assertEqual(row["acceptance"]["attempt"], attempt)
        self.assertEqual(self.run_cli("status")["acceptance"], row["acceptance"])
        self.assertEqual((self.repo / "intro.md").read_text(), "first\n")

    def test_staged_scope_and_failed_finish(self):
        self.run_cli("begin")
        row = self.run_cli("prepare", "--approval", "fixture-3", "--paths", "new.md")
        tree = Path(row["task_workspace"])
        (tree / "new.md").write_text("in scope\n")
        (tree / "intro.md").write_text("outside scope\n")
        subprocess.run(["git", "add", "intro.md"], cwd=tree, check=True, capture_output=True)
        (tree / "intro.md").write_text("first\n")
        self.assertIn("scope", self.run_cli("checkpoint", code=2)["reason"])
        self.assertEqual(self.run_cli("finish", "failed", "--note", "fixture assertion failed")["phase"], "failed")
        self.assertFalse(self.run_cli("begin")["proceed"])

    def test_expired_owner_requires_explicit_recovery(self):
        self.run_cli("begin")
        prepared = self.run_cli("prepare", "--approval", "fixture:recovery", "--paths", "intro.md")
        tree = Path(prepared["task_workspace"])
        (tree / "intro.md").write_text("preserve candidate\n")
        self.run_cli("checkpoint")
        self.assertEqual(self.run_cli("begin")["task_workspace"], str(tree))
        observer = str(uuid4())
        self.run_cli("recover", "--stopped-thread", self.owner, "--note", "deadline still open", owner=observer, code=2)
        with closing(sqlite3.connect(self.repo / ".agent-runs/nightly.sqlite3", isolation_level=None)) as db:
            db.execute("UPDATE nights SET deadline='2000-01-01T00:00:00+08:00'")
        self.assertFalse(self.run_cli("begin")["proceed"])
        self.assertEqual(self.run_cli("status")["phase"], "delivery")
        self.run_cli("checkpoint", code=2)
        self.run_cli("recover", "--stopped-thread", self.owner, "--note", "self", code=2)
        self.run_cli("recover", "--stopped-thread", str(uuid4()), "--note", "wrong", owner=observer, code=2)
        row = self.run_cli("recover", "--stopped-thread", self.owner, "--result", "cancelled", "--note",
                           "Host reported cancellation and the child exited", owner=observer)
        self.assertEqual(row["phase"], "cancelled")
        self.assertEqual((tree / "intro.md").read_text(), "preserve candidate\n")
        self.assertTrue((Path(row["run_dir"]) / "change.patch").is_file())
        self.assertFalse(self.run_cli("begin")["proceed"])


if __name__ == "__main__":
    unittest.main()
