import { useT } from '../i18n';
import React, { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { DemoBanner, Modal } from '../components/ui';
import { api, ExamDetail } from '../api/client';
import { loadOrCreateExam, clearExamCreation, submitExam } from '../lib/coreLoop';
import {useExamDraft,clearSavedDraft} from '../lib/useExamDraft';
import { useSpeechRecognition } from '../lib/speech';
import { experiencedBankPath, experiencedReportPath, hasExperiencedBankOrigin } from '../lib/experiencedBankPath';

type Phase = 'loading' | 'quiz' | 'scoring' | 'uncertain' | 'closed' | 'error';

export default function Diagnosis({ studyMode = false }: { studyMode?: boolean } = {}) {
  const t = useT();

  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const requestedId = params.get('id');
  const returnTask=params.get('return_task_id');
  const validReturnTask=returnTask && /^[1-9]\d*$/.test(returnTask) && Number.isSafeInteger(Number(returnTask))?returnTask:null;
  const [busy, setBusy] = useState(false);
  const [actionFailure, setActionError] = useState<{ key: string; values: Record<string, string | number> } | null>(null);
  const actionError = actionFailure ? t(actionFailure.key, actionFailure.values) : "";
  const [retry, setRetry] = useState(0);
  const [phase, setPhase] = useState<Phase>('loading');
  const [exam, setExam] = useState<ExamDetail | null>(null);
  const currentExam = exam && (!requestedId || String(exam.id) === requestedId) ? exam : null;
  const experiencedPractice = !studyMode && currentExam?.experienced_bank_practice === true;
  const openingBankPractice = !studyMode && !!requestedId && !currentExam && hasExperiencedBankOrigin(params);
  const bankReturn = experiencedPractice || openingBankPractice ? experiencedBankPath(params) : null;
  const returnPath = bankReturn ?? (studyMode ? '/study/notes?tab=review' : '/exam');
  const returnLabel = bankReturn ? t("study.backToQuestionBank") : studyMode ? t("study.backToReviewAndPractice") : t("study.backToExamCenter");
  const diagnosisTitle = bankReturn ? t("study.questionBankPractice") : studyMode ? t("study.newScenarioRetest") : t("study.independentDiagnostic");
  const reportPath = (id: number, source = exam) => {
    const path = !studyMode && source?.experienced_bank_practice === true
      ? experiencedReportPath(id, params)
      : `${studyMode ? '/study/retest' : '/exam/diagnosis'}/report?id=${id}`;
    return validReturnTask ? `${path}&return_task_id=${validReturnTask}` : path;
  };
  const [createErr, setCreateErr] = useState<string | { key: string } | null>(null);
  const [idx, setIdx] = useState(0);
  const locked=useRef(false);
  const draft=useExamDraft(exam,phase==='quiz' && !busy);
  const answers=draft.entries.map(e=>e.value);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [abortOpen, setAbortOpen] = useState(false);

  const [secs, setSecs] = useState(30 * 60);
  useEffect(() => {
    let alive = true;
    setPhase('loading');
    setCreateErr(null);
    loadOrCreateExam(requestedId).then(d => {
      if (!alive) return;
      if (d.status !== 'ACTIVE') {
        clearExamCreation();
        if(['ABORTED','EXPIRED'].includes(d.status)){setExam(d);setPhase('closed');return;}
        navigate(reportPath(d.id, d), { replace: true });
        return;
      }
      setExam(d);
      if (!d.questions.length) { setCreateErr({ key: "study.examNoQuestions" }); setPhase("error"); return; }
      setIdx(0);
      const elapsed = d.started_at ? (Date.now() - new Date(d.started_at).getTime()) / 1000 : 0;
      setSecs(Math.max(0, Math.round(d.duration_minutes * 60 - elapsed)));
      setPhase('quiz');
      if (!requestedId) setParams({ id: String(d.id),...(validReturnTask?{return_task_id:validReturnTask}:{}) }, { replace: true });
    }).catch(e => {
      if (alive) { setCreateErr(String(e.message || e)); setPhase('error'); }
    });
    return () => { alive = false; };
  }, [requestedId, retry]);

  // 计时
  useEffect(() => {
    if (phase !== 'quiz') return;
    const deadline=exam?.started_at?new Date(exam.started_at).getTime()+(exam.duration_minutes*60000):Date.now()+secs*1000;
    const t = setInterval(() => setSecs(Math.max(0,Math.ceil((deadline-Date.now())/1000))), 1000);
    return () => clearInterval(t);
  }, [phase]);

  const qs = exam?.questions ?? [];
  const q = qs[idx];
  const isText = q?.qtype !== 'mcq';
  const answered = isText ? String(answers[idx] ?? '').trim().length > 0 : Number(answers[idx] ?? -1) >= 0;
  const mm = String(Math.floor(secs / 60)).padStart(2, '0');
  const ss = String(secs % 60).padStart(2, '0');

  const { supported: speechSupported, listening, interim, error: speechError, start: startSpeech, stop: stopSpeech } =
    useSpeechRecognition((delta) => {
      if(locked.current || phase!=='quiz')return;
      const cur=String(draft.getEntries()[idx]?.value??'');draft.edit(idx,cur+delta);
    });
  // 切换题目时停止录音，避免识别结果串到下一题。
  useEffect(() => { stopSpeech(); }, [idx, stopSpeech]);
  useEffect(()=>{
    const server=draft.serverExam;if(!server || server.id!==exam?.id || server.status==='ACTIVE')return;
    locked.current=true;stopSpeech();
    if(['EXPIRED','ABORTED'].includes(server.status)){setExam(server);setPhase('closed');}
    else navigate(reportPath(server.id, server),{replace:true});
  },[draft.serverExam,exam?.id,navigate,stopSpeech]);

  const changeQuestion = async (nextIndex: number) => {
    if (!exam || busy || locked.current || listening || interim) return;
    locked.current=true;stopSpeech();
    setBusy(true); setActionError(null);
    try { await draft.flush(); if(draft.getEntries().some(e=>e.conflict))throw new Error(t("study.resolveAnswerConflict"));setIdx(nextIndex); }
    catch (e: any) { setActionError({ key: "study.saveFailedInputRetained", values: { reason: e instanceof Error ? e.message : String(e) } }); }
    finally { locked.current=false;setBusy(false); }
  };

  const returnToBank = async () => {
    if (!bankReturn || !exam || busy || locked.current || listening || interim) return;
    locked.current = true;
    setBusy(true);
    setActionError(null);
    try {
      await draft.flush();
      if (draft.getEntries().some(entry => entry.conflict)) throw new Error(t("study.resolveAnswerConflict"));
      navigate(bankReturn, { state: { experiencedPracticeId: exam.id } });
    } catch (error) {
      setActionError({ key: "study.saveFailed", values: { reason: error instanceof Error ? error.message : String(error) } });
    } finally {
      locked.current = false;
      setBusy(false);
    }
  };

  const doAbort = async () => {
    if (!exam || busy) return;
    setAbortOpen(false); setBusy(true); setActionError(null);
    try {
      await api.post(`/exams/${exam.id}/abort`);
      clearExamCreation(); clearSavedDraft(exam.id); navigate(returnPath);
    } catch (e: any) { setActionError({ key: "study.abandonFailed", values: { reason: e instanceof Error ? e.message : String(e) } }); }
    finally { setBusy(false); }
  };

  const checkSubmission = async () => {
    if(!exam)return;
    setBusy(true);
    try{
      const current=await api.get<ExamDetail>(`/exams/${exam.id}`);
      if(current.status==='ACTIVE'){draft.reconcile(current);locked.current=false;setPhase('quiz');}
      else if(['ABORTED','EXPIRED'].includes(current.status)){setExam(current);setPhase('closed');}
      else {clearExamCreation();clearSavedDraft(exam.id);navigate(reportPath(exam.id,current),{replace:true});}
    }catch(e){setActionError({ key: "study.submissionStatusUnconfirmed", values: { reason: e instanceof Error ? e.message : String(e) } });setPhase('uncertain');}
    finally{setBusy(false);}
  };
  const submit = async () => {
    if (!exam || busy || locked.current || listening || interim || draft.entries.some(e=>e.conflict)) return;
    locked.current=true;stopSpeech();
    setConfirmOpen(false); setBusy(true); setActionError(null); setPhase('scoring');
    try {
      await draft.waitForSave();
      const finalEntries=draft.getEntries();
      const task=await submitExam(exam,finalEntries.map(e=>e.value),api,finalEntries.map(e=>e.revision));
      if(task.exam_id!==exam.id)throw new Error(t("study.missingSubmittedExamId"));
      clearExamCreation(); clearSavedDraft(exam.id);
      navigate(reportPath(exam.id));
    } catch (e: any) {
      setActionError({ key: "study.examSubmissionUnconfirmed", values: { examId: exam.id, reason: e instanceof Error ? e.message : String(e) } });
      setPhase('uncertain');await checkSubmission();
    } finally { setBusy(false); }
  };

  if (phase === 'error') return <div className="card"><h2>{bankReturn ? t("study.practiceLoadFailed") : t("study.examLoadFailed")}</h2><p role="alert">{typeof createErr === "object" && createErr ? t(createErr.key) : createErr}</p><button className="btn btn-primary" onClick={() => setRetry(x => x + 1)}>{t("study.retry")}</button><button className="btn btn-outline" onClick={() => navigate(returnPath)}>{returnLabel}</button></div>;
  if(phase==='uncertain')return <div className="card"><h2>{t("study.checkingSubmission")}</h2><p role="alert">{actionError}</p><p>{t("study.readOnlyWhileCheckingSubmission")}</p><button className="btn btn-primary" disabled={busy} onClick={checkSubmission}>{t("study.recheckSubmission")}</button></div>;
  if(phase==='closed')return <div className="card"><h2>{t(experiencedPractice ? "study.practiceClosed" : "study.examClosed", { status: t(exam?.status === "EXPIRED" ? "study.closedTimedOut" : "study.closedAbandoned") })}</h2><p>{t("study.closedExamNoScore")}</p><button className="btn btn-outline" onClick={() => navigate(returnPath)}>{returnLabel}</button></div>;

  if (phase === 'loading') {
    return (
      <div>
        <div className="quiz-top">
          <div>
            <div className="page-title" style={{ fontSize: 22 }}>{diagnosisTitle}</div>
            <div className="page-sub">{studyMode || bankReturn ? t("study.loadingExamQuestions") : t("study.generatingAdaptiveExam")}</div>
          </div>
        </div>
        <div className="score-box">
          <div className="score-ring">
            <svg width="110" height="110" viewBox="0 0 110 110">
              <circle cx="55" cy="55" r="46" fill="none" stroke="#e8e8f5" strokeWidth="8" />
              <circle className="ring-spin" cx="55" cy="55" r="46" fill="none" stroke="var(--primary)" strokeWidth="8" strokeLinecap="round"
                strokeDasharray="289" strokeDashoffset="80" transform="rotate(-90 55 55)" />
            </svg>
            <div className="ring-label">{studyMode || bankReturn ? t("study.reading") : t("study.selectingQuestions")}</div>
          </div>
          <h3 style={{ fontSize: 18, fontWeight: 650, marginTop: 18 }}>{bankReturn ? t("study.openingPractice") : studyMode ? t("study.openingRetest") : t("study.adaptiveEngineRunning")}</h3>
          <p className="hint" style={{ textAlign: 'center' }}>{studyMode || bankReturn ? t("study.frozenQuestionAndRubric") : t("study.prioritizeSkillGaps")}</p>
        </div>
      </div>
    );
  }

  if (phase === 'scoring') {
    return (
      <div>
        <div className="quiz-top">
          <div>
            <div className="page-title" style={{ fontSize: 22 }}>{diagnosisTitle}</div>
            <div className="page-sub">{t("study.savingAndLockingAnswers")}</div>
          </div>
        </div>
        <DemoBanner />
        <div className="score-box">
          <div className="score-ring">
            <svg width="110" height="110" viewBox="0 0 110 110">
              <circle cx="55" cy="55" r="46" fill="none" stroke="#e8e8f5" strokeWidth="8" />
              <circle className="ring-spin" cx="55" cy="55" r="46" fill="none" stroke="var(--primary)" strokeWidth="8" strokeLinecap="round"
                strokeDasharray="289" strokeDashoffset="80" transform="rotate(-90 55 55)" />
            </svg>
            <div className="ring-label">{t("study.submitting")}</div>
          </div>
          <h3 style={{ fontSize: 18, fontWeight: 650, marginTop: 18 }}>{t("study.waitingForSubmissionConfirmation")}</h3>
          <p className="hint" style={{ textAlign: 'center' }}>{t("study.scoringAfterSubmission")}</p>
          <div className="steps" style={{ maxWidth: 520, margin: '22px auto 0' }}>
            <div className="step now"><span className="step-ic">…</span>{t("study.saveAndSubmit")}</div>
            <div className="step"><span className="step-ic">·</span>{t("study.lockTextAnswers")}</div>
            <div className="step"><span className="step-ic">·</span>{t("study.backgroundScoring")}</div>
            <div className="step"><span className="step-ic">·</span>{t("study.generateReport")}</div>
          </div>
        </div>
      </div>
    );
  }

  if (!q || !exam) return null;
  const interviewSetId = exam.interview_set_id;

  return (
    <div>
      <div className={experiencedPractice ? 'quiz-top experienced-quiz-top' : 'quiz-top'}>
        <div>
          <div className="page-title" style={{ fontSize: 22 }}>{interviewSetId ? t("study.practiceInterviewSet") : diagnosisTitle}</div>
          <div className="page-sub">
            {interviewSetId ? t("study.interviewSetSource", { title: exam.title }) : <>{t("study.questionPosition", { questionNumber: idx + 1, questionCount: qs.length, label: q.skill || q.title.slice(0, 24) })}<span className="chip chip-green" style={{ marginLeft: 8 }}>{t("study.liveQuestionSelection")}</span></>}

          </div>
        </div>
        <div style={{ display: 'flex', flexWrap: experiencedPractice ? 'wrap' : undefined, alignItems: 'center', gap: 12 }}>
          <div className="quiz-timer">
            <span className="timer-ic">⏱</span>
            <b>{mm}:{ss}</b>
            <span className="chip chip-purple">{t("study.timeRemaining")}</span>
          </div>
          {bankReturn && <button className="btn btn-outline btn-sm" disabled={busy || listening || !!interim} onClick={returnToBank}>{t("study.saveAndReturnToSet")}</button>}
          <button className="btn btn-outline btn-sm" onClick={() => setAbortOpen(true)}>{experiencedPractice ? t("study.abandonPractice") : t("study.abandonExam")}</button>
        </div>
      </div>
      {!interviewSetId && <DemoBanner />}

      {actionError && <div role="alert" className="demo-banner demo-banner-err">{actionError}<button className="btn btn-outline btn-sm" onClick={() => navigate(reportPath(exam.id))}>{t("study.viewAttemptStatus")}</button></div>}
      <div role="status" className="hint">{draft.saving?t("study.savingToServer"):draft.entries.some(e=>e.dirty)?t("study.unsavedServerDraftKept"):experiencedPractice?t("study.practiceSavedResumeLink"):t("study.examSavedResumeCenter")}</div>
      {draft.saveError && <div role="alert" className="demo-banner demo-banner-err">{draft.saveError}<button className="btn btn-outline btn-sm" disabled={busy || draft.saving} onClick={()=>void draft.flush().catch(()=>{})}>{t("study.retrySave")}</button></div>}
      {draft.entries.map((entry,i)=>entry.conflict && <div role="alert" className="demo-banner" key={i}>{t("study.answerVersionConflict", { questionNumber: i+1 })}<p>{t("study.serverAnswer", { answer: String(entry.serverValue??t("study.unanswered")) })}</p><button className="btn btn-outline btn-sm" onClick={()=>draft.resolveConflict(i,false)}>{t("study.useServerAnswer")}</button><button className="btn btn-outline btn-sm" onClick={()=>draft.resolveConflict(i,true)}>{t("study.saveEditsOnLatestVersion")}</button></div>)}
      <div className="quiz-card">
        <div className={interviewSetId ? 'bank-quiz-stem' : undefined}>
        <div className="quiz-q-head">
          <span className="chip chip-purple">{t("study.questionNumber", { questionNumber: idx + 1 })}</span>
          <span className="chip chip-slate">{isText ? t("study.writtenAnswer") : t("study.singleChoice")}</span>
          <span className="chip chip-slate">{q.short_code}</span>
          {isText && <span className="chip chip-amber">{t("study.recommendedAnswerTime")}</span>}
        </div>
        <h3 className="quiz-stem">{q.title}</h3>
        </div>

        {isText ? (
          <div className={interviewSetId ? 'bank-quiz-answer' : undefined}>
            {interviewSetId && <label className="hint" htmlFor="bank-practice-answer">{t("study.myAnswer")}</label>}
            <textarea
              id={interviewSetId ? 'bank-practice-answer' : undefined}
              className="textarea"
              style={{ minHeight: 150, marginTop: 16 }}
              placeholder={t("study.answerInputPlaceholder")}
              value={String(answers[idx] ?? '')}
              disabled={busy}
              aria-label={t("study.answerForQuestion", { questionNumber: idx+1 })}
              onChange={(e) => draft.edit(idx,e.target.value)}
            />
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10 }}>
              {speechSupported ? (
                <button
                  className={listening ? 'btn btn-danger' : 'btn btn-outline'}
                  disabled={busy}
                  onClick={() => (listening ? stopSpeech() : startSpeech())}
                  title={listening ? t("study.stopSpeechTitle") : t("study.startSpeechTitle")}
                >
                  {listening ? t("study.stopRecording") : t("study.voiceInput")}
                </button>
              ) : (
                <button className="btn btn-outline" disabled title={t("study.speechUnsupportedTitle")}>{t("study.voiceInputUnavailable")}</button>
              )}
              {listening ? (
                <span className="hint">{interim ? t("study.recognizingSpeech", { interim }) : t("study.listening")}</span>
              ) : speechError ? (
                <span className="hint" role="alert">{speechError}</span>
              ) : (
                <span className="hint">{t("study.speechInputHelp")}</span>
              )}
            </div>
            {interviewSetId && <button className="btn btn-outline btn-sm" disabled={busy || listening || !!interim} onClick={async () => {
              try { await draft.flush(); if (draft.getEntries().some(entry => entry.conflict)) throw new Error(t("study.resolveAnswerConflict")); navigate(`/bank?set=${interviewSetId}`); }
              catch (error) { setActionError({ key: "study.saveErrorMessage", values: { message: error instanceof Error ? error.message : String(error) } }); }
            }}>{t("study.backToInterviewSet")}</button>}
          </div>
        ) : (
          <div className="opts" style={{ marginTop: 18 }}>
            {(q.options || []).map((op, i) => (
              <div
                className={`opt ${answers[idx] === i ? 'sel' : ''}`}
                key={op}
                role="radio" aria-checked={answers[idx]===i} aria-disabled={busy} tabIndex={busy?-1:0}
                onClick={() => {if(!busy)draft.edit(idx,i);}}
                onKeyDown={e=>{if(!busy && ['Enter',' '].includes(e.key)){e.preventDefault();draft.edit(idx,i);}}}
              >
                <span className="opt-radio">{answers[idx] === i ? '●' : '○'}</span>
                <span>{op}</span>
              </div>
            ))}
          </div>
        )}

        <div className="quiz-nav">
          <button className="btn btn-outline" disabled={idx === 0 || busy || listening || !!interim} onClick={() => changeQuestion(idx - 1)}>{t("study.previousQuestion")}</button>
          <div style={{ display: 'flex', gap: 8 }}>
            {qs.map((_, i) => (
              <span key={i} className={`dot-q ${i === idx ? 'now' : answers[i] !== undefined && (typeof answers[i] === 'number' ? answers[i] >= 0 : (answers[i] as string).length > 0) ? 'done' : ''}`} />
            ))}
          </div>
          {idx < qs.length - 1 ? (
            <button className="btn btn-primary" disabled={!answered || busy || listening || !!interim} onClick={() => changeQuestion(idx + 1)}>{t("study.nextQuestion")}</button>
          ) : (
            <button className="btn btn-primary" disabled={!answered || busy || listening || !!interim || draft.entries.some(e=>e.conflict)} onClick={() => setConfirmOpen(true)}>{t("study.submitAnswerSheet")}</button>
          )}
        </div>
      </div>

      {abortOpen && (
        <Modal title={experiencedPractice ? t("study.abandonPractice") : t("study.abandonExam")} onClose={() => setAbortOpen(false)} size="sm"
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setAbortOpen(false)}>{t("study.continueAnswering")}</button>
              <button className="btn btn-danger" onClick={doAbort}>{t("study.confirmAbandon")}</button>
            </>
          }>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.7 }}>
            {experiencedPractice ? t("study.abandonPracticeExplanation") : t("study.abandonExamExplanation")}
          </p>
          {!experiencedPractice && <p className="hint" style={{ marginTop: 6 }}>{t("study.examDraftOnClose")}</p>}
        </Modal>
      )}
      {confirmOpen && (
        <Modal title={t("study.submitAnswerSheet")} onClose={() => setConfirmOpen(false)} size="sm"
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setConfirmOpen(false)}>{t("study.checkAgain")}</button>
              <button className="btn btn-primary" disabled={busy || listening || !!interim || draft.entries.some(e=>e.conflict)} onClick={submit}>{t("study.confirmSubmit")}</button>
            </>
          }>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.7 }}>
            {t("study.submitQuestionCount", { count: qs.length })}
            {experiencedPractice ? t("study.practiceScoringExplanation") : t("study.examScoringExplanation")}
          </p>
          <div className="steps" style={{ marginTop: 16 }}>
            {qs.map((_, i) => (
              <div key={i} className={`step ${answers[i] !== undefined && (typeof answers[i] === 'number' ? answers[i] >= 0 : (answers[i] as string).length > 0) ? 'done' : ''}`}>
                <span className="step-ic">✓</span>{t("study.questionNumber", { questionNumber: i + 1 })}
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}
