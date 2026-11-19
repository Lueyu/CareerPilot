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

@pytest.mark.asyncio
async def test_dashboard_recommends_only_due_retests_with_completed_learning(db_session, test_workspace):
    from datetime import timedelta

    from app.api.v1.learning import _today
    from app.models import LearningTask

    _, skill = await fixture_question(db_session, test_workspace)
    workspace_id = test_workspace.id
    today = _today()
    completed = LearningTask(
        workspace_id=workspace_id, title="Completed source", kind="PRACTICE", status="DONE",
    )
    unfinished = LearningTask(
        workspace_id=workspace_id, title="Unfinished source", kind="PRACTICE", status="PENDING",
    )
    db_session.add_all([completed, unfinished])
    await db_session.flush()

    due = LearningTask(
        workspace_id=workspace_id, skill_id=skill.id, title="Ready today", kind="RETEST",
        due_date=today, retest_of=completed.id, retest_round=1,
    )
    db_session.add_all([
        due,
        LearningTask(workspace_id=workspace_id, skill_id=skill.id, title="Tomorrow", kind="RETEST",
                     due_date=today + timedelta(days=1), retest_of=completed.id),
        LearningTask(workspace_id=workspace_id, skill_id=skill.id, title="Already done", kind="RETEST",
                     due_date=today, retest_of=completed.id, retest_round=1, status="DONE"),
        LearningTask(workspace_id=workspace_id, skill_id=skill.id, title="Learning unfinished", kind="RETEST",
                     due_date=today, retest_of=unfinished.id, retest_round=1),
        LearningTask(workspace_id=workspace_id, skill_id=skill.id, title="Unscheduled", kind="RETEST",
                     retest_of=completed.id, retest_round=1),
        LearningTask(workspace_id=workspace_id, title="No skill", kind="RETEST",
                     due_date=today, retest_of=completed.id, retest_round=1),
    ])
    await db_session.flush()

    result = await dashboard(test_workspace, db_session)
    assert result["due_retests"] == [{
        "id": due.id, "title": due.title, "due_date": today.isoformat(),
        "parent_id": completed.id, "parent_title": completed.title,
    }]
    assert [task["id"] for task in result["today_tasks"]] == [unfinished.id]
