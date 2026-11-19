import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api/client';

type Id = string | number;
type Queue = 'CANDIDATE' | 'ACTIVE' | 'DUE';
type Rating = 'again' | 'hard' | 'good';
type Review = { rating: Rating; request_id: string };
export type StudyCard = {
  id: Id;
  note_id: Id;
  version_id: Id;
  question: string;
  knowledge_point: string;
  status: string;
  next_review_at: string | null;
};

const queues: { status: Queue; title: string; empty: string }[] = [
  { status: 'DUE', title: '到期复习', empty: '目前没有到期卡片。' },
  { status: 'CANDIDATE', title: '候选卡片', empty: '目前没有待确认的卡片。' },
  { status: 'ACTIVE', title: '已启用卡片', empty: '目前没有已启用的卡片。' },
];
const ratings: { value: Rating; label: string }[] = [
  { value: 'again', label: '忘记了' },
  { value: 'hard', label: '有点难' },
  { value: 'good', label: '记住了' },
];

function CardRow({ card, queue, busy, run, onChanged }: {
  card: StudyCard;
  queue: Queue;
  busy: boolean;
  run: (action: () => Promise<void>) => Promise<void>;
  onChanged: (id: Id, options?: { remove?: boolean; notice?: string }) => void;
}) {
  const [answer, setAnswer] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [question, setQuestion] = useState(card.question);
  const [knowledge, setKnowledge] = useState(card.knowledge_point);
  const [replacement, setReplacement] = useState('');
  const navigate = useNavigate();
  const [error, setError] = useState('');
  const [review, setReview] = useState<Review | null>(null);
  const path = '/study/cards/' + encodeURIComponent(card.id);

  async function act(message: string, action: () => Promise<void>) {
    await run(async () => {
      setError('');
      try {
        await action();
      } catch {
        setError(message);
      }
    });
  }

  function startEditing() {
    setQuestion(card.question);
    setKnowledge(card.knowledge_point);
    setReplacement('');
    setEditing(true);
  }

  function changeStatus(status: 'ACTIVE' | 'SKIPPED') {
    void act('更新卡片状态失败，请重试。', async () => {
      await api.patch(path, { status });
      onChanged(card.id, { remove: true });
    });
  }

  function rate(rating: Rating) {
    void act('无法确认评分结果，请重试提交同一评分。', async () => {
      const attempt = review ?? { rating, request_id: crypto.randomUUID() };
      setReview(attempt);
      await api.post(path + '/review', attempt);
      setAnswer(null);
      setReview(null);
      onChanged(card.id, { remove: true, notice: '评分已提交，复习时间已更新。' });
    });
  }

  const next = card.next_review_at ? new Date(card.next_review_at) : null;

  return <li className="study-notebook-card">
    <h3 className="study-notebook-text">{card.question}</h3>
    <p className="hint study-notebook-text">
      {card.knowledge_point && <>知识点 {card.knowledge_point} · </>}
      <Link to={'/study/notes/' + encodeURIComponent(card.note_id)}>来源笔记</Link> · 版本 {card.version_id}
      {next && !Number.isNaN(next.getTime()) && <> · 下次复习 <time dateTime={next.toISOString()}>{next.toLocaleString('zh-CN')}</time></>}
    </p>
    {error && <p role="alert">{error}</p>}
    {editing ? <form onSubmit={event => {
      event.preventDefault();
      void act('保存卡片失败，请重试。', async () => {
        await api.patch(path, {
          question,
          knowledge_point: knowledge,
          ...(replacement.trim() ? { answer: replacement } : {}),
        });
        setEditing(false);
        setAnswer(null);
        onChanged(card.id);
      });
    }}>
      <label className="study-notebook-field">问题
        <textarea className="inp" required value={question} onChange={event => setQuestion(event.target.value)} disabled={busy} />
      </label>
      <label className="study-notebook-field">知识点
        <input className="inp" value={knowledge} onChange={event => setKnowledge(event.target.value)} disabled={busy} />
      </label>
      <label className="study-notebook-field">新答案（留空保留原答案）
        <textarea className="inp" value={replacement} onChange={event => setReplacement(event.target.value)} disabled={busy} />
      </label>
      <div className="study-notebook-actions">
        <button className="btn btn-primary btn-sm" disabled={busy || !question.trim()}>保存卡片</button>
        <button type="button" className="btn btn-outline btn-sm" disabled={busy} onClick={() => setEditing(false)}>取消编辑</button>
      </div>
    </form> : <>
      {answer !== null && <div className="study-notebook-revision">
        <strong>答案</strong>
        <p className="study-notebook-text">{answer}</p>
        {queue === 'DUE' && <div className="study-notebook-actions" aria-label="复习评分">
          {ratings.map(({ value, label }) => <button key={value} type="button" className="btn btn-primary btn-sm"
            disabled={busy || (review !== null && review.rating !== value)}
            onClick={() => rate(value)}>{review?.rating === value ? '重试提交 · ' + label : label}</button>)}
        </div>}
      </div>}
      <div className="study-notebook-actions">
        {answer === null ? <button type="button" className="btn btn-outline btn-sm" disabled={busy}
          onClick={() => void act('揭晓答案失败，请重试。', async () => {
            const result = await api.post<{ answer: string }>(path + '/reveal', {});
            setAnswer(result.answer);
          })}>揭晓答案</button> : <button type="button" className="btn btn-outline btn-sm" disabled={busy || !!review}
          onClick={() => setAnswer(null)}>隐藏答案</button>}
        {queue === 'CANDIDATE' ? <button type="button" className="btn btn-primary btn-sm" disabled={busy}
          onClick={() => changeStatus('ACTIVE')}>启用卡片</button> :
          <button type="button" className="btn btn-outline btn-sm" disabled={busy || !!review}
            onClick={() => void act('读取来源会话失败，请重试。', async () => {
              const note = await api.get<{ session_ids: Id[] }>('/study/notes/' + encodeURIComponent(card.note_id));
              const session = note.session_ids[0];
              if (session === undefined) {
                setError('来源笔记没有关联的学习会话。');
                return;
              }
              navigate('/study/chat/' + encodeURIComponent(session) + '?card=' + encodeURIComponent(card.id) + '&practice=quiz');
            })}>主动练习</button>}
        <button type="button" className="btn btn-outline btn-sm" disabled={busy || !!review} onClick={startEditing}>编辑卡片</button>
        <button type="button" className="btn btn-outline btn-sm" disabled={busy || !!review}
          onClick={() => changeStatus('SKIPPED')}>跳过卡片</button>
        <button type="button" className="btn btn-outline btn-sm" disabled={busy || !!review}
          onClick={() => {
            if (!window.confirm('确定删除这张卡片？')) return;
            void act('删除卡片失败，请重试。', async () => {
              await api.del(path);
              onChanged(card.id, { remove: true });
            });
          }}>删除卡片</button>
      </div>
    </>}
  </li>;
}

type QueueData = { cards: StudyCard[]; loading: boolean; error: boolean };
const emptyQueue: QueueData = { cards: [], loading: true, error: false };

export function StudyCards({ generated }: { generated: StudyCard[] }) {
  const [lists, setLists] = useState<Record<Queue, QueueData>>({ DUE: emptyQueue, CANDIDATE: emptyQueue, ACTIVE: emptyQueue });
  const [working, setWorking] = useState(false);
  const [notice, setNotice] = useState('');
  const inFlight = useRef(false);
  const generations = useRef({ DUE: 0, CANDIDATE: 0, ACTIVE: 0 });

  const load = useCallback(async (status: Queue, additions: StudyCard[] = []) => {
    const request = ++generations.current[status];
    setLists(current => ({ ...current, [status]: { ...current[status], loading: true, error: false } }));
    try {
      const result = await api.get<{ items: StudyCard[] }>('/study/cards?status=' + status);
      if (request === generations.current[status]) {
        const byId = new Map(additions.map(card => [String(card.id), card]));
        for (const card of result.items) byId.set(String(card.id), card);
        setLists(current => ({ ...current, [status]: { cards: [...byId.values()], loading: false, error: false } }));
      }
    } catch {
      if (request === generations.current[status]) {
        setLists(current => ({ ...current, [status]: { ...current[status], loading: false, error: true } }));
      }
    }
  }, []);

  useEffect(() => { queues.forEach(({ status }) => void load(status)); }, [load]);

  useEffect(() => {
    const candidates = generated.filter(card => card.status === 'CANDIDATE');
    if (!candidates.length) return;
    setLists(current => {
      const byId = new Map(current.CANDIDATE.cards.map(card => [String(card.id), card]));
      for (const card of candidates) byId.set(String(card.id), card);
      return { ...current, CANDIDATE: { ...current.CANDIDATE, cards: [...byId.values()], loading: false } };
    });
    void load('CANDIDATE', candidates);
  }, [generated, load]);

  function changed(id: Id, { remove, notice = '卡片已更新。' }: { remove?: boolean; notice?: string } = {}) {
    setNotice(notice);
    if (remove) {
      setLists(current => {
        const next = { ...current };
        for (const { status } of queues) {
          next[status] = { ...current[status], cards: current[status].cards.filter(card => String(card.id) !== String(id)) };
        }
        return next;
      });
    }
    queues.forEach(({ status }) => void load(status));
  }

  return <div className="study-notebook-queues">
    {notice && <p role="status">{notice}</p>}
    {queues.map(({ status, title, empty }) => {
      const list = lists[status];
      return <section className="card study-notebook-panel" key={status} aria-labelledby={'study-cards-' + status}>
        <div className="study-notebook-actions">
          <h2 id={'study-cards-' + status}>{title}</h2>
          <button type="button" className="btn btn-outline btn-sm" disabled={list.loading || working}
            onClick={() => void load(status)}>刷新</button>
        </div>
        {list.loading && <p role="status">正在读取卡片…</p>}
        {list.error && <p role="alert">读取卡片失败，请刷新重试。</p>}
        {!list.loading && !list.error && !list.cards.length && <p className="hint">{empty}</p>}
        <ul className="study-notebook-list">{list.cards.map(card => (
          <CardRow key={card.id} card={card} queue={status} busy={working}
            onChanged={changed}
            run={async action => {
              if (inFlight.current) return;
              inFlight.current = true;
              setWorking(true);
              setNotice('');
              try {
                await action();
              } finally {
                inFlight.current = false;
                setWorking(false);
              }
            }} />
        ))}</ul>
      </section>;
    })}
  </div>;
}
