"""Exercise the scheduler through its CLI against an isolated Git repository."""

import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime


class AgileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.controller = Path(temp.name) / "controller"
        self.target = Path(temp.name) / "target"
        self.entry = self.controller / "scripts/agent/nightly.py"
        self.entry.parent.mkdir(parents=True)
        self.target.mkdir()
        for name in ("nightly.py", "preview.mjs"):
            shutil.copyfile(Path(__file__).resolve().parents[1] / name, self.entry.with_name(name))
        self.verifier = self.entry.with_name("candidate_delivery.py")
        self.verifier.write_text(
            "import json\n"
            "def verify_delivery(run_dir, workspace, base, patch_sha, project):\n"
            "    receipt = json.loads((run_dir / 'binding.json').read_text())\n"
            "    if receipt != dict(workspace=str(workspace), base=base, patch_sha256=patch_sha, project=project):\n"
            "        raise ValueError('Invalid fixture binding')\n"
            "    return dict(receipt, preview_url='http://127.0.0.1:12345/')\n"
        )
        for repo in (self.controller, self.target):
            (repo / ".gitignore").write_text(".agent-runs/\n")
            (repo / "intro.md").write_text("original\n")
            (repo / "other.md").write_text("independent\n")
            self.git("init", "-q", repo=repo)
            self.git("add", ".", repo=repo)
            self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@localhost",
                     "commit", "-qm", "base", repo=repo)
        self.ledger = self.controller / ".agent-runs/nightly.sqlite3"

    def git(self, *args, repo=None):
        return subprocess.check_output(["git", *args], cwd=repo or self.target, stderr=subprocess.PIPE,
                                       encoding="utf-8").strip()

    def cli(self, *args, code=0):
        result = subprocess.run([sys.executable, self.entry, *args], cwd=self.controller,
                                env=dict(os.environ, CODEX_THREAD_ID="12345678-0000-4000-8000-000000000001"),
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, code, (args, result.stdout, result.stderr))
        return json.loads(result.stdout)

    def proposal(self, key, *scope, repo=None, depends=()):
        repo = repo or self.target
        return dict(id=key, project="Squabble", repo=str(repo), base=self.git("rev-parse", "HEAD", repo=repo),
                    title=key, scope=list(scope), acceptance="observable result", depends_on=list(depends),
                    evidence="fixture")

    def submit(self, *tasks, batch="first", code=0):
        path = self.ledger.parent.parent / f"{batch}.json"
        path.write_text(json.dumps(dict(batch=batch, tasks=tasks)), encoding="utf-8")
        return self.cli("propose", "--file", path, code=code)

    def decide(self, task, decision="approve", *extra):
        return self.cli("decide", "--id", task, "--decision", decision, "--reason", "human decision", *extra)

    def task(self, action, key, *extra, owner="worker", code=0):
        return self.cli(action, "--id", key, "--owner", owner, *extra, code=code)

    def bind(self, key, owner="worker"):
        row = self.task("checkpoint", key, owner=owner)
        binding = {field: row[field] for field in ("workspace", "base", "patch_sha256")}
        binding["project"] = row["project"].casefold()
        (Path(row["run_dir"]) / "binding.json").write_text(json.dumps(binding))
        return row

    def test_claim_dev_converts_existing_worktree_without_moving_its_branch(self):
        task = self.proposal("A", "intro.md")
        self.submit(task)
        self.decide("A")
        local = self.task("claim", "A")
        worktree = Path(local["workspace"])
        self.git("checkout", "-qb", "codex/feature", repo=worktree)
        (worktree / "intro.md").write_text("feature\n")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@localhost",
                 "commit", "-qam", "feature", repo=worktree)
        head = self.git("rev-parse", "HEAD", repo=worktree)
        (worktree / "other.md").write_text("pending edit\n")
        (self.target / "other.md").write_text("upstream edit\n")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@localhost",
                 "commit", "-qam", "advance dev")
        self.git("update-ref", "refs/remotes/origin/dev", "HEAD")

        self.task("claim", "A", "--dev", owner="other", code=2)
        self.assertEqual(self.cli("status")["tasks"][0], local)
        dev = self.task("claim", "A", "--dev")
        self.assertEqual(dev["permissions"], "verified-pr-to-dev")
        self.assertEqual(dev["base"], local["base"])
        self.assertEqual(dev["claimed_at"], local["claimed_at"])
        self.assertEqual(dev["history"][:-1], local["history"])
        self.assertEqual(dev["history"][-1]["target"], "dev")
        self.assertEqual(dev, self.task("claim", "A", "--dev"))
        self.assertEqual(dev, self.task("claim", "A"))
        self.assertEqual(self.git("rev-parse", "HEAD", repo=worktree), head)
        self.assertEqual(self.git("branch", "--show-current", repo=worktree), "codex/feature")
        self.assertEqual((worktree / "other.md").read_text(), "pending edit\n")

    def test_dev_claim_rejects_local_finish_but_can_record_failure(self):
        task = self.proposal("A", "other.md")
        self.submit(task)
        self.decide("A")
        self.git("update-ref", "refs/remotes/origin/dev", task["base"])
        dev = self.task("claim", "A", "--dev")
        (Path(dev["workspace"]) / "other.md").write_text("verified local fixture\n")
        bound = self.bind("A")
        self.task("finish", "A", code=2)
        self.assertEqual(self.cli("status")["tasks"][0], bound)
        failed = self.task("finish", "A", "--result", "failed", "--reason", "check failed")
        self.assertEqual(failed["phase"], "failed")

    def test_dev_delivery_records_github_squash_and_releases_scope(self):
        first = self.proposal("A", "intro.md")
        self.submit(first, self.proposal("B", "intro.md"))
        self.decide("A")
        self.decide("B")
        self.git("update-ref", "refs/remotes/origin/dev", first["base"])
        task = self.task("claim", "A")
        worktree = Path(task["workspace"])
        (worktree / "intro.md").write_text("approved feature\n")
        self.git("add", "intro.md", repo=worktree)
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@localhost",
                 "commit", "-qm", "feature", repo=worktree)
        head = self.git("rev-parse", "HEAD", repo=worktree)
        self.git("merge", "--squash", head)
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@localhost",
                 "commit", "-qm", "squashed PR")
        merged = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/dev", merged)
        run_dir = Path(task["run_dir"])
        (run_dir / "report.md").write_text("Tested approved feature")
        api = dict(url="https://github.com/Lueyu/squabble/pull/1", state="MERGED",
                   baseRefName="dev", headRefOid=head, mergeCommit={"oid": merged},
                   isCrossRepository=False)
        nightly = runpy.run_path(str(self.entry))
        args = type("DevDelivery", (), dict(pr=api["url"], head=head))()
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.row_factory = sqlite3.Row
            with patch("subprocess.check_output", return_value=json.dumps(api)):
                with self.assertRaisesRegex(ValueError, "claim --dev"):
                    nightly["record_dev_delivery"](db, nightly["task_rows"](db)["A"], task, args,
                                                   self.target, run_dir)
        self.assertEqual(self.cli("status")["tasks"][0], task)

        task = self.task("claim", "A", "--dev")
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.row_factory = sqlite3.Row
            with patch("subprocess.check_output", return_value=json.dumps(dict(api, baseRefName="main"))):
                with self.assertRaisesRegex(ValueError, "merged into dev"):
                    nightly["record_dev_delivery"](db, nightly["task_rows"](db)["A"], task, args,
                                                   self.target, run_dir)
            with patch("subprocess.check_output", return_value=json.dumps(api)):
                nightly["record_dev_delivery"](db, nightly["task_rows"](db)["A"], task, args,
                                               self.target, run_dir)
                # Previous versions could record a dev delivery on a local-only claim.
                db.execute("DELETE FROM agile_history WHERE task_id='A' AND event='target-changed'")
                row = nightly["task_rows"](db)["A"]
                historical = nightly["task_receipt"](db, row, first, self.ledger.parent)
                self.assertEqual(historical["permissions"], "verified-pr-to-dev")
                nightly["record_dev_delivery"](db, row, historical, args, self.target, run_dir)
        successor = self.task("claim", "B", "--dev")
        self.assertEqual(successor["base"], merged)
        self.assertEqual(Path(successor["workspace"], "intro.md").read_text(), "approved feature\n")
        status = self.cli("status")["tasks"][0]
        self.assertEqual(status["delivery"], dict(pr=api["url"], head=head, dev_commit=merged))
        self.assertEqual([event["event"] for event in status["history"]].count("landed-dev"), 1)

    def test_import_human_decisions_and_order_across_nights(self):
        first = dict(self.proposal("A", "intro.md"), benefit="before", approach="small change",
                     difficulty="S", kind="verified", sources=["intro.md"], priority="P1")
        second = self.proposal("B", "intro.md", repo=self.controller)
        self.submit(first, second)
        self.submit(first, batch="next")
        self.assertEqual(self.cli("status")["ready"], [])
        self.task("claim", "A", code=2)
        self.decide("A", "defer")
        self.decide("B")
        revised = self.controller / "revised.json"
        revised.write_text(json.dumps(dict(first, title="Revised", acceptance="new criterion", benefit="after")))
        self.decide("A", "revise", "--file", revised)
        self.task("claim", "A", code=2)
        reply = "  Approve A after the edits.\nPut it first.  "
        self.decide("A", "approve", "--priority", "99", "--reply", reply)
        self.decide("A", "approve", "--priority", "99", "--reply", reply)
        self.submit(first, batch="later")
        self.submit(dict(first, title="unexpected"), batch="conflict", code=2)
        status = self.cli("status")
        self.assertEqual(status["ready"], ["A", "B"])
        self.assertEqual([row["id"] for row in self.cli("status", "--batch", "next")["tasks"]], ["A"])
        self.assertEqual([row["id"] for row in self.cli("status", "--batch", "first")["tasks"]], ["A", "B"])
        state = status["tasks"][0]
        self.assertEqual(state["acceptance"], "new criterion")
        self.assertEqual(state["benefit"], "after")
        self.assertEqual(state["sources"], ["intro.md"])
        self.assertEqual(state["batches"], ["first", "later", "next"])
        self.assertEqual([event["event"] for event in state["history"]], ["proposed", "defer", "revise", "approve"])
        self.assertEqual(state["history"][2]["proposal"]["title"], "Revised")
        approval = state["history"][3]
        self.assertEqual(approval["reply"], reply)
        self.assertEqual(approval["chat_id"], "12345678-0000-4000-8000-000000000001")
        self.assertIsNotNone(datetime.fromisoformat(approval["at"]).tzinfo)

    def test_concurrent_overlaps_and_explicit_recovery(self):
        proposals = [self.proposal("Folder", "docs/"), self.proposal("Leaf", "docs/new.md"),
                     self.proposal("Independent", "other.md")]
        self.submit(*proposals)
        for proposal in proposals:
            self.decide(proposal["id"])
        with ThreadPoolExecutor(3) as pool:
            results = list(pool.map(
                lambda key: subprocess.run(
                    [sys.executable, self.entry, "claim", "--id", key, "--owner", key],
                    cwd=self.controller, capture_output=True, encoding="utf-8"),
                ("Folder", "Leaf", "Independent"),
            ))
        self.assertEqual(sorted(result.returncode for result in results), [0, 0, 2])
        self.assertEqual(results[2].returncode, 0)
        winner = next(json.loads(result.stdout) for result in results[:2] if not result.returncode)
        key, tree = winner["id"], Path(winner["workspace"])
        (tree / "docs").mkdir()
        (tree / "docs/new.md").write_text("preserved\n")
        self.task("checkpoint", key, owner=key)
        self.task("block", key, "--reason", "waiting for evidence", owner=key)
        self.task("recover", key, "--new-owner", "next", "--reason", "stopped", owner=key, code=2)
        recover = ("--new-owner", "next", "--reason", "stopped", "--confirm-stopped")
        self.task("recover", key, *recover, owner=key)
        self.task("recover", key, *recover, owner=key)
        self.task("resume", key, owner=key, code=2)
        self.task("resume", key, owner="next")
        self.assertEqual((tree / "docs/new.md").read_text(), "preserved\n")
        failed = self.task("finish", key, "--result", "failed", "--reason", "assertion failed", owner="next")
        self.assertEqual(failed["phase"], "failed")
        self.assertEqual(failed["blockers"], ["assertion failed"])
        self.assertEqual(self.task("retry", key, owner="next")["phase"], "claimed")
        self.assertEqual(self.task("retry", key, owner="next")["workspace"], str(tree))

    def test_dependencies_inherit_verified_history_and_auto_integrate_overlap(self):
        first = self.proposal("A", "intro.md")
        follower = self.proposal("B", "intro.md")
        dependent = self.proposal("C", "other.md", depends=("B",))
        self.submit(first, follower, dependent)
        for key in ("A", "B", "C"):
            self.decide(key)
        self.task("claim", "C", code=2)
        a = self.task("claim", "A")
        (Path(a["workspace"]) / "intro.md").write_text("verified A\n")
        self.bind("A")
        a = self.task("finish", "A")
        self.assertEqual(a["delivery"]["preview_url"], "http://127.0.0.1:12345/")
        b = self.task("claim", "B")
        self.assertEqual((Path(b["workspace"]) / "intro.md").read_text(), "verified A\n")
        self.assertIn(a["commit"], self.git("rev-list", b["base"]).splitlines())
        self.assertEqual(b["approved_base"], first["base"])
        self.assertEqual(b["predecessors"][0]["kind"], "overlap")
        self.git("update-ref", "refs/remotes/origin/dev", first["base"])
        self.assertIn("claimed base", self.task("claim", "B", "--dev", code=2)["reason"])
        self.assertEqual(next(row for row in self.cli("status")["tasks"] if row["id"] == "B"), b)
        (Path(b["workspace"]) / "intro.md").write_text("verified B\n")
        self.bind("B")
        b = self.task("finish", "B")
        c = self.task("claim", "C")
        self.assertIn(b["commit"], self.git("rev-list", c["base"]).splitlines())
        self.assertEqual(self.git("rev-parse", "HEAD"), first["base"])
        self.assertEqual((self.target / "intro.md").read_text(), "original\n")

    def test_morning_feedback_excludes_candidates_and_their_predecessor_lineage(self):
        first = self.proposal("A", "intro.md")
        self.submit(first, self.proposal("B", "intro.md"))
        for key in ("A", "B"):
            self.decide(key)
            candidate = self.task("claim", key)
            (Path(candidate["workspace"]) / "intro.md").write_text(f"verified {key}\n")
            self.bind(key)
            candidate = self.task("finish", key)
            if key == "A":
                original = candidate
        self.assertEqual(candidate["predecessors"][0]["id"], "A")

        def feedback(key, decision, code=0):
            return self.cli("feedback", "--id", key, "--decision", decision,
                            "--reason", "Morning review", code=code)

        refs = self.git("show-ref")
        accepted = feedback("B", "accept")
        self.assertEqual(accepted["review"]["decision"], "accept")
        returned = feedback("A", "return")
        self.assertEqual(returned, feedback("A", "return"))
        self.assertEqual(returned["review"]["reason"], "Morning review")
        self.assertEqual(returned["review"]["chat_id"], "12345678-0000-4000-8000-000000000001")
        self.assertIsNotNone(datetime.fromisoformat(returned["review"]["at"]).tzinfo)
        self.assertEqual(returned["history"][:-1], original["history"])
        for field in ("phase", "commit", "ref", "patch_sha256", "delivery"):
            self.assertEqual(returned[field], original[field])
        self.assertEqual(self.git("show-ref"), refs)

        revision = dict(self.proposal("A2", "intro.md"), base=original["commit"])
        self.submit(self.proposal("C", "intro.md"),
                    self.proposal("D", "other.md", depends=("B",)),
                    self.proposal("E", "other.md", depends=("A",)),
                    revision, batch="morning")
        feedback("C", "decline", code=2)
        for key in ("C", "D", "E"):
            self.decide(key)
        status = self.cli("status")
        self.assertEqual(status["ready"], ["C"])
        self.assertEqual(next(task for task in status["tasks"] if task["id"] == "A")["review"],
                         returned["review"])
        self.assertIn("A", self.task("claim", "D", code=2)["reason"])
        self.assertIn("A", self.task("claim", "E", code=2)["reason"])
        clean = self.task("claim", "C")
        self.assertEqual(clean["base"], first["base"])
        self.assertEqual(clean["predecessors"], [])
        (Path(clean["workspace"]) / "intro.md").write_text("verified C\n")
        self.bind("C")
        self.task("finish", "C")
        self.assertEqual(feedback("C", "decline")["review"]["decision"], "decline")
        self.decide("A2")
        revised = self.task("claim", "A2")
        self.assertEqual(revised["base"], original["commit"])
        self.assertEqual(revised["predecessors"], [])

    def test_finish_retries_after_ref_publication_and_keeps_accepted_patch(self):
        self.submit(self.proposal("A", "intro.md"))
        self.decide("A")
        row = self.task("claim", "A")
        directory, tree = Path(row["run_dir"]), Path(row["workspace"])
        (tree / "intro.md").write_text("candidate\n")
        row = self.bind("A")
        (tree / "intro.md").write_text("tampered\n")
        self.assertIn("changed", self.task("finish", "A", code=2)["reason"])
        (tree / "intro.md").write_text("candidate\n")
        binding = directory / "binding.json"
        saved = binding.read_bytes()
        binding.write_text("{}")
        self.assertIn("binding", self.task("finish", "A", code=2)["reason"])
        binding.write_bytes(saved)
        with closing(sqlite3.connect(self.ledger, isolation_level=None)) as db:
            db.execute("""CREATE TRIGGER interrupt_pass BEFORE UPDATE OF phase ON agile_tasks
                          WHEN NEW.phase='passed' BEGIN SELECT RAISE(ABORT,'fixture interrupted'); END""")
            self.assertIn("fixture interrupted", self.task("finish", "A", code=2)["reason"])
            db.execute("DROP TRIGGER interrupt_pass")
        pending = self.cli("status")["tasks"][0]
        self.assertEqual(pending["phase"], "publishing")
        self.assertEqual(self.git("rev-parse", pending["ref"]), pending["commit"])
        self.task("checkpoint", "A", code=2)
        binding.unlink()
        (directory / "change.patch").write_text("tampered after verification")
        passed = self.task("finish", "A")
        self.assertEqual(passed["phase"], "passed")
        self.assertEqual(passed["patch_sha256"], row["patch_sha256"])
        self.assertEqual(self.git("show", f"{passed['commit']}:intro.md"), "candidate")
        date = self.git("show", "-s", "--format=%ct", passed["commit"])
        self.assertEqual(int(date), int(datetime.fromisoformat(passed["claimed_at"]).timestamp()))
        self.assertEqual(self.task("finish", "A"), passed)

    def test_dirty_shared_checkout_does_not_block_or_enter_candidate(self):
        self.submit(self.proposal("A", "intro.md"))
        self.decide("A")
        (self.target / "intro.md").write_text("human edit\n")
        claimed = self.task("claim", "A")
        workspace = Path(claimed["workspace"])
        self.assertEqual((workspace / "intro.md").read_text(), "original\n")
        self.task("claim", "A", owner="different owner", code=2)
        self.assertEqual(self.task("claim", "A")["workspace"], str(workspace))
        (workspace / "intro.md").write_text("isolated edit\n")
        self.bind("A")
        passed = self.task("finish", "A")
        self.assertEqual(self.git("show", f"{passed['commit']}:intro.md"), "isolated edit")
        self.assertEqual((self.target / "intro.md").read_text(), "human edit\n")

    def test_legacy_open_owner_and_read_only_status(self):
        self.assertEqual(self.cli("status")["tasks"], [])
        self.assertFalse(self.ledger.exists())
        self.ledger.parent.mkdir()
        fixture = ("2026-11-19", "legacy owner", "delivery", self.git("rev-parse", "HEAD", repo=self.controller),
                   "2000-01-01", '["intro.md"]', "historic", "preserve")
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.execute("CREATE TABLE nights(day,owner,phase,base,deadline,scope,approval,note)")
            db.execute("INSERT INTO nights VALUES(?,?,?,?,?,?,?,?)", fixture)
        before = self.ledger.read_bytes()
        self.assertEqual(self.cli("legacy", "--night", fixture[0])["legacy"][0]["owner"], fixture[1])
        self.assertEqual(self.cli("legacy", "--night", "absent")["legacy"], [])
        self.assertEqual(self.ledger.read_bytes(), before)
        self.submit(self.proposal("A", "intro.md", repo=self.controller))
        self.decide("A")
        self.assertIn("legacy night", self.task("claim", "A", code=2)["reason"])
        with closing(sqlite3.connect(self.ledger)) as db:
            self.assertEqual(db.execute("SELECT * FROM nights").fetchone(), fixture)
        reconcile = ("legacy-recover", "--night", fixture[0], "--owner", fixture[1], "--reason", "owner stopped")
        self.cli(*reconcile, code=2)
        row = self.cli(*reconcile, "--confirm-stopped")
        self.assertEqual((row["phase"], row["owner"]), ("interrupted", fixture[1]))
        self.assertEqual(self.cli(*reconcile, "--confirm-stopped"), row)
        self.assertIn("A", self.cli("status")["ready"])

    def test_unapproved_legacy_write_paths_are_unavailable(self):
        commands = (("begin",), ("prepare", "--approval", "arbitrary", "--paths", "intro.md"),
                    ("checkpoint",), ("finish", "passed", "--note", "arbitrary"))
        for args in commands:
            result = subprocess.run([sys.executable, self.entry, *args], cwd=self.controller,
                                    capture_output=True, encoding="utf-8")
            self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
        self.assertFalse(self.ledger.exists())
        version_dir = self.ledger.parent / "versions-fixture"
        result = subprocess.check_output([sys.executable, self.entry, "versions", "--run-dir", version_dir],
                                         cwd=self.controller, encoding="utf-8")
        self.assertEqual(Path(result.strip()), version_dir / "versions.json")
        self.assertIn("skill_sha256", json.loads((version_dir / "versions.json").read_bytes()))

    def test_cross_project_dependency_waits_without_merging(self):
        upstream = self.proposal("A", "intro.md")
        downstream = dict(self.proposal("B", "intro.md", repo=self.controller, depends=("A",)),
                          project="CareerPilot")
        self.submit(upstream, downstream)
        self.decide("A")
        self.decide("B")
        self.assertIn("Dependency A", self.task("claim", "B", code=2)["reason"])
        a = self.task("claim", "A")
        (Path(a["workspace"]) / "intro.md").write_text("verified A\n")
        self.bind("A")
        a = self.task("finish", "A")
        b = self.task("claim", "B")
        self.assertEqual(b["base"], downstream["base"])
        self.assertEqual(b["predecessors"], [
            {"id": "A", "kind": "cross-project", "commit": a["commit"], "ref": a["ref"]}
        ])
        self.assertEqual((Path(b["workspace"]) / "intro.md").read_text(), "original\n")

    def test_stale_overlap_requires_inheritance_and_merge_conflict_is_visible(self):
        self.submit(self.proposal("A", "intro.md"))
        self.decide("A")
        a = self.task("claim", "A")
        (Path(a["workspace"]) / "intro.md").write_text("verified A\n")
        self.bind("A")
        a = self.task("finish", "A")
        (self.target / "intro.md").write_text("other baseline\n")
        self.git("add", "intro.md")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@localhost", "commit", "-qm", "advance")
        unrelated = self.proposal("B", "intro.md")
        conflicting = self.proposal("C", "intro.md", depends=("A",))
        self.submit(unrelated, conflicting, batch="advanced")
        self.decide("B")
        self.decide("C")
        self.assertIn("different base", self.task("claim", "B", code=2)["reason"])
        self.assertIn("Cannot integrate dependency A", self.task("claim", "C", code=2)["reason"])
        status = self.cli("status", "--batch", "advanced")
        self.assertEqual(status["ready"], [])
        self.assertIn("Cannot integrate dependency A", status["tasks"][1]["blockers"][0])
        self.assertFalse(Path(status["tasks"][1]["workspace"]).exists())
        self.assertEqual(self.git("rev-parse", a["ref"]), a["commit"])

    @unittest.skipUnless(shutil.which("node"), "Node is required for a CareerPilot preview")
    def test_careerpilot_requires_strict_receipt_and_preview_in_addition_to_project_binding(self):
        self.submit(dict(self.proposal("CP", "intro.md"), project="CareerPilot"))
        self.decide("CP")
        row = self.task("claim", "CP")
        workspace, run = Path(row["workspace"]), Path(row["run_dir"])
        (workspace / "intro.md").write_text("candidate\n")
        (workspace / "other.md").write_text("outside scope\n")
        self.assertIn("scope", self.task("checkpoint", "CP", code=2)["reason"])
        (workspace / "other.md").write_text("independent\n")
        row = self.bind("CP")
        self.assertIn("verify.json", self.task("finish", "CP", code=2)["reason"])
        digest = row["patch_sha256"]
        attempt = "attempt-20261119T000000000Z-123456abcdef"
        pinned = run / attempt
        pinned.mkdir()
        patch = (run / "change.patch").read_bytes()
        (run / "report.md").write_text("report\n")
        (pinned / "candidate.patch").write_bytes(patch)
        verify = {"schema_version": 1, "attempt": attempt, "status": "passed", "exit_code": 0,
                  "checked_commit": row["base"], "workspace": str(workspace), "checks": [], "limitations": [],
                  "patch_mode": "strict", "patch_base": row["base"], "checked_patch_sha256": digest,
                  "checked_patch_after_sha256": digest, "change_patch": {"sha256": digest,
                  "evidence": "candidate.patch", "source": "change.patch"}}
        (pinned / "verify.json").write_text(json.dumps(verify))
        shutil.copyfile(pinned / "verify.json", run / "verify.json")
        self.assertIn("local preview", self.task("finish", "CP", code=2)["reason"])
        subprocess.run(["node", self.entry.with_name("preview.mjs"), run, "0", "--check"],
                       check=True, capture_output=True)
        preview, = pinned.glob("preview-*/http-body.html")
        body = preview.read_bytes()
        preview.write_bytes(b"tampered")
        self.assertIn("local preview", self.task("finish", "CP", code=2)["reason"])
        preview.write_bytes(body)
        passed = self.task("finish", "CP")
        self.assertEqual(passed["delivery"]["strict_receipt"]["attempt"], attempt)
        self.assertEqual(hashlib.sha256(patch).hexdigest(), passed["patch_sha256"])


if __name__ == "__main__":
    unittest.main()
