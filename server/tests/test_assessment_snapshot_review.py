"""Frozen targets and uncalibrated evidence semantics, using isolated fixtures."""
from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_assessment_reliability import prepared, result_for

from app.api.v1.exams import CreateExamReq, create_exam
from app.models import Evidence, Exam, ExamQuestion, JobStandard, JobStandardTarget, Question, Skill, SkillState
from app.services import assessment_jobs as jobs
from app.services import assessment_worker as worker


async def fixture_question(session, workspace):
    skill = Skill(key='target-' + uuid4().hex[:12], name='Target snapshot fixture')
    session.add(skill)
    await session.flush()
    question = Question(short_code='TS-' + uuid4().hex[:12], workspace_id=workspace.id,
        title='Explain the fixture mechanism', qtype='open', topic='rag', skill_id=skill.id,
        skill=skill, rubric={'max_points': 5, 'points': [{'title': 'Mechanism'}]})
    session.add(question)
    await session.flush()
    return question, skill


@pytest.mark.asyncio
async def test_create_freezes_matching_role_and_full_targets_before_standard_edit(db_session, test_workspace):
    question, skill = await fixture_question(db_session, test_workspace)
    standard = JobStandard(role='AI Agent 应用开发工程师', level='P6', version=uuid4().hex[:12], is_seed=True)
    db_session.add(standard)
    await db_session.flush()
    target = JobStandardTarget(standard_id=standard.id, skill_id=skill.id, weight_pct=100, target_level=4.5)
    unrelated = JobStandard(role='Unrelated role fixture', level='P9', version=uuid4().hex[:12], is_seed=True)
    db_session.add_all([target, unrelated])
    await db_session.flush()
    try:
        detail = await create_exam(CreateExamReq(kind=Exam.KIND_BANK, question_ids=[question.id]), test_workspace, db_session)
        eq = await db_session.scalar(select(ExamQuestion).where(ExamQuestion.exam_id == detail['id']))
        snapshot = deepcopy(eq.creation_snapshot['job_standard'])
        assert snapshot['id'] == standard.id, 'A newer unrelated role must not replace the assessment target'
        assert snapshot['level'] == 'P6' and snapshot['calibration_status'] == 'SEED_ASSUMPTION'
        assert snapshot['targets'] == [{'skill_id': skill.id, 'skill_key': skill.key,
            'skill_name': skill.name, 'target_level': 4.5, 'weight_pct': 100}]
        target.target_level = 1.0
        target.weight_pct = 5
        await db_session.commit()
        await db_session.refresh(eq)
        assert eq.creation_snapshot['job_standard'] == snapshot
    finally:
        await db_session.execute(delete(JobStandardTarget).where(JobStandardTarget.standard_id == standard.id))
        await db_session.execute(delete(JobStandard).where(JobStandard.id.in_([standard.id, unrelated.id])))
        await db_session.commit()


@pytest.mark.asyncio
async def test_create_without_standard_exposes_missing_uncalibrated_target(db_session, test_workspace):
    question, _ = await fixture_question(db_session, test_workspace)
    previous = (await db_session.scalars(select(JobStandard).where(JobStandard.is_seed.is_(True),
        JobStandard.role == 'AI Agent 应用开发工程师'))).all()
    for row in previous: row.is_seed = False
    await db_session.flush()
    try:
        detail = await create_exam(CreateExamReq(kind=Exam.KIND_BANK, question_ids=[question.id]), test_workspace, db_session)
        eq = await db_session.scalar(select(ExamQuestion).where(ExamQuestion.exam_id == detail['id']))
        standard = eq.creation_snapshot['job_standard']
        assert standard['status'] == 'NOT_CONFIGURED'
        assert standard['calibration_status'] == 'UNCALIBRATED' and standard['targets'] == []
    finally:
        for row in previous: row.is_seed = True
        await db_session.commit()


@pytest.mark.asyncio
async def test_high_uncalibrated_grade_remains_observation_without_verified_claim(db_session, test_workspace, monkeypatch):
    exam, run, _ = await prepared(db_session, test_workspace, monkeypatch)
    claim = await jobs.claim_step(db_session, 'uncalibrated-high-score')
    result = result_for(claim, criterion_score=4)
    assert result['verdict'] == 'CORRECT' and result['needs_human_review']
    await jobs.complete_step(db_session, claim, result)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    await worker.reconcile(factory)
    await db_session.refresh(run)
    assert run.status == 'COMPLETED'
    evidence = await db_session.scalar(select(Evidence).where(Evidence.workspace_id == test_workspace.id))
    assert evidence.grade == 'OBSERVED_UNCALIBRATED' and evidence.support == 0
    assert evidence.meta['score'] == 5 and evidence.meta['assessed'] is True
    assert evidence.meta['validation_status'] == 'VALID'
    assert evidence.meta['calibration_status'] == 'LEGACY_UNCALIBRATED'
    state = await db_session.scalar(select(SkillState).where(SkillState.workspace_id == test_workspace.id,
        SkillState.skill_id == evidence.skill_id))
    assert state.evidence_coverage == 1 and state.estimated_level is None and state.status == 'UNKNOWN'
