import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useLocale, useT } from '../i18n';
import StudyMarkdown from '../components/StudyMarkdown';
import { StudyHistory, StudyComposerInput, StudyTurn } from '../components/StudyConversation';
import './study.css';
import { messageFor, renderMessage, useWorkspaceApi, type MessageKey, type UiMessage } from './api';

type CoachProps = {
  question?: { id: number; title: string };
  mode: 'study' | 'interview';
  onLibrary: () => void;
  threadId?: number;
  onNote?: (noteId: number) => void;
  onNotebook?: () => void;
  onThreadChange?: (threadId: number, replace?: boolean) => void;
};
type Action = 'chat' | 'generate' | 'hint' | 'answer' | 'followup_answer' | 'finish';
type Status = 'generating' | 'completed' | 'failed';
type StudyAction = 'auto' | 'chat' | 'generate';

type Followup = { quote: string; question: string };
type Feedback = {
  covered: { answer: 'initial' | 'followup'; quote: string; comment: string }[];
  gaps: string[];
  next_steps: string[];
};
type Turn = {
  id: number;
  user_text: string | null;
  assisted: boolean | null;
  reply: string;
  status: Status;
  error: string | null;
  attempt: number;
} & (
  | { action: 'answer'; result: Followup | null }
  | { action: 'finish'; result: Feedback | null }
  | { action: Exclude<Action, 'answer' | 'finish'>; result: null }
);
type Candidate = {
  id: number;
  number: number;
  title: string;
  reference: string | null;
  superseded: boolean;
  question_id: number | null;
};
type ThreadSummary = {
  id: number;
  title: string;
  role: string;
  direction: string;
  question_title: string | null;
  updated_at?: string;
  mode?: CoachProps['mode'];
  question_id?: number | null;
};
type Thread = ThreadSummary & {
  actions: Action[];
  turns: Turn[];
  candidates: Candidate[];
  ability_status: 'unknown' | null;
};
type ThreadPage = { threads: ThreadSummary[]; next_offset: number | null };
type TurnDraft = { action: Action; text?: string; target_number?: number };
type Submission = { thread: number; draft: TurnDraft; id: string };
type Progress = { id: number; attempt: number; status: 'generating'; reply: string };

const turnLabelKeys: Record<Action, MessageKey> = {
  chat: 'registered.coachSpeakerYou', generate: 'registered.questionRequest', hint: 'registered.interviewHint', answer: 'registered.initialResponse',
  followup_answer: 'registered.followupResponse', finish: 'registered.practiceFeedback',
};

function messageOf(error: unknown) {
  return messageFor(error, 'registered.coachRequestFailed');
}

function studyDraft(text: string, choice: StudyAction, revision: number | null): TurnDraft {
  const asksForQuestions = /(?:练|出|给|生成|设计|拟).{0,14}(?:道|组).{0,20}题|(?:生成|出)题|\b(?:generate|write|give me)\b.{0,30}\bquestions?\b/i;
  const revisesQuestion = /第[一二三四五六七八九十两\d]+题.{0,12}(?:换|改|重写)|(?:换|改|重写).{0,12}第[一二三四五六七八九十两\d]+题|\b(?:replace|revise|rewrite)\b.{0,24}(?:question|\d+(?:st|nd|rd|th)\b)/i;
  const inferred = asksForQuestions.test(text) || revisesQuestion.test(text) ? 'generate' : 'chat';
  const action = choice === 'auto' ? inferred : choice;
  return { action, text, target_number: revision ?? undefined };
}

function progressOf(data: string): Progress | null {
  const frame: unknown = JSON.parse(data);
  if (!frame || typeof frame !== 'object' || !('status' in frame) ||
      frame.status !== 'generating' || !('id' in frame) || typeof frame.id !== 'number' ||
      !('attempt' in frame) || typeof frame.attempt !== 'number' ||
      !('reply' in frame) || typeof frame.reply !== 'string') {
    return null;
  }
  return { id: frame.id, attempt: frame.attempt, status: frame.status, reply: frame.reply };
}

function ReplyText({ text, streaming = false }: { text: string; streaming?: boolean }) {
  if (streaming) return <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{text}</div>;
  return (
    <div style={{ overflowWrap: 'anywhere' }}>
      <StudyMarkdown text={text} allowLinks={false} />
    </div>
  );
}

function ReviewFeedback({ feedback }: { feedback: Feedback }) {
  const t = useT();
  return (
    <section aria-label={t('registered.practiceFeedback')} className="stack">
      <div>
        <strong>{t('registered.coveredPoints')}</strong>
        {feedback.covered.length ? (
          <ul>
            {feedback.covered.map((point, index) => (
              <li key={index}>
                {point.comment}
                <blockquote>{point.answer === 'initial' ? t('registered.initialAnswerExcerpt', { quote: point.quote }) : t('registered.followupExcerpt', { quote: point.quote })}</blockquote>
              </li>
            ))}
          </ul>
        ) : <p className="muted">{t('registered.noAnswerEvidence')}</p>}
      </div>
      {([['gaps', 'registered.gaps', feedback.gaps], ['next', 'registered.nextSteps', feedback.next_steps]] as const).map(([section, titleKey, items]) => (
        <div key={section}>
          <strong>{t(titleKey)}</strong>
          {items.length ? <ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul>
            : <p className="muted">{section === 'gaps' ? t('registered.gapsInsufficientEvidence')
              : feedback.covered[0] ? t('registered.practiceUsingExcerpt', { quote: feedback.covered[0].quote })
              : t('registered.practiceWithScenario')}</p>}
        </div>
      ))}
    </section>
  );
}

function Transcript({
  turn, canRetry, pending, onRetry, onMemory, onNote,
}: {
  turn: Turn;
  canRetry: boolean;
  pending: boolean;
  onRetry: () => void;
  onMemory: (text: string) => void;
  onNote?: () => void;
}) {
  const t = useT();
  const assistantLabel = turn.action === 'answer' ? t('registered.interviewerFollowup')
    : turn.action === 'hint' || turn.action === 'finish' ? t(turnLabelKeys[turn.action]) : t('registered.studyCoach');
  return (
    <>
      {turn.user_text && (
        <StudyTurn speaker="user" className="stack">
          <div className="row">
            <strong>{t(turnLabelKeys[turn.action])}</strong>
            {turn.assisted !== null && <small className="muted">{turn.assisted ? t('registered.hintUsed') : t('registered.independentResponse')}</small>}
          </div>
          <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{turn.user_text}</p>
          <button type="button" className="btn btn-outline" onClick={() => onMemory(turn.user_text ?? '')}>
            {t('registered.useAsExperience')}
          </button>
        </StudyTurn>
      )}
      {turn.action !== 'followup_answer' && (
        <StudyTurn speaker="assistant" className="stack">
          <strong>{assistantLabel}</strong>
          {turn.action === 'answer' && turn.result && <blockquote>{t('registered.basedOnInitialAnswer', { quote: turn.result.quote })}</blockquote>}
          {turn.reply && <ReplyText text={turn.reply} streaming={turn.status === 'generating'} />}
          {turn.action === 'finish' && turn.result && <ReviewFeedback feedback={turn.result} />}
          {onNote && turn.status === 'completed' && turn.reply && (
            <button type="button" className="btn btn-outline" disabled={pending} onClick={onNote}>{t('registered.saveAsNote')}</button>
          )}
          {turn.status === 'generating' && <p className="muted" role="status">{t('registered.replyGenerating')}</p>}
          {turn.status === 'failed' && (
            <div className="actions">
              <p className="error" role="alert">{turn.error ?? t('registered.replyIncomplete')}</p>
              {canRetry && <button type="button" className="btn btn-outline" disabled={pending} onClick={onRetry}>{t('registered.retryTurn')}</button>}
            </div>
          )}
        </StudyTurn>
      )}
    </>
  );
}

function MemoryReview({ initial, onClose }: { initial: string; onClose: (saved: boolean) => void }) {
  const t = useT();
  const { send } = useWorkspaceApi();
  const [text, setText] = useState(initial);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<UiMessage | null>(null);
  const limit = 2400;
  const length = [...text.trim()].length;
  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const reviewed = text.trim();
    if (!reviewed || length > limit || pending) return;
    setPending(true);
    setError(null);
    try {
      await send('/memories', 'POST', { text: reviewed, source: 'user_statement', confirmed: true });
      onClose(true);
    } catch (error) {
      setError(messageOf(error));
      setPending(false);
    }
  }
  return (
    <form className="card stack" onSubmit={confirm} aria-label={t('registered.confirmExperience')}>
      <strong>{t('registered.confirmExperience')}</strong>
      <p className="muted">{t('registered.confirmExperienceHelp')}</p>
      <label className="stack">
        {t('registered.editExperienceExcerpt', { limit })}
        <textarea value={text} onChange={event => setText(event.target.value)} required rows={4} autoFocus />
      </label>
      {error && <p className="error" role="alert">{renderMessage(error, t)}</p>}
      <div className="actions">
        <button className="btn btn-primary" disabled={pending || length === 0 || length > limit}>{t('registered.confirmAndSave')}</button>
        <button type="button" className="btn btn-outline" disabled={pending} onClick={() => onClose(false)}>{t('registered.cancel')}</button>
      </div>
    </form>
  );
}

function CoachSession({ question, mode, onLibrary, threadId, onThreadChange, onNote, onNotebook }: CoachProps) {
  const locale = useLocale();
  const t = useT();
  const { request, send, events } = useWorkspaceApi();
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [routeThread, setRouteThread] = useState(threadId);
  const selectionVersion = useRef(0);
  const [selected, setSelected] = useState<number | null>(threadId ?? null);
  const [historySearch, setHistorySearch] = useState('');
  const [thread, setThread] = useState<Thread | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyAttempt, setHistoryAttempt] = useState(0);
  const [detailAttempt, setDetailAttempt] = useState(0);
  const [pending, setPending] = useState(false);
  const [collecting, setCollecting] = useState<number | null>(null);
  const [error, setError] = useState<UiMessage | null>(null);
  const [notice, setNotice] = useState<MessageKey | null>(null);
  const [direction, setDirection] = useState('');
  const [newStudy, setNewStudy] = useState(false);
  const [input, setInput] = useState('');
  const [studyAction, setStudyAction] = useState<StudyAction>('auto');
  const [revision, setRevision] = useState<number | null>(null);
  const [memory, setMemory] = useState<string | null>(null);
  const submission = useRef<Submission | null>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const transcriptEnd = useRef<HTMLDivElement>(null);

  const threadList = new URLSearchParams({ mode });
  if (question) threadList.set('question_id', String(question.id));
  const listPath = '/coach/threads?' + threadList;
  const detailPath = selected === null ? null : '/coach/threads/' + selected;
  const active = thread?.id === selected && routeThread === threadId ? thread : null;
  const latest = active?.turns[active.turns.length - 1];
  const streamingId = latest?.status === 'generating' ? latest.id : null;
  const streamingAttempt = latest?.status === 'generating' ? latest.attempt : null;

  useEffect(() => {
    if (active && threadId === undefined) onThreadChange?.(active.id, true);
  }, [active, threadId, onThreadChange]);

  useEffect(() => {
    const controller = new AbortController();
    request<ThreadPage>(listPath, { signal: controller.signal })
      .then(page => {
        setThreads(page.threads);
        setSelected(current => current ?? page.threads[0]?.id ?? null);
        setNextOffset(page.next_offset);
      })
      .catch(error => { if (!controller.signal.aborted) setError(messageOf(error)); })
      .finally(() => { if (!controller.signal.aborted) setHistoryLoading(false); });
    return () => controller.abort();
  }, [request, listPath, historyAttempt]);

  useEffect(() => {
    if (routeThread === threadId) return;
    setRouteThread(threadId);
    const target = threadId ?? threads[0]?.id ?? null;
    if (target === selected) return;
    selectionVersion.current++;
    setSelected(target);
    setInput('');
    setRevision(null);
    setStudyAction('auto');
    setNewStudy(false);
    setMemory(null);
    setNotice(null);
    setError(null);
  }, [threadId, routeThread, selected, threads]);

  useEffect(() => {
    if (!detailPath) return;
    const controller = new AbortController();
    request<Thread>(detailPath, { signal: controller.signal })
      .then(value => {
        if (controller.signal.aborted) return;
        if ((value.mode && value.mode !== mode) || (question && value.question_id !== undefined && value.question_id !== question.id)) {
          setError({ key: 'registered.unrelatedThread' });
          return;
        }
        setThread(value);
      })
      .catch(error => { if (!controller.signal.aborted) setError(messageOf(error)); });
    return () => controller.abort();
  }, [request, detailPath, detailAttempt, mode, question?.id]);

  useEffect(() => {
    if (!detailPath || streamingId === null) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const stream = new AbortController();
    const path = detailPath + '/turns/' + streamingId + '/events';
    async function reconcile() {
      try {
        const current = await request<Thread>(detailPath ?? '', { signal: controller.signal });
        if (controller.signal.aborted) return;
        setThread(current);
        setThreads(items => items.map(item => item.id === current.id ? current : item));
        setError(null);
        if (current.turns[current.turns.length - 1]?.status === 'generating') {
          timer = window.setTimeout(reconcile, 1000);
        }
      } catch {
        if (controller.signal.aborted) return;
        setError({ key: 'registered.reconnectingThread' });
        timer = window.setTimeout(reconcile, 1500);
      }
    }
    function reconnect() {
      if (controller.signal.aborted || stream.signal.aborted) return;
      stream.abort();
      void reconcile();
    }
    void events(path, stream.signal, data => {
      const frame = progressOf(data);
      if (!frame) {
        reconnect();
        return;
      }
      setThread(current => current ? {
        ...current,
        turns: current.turns.map(turn => turn.id === frame.id && turn.attempt === frame.attempt
          ? { ...turn, reply: frame.reply } : turn),
      } : current);
    }).then(reconnect, reconnect);
    return () => {
      controller.abort();
      stream.abort();
      window.clearTimeout(timer);
    };
  }, [request, events, detailPath, streamingId, streamingAttempt]);

  async function loadOlder() {
    if (nextOffset === null || pending) return;
    setPending(true);
    setError(null);
    try {
      const page = await request<ThreadPage>(listPath + '&offset=' + nextOffset);
      setThreads(current => [...current, ...page.threads.filter(item => !current.some(row => row.id === item.id))]);
      setNextOffset(page.next_offset);
    } catch (error) {
      setError(messageOf(error));
    } finally {
      setPending(false);
    }
  }

  function openThread(id: number) {
    selectionVersion.current++;
    setInput('');
    setRevision(null);
    setStudyAction('auto');
    setNewStudy(false);
    setMemory(null);
    setNotice(null);
    setError(null);
    setSelected(id);
    onThreadChange?.(id);
  }

  async function start() {
    if (pending) return;
    setPending(true);
    setError(null);
    const version = selectionVersion.current;
    try {
      const result = await send<Thread>('/coach/threads', 'POST', { mode, question_id: question?.id, direction });
      if (version !== selectionVersion.current) return;
      openThread(result.id);
      setThread(result);
      setThreads(current => [result, ...current]);
      setDirection('');
    } catch (error) {
      if (version === selectionVersion.current) setError(messageOf(error));
    } finally {
      setPending(false);
    }
  }

  async function submitTurn(draft: TurnDraft) {
    if (!active || !detailPath || pending) return;
    const previous = submission.current;
    const id = previous?.thread === active.id &&
      previous.draft.action === draft.action && previous.draft.text === draft.text &&
      previous.draft.target_number === draft.target_number
      ? previous.id : crypto.randomUUID();
    submission.current = { thread: active.id, draft, id };
    setPending(true);
    setError(null);
    const version = selectionVersion.current;
    try {
      const turn = await send<Turn>(detailPath + '/turns', 'POST', { ...draft, request_id: id });
      if (version !== selectionVersion.current) return;
      submission.current = null;
      if (draft.text) {
        setInput('');
        setRevision(null);
      }
      if (turn.status !== 'generating') {
        const value = await request<Thread>(detailPath);
        if (version === selectionVersion.current) setThread(value);
      } else {
        setThread(current => current ? {
          ...current,
          actions: [],
          turns: [...current.turns.filter(row => row.id !== turn.id), turn],
        } : current);
      }
      transcriptEnd.current?.scrollIntoView({ block: 'nearest' });
    } catch (error) {
      if (version === selectionVersion.current) setError(messageOf(error));
    } finally {
      setPending(false);
    }
  }

  async function retry(turn: Turn) {
    if (!detailPath || pending) return;
    setPending(true);
    setError(null);
    const version = selectionVersion.current;
    try {
      const result = await send<Turn>(detailPath + '/turns/' + turn.id + '/retry', 'POST', { attempt: turn.attempt });
      if (version !== selectionVersion.current) return;
      if (result.status !== 'generating') {
        const value = await request<Thread>(detailPath);
        if (version === selectionVersion.current) setThread(value);
      } else {
        setThread(current => current ? {
          ...current, actions: [],
          turns: current.turns.map(row => row.id === result.id ? result : row),
        } : current);
      }
    } catch (error) {
      if (version === selectionVersion.current) setError(messageOf(error));
    } finally {
      setPending(false);
    }
  }

  async function saveNote(turn: Turn) {
    if (!detailPath || pending || !onNote) return;
    setPending(true);
    setError(null);
    const version = selectionVersion.current;
    try {
      const note = await send<{ id: number }>(detailPath + '/turns/' + turn.id + '/note', 'POST', {});
      if (version === selectionVersion.current) onNote(note.id);
    } catch (error) {
      if (version === selectionVersion.current) setError(messageOf(error));
    } finally {
      setPending(false);
    }
  }

  async function collect(candidate: Candidate) {
    if (!detailPath || collecting !== null) return;
    const id = selected;
    setCollecting(candidate.id);
    setError(null);
    const version = selectionVersion.current;
    try {
      const result = await send<{ question_id: number }>(
        detailPath + '/candidates/' + candidate.id + '/collect', 'POST', {},
      );
      if (version !== selectionVersion.current) return;
      setThread(current => current && current.id === id ? {
        ...current,
        candidates: current.candidates.map(row => row.id === candidate.id ? { ...row, ...result } : row),
      } : current);
      setNotice('registered.questionBookmarked');
    } catch (error) {
      if (version === selectionVersion.current) setError(messageOf(error));
    } finally {
      setCollecting(null);
    }
  }

  const study = mode === 'study';
  const canStart = study || Boolean(question);
  const answerAction = active?.actions.includes('answer') ? 'answer'
    : active?.actions.includes('followup_answer') ? 'followup_answer' : null;
  const canCompose = active && streamingId === null && (study || answerAction !== null);
  const visibleThreads = (active && !threads.some(item => item.id === active.id) ? [active, ...threads] : threads)
    .filter(item => item.title.toLocaleLowerCase(locale).includes(historySearch.trim().toLocaleLowerCase(locale)));
  const candidates = active?.candidates.filter(item => !item.superseded) ?? [];
  const revisions = active?.candidates.filter(item => item.superseded) ?? [];

  function compose(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = input.trim();
    if (!text) return;
    if (study) void submitTurn(studyDraft(text, studyAction, revision));
    else if (answerAction) void submitTurn({ action: answerAction, text });
  }

  return (
    <section className="panel stack public-study">
      <div className="row">
        <h2>{study ? t('registered.studyCoach') : t('registered.mockInterview')}</h2>
        {study && onNotebook && <button type="button" className="btn btn-outline" onClick={onNotebook}>{t('registered.myNotes')}</button>}
        <button type="button" className="btn btn-outline" onClick={onLibrary}>{t('registered.backToMyBank')}</button>
      </div>
      {question && !active && <p>{question.title}</p>}
      <div className="public-study-layout">
        <aside className="workspace-rail" aria-label={study ? t('registered.studyHistory') : t('registered.interviewHistory')}>
          <strong>{study ? t('registered.studyHistory') : t('registered.interviewHistory')}</strong>
          <input className="workspace-search" type="search" aria-label={t('registered.searchSessions')} placeholder={t('registered.searchLoadedSessions')}
            value={historySearch} onChange={event => setHistorySearch(event.target.value)} />
          <nav className="workspace-history" aria-label={t('registered.pastSessions')}>
            <StudyHistory rows={visibleThreads} renderRow={item => (
              <div className={'workspace-history-item' + (item.id === selected ? ' selected' : '')} key={item.id}>
                <button type="button" title={item.title} aria-current={item.id === selected ? 'true' : undefined}
                  disabled={pending} onClick={() => openThread(item.id)}>{item.title}</button>
              </div>
            )} />
            {historyLoading && <p className="muted" role="status">{t('registered.historyLoading')}</p>}
            {!historyLoading && !visibleThreads.length && <p className="muted">{historySearch ? t('registered.noMatchingSession') : t('registered.noSessionHistory')}</p>}
          </nav>
          {nextOffset !== null &&
            <button type="button" className="btn btn-outline" disabled={pending} onClick={() => void loadOlder()}>{t('registered.olderSessions')}</button>}
          {canStart && (question || active) && (
            <button type="button" className="btn btn-outline" disabled={pending || historyLoading} onClick={() => {
              if (study && !question) setNewStudy(value => !value);
              else void start();
            }}>
              {study ? newStudy ? t('registered.cancelNewSession') : t('registered.newStudyChat') : t('registered.startInterview')}
            </button>
          )}
        </aside>
        <div className="public-study-content stack">
        {error && (
          <div className="actions">
            <p className="error" role="alert">{renderMessage(error, t)}</p>
            {!active && !historyLoading && (
              <button type="button" className="btn btn-outline" onClick={() => {
                setError(null);
                if (selected === null) {
                  setHistoryLoading(true);
                  setHistoryAttempt(value => value + 1);
                } else {
                  setDetailAttempt(value => value + 1);
                }
              }}>{t('registered.reload')}</button>
            )}
          </div>
        )}
        {notice && <p className="muted" role="status">{t(notice)}</p>}
        {!active && selected !== null && !error && <p className="muted" role="status">{t('registered.threadLoading')}</p>}
        {(newStudy || !active && selected === null) && study && !question && !historyLoading && (
          <form className="stack" onSubmit={event => { event.preventDefault(); void start(); }}>
            <label className="stack">
              {t('registered.optionalPracticeTopic')}
              <input value={direction} maxLength={64} onChange={event => setDirection(event.target.value)}
                placeholder={t('registered.practiceTopicPlaceholder')} />
            </label>
            <button className="btn btn-primary" disabled={pending}>{t('registered.startStudyChat')}</button>
          </form>
        )}
        {!canStart && !active && !historyLoading && <p className="muted">{t('registered.pickQuestionToAnswer')}</p>}
        {active && (
          <>
            <p className="muted">{[active.role, active.direction].filter(Boolean).join(' · ')}</p>
            {active.question_title && <h3>{active.question_title}</h3>}
            {active.ability_status === 'unknown' && <p className="muted">{t('registered.unknownCompetency')}</p>}
            {study && active.turns.length === 0 && (
              <p className="muted">{t('registered.studyChatGuide')}</p>
            )}
            <div className="study-thread" aria-label={study ? t('registered.conversationTranscript') : t('registered.interviewTranscript')}>
              {active.turns.map(turn => (
                <Transcript key={turn.id} turn={turn} canRetry={turn.id === latest?.id} pending={pending}
                  onRetry={() => void retry(turn)} onMemory={setMemory}
                  onNote={study && onNote ? () => void saveNote(turn) : undefined} />
              ))}
              <div ref={transcriptEnd} />
            </div>
            {memory !== null && (
              <MemoryReview key={memory} initial={memory} onClose={saved => {
                setMemory(null);
                if (saved) setNotice('registered.experienceSaved');
              }} />
            )}
            {canCompose && (
              <form className="coach-compose stack" onSubmit={compose}>
                {study && (
                  <label className="stack">
                    {t('registered.conversationPurpose')}
                    <select value={studyAction} onChange={event => {
                      const choice = event.target.value;
                      setStudyAction(choice === 'generate' || choice === 'chat' ? choice : 'auto');
                      setRevision(null);
                    }}>
                      <option value="auto">{t('registered.inferChatAction')}</option>
                      <option value="chat">{t('registered.discuss')}</option>
                      <option value="generate">{t('registered.generateOrReviseQuestions')}</option>
                    </select>
                  </label>
                )}
                <label className="stack">
                  {study ? (revision ? t('registered.reviseQuestionNumber', { number: revision }) : studyAction === 'generate' ? t('registered.describeQuestionRequest') : t('registered.discussionPrompt'))
                    : answerAction === 'answer' ? t('registered.yourIndependentResponse') : t('registered.followupResponse')}
                  <StudyComposerInput ref={composer} rows={4} maxLength={6000} required value={input}
                    onChange={event => setInput(event.target.value)}
                    onSend={() => { if (!pending) composer.current?.form?.requestSubmit(); }}
                    placeholder={study && studyAction !== 'chat' ? t('registered.questionRequestExample') : undefined} />
                </label>
                <div className="actions">
                  <button className="btn btn-primary" disabled={pending || !input.trim()}>
                    {study ? t('registered.send') : t('registered.submitAnswer')}
                  </button>
                  {revision !== null && <button type="button" className="btn btn-outline" onClick={() => setRevision(null)}>{t('registered.clearSelectedQuestionNumber')}</button>}
                </div>
              </form>
            )}
            {!study && (active.actions.includes('hint') || active.actions.includes('finish')) && (
              <div className="actions">
                {active.actions.includes('hint') && <button type="button" className="btn btn-outline" disabled={pending}
                  onClick={() => void submitTurn({ action: 'hint' })}>{t('registered.askForHint')}</button>}
                {active.actions.includes('finish') && <button type="button" className="btn btn-primary" disabled={pending}
                  onClick={() => void submitTurn({ action: 'finish' })}>
                  {active.actions.includes('followup_answer') ? t('registered.skipToFeedback') : t('registered.finishAndSeeFeedback')}
                </button>}
              </div>
            )}
            {candidates.length > 0 && (
              <section className="stack" aria-label={t('registered.candidateQuestions')}>
                <h3>{t('registered.candidateQuestions')}</h3>
                <div className="grid">
                  {candidates.map(candidate => (
                    <article className="card stack" key={candidate.id}>
                      <strong>{candidate.number}. {candidate.title}</strong>
                      {candidate.reference && (
                        <details>
                          <summary>{t('registered.aiSuggestedAnswer')}</summary>
                          <ReplyText text={candidate.reference} />
                        </details>
                      )}
                      <div className="actions">
                        {candidate.question_id ? <span className="muted">{t('registered.bookmarked')}</span>
                          : <button type="button" className="btn btn-primary" disabled={collecting !== null}
                            onClick={() => void collect(candidate)}>{t('registered.bookmarkQuestionNumber', { number: candidate.number })}</button>}
                        <button type="button" className="btn btn-outline" disabled={streamingId !== null || pending}
                          onClick={() => {
                            setStudyAction('generate');
                            setRevision(candidate.number);
                            composer.current?.focus();
                          }}>{t('registered.reviseQuestionNumber', { number: candidate.number })}</button>
                      </div>
                    </article>
                  ))}
                </div>
                {revisions.length > 0 && (
                  <details>
                    <summary>{t('registered.viewReplacedQuestions')}</summary>
                    <ul>{revisions.map(item => <li key={item.id}>{item.question_id ? t('registered.replacedSavedQuestion', { number: item.number, title: item.title }) : <>{item.number}. {item.title}</>}</li>)}</ul>
                  </details>
                )}
              </section>
            )}
          </>
        )}
        </div>
      </div>
    </section>
  );
}

export default function Coach(props: CoachProps) {
  return <CoachSession key={props.mode + ':' + (props.question?.id ?? '')} {...props} />;
}
