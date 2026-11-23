import { useEffect, useRef, useState, type FormEvent } from 'react';
import Markdown from 'react-markdown';
import { request } from './api';

type CoachProps = {
  question?: { id: number; title: string };
  mode: 'study' | 'interview';
  onLibrary: () => void;
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

const turnLabels: Record<Action, string> = {
  chat: '你', generate: '你的出题要求', hint: '面试提示', answer: '原始作答',
  followup_answer: '追问作答', finish: '练习点评',
};

function messageOf(error: unknown) {
  return error instanceof Error ? error.message : '请求失败，请稍后重试';
}

function post<T>(path: string, body: object) {
  return request<T>(path, { method: 'POST', body: JSON.stringify(body) });
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
      <Markdown skipHtml disallowedElements={['a', 'img']} unwrapDisallowed>{text}</Markdown>
    </div>
  );
}

function ReviewFeedback({ feedback }: { feedback: Feedback }) {
  return (
    <section aria-label="练习点评" className="stack">
      <div>
        <strong>答到了什么</strong>
        {feedback.covered.length ? (
          <ul>
            {feedback.covered.map((point, index) => (
              <li key={index}>
                {point.comment}
                <blockquote>{point.answer === 'initial' ? '原答' : '追问作答'} · {point.quote}</blockquote>
              </li>
            ))}
          </ul>
        ) : <p className="muted">暂无可引用的作答证据</p>}
      </div>
      {([['可以补充', feedback.gaps], ['下一次怎么答', feedback.next_steps]] as const).map(([title, items]) => (
        <div key={title}>
          <strong>{title}</strong>
          {items.length ? <ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul>
            : <p className="muted">{title === '可以补充' ? '这次作答还不足以判断具体遗漏。'
              : feedback.covered[0] ? `练习建议：以“${feedback.covered[0].quote}”为例，说明你会用哪项结果检验这个判断。`
              : '练习建议：再选一个具体场景作答，说明判断依据和验证方法。'}</p>}
        </div>
      ))}
    </section>
  );
}

function Transcript({
  turn, canRetry, pending, onRetry, onMemory,
}: {
  turn: Turn;
  canRetry: boolean;
  pending: boolean;
  onRetry: () => void;
  onMemory: (text: string) => void;
}) {
  const assistantLabel = turn.action === 'answer' ? '面试官追问'
    : turn.action === 'hint' || turn.action === 'finish' ? turnLabels[turn.action] : '学习助手';
  return (
    <article className="card stack">
      {turn.user_text && (
        <div>
          <div className="row">
            <strong>{turnLabels[turn.action]}</strong>
            {turn.assisted !== null && <small className="muted">{turn.assisted ? '已获提示' : '独立作答'}</small>}
          </div>
          <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{turn.user_text}</p>
          <button type="button" className="secondary" onClick={() => onMemory(turn.user_text ?? '')}>
            选作项目经历
          </button>
        </div>
      )}
      {turn.action !== 'followup_answer' && (
        <div>
          <strong>{assistantLabel}</strong>
          {turn.action === 'answer' && turn.result && <blockquote>依据原答 · {turn.result.quote}</blockquote>}
          {turn.reply && <ReplyText text={turn.reply} streaming={turn.status === 'generating'} />}
          {turn.action === 'finish' && turn.result && <ReviewFeedback feedback={turn.result} />}
          {turn.status === 'generating' && <p className="muted" role="status">正在生成回复…</p>}
          {turn.status === 'failed' && (
            <div className="actions">
              <p className="error" role="alert">{turn.error ?? '回复未完成'}</p>
              {canRetry && <button type="button" className="secondary" disabled={pending} onClick={onRetry}>重试这轮</button>}
            </div>
          )}
        </div>
      )}
    </article>
  );
}

function MemoryReview({ initial, onClose }: { initial: string; onClose: (saved: boolean) => void }) {
  const [text, setText] = useState(initial);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const limit = 2400;
  const length = [...text.trim()].length;
  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const reviewed = text.trim();
    if (!reviewed || length > limit || pending) return;
    setPending(true);
    setError('');
    try {
      await post('/memories', { text: reviewed, source: 'user_statement', confirmed: true });
      onClose(true);
    } catch (error) {
      setError(messageOf(error));
      setPending(false);
    }
  }
  return (
    <form className="card stack" onSubmit={confirm} aria-label="确认项目经历">
      <strong>确认项目经历</strong>
      <p className="muted">保留你能确认的事实。保存后，助手可在练习时引用这段经历。</p>
      <label className="stack">
        从你的原文中摘取或修改（最多 {limit} 字）
        <textarea value={text} onChange={event => setText(event.target.value)} required rows={4} autoFocus />
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="actions">
        <button className="primary" disabled={pending || length === 0 || length > limit}>确认并保存</button>
        <button type="button" className="secondary" disabled={pending} onClick={() => onClose(false)}>取消</button>
      </div>
    </form>
  );
}

function CoachSession({ question, mode, onLibrary }: CoachProps) {
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [thread, setThread] = useState<Thread | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyAttempt, setHistoryAttempt] = useState(0);
  const [detailAttempt, setDetailAttempt] = useState(0);
  const [pending, setPending] = useState(false);
  const [collecting, setCollecting] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
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
  const active = thread?.id === selected ? thread : null;
  const latest = active?.turns[active.turns.length - 1];
  const streamingId = latest?.status === 'generating' ? latest.id : null;
  const streamingAttempt = latest?.status === 'generating' ? latest.attempt : null;

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
  }, [listPath, historyAttempt]);

  useEffect(() => {
    if (!detailPath) return;
    const controller = new AbortController();
    request<Thread>(detailPath, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setThread(value); })
      .catch(error => { if (!controller.signal.aborted) setError(messageOf(error)); });
    return () => controller.abort();
  }, [detailPath, detailAttempt]);

  useEffect(() => {
    if (!detailPath || streamingId === null) return;
    const controller = new AbortController();
    let timer: number | undefined;
    let reconciling = false;
    const path = '/api/v1' + detailPath + '/turns/' + streamingId + '/events';
    const source = typeof EventSource === 'undefined' ? null : new EventSource(path);
    async function reconcile() {
      try {
        const current = await request<Thread>(detailPath ?? '', { signal: controller.signal });
        if (controller.signal.aborted) return;
        setThread(current);
        setThreads(items => items.map(item => item.id === current.id ? current : item));
        setError('');
        if (current.turns[current.turns.length - 1]?.status === 'generating') {
          timer = window.setTimeout(reconcile, 1000);
        }
      } catch {
        if (controller.signal.aborted) return;
        setError('连接暂时中断，正在重新读取已保存的回复。');
        timer = window.setTimeout(reconcile, 1500);
      }
    }
    function reconnect() {
      if (reconciling) return;
      reconciling = true;
      source?.close();
      void reconcile();
    }
    if (source) {
      source.onmessage = event => {
        try {
          const frame = progressOf(event.data);
          if (!frame) {
            reconnect();
            return;
          }
          setThread(current => current ? {
            ...current,
            turns: current.turns.map(turn => turn.id === frame.id && turn.attempt === frame.attempt
              ? { ...turn, reply: frame.reply } : turn),
          } : current);
        } catch {
          reconnect();
        }
      };
      source.onerror = reconnect;
      source.addEventListener('unavailable', reconnect);
    } else {
      void reconcile();
    }
    return () => {
      source?.close();
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [detailPath, streamingId, streamingAttempt]);

  async function loadOlder() {
    if (nextOffset === null || pending) return;
    setPending(true);
    setError('');
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
    setInput('');
    setRevision(null);
    setStudyAction('auto');
    setNewStudy(false);
    setMemory(null);
    setNotice('');
    setError('');
    setSelected(id);
  }

  async function start() {
    if (pending) return;
    setPending(true);
    setError('');
    try {
      const result = await post<Thread>('/coach/threads', { mode, question_id: question?.id, direction });
      openThread(result.id);
      setThread(result);
      setThreads(current => [result, ...current]);
      setDirection('');
    } catch (error) {
      setError(messageOf(error));
    } finally {
      setPending(false);
    }
  }

  async function send(draft: TurnDraft) {
    if (!active || !detailPath || pending) return;
    const previous = submission.current;
    const id = previous?.thread === active.id &&
      previous.draft.action === draft.action && previous.draft.text === draft.text &&
      previous.draft.target_number === draft.target_number
      ? previous.id : crypto.randomUUID();
    submission.current = { thread: active.id, draft, id };
    setPending(true);
    setError('');
    try {
      const turn = await post<Turn>(detailPath + '/turns', { ...draft, request_id: id });
      submission.current = null;
      if (draft.text) {
        setInput('');
        setRevision(null);
      }
      if (turn.status !== 'generating') {
        setThread(await request<Thread>(detailPath));
      } else {
        setThread(current => current ? {
          ...current,
          actions: [],
          turns: [...current.turns.filter(row => row.id !== turn.id), turn],
        } : current);
      }
      transcriptEnd.current?.scrollIntoView({ block: 'nearest' });
    } catch (error) {
      setError(messageOf(error));
    } finally {
      setPending(false);
    }
  }

  async function retry(turn: Turn) {
    if (!detailPath || pending) return;
    setPending(true);
    setError('');
    try {
      const result = await post<Turn>(detailPath + '/turns/' + turn.id + '/retry', { attempt: turn.attempt });
      if (result.status !== 'generating') {
        setThread(await request<Thread>(detailPath));
      } else {
        setThread(current => current ? {
          ...current, actions: [],
          turns: current.turns.map(row => row.id === result.id ? result : row),
        } : current);
      }
    } catch (error) {
      setError(messageOf(error));
    } finally {
      setPending(false);
    }
  }

  async function collect(candidate: Candidate) {
    if (!detailPath || collecting !== null) return;
    const id = selected;
    setCollecting(candidate.id);
    setError('');
    try {
      const result = await post<{ question_id: number }>(
        detailPath + '/candidates/' + candidate.id + '/collect', {},
      );
      setThread(current => current && current.id === id ? {
        ...current,
        candidates: current.candidates.map(row => row.id === candidate.id ? { ...row, ...result } : row),
      } : current);
      setNotice('已收藏到我的题库');
    } catch (error) {
      setError(messageOf(error));
    } finally {
      setCollecting(null);
    }
  }

  const study = mode === 'study';
  const canStart = study || Boolean(question);
  const answerAction = active?.actions.includes('answer') ? 'answer'
    : active?.actions.includes('followup_answer') ? 'followup_answer' : null;
  const canCompose = active && streamingId === null && (study || answerAction !== null);
  const candidates = active?.candidates.filter(item => !item.superseded) ?? [];
  const revisions = active?.candidates.filter(item => item.superseded) ?? [];

  function compose(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = input.trim();
    if (!text) return;
    if (study) void send(studyDraft(text, studyAction, revision));
    else if (answerAction) void send({ action: answerAction, text });
  }

  return (
    <section className="panel stack">
      <div className="row">
        <h2>{study ? '学习助手' : '模拟面试'}</h2>
        <button type="button" className="secondary" onClick={onLibrary}>回到我的题库</button>
      </div>
      {question && !active && <p>{question.title}</p>}
      <div className="actions">
        {historyLoading && <p className="muted" role="status">正在读取历史…</p>}
        {threads.length > 0 && (
          <label className="stack">
            {study ? '学习历史' : '面试历史'}
            <select value={selected ?? ''} disabled={pending} onChange={event => openThread(Number(event.target.value))}>
              {threads.map(item => <option value={item.id} key={item.id}>{item.title} · {item.role} · #{item.id}</option>)}
            </select>
          </label>
        )}
        {nextOffset !== null &&
          <button type="button" className="secondary" disabled={pending} onClick={() => void loadOlder()}>更早的记录</button>}
        {canStart && (question || active) && (
          <button type="button" className="secondary" disabled={pending || historyLoading} onClick={() => {
            if (study && !question) setNewStudy(value => !value);
            else void start();
          }}>
            {study ? newStudy ? '取消新建' : '新建学习对话' : '开始一轮面试'}
          </button>
        )}
      </div>
      {error && (
        <div className="actions">
          <p className="error" role="alert">{error}</p>
          {!active && !historyLoading && (
            <button type="button" className="secondary" onClick={() => {
              setError('');
              if (selected === null) {
                setHistoryLoading(true);
                setHistoryAttempt(value => value + 1);
              } else {
                setDetailAttempt(value => value + 1);
              }
            }}>重新读取</button>
          )}
        </div>
      )}
      {notice && <p className="muted" role="status">{notice}</p>}
      {!active && selected !== null && !error && <p className="muted" role="status">正在读取这段记录…</p>}
      {(newStudy || !active && selected === null) && study && !question && !historyLoading && (
        <form className="stack" onSubmit={event => { event.preventDefault(); void start(); }}>
          <label className="stack">
            练习方向（选填）
            <input value={direction} maxLength={64} onChange={event => setDirection(event.target.value)}
              placeholder="留空沿用题库中选中的方向" />
          </label>
          <button className="primary" disabled={pending}>开始学习对话</button>
        </form>
      )}
      {!canStart && !active && !historyLoading && <p className="muted">从题库选一道题，开始独立作答。</p>}
      {active && (
        <>
          <p className="muted">{[active.role, active.direction].filter(Boolean).join(' · ')}</p>
          {active.question_title && <h3>{active.question_title}</h3>}
          {active.ability_status === 'unknown' && <p className="muted">能力状态：未知 · 本轮仅提供练习点评</p>}
          {study && active.turns.length === 0 && (
            <p className="muted">可以交流一道题，也可以生成一组候选题，挑选满意的收藏。</p>
          )}
          <div className="stack" aria-label={study ? '对话记录' : '面试记录'}>
            {active.turns.map(turn => (
              <Transcript key={turn.id} turn={turn} canRetry={turn.id === latest?.id} pending={pending}
                onRetry={() => void retry(turn)} onMemory={setMemory} />
            ))}
            <div ref={transcriptEnd} />
          </div>
          {memory !== null && (
            <MemoryReview key={memory} initial={memory} onClose={saved => {
              setMemory(null);
              if (saved) setNotice('已保存项目经历');
            }} />
          )}
          {canCompose && (
            <form className="stack" onSubmit={compose}>
              {study && (
                <label className="stack">
                  对话用途
                  <select value={studyAction} onChange={event => {
                    const choice = event.target.value;
                    setStudyAction(choice === 'generate' || choice === 'chat' ? choice : 'auto');
                    setRevision(null);
                  }}>
                    <option value="auto">按消息内容识别</option>
                    <option value="chat">交流学习</option>
                    <option value="generate">生成或修改候选题</option>
                  </select>
                </label>
              )}
              <label className="stack">
                {study ? (revision ? '修改第 ' + revision + ' 题' : studyAction === 'generate' ? '出题或修改要求' : '想讨论什么')
                  : answerAction === 'answer' ? '你的独立作答' : '追问作答'}
                <textarea ref={composer} rows={4} maxLength={6000} required value={input}
                  onChange={event => setInput(event.target.value)}
                  placeholder={study && studyAction !== 'chat' ? '例如，出三道活动复盘题，或把第二题换成实习场景' : undefined} />
              </label>
              <div className="actions">
                <button className="primary" disabled={pending || !input.trim()}>
                  {study ? '发送' : '提交作答'}
                </button>
                {revision !== null && <button type="button" className="secondary" onClick={() => setRevision(null)}>取消指定题号</button>}
              </div>
            </form>
          )}
          {!study && (active.actions.includes('hint') || active.actions.includes('finish')) && (
            <div className="actions">
              {active.actions.includes('hint') && <button type="button" className="secondary" disabled={pending}
                onClick={() => void send({ action: 'hint' })}>给我一条提示</button>}
              {active.actions.includes('finish') && <button type="button" className="primary" disabled={pending}
                onClick={() => void send({ action: 'finish' })}>
                {active.actions.includes('followup_answer') ? '跳过追问并查看点评' : '结束并查看点评'}
              </button>}
            </div>
          )}
          {candidates.length > 0 && (
            <section className="stack" aria-label="候选题">
              <h3>候选题</h3>
              <div className="grid">
                {candidates.map(candidate => (
                  <article className="card stack" key={candidate.id}>
                    <strong>{candidate.number}. {candidate.title}</strong>
                    {candidate.reference && (
                      <details>
                        <summary>参考答法 · AI 生成</summary>
                        <ReplyText text={candidate.reference} />
                      </details>
                    )}
                    <div className="actions">
                      {candidate.question_id ? <span className="muted">已收藏</span>
                        : <button type="button" className="primary" disabled={collecting !== null}
                          onClick={() => void collect(candidate)}>收藏第 {candidate.number} 题</button>}
                      <button type="button" className="secondary" disabled={streamingId !== null || pending}
                        onClick={() => {
                          setStudyAction('generate');
                          setRevision(candidate.number);
                          composer.current?.focus();
                        }}>修改第 {candidate.number} 题</button>
                    </div>
                  </article>
                ))}
              </div>
              {revisions.length > 0 && (
                <details>
                  <summary>查看已替换的题目</summary>
                  <ul>{revisions.map(item => <li key={item.id}>{item.number}. {item.title}{item.question_id ? ' · 已收藏' : ''}</li>)}</ul>
                </details>
              )}
            </section>
          )}
        </>
      )}
    </section>
  );
}

export default function Coach(props: CoachProps) {
  return <CoachSession key={props.mode + ':' + (props.question?.id ?? '')} {...props} />;
}
