import { t, useT } from '../i18n';
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
  throw new Error(t("study.incompleteOrganizedQuestion"));
}

type Status =
  | { kind: 'reading' | 'ready' | 'saving' | 'unconfirmed' }
  | { kind: 'read-error' | 'save-error'; message: string };

export default function StudyInterviewQuestion({ sessionId, canOrganize }: {
  sessionId: number;
  canOrganize: boolean;
}) {
  const t = useT();

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
    <section className="card coach-interview-question" aria-label={t("study.organizeInterviewQuestion")}>
      <div className="study-actions">
        <button type="button" className="btn btn-outline" onClick={organize} disabled={disabled}>
          {saving ? t("study.organizingInterviewQuestion") : status.kind === 'save-error' || status.kind === 'unconfirmed' ? t("study.retryInterviewQuestion") : t("study.turnIntoInterviewQuestion")}
        </button>
        {canRetryRead && <button type="button" className="btn btn-ghost" onClick={reread}>{t("study.reloadQuestion")}</button>}
      </div>
      {checking && <p role="status">{t("study.loadingOrganizedQuestion")}</p>}
      {saving && <p role="status">{t("study.organizingFromConversation")}</p>}
      {status.kind === 'unconfirmed' && <p role="status">{t("study.organizedQuestionUnconfirmed")}</p>}
      {status.kind === 'read-error' && <p role="alert">{t("study.loadOrganizedQuestionFailed", { message: status.message })}</p>}
      {status.kind === 'save-error' && <p role="alert">{t("study.organizeInterviewQuestionFailed", { message: status.message })}</p>}
      {!canOrganize && !question && status.kind === 'ready' && <p className="hint">{t("study.needAssistantReplyToOrganize")}</p>}
      {question && <>
        <p role="status">{t("study.savedToMyQuestions", { shortCode: question.short_code })}</p>
        <details key={question.id}>
          <summary>{question.title}</summary>
          <div className="bank-answer bank-review-answer" dangerouslySetInnerHTML={{ __html: answer }} />
        </details>
        <Link to="/bank?scope=mine">{t("study.viewInMyQuestions")}</Link>
      </>}
    </section>
  );
}
