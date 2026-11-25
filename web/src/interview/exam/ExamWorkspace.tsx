import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, Navigate, useLocation, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../../api/client';
import { ExamConfiguration } from './ExamConfiguration';
import { ExamReport } from './ExamReport';
import { TimedExam } from './TimedExam';
import { chatHref, errorMessage, examHref, positiveId, reportStatuses, useCopy, type Attempt } from './examState';
import './exam.css';

export { ExamConfiguration } from './ExamConfiguration';

type AttemptSummary = {
  id: number; title: string; status: string; origin_session_id?: number | null; interview_agent?: boolean;
};

function ConfigurationPage({ sessionId, topic }: { sessionId?: number; topic?: string }) {
  const copy = useCopy();
  const [attempts, setAttempts] = useState<AttemptSummary[]>();
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let ignore = false;
    api.get<{ history: AttemptSummary[] }>('/exams').then(
      data => {
        if (ignore) return;
        setAttempts(data.history.filter(attempt => attempt.interview_agent &&
          (!sessionId || attempt.origin_session_id === sessionId)).slice(0, 8));
        setError('');
      },
      error => { if (!ignore) setError(errorMessage(error)); },
    );
    return () => { ignore = true; };
  }, [sessionId, retry]);
  return <main className="ia-exam-page ia-exam-setup-page">
    <h1>{copy('模拟考试', 'Mock exam')}</h1>
    <ExamConfiguration sessionId={sessionId} topic={topic} />
    {(attempts?.length || error) ? <details className="ia-exam-card ia-exam-recent" open>
      <summary>{copy('最近的组卷记录', 'Recent papers')}</summary>
      {error && <p role="alert">{error} <button type="button" className="ia-exam-text-button"
        onClick={() => setRetry(value => value + 1)}>{copy('重试', 'Retry')}</button></p>}
      <ul>{attempts?.map(attempt => <li key={attempt.id}>
        <span>{attempt.title} · #{attempt.id}</span>
        <Link className="ia-exam-text-button" to={examHref(attempt.id, attempt.status !== 'ACTIVE')}>
          {attempt.status === 'ACTIVE' ? copy('打开答卷', 'Open paper') : copy('查看结果', 'View result')}
        </Link>
      </li>)}</ul>
    </details> : null}
  </main>;
}

type LoadState = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; attempt: Attempt; receivedAt: number };

function AttemptPage({ id, reportRoute, session }: { id: number; reportRoute: boolean; session?: number }) {
  const copy = useCopy();
  const [load, setLoad] = useState<LoadState>({ kind: 'loading' });
  const [refreshError, setRefreshError] = useState('');
  const request = useRef(0);
  const refresh = useCallback(async () => {
    const latest = ++request.current;
    try {
      const attempt = await api.get<Attempt>(`/exams/${id}`);
      if (latest !== request.current) return;
      setLoad({ kind: 'ready', attempt, receivedAt: performance.now() });
      setRefreshError('');
      return attempt;
    } catch (failure) {
      if (latest !== request.current) return;
      const message = errorMessage(failure);
      setLoad(previous => previous.kind === 'ready' ? previous : { kind: 'error', message });
      setRefreshError(message);
    }
  }, [id]);

  useEffect(() => {
    void refresh();
    return () => { request.current++; };
  }, [refresh, reportRoute]);

  if (load.kind === 'loading') return <main className="ia-exam-page"><p role="status">{copy('正在读取答卷…', 'Loading paper…')}</p></main>;
  if (load.kind === 'error') return <main className="ia-exam-page"><section className="ia-exam-card">
    <h1>{copy('无法打开答卷', 'The paper is unavailable')}</h1>
    <p role="alert">{load.message}</p>
    <div className="ia-exam-actions">
      <button type="button" className="ia-exam-button ia-exam-primary" onClick={() => void refresh()}>{copy('重试', 'Retry')}</button>
      <Link className="ia-exam-button" to={examHref(undefined, false, session)}>{copy('返回组卷', 'Back to exam setup')}</Link>
    </div>
  </section></main>;

  const { attempt, receivedAt } = load;
  const routeSession = attempt.interview_agent ? undefined : session;
  if (attempt.status === 'ACTIVE') {
    if (reportRoute && !refreshError) {
      // A direct visit to the report route for an active paper resumes the attempt.
      return <Navigate replace to={examHref(id, false, routeSession)} />;
    }
    return <>
      {refreshError && <p className="ia-exam-refresh-error" role="alert">{refreshError}</p>}
      <TimedExam attempt={attempt} receivedAt={receivedAt} refresh={refresh} routeSession={routeSession} />
    </>;
  }
  if (!reportRoute) return <Navigate replace to={examHref(id, true, routeSession)} />;
  if (reportStatuses.has(attempt.status)) return <ExamReport attempt={attempt} routeSession={routeSession} />;

  const expired = attempt.status === 'EXPIRED';
  return <main className="ia-exam-page"><section className="ia-exam-card">
    <h1>{expired ? copy('考试时间已到', 'Time limit reached') : copy('本次作答已关闭', 'This attempt is closed')}</h1>
    <p>{expired
      ? copy('服务器保留了到期前已同步的答案。未交卷的答卷没有评分报告。', 'Answers synced before the deadline are saved. There is no report for an unsubmitted paper.')
      : copy('当前答卷没有可查看的评分报告。', 'There is no scoring report for this attempt.')}</p>
    <div className="ia-exam-actions">
      <Link className="ia-exam-button ia-exam-primary" to={examHref(undefined, false, positiveId(attempt.origin_session_id) ?? routeSession)}>{copy('重新组卷', 'Configure another paper')}</Link>
      <Link className="ia-exam-button" to={chatHref(attempt.origin_session_id ?? routeSession)}>{copy('返回对话', 'Return to chat')}</Link>
    </div>
  </section></main>;
}

export default function ExamWorkspace() {
  const { attemptId } = useParams();
  const { pathname } = useLocation();
  const [search] = useSearchParams();
  const sessionId = positiveId(search.get('session'));
  const topic = search.get('topic') ?? undefined;
  const id = positiveId(attemptId);
  return <div className="ia-exam ia-exam-workspace">
    {attemptId && !id ? <Navigate replace to={examHref(undefined, false, sessionId)} />
      : id ? <AttemptPage key={`${id}${/\/report\/?$/.test(pathname) ? '/report' : ''}`} id={id}
          reportRoute={/\/report\/?$/.test(pathname)} session={sessionId} />
        : <ConfigurationPage key={sessionId + ':' + topic} sessionId={sessionId} topic={topic} />}
  </div>;
}
