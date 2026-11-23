import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { messageOf } from '../lib/questionWorkshop';
import { sanitizeReviewAnswer } from '../pages/BankReviewQueue';
import { draftKey, remember, stored } from './StudyShell';

function readQuestion(value: unknown) {
  if (typeof value === 'object' && value !== null &&
      'id' in value && typeof value.id === 'number' && Number.isSafeInteger(value.id) && value.id > 0 &&
      'short_code' in value && typeof value.short_code === 'string' && value.short_code.trim() &&
      'title' in value && typeof value.title === 'string' && value.title.trim() &&
      'answer_html' in value && typeof value.answer_html === 'string' && value.answer_html.trim()) {
    return { id: value.id, short_code: value.short_code, title: value.title, answer_html: value.answer_html };
  }
  throw new Error('未收到完整的面试题内容');
}

type Status =
  | { kind: 'reading' | 'ready' | 'saving' | 'unconfirmed' }
  | { kind: 'read-error' | 'save-error'; message: string };

export default function StudyInterviewQuestion({ sessionId, canOrganize }: {
  sessionId: number;
  canOrganize: boolean;
}) {
  const path = `/study/sessions/${sessionId}/interview-question`;
  const pendingKey = draftKey(sessionId) + ':interview-question';
  const [question, setQuestion] = useState<ReturnType<typeof readQuestion> | null>(null);
  const [status, setStatus] = useState<Status>({ kind: 'reading' });
  const [readAttempt, setReadAttempt] = useState(0);
  const posting = useRef(false);
  const answer = useMemo(() => question ? sanitizeReviewAnswer(question.answer_html) : '', [question]);

  useEffect(() => {
    let live = true;
    async function restore() {
      try {
        const value = await api.get<unknown>(path);
        if (!live) return;
        const latest = value === null ? null : readQuestion(value);
        const pending = stored(pendingKey);
        const unconfirmed = pending && (!latest || pending.split(':')[0] === String(latest.id));
        if (pending && !unconfirmed) remember(pendingKey, '');
        setQuestion(latest);
        setStatus({ kind: unconfirmed ? 'unconfirmed' : 'ready' });
      } catch (error) {
        if (live) setStatus({ kind: 'read-error', message: messageOf(error) });
      }
    }
    void restore();
    return () => { live = false; };
  }, [path, pendingKey, readAttempt]);

  const checking = status.kind === 'reading';
  const saving = status.kind === 'saving';
  const canRetryRead = status.kind === 'read-error' || status.kind === 'save-error' || status.kind === 'unconfirmed';
  const disabled = !canOrganize || checking || saving || status.kind === 'read-error';

  async function organize() {
    if (disabled || posting.current) return;
    posting.current = true;
    setStatus({ kind: 'saving' });
    const pending = `${question?.id ?? 0}:${crypto.randomUUID()}`;
    remember(pendingKey, pending);
    try {
      const result = readQuestion(await api.post<unknown>(path, {}));
      if (stored(pendingKey) === pending) remember(pendingKey, '');
      setQuestion(result);
      setStatus({ kind: 'ready' });
    } catch (error) {
      setStatus({ kind: 'save-error', message: messageOf(error) });
    } finally {
      posting.current = false;
    }
  }

  function reread() {
    setStatus({ kind: 'reading' });
    setReadAttempt(attempt => attempt + 1);
  }

  return (
    <section className="card coach-interview-question" aria-label="面试题整理">
      <div className="study-actions">
        <button type="button" className="btn btn-outline" onClick={organize} disabled={disabled}>
          {saving ? '正在整理面试题…' : status.kind === 'save-error' || status.kind === 'unconfirmed' ? '重试整理面试题' : '整理成面试题'}
        </button>
        {canRetryRead && <button type="button" className="btn btn-ghost" onClick={reread}>重新读取</button>}
      </div>
      {checking && <p role="status">正在读取已整理的面试题…</p>}
      {saving && <p role="status">正在根据本次对话整理面试题。</p>}
      {status.kind === 'unconfirmed' && <p role="status">上次整理结果尚未确认，可以重新读取或重试。</p>}
      {status.kind === 'read-error' && <p role="alert">读取面试题失败。{status.message}</p>}
      {status.kind === 'save-error' && <p role="alert">整理面试题失败。{status.message}</p>}
      {!canOrganize && !question && status.kind === 'ready' && <p className="hint">收到学习助手的回复后，可以把本次对话整理成面试题。</p>}
      {question && <>
        <p role="status">已保存至我的题目 · {question.short_code}</p>
        <details key={question.id}>
          <summary>{question.title}</summary>
          <div className="bank-answer bank-review-answer" dangerouslySetInnerHTML={{ __html: answer }} />
        </details>
        <Link to="/bank?scope=mine">在我的题目中查看</Link>
      </>}
    </section>
  );
}
