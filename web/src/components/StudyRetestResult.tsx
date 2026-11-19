import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type ExamReportData } from '../api/client';

type Retest = {
  exam_id: number; evidence_ids: number[]; scoring_run_id: number | null;
  competency_snapshot_id: number | null; skill_state_after?: { estimated_level?: number | null } | null;
};
type Origin = { retest_exam_id: number; session_id: number; source: { note_id?: number }; knowledge_point: string };

export default function StudyRetestResult({ report, practiceId }: { report: ExamReportData; practiceId: number }) {
  const [evidence, setEvidence] = useState<{ retest: Retest; origin: Origin }>();
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let live = true;
    setEvidence(undefined); setError('');
    Promise.all([
      api.get<Retest>('/study/practices/' + practiceId + '/retest'),
      api.get<Origin>('/study/practices/' + practiceId),
    ]).then(([retest, origin]) => {
      if (!live) return;
      if (retest.exam_id !== report.id || origin.retest_exam_id !== report.id) throw new Error('原练习已关联另一场复测。');
      setEvidence({ retest, origin });
    }).catch((err: Error) => { if (live) setError(err.message); });
    return () => { live = false; };
  }, [report.id, practiceId, reload]);
  const question = report.questions[0];
  const ids = evidence?.retest.evidence_ids;
  const feedback = question?.answer?.feedback?.criterion_scores ?? [];
  return <article className="study-retest-result">
    <span className="study-status-pill">本次评分已完成</span>
    <h1>{ids?.length ? '本次新情境有了独立作答证据' : '新情境复测结果'}</h1>
    <section className="study-result-summary"><h2>{evidence?.origin.knowledge_point || question?.skill || '本次作答'}</h2>
      <p>{question?.title}</p>
      {question?.answer?.points != null && <strong>本题得分 {question.answer.points} / {question.answer.max_points}</strong>}
    </section>
    {error && <p role="alert">{error} <button className="btn btn-outline" onClick={() => setReload(n => n + 1)}>重试读取证据</button></p>}
    {!error && !evidence && <p role="status">正在核对本次证据…</p>}
    {feedback.map(item => <section className="study-result-criterion" key={item.criterion_id}><h3>{item.title || '评价要点'} · {item.score} / {item.max_score}</h3>
      {item.reasoning_summary && <p>{item.reasoning_summary}</p>}
      {(item.answer_quotes ?? []).filter(quote => quote && question?.answer?.text_answer?.includes(quote)).map(quote => <blockquote key={quote}>{quote}</blockquote>)}
      {item.score < item.max_score && item.why_not_higher && <p>{item.why_not_higher}</p>}
    </section>)}
    {evidence && <section className="study-result-trail"><h3>可回溯的记录</h3>
      <p>复测 #{report.id} → 评分 #{evidence.retest.scoring_run_id ?? '未记录'} → {ids?.length ? '有效证据 ' + ids.map(n => '#' + n).join('、') : '本次尚无独立作答证据'}</p>
      {evidence.retest.competency_snapshot_id && <p>画像快照 #{evidence.retest.competency_snapshot_id}{evidence.retest.skill_state_after?.estimated_level != null && ' · 当时能力等级 ' + evidence.retest.skill_state_after.estimated_level}</p>}
    </section>}
    <div className="study-actions">{evidence?.origin.source?.note_id && <Link className="btn btn-primary" to={'/study/notes/' + evidence.origin.source.note_id}>回到来源笔记</Link>}
      {evidence && <Link className="btn btn-outline" to={'/study/chat/' + evidence.origin.session_id}>继续学习</Link>}
      <Link className="btn btn-ghost" to={'/exam/diagnosis/report?id=' + report.id}>完整评分报告</Link>
    </div>
  </article>;
}
