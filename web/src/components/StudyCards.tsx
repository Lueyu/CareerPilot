import { useLocale, useT } from '../i18n';
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
  { status: 'DUE', title: "study.dueForReview", empty: "study.noCardsDue" },
  { status: 'CANDIDATE', title: "study.candidateCards", empty: "study.noCandidateCards" },
  { status: 'ACTIVE', title: "study.activeCards", empty: "study.noActiveCards" },
];
const ratings: { value: Rating; label: string }[] = [
  { value: 'again', label: "study.forgot" },
  { value: 'hard', label: "study.hardRecall" },
  { value: 'good', label: "study.remembered" },
];

function CardRow({ card, queue, busy, run, onChanged }: {
  card: StudyCard;
  queue: Queue;
  busy: boolean;
  run: (action: () => Promise<void>) => Promise<void>;
  onChanged: (id: Id, options?: { remove?: boolean; notice?: string }) => void;
}) {
  const t = useT();

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
    void act("study.cardStatusUpdateFailed", async () => {
      await api.patch(path, { status });
      onChanged(card.id, { remove: true });
    });
  }

  function rate(rating: Rating) {
    void act("study.reviewRatingUnconfirmed", async () => {
      const attempt = review ?? { rating, request_id: crypto.randomUUID() };
      setReview(attempt);
      await api.post(path + '/review', attempt);
      setAnswer(null);
      setReview(null);
      onChanged(card.id, { remove: true, notice: "study.reviewRatingSaved" });
    });
  }

  const locale = useLocale();
  const next = card.next_review_at ? new Date(card.next_review_at) : null;

  return <li className="study-notebook-card">
    <h3 className="study-notebook-text">{card.question}</h3>
    <p className="hint study-notebook-text">
      {card.knowledge_point && <>{t("study.cardKnowledgePoint", { topic: card.knowledge_point })}</>}
      <Link to={'/study/notes/' + encodeURIComponent(card.note_id)}>{t("study.sourceNote")}</Link>{t("study.cardVersion", { version: card.version_id })}{next && !Number.isNaN(next.getTime()) && <>{t("study.nextReviewPrefix")}<time dateTime={next.toISOString()}>{next.toLocaleString(locale)}</time></>}
    </p>
    {error && <p role="alert">{t(error)}</p>}
    {editing ? <form onSubmit={event => {
      event.preventDefault();
      void act("study.cardSaveFailed", async () => {
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
      <label className="study-notebook-field">{t("study.question")}
        <textarea className="inp" required value={question} onChange={event => setQuestion(event.target.value)} disabled={busy} />
      </label>
      <label className="study-notebook-field">{t("study.knowledgePoint")}
        <input className="inp" value={knowledge} onChange={event => setKnowledge(event.target.value)} disabled={busy} />
      </label>
      <label className="study-notebook-field">{t("study.newCardAnswer")}
        <textarea className="inp" value={replacement} onChange={event => setReplacement(event.target.value)} disabled={busy} />
      </label>
      <div className="study-notebook-actions">
        <button className="btn btn-primary btn-sm" disabled={busy || !question.trim()}>{t("study.saveCard")}</button>
        <button type="button" className="btn btn-outline btn-sm" disabled={busy} onClick={() => setEditing(false)}>{t("study.cancelEditing")}</button>
      </div>
    </form> : <>
      {answer !== null && <div className="study-notebook-revision">
        <strong>{t("study.answer")}</strong>
        <p className="study-notebook-text">{answer}</p>
        {queue === 'DUE' && <div className="study-notebook-actions" aria-label={t("study.reviewRating")}>
          {ratings.map(({ value, label }) => <button key={value} type="button" className="btn btn-primary btn-sm"
            disabled={busy || (review !== null && review.rating !== value)}
            onClick={() => rate(value)}>{review?.rating === value ? t("study.resubmitRating", { rating: t(label) }) : t(label)}</button>)}
        </div>}
      </div>}
      <div className="study-notebook-actions">
        {answer === null ? <button type="button" className="btn btn-outline btn-sm" disabled={busy}
          onClick={() => void act("study.revealAnswerFailed", async () => {
            const result = await api.post<{ answer: string }>(path + '/reveal', {});
            setAnswer(result.answer);
          })}>{t("study.revealAnswer")}</button> : <button type="button" className="btn btn-outline btn-sm" disabled={busy || !!review}
          onClick={() => setAnswer(null)}>{t("study.hideAnswer")}</button>}
        {queue === 'CANDIDATE' ? <button type="button" className="btn btn-primary btn-sm" disabled={busy}
          onClick={() => changeStatus('ACTIVE')}>{t("study.activateCard")}</button> :
          <button type="button" className="btn btn-outline btn-sm" disabled={busy || !!review}
            onClick={() => void act("study.loadSourceSessionFailed", async () => {
              const note = await api.get<{ session_ids: Id[] }>('/study/notes/' + encodeURIComponent(card.note_id));
              const session = note.session_ids[0];
              if (session === undefined) {
                setError("study.noteHasNoSession");
                return;
              }
              navigate('/study/chat/' + encodeURIComponent(session) + '?card=' + encodeURIComponent(card.id) + '&practice=quiz');
            })}>{t("study.activePractice")}</button>}
        <button type="button" className="btn btn-outline btn-sm" disabled={busy || !!review} onClick={startEditing}>{t("study.editCard")}</button>
        <button type="button" className="btn btn-outline btn-sm" disabled={busy || !!review}
          onClick={() => changeStatus('SKIPPED')}>{t("study.skipCard")}</button>
        <button type="button" className="btn btn-outline btn-sm" disabled={busy || !!review}
          onClick={() => {
            if (!window.confirm(t("study.confirmDeleteCard"))) return;
            void act("study.cardDeleteFailed", async () => {
              await api.del(path);
              onChanged(card.id, { remove: true });
            });
          }}>{t("study.deleteCard")}</button>
      </div>
    </>}
  </li>;
}

type QueueData = { cards: StudyCard[]; loading: boolean; error: boolean };
const emptyQueue: QueueData = { cards: [], loading: true, error: false };

export function StudyCards({ generated }: { generated: StudyCard[] }) {
  const t = useT();

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

  function changed(id: Id, { remove, notice = "study.cardUpdated" }: { remove?: boolean; notice?: string } = {}) {
    setNotice(notice);
    if (remove) {
      setLists(current => {
        const locale = useLocale();
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
    {notice && <p role="status">{t(notice)}</p>}
    {queues.map(({ status, title, empty }) => {
      const list = lists[status];
      return <section className="card study-notebook-panel" key={status} aria-labelledby={'study-cards-' + status}>
        <div className="study-notebook-actions">
          <h2 id={'study-cards-' + status}>{t(title)}</h2>
          <button type="button" className="btn btn-outline btn-sm" disabled={list.loading || working}
            onClick={() => void load(status)}>{t("study.refresh")}</button>
        </div>
        {list.loading && <p role="status">{t("study.loadingCards")}</p>}
        {list.error && <p role="alert">{t("study.loadCardsFailed")}</p>}
        {!list.loading && !list.error && !list.cards.length && <p className="hint">{t(empty)}</p>}
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
