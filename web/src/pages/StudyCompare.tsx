import { useT } from '../i18n';
import { useEffect, useRef, useState, type ComponentProps } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import StudyText from '../components/StudyText';

type Id = string | number;
type Message = { id: Id; role: string; content: string; status: string; blocks?: unknown[]; sources?: ComponentProps<typeof StudyText>['sources'] };
type Session = { title: string; messages: Message[] };

export function comparePath(noteId: Id, sessionId: Id, messageId?: Id) {
  const query = new URLSearchParams({ view: 'compare', session: String(sessionId) });
  if (messageId !== undefined) query.set('message', String(messageId));
  return `/study/notes/${encodeURIComponent(noteId)}?${query}`;
}

export default function StudyCompare({ sessionId, messageId, noteId }: { sessionId: Id; messageId?: Id; noteId: Id }) {
  const t = useT();

  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const transcript = useRef<HTMLDivElement>(null);
  const target = useRef<HTMLElement>(null);
  const chat = `/study/chat/${encodeURIComponent(sessionId)}`;
  const focused = session?.messages.some(message => String(message.id) === String(messageId));

  useEffect(() => {
    let active = true;
    setError(false);
    api.get<Session>(`/study/sessions/${encodeURIComponent(sessionId)}`)
      .then(result => { if (active) setSession(result); })
      .catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, [sessionId, retry]);

  useEffect(() => {
    if (transcript.current) transcript.current.scrollTop = target.current ? target.current.offsetTop - 12 : 0;
  }, [session, messageId]);

  return <section className="study-notebook-panel study-notebook-paper study-notebook-conversation" id="study-conversation" aria-labelledby="study-conversation-title">
    <div className="study-notebook-actions">
      <h2 id="study-conversation-title">{t("study.sourceSession")}</h2>
      {session && <Link className="btn btn-outline btn-sm" to={focused && messageId !== undefined ? `${chat}?message=${encodeURIComponent(messageId)}` : chat}>{t("study.openOriginalSession")}</Link>}
    </div>
    {error ? <p role="alert">{t("study.sourceSessionUnavailable")}<button className="btn btn-outline btn-sm" onClick={() => setRetry(value => value + 1)}>{t("study.retry")}</button></p>
      : !session ? <p role="status">{t("study.loadingSourceSession")}</p> : <>
      <p className="study-notebook-conversation-title">{session.title}</p>
      {messageId !== undefined && !focused && <p role="status">{t("study.messageNotInSession")}</p>}
      <div className="study-notebook-transcript" ref={transcript} tabIndex={0} role="region" aria-label={t("study.sessionMessages")}>
        {!session.messages.length && <p>{t("study.emptySession")}</p>}
        {session.messages.map(message => {
          const selected = String(message.id) === String(messageId);
          const speaker = message.role === 'assistant' ? t("study.studyAssistant") : message.role === 'user' ? t("study.me") : t("study.system");
          return <article className={'study-notebook-turn' + (selected ? ' is-focused' : '')} ref={selected ? target : undefined} key={message.id}>
            <div className="study-notebook-turn-head"><strong>{speaker}</strong>
              <Link to={comparePath(noteId, sessionId, message.id)} aria-current={selected ? 'location' : undefined}>{selected ? t("study.currentMessage") : t("study.jumpToMessage")}</Link>
              <Link to={`${chat}?message=${encodeURIComponent(message.id)}`}>{t("study.originalMessage")}</Link>
            </div>
            {message.content ? <StudyText text={message.content} blocks={message.blocks} sources={message.sources} streaming={message.status === 'generating'} /> : <p>{t("study.noMessageContent")}</p>}
          </article>;
        })}
      </div>
    </>}
  </section>;
}
