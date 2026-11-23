"""Project facts are learning context, never proof of a user's contribution."""
import importlib
import importlib.util
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete

from app.api.v1 import projects
from app.models import Project, ProjectFinding, ProjectScan, Workspace


def context_builder():
    assert importlib.util.find_spec("app.services.project_context"), "project context builder is required"
    return importlib.import_module("app.services.project_context").build_project_context


@pytest.mark.asyncio
async def test_manual_projects_have_no_fake_repository_and_persist_profile(db_session, test_workspace):
    body = projects.ProjectIn(name="工作知识库", kind="manual", profile={
        "background": "公司内部客服知识库", "role": "后端开发", "responsibilities": "实现权限过滤",
    })
    first = await projects.create_project(body, ws=test_workspace, session=db_session)
    second = await projects.create_project(projects.ProjectIn(name="另一个工作项目", kind="manual"), ws=test_workspace, session=db_session)
    assert first["id"] != second["id"]
    assert first["kind"] == "manual"
    assert first["repo_url"] == first["owner"] == first["repo"] == ""
    assert first["profile"]["role"] == "后端开发"
    result = await projects.list_projects(ws=test_workspace, session=db_session)
    assert next(p for p in result["items"] if p["id"] == first["id"])["profile"] == first["profile"]


@pytest.mark.asyncio
async def test_github_creation_remains_compatible_and_duplicate_scoped(db_session, test_workspace):
    body = projects.ProjectIn(name="GitHub project", repo_url="https://github.com/example/knowledge.git")
    result = await projects.create_project(body, ws=test_workspace, session=db_session)
    assert result["kind"] == "github"
    assert result["owner"] == "example" and result["repo"] == "knowledge"
    with pytest.raises(HTTPException) as exc:
        await projects.create_project(body, ws=test_workspace, session=db_session)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_profile_patch_preserves_repository_meta_and_updates_real_data(db_session, test_workspace):
    patch = getattr(projects, "update_project_profile", None)
    assert callable(patch), "project profile must have a persistent edit endpoint"
    result = await projects.create_project(projects.ProjectIn(name="Repository", owner="example", repo="repo", description="repo description"), ws=test_workspace, session=db_session)
    row = await db_session.get(Project, result["id"])
    row.meta = {**row.meta, "languages": {"Python": 10}}
    await db_session.commit()
    await patch(result["id"], projects.ProjectProfileIn(profile={"role": "后端开发", "responsibilities": "写检索接口"}), ws=test_workspace, session=db_session)
    updated = await patch(result["id"], projects.ProjectProfileIn(profile={"role": "", "outcomes": "尚未上线"}), ws=test_workspace, session=db_session)
    assert updated["profile"]["role"] == ""
    assert updated["profile"]["responsibilities"] == "写检索接口"
    assert updated["meta"]["languages"] == {"Python": 10}
    assert updated["description"] == "repo description"
    await db_session.refresh(row)
    assert row.meta["profile"]["outcomes"] == "尚未上线"


@pytest.mark.asyncio
async def test_manual_scan_is_refused_before_network_or_trace(db_session, test_workspace, monkeypatch):
    row = Project(workspace_id=test_workspace.id, name="Manual", repo_url="", owner="", repo="", branch="", meta={"kind": "manual"})
    db_session.add(row)
    await db_session.commit()
    from app.api.v1 import project_autopilot
    async def forbidden(*args, **kwargs):
        pytest.fail("manual projects must never enqueue repository analysis")
    monkeypatch.setattr(project_autopilot.project_jobs, "enqueue", forbidden)
    with pytest.raises(HTTPException) as exc:
        await project_autopilot.reanalyze(row.id, project_autopilot.ReanalyzeIn(acknowledge_source_transfer=True),
                                          ws=test_workspace, session=db_session)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_new_context_snapshots_exclude_legacy_keyword_scans(db_session, test_workspace):
    build = context_builder()
    row = Project(workspace_id=test_workspace.id, name="Knowledge", repo_url="https://github.com/example/knowledge", owner="example", repo="knowledge", meta={
        "profile": {"role": "后端开发", "responsibilities": "实现权限过滤", "outcomes": "还没有量化结果"},
        "token": "never-export-unrelated-meta", "description": "repo intro",
    })
    db_session.add(row)
    await db_session.flush()
    scan = ProjectScan(project_id=row.id, workspace_id=test_workspace.id, status="SUCCESS", commit_sha="9:40:2026-11-16")
    db_session.add(scan)
    await db_session.flush()
    finding = ProjectFinding(project_id=row.id, workspace_id=test_workspace.id, scan_id=scan.id, domain="rag", level="VERIFIED", title="包含检索模块", detail="代码观察，不能归因到个人", file_path="app/search.py")
    db_session.add(finding)
    await db_session.commit()
    first = (await build(db_session, test_workspace.id, [row.id]))[0]
    repeated = (await build(db_session, test_workspace.id, [row.id]))[0]
    assert first == repeated
    assert first["has_personal_context"] is True
    assert first["profile"]["outcomes"] == "还没有量化结果"
    assert "never-export-unrelated-meta" not in str(first)
    assert {f["source"] for f in first["facts"]} == {"user_statement"}
    assert any(f["id"] == f"P{row.id}-role" for f in first["facts"])
    assert not any(f["id"] == f"P{row.id}-finding-{finding.id}" for f in first["facts"])
    assert "scan_fingerprint" not in first
    assert "commit_sha" not in first
    row.meta = {**row.meta, "profile": {"role": "架构师", "responsibilities": "设计架构"}}
    await db_session.delete(finding)
    await db_session.commit()
    changed = (await build(db_session, test_workspace.id, [row.id]))[0]
    assert first["profile"]["role"] == "后端开发"
    assert first["snapshot_fingerprint"] != changed["snapshot_fingerprint"]
    assert not any(f["source"] == "repository_observation" for f in first["facts"])


@pytest.mark.asyncio
async def test_repository_only_context_does_not_imply_personal_contribution(db_session, test_workspace):
    build = context_builder()
    row = Project(workspace_id=test_workspace.id, name="Repository", repo_url="https://github.com/example/repo", owner="example", repo="repo", meta={"profile": {"role": "参与者"}})
    db_session.add(row)
    await db_session.commit()
    assert (await build(db_session, test_workspace.id, [row.id]))[0]["has_personal_context"] is False
    assert await build(db_session, test_workspace.id, []) == []


@pytest.mark.asyncio
async def test_context_and_profile_never_access_another_workspace(db_session, test_workspace):
    build = context_builder()
    other = Workspace(slug="other-project-" + uuid4().hex, name="Other", is_demo=False)
    db_session.add(other)
    await db_session.flush()
    row = Project(workspace_id=other.id, name="Private project", repo_url="", owner="", repo="", branch="", meta={"kind": "manual", "profile": {"role": "secret"}})
    db_session.add(row)
    await db_session.commit()
    try:
        with pytest.raises(ValueError, match="project"):
            await build(db_session, test_workspace.id, [row.id])
        with pytest.raises(HTTPException) as exc:
            await projects.update_project_profile(row.id, projects.ProjectProfileIn(profile={"role": "overwrite"}), ws=test_workspace, session=db_session)
        assert exc.value.status_code == 404
    finally:
        await db_session.execute(delete(Workspace).where(Workspace.id == other.id))
        await db_session.commit()


def test_project_profile_rejects_unknown_fields_and_excessive_text():
    profile_type = getattr(projects, "ProjectProfileIn", None)
    assert profile_type is not None
    with pytest.raises(ValidationError):
        profile_type(profile={"api_key": "secret"})
    with pytest.raises(ValidationError):
        profile_type(profile={"role": "x" * 5001})


@pytest.mark.asyncio
async def test_scan_id_cannot_read_findings_from_a_different_project(db_session, test_workspace):
    one = Project(workspace_id=test_workspace.id, name="One", repo_url="", owner="one", repo="one")
    two = Project(workspace_id=test_workspace.id, name="Two", repo_url="", owner="two", repo="two")
    db_session.add_all([one, two])
    await db_session.flush()
    scan = ProjectScan(project_id=two.id, workspace_id=test_workspace.id, status="SUCCESS", stats={"stats": {"private": True}})
    db_session.add(scan)
    await db_session.flush()
    db_session.add(ProjectFinding(project_id=two.id, workspace_id=test_workspace.id, scan_id=scan.id, title="Different project's finding"))
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await projects.list_findings(one.id, scan_id=scan.id, ws=test_workspace, session=db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_import_registers_without_scanning_and_preserves_personal_profile(db_session, test_workspace):
    result = await projects.create_project(projects.ProjectIn(
        name="Repository", owner="example", repo="scan-fixture",
        profile={"role": "后端开发", "responsibilities": "实现鉴权"},
    ), ws=test_workspace, session=db_session)
    assert result['run_id'] is None
    assert not any(route.path == '/projects/{project_id}/scan' for route in projects.router.routes)
    updated = await projects.get_project(result["id"], ws=test_workspace, session=db_session)
    assert updated["profile"]["responsibilities"] == "实现鉴权"
    assert updated["latest_scan"] is None
