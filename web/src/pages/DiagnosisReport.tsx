import { useLocale, useT } from '../i18n';
import StudyRetestResult from '../components/StudyRetestResult';
import StudyEntryButton from '../components/StudyEntryButton';
import React, { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { experiencedBankPath, hasExperiencedBankOrigin } from '../lib/experiencedBankPath';
import { DemoBanner, PageHead } from '../components/ui';
import { api, ExamReportData, ScoringTaskAck } from '../api/client';
import { clearExamCreation } from '../lib/coreLoop';
import AssessmentEvidence,{referenceText} from '../components/AssessmentEvidence';
import QuestionAmbiguityFeedback from '../components/QuestionAmbiguityFeedback';


const VERDICT_CHIP: Record<string, string> = {
  CORRECT: 'chip-green',
  PARTIAL: 'chip-amber',
  WRONG: 'chip-red',
};
const VERDICT_NAME: Record<string, string> = {
  CORRECT: "study.correct",
  PARTIAL: "study.partialCredit",
  WRONG: "study.missedKeyPoints",
  SCORING_FAILED: "study.scoringFailed",
};
const JUDGE_CHIP: Record<string, { cls: string; name: string }> = {
  'judge-v1': { cls: 'chip-purple', name: "study.aiScoring" },
  'judge-v2': { cls: 'chip-purple', name: "study.aiScoring" },
  'rule-v1': { cls: 'chip-slate', name: "study.ruleBasedScoring" },
  'rule-v2': { cls: 'chip-slate', name: "study.ruleBasedScoring" },
};
const PENDING_STATUSES=['SUBMITTED','TRANSCRIPT_CONFIRMED','FROZEN','SCORING','SCORING_RETRY_WAIT'];
const STATUS_LABELS:Record<string,string>={COMPLETED:"study.scoringComplete",SCORING_FAILED:"study.scoringIncomplete",NEEDS_REVIEW:"study.scoringNeedsReview",SCORING:"study.processingScores",SCORING_RETRY_WAIT:"study.waitingForBackgroundRetry",SUBMITTED:"study.answerSheetSubmitted",FROZEN:"study.answerSheetLocked",TRANSCRIPT_CONFIRMED:"study.answerSheetConfirmed",ABORTED:"study.examAbandoned",EXPIRED:"study.examExpired"};
interface AnswerGap {order_no:number;exam_question_id?:number;title:string;detail:string;quote?:string|null;ratio:number;criterion_id:string}

export default function DiagnosisReport({ studyMode = false }: { studyMode?: boolean } = {}) {
  const t = useT();
  const locale = useLocale();

  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { hash } = useLocation();
  const returnTask=params.get('return_task_id');
  const returnLink=returnTask && /^[1-9]\d*$/.test(returnTask) && Number.isSafeInteger(Number(returnTask))?<button className="btn btn-outline" onClick={()=>navigate(`/learning/task/${returnTask}`)}>{t("study.backToLearningPath")}</button>:null;
  const [open, setOpen] = useState<number[]>([0]);
  const [report, setReport] = useState<ExamReportData | null>(null);
  const [reload, setReload] = useState(0);
  const [failure, setErr] = useState<string | { key: string } | null>(null);
  const err = typeof failure === "object" && failure ? t(failure.key) : failure;
  const [deepBusy, setDeepBusy] = useState(false);
  const [deepErr, setDeepErr] = useState<string | null>(null);
  const [rescoreBusy, setRescoreBusy] = useState(false);
  const [rescoreFailure, setRescoreErr] = useState<string | { key: string } | null>(null);
  const rescoreErr = typeof rescoreFailure === "object" && rescoreFailure ? t(rescoreFailure.key) : rescoreFailure;
  const [pollPaused,setPollPaused]=useState(false);
  const reportId=params.get('id');const currentId=useRef(reportId);currentId.current=reportId;
  const currentReport = report && String(report.id) === reportId ? report : null;
  const experiencedPractice = !studyMode && currentReport?.experienced_bank_practice === true;
  const openingBankReport = !studyMode && !currentReport && hasExperiencedBankOrigin(params);
  const reportTitle = t("study.questionBankPracticeReport");
  const bankReturn = experiencedPractice || openingBankReport ? <Link className="btn btn-outline" to={experiencedBankPath(params)}>{t("study.backToQuestionBank")}</Link> : null;
  const retryPending=useRef(false);
  const toggle = (i: number) =>
    setOpen((o) => (o.includes(i) ? o.filter((x) => x !== i) : [...o, i]));

  // SCORING_FAILED 后安全重试: 重新评分 → 成功则整体替换报告
  const doRescore = async () => {
    if (!report || retryPending.current || (report.scoring_task && !report.scoring_task.can_retry)) return;
    retryPending.current=true;const id=report.id;
    setRescoreBusy(true);
    setRescoreErr(null);
    try {
      const task = await api.post<ScoringTaskAck>(`/exams/${id}/rescore`);
      if(currentId.current!==String(id))return;
      if(task.exam_id!==id){setRescoreErr({ key: "study.retryResponseWrongExam" });return;}
      setReport(old=>old && old.id===id?{...old,status:task.status,score:null,gained_points:null,overall:null,scoring_task:old.scoring_task?{...old.scoring_task,status:'QUEUED',can_retry:false}:null}:old);
      setReload(x=>x+1);
    } catch (e: any) {
      if(currentId.current===String(id))setRescoreErr(String(e.message || e));
    } finally {
      retryPending.current=false;setRescoreBusy(false);
    }
  };

  const genDeep = async () => {
    if (!report) return;
    setDeepBusy(true);
    setDeepErr(null);
    try {
      const deep = await api.post<ExamReportData['diagnosis_deep']>(`/exams/${report.id}/report/deep`);
      if(currentId.current===String(report.id))setReport(old=>old && old.id===report.id?{...old,diagnosis_deep:deep,has_deep:true}:old);
    } catch (e: any) {
      setDeepErr(String(e.message || e));
    } finally {
      setDeepBusy(false);
    }
  };

  useEffect(() => {
    setReport(null);setErr(null);setDeepErr(null);setRescoreErr(null);setOpen([0]);
  },[reportId]);
  useEffect(() => {
    let alive = true,reads=0,timer:ReturnType<typeof setTimeout>|undefined;
    setPollPaused(false);
    if (!reportId || !/^\d+$/.test(reportId)) { setErr({ key: "study.reportExamIdRequired" }); return; }
    const poll=async()=>{
      // eslint-disable-next-line no-useless-assignment -- try/catch 两分支都会重新赋值，初值仅为通过 lint 保留
      reads++;let again=false;
      try{const r=await api.get<ExamReportData>(`/exams/${reportId}/report`);if(!alive)return;if(String(r.id)!==reportId){setErr({ key: "study.reportResponseWrongExam" });again=true;}else{setReport(r);setErr(null);again=PENDING_STATUSES.includes(r.status);}}
      catch(e){if(!alive)return;setErr(String(e instanceof Error?e.message:e));again=true;}
      if(!alive || !again)return;
      if(reads>=120){setPollPaused(true);return;}
      timer=setTimeout(()=>{void poll();},5000);
    };
    void poll();return()=>{alive=false;if(timer)clearTimeout(timer);};
  }, [reportId, reload]);

  useEffect(() => {
    if (report?.status === 'COMPLETED') clearExamCreation();
  }, [report]);

  const linkedQuestionIndex = currentReport?.questions.findIndex(q => hash === `#exam-question-${q.order_no}`) ?? -1;
  useEffect(() => {
    if (linkedQuestionIndex < 0) return;
    setOpen(current => current.includes(linkedQuestionIndex) ? current : [...current, linkedQuestionIndex]);
    document.getElementById(hash.slice(1))?.scrollIntoView();
  }, [reportId, hash, linkedQuestionIndex]);

  // 后端报告
  if (currentReport) {
    const r = currentReport;
    const studyPracticeId = r.questions[0]?.frozen?.study_practice_id;
    if (studyMode && r.status === 'COMPLETED' && Number.isSafeInteger(studyPracticeId) && studyPracticeId > 0) {
      return <StudyRetestResult key={r.id} report={r} practiceId={studyPracticeId} />;
    }
    const deep = r.diagnosis_deep;
    const coverage = deep?.coverage;
    const ob = r.overall;
    const degraded = r.status !== 'COMPLETED';
    const canRetry=r.scoring_task?r.scoring_task.can_retry:r.status==='SCORING_FAILED';
    const pending=PENDING_STATUSES.includes(r.status);
    const reportTitles:Record<string,string>={diagnosis:t("study.diagnosticReport"),focused:t("study.focusedExamReport"),bank_exam:t("study.interviewSetExamReport"),project:t("study.projectInterviewReport"),practice:t("study.practiceFeedbackReport")};
    const adjacentQuestions=r.questions.filter(q=>q.frozen?.diagnostic_origin?.scope==='adjacent_scenario_diagnostic_not_course_completion_or_delayed_retest');
    const missingAssistanceDeclaration=adjacentQuestions.some(q=>q.frozen?.diagnostic_origin?.external_assistance_declaration==='NOT_COLLECTED');
    const failedSet = new Set(r.scoring_failed || []);
    const frozenMaximums=r.questions.map(q=>q.frozen?.rubric?.max_points);
    const totalPoints=typeof r.total_points==='number' && r.total_points>0?r.total_points:frozenMaximums.length>0 && frozenMaximums.every(value=>typeof value==='number' && Number.isFinite(value) && value>0)?frozenMaximums.reduce((sum,value)=>sum+value,0):'—';
    const judges = r.summary?.judges;
    const gaps=r.questions.flatMap<AnswerGap>(q=>{
      const detailed=q.answer?.feedback?.criterion_scores;
      if(detailed?.length)return detailed.filter(c=>c.score<c.max_score).map(c=>({order_no:q.order_no,exam_question_id:q.exam_question_id,title:c.title || q.frozen?.rubric?.criteria?.find((x:any)=>x.criterion_id===c.criterion_id)?.title || c.criterion_id,detail:c.missing_points.join(t("study.clauseSeparator")) || c.why_not_higher,quote:c.answer_quotes[0],ratio:c.max_score?c.score/c.max_score:1,criterion_id:c.criterion_id}));
      return (q.answer?.feedback?.missed_points || []).map(p=>({order_no:q.order_no,exam_question_id:q.exam_question_id,title:typeof p==='string'?p:p.title,detail:t("study.historicalEvidenceUnavailable"),quote:null,ratio:1,criterion_id:''}));
    }).sort((a,b)=>a.ratio-b.ratio).slice(0,3);
    const strengths=r.questions.flatMap(q=>(q.answer?.feedback?.hit_points || []).map(p=>t("study.questionObservation", { question: q.order_no, observation: typeof p === "string" ? p : p.title }))).slice(0,3);
    const partialEvidence=r.questions.flatMap(q=>(q.answer?.feedback?.criterion_scores || []).filter(c=>c.score>0 && c.score<3).map(c=>({order_no:q.order_no,id:c.criterion_id,title:c.title || q.frozen?.rubric?.criteria?.find((item:any)=>item.criterion_id===c.criterion_id)?.title || c.criterion_id,score:c.score,maximum:c.max_score,quotes:c.answer_quotes || []}))).slice(0,3);
    const recordedStrengths=r.questions.flatMap(q=>{const value=(q.answer?.feedback as {strengths?:unknown}|null)?.strengths;return Array.isArray(value)?value.filter((item):item is string=>typeof item==='string').map(item=>t("study.questionObservation", { question: q.order_no, observation: item })):[];}).slice(0,3);
    const scoringSource = judges && typeof judges === 'object'
      ? t("study.scoringSourceCounts", { aiCount: judges.judge_v1 == null && judges.judge_v2 == null ? t("study.notRecorded") : (judges.judge_v1 ?? 0) + (judges.judge_v2 ?? 0), ruleCount: judges.rule_v1 == null && judges.rule_v2 == null ? t("study.notRecorded") : (judges.rule_v1 ?? 0) + (judges.rule_v2 ?? 0) })
      : t("study.scoringSourcesPerQuestion");
    return (
      <div>
        <PageHead title={experiencedPractice ? reportTitle : (r.title === '社招专项' ? t("study.questionBankPractice") : r.title?.trim()) || reportTitles[r.kind || ''] || t("study.answerReport")} sub={t("study.reportSubtitle", { kind: t(experiencedPractice ? "study.practice" : "study.exam"), examId: r.id, status: STATUS_LABELS[r.status] ? t(STATUS_LABELS[r.status]) : r.status, date: r.completed_at ? new Date(r.completed_at).toLocaleString(locale, { hour12: false }) : "—" })}>
          {bankReturn ?? <button className="btn btn-outline" onClick={() => navigate('/exam')}>{t("study.backToExamCenter")}</button>}
          {!degraded && !experiencedPractice && <button className="btn btn-primary" onClick={() => navigate('/learning')}>{t("study.viewTodayTasks")}</button>}
          {returnLink}
        </PageHead>
        <DemoBanner />
        {adjacentQuestions.length>0 && <section className="card" style={{marginBottom:16}} aria-label={t("study.diagnosticScope")}><h3>{t("study.adjacentScenarioDiagnostic")}</h3><p>{t("study.adjacentScenarioScope", { questions: adjacentQuestions.map(q=>`Q${q.order_no}`).join(t("study.listSeparator")) })}</p>{missingAssistanceDeclaration && <p className="hint">{t("study.externalAssistanceNotCollected")}</p>}</section>}
        {err && <p role="alert">{t("study.reportStatusRefreshFailed", { reason: err })}<button className="btn btn-outline btn-sm" onClick={()=>setReload(x=>x+1)}>{t("study.refreshAttemptStatus")}</button></p>}
        {pollPaused && <p role="status">{t("study.reportAutoRefreshPaused")}<button className="btn btn-outline btn-sm" onClick={()=>setReload(x=>x+1)}>{t("study.resumeAttemptRefresh")}</button></p>}

        {/* SCORING_FAILED: 显式失败态, 不给假分, 提供重评入口 */}
        {degraded && (
          <div style={{ padding: '14px 18px', borderRadius: 12, marginBottom: 18, background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.3)' }}>
            <div role="status" style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>{t(STATUS_LABELS[r.status] || "study.attemptScoringIncomplete")}{experiencedPractice ? t("study.practiceScorePending") : t("study.noValidExamScore")}</div>
            <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.75, marginBottom: 10 }}>
              {pending?t("study.lockedAnswersWithProgress"):t("study.failedQuestionsList", { questions: (r.scoring_failed || []).map(n=>`Q${n}`).join(t("study.listSeparator")) || t("study.seeQuestionStatuses") })} {experiencedPractice ? t("study.completedQuestionScoresKept") : t("study.completedResultsBeforeTotal")}
              {canRetry?t("study.safeScoringRetry"):pending?(experiencedPractice?t("study.keepReportLink"):t("study.returnViaExamCenter")):t("study.unsafeScoringRetry")}
            </div>
            {r.scoring_task && <div><p>{t("study.scoredQuestionCount", { scored: r.scoring_task.completed_count, total: r.scoring_task.total_count, retryTime: r.scoring_task.next_retry_at?t("study.nextScoringRetryTime", { time: new Date(r.scoring_task.next_retry_at).toLocaleString(locale) }):'' })}</p>{r.scoring_task.error && <p role="alert">{r.scoring_task.error.message}</p>}{r.scoring_task.questions.filter(q=>q.error).map(q=><p role="alert" key={q.order_no}>{t("study.scoringQuestionError", { question: q.order_no, error: q.error?.message ?? "", action: t(q.can_retry ? "study.canSafelyRetry" : "study.addressErrorOrWaitForReview") })}</p>)}</div>}
            {rescoreErr && <div className="hint" style={{ color: 'var(--red)', marginBottom: 8 }}>{t("study.lastRescoreFailed", { reason: rescoreErr })}</div>}
            <button className="btn btn-primary" disabled={rescoreBusy} onClick={canRetry ? doRescore : () => setReload(x => x + 1)}>
              {rescoreBusy ? t("study.registeringRetry") : canRetry ? t("study.rescoreFailedQuestions") : t("study.refreshAttemptStatus")}
            </button>
          </div>
        )}

        <div className="report-hero">
          <div>
            <div className="hero-label">{experiencedPractice ? t("study.practiceScore") : t("study.scoreWithTitle", { title: r.title === '社招专项' ? t("study.questionBankPractice") : r.title })}</div>
            <div style={{ fontSize: 24, fontWeight: 700, marginTop: 4 }}>
              {!degraded && r.score != null ? t("study.scoreInPoints", { score: r.score }) : t("study.scoreIncomplete")}
              <span className="hero-chip" style={{ marginLeft: 10 }}>{t("study.scoreBreakdown", { questionCount: r.questions.length, score: !degraded?r.gained_points??'—':'—', maxScore: totalPoints })}</span>
            </div>
            <div className="hint" style={{ color: 'rgba(255,255,255,0.55)', marginTop: 8 }}>
              {experiencedPractice ? t("study.practiceScoreScope") : r.selection_reason}
            </div>
            {!degraded && !experiencedPractice && (
              <div className="hint" style={{ color: 'rgba(255,255,255,0.75)', marginTop: 6 }}>
                {t("study.examScoreScope")}
              </div>
            )}
          </div>
          <div className="report-score">
            {!degraded && r.score != null ? r.score : '—'}<span>/100</span>
            <div className="note" style={{ fontSize: 12, color: 'rgba(255,255,255,0.5)', marginTop: 2 }}>{t("study.answerSheetScore")}</div>
          </div>
        </div>

        {!degraded && <section className="card" aria-label={t("study.keyAnswerFeedback")} style={{marginBottom:18,padding:18}}>
          <h3>{t("study.feedbackSummary")}</h3>
          {strengths.length?<p><b>{t("study.criteriaMet")}</b>{strengths.join(t("study.clauseSeparator"))}</p>:<p>{t("study.noPassingCriteria")}</p>}
          {partialEvidence.length>0 && <div><h4>{t("study.partialEvidence")}</h4>{partialEvidence.map(item=><div key={`${item.order_no}:${item.id}`}><p>{t("study.partialEvidenceScore", { question: item.order_no, criterion: item.title, score: item.score, maxScore: item.maximum })}</p>{item.quotes.map((quote,index)=><blockquote key={index} style={{whiteSpace:'pre-wrap'}}>{quote}</blockquote>)}</div>)}</div>}
          {recordedStrengths.length>0 && <p><b>{t("study.strengthsRecorded")}</b>{recordedStrengths.join(t("study.clauseSeparator"))}</p>}
          {gaps.length?gaps.map((gap,i)=>{
            const task=!experiencedPractice && gap.exam_question_id?r.learning_tasks?.find(t=>t.source_exam_question_id===gap.exam_question_id && (gap.criterion_id && t.criterion_id?t.criterion_id===gap.criterion_id:t.source_criterion===gap.title || (!!gap.criterion_id && t.source_criterion===gap.criterion_id))):undefined;
            return <article key={`${gap.order_no}:${i}`}><h4>{i+1}. Q{gap.order_no} · {gap.title}</h4><p>{gap.detail}</p>{gap.quote && <blockquote>{gap.quote}</blockquote>}
              <a href={`#exam-question-${gap.order_no}`} onClick={()=>setOpen(old=>[...new Set([...old,r.questions.findIndex(q=>q.order_no===gap.order_no)])])}>{t("study.viewAnswerAndEvidence")}</a>
              {task && <button className="btn btn-primary btn-sm" style={{marginLeft:12}} onClick={()=>navigate(`/learning/task/${task.id}`)}>{t("study.studyCriterion")}</button>}
            </article>;
          }):<p>{experiencedPractice ? t("study.noMissingCriteria") : t("study.noMissingCriteriaDomainScope")}</p>}
          <p className="hint">{t("study.scoringReviewScope")}</p>
        </section>}

        {/* 诊断结论(确定性) */}
        {!experiencedPractice && !degraded && ob && (
          <div className="card" style={{ marginBottom: 18 }}>
            <div className="card-head">
              <span className="card-title">{t("study.diagnosticFindings")}</span>
              <span className="card-sub">{scoringSource}</span>
            </div>
            <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.8 }}>{ob.conclusion}</p>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
              {ob.strong.map((s) => <span key={s} className="chip chip-green">✓ {s}</span>)}
              {ob.mid.map((s) => <span key={s} className="chip chip-amber">△ {s}</span>)}
              {ob.weak.map((s) => <span key={s} className="chip chip-red">✗ {s}</span>)}
            </div>
            {ob.priority && (
              <div style={{ marginTop: 14, padding: '12px 14px', borderRadius: 10, background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.25)' }}>
                <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 4 }}>{t("study.priorityAction")}</div>
                <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.75 }}>{ob.priority.advice}</div>
              </div>
            )}
          </div>
        )}

        {/* 能力域得分 */}
        {!experiencedPractice && !!r.skill_breakdown?.length && (
          <div className="card" style={{ marginBottom: 18 }}>
            <div className="card-head">
              <span className="card-title">{t("study.answersBySkillDomain")}</span>
              <span className="card-sub">{t("study.domainsSortedByScore")}</span>
            </div>
            <div style={{ display: 'grid', gap: 12 }}>
              {r.skill_breakdown.map((g) => {
                const pct = g.ratio==null?null:Math.round(g.ratio * 100);
                const color = g.ratio==null?'var(--text-3)':g.ratio >= 0.8 ? 'var(--green)' : g.ratio >= 0.6 ? 'var(--amber)' : 'var(--red)';
                return (
                  <div key={g.skill_key} style={{ display: 'grid', gridTemplateColumns: '150px 1fr 130px', gap: 14, alignItems: 'center' }}>
                    <div>
                      <div style={{ fontSize: 13.5, fontWeight: 600 }}>{g.skill_name}</div>
                      <div className="hint" style={{ margin: 0 }}>{t("study.domainQuestionCount", { questionCount: g.q_count, questionNumbers: g.order_nos.join(' Q') })}</div>
                    </div>
                    <div>
                      {pct==null?<span className="hint">{t("study.incompleteScoringNotZero")}</span>:<div style={{ height: 8, borderRadius: 4, background: 'var(--surface-3)', overflow: 'hidden' }}>
                        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 4, transition: 'width .4s ease' }} />
                      </div>}
                    </div>
                    <div style={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'right' }}>
                      <b style={{ color }}>{g.earned??'—'}/{g.max}</b>
                      <span style={{ marginLeft: 8 }}>✓{g.verdicts.CORRECT} △{g.verdicts.PARTIAL} ✗{g.verdicts.WRONG}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* 深度诊断(LLM) */}
        {!experiencedPractice && !degraded && (
        <div className="card" style={{ marginBottom: 18 }}>
          <div className="card-head">
            <span className="card-title">{t("study.deepAiDiagnosis")}</span>
            <span className="card-sub">
              {deep?.model ? `${deep.model} · ${deep.generated_at ? new Date(deep.generated_at).toLocaleString(locale, { dateStyle: "short", timeStyle: "short" }) : ""}` : t("study.deepDiagnosisSummary")}
            </span>
          </div>
          {!deep && !deepBusy && (
            <div style={{ textAlign: 'center', padding: '10px 0 4px' }}>
              <p className="hint" style={{ marginBottom: 12 }}>
                {t("study.deepDiagnosisOptional")}
              </p>
              <button className="btn btn-primary" onClick={genDeep}>{t("study.generateDeepDiagnosis")}</button>
            </div>
          )}
          {deepBusy && (
            <div style={{ textAlign: 'center', padding: '14px 0' }}>
              <div style={{
                width: 28, height: 28, margin: '0 auto 10px', borderRadius: '50%',
                border: '3px solid var(--border-2)', borderTopColor: 'var(--primary)',
                animation: 'spin 0.9s linear infinite',
              }} />
              <style>{'@keyframes spin { to { transform: rotate(360deg); } }'}</style>
              <div style={{ fontSize: 13, color: 'var(--text-3)' }}>{t("study.generatingExtraDiagnosis")}</div>
            </div>
          )}
          {deepErr && (
            <div className="demo-banner demo-banner-err" style={{ marginBottom: 10 }}>{t("study.deepDiagnosisFailed", { reason: deepErr })}</div>
          )}
          {deep && !deepBusy && (
            <>
              {coverage && (
                <p role="status">
                  {t("study.explainedQuestionCount", { explained: coverage.explained, total: coverage.total })}
                  {coverage.missing_order_nos.length
                    ? t("study.unexplainedQuestionCount", { questions: coverage.missing_order_nos.map(no => `Q${no}`).join(t("study.listSeparator")) })
                    : t("study.allQuestionsExplained")}
                </p>
              )}
              <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.85, whiteSpace: 'pre-wrap' }}>{deep.conclusion}</p>
              {!!deep.weaknesses.length && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>{t("study.weakAreas")}</div>
                  <div style={{ display: 'grid', gap: 10 }}>
                    {deep.weaknesses.map((w, i) => (
                      <div key={i} style={{ padding: '10px 14px', borderRadius: 10, background: 'rgba(220,38,38,0.07)', border: '1px solid rgba(220,38,38,0.2)' }}>
                        <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 4 }}>{w.skill}</div>
                        <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.75 }}>{w.why}</div>
                        {w.gap && <div style={{ fontSize: 12.5, color: 'var(--text-3)', lineHeight: 1.7, marginTop: 6 }}><b>{t("study.missingSkillPrefix")}</b>{w.gap}</div>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {!!deep.strengths.length && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>{t("study.strongAreas")}</div>
                  <div style={{ display: 'grid', gap: 8 }}>
                    {deep.strengths.map((s, i) => (
                      <div key={i} style={{ padding: '8px 14px', borderRadius: 10, background: 'rgba(22,163,74,0.07)', border: '1px solid rgba(22,163,74,0.2)', fontSize: 13, lineHeight: 1.7 }}>
                        <b>{s.skill}</b><span style={{ color: 'var(--text-2)' }}> — {s.why}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {!!deep.actions.length && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>{t("study.prioritizedActions")}</div>
                  <div style={{ display: 'grid', gap: 8 }}>
                    {deep.actions.map((a, i) => (
                      <div key={i} style={{ padding: '10px 14px', borderRadius: 10, background: 'var(--surface-2)', border: '1px solid var(--border-2)' }}>
                        <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 4 }}>{i + 1}. {a.title}</div>
                        <div style={{ fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.75 }}>{a.detail}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
        )}

        {!experiencedPractice && !degraded && <div className="card" style={{ marginBottom: 18 }}>
          <div className="card-head"><span className="card-title">{t("study.tasksForMissedCriteria")}</span><span className="card-sub">{t("study.observationCount", { count: r.observations?.length ?? r.questions.filter(q => q.answer?.evidence_refs?.length).length })}</span></div>
          {(r.learning_tasks ?? []).length === 0 && <p className="hint">{t("study.noLinkedTasks")}</p>}
          {(r.learning_tasks ?? []).map(task => <div className="list-row" key={task.id}>
            <div className="list-main"><div className="list-title">{task.title}</div><div className="list-sub">{t("study.missingScoringCriteria", { criteria: task.source_criterion || t("study.viewTaskSource") })}</div></div>
            <button className="btn btn-primary btn-sm" onClick={() => navigate(`/learning/task/${task.id}`)}>{t("study.studyCriterion")}</button>
          </div>)}
        </div>}

        <div className="card-head" style={{ margin: '0 0 12px' }}>
          <span className="card-title">{t("study.perQuestionScoring")}</span>
        </div>
        {r.questions.map((q, i) => {
          const a = q.answer;
          const step=r.scoring_task?.questions.find(item=>item.order_no===q.order_no);
          const validScore=typeof a?.points==='number' && Number.isFinite(a.points) && a.points>=0 && typeof a.max_points==='number' && Number.isFinite(a.max_points) && a.max_points>0 && a.points<=a.max_points;
          const evaluated=validScore && ['CORRECT','PARTIAL','WRONG'].includes(a?.verdict || '') && !a?.feedback?.error && (step?step.status==='SUCCEEDED' && !!a?.feedback:!failedSet.has(q.order_no) && (!degraded || !!a?.feedback));
          const ok = evaluated && a?.verdict === 'CORRECT';
          const dq = evaluated?deep?.per_question?.[String(q.order_no)]:null;
          const jc = evaluated && a?.judge_version ? JUDGE_CHIP[a.judge_version] : null;
          const failedQ = !evaluated && (step?.status==='FAILED' || failedSet.has(q.order_no));
          const pendingLabel=({QUEUED:t("study.pendingScoring"),RUNNING:t("study.scoring"),RETRY_WAIT:t("study.waitingRetry"),FAILED:t("study.scoringFailed"),NEEDS_REVIEW:t("study.resultsNeedReview"),CANCELLED:t("study.scoringCanceled")} as Record<string,string>)[step?.status || ''] || (r.status==='NEEDS_REVIEW'?t("study.resultsNeedReview"):failedQ?t("study.scoringFailed"):t("study.noValidScoreYet"));
          return (
            <div className="report-q" key={q.order_no} id={`exam-question-${q.order_no}`}>
              <div className="report-q-head" role="button" tabIndex={0} aria-expanded={open.includes(i)} onKeyDown={e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();toggle(i);}}} onClick={() => toggle(i)}>
                <span className={`chip ${failedQ ? 'chip-red' : evaluated?VERDICT_CHIP[a?.verdict || ''] || 'chip-slate':'chip-slate'}`}>{evaluated ? (VERDICT_NAME[a?.verdict || ''] ? t(VERDICT_NAME[a?.verdict || '']) : '') : pendingLabel}</span>
                {jc && <span className={`chip ${jc.cls}`} style={{ marginLeft: 6 }}>{t(jc.name)}</span>}
                <span className="report-q-score" style={{ color: ok ? 'var(--green)' : 'var(--amber)' }}>{evaluated?a?.points:'—'}<small style={{ fontSize: 13, color: 'var(--text-4)' }}>/{q.frozen?.rubric?.max_points ?? a?.max_points ?? '—'}</small></span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 600 }}>Q{q.order_no} · {q.title}</div>
                  <div className="hint" style={{ margin: 0 }}>{experiencedPractice ? q.short_code : <>{t("study.quotedEvidenceCount", { skill: q.skill ?? "", count: evaluated?(a?.evidence_refs || []).join(', ') || '—':t("study.noValidScoringEvidence") })}</>}</div>
                </div>
                <span style={{ color: 'var(--text-4)', fontSize: 12 }}>{open.includes(i) ? t("study.collapse") : t("study.expand")}</span>
              </div>
              {open.includes(i) && (
                <div className="report-q-body">
                  {!evaluated && (step?.error?.message || failedQ) && (
                    <div style={{ padding: '10px 14px', borderRadius: 10, background: 'rgba(220,38,38,0.07)', border: '1px solid rgba(220,38,38,0.25)', marginBottom: 12, fontSize: 13, lineHeight: 1.75 }}>
                      ⚠️ {step?.error?.message || (typeof a?.feedback?.error==='string'?a.feedback.error:t("study.questionScoringFailed"))} {step?.can_retry || canRetry?t("study.retryScoringAtTop"):t("study.followScoringStatus")}
                    </div>
                  )}
                  {dq && (
                    <>
                      <div style={{ padding: '10px 14px', borderRadius: 10, background: 'rgba(109,93,246,0.09)', border: '1px solid rgba(109,93,246,0.25)', marginBottom: 12 }}>
                        <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 4 }}>{t("study.aiScoringRationale")}</div>
                        <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.8 }}>{dq.why}</div>
                        <div style={{ fontSize: 13, fontWeight: 650, margin: '10px 0 4px', color: 'var(--primary)' }}>{t("study.howToImprove")}</div>
                        <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.8 }}>{dq.how}</div>
                      </div>
                    </>
                  )}
                  <p><b>{t("study.yourAnswer")}</b>{a?.text_answer || (a?.choice_index != null ? t("study.chosenOption", { choiceNumber: a.choice_index + 1 }) : t("study.unansweredInParentheses"))}</p>
                  <AssessmentEvidence question={q} evaluated={evaluated}/>
                  {evaluated && a?.feedback && !a.feedback.criterion_scores?.length && (
                    <>
                      <p>
                        <b>{t("study.keyPointsMet")}</b>
                        {a.feedback.hit_points?.length
                          ? a.feedback.hit_points.map((p: any) => (typeof p === 'string' ? p : p.title)).join(t("study.listSeparator"))
                          : t("study.noMetPointsRecorded")}
                      </p>
                      <p>
                        <b>{t("study.missingKeyPoints")}</b>
                        {a.feedback.missed_points?.length
                          ? a.feedback.missed_points.map((p: any) => (typeof p === 'string' ? p : p.title)).join(t("study.listSeparator"))
                          : t("study.none")}
                      </p>
                      {a.feedback.next && <p><b>{t("study.suggestedNextStep")}</b>{a.feedback.next}</p>}
                    </>
                  )}
                  {evaluated && !!a?.feedback?.followups?.length && (
                    <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px dashed var(--border-2)' }}>
                      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>{t("study.interviewerFollowUp")}</div>
                      <div style={{ display: 'grid', gap: 8 }}>
                        {a.feedback.followups.map((f, fi) => (
                          <div key={fi} style={{ padding: '9px 13px', borderRadius: 10, background: 'var(--surface-2)', border: '1px solid var(--border-2)' }}>
                            <div style={{ fontSize: 13, lineHeight: 1.7 }}>
                              <span className={`chip ${f.kind === 'Boundary' ? 'chip-amber' : f.kind === 'Trade-off' ? 'chip-purple' : f.kind === 'Expression' ? 'chip-red' : 'chip-blue'}`} style={{ marginRight: 8, fontSize: 11 }}>{f.label || f.kind}</span>
                              {f.question}
                            </div>
                            <div style={{ fontSize: 12, color: 'var(--text-3)', lineHeight: 1.65, marginTop: 5 }}>💡 {f.why}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {evaluated && q.reference_answer && (
                    <div style={{ marginTop: 12, borderTop: '1px dashed var(--border-2)', paddingTop: 10 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>{t("study.expectedAnswer")}</div>
                      <div className="bank-answer" style={{whiteSpace:'pre-wrap'}}>{referenceText(q.reference_answer)}</div>
                    </div>
                  )}
                  {q.exam_question_id && <StudyEntryButton kind="exam_question" id={q.exam_question_id}/>}
                  {q.exam_question_id && <QuestionAmbiguityFeedback key={`${r.id}:${q.exam_question_id}`} examId={r.id} questionId={q.exam_question_id}/>}
                </div>
              )}
            </div>
          );
        })}

        {!experiencedPractice && !degraded && (
        <div className="card" style={{ marginTop: 18, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div>
            <div className="card-title" style={{ margin: 0 }}>{t("study.nextStep")}</div>
            <p className="hint" style={{ margin: '4px 0 0' }}>{t("study.observationsAndNewQuestion")}</p>
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn btn-outline" onClick={() => navigate('/exam/diagnosis')}>{t("study.retest")}</button>
            <button className="btn btn-primary" onClick={() => navigate('/learning')}>{t("study.startLearning")}</button>
          </div>
        </div>
        )}
      </div>
    );
  }

  return <div>
    <PageHead title={openingBankReport ? reportTitle : t("study.thisDiagnosticReport")} sub={reportId ? `${openingBankReport ? t("study.practice") : t("study.exam")} #${reportId}` : t("study.examNotSpecified")}>
      {bankReturn ?? <button className="btn btn-outline" onClick={() => navigate('/exam')}>{t("study.backToExamCenter")}</button>}
      {returnLink}
    </PageHead>
    <div className="card">{err ? <><p role="alert">{t("study.reportLoadFailed", { reason: err })}</p>{reportId && <button className="btn btn-primary" onClick={() => setReload(x => x + 1)}>{t("study.retryReport")}</button>}</> : <p>{t("study.loadingReport")}</p>}</div>
  </div>;
}
