import hashlib
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import candidate_delivery as delivery
import review_board


def git(tree, *args):
    return subprocess.check_output(["git", *args], cwd=tree)


@pytest.fixture
def identity(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    git(tree, "init", "-q")
    git(tree, "config", "user.name", "Preview fixture")
    git(tree, "config", "user.email", "fixture@example.invalid")
    git(tree, "config", "core.autocrlf", "false")
    (tree / "app.txt").write_bytes(b"base\n")
    (tree / "docker-compose.yml").write_bytes(b"services: {}\n")
    git(tree, "add", ".")
    git(tree, "commit", "-qm", "Base")
    base = git(tree, "rev-parse", "HEAD").decode().strip()
    (tree / "app.txt").write_bytes(b"candidate\n")
    run = tmp_path / "run"
    run.mkdir()
    patch = git(tree, "diff", "--no-ext-diff", "--binary", base)
    (run / "change.patch").write_bytes(patch)
    return run, tree, base, hashlib.sha256(patch).hexdigest(), "careerpilot"


def test_start_uses_candidate_snapshot_without_ignored_environment(identity, monkeypatch):
    run, tree, *_ = identity
    (tree / ".git" / "info" / "exclude").write_text(".env\n", encoding="utf-8")
    (tree / ".env").write_text("PRIVATE=ignored", encoding="utf-8")
    monkeypatch.setattr(delivery, "docker_host", lambda: "unix:///var/run/docker.sock")

    def inspect_snapshot(attempt, receipt, compose):
        assert receipt["workspace"] == str(tree)
        assert compose == b"services: {}\n"
        assert (attempt / "source" / "app.txt").read_bytes() == b"candidate\n"
        assert not (attempt / "source" / ".env").exists()
        raise ValueError("Fixture stops before Docker startup.")

    monkeypatch.setattr(delivery, "configure", inspect_snapshot)
    with pytest.raises(ValueError, match="Fixture stops"):
        delivery.start(*identity, ["unused"])
    assert delivery.read(run / delivery.RECEIPT)["status"] == "failed"


def test_candidate_rejects_extra_files_and_requires_browser_for_client(identity):
    run, tree, base, _, project = identity
    (tree / "web").mkdir()
    (tree / "web" / "new.txt").write_bytes(b"visible\n")
    with pytest.raises(ValueError, match="intent-to-add"):
        delivery.candidate(*identity)
    git(tree, "add", "--intent-to-add", "web/new.txt")
    patch = git(tree, "diff", "--no-ext-diff", "--binary", base)
    (run / "change.patch").write_bytes(patch)
    args = run, tree, base, hashlib.sha256(patch).hexdigest(), project
    with pytest.raises(ValueError, match="browser command"):
        delivery.start(*args, ["unused"])


def test_start_preserves_orphan_attempt_and_uses_an_unused_directory(identity, monkeypatch):
    run, *_ = identity
    tokens = iter([UUID(int=1), UUID(int=2)])
    orphan = run / f"candidate-preview-{UUID(int=1).hex}"
    orphan.mkdir()
    (orphan / "partial.log").write_text("interrupted before receipt", encoding="utf-8")
    monkeypatch.setattr(delivery, "uuid4", lambda: next(tokens))
    monkeypatch.setattr(delivery, "docker_host", lambda: "unix:///var/run/docker.sock")

    def inspect_attempt(attempt, receipt, _compose):
        assert attempt == run / f"candidate-preview-{UUID(int=2).hex}"
        assert delivery.read(run / delivery.RECEIPT) == receipt
        raise ValueError("Fixture stops before Docker startup.")

    monkeypatch.setattr(delivery, "configure", inspect_attempt)
    with pytest.raises(ValueError, match="Fixture stops"):
        delivery.start(*identity, ["unused"])
    assert (orphan / "partial.log").read_text() == "interrupted before receipt"


def test_verify_reuses_bound_receipt_after_publication(identity, monkeypatch):
    run, tree, *_ = identity
    binding, _, _ = delivery.candidate(*identity)
    attempt = run / "candidate-preview"
    attempt.mkdir()
    monkeypatch.setenv("LIVE_VENDOR_API_KEY", "private-fixture")
    env = delivery.environment()
    assert "LIVE_VENDOR_API_KEY" not in env
    cmd = [sys.executable, "-c", "assert 2 + 2 == 4; print('assertion passed')"]
    check = delivery.run_check(attempt, "regression", cmd, tree, env)
    app = {"preview_url": "http://127.0.0.1:51001/", "asset": "/bundle.js"}
    receipt = dict(binding, status="passed", regression=check, application=app, preview_url=app["preview_url"])
    delivery.save(run / delivery.RECEIPT, receipt)
    monkeypatch.setattr(delivery, "application_check", lambda *_: app)
    assert delivery.verify_delivery(*identity) == receipt
    git(tree, "commit", "-qam", "Published candidate")
    assert delivery.verify_delivery(*identity) == receipt

    monkeypatch.setattr(delivery, "application_check", lambda *_: dict(app, asset="/other.js"))
    with pytest.raises(ValueError, match="running application differs"):
        delivery.verify_delivery(*identity)
    (attempt / "regression.log").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="regression evidence"):
        delivery.verify_delivery(*identity)
    (tree / "app.txt").write_bytes(b"new edit\n")
    with pytest.raises(ValueError, match="differs from change.patch"):
        delivery.verify_delivery(*identity)


def test_morning_links_only_verified_dev_snapshots_and_keeps_failed_receipts(identity, monkeypatch):
    run, tree, base, _, project = identity
    git(tree, "update-ref", "refs/remotes/origin/dev", base)
    git(tree, "commit", "-qam", "Deployment")
    deployed = git(tree, "rev-parse", "HEAD").decode().strip()
    (run / "change.patch").write_bytes(b"")
    binding, _, _ = delivery.candidate(run, tree, deployed, delivery.digest(b""), project)
    attempt = run / "candidate-preview"
    attempt.mkdir()
    regression = [sys.executable, "-c",
                  "from pathlib import Path; assert Path('app.txt').read_text().strip() == 'candidate'; print('passed')"]
    check = delivery.run_check(attempt, "regression", regression, tree, delivery.environment())
    app = {"preview_url": "http://127.0.0.1:51001/"}
    receipt = dict(binding, status="passed", regression=check, application=app, preview_url=app["preview_url"])
    delivery.save(run / delivery.RECEIPT, receipt)
    monkeypatch.setattr(delivery, "application_check", lambda *_: app)
    failed = run.parent / "failed-preview"
    failed.mkdir()
    delivery.save(failed / delivery.RECEIPT, dict(binding, project="squabble", status="failed"))

    off_dev = "\n".join(review_board.morning([], [failed, run]))
    assert deployed[:12] in off_dev and app["preview_url"] not in off_dev
    assert "Squabble" in off_dev and "failed-preview" in off_dev

    git(tree, "commit", "--allow-empty", "-qm", "Newer dev tip")
    git(tree, "update-ref", "refs/remotes/origin/dev", "HEAD")
    git(tree, "checkout", "--detach", deployed)
    on_dev = "\n".join(review_board.morning([], [failed, run]))
    assert app["preview_url"] in on_dev
    (attempt / "regression.log").write_text("changed", encoding="utf-8")
    assert app["preview_url"] not in "\n".join(review_board.morning([], [run]))


@pytest.mark.parametrize("code", ["pass", "print('# pass 0')", "print('1 skipped in 0.1s')", "raise SystemExit(2)"])
def test_regression_requires_successful_executed_check(tmp_path, code):
    with pytest.raises(ValueError, match="did not pass"):
        delivery.run_check(tmp_path, "regression", [sys.executable, "-c", code], tmp_path, os.environ.copy())


def test_stop_checks_ownership_and_is_idempotent(tmp_path, monkeypatch):
    run = tmp_path / "candidate-preview"
    run.mkdir()
    owner = "candidate-" + "7" * 32
    receipt = {"owner": owner, "docker_host": "unix:///var/run/docker.sock", "status": "passed"}
    delivery.save(tmp_path / delivery.RECEIPT, receipt)
    labels = {delivery.OWNER: owner, "com.docker.compose.project": owner, "com.docker.compose.service": "app",
              "com.docker.compose.project.config_files": str(tmp_path / "foreign-compose.json")}
    item = {"Id": "owned-id", "Config": {"Labels": labels}, "State": {"Running": True}}
    calls = []

    def docker_output(args, **_):
        command = args[3:]
        if command[0] == "ps":
            assert command[-1] == f"label={delivery.OWNER}={owner}"
            return b"owned-id\n"
        if command[0] == "inspect":
            assert command == ["inspect", "owned-id"]
            return delivery.json.dumps([item]).encode()
        calls.append(command)
        item["State"]["Running"] = False
        return b""

    monkeypatch.setattr(delivery, "output", docker_output)
    with pytest.raises(ValueError, match="different deployment"):
        delivery.stop(tmp_path)
    assert not calls
    labels["com.docker.compose.project.config_files"] = str(run / "compose.json")
    first = delivery.stop(tmp_path)
    assert delivery.stop(tmp_path) == first
    assert calls == [["stop", "owned-id"]]
    assert delivery.read(tmp_path / delivery.RECEIPT)["status"] == "passed"
