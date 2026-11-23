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
const timeBudget = (minutes: number) => minutes % 60 === 0 ? `${minutes / 60} 小时` : `${minutes} 分钟`;
const weekdays = ['一', '二', '三', '四', '五', '六', '日'];
const itemStatus = { scheduled: '待完成', completed: '已记录', overdue: '已逾期', unscheduled: '待取舍' };
const calendarStatus: Record<CalendarStatus, string> = { completed: '已完成', scheduled: '已安排', shifted: '顺延 / 逾期', retest: '复测' };
const checkNames: Record<string, string> = { understanding: '理解', practice: '实践', transfer: '迁移', retention: '保持' };
const checkStatus: Record<string, string> = {
  UNVERIFIED: '未验证', ASSISTED_CORRECT: '有辅助正确', INDEPENDENT_CORRECT: '独立正确', PRACTICE_CORRECT: '本题练习正确',
  NOT_SUBMITTED: '未提交', PENDING_REVIEW: '待检查', NEEDS_REVISION: '需修改', TASK_VERIFIED: '本任务已验证',
  CODE_REVIEWED: '代码检查已完成', PENDING_RETEST: '待复测', PASSED_THIS_ATTEMPT: '本次通过', NOT_DUE: '未到期',
};

const localDate = (value: string) => new Date(value + 'T12:00:00');
const dateKey = (date: Date) => [
  String(date.getFullYear()).padStart(4, '0'), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0'),
].join('-');
const shortDate = (value: string) => localDate(value).toLocaleDateString('zh-CN', { month: 'long', day: 'numeric' });
const fullDate = (value: string) => localDate(value).toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' });
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
  const progress = plan.progress;
  const delta = progress?.delta ?? null;
  const comparable = progress?.status !== 'unavailable' && delta !== null &&
    progress?.baseline_completed != null && progress.expected != null;
  const percent = progress && progress.total > 0 ? Math.round(100 * progress.completed / progress.total) : null;
  const remaining = daysBetween(plan.today, plan.deadline);
  const paceLabel = comparable
    ? { ahead: '领先 ' + Math.abs(delta) + ' 项', on_track: '按计划', behind: '落后 ' + Math.abs(delta) + ' 项', unavailable: '暂无进度基准' }[progress.status]
    : '暂无法比较';
  const plannedCount = comparable && progress.baseline_completed === progress.completed ? progress.expected : null;
  const plannedMarker = plannedCount !== null && progress && progress.total > 0 ? 100 * plannedCount / progress.total : null;

  return <section className="daily-plan-card daily-plan-goal" aria-labelledby="daily-plan-goal">
    <div className="daily-plan-row">
      <span className="daily-plan-eyebrow">{registered ? '学习目标' : '投递目标'}</span>
      <Button disabled={registered && !plan.target_role} onClick={() => onOpen('goal')} aria-haspopup="dialog">调整目标</Button>
    </div>
    <h2 id="daily-plan-goal">{goal}</h2>
    {(!registered || plan.target_role) && <p className="daily-plan-goal-date">
      {registered && !plan.deadline_confirmed && '建议目标日 · '}
      <time dateTime={plan.deadline}>{fullDate(plan.deadline)}</time>{!registered && '开始投递'}
      <span aria-hidden="true"> · </span>
      {remaining > 0 ? '还剩 ' + remaining + ' 天' : remaining === 0 ? '今天是目标日' : '目标日已过去 ' + Math.abs(remaining) + ' 天'}
    </p>}
    {plan.schedule_profile === 'agent_interview_sprint' && <p className="daily-plan-goal-date">
      周一至周五每天 {timeBudget(plan.daily_minutes)} · 周末休息
      {plan.sprint_start_date && plan.today < plan.sprint_start_date && ` · ${shortDate(plan.sprint_start_date)}开始`}
    </p>}
    <div className="daily-plan-row daily-plan-progress-line">
      <div className="daily-plan-progress-count">
        {percent !== null && <strong>{percent}%</strong>}
        <span>{progress ? (progress.total ? progress.completed + ' / ' + progress.total + ' 项已完成' : '暂无任务') : '完成进度暂不可用'}</span>
      </div>
      <span className={'daily-plan-badge daily-plan-pace-' + (comparable ? progress.status : 'unavailable')}>{paceLabel}</span>
    </div>
    {percent !== null && <div className="daily-plan-track" role="progressbar" aria-label="当前计划完成进度" aria-valuenow={percent}
      aria-valuetext={progress?.completed + ' / ' + progress?.total + ' 项已完成'}>
      <span className="daily-plan-track-fill" style={{ width: percent + '%' }} />
      {plannedMarker !== null && <span className="daily-plan-track-marker" aria-hidden="true"
        title={'同期应完成 ' + plannedCount + ' 项'} style={{ left: plannedMarker + '%' }} />}
    </div>}
    <div className="daily-plan-row daily-plan-goal-footer">
      <p>{comparable ? '基准任务：实际 ' + progress.baseline_completed + ' 项 · 同期应完成 ' + progress.expected + ' 项' : '计划节奏暂无法比较'}</p>
      <button className="daily-plan-text-button" type="button" onClick={() => onOpen('progress')} aria-haspopup="dialog">进度怎么算</button>
    </div>
  </section>;
}

function MonthCalendar({ plan, days, month, selectedDate, onMonth, onSelect }: {
  plan: Plan; days: Map<string, Day>; month: string; selectedDate: string;
  onMonth: (value: string) => void; onSelect: (value: string) => void;
}) {
  const firstAvailable = plan.days[0]?.date ?? plan.today;
  const lastAvailable = plan.days[plan.days.length - 1]?.date ?? plan.today;
  const first = localDate(month);
  const firstCell = addDays(month, -((first.getDay() + 6) % 7));
  const nextMonth = addMonths(month, 1);
  const previousMonth = addMonths(month, -1);
  const rowCount = Math.ceil(daysBetween(firstCell, nextMonth) / 7);
  const dates = Array.from({ length: rowCount * 7 }, (_, index) => addDays(firstCell, index));
  const [year, monthNumber] = [first.getFullYear(), first.getMonth() + 1];

  return <section className="daily-plan-card daily-plan-calendar" aria-labelledby="daily-plan-month">
    <header className="daily-plan-row">
      <h2 id="daily-plan-month" aria-live="polite">{year} 年 {monthNumber} 月</h2>
      <nav className="daily-plan-calendar-nav" aria-label="月份导航">
        <Button className="btn btn-outline daily-plan-month-arrow" aria-label="上个月" disabled={month <= monthKey(firstAvailable)}
          onClick={() => onMonth(previousMonth)}><span className="daily-plan-chevron" aria-hidden="true" /></Button>
        <Button onClick={() => onSelect(plan.today)}>回到今天</Button>
        <Button className="btn btn-outline daily-plan-month-arrow" aria-label="下个月" disabled={month >= monthKey(lastAvailable)}
          onClick={() => onMonth(nextMonth)}><span className="daily-plan-chevron" aria-hidden="true" /></Button>
      </nav>
    </header>
    <ul className="daily-plan-legend" aria-label="日历状态">
      <li><span className="daily-plan-dot daily-plan-completed" />已完成</li>
      <li><span className="daily-plan-dot daily-plan-scheduled" />已安排</li>
      <li><span className="daily-plan-dot daily-plan-shifted" />顺延 / 逾期</li>
      <li><span className="daily-plan-dot daily-plan-retest" />复测</li>
    </ul>
    <div className="daily-plan-calendar-grid">
      {weekdays.map(weekday => <span key={weekday} className="daily-plan-weekday" aria-hidden="true">{weekday}</span>)}
      {dates.map(date => {
        const day = days.get(date);
        const kinds = calendarStatuses(day?.items ?? []);
        const isAdjacent = monthKey(date) !== month;
        const isToday = date === plan.today;
        const isDeadline = date === plan.deadline;
        const isSelected = date === selectedDate;
        const completed = day?.items.filter(item => item.status === 'completed').length ?? 0;
        const shortSummary = day?.items.length
          ? completed === day.items.length ? '完成 ' + completed + '\u00a0项' : day.items.length + ' 项 · ' + day.minutes + '\u00a0分'
          : null;
        const label = [
          fullDate(date), isToday && '今天', isDeadline && (plan.target_role === undefined ? '投递目标日' : '学习目标日'), isSelected && '已选择',
          day?.capacity_minutes === 0 && '周末休息',
          day ? day.items.length ? day.items.length + ' 项任务，' + day.minutes + ' 分钟' : '当天没有安排' : '日期暂无数据',
          ...kinds.map(kind => calendarStatus[kind]),
        ].filter(Boolean).join('，');
        const content = <>
          <span className="daily-plan-cell-heading">
            <time dateTime={date}>{Number(date.slice(-2))}</time>
            {isToday && <span className="daily-plan-cell-today">今天</span>}
            {isDeadline && <span className="daily-plan-cell-deadline">目标日</span>}
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
  return <>
    <Link className="btn btn-primary" to={(registered ? '/beta' : '') + '/study/chat?question=' + questionId}>开始学习</Link>
    <Link className="btn btn-outline" to={(registered ? '/beta' : '') + '/exam?question=' + questionId}>开始答题</Link>
  </>;
}

function TaskCard({ item, selectedDate, plan, recommended, focused, registered, onSave, busy, error }: {
  item: Item; selectedDate: string; plan: Plan; recommended: boolean; focused: boolean; registered: boolean;
  onSave: (itemKey: string, note: string) => Promise<boolean>; busy: boolean; error: string;
}) {
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
      <span className={'daily-plan-badge ' + (isRetest ? 'daily-plan-retest-badge' : 'daily-plan-task-stage')}>{item.stage} · {item.minutes} 分钟</span>
      {(!recommended || isCompleted) && <span className={'daily-plan-badge daily-plan-item-' + item.status}>{itemStatus[item.status]}</span>}
    </div>
    <h3>{recommended ? '学习建议：先做「' : ''}{item.title}{recommended && '」'}</h3>
    <p>{item.criteria}</p>
    {awaitingPriorRound && !isCompleted && <p>先记录同一道题的上一遍，再记录这一遍。</p>}
    {item.verification && <div className="daily-plan-task-badges" aria-label="独立验证状态">
      {Object.entries(item.verification).filter(([key, value]) => checkNames[key] && checkStatus[value]).map(([key, value]) =>
        <span className="chip chip-slate" key={key}>{checkNames[key]}：{checkStatus[value]}</span>)}
    </div>}
    {item.due && <p>原定日期：{shortDate(item.due)}</p>}
    {item.moved_from && <p>从 {shortDate(item.moved_from)} 顺延。</p>}
    {item.evidence && <details className="daily-plan-evidence-details" open={focused || undefined}>
      <summary>查看完成依据</summary><p className="daily-plan-evidence">{item.evidence.note}</p><p>{item.question_id == null ? '能力验证仍需独立评估。' : '这是你自行记录的学习进度。'}</p>
    </details>}
    {(item.question_id != null || item.task_id !== null || item.study_session_id !== null || mayRecord && note === null) &&
      <div className="daily-plan-actions">
        {item.question_id != null && <QuestionLinks registered={registered} questionId={item.question_id} />}
        {item.task_id !== null && <Link className={'btn ' + (isCompleted ? 'btn-outline' : 'btn-primary')} to={'/learning/task/' + item.task_id}>
          {isCompleted ? '查看任务记录' : isRetest ? '打开复测' : '开始学习'}
        </Link>}
        {item.study_session_id !== null && <Link className="btn btn-outline" to={(registered ? '/beta' : '') + '/study/chat/' + item.study_session_id}>打开学习记录</Link>}
        {mayRecord && note === null && <Button primary={recommended} onClick={() => setNote('')}>记录完成依据</Button>}
      </div>}
    {mayRecord && note !== null && <form className="daily-plan-form" onSubmit={async event => {
      event.preventDefault();
      if (await onSave(item.key, note.trim())) setNote(null);
    }}>
      <label htmlFor={evidenceId}>填写答案、产物链接及你核对过的结果</label>
      <textarea id={evidenceId} className="textarea" rows={3} required minLength={8} maxLength={4000}
        value={note} onChange={event => setNote(event.target.value)} autoFocus />
      {error && <p className="daily-plan-error" role="alert">{error}</p>}
      <div className="daily-plan-actions">
        <Button primary type="submit" disabled={busy || note.trim().length < 8}>保存完成依据</Button>
        <Button disabled={busy} onClick={() => setNote(null)}>取消</Button>
      </div>
    </form>}
  </article>;
}

function DayDetails({ plan, days, selectedDate, sourceKey, registered, onReplan, saveEvidence, busy, notice, error }: {
  plan: Plan; days: Map<string, Day>; selectedDate: string; sourceKey: string | null; registered: boolean; onReplan: () => void;
  saveEvidence: (key: string, note: string) => Promise<boolean>; busy: boolean; notice: string; error: string;
}) {
  const selected = days.get(selectedDate);
  const weekday = localDate(selectedDate).toLocaleDateString('zh-CN', { weekday: 'short' });
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
          {isToday && <span className="daily-plan-badge daily-plan-task-stage">今天</span>}
          {isGoalDay && <span className="daily-plan-badge daily-plan-item-overdue">目标日</span>}
        </div>
      </div>
      <p>{selected ? capacity === 0
        ? items.length ? `周末休息 · ${items.length} 项历史记录` : '周末休息'
        : `${items.length} 项任务 · ${selected.minutes} / ${capacity} 分钟`
        : '日期暂无数据'}</p>
    </header>
    <div className="daily-plan-day-body" role="region" aria-label="当日任务与后续安排" tabIndex={0}>
      {notice && <p className="daily-plan-notice" role="status">{notice}</p>}
      {sourceKey && !items.some(item => item.key === sourceKey) && <p role="status">这项来源练习已不在所选日期的计划中。</p>}
      {items.length ? <div className="daily-plan-task-list">{items.map(item => <TaskCard
        key={item.key} registered={registered} item={item} selectedDate={selectedDate} plan={plan} focused={sourceKey === item.key}
        recommended={nextTask?.key === item.key} busy={busy} error={recording === item.key ? error : ''}
        onSave={(itemKey, note) => { setRecording(itemKey); return saveEvidence(itemKey, note); }}
      />)}</div> : <p className="daily-plan-empty">{selected ? capacity === 0 ? '周末不安排新任务。' : '当天没有安排，可以留作缓冲。' : '请选择日历中有数据的日期。'}</p>}
      <div className="daily-plan-actions daily-plan-day-links">
        <button className="daily-plan-text-button" type="button" onClick={onReplan} aria-haspopup="dialog">查看重排原因</button>
        {plan.target_role === undefined && <Link className="daily-plan-text-button" to={historyUrl}>学习与复测记录</Link>}
      </div>
      <section className="daily-plan-upcoming" aria-label="接下来三天">
        <h3>接下来三天</h3>
        <ul>{[0, 1, 2].map(offset => {
          const date = addDays(plan.today, offset);
          const day = days.get(date);
          return <li key={date}><time dateTime={date}>{shortDate(date)}</time><span aria-hidden="true"> · </span>{
            day ? day.items.length ? day.items.length + ' 项 / ' + day.minutes + ' 分钟' : day.capacity_minutes === 0 ? '周末休息' : '暂无安排' : '暂无计划数据'
          }{day && day.items.length > 0 && <p className="daily-plan-upcoming-tasks">
            {day.items.map(item => item.title).join('、')}
          </p>}</li>;
        })}</ul>
      </section>
    </div>
  </section>;
}

function PlanDialog({ title, onDismiss, busy, children }: {
  title: string; onDismiss: () => void; busy: boolean; children: React.ReactNode;
}) {
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
      <Button aria-label="关闭" disabled={busy} onClick={dismiss}>×</Button>
    </header>
    <div className="daily-plan-dialog-body">{children}</div>
  </dialog>;
}

function GoalForm({ plan, goal, registered, onSave, busy, error }: {
  plan: Plan; goal: string; registered: boolean; onSave: (next: Settings) => Promise<boolean>; busy: boolean; error: string;
}) {
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
    <label htmlFor="daily-deadline">{registered ? '学习目标日期' : '投递日期'}</label>
    <input id="daily-deadline" type="date" required min={plan.today} max={addDays(plan.today, 180)}
      value={deadline} onChange={event => setDeadline(event.target.value)} />
    <label htmlFor="daily-minutes">{plan.schedule_profile ? '每个工作日学习容量（分钟）' : '每日学习容量（分钟）'}</label>
    <input id="daily-minutes" type="number" required min={35} max={480} value={minutes} onChange={event => setMinutes(event.target.value)} />
    <p>调整日期或每天的学习时间会重排任务，并以今天的计划重新建立节奏基准。已有完成记录会保留。</p>
    {canAdopt && !changed && <label className="daily-plan-checkbox">
      <input type="checkbox" checked={confirmBaseline} onChange={event => setConfirmBaseline(event.target.checked)} />
      从今天起为现有计划建立进度基准
    </label>}
    {error && <p className="daily-plan-error" role="alert">{error}</p>}
    <div className="daily-plan-actions"><Button primary type="submit" disabled={busy}>保存并查看重排</Button></div>
  </form>;
}

function RescheduleDetails({ plan, registered, onEdit, onReplan, busy, error }: {
  plan: Plan; registered: boolean; onEdit: () => void; onReplan?: () => void; busy: boolean; error: string;
}) {
  const changes = plan.days.flatMap(day => day.items
    .filter(item => (item.moved_from && item.status !== 'completed') || item.status === 'overdue')
    .map(item => ({ day: day.date, item })));
  return <>
    <p>未完成的逾期任务会按剩余容量顺延。{!registered && '复测保留最早可验证日期，'}已完成的任务保留在实际完成日。</p>
    <section className="daily-plan-upcoming">
      <h3>调整后的安排</h3>
      {changes.length ? <ul>{changes.map(({ day, item }) => <li key={item.key}>{item.title}：{
        item.moved_from ? shortDate(item.moved_from) + ' → ' + shortDate(day) : shortDate(day) + '逾期，待重排'
      }</li>)}</ul> : <p>目前没有日期变动。</p>}
    </section>
    {plan.overflow.length > 0 && <section className="daily-plan-upcoming">
      <h3>目标日前排不下的 {plan.overflow.length} 项</h3>
      <ul>{plan.overflow.map(item => <li key={item.key}>{item.title}（{item.minutes} 分钟{
        item.moved_from ? '，原安排 ' + shortDate(item.moved_from) : ''
      }）{item.question_id != null && <div className="daily-plan-actions"><QuestionLinks registered={registered} questionId={item.question_id} /></div>}</li>)}</ul>
    </section>}
    {registered && error && <p className="daily-plan-error" role="alert">{error}</p>}
    <div className="daily-plan-actions">
      <Button disabled={registered && !plan.target_role} onClick={onEdit}>{registered ? '调整学习目标' : '调整投递目标'}</Button>
      {onReplan && plan.target_role && <Button disabled={busy} onClick={onReplan}>重新安排</Button>}
    </div>
  </>;
}

function ProgressDetails({ plan }: { plan: Plan }) {
  const progress = plan.progress;
  return <>
    <p>完成百分比按当前计划计算。已记录学习完成的任务计入已完成，目标日前排不下的任务也计入计划总数。</p>
    <p>计划节奏只比较基准里的同一批任务。系统按基准日期及当时排定的日程，计算到今天应完成多少项，再和同一批任务的实际完成数比较。</p>
    <p>当进度条包含基准之外的已完成任务时，计划和实际的同期对照仍在下方单独显示。</p>
    {progress?.baseline_date && <p>基准日期：{fullDate(progress.baseline_date)}。</p>}
    {plan.target_role !== '' && (!progress || progress.status === 'unavailable') && <p>{progress?.baseline_date
      ? '基准内没有可比较的任务。可以在「调整目标」中查看设置。'
      : '目前没有可比较的进度基准。可以在「调整目标」中从今天起建立基准。'}</p>}
    {plan.target_role === undefined ? <p>能力验证记录在「学习与复测记录」中。</p> : <p>完成依据是你自行记录的学习进度。</p>}
  </>;
}

const dialogTitles: Record<DialogName, string> = { goal: '调整投递目标', replan: '查看重排原因', progress: '进度怎么算' };

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
  const [params, setParams] = useSearchParams();
  const [saved, setSaved] = useState<Plan>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [monthOverride, setMonthOverride] = useState<{ selectedDate: string; month: string }>();
  const plan = saved ?? data;
  const registered = mode.kind === 'registered';
  const goal = registered ? plan?.target_role || '尚未设置目标岗位' : mode.goal;
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
      setError(problem instanceof Error ? problem.message : '保存失败，请重试。');
      return false;
    } finally {
      setBusy(false);
    }
  };

  const heading = <header className="daily-plan-heading">
    <div><h1>学习计划</h1><p>{registered ? '根据目标岗位的已保存题目安排每天的练习。' : '看清每天要做什么，以及离投递目标还有多远。'}</p></div>
    {!registered && <Link className="btn btn-outline" to={historyUrl}>学习与复测记录</Link>}
  </header>;

  if (!plan) return <main className="daily-plan">
    {heading}{banner}<p role={loadError ? 'alert' : 'status'}>{loading ? '正在读取计划…' : loadError ?? '学习计划暂不可用。'}</p>
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
    shifted > 0 && shifted + ' 项已顺延',
    plan.overdue_count > 0 && plan.overdue_count + ' 项逾期',
    plan.overflow.length > 0 && plan.overflow.length + ' 项在目标日前排不下',
  ].filter(Boolean).join('，');

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
      {plan.target_role ? '在题库中为当前岗位保存题目，日历就会安排练习。' : '请先在题库中设置目标岗位并保存练习题。'}
    </p>}
    <div className="daily-plan-layout">
      <MonthCalendar plan={plan} days={days} month={month} selectedDate={selectedDate}
        onMonth={value => setMonthOverride({ selectedDate, month: value })} onSelect={selectDate} />
      <DayDetails registered={registered} key={selectedDate} plan={plan} days={days} selectedDate={selectedDate} sourceKey={sourceKey}
        onReplan={() => setModal('replan')} busy={busy} error={error} notice={notice}
        saveEvidence={async (itemKey, note) => {
          const ok = await write(() => saveEvidence(itemKey, note, plan));
          if (ok) setNotice('已保存学习记录。');
          return ok;
        }} />
    </div>
    {replanSummary && <section className="daily-plan-card daily-plan-replan">
      <p><span className="daily-plan-badge daily-plan-item-overdue">重排提醒</span>{replanSummary}。</p>
      <Button onClick={() => setModal('replan')} aria-haspopup="dialog">查看变动</Button>
    </section>}
    {modal && <PlanDialog title={registered && modal === 'goal' ? '调整学习目标' : dialogTitles[modal]} busy={busy} onDismiss={() => setModal()}>
      {modal === 'goal' && registered && !plan.target_role && <p>请先在题库中设置目标岗位。</p>}
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
