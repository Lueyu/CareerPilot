from dataclasses import asdict, dataclass
from datetime import date, timedelta

from app.services.agent_sprint_curriculum import sprint_blocks

DEFAULT_DEADLINE = date(2027, 2, 1)
DEFAULT_CAPACITY = 60
MAX_DAYS = 180
PREFERENCE_KEY = 'daily_learning_plan'
PACE_BASELINE_VERSION = 1
AGENT_INTERVIEW_SPRINT = 'agent_interview_sprint'
SOURCE_PRIORITY = {'retest': 0, 'sprint': 1, 'curriculum': 1, 'goal': 2, 'task': 3}

MODULES = (
    ('baseline', '起点与目标', 'Agent、工作流与普通对话', '给同一个客服请求画三种处理流程，指出哪一步需要模型决策'),
    ('python', '基础', 'Python、HTTP 与 JSON', '写一个读入 JSON 并请求模拟服务的小程序，展示正常和错误响应'),
    ('llm', '基础', '消息结构与结构化输出', '写出系统、用户、工具三类消息，验证缺字段与类型错误会被拒绝'),
    ('stream', '基础', '流式输出与取消', '演示收到首个响应片段、用户主动取消和正常结束三种情况，记录各自的界面状态'),
    ('tools', '基础', '工具调用与执行循环', '实现工具调用循环，展示成功、工具失败和达到步数上限的记录'),
    ('workflow', '动手构建', '状态、分支与人工确认', '画出状态迁移图，演示流程暂停后恢复且副作用只执行一次'),
    ('rag', '动手构建', '检索与引用', '做一个文档问答流程，展示有来源、无来源和来源冲突三个用例'),
    ('context', '动手构建', '上下文与记忆', '制定上下文保留规则，用对话回放演示旧信息失效后如何处理'),
    ('persist', '动手构建', '运行状态与持久化', '记录一次执行的输入、步骤和结果，在模拟中断后从正确的步骤恢复'),
    ('eval', '工程进阶', '评估与可观测', '准备五个测试用例，记录成功率、失败类别和调整前后的对比'),
    ('async', '工程进阶', '异步任务与并发限制', '用两种负载运行模拟任务队列，记录并发上限、积压和任务超时行为'),
    ('reliability', '工程进阶', '超时、重试与幂等', '模拟超时或重复提交，展示操作不会重复落库并记录重试上限'),
    ('safety', '工程进阶', '权限与运行边界', '写一份权限表，演示拒绝、人工确认和通过三个分支'),
    ('cost', '工程进阶', '模型选型、延迟与成本', '对两个模型配置制定相同的测试集，写出质量、延迟和预算的取舍依据'),
    ('portfolio', '面试演练', '项目演示与可复现证据', '整理一个可运行的最小演示，记录运行命令、预期输出及一个已知限制'),
    ('design', '面试演练', '端到端 Agent 系统设计', '在白板上给出执行、数据、评估和故障恢复流程，解释两项取舍'),
    ('incident', '面试演练', '故障复盘与深挖追问', '把一次模拟故障讲成可验证的复盘，说明现象、证据、修复和预防指标'),
    ('interview', '面试演练', '项目讲述与模拟面试', '完成一次限时模拟面试，复盘两项失分并写出改进答案'),
)


@dataclass(frozen=True)
class PlanItem:
    key: str
    title: str
    stage: str
    source: str
    minutes: int
    criteria: str
    task_id: int | None = None
    goal_id: int | None = None
    due: str | None = None
    completed_on: str | None = None
    verification: dict | None = None
    study_session_id: int | None = None


def curriculum() -> list[PlanItem]:
    result = []
    for key, stage, topic, artifact in MODULES:
        drills = (
            ('explain', '理解', 20, f'用自己的话解释「{topic}」，给出一个适用及一个不适用的场景。'),
            ('practice', '实践', 35, f'{artifact}。保存代码、流程图或答题记录。'),
            ('transfer', '迁移', 25, f'换一个业务场景独立应用「{topic}」，说明一项权衡和一条验证方法。'),
            ('review', '回顾', 20, f'不看资料，用 2 分钟回答「{topic}」的面试提问并记录一条待改进之处。'),
        )
        for suffix, label, minutes, criteria in drills:
            result.append(PlanItem(f'{key}:{suffix}', f'{label}：{topic}', stage, 'curriculum', minutes, criteria))
    return result


def next_workday(day: date) -> date:
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def _workday_offset(start: date, offset: int) -> date:
    weekends = (start.weekday() + offset) // 5
    return start + timedelta(days=offset + 2 * weekends)


def pace_baseline(state: dict) -> dict | None:
    baseline = state.get('pace_baseline')
    if isinstance(baseline, dict) and baseline.get('version') == PACE_BASELINE_VERSION:
        return baseline
    return None


def _progress(baseline: dict | None, today: str, keys: set[str], completed: set[str]) -> dict:
    progress = {'total': len(keys), 'completed': len(completed)}
    due_on = baseline['due_on'] if baseline else {}
    cohort = keys & due_on.keys()
    if not cohort:
        return {**progress, 'expected': None, 'baseline_completed': None, 'delta': None,
                'status': 'unavailable', 'baseline_date': baseline['date'] if baseline else None}
    expected = sum(due_on[key] <= today for key in cohort)
    baseline_completed = len(cohort & completed)
    delta = baseline_completed - expected
    return {**progress, 'expected': expected, 'baseline_completed': baseline_completed, 'delta': delta,
            'status': 'ahead' if delta > 0 else 'behind' if delta < 0 else 'on_track',
            'baseline_date': baseline['date']}


def build_plan(state: dict, today: date, existing: list[PlanItem], *, reschedule: bool = False,
               capture_baseline: bool = False) -> dict:
    start = date.fromisoformat(state['start_date'])
    deadline = date.fromisoformat(state['deadline'])
    capacity = state['daily_minutes']
    evidence = state.get('evidence', {})
    previous = state.get('assignments', {})
    profile = state.get('schedule_profile')
    sprint_active = profile == AGENT_INTERVIEW_SPRINT
    sprint_start = date.fromisoformat(state['sprint_start_date']) if sprint_active else None
    assignments = {}
    visible_start = max(start, today - timedelta(days=MAX_DAYS))
    visible_end = max(deadline, today)
    calendar = {visible_start + timedelta(days=offset): [] for offset in range((visible_end - visible_start).days + 1)}
    minutes = dict.fromkeys(calendar, 0)
    capacity_by_day = {day: 0 if sprint_active and day.weekday() >= 5 else capacity for day in calendar}
    overflow, queued = [], []
    curriculum_days = set()
    generic_items = [item for item in curriculum() if not sprint_active or evidence.get(item.key, {}).get('date')]
    blocks = {block.key: block for block in sprint_blocks()
              if sprint_active or evidence.get(block.key, {}).get('date')}
    sprint_items = [PlanItem(block.key, block.title, block.stage, 'sprint', block.minutes, block.criteria)
                    for block in blocks.values()]
    sprint_dates = {key: _workday_offset(sprint_start, block.day) for key, block in blocks.items()} if sprint_start else {}
    plan_items = {item.key: item for item in [*existing, *generic_items, *sprint_items]}
    completed_dates = {key: item.completed_on or evidence.get(key, {}).get('date') for key, item in plan_items.items()}
    completed_keys = {key for key, day in completed_dates.items() if day}

    for item in plan_items.values():
        last = previous.get(item.key, {})
        scheduled = last.get('date')
        scheduled_day = date.fromisoformat(scheduled) if scheduled else None
        record = evidence.get(item.key)
        completed = completed_dates[item.key]
        data = {**asdict(item), 'completed_on': completed, 'evidence': record, 'moved_from': last.get('moved_from')}
        if item.source == 'sprint':
            prerequisite = blocks[item.key].prerequisite
            data.update(prerequisite=prerequisite,
                        prerequisite_complete=prerequisite is None or prerequisite in completed_keys)
        if completed:
            target = date.fromisoformat(completed)
            if target in calendar:
                calendar[target].append({**data, 'status': 'completed'})
                minutes[target] += item.minutes
                if item.source == 'curriculum':
                    curriculum_days.add(target)
            if scheduled:
                assignments[item.key] = last
        elif (scheduled_day and visible_start <= scheduled_day < today and not reschedule
              and capacity_by_day[scheduled_day]):
            calendar[scheduled_day].append({**data, 'status': 'overdue'})
            minutes[scheduled_day] += item.minutes
            assignments[item.key] = last
        else:
            queued.append((item, data, last))

    queued.sort(key=lambda entry: (SOURCE_PRIORITY[entry[0].source], sprint_dates.get(entry[0].key, date.min),
                                   entry[0].due or ''))
    curriculum_floor = today
    curriculum_blocked = False
    for item, data, last in queued:
        earliest = max(today, date.fromisoformat(item.due)) if item.due else today
        if item.source == 'curriculum':
            earliest = curriculum_floor
        if item.source == 'sprint':
            earliest = max(earliest, sprint_dates[item.key])
            if prerequisite := blocks[item.key].prerequisite:
                predecessor_date = completed_dates.get(prerequisite) or assignments.get(prerequisite, {}).get('date')
                earliest = max(earliest, next_workday(date.fromisoformat(predecessor_date))) if predecessor_date else None
        old_date = last.get('date') or item.due
        valid = [day for day in calendar if earliest and earliest <= day <= deadline and capacity_by_day[day]
                 and minutes[day] + item.minutes <= capacity_by_day[day]
                 and (item.source != 'curriculum' or not curriculum_blocked and day not in curriculum_days)]
        if not valid:
            if item.source == 'curriculum':
                curriculum_blocked = True
            overflow.append({**data, 'status': 'unscheduled', 'moved_from': data['moved_from'] or old_date})
            if old_date or data['moved_from']:
                assignments[item.key] = {'date': None, 'moved_from': data['moved_from'] or old_date}
            continue
        old_day = date.fromisoformat(old_date) if old_date else None
        use_earliest = (sprint_active and item.source in {'retest', 'sprint'}) or (reschedule and item.source == 'curriculum')
        assigned = old_day if old_day in valid and not use_earliest else valid[0]
        if item.source == 'curriculum':
            curriculum_days.add(assigned)
            curriculum_floor = assigned + timedelta(days=1)
        if old_day and old_day != assigned:
            data['moved_from'] = data['moved_from'] or old_date
        assignments[item.key] = {'date': assigned.isoformat(), 'moved_from': data['moved_from']}
        minutes[assigned] += item.minutes
        calendar[assigned].append({**data, 'status': 'scheduled'})

    days = [{'date': day.isoformat(), 'items': items, 'minutes': minutes[day], 'capacity_minutes': capacity_by_day[day]}
            for day, items in calendar.items()]
    today_text = today.isoformat()
    baseline = pace_baseline(state)
    if capture_baseline:
        baseline = {'version': PACE_BASELINE_VERSION, 'date': today_text, 'due_on': {
            key: entry['date'] for key, entry in assignments.items()
            if key not in completed_keys and entry['date'] and entry['date'] >= today_text
        }}
    return {
        'start_date': state['start_date'], 'deadline': state['deadline'], 'daily_minutes': capacity,
        'schedule_profile': profile, 'sprint_start_date': sprint_start.isoformat() if sprint_start else None,
        'today': today_text, 'days': days, 'overflow': overflow,
        'overdue_count': sum(item['status'] == 'overdue' for day in days for item in day['items']),
        'completed_count': sum(item['status'] == 'completed' for day in days for item in day['items']),
        'progress': _progress(baseline, today_text, set(plan_items), completed_keys),
        'assignments': assignments,
        'pace_baseline': baseline,
    }
