import {useT} from '../i18n';
import React,{useEffect,useRef,useState} from 'react';
import {ProjectCopyError} from '../lib/projectIntelligence';
import {api} from '../api/client';

type Category='QUESTION_AMBIGUITY'|'RUBRIC_AMBIGUITY'|'REFERENCE_ISSUE'|'OTHER';
interface Feedback {id:number;status:'PENDING_REVIEW';reason:string;category:Category}
function feedbackError(error: unknown, t: ReturnType<typeof useT>) {
  return error instanceof ProjectCopyError ? t(error.localeKey) : error instanceof Error ? error.message : String(error);
}
const categories:Record<Category,string>={QUESTION_AMBIGUITY:'projects.unclearQuestion',RUBRIC_AMBIGUITY:'projects.unclearRubric',REFERENCE_ISSUE:'projects.referenceIssue',OTHER:'projects.otherQuestionIssue'};

export default function QuestionAmbiguityFeedback({examId,questionId}:{examId:number;questionId:number}){
  const t = useT();
  const [reason,setReason]=useState(''),[category,setCategory]=useState<Category>('QUESTION_AMBIGUITY');
  const [items,setItems]=useState<Feedback[]>([]),[error,setError]=useState<{detail: unknown}>(),[busy,setBusy]=useState(false);
  const [historyError,setHistoryError]=useState<{detail: unknown}>(),[reload,setReload]=useState(0);
  const pending=useRef(false),alive=useRef(true),request=useRef<{content:string;key:string}|null>(null),submitted=useRef(false);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  useEffect(()=>{let current=true;setHistoryError(undefined);
    api.get<{items:Feedback[]}>(`/assessment-feedback?exam_id=${examId}&exam_question_id=${questionId}`)
      .then(data=>{if(current && !submitted.current)setItems(data.items || []);})
      .catch(e=>{if(current)setHistoryError({detail: e});});
    return()=>{current=false;};
  },[examId,questionId,reload]);
  const submit=async()=>{
    const clean=reason.trim();if(pending.current || clean.length<5)return;
    pending.current=true;setBusy(true);setError(undefined);
    const content=JSON.stringify([category,clean]);
    if(request.current?.content!==content)request.current={content,key:globalThis.crypto?.randomUUID?.() || `feedback-${Date.now()}-${Math.random().toString(36).slice(2)}`};
    try{
      const saved=await api.post<Feedback>(`/exams/${examId}/questions/${questionId}/feedback`,{category,reason:clean,request_key:request.current.key});
      if(saved.status!=='PENDING_REVIEW' || !saved.id)throw new ProjectCopyError('projects.missingSavedFeedback');
      if(!alive.current)return;
      submitted.current=true;setItems(old=>[saved,...old.filter(item=>item.id!==saved.id)]);setReason('');request.current=null;
    }catch(e){if(alive.current)setError({detail: e});}
    finally{pending.current=false;if(alive.current)setBusy(false);}
  };
  return <section style={{marginTop:16,paddingTop:12,borderTop:'1px dashed var(--border-2)'}} aria-label={t('projects.questionAmbiguityFeedback')}>
    <div style={{fontSize:13,fontWeight:650}}>{t('projects.unclearQuestionOrRubric')}</div>
    <p className="hint">{t('projects.feedbackReviewGuidance')}</p>
    {historyError && <p role="alert">{t('projects.feedbackHistoryError', {error: feedbackError(historyError.detail, t)})} <button className="btn btn-outline btn-sm" onClick={()=>setReload(x=>x+1)}>{t('projects.reloadFeedback')}</button></p>}
    {items.map(item=><div key={item.id} role="status" style={{margin:'8px 0'}}><b>{t('projects.feedbackPending')}</b> · {t(categories[item.category] || 'projects.questionFeedback')}<p style={{whiteSpace:'pre-wrap',margin:'4px 0'}}>{item.reason}</p></div>)}
    <label>{t('projects.issueType')} <select aria-label={t('projects.feedbackCategory')} value={category} disabled={busy} onChange={e=>setCategory(e.target.value as Category)}>{Object.entries(categories).map(([key,label])=><option key={key} value={key}>{t(label)}</option>)}</select></label>
    <textarea aria-label={t('projects.feedbackReason')} rows={3} maxLength={2000} value={reason} disabled={busy} onChange={e=>setReason(e.target.value)} placeholder={t('projects.feedbackPlaceholder')} style={{display:'block',width:'100%',margin:'8px 0'}}/>
    {error && <p role="alert">{t('projects.feedbackNotSaved', {error: feedbackError(error.detail, t)})}</p>}
    <button className="btn btn-outline btn-sm" disabled={busy || reason.trim().length<5} onClick={submit}>{busy?t('projects.savingFeedback'):t('projects.submitQuestionFeedback')}</button>
  </section>;
}
