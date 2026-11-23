from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1 import daily_learning_plan as api
from app.api.v1.memory import delete_memory, list_memory
from app.models import LearningTask, Workspace
from app.services import daily_learning_plan as planner
from app.services.daily_learning_plan import DEFAULT_DEADLINE, PlanItem, build_plan, curriculum

TODAY = date(2026, 11, 18)


def state(deadline=DEFAULT_DEADLINE, minutes=60, **extra):
    return {'start_date': TODAY.isoformat(), 'deadline': deadline.isoformat(), 'daily_minutes': minutes, **extra}


def assignments(plan):
    return {item['key']: day['date'] for day in plan['days'] for item in day['items']}


@pytest.fixture
def sprint_roster(monkeypatch):
    blocks = (
        ('sprint:build', 0, 300, None),
        ('sprint:round-1', 0, 120, None),
        ('sprint:review', 1, 300, None),
        ('sprint:round-2', 1, 120, 'sprint:round-1'),
        ('sprint:round-3', 2, 120, 'sprint:round-2'),
    )
    monkeypatch.setattr(planner, 'sprint_blocks', lambda: [
        SimpleNamespace(key=key, day=day, minutes=minutes, prerequisite=prerequisite,
                        title=key, stage='面试冲刺', criteria='保存练习及复盘结果')
        for key, day, minutes, prerequisite in blocks
    ])


def test_new_workspace_has_ordered_actionable_daily_path():
    plan = build_plan(state(), TODAY, [])
    items = [item for day in plan['days'] for item in day['items']]
    assert [item['key'] for item in items] == [item.key for item in curriculum()]
    assert plan['days'][0]['items'][0]['stage'] == '起点与目标'
    assert items[-1]['stage'] == '面试演练'
    assert plan['deadline'] == '2027-02-01'
    assert len(plan['days']) == 76
    assert plan['days'][-1]['date'] == plan['deadline']
    assert all(item['criteria'] and item['status'] == 'scheduled' for item in items)
    assert plan['completed_count'] == 0
    assert plan['schedule_profile'] is None
    assert all(day['capacity_minutes'] == 60 for day in plan['days'])


def test_sprint_reserves_weekdays_for_due_retests_then_blocks_and_fills_remaining_slots(sprint_roster):
    today = date(2026, 11, 20)
    existing = [
        PlanItem('task:1', '复测', '验证', 'retest', 90, '独立作答', due='2026-11-21'),
        PlanItem('task:2', '学习目标', '实践', 'goal', 70, '完成报告', due='2026-11-23', goal_id=2),
    ]
    plan = build_plan(state(minutes=480, schedule_profile='agent_interview_sprint', sprint_start_date='2026-11-23'),
                      today, existing, capture_baseline=True)
    days = {day['date']: day for day in plan['days']}
    assert plan['schedule_profile'] == 'agent_interview_sprint'
    assert plan['sprint_start_date'] == '2026-11-23'
    assert not days['2026-11-20']['items']
    assert days['2026-11-21']['capacity_minutes'] == days['2026-11-22']['capacity_minutes'] == 0
    assert not days['2026-11-21']['items'] and not days['2026-11-22']['items']
    monday = days['2026-11-23']
    assert monday['capacity_minutes'] == 480 and monday['minutes'] == 460
    assert [item['key'] for item in monday['items']] == ['task:1', 'sprint:build', 'task:2']
    assert monday['items'][0]['due'] == '2026-11-21'
    assert monday['items'][2]['goal_id'] == 2
    assert assignments(plan)['sprint:round-1'] == '2026-11-24'
    assert assignments(plan)['sprint:round-2'] == '2026-11-25'
    assert assignments(plan)['sprint:round-3'] == '2026-11-26'
    assert all(item['moved_from'] is None for day in plan['days'] for item in day['items']
               if item['source'] == 'sprint')


def test_sprint_settings_validate_profile_and_minutes():
    body = {'deadline': DEFAULT_DEADLINE, 'daily_minutes': 480, 'schedule_profile': 'agent_interview_sprint'}
    assert api.PlanSettings.model_validate(body).schedule_profile == 'agent_interview_sprint'
    with pytest.raises(ValidationError):
        api.PlanSettings.model_validate({**body, 'daily_minutes': 481})
    with pytest.raises(ValidationError):
        api.PlanSettings.model_validate({**body, 'schedule_profile': 'unknown'})


def test_late_work_rolls_forward_without_double_booking_or_early_retest():
    deadline = date(2026, 11, 23)
    future_retest = PlanItem('task:1', '复测', '验证', 'retest', 35, '独立作答', due='2026-11-21')
    start = build_plan(state(deadline, 35), TODAY, [future_retest])
    evidence = {'baseline:explain': {'date': '2026-11-18', 'note': '完成了两种示例解释并核对差异'}}
    configured = state(deadline, 35, assignments=start['assignments'], evidence=evidence)
    later = date(2026, 11, 20)
    overdue = build_plan(configured, later, [future_retest])
    assert overdue['overdue_count'] == 1
    revised = build_plan(configured, later, [future_retest], reschedule=True)
    assert revised['overdue_count'] == 0
    assert revised['completed_count'] == 1
    assert assignments(revised)['task:1'] == '2026-11-21'
    assert assignments(revised)['baseline:explain'] == '2026-11-18'
    assert assignments(revised)['baseline:practice'] == '2026-11-20'
    assert revised['assignments']['baseline:practice']['moved_from'] == '2026-11-19'
    assert all(day['minutes'] <= 35 for day in revised['days'])
    assert len(assignments(revised)) == sum(len(day['items']) for day in revised['days'])
    assert all(item['status'] == 'unscheduled' for item in revised['overflow'])


def test_later_curriculum_does_not_skip_a_practice_that_cannot_fit():
    due = date(2026, 11, 19)
    retest = PlanItem('task:1', '复测', '验证', 'retest', 10, '独立作答', due=due.isoformat())
    plan = build_plan(state(due, 35), TODAY, [retest])
    assert set(assignments(plan)) == {'baseline:explain', 'task:1'}
    assert [item['key'] for item in plan['overflow'][:2]] == ['baseline:practice', 'baseline:transfer']


def test_empty_baseline_does_not_claim_the_plan_is_on_track():
    finished = PlanItem('task:1', '当天已经完成的任务', '实践', 'task', 35, '已记录', completed_on=TODAY.isoformat())
    plan = build_plan(state(TODAY, 35), TODAY, [finished], capture_baseline=True)
    assert len(plan['overflow']) == len(curriculum())
    assert plan['progress']['status'] == 'unavailable'
    assert plan['progress']['baseline_date'] == TODAY.isoformat()
    assert plan['progress']['completed'] == 1
    assert plan['progress']['expected'] is None


def test_progress_counts_unique_tasks_and_completions_outside_visible_calendar():
    today = date(2027, 12, 1)
    old = PlanItem('task:1', '已完成', '当前学习目标', 'task', 25, '保存结果', completed_on=TODAY.isoformat())
    pending = PlanItem('task:2', '未排入', '当前学习目标', 'task', 45, '保存结果')
    plan = build_plan(state(today, 35, evidence={'baseline:explain': {'date': TODAY.isoformat()}}),
                      today, [old, old, pending])

    assert plan['completed_count'] == 0
    assert any(item['key'] == pending.key for item in plan['overflow'])
    assert plan['progress'] == {
        'total': len(curriculum()) + 2, 'completed': 2, 'expected': None,
        'baseline_completed': None, 'delta': None, 'status': 'unavailable', 'baseline_date': None,
    }


@pytest.mark.asyncio
async def test_api_persists_settings_and_evidence_without_touching_task(db_session, test_workspace, monkeypatch):
    class Clock:
        @staticmethod
        def now(_zone):
            return datetime(2026, 11, 18, 10, tzinfo=timezone.utc)

    monkeypatch.setattr(api, 'datetime', Clock)
    test_workspace.preferences = {'unrelated': True}
    task = LearningTask(workspace_id=test_workspace.id, title='实际诊断补课', kind='PRACTICE', est_minutes=25,
                        acceptance='提交最小示例')
    db_session.add(task)
    await db_session.commit()
    first = await api.get_plan(test_workspace, db_session)
    assert any(item['task_id'] == task.id for item in first['days'][0]['items'])
    assert test_workspace.preferences['unrelated'] is True
    assert first['progress']['baseline_date'] == TODAY.isoformat()
    assert first['progress']['baseline_completed'] == 0
    record = api.EvidenceIn(item_key='baseline:explain', note='流程图已落地；标出了模型决策点。')
    updated = await api.record_evidence(record, test_workspace, db_session)
    assert updated['completed_count'] == 1
    assert updated['progress']['baseline_completed'] == 1
    assert await api.record_evidence(record, test_workspace, db_session) == updated
    candidate, = (await list_memory(ws=test_workspace, session=db_session))['items']
    assert candidate['status'] == 'CANDIDATE'
    assert candidate['meta']['daily_plan_item_key'] == record.item_key
    assert candidate['meta']['daily_plan_date'] == TODAY.isoformat()
    assert candidate['eligible_for_personalization'] is False
    await delete_memory(candidate['id'], ws=test_workspace, session=db_session)
    assert await api.record_evidence(record, test_workspace, db_session) == updated
    assert (await list_memory(ws=test_workspace, session=db_session))['items'] == []
    other_workspace = Workspace(slug='daily-' + uuid4().hex, name='Other daily plan')
    db_session.add(other_workspace)
    await db_session.commit()
    await api.get_plan(other_workspace, db_session)
    await api.record_evidence(record, other_workspace, db_session)
    other_candidate, = (await list_memory(ws=other_workspace, session=db_session))['items']
    assert other_candidate['id'] != candidate['id']
    assert other_candidate['source_ref'] == candidate['source_ref']
    edited = await api.update_plan(api.PlanSettings(deadline=date(2026, 12, 1), daily_minutes=35), test_workspace, db_session)
    assert edited['completed_count'] == 1
    assert edited['deadline'] == '2026-12-01'
    assert edited['overflow']
    await db_session.refresh(task)
    assert task.status == 'PENDING'
    assert task.due_date is None


@pytest.mark.asyncio
async def test_first_get_after_midnight_moves_pending_work_and_preserves_evidence(db_session, test_workspace, monkeypatch):
    class Clock:
        day = 18

        @classmethod
        def now(cls, zone):
            return datetime(2026, 11, cls.day, 9, tzinfo=zone)

    monkeypatch.setattr(api, 'datetime', Clock)
    task = LearningTask(workspace_id=test_workspace.id, title='原定 11/19 的任务', kind='PRACTICE',
                        due_date=date(2026, 11, 19), est_minutes=25)
    db_session.add(task)
    await db_session.commit()
    await api.update_plan(api.PlanSettings(deadline=date(2026, 11, 24), daily_minutes=60), test_workspace, db_session)
    first = await api.get_plan(test_workspace, db_session)
    assert assignments(first)[f'task:{task.id}'] == '2026-11-19'
    assert first['progress']['baseline_date'] == TODAY.isoformat()
    assert first['progress']['expected'] == 1
    await api.record_evidence(api.EvidenceIn(item_key='baseline:explain', note='画出了决策点和工具调用流程。'),
                              test_workspace, db_session)
    Clock.day = 20
    revised = await api.get_plan(test_workspace, db_session)
    assert revised['overdue_count'] == 0 and revised['completed_count'] == 1
    assert assignments(revised)['baseline:explain'] == '2026-11-18'
    assert assignments(revised)['baseline:practice'] == '2026-11-20'
    moved = next(item for day in revised['days'] for item in day['items'] if item['task_id'] == task.id)
    assert moved['due'] == moved['moved_from'] == '2026-11-19'
    assert all(day['minutes'] <= 60 for day in revised['days'])
    assert await api.get_plan(test_workspace, db_session) == revised
    assert revised['progress'] == {
        'total': len(curriculum()) + 1, 'completed': 1, 'expected': 4,
        'baseline_completed': 1, 'delta': -3, 'status': 'behind', 'baseline_date': TODAY.isoformat(),
    }
    assert (await api.reschedule(test_workspace, db_session))['progress'] == revised['progress']

    unrelated = LearningTask(workspace_id=test_workspace.id, title='新任务', kind='PRACTICE',
                             status=LearningTask.STATUS_DONE, completed_at=Clock.now(api.LOCAL_TIME))
    db_session.add(unrelated)
    await db_session.commit()
    next_plan = await api.get_plan(test_workspace, db_session)
    assert next_plan['progress'] == {**revised['progress'], 'total': len(curriculum()) + 2, 'completed': 2}

    late = await api.record_evidence(
        api.EvidenceIn(item_key='baseline:practice', note='已经用流程图保存了实践结果。'), test_workspace, db_session,
    )
    assert late['progress'] == {**next_plan['progress'], 'completed': 3, 'baseline_completed': 2, 'delta': -2}

    await db_session.delete(task)
    await db_session.commit()
    retired = await api.get_plan(test_workspace, db_session)
    assert retired['progress'] == {**late['progress'], 'total': len(curriculum()) + 1, 'expected': 3, 'delta': -1}


@pytest.mark.asyncio
async def test_legacy_plan_needs_explicit_baseline_and_settings_changes_restart_it(db_session, test_workspace, monkeypatch):
    class Clock:
        day = 20

        @classmethod
        def now(cls, zone):
            return datetime(2026, 11, cls.day, 9, tzinfo=zone)

    monkeypatch.setattr(api, 'datetime', Clock)
    deadline = date(2026, 11, 24)
    evidence = {'baseline:explain': {'date': TODAY.isoformat(), 'note': '此前记录的学习完成依据。'}}
    initial = build_plan(state(deadline), TODAY, [])
    test_workspace.preferences = {api.PREFERENCE_KEY: state(deadline, evidence=evidence,
                                                           assignments=initial['assignments'], planned_on=TODAY.isoformat())}
    await db_session.commit()
    settings = api.PlanSettings(deadline=deadline, daily_minutes=60)

    legacy = await api.get_plan(test_workspace, db_session)
    assert assignments(legacy)['baseline:practice'] == '2026-11-20'
    assert legacy['progress'] == {
        'total': len(curriculum()), 'completed': 1, 'expected': None,
        'baseline_completed': None, 'delta': None, 'status': 'unavailable', 'baseline_date': None,
    }
    assert (await api.reschedule(test_workspace, db_session))['progress'] == legacy['progress']
    assert (await api.update_plan(settings, test_workspace, db_session))['progress'] == legacy['progress']

    confirmed = api.PlanSettings(deadline=deadline, daily_minutes=60, confirm_baseline=True)
    started = await api.update_plan(confirmed, test_workspace, db_session)
    assert started['progress'] == {
        'total': len(curriculum()), 'completed': 1, 'expected': 1,
        'baseline_completed': 0, 'delta': -1, 'status': 'behind', 'baseline_date': '2026-11-20',
    }
    Clock.day = 21
    assert (await api.update_plan(confirmed, test_workspace, db_session))['progress']['baseline_date'] == '2026-11-20'
    changed = await api.update_plan(api.PlanSettings(deadline=deadline, daily_minutes=35), test_workspace, db_session)
    assert changed['progress']['baseline_date'] == '2026-11-21'
    assert changed['progress']['completed'] == 1

    Clock.day = 25
    expired = await api.update_plan(api.PlanSettings(deadline=deadline, daily_minutes=35), test_workspace, db_session)
    assert expired['deadline'] == deadline.isoformat()
    assert expired['progress']['baseline_date'] == '2026-11-21'


@pytest.mark.asyncio
async def test_sprint_api_rebases_bridges_weekend_and_gates_round_evidence(db_session, test_workspace, monkeypatch,
                                                                       sprint_roster):
    class Clock:
        day = 18

        @classmethod
        def now(cls, zone):
            return datetime(2026, 11, cls.day, 9, tzinfo=zone)

    def day_items(plan, scheduled_date):
        return {item['key']: item for day in plan['days'] if day['date'] == scheduled_date for item in day['items']}

    def note(key):
        return api.EvidenceIn(item_key=key, note='已保存限时作答、评分明细和主要失分点。')

    monkeypatch.setattr(api, 'datetime', Clock)
    test_workspace.preferences = {'unrelated': True}
    await db_session.commit()
    omitted = api.PlanSettings(deadline=DEFAULT_DEADLINE, daily_minutes=480)
    await api.update_plan(omitted, test_workspace, db_session)
    previous = await api.record_evidence(note('baseline:explain'), test_workspace, db_session)
    assert previous['progress']['baseline_date'] == '2026-11-18'
    settings = api.PlanSettings(deadline=DEFAULT_DEADLINE, daily_minutes=480,
                                schedule_profile='agent_interview_sprint')
    Clock.day = 19
    started = await api.update_plan(settings, test_workspace, db_session)
    assert test_workspace.preferences['unrelated'] is True
    assert started['progress']['baseline_date'] == '2026-11-19'
    assert started['sprint_start_date'] == '2026-11-20'
    assert day_items(started, '2026-11-18') == day_items(previous, '2026-11-18')
    assert not day_items(started, '2026-11-19')
    assert assignments(started)['sprint:round-1'] == '2026-11-20'
    assert assignments(started)['sprint:round-2'] == '2026-11-23'
    assert not any(key.startswith('baseline:') and key != 'baseline:explain' for key in assignments(started))
    assert await api.update_plan(settings, test_workspace, db_session) == started

    Clock.day = 21
    moved = await api.get_plan(test_workspace, db_session)
    assert not day_items(moved, '2026-11-21') and not day_items(moved, '2026-11-22')
    assert moved['progress']['baseline_date'] == '2026-11-19'
    assert moved['progress']['expected'] == 2 and moved['progress']['status'] == 'behind'
    assert day_items(moved, '2026-11-23')['sprint:round-1']['moved_from'] == '2026-11-20'
    assert not day_items(moved, '2026-11-24')['sprint:round-2']['prerequisite_complete']
    assert await api.update_plan(settings, test_workspace, db_session) == moved
    assert await api.update_plan(omitted, test_workspace, db_session) == moved

    with pytest.raises(HTTPException) as weekend:
        await api.record_evidence(note('sprint:round-1'), test_workspace, db_session)
    assert weekend.value.status_code == 422
    Clock.day = 24
    with pytest.raises(HTTPException) as locked:
        await api.record_evidence(note('sprint:round-2'), test_workspace, db_session)
    assert locked.value.status_code == 422
    first_round = await api.record_evidence(note('sprint:round-1'), test_workspace, db_session)
    assert day_items(first_round, '2026-11-24')['sprint:round-1']['status'] == 'completed'
    assert day_items(first_round, '2026-11-25')['sprint:round-2']['prerequisite_complete']
    with pytest.raises(HTTPException) as too_early:
        await api.record_evidence(note('sprint:round-2'), test_workspace, db_session)
    assert too_early.value.status_code == 422

    Clock.day = 25
    second_round = await api.record_evidence(note('sprint:round-2'), test_workspace, db_session)
    assert day_items(second_round, '2026-11-25')['sprint:round-2']['status'] == 'completed'
    assert await api.record_evidence(note('sprint:round-2'), test_workspace, db_session) == second_round
    reverted = await api.update_plan(api.PlanSettings(deadline=DEFAULT_DEADLINE, daily_minutes=60,
                                                       schedule_profile=None), test_workspace, db_session)
    assert reverted['schedule_profile'] is None and reverted['sprint_start_date'] is None
    assert reverted['completed_count'] == 3
    assert day_items(reverted, '2026-11-24')['sprint:round-1']['moved_from'] == '2026-11-20'
    assert 'sprint:round-3' not in assignments(reverted)
    assert 'baseline:practice' in assignments(reverted)

    restarted = await api.update_plan(settings, test_workspace, db_session)
    assert restarted['sprint_start_date'] == '2026-11-26'
    assert restarted['completed_count'] == 3
    assert assignments(restarted)['sprint:round-3'] == '2026-11-30'
