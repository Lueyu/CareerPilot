"""Practice feedback cannot be presented as a formal dashboard exam."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.api.v1.dashboard import dashboard
from app.models import Exam, JobStandard, JobStandardTarget
from test_assessment_snapshot_review import fixture_question


@pytest.mark.asyncio
async def test_dashboard_excludes_completed_practice(db_session, test_workspace):
    now = datetime.now(timezone.utc)
    db_session.add_all([
        Exam(workspace_id=test_workspace.id, kind='practice', title='Practice feedback fixture', status='COMPLETED', completed_at=now),
        Exam(workspace_id=test_workspace.id, kind='focused', title='Formal exam fixture', status='COMPLETED', completed_at=now),
    ])
    await db_session.flush()
    result = await dashboard(test_workspace, db_session)
    assert [item['title'] for item in result['recent_activity']] == ['Formal exam fixture']


@pytest.mark.asyncio
async def test_dashboard_uses_latest_matching_standard_with_multiple_versions(db_session, test_workspace):
    _, skill = await fixture_question(db_session, test_workspace)
    standards = [JobStandard(role=role, level='P6', version=uuid4().hex[:12], is_seed=True) for role in
        ('AI Agent 应用开发工程师','AI Agent 应用开发工程师','Unrelated fixture role')]
    db_session.add_all(standards)
    await db_session.flush()
    db_session.add(JobStandardTarget(standard_id=standards[1].id, skill_id=skill.id, weight_pct=100, target_level=4.5))
    await db_session.flush()
    try:
        result = await dashboard(test_workspace, db_session)
        assert len(result['domains']) == 1
        assert result['domains'][0]['target_level'] == 4.5
        assert result['domains'][0]['key'] == skill.key
    finally:
        await db_session.execute(delete(JobStandardTarget).where(JobStandardTarget.standard_id.in_([s.id for s in standards])))
        await db_session.execute(delete(JobStandard).where(JobStandard.id.in_([s.id for s in standards])))
        await db_session.commit()
