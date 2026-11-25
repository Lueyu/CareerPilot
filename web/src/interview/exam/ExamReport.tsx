import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import { sanitizeReviewAnswer } from '../../pages/BankReviewQueue';
import { answered, chatHref, durationLabel, errorMessage, pendingStatuses, useCopy, type Attempt, type Report } from './examState';

const back = new URL('./assets/back.svg', import.meta.url).href;
const clipboard = new URL('./assets/clipboard.svg', import.meta.url).href;
const chevron = new URL('./assets/chevron.svg', import.meta.url).href;
const file = new URL('./assets/file.svg', import.meta.url).href;
const close = new URL('./assets/close.svg', import.meta.url).href;

type ReportQuestion = Report['questions'][number];

function submittedAnswer(question: ReportQuestion) {
  return question.qtype === 'mcq' ? question.answer?.choice_index : question.answer?.text_answer;
}

function verdictOf(question: ReportQuestion) {
  return answered(submittedAnswer(question)) ? (question.answer?.verdict ?? 'UNKNOWN') : 'UNANSWERED';
}

function verdictLabel(verdict: string, copy: ReturnType<typeof useCopy>) {
  switch (verdict) {
    case 'CORRECT': return copy('正确', 'Correct');
    case 'PARTIAL': return copy('部分得分', 'Partial credit');
    case 'WRONG': return copy('需改进', 'Needs improvement');
    case 'UNANSWERED': return copy('未作答', 'Unanswered');
    default: return copy('未评分', 'Unscored');
  }
}

function storedReference(html: string) {
  // Reference content is displayed as evidence inside the workspace.
  return sanitizeReviewAnswer(html).replace(/<a\b[^>]*>/gi, '<span>').replace(/<\/a\s*>/gi, '</span>');
}

function AnswerDetails({ question, title, observation, onClose }: {
  question: ReportQuestion; title: string; observation?: NonNullable<Report['observations']>[number]; onClose: () => void;
}) {
  const copy = useCopy();
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  const raw = submittedAnswer(question);
  const original = typeof raw === 'number' ? question.options?.[raw] : raw;
  const correct = question.correct_choice_index == null ? undefined : question.options?.[question.correct_choice_index];
  const feedback = question.answer?.feedback;
  const advice = feedback?.judge_reasoning || feedback?.advice;
  const provenance = question.provenance;
  const project = provenance?.interview_reference;
  const lists = [
    { label: copy('已覆盖', 'Covered'), items: feedback?.hit_points },
    { label: copy('待改进', 'Missed'), items: feedback?.missed_points },
  ];
  return <dialog ref={dialog} onCancel={onClose} className="ia-exam ia-exam-drawer" aria-labelledby="ia-exam-evidence-title">
    <header className="ia-exam-card-heading">
      <img src={file} alt="" className="ia-exam-icon-tile" />
      <div><h2 id="ia-exam-evidence-title">{copy('评分依据与标准答案', 'Scoring evidence and reference answer')}</h2>
        <p>{title} · {copy(`第 ${question.order_no} 题`, `Question ${question.order_no}`)}</p>
      </div>
      <button autoFocus className="ia-exam-button ia-exam-icon-button ia-exam-drawer-close" type="button" onClick={onClose}
        aria-label={copy('关闭评分依据', 'Close evidence')}><img src={close} alt="" /></button>
    </header>
    <section><h3>{copy('我的原答', 'My original answer')}</h3>
      {question.answer_format === 'code' && original?.trim()
        ? <pre className="ia-exam-original ia-exam-code">{original}</pre>
        : <p className="ia-exam-original">{original?.trim() ? original : copy('本题未作答。', 'This question was unanswered.')}</p>}
    </section>
    <section><h3>{copy('标准答案对比', 'Reference answer')}</h3>
      {project?.project_name && <p>{copy('面经来源项目', 'Interview source project')} · {project.project_name}</p>}
      {correct && <p>{copy('正确选项', 'Correct option')} · {correct}</p>}
      {question.reference_answer
        ? <div className="ia-exam-answer-html" dangerouslySetInnerHTML={{ __html: storedReference(question.reference_answer) }} />
        : !correct && <p>{copy('此题暂无已保存的参考答案。', 'This question has no saved reference answer.')}</p>}
    </section>
    <section><h3>{copy('评分依据', 'Scoring evidence')}</h3>
      <p>{verdictLabel(verdictOf(question), copy)}
        {question.answer?.points != null && <> · {question.answer.points} / {question.answer.max_points}</>}
        {question.answer?.judge_version && <> · {question.answer.judge_version}</>}
      </p>
      {advice && <p>{advice}</p>}
      {lists.map(({ label, items }) => Boolean(items?.length) && <div key={label}>
        <h4>{label}</h4><ul>{items?.map((point, index) => <li key={index}>{typeof point === 'string' ? point : point.title}</li>)}</ul>
      </div>)}
      {feedback?.criterion_scores?.map(criterion => <details key={criterion.criterion_id}>
        <summary>{criterion.title || criterion.criterion_id} · {criterion.score} / {criterion.max_score}</summary>
        {criterion.reasoning_summary && <p>{criterion.reasoning_summary}</p>}
        {criterion.why_not_higher && <p>{criterion.why_not_higher}</p>}
        {criterion.answer_quotes?.map((quote, index) => <blockquote key={index}>{quote}</blockquote>)}
      </details>)}
      {observation?.independent === false && <p>{copy('此作答未计为独立验证。', 'This answer did not qualify as independent evidence.')}</p>}
      {observation?.evidence_interpretation && <p>{observation.evidence_interpretation}</p>}
      {!!question.answer?.evidence_refs?.length && <p>{copy('证据编号', 'Evidence references')} · {question.answer.evidence_refs.join(' · ')}</p>}
      {provenance?.question_version && <details>
        <summary>{copy('冻结版本', 'Frozen versions')}</summary>
        <p>{copy('题目', 'Question')} · <code>{question.short_code}</code> · <code>{provenance.question_version}</code></p>
        {provenance.answer_revision != null && <p>{copy('原答修订号', 'Answer revision')} · {provenance.answer_revision}</p>}
        {provenance.rubric_version && <p>{copy('评分标准', 'Rubric')} · <code>{provenance.rubric_version}</code></p>}
        {project?.source_item_id != null && <p>{copy('面经条目', 'Interview item')} · #{project.source_item_id}</p>}
      </details>}
    </section>
  </dialog>;
}

export function ExamReport({ attempt, routeSession }: { attempt: Attempt; routeSession?: number }) {
  const copy = useCopy();
  const [report, setReport] = useState<Report>();
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [openOrder, setOpenOrder] = useState<number>();
  const [rescoring, setRescoring] = useState(false);
  const action = useRef(false);
  const status = report?.status ?? attempt.status;
  const origin = report?.origin_session_id ?? attempt.origin_session_id ?? routeSession;
  const loading = !report && !error;
  const practice = (report?.kind ?? attempt.kind) === 'practice' || Boolean(attempt.interview_set_id);

  useEffect(() => {
    let ignore = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const result = await api.get<Report>(`/exams/${attempt.id}/report`);
        if (ignore) return;
        if (result.id !== attempt.id || !Array.isArray(result.questions)) {
          throw new Error(copy('此答卷没有可在这里显示的考试报告。', 'This attempt has no report available in the exam workspace.'));
        }
        setReport(result);
        setError('');
        if (pendingStatuses.has(result.status)) timer = setTimeout(load, 1800);
      } catch (failure) {
        if (!ignore) setError(errorMessage(failure));
      }
    };
    void load();
    return () => { ignore = true; clearTimeout(timer); };
  }, [attempt.id, copy, revision]);

  const rescore = async () => {
    if (action.current) return;
    action.current = true;
    setRescoring(true);
    setError('');
    try {
      await api.post(`/exams/${attempt.id}/rescore`);
      setRevision(value => value + 1);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      action.current = false;
      setRescoring(false);
    }
  };

  const question = report?.questions.find(item => item.order_no === openOrder);
  const scores = report?.questions.map(verdictOf) ?? [];
  const statusLabel = status === 'COMPLETED' ? copy('已完成', 'Completed')
    : pendingStatuses.has(status) ? copy('评分中', 'Scoring')
      : copy('待处理', 'Needs attention');
  const elapsed = (Date.parse(report?.submitted_at ?? '') - Date.parse(report?.started_at ?? '')) / 1000;
  const score = report?.score;

  return <>
    <header className="ia-exam-topbar ia-exam-report-topbar">
      <Link className="ia-exam-button" to={chatHref(origin)}><img src={back} alt="" />{copy('回到聊天', 'Back to chat')}</Link>
      <h1>{copy('考后报告', 'Exam report')}</h1>
      <span className={`ia-exam-badge ${status === 'COMPLETED' ? 'ia-exam-success' : 'ia-exam-warn'}`}>{statusLabel}</span>
    </header>
    <main className="ia-exam-report">
      <section className="ia-exam-card">
        <div className="ia-exam-card-heading">
          <img className="ia-exam-icon-tile" src={clipboard} alt="" />
          <h2>{report?.title ?? attempt.title}</h2>
          {report?.completed_at && <span>{new Date(report.completed_at).toLocaleDateString()}
            {Number.isFinite(elapsed) && elapsed >= 0 && <> · {copy('用时', 'Time spent')} {durationLabel(elapsed)}</>}</span>}
        </div>
        {loading && <p role="status">{copy('正在读取评分状态…', 'Loading scoring status…')}</p>}
        {error && <p role="alert" className="ia-exam-error">{error}{' '}
          <button type="button" className="ia-exam-text-button" onClick={() => setRevision(value => value + 1)}>{copy('刷新报告', 'Refresh report')}</button>
        </p>}
        {report && pendingStatuses.has(status) && <div role="status">
          <p>{status === 'SCORING_RETRY_WAIT' ? copy('评分暂时受阻，系统将重试。原答已锁定。', 'Scoring is temporarily delayed and will retry. Your answers are locked.')
            : copy('原答已锁定，正在等待后台评分。', 'Your answers are locked and await background scoring.')}</p>
          {report.scoring_task && <progress value={report.scoring_task.completed_count} max={report.scoring_task.total_count || 1}
            aria-label={copy('评分进度', 'Scoring progress')} />}
        </div>}
        {report && (status === 'SCORING_FAILED' || status === 'NEEDS_REVIEW') && <div>
          <p role="status">{report.scoring_task?.error?.message || copy('报告尚未生成，原答仍然保留。', 'The report is unavailable. Your submitted answers are still saved.')}</p>
          {report.scoring_task?.error?.code === 'NOT_CONFIGURED' &&
            <p>{copy('简答评分需要在设置中配置可用模型。', 'Written-answer scoring requires a working model in Settings.')}</p>}
          {report.scoring_task?.can_retry !== false &&
            <button className="ia-exam-button ia-exam-primary" type="button" disabled={rescoring} onClick={() => void rescore()}>
              {rescoring ? copy('正在重试…', 'Retrying…') : copy('重试评分', 'Retry scoring')}
            </button>}
        </div>}
        {report && status === 'COMPLETED' && <>
          <div className="ia-exam-overview">
            <div className="ia-exam-score-ring" style={{ background: score == null ? undefined : `conic-gradient(var(--exam-ring) ${Math.max(0, Math.min(score, 100))}%, var(--exam-track) 0)` }}>
              <span>{score == null ? '—' : `${score}%`}</span>
            </div>
            <div>
              <strong>{score == null ? copy('暂无总分', 'No overall score') : `${score} / 100`}</strong>
              <p>{['CORRECT', 'PARTIAL', 'WRONG', 'UNANSWERED'].map(verdict =>
                `${verdictLabel(verdict, copy)} ${scores.filter(value => value === verdict).length}`).join(' · ')}</p>
              {report.overall?.priority && <span className="ia-exam-badge ia-exam-warn">
                {copy('优先练习', 'Practice next')} · {report.overall.priority.skill_name}
              </span>}
            </div>
          </div>
          <p>{practice ? copy('本次为练习，成绩仅用于回顾。', 'This was practice. Results are for review only.')
            : copy('分数描述本次作答表现。能力画像仅纳入符合条件的独立证据。', 'The score describes this attempt. Only eligible independent evidence informs the competency profile.')}</p>
          {report.overall?.priority?.advice && <p>{report.overall.priority.advice}</p>}
          <ul className="ia-exam-result-list">
            {report.questions.map(item => {
              const verdict = verdictOf(item);
              return <li key={item.order_no}><button type="button" onClick={() => setOpenOrder(item.order_no)}>
                <span>{item.order_no}. {item.title}</span>
                <span className={`ia-exam-badge ${verdict === 'CORRECT' ? 'ia-exam-success' : verdict === 'PARTIAL' || verdict === 'WRONG' ? 'ia-exam-warn' : 'ia-exam-neutral'}`}>{verdictLabel(verdict, copy)}</span>
                <img src={chevron} alt="" />
              </button></li>;
            })}
          </ul>
          <div className="ia-exam-actions">
            <Link className="ia-exam-button ia-exam-primary" to={chatHref(origin, attempt.id)}>{copy('同步到聊天', 'Sync to chat')}</Link>
            <small>{copy('报告已保存到作答记录。', 'The report is saved to your attempt history.')}</small>
          </div>
        </>}
      </section>
    </main>
    {question && report && <AnswerDetails question={question} title={report.title}
      observation={report.observations?.find(item => item.order_no === question.order_no)} onClose={() => setOpenOrder(undefined)} />}
  </>;
}
