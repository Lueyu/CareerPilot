import React,{useEffect,useRef,useState} from 'react';
import {api} from '../api/client';
import {learningError} from '../lib/learningPath';

type Action='shorten'|'pause'|'resume'|'skip';
export default function LearningPlanControls({goalId,revision,status,activityId,activityStatus,onChanged}:{goalId:number;revision:number;status:string;activityId?:number;activityStatus?:string;onChanged:()=>void}){
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
      if(!result.plan_revision || result.mastery_updated!==false)throw new Error('未收到有效的计划调整回执，请刷新核对');
      setNotice('这次安排已调整；能力与验证结果未因此改变。');setReason('');onChanged();
    }catch(e){if(alive.current)setError(learningError(e));}finally{pending.current=false;if(alive.current)setBusy(false);}
  };
  return <details className="card" style={{marginTop:16}}><summary>调整当前计划</summary><p className="hint">可缩短、暂停或跳过本次安排，原因会留在计划历史中；跳过不会标记掌握。计划版本 {revision}。</p>{inactive && <p role="alert">来源需要复核，暂不能调整或继续提交。</p>}{error && <p role="alert">{error}。原因已保留；请刷新路径核对版本后再提交。</p>}{notice && <p role="status">{notice}</p>}<label>调整方式 <select aria-label="学习计划调整方式" value={action} disabled={busy || inactive} onChange={e=>setAction(e.target.value as Action)}><option value="shorten">今天少做一点</option><option value="pause">暂停这条学习路径</option><option value="resume">恢复路径或当前延后活动</option><option value="skip" disabled={!activityId || ['QUEUED','RUNNING'].includes(activityStatus || '')}>跳过当前活动</option></select></label><label style={{display:'block',marginTop:10}}>说明这次调整原因<textarea className="textarea" rows={3} aria-label="学习计划调整原因" value={reason} disabled={busy || inactive} onChange={e=>setReason(e.target.value)}/></label><button className="btn btn-outline" disabled={busy || inactive || !reason.trim() || action==='skip' && !activityId} onClick={save}>{busy?'正在保存…':'保存这次调整'}</button></details>;
}
