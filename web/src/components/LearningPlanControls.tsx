import { useT } from '../i18n';
import React,{useEffect,useRef,useState} from 'react';
import {api} from '../api/client';
import {learningError} from '../lib/learningPath';

type Action='shorten'|'pause'|'resume'|'skip';
export default function LearningPlanControls({goalId,revision,status,activityId,activityStatus,onChanged}:{goalId:number;revision:number;status:string;activityId?:number;activityStatus?:string;onChanged:()=>void}){
  const t = useT();
  const [action,setAction]=useState<Action>(status==='PAUSED'?'resume':'shorten'),[reason,setReason]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const pending=useRef(false),alive=useRef(true);
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  const inactive=['NEEDS_REVIEW','WITHDRAWN'].includes(status);
  const save=async()=>{
    if(pending.current || !reason.trim() || inactive)return;
    pending.current=true;setBusy(true);setError('');setNotice('');
    try{
      const result=await api.post<{plan_revision:number;mastery_updated:boolean}>(`/learning/goals/${goalId}/adjust`,{action,reason:reason.trim(),expected_plan_revision:revision,...(action==='skip' || action==='resume' && ['DEFERRED','SKIPPED'].includes(activityStatus || '')?{activity_id:activityId}:{})});
      if(!alive.current)return;
      if(!result.plan_revision || result.mastery_updated!==false)throw new Error(t('progress.learningPlanControlsInvalidReceipt'));
      setNotice(t('progress.learningPlanControlsSaved'));setReason('');onChanged();
    }catch(e){if(alive.current)setError(learningError(e));}finally{pending.current=false;if(alive.current)setBusy(false);}
  };
  return <details className="card" style={{marginTop:16}}><summary>{t('progress.learningPlanControlsHeading')}</summary><p className="hint">{t('progress.learningPlanControlsDescription', { revision })}</p>{inactive && <p role="alert">{t('progress.learningPlanControlsStaleSource')}</p>}{error && <p role="alert">{error}{t('progress.learningPlanControlsReasonPreserved')}</p>}{notice && <p role="status">{notice}</p>}<label>{t('progress.learningPlanControlsAdjustment') + ' '}<select aria-label={t('progress.learningPlanControlsAdjustmentLabel')} value={action} disabled={busy || inactive} onChange={e=>setAction(e.target.value as Action)}><option value="shorten">{t('progress.learningPlanControlsShorten')}</option><option value="pause">{t('progress.learningPlanControlsPause')}</option><option value="resume">{t('progress.learningPlanControlsResume')}</option><option value="skip" disabled={!activityId || ['QUEUED','RUNNING'].includes(activityStatus || '')}>{t('progress.learningPlanControlsSkip')}</option></select></label><label style={{display:'block',marginTop:10}}>{t('progress.learningPlanControlsReason')}<textarea className="textarea" rows={3} aria-label={t('progress.learningPlanControlsReasonLabel')} value={reason} disabled={busy || inactive} onChange={e=>setReason(e.target.value)}/></label><button className="btn btn-outline" disabled={busy || inactive || !reason.trim() || action==='skip' && !activityId} onClick={save}>{busy?t('progress.saving'):t('progress.learningPlanControlsSave')}</button></details>;
}
