import { useT } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';

interface Submission {
  submission_id:number; revision:number; status:string; can_retry:boolean; error?:{message?:string};
  payload:{text_answer:string}; feedback?:Record<string,unknown>; assistance:{hint_used:boolean;reference_viewed:boolean;feedback_seen:boolean};
}
interface Practice {
  id:number; session_id:number; question:string; knowledge_point:string; skill_key:string; skill_name:string;
  card_id?:number; kind:'quiz'|'explain'; status:string; submissions:Submission[]; retest_exam_id?:number; assistance:{hint_used:boolean;reference_viewed:boolean};
}
function Feedback({value,answer}:{value:Record<string,unknown>;answer:string}) {
  const t = useT();

  const criteria=Array.isArray(value.criterion_scores) ? value.criterion_scores : [];
  return <section className="study-practice-feedback" aria-label={t("study.practiceFeedback")}><h4>{t("study.practiceFeedback")}</h4>
    {criteria.map((row,index)=>{
      const quotes=[...new Set<string>((row.answer_quotes||[]).filter((quote:unknown)=>typeof quote==='string' && quote.trim() && answer.includes(quote)))];
      const gaps=[...new Set<string>([row.why_not_higher,...(row.missing_points||[])].filter(Boolean))];
      return <div key={row.criterion_id}><h5>{t("study.criterionScore", { criterionNumber: index+1, score: row.score })}</h5>
        {quotes.length>0 && <p>{t("study.originalAnswerQuote")}{quotes.map(quote=><q key={quote}>{quote}</q>)}</p>}
        {row.reasoning_summary && <p>{t("study.criterionFeedback", { label: t(row.score>0 ? "study.whatWorked" : "study.criterionPerformance"), explanation: row.reasoning_summary })}</p>}
        {row.score<4 && gaps.length>0 && <p>{t("study.remainingGaps", { gaps: gaps.join(t("study.clauseSeparator")) })}</p>}
      </div>;
    })}
    {typeof value.scope_note==='string' && <p className="hint">{value.scope_note}</p>}
  </section>;
}
const statusLabel:Record<string,string>={COMPLETED:"study.evaluated",FAILED:"study.evaluationFailed",NEEDS_REVIEW:"study.awaitingManualReview",CANCELLED:"study.evaluationCanceled",QUEUED:"study.awaitingEvaluation",RUNNING:"study.evaluating"};

export interface PracticeRequest { kind: 'quiz' | 'explain'; messageId?: number; key: string }
export default function StudyPracticePanel({sessionId,cardId,ended,focusMessageId,initialRequest}:{sessionId:number;cardId?:number;ended:boolean;focusMessageId?:number;initialRequest?:PracticeRequest}) {
  const t = useT();

  const base='/study/sessions/'+sessionId+'/practices';
  const [params]=useSearchParams();
  const cardQuiz=cardId && params.get('practice')==='quiz' ? cardId : undefined;
  const scope=base+'/'+(cardQuiz??'');
  const [items,setItems]=useState<Practice[]>([]);
  const [loadedBase,setLoadedBase]=useState('');
  const [selected,setSelected]=useState<{scope:string;id:number}>();
  const [skills,setSkills]=useState<{key:string;name:string}[]>([]);
  const [skill,setSkill]=useState('');
  const [answer,setAnswer]=useState('');
  const [help,setHelp]=useState('');
  const [err,setErr]=useState('');
  const [busy,setBusy]=useState(false);
  const [result,setResult]=useState<any>(null);
  const lastKey=useRef(new Map<string,string>());
  const sequence=useRef(0);
  const inFlight=useRef(false);
  const autoAttempt=useRef('');
  const currentBase=useRef(base);
  currentBase.current=base;
  const currentScope=useRef(scope);
  currentScope.current=scope;
  const ready=loadedBase===base;
  const matching=ready && cardQuiz ? items.find(item=>item.card_id===cardQuiz && item.kind==='quiz') : undefined;
  const p=ready ? (selected?.scope===scope ? items.find(item=>item.id===selected.id) : undefined) ?? (cardQuiz ? matching : items[0]) : undefined;
  const history=ready ? items.filter(item=>item.id!==p?.id) : [];
  async function refresh() {
    if(currentBase.current!==base)return;
    const version=++sequence.current;
    const value=await api.get(base);
    if(version===sequence.current && currentBase.current===base){setItems(value.items);setLoadedBase(base)}
  }
  useEffect(()=>{
    refresh().catch(e=>{if(currentBase.current===base)setErr(e.message)});
    api.get('/profile').then(data=>setSkills(data.domains||[])).catch(()=>{});
    return ()=>{sequence.current++};
  },[base]);
  const pending=p?.submissions.some(s=>!['COMPLETED','FAILED','NEEDS_REVIEW','CANCELLED'].includes(s.status));
  useEffect(()=>{
    if(!pending)return;
    const timer=setInterval(()=>refresh().catch(e=>setErr(e.message)),1800);
    return ()=>clearInterval(timer);
  },[pending,base]);
  useEffect(()=>{
    let live=true;
    if(p?.retest_exam_id)api.get('/study/practices/'+p.id+'/retest').then(v=>{if(live)setResult(v)}).catch(e=>{if(live)setErr(e.message)});
    else setResult(null);
    return ()=>{live=false};
  },[p?.retest_exam_id,p?.id]);
  async function perform(op:string,fn:(key:string)=>Promise<void>,stableKey?:string) {
    if(inFlight.current)return;
    inFlight.current=true;
    setErr('');setBusy(true);
    try{const key=stableKey||lastKey.current.get(op)||crypto.randomUUID(); lastKey.current.set(op,key);
      await fn(key);lastKey.current.delete(op);await refresh()}catch(e){if(currentScope.current===scope)setErr((e as Error).message)}finally{inFlight.current=false;setBusy(false)}
  }
  function start(kind:'quiz'|'explain', requestKey?:string) {
    if(!ready || ended)return;
    if(kind==='quiz' && matching){setSelected({scope,id:matching.id});return}
    const stableKey=kind==='quiz' && cardQuiz ? 'study-card-quiz-'+cardQuiz : requestKey;
    perform('start-'+kind+'-'+skill+'-'+(cardId||'')+'-'+(focusMessageId||''),async key=>{
      const item:Practice=await api.post(base,{kind,skill_key:cardQuiz ? undefined : skill||undefined,card_id:cardId,...(!cardId&&focusMessageId?{focus_message_id:focusMessageId}:{}),request_key:key});
      if(currentScope.current===scope){
        setItems(current=>[item,...current.filter(existing=>existing.id!==item.id)]);
        setSelected({scope,id:item.id});
      }
    },stableKey);
  }
  const startedRequest = useRef('');
  useEffect(() => {
    if (!ready || ended || busy || !initialRequest || startedRequest.current === initialRequest.key) return;
    startedRequest.current = initialRequest.key;
    start(initialRequest.kind, initialRequest.key);
  }, [ready, ended, busy, initialRequest]);
  useEffect(()=>{setErr('')},[scope]);
  useEffect(()=>{setAnswer('');setHelp('')},[scope,p?.id]);
  useEffect(()=>{
    if(!cardQuiz || ended || busy || !ready || matching || autoAttempt.current===scope)return;
    autoAttempt.current=scope;
    start('quiz');
  },[scope,cardQuiz,ended,busy,ready,matching]);
  const sub=p?.submissions[p.submissions.length-1];
  const path='/study/practices/'+p?.id;
  const active=p && p.status!=='SKIPPED';
  return <section className="card study-practice" aria-label={t("study.activePractice")}>
    <div className="study-actions"><h3>{t("study.activePractice")}</h3>{!cardId&&focusMessageId&&<small>{t("study.practiceBasedOnMessage", { messageId: focusMessageId })}</small>}
      {!ended && <><label>{t("study.practiceTopic")}<select value={skill} onChange={e=>setSkill(e.target.value)}><option value="">{t("study.useCurrentTopic")}</option>{skills.map(s=><option key={s.key} value={s.key}>{s.name}</option>)}</select></label>
      <button className="btn btn-outline" disabled={!ready||busy} id="study-restate" onClick={()=>start('explain')}>{t("study.letMeExplain")}</button>
      <button className="btn btn-outline" disabled={!ready||busy} id="study-quiz" onClick={()=>start('quiz')}>{cardId?t("study.quizMeOnCard"):t("study.quizMe")}</button></>}
    </div>
    {err && <p role="alert">{err}</p>}
    {!ready && !err && <p role="status">{t("study.loadingPractice")}</p>}
    {!p && ready && busy && <p role="status">{t("study.preparingPractice")}</p>}
    {p && <article><p className="hint">{p.knowledge_point} · {p.skill_name||t("study.noLinkedSkill")}</p><div className="study-practice-question"><small>{t("study.currentQuestion")}</small><p className="study-content">{p.question}</p></div>
    {p.submissions.map(s=><details key={s.submission_id} open><summary>{s.revision===1?t("study.firstAnswer"):t("study.practiceAttemptRevision", { revision: s.revision })} · {t(statusLabel[s.status] || "study.evaluationInProgress")}</summary>
      <blockquote>{s.payload.text_answer}</blockquote>
      <p className="hint">{[s.assistance?.hint_used?t("study.hintUsed"):'',s.assistance?.reference_viewed?t("study.referenceViewed"):'',s.assistance?.feedback_seen?t("study.feedbackSeen"):''].filter(Boolean).join(' · ')||t("study.noAssistanceRecorded")}</p>
      {s.status==='COMPLETED' && s.feedback && <Feedback value={s.feedback} answer={s.payload.text_answer}/>}
      {s.error && <p role="alert">{s.error.message||t("study.evaluationFailed")}</p>}
      {s.can_retry && <button className="btn btn-outline" onClick={()=>perform('retry',async()=>{await api.post('/learning/submissions/'+s.submission_id+'/retry')})}>{t("study.retryEvaluation")}</button>}
    </details>)}
    {active && !ended && <><label>{sub?t("study.reviseAnswer"):t("study.myOriginalAnswer")}<textarea value={answer} onChange={e=>setAnswer(e.target.value)} rows={3}/></label>
      <div className="study-actions"><button className="btn btn-primary" disabled={busy||pending||!answer.trim()} onClick={()=>perform('answer-'+p.id+'-'+answer,async key=>{
        await api.post(path+'/answers',{text_answer:answer.trim(),request_key:key,parent_submission_id:sub?.submission_id});setAnswer('');
      })}>{sub?t("study.submitRevision"):t("study.submitOriginalAnswer")}</button>
      {(['hint','reference'] as const).map(kind=><button key={kind} className="btn btn-outline" disabled={busy} onClick={()=>perform(kind,async key=>{
        const data=await api.post(path+'/assistance',{kind,request_key:key});setHelp(data.content)
      })}>{kind==='hint'?t("study.requestHint"):t("study.viewSampleAnswer")}</button>)}
      <button className="btn btn-ghost" disabled={busy} onClick={()=>perform('skip',async()=>{await api.post(path+'/skip');setHelp('')})}>{t("study.skipAndContinueExplanation")}</button></div></>}
    {help && <p className="study-content">{help}</p>}
    {sub && active && (!p.retest_exam_id || ['ABORTED','EXPIRED'].includes(result?.status)) && !ended && <button className="btn btn-outline" disabled={busy||pending} onClick={()=>perform('retest',async()=>{await api.post(path+'/retest')})}>{t("study.generateNewScenarioRetest")}</button>}
    {p.retest_exam_id && <p><Link to={'/study/retest'+(result?.status==='COMPLETED'?'/report':'')+'?id='+p.retest_exam_id}>{t("study.openNewScenarioRetest")}</Link> {result && <span>{t("study.retestOverview", { status: result.status, skill: result.skill_key, evidenceCount: result.evidence_ids.length, level: result.skill_state?.estimated_level??t("study.awaitingVerification") })}</span>}</p>}
    </article>}
    {history.length>0 && <details><summary>{t("study.otherPracticeAttempts", { count: history.length })}</summary>{history.map(r=><div key={r.id}><strong>{r.question}</strong>{r.submissions.map(s=><p key={s.submission_id}>{t("study.practiceHistoryAnswer", { kind: t(s.revision===1 ? "study.originalAnswer" : "study.revisionLabel"), answer: s.payload.text_answer, status: t(statusLabel[s.status] || "study.evaluationInProgress") })}</p>)}{r.retest_exam_id&&<Link to={'/study/retest?id='+r.retest_exam_id}>{t("study.viewRetest", { examId: r.retest_exam_id })}</Link>}</div>)}</details>}
  </section>
}
