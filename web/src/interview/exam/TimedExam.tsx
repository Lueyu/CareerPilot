import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, ApiError, workspaceStorageKey } from '../../api/client';
import { clearSavedDraft, useExamDraft } from '../../lib/useExamDraft';
import { answered, chatHref, durationLabel, errorMessage, examHref, positiveId, useCopy, type Attempt } from './examState';

const back = new URL('./assets/back.svg', import.meta.url).href;
const bookmark = new URL('./assets/bookmark.svg', import.meta.url).href;
const radio = new URL('./assets/radio.svg', import.meta.url).href;

type Position = { current: number; marked: number[] };
function readPosition(key: string): Position {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(key) ?? 'null');
    if (saved && typeof saved === 'object' && 'current' in saved && 'marked' in saved && Array.isArray(saved.marked)) {
      const current = typeof saved.current === 'number' ? positiveId(saved.current) ?? 1 : 1;
      const marked = saved.marked.filter((value: unknown): value is number =>
        typeof value === 'number' && positiveId(value) !== undefined);
      return { current, marked };
    }
  } catch { /* The exam can still be answered without browser storage. */ }
  return { current: 1, marked: [] };
}

function useRemaining(attempt: Attempt, receivedAt: number) {
  const [now, setNow] = useState(() => ({ monotonic: performance.now(), wall: Date.now() }));
  const start = Date.parse(attempt.started_at ?? '');
  const serverNow = Date.parse(attempt.server_now ?? '');
  const deadline = attempt.deadline_at ? Date.parse(attempt.deadline_at) : start + attempt.duration_minutes * 60_000;
  useEffect(() => {
    const tick = () => setNow({ monotonic: performance.now(), wall: Date.now() });
    tick();
    const interval = window.setInterval(tick, 250);
    document.addEventListener('visibilitychange', tick);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', tick);
    };
  }, [receivedAt]);
  const effectiveNow = Number.isFinite(serverNow) ? serverNow + Math.max(0, now.monotonic - receivedAt) : now.wall;
  return Number.isFinite(deadline) ? Math.max(0, (deadline - effectiveNow) / 1000) : undefined;
}

function Confirm({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return <dialog ref={ref} className="ia-exam ia-exam-dialog" aria-labelledby="ia-exam-confirm-title"
    onCancel={event => { event.preventDefault(); onClose(); }}>
    {children}
  </dialog>;
}

export function TimedExam({ attempt, receivedAt, refresh, routeSession }: {
  attempt: Attempt; receivedAt: number; refresh: () => Promise<Attempt | undefined>; routeSession?: number;
}) {
  const copy = useCopy();
  const navigate = useNavigate();
  const storageKey = workspaceStorageKey('ia-exam-position:' + attempt.id);
  const [position, setPosition] = useState(() => readPosition(storageKey));
  const [confirmation, setConfirmation] = useState<'submit' | 'leave'>();
  const [working, setWorking] = useState(false);
  const [checkSubmission, setCheckSubmission] = useState(false);
  const [error, setError] = useState('');
  const action = useRef(false);
  const remaining = useRemaining(attempt, receivedAt);
  const outOfTime = remaining === 0;
  const draft = useExamDraft(attempt, !outOfTime && !checkSubmission && (!working || confirmation === 'leave'));
  const questions = attempt.questions;
  const order = questions.some(question => question.order_no === position.current)
    ? position.current : (questions[0]?.order_no ?? 1);
  const index = questions.findIndex(question => question.order_no === order);
  const question = questions[index];
  const code = question?.answer_format === 'code';
  const entry = draft.entries[index];
  const complete = draft.entries.filter(entry => answered(entry.value)).length;
  const hasConflict = draft.entries.some(entry => entry.conflict);
  const marked = position.marked.includes(order);
  const origin = chatHref(attempt.origin_session_id ?? routeSession);

  useEffect(() => {
    try { localStorage.setItem(storageKey, JSON.stringify(position)); } catch { /* Optional storage. */ }
  }, [position, storageKey]);

  useEffect(() => {
    if (outOfTime) void refresh();
  }, [outOfTime, refresh]);

  useEffect(() => {
    if (draft.serverExam && draft.serverExam.status !== 'ACTIVE') void refresh();
  }, [draft.serverExam, refresh]);

  const select = (newOrder: number) => setPosition(previous => ({ ...previous, current: newOrder }));
  const submit = async () => {
    if (action.current || outOfTime || hasConflict || checkSubmission) return;
    action.current = true;
    setWorking(true);
    setError('');
    try {
      await draft.waitForSave().catch(() => undefined);
      const latest = draft.getEntries();
      if (latest.some(entry => entry.conflict)) {
        setError(copy('请先处理草稿冲突。', 'Resolve the draft conflict before submitting.'));
        return;
      }
      // Submit only remaining edits. The server freezes all answers in the same transaction.
      const edits = draft.payload().filter((_, index) => latest[index]?.dirty);
      await api.post(`/exams/${attempt.id}/submit`, { answers: edits });
      try { clearSavedDraft(attempt.id); } catch { /* The submitted record is already safe on the server. */ }
      navigate(examHref(attempt.id, true, routeSession), { replace: true });
    } catch (failure) {
      const current = await refresh();
      if (current?.status === 'ACTIVE' && failure instanceof ApiError && failure.status === 409) draft.reconcile(current);
      if (!current && (!(failure instanceof ApiError) || failure.status >= 500 || failure.status === 409)) {
        setCheckSubmission(true);
      }
      setError(errorMessage(failure));
    } finally {
      setConfirmation(undefined);
      setWorking(false);
      action.current = false;
    }
  };

  const leave = async () => {
    if (action.current) return;
    action.current = true;
    setWorking(true);
    setError('');
    try {
      if (!outOfTime && !checkSubmission) await draft.flush();
      navigate(origin);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setConfirmation(undefined);
      setWorking(false);
      action.current = false;
    }
  };

  if (!question) return <section className="ia-exam-card">
    <p>{copy('此答卷没有可作答的题目。', 'This paper has no answerable questions.')}</p>
    <Link className="ia-exam-button" to={origin}>{copy('返回对话', 'Return to chat')}</Link>
  </section>;

  return <>
    <header className="ia-exam-topbar">
      <div className="ia-exam-topgroup">
        <button type="button" className="ia-exam-button ia-exam-icon-button" onClick={() => setConfirmation('leave')}
          aria-label={copy('返回对话', 'Return to chat')}><img src={back} alt="" /></button>
        <div><h1>{attempt.title}</h1>
          <small>{attempt.kind === 'practice' ? copy('限时练习', 'Timed practice') : copy('考试中', 'Test in progress')} · {draft.saving
            ? copy('正在保存草稿…', 'Saving draft…')
            : draft.saveError || hasConflict ? copy('草稿待同步', 'Draft needs attention')
              : draft.entries.some(answer => answer.dirty) ? copy('待保存', 'Waiting to save') : copy('自动保存已启用', 'Autosave enabled')}</small>
        </div>
      </div>
      <div className="ia-exam-topgroup">
        <span className="ia-exam-badge ia-exam-warn" role="timer" aria-label={copy('剩余时间', 'Time remaining')}>
          {remaining === undefined ? copy('计时不可用', 'Timer unavailable') : durationLabel(remaining)}
        </span>
        <div className="ia-exam-progress">
          <small>{copy(`已作答 ${complete} / ${questions.length}`, `Answered ${complete} / ${questions.length}`)}</small>
          <progress value={complete} max={questions.length} aria-label={copy('答题进度', 'Answer progress')} />
        </div>
        <button type="button" className="ia-exam-button ia-exam-primary" disabled={outOfTime || working || hasConflict || checkSubmission}
          onClick={() => setConfirmation('submit')}>{copy('交卷', 'Submit')}</button>
      </div>
    </header>
    <main className="ia-exam-solve">
      <nav className="ia-exam-card ia-exam-navigator" aria-label={copy('题目导航', 'Question navigator')}>
        <div className="ia-exam-card-heading"><h2>{copy('题目导航', 'Question navigator')}</h2><span>{copy(`${questions.length} 题`, `${questions.length} questions`)}</span></div>
        <div className="ia-exam-order">
          {questions.map((item, offset) => {
            const saved = answered(draft.entries[offset]?.value);
            const flagged = position.marked.includes(item.order_no);
            return <button type="button" key={item.order_no} aria-current={index === offset ? 'step' : undefined}
              data-answered={saved} data-marked={flagged} onClick={() => select(item.order_no)}
              aria-label={copy(`第 ${item.order_no} 题`, `Question ${item.order_no}`)
                + (saved ? copy('，已答', ', answered') : '') + (flagged ? copy('，已标记', ', marked') : '')}>
              {item.order_no}
            </button>;
          })}
        </div>
        <p className="ia-exam-legend"><span>{copy('已答', 'Answered')}</span><span>{copy('待答', 'Unanswered')}</span><span>{copy('标记', 'Marked')}</span></p>
      </nav>
      <section className="ia-exam-card ia-exam-answer">
        <div className="ia-exam-card-heading">
          <span className="ia-exam-badge">{question.qtype === 'mcq' ? copy('单选', 'Single choice')
            : code ? copy('代码', 'Code') : copy('简答', 'Written answer')}</span>
          <small>{copy(`第 ${index + 1} / ${questions.length} 题`, `Question ${index + 1} of ${questions.length}`)}</small>
          <button className="ia-exam-button ia-exam-mark" type="button" aria-pressed={marked}
            onClick={() => setPosition(previous => ({ ...previous, marked: marked
              ? previous.marked.filter(value => value !== order) : [...previous.marked, order] }))}>
            <img src={bookmark} alt="" />{marked ? copy('已标记', 'Marked') : copy('标记', 'Mark')}
          </button>
        </div>
        <h2 id="ia-exam-question-title">{question.order_no}. {question.title}</h2>
        {(question.skill || question.difficulty) && <p className="ia-exam-question-meta">
          {[question.skill, question.difficulty && copy(`难度 ${question.difficulty} / 5`, `Difficulty ${question.difficulty} / 5`)].filter(Boolean).join(' · ')}
        </p>}
        <fieldset aria-labelledby="ia-exam-question-title" disabled={!entry || outOfTime || working || checkSubmission}>
          {question.qtype === 'mcq'
            ? <div className="ia-exam-options">{question.options?.map((option, optionIndex) => <label key={optionIndex}>
              <input type="radio" name={`ia-exam-answer-${order}`} checked={entry?.value === optionIndex}
                onChange={() => draft.edit(index, optionIndex)} />
              <img src={radio} alt="" />
              <span>{optionIndex < 26 ? String.fromCharCode(65 + optionIndex) : optionIndex + 1}</span>
              <span>{option}</span>
            </label>)}</div>
            : <textarea rows={code ? 12 : 8} maxLength={30000}
                className={code ? 'ia-exam-code' : undefined} spellCheck={!code}
                autoCapitalize={code ? 'off' : undefined} autoCorrect={code ? 'off' : undefined}
                aria-label={code ? copy('我的代码', 'My code') : copy('我的答案', 'My answer')}
                placeholder={code ? copy('在此填写代码…', 'Write your code here…') : copy('在此填写你的答案…', 'Write your answer here…')}
                value={typeof entry?.value === 'string' ? entry.value : ''} onChange={event => draft.edit(index, event.target.value)} />}
        </fieldset>
        {code && <small>{copy('按原题标准评阅代码。', 'Code is reviewed using the question rubric.')}</small>}
        {question.qtype === 'mcq' && answered(entry?.value) && <button className="ia-exam-text-button" type="button"
          disabled={outOfTime || working || checkSubmission} onClick={() => draft.edit(index, -1)}>{copy('清除选择', 'Clear selection')}</button>}
        {entry?.conflict && <div className="ia-exam-conflict" role="alert">
          <p>{copy('其他页面修改了这道题。请选择要保留的答案。', 'This answer changed in another page. Choose which version to keep.')}</p>
          <p>{copy('服务器草稿', 'Server draft')} · {typeof entry.serverValue === 'number'
            ? question.options?.[entry.serverValue] || copy('未作答', 'Unanswered') : entry.serverValue || copy('未作答', 'Unanswered')}</p>
          <button type="button" className="ia-exam-button" onClick={() => draft.resolveConflict(index, true)}>{copy('保留我的草稿', 'Keep my draft')}</button>
          <button type="button" className="ia-exam-button" onClick={() => draft.resolveConflict(index, false)}>{copy('使用服务器草稿', 'Use saved draft')}</button>
        </div>}
        {hasConflict && !entry?.conflict && <p role="status">{copy('其他题目有草稿冲突。处理后再交卷。', 'Another question has a draft conflict. Resolve it before submitting.')}</p>}
        {draft.saveError && <p className="ia-exam-error" role="alert">{draft.saveError} {!outOfTime && !hasConflict && !checkSubmission &&
          <button type="button" className="ia-exam-text-button" onClick={() => void draft.flush().catch(() => undefined)}>{copy('重试保存', 'Retry save')}</button>}</p>}
        {error && <p className="ia-exam-error" role="alert">{error}</p>}
        {checkSubmission && <p role="status">{copy('正在确认交卷结果，原答暂时锁定。', 'Answer editing is paused until submission status is confirmed.')}{' '}
          <button type="button" className="ia-exam-text-button" onClick={() => void refresh().then(current => {
            if (current?.status === 'ACTIVE') setCheckSubmission(false);
          })}>{copy('刷新交卷状态', 'Refresh submission status')}</button>
        </p>}
        {outOfTime && <p role="status">{copy('时间已到。', 'Time is up.')} <button type="button" className="ia-exam-text-button"
          onClick={() => void refresh()}>{copy('刷新状态', 'Refresh status')}</button></p>}
        <div className="ia-exam-actions ia-exam-spread">
          <button type="button" className="ia-exam-button" onClick={() => setConfirmation('leave')}>{copy('返回对话', 'Return to chat')}</button>
          <div>
            {index > 0 && <button type="button" className="ia-exam-button" onClick={() => select(questions[index - 1].order_no)}>{copy('上一题', 'Previous')}</button>}
            {index + 1 < questions.length && <button type="button" className="ia-exam-button ia-exam-primary"
              onClick={() => select(questions[index + 1].order_no)}>{copy('下一题', 'Next question')}</button>}
          </div>
        </div>
      </section>
    </main>
    {confirmation && <Confirm onClose={() => { if (!working) setConfirmation(undefined); }}>
      <h2 id="ia-exam-confirm-title">{confirmation === 'submit' ? copy('提交本次考试？', 'Submit this paper?') : copy('返回聊天工作台？', 'Return to chat?')}</h2>
      <p>{confirmation === 'submit'
        ? copy(`还有 ${questions.length - complete} 题未作答。交卷后答案将锁定并进入评分。`,
          `${questions.length - complete} questions are unanswered. Submission locks your answers and starts scoring.`)
        : copy('已保存的答案会保留，计时继续。可从组卷页重新打开此答卷。',
          'Saved answers are kept and the timer keeps running. Reopen the paper from the exam page.')}</p>
      <div className="ia-exam-actions">
        <button type="button" className="ia-exam-button" autoFocus disabled={working} onClick={() => setConfirmation(undefined)}>{copy('继续作答', 'Keep answering')}</button>
        <button type="button" className="ia-exam-button ia-exam-primary" disabled={working || (confirmation === 'submit' && (outOfTime || hasConflict))}
          onClick={() => void (confirmation === 'submit' ? submit() : leave())}>
          {confirmation === 'submit' ? copy('确认交卷', 'Confirm submission') : copy('保存并返回', 'Save and return')}
        </button>
      </div>
    </Confirm>}
  </>;
}
