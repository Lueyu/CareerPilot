"""Local-only boundaries. Unit cases never contact GitHub or the personal DB."""
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.api.v1 import career_assets, public_content
from app.api.v1 import evolution as evolution_api
from app.config import Settings
from app.services import evolution
from app.services.github_publish import GitHubError, GitHubPublisher


@pytest.mark.asyncio
async def test_personal_workspace_is_idempotent_and_contains_no_demo_data(db_session):
    from sqlalchemy import func, select

    from app import seed
    from app.models import Evidence, LearningTask, Workspace, WorkspaceMember

    ensure = getattr(seed, "ensure_personal_workspace", None)
    assert callable(ensure), "startup seed must ensure a clean personal workspace"
    first = await ensure(db_session)
    second = await ensure(db_session)
    assert first.id == second.id
    assert first.slug == "personal" and first.is_demo is False
    assert (await db_session.scalar(select(func.count()).select_from(Workspace).where(Workspace.slug == "personal"))) == 1
    assert (await db_session.scalar(select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.workspace_id == first.id))) == 1
    for model in (Evidence, LearningTask):
        assert (await db_session.scalar(select(func.count()).select_from(model).where(model.workspace_id == first.id))) == 0


@pytest.mark.asyncio
async def test_workspace_list_reflects_database_and_missing_slug_does_not_fall_back(db_session, test_workspace, enrolled_default_owner):
    from app.api.deps import get_workspace
    from app.api.v1 import settings
    from app.services.local_credentials import authenticate
    principal = await authenticate(enrolled_default_owner)

    listing = getattr(settings, "list_workspaces", None)
    assert callable(listing), "workspace switcher must list persisted rows"
    result = await listing(session=db_session, scope=principal)
    assert test_workspace.slug in {item["slug"] for item in result["workspaces"]}
    with pytest.raises(HTTPException) as exc:
        await get_workspace(session=db_session, _auth=principal, x_workspace="missing-workspace-fixture")
    assert exc.value.status_code == 404


class NeverSession:
    async def execute(self, *_args, **_kwargs):
        pytest.fail("disabled action must refuse before database work")

    async def get(self, *_args, **_kwargs):
        pytest.fail("disabled action must refuse before database work")

    async def flush(self):
        pytest.fail("disabled action must not mutate the database")


def test_default_workspace_is_personal():
    assert Settings(_env_file=None).default_workspace_slug == "personal"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD", "post"])
@pytest.mark.asyncio
async def test_publisher_refuses_every_non_get_even_with_token(monkeypatch, method):
    calls = []

    async def network_request(*args, **kwargs):
        calls.append((args, kwargs))
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncClient, "request", network_request)
    with pytest.raises(GitHubError) as exc:
        await GitHubPublisher(token="fixture-token")._request(method, "/repos/fixture/fixture")
    assert exc.value.status_code == 403
    assert exc.value.detail == "public_writes_disabled"
    assert calls == []


@pytest.mark.parametrize("operation,args", [
    ("create_branch", ("fixture/repo", "test", "sha")),
    ("write_file", ("fixture/repo", "file.md", "body", "branch", "msg")),
    ("create_pr", ("fixture/repo", "head", "base", "title", "body")),
    ("merge_pr", ("fixture/repo", 1)),
])
@pytest.mark.asyncio
async def test_dry_run_write_operations_remain_local(operation, args):
    result = await getattr(GitHubPublisher(dry_run=True), operation)(*args)
    assert result["dry_run"] is True


@pytest.mark.asyncio
async def test_dry_run_preview_needs_neither_network_nor_token(monkeypatch):
    async def refuse_network(*_args, **_kwargs):
        pytest.fail("local dry-run must not contact GitHub")

    monkeypatch.setattr(httpx.AsyncClient, "request", refuse_network)
    publisher = GitHubPublisher(dry_run=True)
    assert await publisher.get_base_sha("fixture/repo", "main") == "local-preview"
    assert await publisher.get_file("fixture/repo", "file.md", "main") is None


@pytest.mark.parametrize("operation,args", [
    (public_content.publish_draft, {"draft_id": 1, "dry_run": False}),
    (public_content.rollback_publication, {"pub_id": 1}),
    (career_assets.approve_update, {"run_id": 1, "auto_merge": True, "dry_run": False}),
    (career_assets.rollback_update, {"run_id": 1}),
    (evolution_api.approve, {"cid": 1}),
])
@pytest.mark.asyncio
async def test_write_api_refuses_before_side_effects(operation, args):
    with pytest.raises(HTTPException) as exc:
        await operation(**args, ws=SimpleNamespace(id=1), session=NeverSession())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_evolution_service_cannot_activate_candidate():
    candidate = SimpleNamespace(status="EVALUATED", approved_at=None)
    with pytest.raises(PermissionError, match="disabled"):
        await evolution.approve_candidate(NeverSession(), 1, candidate)
    assert candidate.status == "EVALUATED"
    assert candidate.approved_at is None


@pytest.mark.asyncio
async def test_preexisting_active_policy_is_not_consumed():
    assert await evolution.get_active_candidate(NeverSession(), 1, "exam_selection") is None


def test_environment_cannot_enable_closed_features(monkeypatch):
    monkeypatch.setenv("PUBLIC_WRITES_ENABLED", "true")
    monkeypatch.setenv("EVOLUTION_ACTIVATION_ENABLED", "true")
    cfg = Settings(_env_file=None)
    assert getattr(cfg, "public_writes_enabled", None) is False
    assert getattr(cfg, "evolution_activation_enabled", None) is False


@pytest.mark.asyncio
async def test_personal_startup_can_use_a_custom_loopback_web_port():
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options("/healthz", headers={
            "Origin": "http://127.0.0.1:15173", "Access-Control-Request-Method": "GET",
        })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:15173"


@pytest.mark.skipif(os.name != "nt", reason="Windows local operations scripts")
def test_restore_rejects_original_database_before_any_connection(tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell is not None
    script = Path(__file__).resolve().parents[2] / "deploy" / "restore.ps1"
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(script),
         "-BackupFile", str(tmp_path / "unused.dump"), "-TargetDatabase", "careerpilot"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
    )
    assert result.returncode != 0
    assert "personal database is forbidden" in result.stderr
    assert "Restore verified" not in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows local operations scripts")
def test_failed_backup_does_not_leave_a_fake_archive_or_claim_success(tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell is not None
    script = Path(__file__).resolve().parents[2] / "deploy" / "backup.ps1"
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(script),
         "-Database", "nonexistent_" + uuid4().hex + "_test", "-OutDir", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
    )
    assert result.returncode != 0
    assert "Backup verified" not in result.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_concurrent_start_cannot_overwrite_completed_learning_task(db_session, test_workspace):
    import asyncio

    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.api.v1.learning import start_task
    from app.models import LearningTask

    task = LearningTask(workspace_id=test_workspace.id, title="Concurrent progress fixture",
                        kind="UNDERSTAND", status="PENDING")
    db_session.add(task)
    await db_session.commit()
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    async with factory() as starter, factory() as completer:
        start_pid = await starter.scalar(text("SELECT pg_backend_pid()"))
        completing = (await completer.execute(select(LearningTask).where(
            LearningTask.id == task.id).with_for_update(of=LearningTask))).scalar_one()
        completing.status = "DONE"
        await completer.flush()  # Keep DONE uncommitted while start attempts its transition.
        pending_start = asyncio.create_task(start_task(task.id, ws=test_workspace, session=starter))
        try:
            waiting = False
            for _ in range(100):
                await completer.execute(text("SELECT pg_stat_clear_snapshot()"))
                waiting = await completer.scalar(text(
                    "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"
                ), {"pid": start_pid})
                if waiting:
                    break
                await asyncio.sleep(0.01)
            assert waiting, "start must overlap the uncommitted completion transaction"
            await completer.commit()
            result = await asyncio.wait_for(pending_start, timeout=5)
            assert result["status"] == "DONE"
            await db_session.refresh(task)
            assert task.status == "DONE"
        finally:
            if not pending_start.done():
                pending_start.cancel()
            await asyncio.gather(pending_start, return_exceptions=True)
