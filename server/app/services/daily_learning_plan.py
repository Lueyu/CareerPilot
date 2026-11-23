from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta

DEFAULT_CAPACITY = 60
MAX_DAYS = 180
PREFERENCE_KEY = 'daily_learning_plan'
PACE_BASELINE_VERSION = 1
AGENT_INTERVIEW_SPRINT = 'agent_interview_sprint'
SOURCE_PRIORITY = {'retest': 0, 'sprint': 1, 'curriculum': 1, 'goal': 2, 'task': 3, 'question': 3}

@dataclass(frozen=True)
class SprintBlock:
    """One practice block, with its day counted in weekdays from November 20."""

    key: str
    day: int
    stage: str
    title: str
    minutes: int
    criteria: str
    prerequisite: str | None = None


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
    question_id: int | None = None


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
               capture_baseline: bool = False,
               curriculum: list[PlanItem] | None = None, sprint: Sequence[SprintBlock] = ()) -> dict:
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
    generic_items = [item for item in (curriculum or []) if not sprint_active or evidence.get(item.key, {}).get('date')]
    blocks = {block.key: block for block in sprint
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
        if item.question_id is None:
            data.pop('question_id')
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
