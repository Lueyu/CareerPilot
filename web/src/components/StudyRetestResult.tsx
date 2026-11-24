import { useT } from '../i18n';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, type ExamReportData } from '../api/client';

type Retest = {
  exam_id: number; evidence_ids: number[]; scoring_run_id: number | null;
  competency_snapshot_id: number | null; skill_state_after?: { estimated_level?: number | null } | null;
};
type Origin = { retest_exam_id: number; session_id: number; source: { note_id?: number }; knowledge_point: string };

export default function StudyRetestResult({ report, practiceId }: { report: ExamReportData; practiceId: number }) {
  const t = useT();

  const [evidence, setEvidence] = useState<{ retest: Retest; origin: Origin }>();
  const [error, setError] = useState<string | { key: string }>('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let live = true;
    setEvidence(undefined); setError('');
    Promise.all([
      api.get<Retest>('/study/practices/' + practiceId + '/retest'),
      api.get<Origin>('/study/practices/' + practiceId),
    ]).then(([retest, origin]) => {
      if (!live) return;
      if (retest.exam_id !== report.id || origin.retest_exam_id !== report.id) {
        setError({ key: "study.retestAlreadyLinked" });
        return;
      }
      setEvidence({ retest, origin });
    }).catch((err: Error) => { if (live) setError(err.message); });
    return () => { live = false; };
  }, [report.id, practiceId, reload]);
  const question = report.questions[0];
  const ids = evidence?.retest.evidence_ids;
  const feedback = question?.answer?.feedback?.criterion_scores ?? [];
  return <article className="study-retest-result">
    <span className="study-status-pill">{t("study.scoringComplete")}</span>
    <h1>{ids?.length ? t("study.independentEvidenceFromRetest") : t("study.newScenarioRetestResult")}</h1>
    <section className="study-result-summary"><h2>{evidence?.origin.knowledge_point || question?.skill || t("study.thisAnswer")}</h2>
      <p>{question?.title}</p>
      {question?.answer?.points != null && <strong>{t("study.questionPoints", { points: question.answer.points, maxPoints: question.answer.max_points })}</strong>}
    </section>
    {error && <p role="alert">{typeof error === "string" ? error : t(error.key)} <button className="btn btn-outline" onClick={() => setReload(n => n + 1)}>{t("study.retryEvidenceLoad")}</button></p>}
    {!error && !evidence && <p role="status">{t("study.verifyingRetestEvidence")}</p>}
    {feedback.map(item => <section className="study-result-criterion" key={item.criterion_id}><h3>{item.title || t("study.evaluationCriterion")} · {item.score} / {item.max_score}</h3>
      {item.reasoning_summary && <p>{item.reasoning_summary}</p>}
      {(item.answer_quotes ?? []).filter(quote => quote && question?.answer?.text_answer?.includes(quote)).map(quote => <blockquote key={quote}>{quote}</blockquote>)}
      {item.score < item.max_score && item.why_not_higher && <p>{item.why_not_higher}</p>}
    </section>)}
    {evidence && <section className="study-result-trail"><h3>{t("study.evidenceTrail")}</h3>
      <p>{t("study.retestEvidenceTrail", { examId: report.id, scoringRun: evidence.retest.scoring_run_id ?? t("study.notRecorded"), evidence: ids?.length ? t("study.validEvidence", { ids: ids.map(n => "#" + n).join(t("study.listSeparator")) }) : t("study.noIndependentRetestEvidence") })}</p>
      {evidence.retest.competency_snapshot_id && <p>{t("study.competencySnapshot", { snapshotId: evidence.retest.competency_snapshot_id, levelDetail: evidence.retest.skill_state_after?.estimated_level != null ? t("study.historicalSkillLevel", { level: evidence.retest.skill_state_after.estimated_level }) : "" })}</p>}
    </section>}
    <div className="study-actions">{evidence?.origin.source?.note_id && <Link className="btn btn-primary" to={'/study/notes/' + evidence.origin.source.note_id}>{t("study.backToSourceNote")}</Link>}
      {evidence && <Link className="btn btn-outline" to={'/study/chat/' + evidence.origin.session_id}>{t("study.continueStudying")}</Link>}
      <Link className="btn btn-ghost" to={'/exam/diagnosis/report?id=' + report.id}>{t("study.fullScoreReport")}</Link>
    </div>
  </article>;
}
