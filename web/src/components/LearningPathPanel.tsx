import { t as translate, useT } from '../i18n';
import React,{useEffect,useRef,useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {api,getWorkspace,LearningTaskView} from '../api/client';
import {LearningAssistance,LearningPathData,LearningSubmission,LearningSubmissionAck,LEARNING_PENDING,LEARNING_RUN_LABELS,learningError} from '../lib/learningPath';
import ProjectPracticePanel,{ProjectActivityPicker} from './ProjectPracticePanel';
import LearningMemoryReview from './LearningMemoryReview';
import LearningPlanControls from './LearningPlanControls';

const verificationLabels:Record<string,string>={
  get UNVERIFIED() { return translate('progress.unverified'); },
  get ASSISTED_CORRECT() { return translate('progress.assistedCorrect'); },
  get INDEPENDENT_CORRECT() { return translate('progress.independentCorrect'); },
  get PRACTICE_CORRECT() { return translate('progress.learningPathPanelPracticeCorrect'); },
  get NOT_SUBMITTED() { return translate('progress.notSubmitted'); },
  get PENDING_REVIEW() { return translate('progress.awaitingReview'); },
  get NEEDS_REVISION() { return translate('progress.needsRevision'); },
  get TASK_VERIFIED() { return translate('progress.taskVerified'); },
  get CODE_REVIEWED() { return translate('progress.learningPathPanelCodeReviewOnly'); },
  get PENDING_RETEST() { return translate('progress.awaitingRetest'); },
  get PASSED_THIS_ATTEMPT() { return translate('progress.passedThisTime'); },
  get NOT_DUE() { return translate('progress.notDue'); },
};
const emptyAssistance:LearningAssistance={hint_used:false,reference_viewed:false,ai_declared:false,feedback_seen:false};
const key=()=>globalThis.crypto?.randomUUID?.() || `learning-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const contentText=(value:unknown):string=>typeof value==='string'?value:Array.isArray(value)?value.filter(v=>typeof v==='string').join('\n'):value && typeof value==='object' && 'text' in value?String(value.text):'';

export default function LearningPathPanel({task,onProgressSaved}:{task:LearningTaskView;onProgressSaved?:()=>void}){
  const t = useT();
  const navigate=useNavigate();
  const [path,setPath]=useState<LearningPathData|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[refresh,setRefresh]=useState(0),[paused,setPaused]=useState(false);
  const [selected,setSelected]=useState<number|null>(null),[answer,setAnswer]=useState(''),[ai,setAi]=useState(false),[draftError,setDraftError]=useState('');
  const [detail,setDetail]=useState<LearningSubmission|null>(null),[help,setHelp]=useState<{kind:string;text:string}|null>(null),[notice,setNotice]=useState('');
  const currentTask=useRef(task.id);currentTask.current=task.id;
  const mounted=useRef(true),pending=useRef(false),requests=useRef<Record<string,string>>({});
  const pendingSubmission=useRef<number|null>(null);
  const activity=path?.activities.find(a=>a.id===selected) || path?.activities[0];
  const history=(path?.submissions || []).filter(s=>s.activity_id===activity?.id).sort((a,b)=>b.revision-a.revision || b.id-a.id);
  const latest=history[0] || activity?.latest_submission || null;
  const running=!!pendingSubmission.current || !!latest && LEARNING_PENDING.has(latest.status);
  const activityPaused=!!activity && (['DEFERRED','SKIPPED'].includes(activity.status) || path?.goal?.status!=='ACTIVE');
  const assistance=activity?.assistance || emptyAssistance;
  const draftKey=activity?`cp-learning-draft:${getWorkspace()}:${task.id}:${activity.id}:${activity.revision}`:'';
  const requestKey=(signature:string)=>requests.current[signature] ||= key();

  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{setPath(null);setSelected(null);setDetail(null);setHelp(null);setError('');setNotice('');pendingSubmission.current=null;requests.current={};},[task.id]);
  useEffect(()=>{
    let alive=true,reads=0,timer:ReturnType<typeof setTimeout>|undefined;
    const taskId=task.id;setLoading(true);setPaused(false);
    const read=async()=>{
      try{
        const data=await api.get<LearningPathData>(`/learning/tasks/${taskId}/path`);
        if(!alive)return;
        if(data.task_id!==taskId || !Array.isArray(data.activities) || !Array.isArray(data.submissions))throw new Error(translate('progress.learningPathPanelIncompletePath'));
        setPath(data);setError('');setLoading(false);reads++;
        if(pendingSubmission.current && data.submissions.some(s=>s.id===pendingSubmission.current))pendingSubmission.current=null;
        if(data.submissions.some(s=>LEARNING_PENDING.has(s.status)) || pendingSubmission.current){
          if(reads<120)timer=setTimeout(()=>void read(),5000);else setPaused(true);
        }
      }catch(e){if(alive){setError(translate('progress.learningPathPanelLoadError', { error: learningError(e) }));setLoading(false);}}
    };
    void read();return()=>{alive=false;if(timer)clearTimeout(timer);};
  },[task.id,refresh]);
  useEffect(()=>{
    setDetail(null);setHelp(null);setNotice('');setDraftError('');
    if(!activity){setAnswer('');setAi(false);return;}
    try{
      const raw=localStorage.getItem(draftKey),draft=raw?JSON.parse(raw):null;
      setAnswer(typeof draft?.answer==='string'?draft.answer:latest?.payload?.text_answer || '');setAi(draft?.ai===true);
    }catch{setAnswer('');setAi(false);setDraftError(translate('progress.learningPathPanelDraftReadError'));}
  },[draftKey]);
  const saveDraft=(text:string,aiDeclared:boolean)=>{
    setAnswer(text);setAi(aiDeclared);
    try{localStorage.setItem(draftKey,JSON.stringify({answer:text,ai:aiDeclared}));setDraftError('');}catch{setDraftError(t('progress.learningPathPanelDraftSaveError'));}
  };
  const perform=async(action:()=>Promise<void>)=>{
    if(pending.current)return;pending.current=true;setBusy(true);setError('');setNotice('');
    try{await action();}catch(e){if(mounted.current)setError(learningError(e));}finally{pending.current=false;if(mounted.current)setBusy(false);}
  };
  const prepare=()=>perform(async()=>{
    const id=task.id;const data=await api.post<LearningPathData>(`/learning/tasks/${id}/prepare`);
    if(currentTask.current!==id)return;
    if(data.task_id!==id || !data.goal)throw new Error(t('progress.learningPathPanelInvalidGoal'));
    setPath(data);
  });
  const submit=()=>perform(async()=>{
    if(!activity || !answer.trim() || running || activityPaused)return;
    const id=task.id,activityId=activity.id;
    const payload={text_answer:answer,ai_declared:ai};
    const result=await api.post<LearningSubmissionAck>(`/learning/activities/${activityId}/submissions`,{payload,expected_activity_revision:activity.revision,parent_submission_id:latest?.id || null,request_key:requestKey(JSON.stringify([activityId,activity.revision,latest?.id,payload]))});
    if(currentTask.current!==id)return;
    if(!result.submission_id)throw new Error(t('progress.learningPathPanelMissingSubmission'));
    pendingSubmission.current=result.submission_id;setDetail(null);setHelp(null);setNotice(t('progress.learningPathPanelSubmissionSaved'));setRefresh(x=>x+1);
  });
  const viewFeedback=(submission:LearningSubmission)=>perform(async()=>{
    const id=task.id;const value=await api.get<LearningSubmission>(`/learning/submissions/${submission.id}`);
    if(currentTask.current!==id)return;
    if(value.id!==submission.id || value.activity_id!==activity?.id)throw new Error(t('progress.learningPathPanelFeedbackMismatch'));
    setDetail(value);
    setPath(old=>old?{...old,activities:old.activities.map(a=>a.id===value.activity_id?{...a,assistance:{...a.assistance,feedback_seen:true}}:a)}:old);
  });
  const askHelp=(kind:'HINT'|'REFERENCE')=>perform(async()=>{
    if(!activity || activityPaused)return;const id=task.id,activityId=activity.id;
    const result=await api.post<{assistance:LearningAssistance;content:unknown}>(`/learning/activities/${activityId}/assistance`,{kind,expected_activity_revision:activity.revision,request_key:requestKey(`assistance:${activityId}:${activity.revision}:${kind}`)});
    if(currentTask.current!==id)return;
    const displayed=contentText(result.content);if(!displayed || !result.assistance)throw new Error(t('progress.learningPathPanelIncompleteAssistance'));
    setPath(old=>old?{...old,activities:old.activities.map(a=>a.id===activityId?{...a,assistance:result.assistance}:a)}:old);setHelp({kind,text:displayed});
  });
  const retry=()=>perform(async()=>{
    if(!latest?.can_retry)return;const id=task.id;
    const result=await api.post<LearningSubmissionAck>(`/learning/submissions/${latest.id}/retry`);
    if(currentTask.current!==id)return;
    if(result.submission_id!==latest.id)throw new Error(t('progress.learningPathPanelRetryMismatch'));
    pendingSubmission.current=result.submission_id;setRefresh(x=>x+1);
  });
  const complete=()=>perform(async()=>{
    const id=task.id;await api.post(`/learning/tasks/${id}/complete`,{artifact_text:answer});
    if(currentTask.current!==id)return;setNotice(t('progress.learningPathPanelProgressSaved'));setRefresh(x=>x+1);onProgressSaved?.();
  });
  const retest=()=>perform(async()=>{
    const id=task.id;const result=await api.post<{exam_id:number}>(`/learning/tasks/${id}/retest`);
    if(currentTask.current!==id)return;if(!result.exam_id)throw new Error(t('progress.learningPathPanelMissingRetest'));
    navigate(`/exam/diagnosis?id=${result.exam_id}&return_task_id=${id}`);
  });

  if(!path)return <section className="card" style={{marginBottom:16}}><h3>{t('progress.learningPath')}</h3>{error?<p role="alert">{error}</p>:<p role="status">{t('progress.learningPathPanelLoading')}</p>}{!loading && <button className="btn btn-outline" onClick={()=>setRefresh(x=>x+1)}>{t('progress.learningPathPanelReload')}</button>}</section>;
  const goal=path.goal;
  return <section style={{marginBottom:18}} aria-label={t('progress.learningPath')}>
    {error && <p role="alert" className="demo-banner demo-banner-err">{error}{t('progress.learningPathPanelDraftPreserved')}</p>}
    {notice && <p role="status">{notice}</p>}
    {paused && <p role="status">{t('progress.learningPathPanelPollingPaused')}<button className="btn btn-outline btn-sm" onClick={()=>setRefresh(x=>x+1)}>{t('progress.learningPathPanelResumePolling')}</button></p>}
    {path.worker?.online===false && <p className="hint">{t('progress.learningPathPanelWorkerOffline')}</p>}
    {!goal?<div className="card"><h3>{t('progress.learningPathPanelCreateGoal')}</h3><p>{task.source_criterion || task.title}</p><p className="hint">{t('progress.learningPathPanelCreateGoalExplanation')}</p><button className="btn btn-primary" disabled={busy} onClick={prepare}>{busy?t('progress.learningPathPanelPreparing'):t('progress.learningPathPanelPrepare')}</button></div>:<>
      <div className="card" style={{marginBottom:16}}><div className="card-title">{t('progress.learningPathPanelCurrentGoal') + ' '}<button className="btn btn-ghost btn-sm" disabled={busy || loading} onClick={()=>setRefresh(x=>x+1)}>{t('progress.learningPathPanelRefresh')}</button></div><p>{goal.target_behavior}</p><p className="hint">{t('progress.learningPathPanelFeedbackScope')}</p>
        <div style={{display:'flex',gap:12,flexWrap:'wrap'}}>{Object.entries(goal.verification).map(([name,value])=><span className="chip chip-slate" key={name}>{{understanding:t('progress.understanding'),practice:t('progress.practice'),transfer:t('progress.transfer'),retention:t('progress.retention')}[name] || name}：{verificationLabels[value] || value}</span>)}</div>
        {goal.evidence_status!=='VALID' && <p className="hint">{t('progress.learningPathPanelInvalidSource', { status: goal.evidence_status })}</p>}
        <details style={{marginTop:12}}><summary>{t('progress.learningPathPanelStandards')}</summary><p>{t('progress.learningPathPanelSourceCriterion')}{task.source_criterion || t('progress.learningPathPanelSourceIncomplete')}</p>{task.content?.observation?.answer_excerpt && <blockquote>{task.content.observation.answer_excerpt}</blockquote>}<p className="hint">{t('progress.learningPathPanelGoalVersions', { revision: goal.plan_revision, criteria: goal.success_criteria_version || t('progress.notRecorded') })}</p>{goal.origin_refs && <dl>{['exam_id','exam_question_id','answer_revision_id','scoring_run_id','criterion_id','rubric_version','judge_version'].map(name=>goal.origin_refs?.[name]!=null?<React.Fragment key={name}><dt>{name}</dt><dd>{String(goal.origin_refs[name])}</dd></React.Fragment>:null)}</dl>}</details>
      </div>
      {path.activities.length===0 && <div className="card" style={{marginBottom:16}}><h3>{t('progress.learningPathPanelGoalSaved')}</h3><p className="hint">{t('progress.learningPathPanelPrepareExplanation')}</p><button className="btn btn-primary" disabled={busy || !goal.allowed_actions.includes('prepare')} onClick={prepare}>{busy?t('progress.learningPathPanelPreparing'):t('progress.learningPathPanelPrepare')}</button></div>}
      {path.learning_content && <details className="card" style={{marginBottom:16}}><summary>{t('progress.learningPathPanelShortLesson')}</summary>{path.learning_content.analogy && <p>{path.learning_content.analogy}</p>}{path.learning_content.analogy_limits && <p>{t('progress.learningPathPanelAnalogyLimits')}{path.learning_content.analogy_limits}</p>}{path.learning_content.example && <p>{path.learning_content.example}</p>}{path.learning_content.principles?.map((p,i)=><p key={i}>{p}</p>)}</details>}
      {path.activities.length>1 && <details className="card" style={{marginBottom:16}}><summary>{t('progress.learningPathPanelActivityCount', { count: path.activities.length })}</summary>{path.activities.map(a=><button key={a.id} className={'btn btn-sm '+(a.id===activity?.id?'btn-primary':'btn-outline')} style={{margin:6}} onClick={()=>setSelected(a.id)} disabled={busy}>{a.position}. {a.title}</button>)}</details>}
      {activity && <div className="card"><div className="card-title">{t('progress.learningPathPanelCurrentActivity', { title: activity.title })}</div><p style={{whiteSpace:'pre-wrap'}}>{activity.prompt}</p>{activity.authoring_source==='deterministic_prompt_template' && <p className="hint">{t('progress.learningPathPanelRuleBased')}</p>}{activity.required_output && <p>{t('progress.learningPathPanelRequiredOutput')}{activity.required_output}</p>}<details><summary>{t('progress.learningPathPanelCriteria')}</summary>{activity.success_criteria?.map((c,i)=>typeof c==='string'?<p key={i}>{c}</p>:<div key={i}><p><b>{c.title || c.criterion_id}</b>{c.detail?`：${c.detail}`:''}</p>{c.required_evidence?.map((point,n)=><p key={n}>{point}</p>)}{c.score_anchors?.map(a=><p className="hint" key={a.anchor_id}>{t('progress.learningPathPanelScoreAnchor', { score: a.score, description: a.description })}</p>)}</div>)}<p className="hint">{t('progress.learningPathPanelActivityVersion', { revision: activity.revision, contentVersion: activity.content_version || t('progress.learningPathPanelMissingContentVersion') })}</p></details>
        {activityPaused && <p role="status">{t('progress.learningPathPanelInactiveActivity')}</p>}{activity.kind==='CONCEPT' && <>
          <div style={{display:'flex',gap:8,margin:'12px 0'}}><button className="btn btn-outline btn-sm" disabled={busy || running || activityPaused || !activity.allowed_actions.includes('hint')} onClick={()=>askHelp('HINT')}>{t('progress.learningPathPanelGetHint')}</button><button className="btn btn-outline btn-sm" disabled={busy || running || activityPaused || !activity.allowed_actions.includes('reference')} onClick={()=>askHelp('REFERENCE')}>{t('progress.learningPathPanelViewReference')}</button></div>
          <p className="hint">{t('progress.learningPathPanelAssistanceSummary', { hint: assistance.hint_used ? t('progress.learningPathPanelHintUsed') : t('progress.learningPathPanelHintUnused'), reference: assistance.reference_viewed ? t('progress.learningPathPanelReferenceSeen') : t('progress.learningPathPanelReferenceUnseen'), feedback: assistance.feedback_seen ? t('progress.learningPathPanelFeedbackSeen') : t('progress.learningPathPanelFeedbackUnseen') })}</p>
          {help && <div className="demo-banner" style={{whiteSpace:'pre-wrap'}}>{t('progress.learningPathPanelAssistanceRecorded', { type: help.kind==='HINT' ? t('progress.learningPathPanelSmallHint') : t('progress.learningPathPanelReference') })}<p>{help.text}</p></div>}
          <label>{t('progress.learningPathPanelMyAnswer')}<textarea className="textarea" aria-label={t('progress.learningPathPanelAnswerLabel')} rows={6} value={answer} disabled={busy || running || activityPaused} onChange={e=>saveDraft(e.target.value,ai)} placeholder={t('progress.learningPathPanelAnswerPlaceholder')}/></label>
          <label style={{display:'block',margin:'8px 0'}}><input type="checkbox" aria-label={t('progress.learningPathPanelAiAssistance')} checked={ai} disabled={busy || running || activityPaused} onChange={e=>saveDraft(answer,e.target.checked)}/>{t('progress.learningPathPanelAiAssistance')}</label>
          <p className="hint">{t('progress.learningPathPanelDraftExplanation')}</p>{draftError && <p role="alert">{draftError}</p>}
          <button className="btn btn-primary" disabled={busy || running || activityPaused || !answer.trim() || !activity.allowed_actions.includes('submit')} onClick={submit}>{busy?t('progress.saving'):latest?t('progress.learningPathPanelSubmitRevision'):t('progress.learningPathPanelSubmitPractice')}</button>
        </>}
        {activity.kind==='PROJECT' && <ProjectPracticePanel key={`${activity.id}:${activity.revision}`} activity={{...activity,allowed_actions:activityPaused?[]:activity.allowed_actions}} latest={latest} onSubmitted={ack=>{pendingSubmission.current=ack.submission_id;setDetail(null);setRefresh(x=>x+1);}}/>}
        {latest && <div style={{marginTop:16}}><h4>{t('progress.learningPathPanelSubmissionRevision', { revision: latest.revision })}</h4><p role="status">{LEARNING_RUN_LABELS[latest.status] || latest.status}</p>{latest.error && <p role="alert">{learningError(latest.error)}{t('progress.learningPathPanelReviewStatusScope')}</p>}{latest.can_retry && <button className="btn btn-outline" disabled={busy} onClick={retry}>{t('progress.learningPathPanelRetryReview')}</button>}{latest.has_feedback && <button className="btn btn-primary" disabled={busy} onClick={()=>viewFeedback(latest)}>{t('progress.learningPathPanelViewFeedback')}</button>}</div>}
        {detail?.feedback && <div style={{marginTop:16}} aria-label={t('progress.learningPathPanelFeedbackLabel')}><h4>{t('progress.learningPathPanelFeedbackRevision', { title: detail.id===latest?.id ? t('progress.learningPathPanelCurrentFeedback') : t('progress.learningPathPanelPastFeedback'), revision: detail.revision })}</h4><p className="hint">{detail.feedback.scope_note || t('progress.learningPathPanelPracticeScope')}</p>{(detail.payload?.text_answer || detail.payload?.answer_text) && <details><summary>{t('progress.learningPathPanelOriginalSubmission')}</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{detail.payload.text_answer || detail.payload.answer_text}</pre></details>}{detail.feedback.project_feedback && <div className="demo-banner"><div><p>{t('progress.learningPathPanelCodeOnlyScope')}</p><p>{detail.feedback.project_feedback.scope_note}</p><p className="hint">{t('progress.learningPathPanelLogsAndAcceptance', { logs: detail.payload?.test_evidence?.user_log_supplied ? t('progress.learningPathPanelUserTestLogs') : t('progress.learningPathPanelNoTestLogs'), acceptance: detail.feedback.project_feedback.acceptance_version || t('progress.notRecorded') })}</p><h5>{t('progress.learningPathPanelExplainImplementation')}</h5>{detail.feedback.project_feedback.defense_questions?.map((question,i)=><p key={i}>{question}</p>)}</div></div>}{detail.feedback.versions && <p className="hint">{t('progress.learningPathPanelUncalibrated', { versions: Object.entries(detail.feedback.versions).map(([name, value]) => name + ': ' + value).join(' · ') })}</p>}<h5>{t('progress.learningPathPanelMetCriteria')}</h5>{detail.feedback.correct.length?detail.feedback.correct.map((c,i)=><div key={i}>{c.quotes.map((quote,n)=><blockquote key={n}>{quote}</blockquote>)}<p>{c.why}</p></div>):<p>{t('progress.learningPathPanelNoMetCriteria')}</p>}<h5>{t('progress.learningPathPanelMissingCriteria')}</h5>{detail.feedback.gaps.map((g,i)=><div key={i}><b>{g.kind==='MISSING_OR_UNCLEAR'?t('progress.learningPathPanelMissingOrUnclear'):g.kind}</b>{g.quotes.map((quote,n)=><blockquote key={n}>{quote}</blockquote>)}<p>{g.why}</p>{g.minimal_hint && <p>{t('progress.learningPathPanelHintPrefix')}{g.minimal_hint}</p>}</div>)}<p>{t('progress.learningPathPanelNextStep')}{detail.feedback.next_action==='REVISE'?t('progress.learningPathPanelReviseFirst'):detail.feedback.next_action==='NEW_CONTEXT_RETEST'?t('progress.learningPathPanelVerifyNewContext'):detail.feedback.next_action}</p><p className="hint">{t('progress.learningPathPanelFeedbackAssistance')}</p></div>}
        {history.length>0 && <details style={{marginTop:16}}><summary>{t('progress.learningPathPanelSubmissionCount', { count: history.length })}</summary>{history.map(s=><div key={s.id} style={{margin:'8px 0'}}>{t('progress.learningPathPanelHistoricalRevision', { revision: s.revision, status: LEARNING_RUN_LABELS[s.status] || s.status })}{s.has_feedback && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={()=>viewFeedback(s)}>{t('progress.learningPathPanelViewHistoricalFeedback')}</button>}</div>)}</details>}
      </div>}
      <LearningPlanControls key={`plan-${goal.id}`} goalId={goal.id} revision={goal.plan_revision} status={goal.status} activityId={activity?.id} activityStatus={activity?.status} onChanged={()=>setRefresh(x=>x+1)}/><ProjectActivityPicker key={`project-${goal.id}`} goalId={goal.id} onCreated={id=>{setSelected(id);setRefresh(x=>x+1);}}/>
      <div className="card" style={{marginTop:16}}><h3>{t('progress.learningPathPanelVerification')}</h3>{path.next_action && <p>{path.next_action.message}</p>}<p className="hint">{t('progress.learningPathPanelVerificationExplanation')}</p>{goal.allowed_actions.some(action=>action==='complete' || action==='complete_reading') && <button className="btn btn-outline" disabled={busy || running} onClick={complete}>{t('progress.learningPathPanelMarkComplete')}</button>}{goal.allowed_actions.includes('retest') && <button className="btn btn-primary" disabled={busy || running} onClick={retest}>{t('progress.learningPathPanelStartVerification')}</button>}{!goal.allowed_actions.includes('retest') && <p>{t('progress.learningPathPanelVerifyAfterLearning')}</p>}</div>
      {(path.source_memories?.length || path.plan_history?.length || path.evolution_candidates?.length)?<details className="card" style={{marginTop:16}}><summary>{t('progress.learningPathPanelHistory')}</summary>{path.source_memories?.map(memory=><p key={`source-${memory.id}`}>{t('progress.learningPathPanelReferencedMemory', { id: memory.id, revision: memory.revision, content: memory.content })}</p>)}{path.plan_history?.map((entry,i)=><div key={`plan-${i}`}><b>{t('progress.learningPathPanelPlanVersion', { revision: entry.revision })}</b><p>{entry.reason}</p>{entry.changes.map((change,n)=><p key={n}>{change.activity_id?t('progress.learningPathPanelActivityPrefix', { id: change.activity_id }):t('progress.learningPathPanelPathPrefix')}{change.from} → {change.to}</p>)}<p className="hint">{t('progress.learningPathPanelPlanChangeScope')}</p></div>)}{path.evolution_candidates?.map(candidate=><div key={`strategy-${candidate.id}`}><b>{candidate.title}</b><p>{candidate.reason}</p><p className="hint">{t('progress.learningPathPanelCandidateStatus', { status: candidate.status })}</p></div>)}</details>:null}
      <LearningMemoryReview key={`memory-${goal.id}`} goalId={goal.id} items={path.memory_candidates || []} onReviewed={()=>setRefresh(x=>x+1)}/>
    </>}
  </section>;
}
