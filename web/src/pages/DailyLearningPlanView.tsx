import { getLocale, t, useT } from '../i18n';
import React, { useEffect, useId, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import './dailyLearningPlan.css';

type Item = {
  key: string;
  title: string;
  stage: string;
  source: 'curriculum' | 'task' | 'goal' | 'retest' | 'sprint' | 'question';
  question_id?: number | null;
  criteria: string;
  minutes: number;
  task_id: number | null;
  study_session_id: number | null;
  due: string | null;
  moved_from: string | null;
  status: 'scheduled' | 'completed' | 'overdue' | 'unscheduled';
  evidence: { date: string; note: string } | null;
  verification: Record<string, string> | null;
  prerequisite_complete?: boolean;
};

type Day = { date: string; items: Item[]; minutes: number; capacity_minutes?: number };
type Progress = {
  total: number;
  completed: number;
  expected: number | null;
  baseline_completed: number | null;
  delta: number | null;
  status: 'ahead' | 'on_track' | 'behind' | 'unavailable';
  baseline_date: string | null;
};
export type Plan = {
  target_role?: string;
  deadline_confirmed?: boolean;
  today: string;
  start_date: string;
  deadline: string;
  daily_minutes: number;
  schedule_profile?: 'agent_interview_sprint' | null;
  sprint_start_date?: string | null;
  days: Day[];
  overflow: Item[];
  overdue_count: number;
  progress?: Progress;
};
export type Settings = { deadline: string; daily_minutes: number; confirm_baseline: boolean };
type DialogName = 'goal' | 'replan' | 'progress';
type CalendarStatus = 'completed' | 'scheduled' | 'shifted' | 'retest';

const historyUrl = '/learning?plan=history';
const stageLabels: Record<string, string> = {
  '起点与目标': 'progress.dailyLearningPlanViewStageGoals',
  '基础': 'progress.dailyLearningPlanViewStageFoundations',
  '动手构建': 'progress.dailyLearningPlanViewStageBuild',
  '工程进阶': 'progress.dailyLearningPlanViewStageAdvancedEngineering',
  '面试演练': 'progress.dailyLearningPlanViewStageInterview',
  'Agent 基础': 'progress.dailyLearningPlanViewStageAgentFoundations',
  '项目冲刺': 'progress.dailyLearningPlanViewStageProjectSprint',
  '投递准备': 'progress.dailyLearningPlanViewStageApplications',
  '当前学习目标': 'progress.dailyLearningPlanViewStageCurrentGoal',
  '延迟复测': 'progress.dailyLearningPlanViewStageDelayedRetest',
};
const timeBudget = (minutes: number) => minutes % 60 === 0 ? t('progress.dailyLearningPlanViewHours', { hours: minutes / 60 }) : t('progress.dailyLearningPlanViewMinutes', { minutes: minutes });
const weekdayLabels = {
  monday: 'progress.dailyLearningPlanViewMonday',
  tuesday: 'progress.dailyLearningPlanViewTuesday',
  wednesday: 'progress.dailyLearningPlanViewWednesday',
  thursday: 'progress.dailyLearningPlanViewThursday',
  friday: 'progress.dailyLearningPlanViewFriday',
  saturday: 'progress.dailyLearningPlanViewSaturday',
  sunday: 'progress.dailyLearningPlanViewSunday',
};
const weekdayKeys = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'] as const;
const itemStatus = {
  get scheduled() { return t('progress.dailyLearningPlanViewItemScheduled'); },
  get completed() { return t('progress.dailyLearningPlanViewItemCompleted'); },
  get overdue() { return t('progress.dailyLearningPlanViewItemOverdue'); },
  get unscheduled() { return t('progress.dailyLearningPlanViewItemUnscheduled'); },
};
const calendarStatus: Record<CalendarStatus, string> = {
  get completed() { return t('progress.dailyLearningPlanViewCalendarCompleted'); },
  get scheduled() { return t('progress.dailyLearningPlanViewCalendarScheduled'); },
  get shifted() { return t('progress.dailyLearningPlanViewCalendarShifted'); },
  get retest() { return t('progress.retest'); },
};
const checkNames: Record<string, string> = {
  get understanding() { return t('progress.understanding'); },
  get practice() { return t('progress.practice'); },
  get transfer() { return t('progress.transfer'); },
  get retention() { return t('progress.retention'); },
};
const checkStatus: Record<string, string> = {
  get UNVERIFIED() { return t('progress.unverified'); },
  get ASSISTED_CORRECT() { return t('progress.assistedCorrect'); },
  get INDEPENDENT_CORRECT() { return t('progress.independentCorrect'); },
  get PRACTICE_CORRECT() { return t('progress.dailyLearningPlanViewPracticeCorrect'); },
  get NOT_SUBMITTED() { return t('progress.notSubmitted'); },
  get PENDING_REVIEW() { return t('progress.awaitingReview'); },
  get NEEDS_REVISION() { return t('progress.needsRevision'); },
  get TASK_VERIFIED() { return t('progress.taskVerified'); },
  get CODE_REVIEWED() { return t('progress.dailyLearningPlanViewCodeReviewComplete'); },
  get PENDING_RETEST() { return t('progress.awaitingRetest'); },
  get PASSED_THIS_ATTEMPT() { return t('progress.passedThisTime'); },
  get NOT_DUE() { return t('progress.notDue'); },
};

const localDate = (value: string) => new Date(value + 'T12:00:00');
const dateKey = (date: Date) => [
  String(date.getFullYear()).padStart(4, '0'), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0'),
].join('-');
const shortDate = (value: string) => localDate(value).toLocaleDateString(getLocale(), { month: 'long', day: 'numeric' });
const fullDate = (value: string) => localDate(value).toLocaleDateString(getLocale(), { year: 'numeric', month: 'long', day: 'numeric' });
const monthKey = (date: string) => date.slice(0, 7) + '-01';

function isDateKey(value: string | null): value is string {
  return value !== null && /^\d{4}-\d{2}-\d{2}$/.test(value) && dateKey(localDate(value)) === value;
}

function addDays(value: string, days: number) {
  const date = localDate(value);
  date.setDate(date.getDate() + days);
  return dateKey(date);
}

function addMonths(value: string, months: number) {
  const date = localDate(value);
  date.setMonth(date.getMonth() + months);
  return dateKey(date);
}

function daysBetween(start: string, end: string) {
  return (Date.parse(end) - Date.parse(start)) / 86_400_000;
}

function calendarStatuses(items: Item[]) {
  const kinds = new Set<CalendarStatus>();
  for (const item of items) {
    if (item.status === 'completed') kinds.add('completed');
    if (item.moved_from || item.status === 'overdue') kinds.add('shifted');
    if (item.source === 'retest') kinds.add('retest');
    if (item.status === 'scheduled' && !item.moved_from && item.source !== 'retest') kinds.add('scheduled');
  }
  return [...kinds];
}

function Button({ primary = false, children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { primary?: boolean }) {
  return <button type="button" className={'btn ' + (primary ? 'btn-primary' : 'btn-outline')} {...props}>{children}</button>;
}

function GoalSummary({ plan, goal, registered, onOpen }: {
  plan: Plan; goal: string; registered: boolean; onOpen: (name: DialogName) => void;
}) {
  const t = useT();
  const progress = plan.progress;
  const delta = progress?.delta ?? null;
  const comparable = progress?.status !== 'unavailable' && delta !== null &&
    progress?.baseline_completed != null && progress.expected != null;
  const percent = progress && progress.total > 0 ? Math.round(100 * progress.completed / progress.total) : null;
  const remaining = daysBetween(plan.today, plan.deadline);
  const paceLabel = comparable
    ? { ahead: t('progress.dailyLearningPlanViewAhead', { count: Math.abs(delta) }), on_track: t('progress.dailyLearningPlanViewOnTrack'), behind: t('progress.dailyLearningPlanViewBehind', { count: Math.abs(delta) }), unavailable: t('progress.dailyLearningPlanViewNoBaseline') }[progress.status]
    : t('progress.dailyLearningPlanViewNotComparable');
  const plannedCount = comparable && progress.baseline_completed === progress.completed ? progress.expected : null;
  const plannedMarker = plannedCount !== null && progress && progress.total > 0
    ? { count: plannedCount, left: 100 * plannedCount / progress.total }
    : null;

  return <section className="daily-plan-card daily-plan-goal" aria-labelledby="daily-plan-goal">
    <div className="daily-plan-row">
      <span className="daily-plan-eyebrow">{registered ? t('progress.dailyLearningPlanViewLearningGoal') : t('progress.dailyLearningPlanViewApplicationGoal')}</span>
      <Button disabled={registered && !plan.target_role} onClick={() => onOpen('goal')} aria-haspopup="dialog">{t('progress.dailyLearningPlanViewAdjustGoal')}</Button>
    </div>
    <h2 id="daily-plan-goal">{goal}</h2>
    {(!registered || plan.target_role) && <p className="daily-plan-goal-date">
      {registered && !plan.deadline_confirmed && t('progress.dailyLearningPlanViewSuggestedDate')}
      <time dateTime={plan.deadline}>{registered ? fullDate(plan.deadline) : t('progress.dailyLearningPlanViewStartApplicationDate', { date: fullDate(plan.deadline) })}</time>
      <span aria-hidden="true"> · </span>
      {remaining > 0 ? t('progress.dailyLearningPlanViewRemaining', { days: remaining }) : remaining === 0 ? t('progress.dailyLearningPlanViewTargetToday') : t('progress.dailyLearningPlanViewPastTarget', { days: Math.abs(remaining) })}
    </p>}
    {plan.schedule_profile === 'agent_interview_sprint' && <p className="daily-plan-goal-date">
      {t('progress.dailyLearningPlanViewWeekdayBudget', { time: timeBudget(plan.daily_minutes) })}
      {plan.sprint_start_date && plan.today < plan.sprint_start_date && t('progress.dailyLearningPlanViewSprintStart', { date: shortDate(plan.sprint_start_date) })}
    </p>}
    <div className="daily-plan-row daily-plan-progress-line">
      <div className="daily-plan-progress-count">
        {percent !== null && <strong>{percent}%</strong>}
        <span>{progress ? (progress.total ? t('progress.dailyLearningPlanViewCompletionCount', { completed: progress.completed, total: progress.total }) : t('progress.dailyLearningPlanViewNoTasks')) : t('progress.dailyLearningPlanViewProgressUnavailable')}</span>
      </div>
      <span className={'daily-plan-badge daily-plan-pace-' + (comparable ? progress.status : 'unavailable')}>{paceLabel}</span>
    </div>
    {percent !== null && <div className="daily-plan-track" role="progressbar" aria-label={t('progress.dailyLearningPlanViewCompletionProgress')} aria-valuenow={percent}
      aria-valuetext={t('progress.dailyLearningPlanViewCompletionCount', { completed: progress?.completed ?? 0, total: progress?.total ?? 0 })}>
      <span className="daily-plan-track-fill" style={{ width: percent + '%' }} />
      {plannedMarker !== null && <span className="daily-plan-track-marker" aria-hidden="true"
        title={t('progress.dailyLearningPlanViewExpected', { count: plannedMarker.count })} style={{ left: plannedMarker.left + '%' }} />}
    </div>}
    <div className="daily-plan-row daily-plan-goal-footer">
      <p>{comparable ? t('progress.dailyLearningPlanViewBaselineTasks', { actual: progress.baseline_completed ?? '—', expected: progress.expected ?? '—' }) : t('progress.dailyLearningPlanViewPaceUnavailable')}</p>
      <button className="daily-plan-text-button" type="button" onClick={() => onOpen('progress')} aria-haspopup="dialog">{t('progress.dailyLearningPlanViewProgressMethod')}</button>
    </div>
  </section>;
}

function MonthCalendar({ plan, days, month, selectedDate, onMonth, onSelect }: {
  plan: Plan; days: Map<string, Day>; month: string; selectedDate: string;
  onMonth: (value: string) => void; onSelect: (value: string) => void;
}) {
  const t = useT();
  const firstAvailable = plan.days[0]?.date ?? plan.today;
  const lastAvailable = plan.days[plan.days.length - 1]?.date ?? plan.today;
  const first = localDate(month);
  const firstCell = addDays(month, -((first.getDay() + 6) % 7));
  const nextMonth = addMonths(month, 1);
  const previousMonth = addMonths(month, -1);
  const rowCount = Math.ceil(daysBetween(firstCell, nextMonth) / 7);
  const dates = Array.from({ length: rowCount * 7 }, (_, index) => addDays(firstCell, index));
  const year = first.getFullYear();
  const monthLabel = getLocale() === 'zh-CN' ? first.getMonth() + 1 : first.toLocaleDateString(getLocale(), { month: 'long' });

  return <section className="daily-plan-card daily-plan-calendar" aria-labelledby="daily-plan-month">
    <header className="daily-plan-row">
      <h2 id="daily-plan-month" aria-live="polite">{t('progress.dailyLearningPlanViewMonthTitle', { year, month: monthLabel })}</h2>
      <nav className="daily-plan-calendar-nav" aria-label={t('progress.dailyLearningPlanViewMonthNavigation')}>
        <Button className="btn btn-outline daily-plan-month-arrow" aria-label={t('progress.dailyLearningPlanViewPreviousMonth')} disabled={month <= monthKey(firstAvailable)}
          onClick={() => onMonth(previousMonth)}><span className="daily-plan-chevron" aria-hidden="true" /></Button>
        <Button onClick={() => onSelect(plan.today)}>{t('progress.dailyLearningPlanViewBackToday')}</Button>
        <Button className="btn btn-outline daily-plan-month-arrow" aria-label={t('progress.dailyLearningPlanViewNextMonth')} disabled={month >= monthKey(lastAvailable)}
          onClick={() => onMonth(nextMonth)}><span className="daily-plan-chevron" aria-hidden="true" /></Button>
      </nav>
    </header>
    <ul className="daily-plan-legend" aria-label={t('progress.dailyLearningPlanViewCalendarStatus')}>
      <li><span className="daily-plan-dot daily-plan-completed" />{t('progress.dailyLearningPlanViewCalendarCompleted')}</li>
      <li><span className="daily-plan-dot daily-plan-scheduled" />{t('progress.dailyLearningPlanViewCalendarScheduled')}</li>
      <li><span className="daily-plan-dot daily-plan-shifted" />{t('progress.dailyLearningPlanViewCalendarShifted')}</li>
      <li><span className="daily-plan-dot daily-plan-retest" />{t('progress.retest')}</li>
    </ul>
    <div className="daily-plan-calendar-grid">
      {weekdayKeys.map(weekday => <span key={weekday} className="daily-plan-weekday" aria-hidden="true">{t(weekdayLabels[weekday])}</span>)}
      {dates.map(date => {
        const day = days.get(date);
        const kinds = calendarStatuses(day?.items ?? []);
        const isAdjacent = monthKey(date) !== month;
        const isToday = date === plan.today;
        const isDeadline = date === plan.deadline;
        const isSelected = date === selectedDate;
        const completed = day?.items.filter(item => item.status === 'completed').length ?? 0;
        const shortSummary = day?.items.length
          ? completed === day.items.length ? t('progress.dailyLearningPlanViewDayCompleted', { count: completed }) : t('progress.dailyLearningPlanViewDayCompact', { count: day.items.length, minutes: day.minutes })
          : null;
        const label = [
          fullDate(date), isToday && t('progress.today'), isDeadline && (plan.target_role === undefined ? t('progress.dailyLearningPlanViewApplicationTargetDate') : t('progress.dailyLearningPlanViewLearningTargetDate')), isSelected && t('progress.dailyLearningPlanViewSelected'),
          day?.capacity_minutes === 0 && t('progress.dailyLearningPlanViewWeekendRest'),
          day ? day.items.length ? t('progress.dailyLearningPlanViewDayTasks', { count: day.items.length, minutes: day.minutes }) : t('progress.dailyLearningPlanViewNoPlanThatDay') : t('progress.dailyLearningPlanViewNoDateData'),
          ...kinds.map(kind => calendarStatus[kind]),
        ].filter(Boolean).join(getLocale() === 'zh-CN' ? '，' : ', ');
        const content = <>
          <span className="daily-plan-cell-heading">
            <time dateTime={date}>{Number(date.slice(-2))}</time>
            {isToday && <span className="daily-plan-cell-today">{t('progress.today')}</span>}
            {isDeadline && <span className="daily-plan-cell-deadline">{t('progress.dailyLearningPlanViewTargetDay')}</span>}
          </span>
          {shortSummary && <span className="daily-plan-cell-summary">{shortSummary}</span>}
          {kinds.length > 0 && <span className="daily-plan-cell-dots">
            {kinds.map(kind => <span key={kind} className={'daily-plan-dot daily-plan-' + kind} />)}
          </span>}
        </>;
        const className = ['daily-plan-cell', isAdjacent && 'is-adjacent', isToday && 'is-today',
          isDeadline && 'is-deadline', isSelected && 'is-selected'].filter(Boolean).join(' ');
        return day
          ? <button type="button" className={className} key={date} data-date={date} aria-label={label} aria-pressed={isSelected}
              aria-current={isToday ? 'date' : undefined} title={label} onClick={() => onSelect(date)}>{content}</button>
          : <div className={className} key={date} data-date={date} title={label}><span className="daily-plan-sr-only">{label}</span><div aria-hidden="true">{content}</div></div>;
      })}
    </div>
  </section>;
}

function QuestionLinks({ questionId, registered }: { questionId: number; registered: boolean }) {
  const t = useT();
  return <>
    <Link className="btn btn-primary" to={(registered ? '/beta' : '') + '/study/chat?question=' + questionId}>{t('progress.dailyLearningPlanViewStartLearning')}</Link>
    <Link className="btn btn-outline" to={(registered ? '/beta' : '') + '/exam?question=' + questionId}>{t('progress.dailyLearningPlanViewStartQuestion')}</Link>
  </>;
}

function TaskCard({ item, selectedDate, plan, recommended, focused, registered, onSave, busy, error }: {
  item: Item; selectedDate: string; plan: Plan; recommended: boolean; focused: boolean; registered: boolean;
  onSave: (itemKey: string, note: string) => Promise<boolean>; busy: boolean; error: string;
}) {
  const t = useT();
  const [note, setNote] = useState<string | null>(null);
  const evidenceId = useId();
  const card = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!focused) return;
    card.current?.focus({ preventScroll: true });
    card.current?.scrollIntoView({ block: 'center' });
  }, [focused, selectedDate]);
  const isRetest = item.source === 'retest';
  const isCompleted = item.status === 'completed';
  const awaitingPriorRound = item.prerequisite_complete === false;
  const mayRecord = item.task_id === null && !isCompleted && !awaitingPriorRound && selectedDate <= plan.today;

  return <article ref={card} className={'daily-plan-task' + (focused ? ' daily-plan-task-source' : '')} tabIndex={focused ? -1 : undefined}>
    <div className="daily-plan-task-badges">
      <span className={'daily-plan-badge ' + (isRetest ? 'daily-plan-retest-badge' : 'daily-plan-task-stage')}>{t('progress.dailyLearningPlanViewStageDuration', { stage: item.source !== 'question' && stageLabels[item.stage] ? t(stageLabels[item.stage]) : item.stage, minutes: item.minutes })}</span>
      {(!recommended || isCompleted) && <span className={'daily-plan-badge daily-plan-item-' + item.status}>{itemStatus[item.status]}</span>}
    </div>
    <h3>{recommended ? t('progress.dailyLearningPlanViewRecommended', { title: item.title }) : item.title}</h3>
    <p>{item.criteria}</p>
    {awaitingPriorRound && !isCompleted && <p>{t('progress.dailyLearningPlanViewPreviousPass')}</p>}
    {item.verification && <div className="daily-plan-task-badges" aria-label={t('progress.dailyLearningPlanViewVerificationStatus')}>
      {Object.entries(item.verification).filter(([key, value]) => checkNames[key] && checkStatus[value]).map(([key, value]) =>
        <span className="chip chip-slate" key={key}>{checkNames[key]}：{checkStatus[value]}</span>)}
    </div>}
    {item.due && <p>{t('progress.dailyLearningPlanViewOriginalDate', { date: shortDate(item.due) })}</p>}
    {item.moved_from && <p>{t('progress.dailyLearningPlanViewMovedFrom', { date: shortDate(item.moved_from) })}</p>}
    {item.evidence && <details className="daily-plan-evidence-details" open={focused || undefined}>
      <summary>{t('progress.dailyLearningPlanViewViewEvidence')}</summary><p className="daily-plan-evidence">{item.evidence.note}</p><p>{item.question_id == null ? t('progress.dailyLearningPlanViewRequiresIndependent') : t('progress.dailyLearningPlanViewSelfRecorded')}</p>
    </details>}
    {(item.question_id != null || item.task_id !== null || item.study_session_id !== null || mayRecord && note === null) &&
      <div className="daily-plan-actions">
        {item.question_id != null && <QuestionLinks registered={registered} questionId={item.question_id} />}
        {item.task_id !== null && <Link className={'btn ' + (isCompleted ? 'btn-outline' : 'btn-primary')} to={'/learning/task/' + item.task_id}>
          {isCompleted ? t('progress.dailyLearningPlanViewViewTaskRecord') : isRetest ? t('progress.dailyLearningPlanViewOpenRetest') : t('progress.dailyLearningPlanViewStartLearning')}
        </Link>}
        {item.study_session_id !== null && <Link className="btn btn-outline" to={(registered ? '/beta' : '') + '/study/chat/' + item.study_session_id}>{t('progress.dailyLearningPlanViewOpenLearningRecord')}</Link>}
        {mayRecord && note === null && <Button primary={recommended} onClick={() => setNote('')}>{t('progress.dailyLearningPlanViewRecordEvidence')}</Button>}
      </div>}
    {mayRecord && note !== null && <form className="daily-plan-form" onSubmit={async event => {
      event.preventDefault();
      if (await onSave(item.key, note.trim())) setNote(null);
    }}>
      <label htmlFor={evidenceId}>{t('progress.dailyLearningPlanViewEvidenceLabel')}</label>
      <textarea id={evidenceId} className="textarea" rows={3} required minLength={8} maxLength={4000}
        value={note} onChange={event => setNote(event.target.value)} autoFocus />
      {error && <p className="daily-plan-error" role="alert">{error}</p>}
      <div className="daily-plan-actions">
        <Button primary type="submit" disabled={busy || note.trim().length < 8}>{t('progress.dailyLearningPlanViewSaveEvidence')}</Button>
        <Button disabled={busy} onClick={() => setNote(null)}>{t('progress.cancel')}</Button>
      </div>
    </form>}
  </article>;
}

function DayDetails({ plan, days, selectedDate, sourceKey, registered, onReplan, saveEvidence, busy, notice, error }: {
  plan: Plan; days: Map<string, Day>; selectedDate: string; sourceKey: string | null; registered: boolean; onReplan: () => void;
  saveEvidence: (key: string, note: string) => Promise<boolean>; busy: boolean; notice: string; error: string;
}) {
  const t = useT();
  const selected = days.get(selectedDate);
  const weekday = localDate(selectedDate).toLocaleDateString(getLocale(), { weekday: 'short' });
  const items = selected?.items ?? [];
  const isToday = selectedDate === plan.today;
  const isGoalDay = selectedDate === plan.deadline;
  const capacity = selected?.capacity_minutes ?? plan.daily_minutes;
  const nextTask = isToday ? items.find(item => item.status !== 'completed' && item.source !== 'retest' && item.prerequisite_complete !== false) : undefined;
  const [recording, setRecording] = useState<string | null>(null);

  return <section className="daily-plan-card daily-plan-day" aria-labelledby="daily-plan-selected-day">
    <header className="daily-plan-day-head">
      <div className="daily-plan-row">
        <h2 id="daily-plan-selected-day"><time dateTime={selectedDate}>{shortDate(selectedDate)}</time> <small>{weekday}</small></h2>
        <div className="daily-plan-task-badges">
          {isToday && <span className="daily-plan-badge daily-plan-task-stage">{t('progress.today')}</span>}
          {isGoalDay && <span className="daily-plan-badge daily-plan-item-overdue">{t('progress.dailyLearningPlanViewTargetDay')}</span>}
        </div>
      </div>
      <p>{selected ? capacity === 0
        ? items.length ? t('progress.dailyLearningPlanViewWeekendHistory', { count: items.length }) : t('progress.dailyLearningPlanViewWeekendRest')
        : t('progress.dailyLearningPlanViewTaskBudget', { tasks: items.length, minutes: selected.minutes, capacity: capacity })
        : t('progress.dailyLearningPlanViewNoDateData')}</p>
    </header>
    <div className="daily-plan-day-body" role="region" aria-label={t('progress.dailyLearningPlanViewDailyTasksAndNext')} tabIndex={0}>
      {notice && <p className="daily-plan-notice" role="status">{notice}</p>}
      {sourceKey && !items.some(item => item.key === sourceKey) && <p role="status">{t('progress.dailyLearningPlanViewSourceMissing')}</p>}
      {items.length ? <div className="daily-plan-task-list">{items.map(item => <TaskCard
        key={item.key} registered={registered} item={item} selectedDate={selectedDate} plan={plan} focused={sourceKey === item.key}
        recommended={nextTask?.key === item.key} busy={busy} error={recording === item.key ? error : ''}
        onSave={(itemKey, note) => { setRecording(itemKey); return saveEvidence(itemKey, note); }}
      />)}</div> : <p className="daily-plan-empty">{selected ? capacity === 0 ? t('progress.dailyLearningPlanViewNoWeekendTasks') : t('progress.dailyLearningPlanViewBufferDay') : t('progress.dailyLearningPlanViewSelectDate')}</p>}
      <div className="daily-plan-actions daily-plan-day-links">
        <button className="daily-plan-text-button" type="button" onClick={onReplan} aria-haspopup="dialog">{t('progress.dailyLearningPlanViewReplanReason')}</button>
        {plan.target_role === undefined && <Link className="daily-plan-text-button" to={historyUrl}>{t('progress.learningHistory')}</Link>}
      </div>
      <section className="daily-plan-upcoming" aria-label={t('progress.dailyLearningPlanViewNextThreeDays')}>
        <h3>{t('progress.dailyLearningPlanViewNextThreeDays')}</h3>
        <ul>{[0, 1, 2].map(offset => {
          const date = addDays(plan.today, offset);
          const day = days.get(date);
          return <li key={date}><time dateTime={date}>{shortDate(date)}</time><span aria-hidden="true"> · </span>{
            day ? day.items.length ? t('progress.dailyLearningPlanViewUpcomingCount', { count: day.items.length, minutes: day.minutes }) : day.capacity_minutes === 0 ? t('progress.dailyLearningPlanViewWeekendRest') : t('progress.dailyLearningPlanViewNothingScheduled') : t('progress.dailyLearningPlanViewNoPlanData')
          }{day && day.items.length > 0 && <p className="daily-plan-upcoming-tasks">
            {day.items.map(item => item.title).join(getLocale() === 'zh-CN' ? '、' : ', ')}
          </p>}</li>;
        })}</ul>
      </section>
    </div>
  </section>;
}

function PlanDialog({ title, onDismiss, busy, children }: {
  title: string; onDismiss: () => void; busy: boolean; children: React.ReactNode;
}) {
  const t = useT();
  const modal = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const dialog = modal.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  useEffect(() => heading.current?.focus(), [title]);
  const dismiss = () => {
    if (busy) return;
    modal.current?.close();
    onDismiss();
  };
  return <dialog className="daily-plan-dialog" ref={modal} aria-labelledby="daily-plan-dialog-title" onCancel={event => {
    event.preventDefault();
    dismiss();
  }}>
    <header className="daily-plan-row">
      <h2 id="daily-plan-dialog-title" ref={heading} tabIndex={-1}>{title}</h2>
      <Button aria-label={t('progress.close')} disabled={busy} onClick={dismiss}>×</Button>
    </header>
    <div className="daily-plan-dialog-body">{children}</div>
  </dialog>;
}

function GoalForm({ plan, goal, registered, onSave, busy, error }: {
  plan: Plan; goal: string; registered: boolean; onSave: (next: Settings) => Promise<boolean>; busy: boolean; error: string;
}) {
  const t = useT();
  const [deadline, setDeadline] = useState(plan.deadline);
  const [minutes, setMinutes] = useState(String(plan.daily_minutes));
  const [confirmBaseline, setConfirmBaseline] = useState(false);
  const changed = deadline !== plan.deadline || Number(minutes) !== plan.daily_minutes;
  const canAdopt = plan.progress?.status === 'unavailable' && plan.progress.baseline_date === null &&
    (!registered || plan.progress.total > 0);

  return <form className="daily-plan-form" onSubmit={event => {
    event.preventDefault();
    void onSave({ deadline, daily_minutes: Number(minutes), confirm_baseline: canAdopt && !changed && confirmBaseline });
  }}>
    <p>{goal}</p>
    <label htmlFor="daily-deadline">{registered ? t('progress.dailyLearningPlanViewLearningGoalDateField') : t('progress.dailyLearningPlanViewApplicationDate')}</label>
    <input id="daily-deadline" type="date" required min={plan.today} max={addDays(plan.today, 180)}
      value={deadline} onChange={event => setDeadline(event.target.value)} />
    <label htmlFor="daily-minutes">{plan.schedule_profile ? t('progress.dailyLearningPlanViewWeekdayCapacity') : t('progress.dailyLearningPlanViewDailyCapacity')}</label>
    <input id="daily-minutes" type="number" required min={35} max={480} value={minutes} onChange={event => setMinutes(event.target.value)} />
    <p>{t('progress.dailyLearningPlanViewReplanExplanation')}</p>
    {canAdopt && !changed && <label className="daily-plan-checkbox">
      <input type="checkbox" checked={confirmBaseline} onChange={event => setConfirmBaseline(event.target.checked)} />
      {t('progress.dailyLearningPlanViewSetBaseline')}
    </label>}
    {error && <p className="daily-plan-error" role="alert">{error}</p>}
    <div className="daily-plan-actions"><Button primary type="submit" disabled={busy}>{t('progress.dailyLearningPlanViewSaveAndReview')}</Button></div>
  </form>;
}

function RescheduleDetails({ plan, registered, onEdit, onReplan, busy, error }: {
  plan: Plan; registered: boolean; onEdit: () => void; onReplan?: () => void; busy: boolean; error: string;
}) {
  const t = useT();
  const changes = plan.days.flatMap(day => day.items
    .filter(item => (item.moved_from && item.status !== 'completed') || item.status === 'overdue')
    .map(item => ({ day: day.date, item })));
  return <>
    <p>{registered ? t('progress.dailyLearningPlanViewRegisteredRescheduleRules') : t('progress.dailyLearningPlanViewPersonalRescheduleRules')}</p>
    <section className="daily-plan-upcoming">
      <h3>{t('progress.dailyLearningPlanViewUpdatedSchedule')}</h3>
      {changes.length ? <ul>{changes.map(({ day, item }) => <li key={item.key}>{item.title}：{
        item.moved_from ? shortDate(item.moved_from) + ' → ' + shortDate(day) : shortDate(day) + t('progress.dailyLearningPlanViewOverdueToReschedule')
      }</li>)}</ul> : <p>{t('progress.dailyLearningPlanViewNoScheduleChanges')}</p>}
    </section>
    {plan.overflow.length > 0 && <section className="daily-plan-upcoming">
      <h3>{t('progress.dailyLearningPlanViewOverflow', { count: plan.overflow.length })}</h3>
      <ul>{plan.overflow.map(item => <li key={item.key}>{item.moved_from
        ? t('progress.dailyLearningPlanViewMovedOverflowItem', { title: item.title, minutes: item.minutes, date: shortDate(item.moved_from) })
        : t('progress.dailyLearningPlanViewOverflowItem', { title: item.title, minutes: item.minutes })}{item.question_id != null && <div className="daily-plan-actions"><QuestionLinks registered={registered} questionId={item.question_id} /></div>}</li>)}</ul>
    </section>}
    {registered && error && <p className="daily-plan-error" role="alert">{error}</p>}
    <div className="daily-plan-actions">
      <Button disabled={registered && !plan.target_role} onClick={onEdit}>{registered ? t('progress.dailyLearningPlanViewEditLearningGoal') : t('progress.dailyLearningPlanViewEditApplicationGoal')}</Button>
      {onReplan && plan.target_role && <Button disabled={busy} onClick={onReplan}>{t('progress.dailyLearningPlanViewReschedule')}</Button>}
    </div>
  </>;
}

function ProgressDetails({ plan }: { plan: Plan }) {
  const t = useT();
  const progress = plan.progress;
  return <>
    <p>{t('progress.dailyLearningPlanViewCompletionFormula')}</p>
    <p>{t('progress.dailyLearningPlanViewPaceFormula')}</p>
    <p>{t('progress.dailyLearningPlanViewBaselineComparison')}</p>
    {progress?.baseline_date && <p>{t('progress.dailyLearningPlanViewBaselineDated', { date: fullDate(progress.baseline_date) })}</p>}
    {plan.target_role !== '' && (!progress || progress.status === 'unavailable') && <p>{progress?.baseline_date
      ? t('progress.dailyLearningPlanViewNoBaselineTasks')
      : t('progress.dailyLearningPlanViewSetBaselineSuggestion')}</p>}
    {plan.target_role === undefined ? <p>{t('progress.dailyLearningPlanViewVerifyInHistory')}</p> : <p>{t('progress.dailyLearningPlanViewEvidenceScope')}</p>}
  </>;
}

const dialogTitles: Record<DialogName, string> = {
  get goal() { return t('progress.dailyLearningPlanViewEditApplicationGoal'); },
  get replan() { return t('progress.dailyLearningPlanViewReplanReason'); },
  get progress() { return t('progress.dailyLearningPlanViewProgressMethod'); },
};

type Props = {
  data?: Plan;
  loading: boolean;
  loadError?: string | null;
  mode: { kind: 'personal'; goal: string; banner: React.ReactNode } | { kind: 'registered' };
  saveSettings: (settings: Settings, plan: Plan) => Promise<Plan>;
  saveEvidence: (itemKey: string, note: string, plan: Plan) => Promise<Plan>;
  replan?: (plan: Plan) => Promise<Plan>;
};

export default function DailyLearningPlanView({ data, loading, loadError, mode, saveSettings, saveEvidence, replan }: Props) {
  const t = useT();
  const [params, setParams] = useSearchParams();
  const [saved, setSaved] = useState<Plan>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [monthOverride, setMonthOverride] = useState<{ selectedDate: string; month: string }>();
  const plan = saved ?? data;
  const registered = mode.kind === 'registered';
  const goal = registered ? plan?.target_role || t('progress.dailyLearningPlanViewTargetRoleMissing') : mode.goal;
  const banner = mode.kind === 'personal' ? mode.banner : null;
  const view = params.get('plan');
  const modal = view === 'goal' || view === 'replan' || view === 'progress' ? view : undefined;
  const sourceKey = params.get('item');

  const write = async (request: () => Promise<Plan>) => {
    if (busy) return false;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      setSaved(await request());
      return true;
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : t('progress.dailyLearningPlanViewSaveError'));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const heading = <header className="daily-plan-heading">
    <div><h1>{t('progress.dailyLearningPlanViewHeading')}</h1><p>{registered ? t('progress.dailyLearningPlanViewRegisteredDescription') : t('progress.dailyLearningPlanViewPersonalDescription')}</p></div>
    {!registered && <Link className="btn btn-outline" to={historyUrl}>{t('progress.learningHistory')}</Link>}
  </header>;

  if (!plan) return <main className="daily-plan">
    {heading}{banner}<p role={loadError ? 'alert' : 'status'}>{loading ? t('progress.dailyLearningPlanViewLoading') : loadError ?? t('progress.dailyLearningPlanViewUnavailable')}</p>
  </main>;

  const requestedDate = params.get('date');
  const selectedDate = isDateKey(requestedDate) ? requestedDate : plan.today;
  const days = new Map(plan.days.map(day => [day.date, day]));
  const selectedMonth = monthKey(selectedDate);
  const firstMonth = monthKey(plan.days[0]?.date ?? plan.today);
  const lastMonth = monthKey(plan.days[plan.days.length - 1]?.date ?? plan.today);
  const month = monthOverride?.selectedDate === selectedDate && monthOverride.month >= firstMonth && monthOverride.month <= lastMonth
    ? monthOverride.month : selectedMonth >= firstMonth && selectedMonth <= lastMonth ? selectedMonth : monthKey(plan.today);
  const shifted = plan.days.reduce((count, day) => count + day.items.filter(item => item.moved_from && item.status === 'scheduled').length, 0);
  const replanSummary = [
    shifted > 0 && t('progress.dailyLearningPlanViewShiftedCount', { count: shifted }),
    plan.overdue_count > 0 && t('progress.dailyLearningPlanViewOverdueCount', { count: plan.overdue_count }),
    plan.overflow.length > 0 && t('progress.dailyLearningPlanViewOverflowCount', { count: plan.overflow.length }),
  ].filter(Boolean).join(getLocale() === 'zh-CN' ? '，' : ', ');

  const selectDate = (date: string) => {
    setMonthOverride(undefined);
    setError('');
    setNotice('');
    if (date !== selectedDate || sourceKey) setParams({ plan: 'day', date });
  };

  const setModal = (next?: DialogName) => {
    setError('');
    const search = new URLSearchParams(params);
    if (next) search.set('plan', next);
    else if (search.has('date') || sourceKey) search.set('plan', 'day');
    else search.delete('plan');
    setParams(search, { replace: modal !== undefined });
  };

  return <main className="daily-plan">
    {heading}
    {banner}
    <GoalSummary plan={plan} goal={goal} registered={registered} onOpen={setModal} />
    {registered && !plan.progress?.total && <p className="daily-plan-card">
      {plan.target_role ? t('progress.dailyLearningPlanViewSaveQuestions') : t('progress.dailyLearningPlanViewSetRoleAndSave')}
    </p>}
    <div className="daily-plan-layout">
      <MonthCalendar plan={plan} days={days} month={month} selectedDate={selectedDate}
        onMonth={value => setMonthOverride({ selectedDate, month: value })} onSelect={selectDate} />
      <DayDetails registered={registered} key={selectedDate} plan={plan} days={days} selectedDate={selectedDate} sourceKey={sourceKey}
        onReplan={() => setModal('replan')} busy={busy} error={error} notice={notice}
        saveEvidence={async (itemKey, note) => {
          const ok = await write(() => saveEvidence(itemKey, note, plan));
          if (ok) setNotice(t('progress.dailyLearningPlanViewRecordSaved'));
          return ok;
        }} />
    </div>
    {replanSummary && <section className="daily-plan-card daily-plan-replan">
      <p><span className="daily-plan-badge daily-plan-item-overdue">{t('progress.dailyLearningPlanViewReplanNotice')}</span>{t('progress.dailyLearningPlanViewRescheduleSummary', { summary: replanSummary })}</p>
      <Button onClick={() => setModal('replan')} aria-haspopup="dialog">{t('progress.dailyLearningPlanViewViewChanges')}</Button>
    </section>}
    {modal && <PlanDialog title={registered && modal === 'goal' ? t('progress.dailyLearningPlanViewEditLearningGoal') : dialogTitles[modal]} busy={busy} onDismiss={() => setModal()}>
      {modal === 'goal' && registered && !plan.target_role && <p>{t('progress.dailyLearningPlanViewSetRole')}</p>}
      {modal === 'goal' && (!registered || plan.target_role) && <GoalForm busy={busy} error={error} plan={plan} goal={goal} registered={registered} onSave={async settings => {
        const ok = await write(() => saveSettings(settings, plan));
        if (ok) setModal('replan');
        return ok;
      }} />}
      {modal === 'replan' && <RescheduleDetails plan={plan} registered={registered} busy={busy} error={error}
        onEdit={() => setModal('goal')} onReplan={replan && (() => void write(() => replan(plan)))} />}
      {modal === 'progress' && <ProgressDetails plan={plan} />}
    </PlanDialog>}
  </main>;
}
