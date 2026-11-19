from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1 import career_assets
from app.models.project import AssetUpdateRun, Project, ProjectFinding, ProjectScan
from app.models.project_analysis import ProjectAnalysis
from app.models.public_content import PublicContentDraft


@pytest.mark.asyncio
async def test_asset_update_uses_only_published_ai_observations(db_session, test_workspace):
    project = Project(
        workspace_id=test_workspace.id, name="Fixture", owner="example", repo="fixture",
        repo_url="https://github.com/example/fixture", last_seen_sha="newer-revision",
    )
    db_session.add(project)
    await db_session.flush()
    scan = ProjectScan(workspace_id=test_workspace.id, project_id=project.id, status="SUCCESS")
    db_session.add(scan)
    await db_session.flush()
    db_session.add(ProjectFinding(
        workspace_id=test_workspace.id, project_id=project.id, scan_id=scan.id, title="Legacy keyword match",
    ))
    draft = PublicContentDraft(
        short_code=f"fixture-{uuid4().hex[:12]}", workspace_id=test_workspace.id, title="Legacy",
        slug="projects/fixture", markdown_body="Historical keyword match", status="DRAFT",
    )
    db_session.add(draft)
    await db_session.flush()
    historical = AssetUpdateRun(
        short_code=f"fixture-{uuid4().hex[:12]}", workspace_id=test_workspace.id,
        project_id=project.id, draft_id=draft.id, stats={},
    )
    report = ProjectAnalysis(
        workspace_id=test_workspace.id, project_id=project.id, request_key="fixture",
        source_revision="reported-revision", status="COMPLETED", report_status="VALID", coverage_status="COMPLETE",
        report={"claims": [
            {"kind": "CODE_OBSERVED", "title": "Observed", "text": "The server loads a queue.",
             "citations": [{"path": "src/server.py", "start_line": 10, "end_line": 15}]},
            {"kind": "DOC_CLAIM", "title": "Unverified", "text": "Claimed dramatic improvement",
             "citations": [{"path": "README.md", "start_line": 1, "end_line": 3}]},
            {"kind": "CODE_OBSERVED", "title": "Uncited", "text": "Uncited statement", "citations": []},
        ]},
    )
    db_session.add_all([historical, report])
    await db_session.flush()
    project.latest_valid_analysis_id = report.id
    await db_session.commit()
    kwargs = {"session": db_session, "ws": test_workspace}

    for project_id in (project.id, None):
        with pytest.raises(HTTPException) as error:
            await career_assets.run_update_pipeline(project_id=project_id, **kwargs)
        assert error.value.status_code == 409
        assert "AI" in error.value.detail
    assert [run["id"] for run in (await career_assets.list_updates(**kwargs))["items"]] == [historical.id]
    with pytest.raises(HTTPException) as error:
        await career_assets.approve_update(historical.id, auto_merge=False, **kwargs)
    assert error.value.status_code == 409
    assert draft.status == "DRAFT"

    report.report_published_at = datetime.now(timezone.utc)
    await db_session.commit()
    result = await career_assets.run_update_pipeline(**kwargs)
    assert result["project"]["id"] == project.id
    assert result["stats"]["analysis_id"] == report.id
    assert result["stats"]["freshness"] == "NEEDS_REVIEW"
    assert result["risk_level"] == "MEDIUM"
    generated = await db_session.get(PublicContentDraft, result["draft"]["id"])
    assert generated.license_status == "UNKNOWN"
    assert "The server loads a queue." in generated.markdown_body
    assert "src/server.py:10-15" in generated.markdown_body
    assert "reported-revision" in generated.markdown_body
    for excluded in ("Claimed dramatic improvement", "Uncited statement", "keyword match"):
        assert excluded not in generated.markdown_body
        assert excluded not in result["diff_markdown"]

    retry = await career_assets.run_update_pipeline(project_id=project.id, **kwargs)
    assert retry["draft"]["slug"] != result["draft"]["slug"]
    assert retry["stats"]["analysis_id"] == report.id

    project.archived_at = datetime.now(timezone.utc)
    await db_session.commit()
    with pytest.raises(HTTPException) as error:
        await career_assets.run_update_pipeline(project_id=project.id, **kwargs)
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        await career_assets.approve_update(result["id"], auto_merge=False, **kwargs)
    assert error.value.status_code == 409
    assert generated.status == "DRAFT"
