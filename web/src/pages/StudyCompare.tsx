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
      <h2 id="study-conversation-title">来源会话</h2>
      {session && <Link className="btn btn-outline btn-sm" to={focused && messageId !== undefined ? `${chat}?message=${encodeURIComponent(messageId)}` : chat}>打开原会话 ↗</Link>}
    </div>
    {error ? <p role="alert">暂时无法读取来源会话。 <button className="btn btn-outline btn-sm" onClick={() => setRetry(value => value + 1)}>重试</button></p>
      : !session ? <p role="status">正在读取来源会话…</p> : <>
      <p className="study-notebook-conversation-title">{session.title}</p>
      {messageId !== undefined && !focused && <p role="status">这条消息不在当前会话中。</p>}
      <div className="study-notebook-transcript" ref={transcript} tabIndex={0} role="region" aria-label="会话消息">
        {!session.messages.length && <p>此会话还没有消息。</p>}
        {session.messages.map(message => {
          const selected = String(message.id) === String(messageId);
          const speaker = message.role === 'assistant' ? '学习助手' : message.role === 'user' ? '我' : '系统';
          return <article className={'study-notebook-turn' + (selected ? ' is-focused' : '')} ref={selected ? target : undefined} key={message.id}>
            <div className="study-notebook-turn-head"><strong>{speaker}</strong>
              <Link to={comparePath(noteId, sessionId, message.id)} aria-current={selected ? 'location' : undefined}>{selected ? '当前定位' : '定位此消息'}</Link>
              <Link to={`${chat}?message=${encodeURIComponent(message.id)}`}>原消息 ↗</Link>
            </div>
            {message.content ? <StudyText text={message.content} blocks={message.blocks} sources={message.sources} streaming={message.status === 'generating'} /> : <p>暂无消息正文。</p>}
          </article>;
        })}
      </div>
    </>}
  </section>;
}
