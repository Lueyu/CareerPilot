import React, {useEffect, useState} from 'react';
import {api} from '../api/client';
import {messageOf} from '../lib/questionWorkshop';
import type {AutopilotRun} from '../lib/projectIntelligence';

interface Step {
  id:number;
  request_meta?:{routing_mode?:string;requested_tier?:string;tier?:string;routing_reason?:string;task?:string;model?:string;reported_model?:string};
  usage?:{total_tokens?:number};
}
const taskNames: Record<string,string> = {
  plan_modules:'规划阅读范围', analyze_module:'分析源码', verify_cross_module:'核对跨模块依据',
  explain_project_section:'撰写报告章节', extract_memory_candidates:'提取待确认记忆', analyze_change_impact:'分析版本变化',
};
const tierNames: Record<string,string> = {flash:'快速档',strong:'深入档'};
export default function ProjectModelTrace({run}: {run:AutopilotRun}) {
  const [open,setOpen] = useState(false);
  const [steps,setSteps] = useState<Step[]>();
  const [error,setError] = useState('');
  useEffect(() => {
    if (!open) return;
    let alive=true;
    api.get<{id:number;steps:Step[]}>('/analysis-runs/' + run.id).then(value=>{
      if (alive && value.id === run.id) {setSteps(value.steps.filter(s => s.request_meta?.model));setError('');}
    }).catch(e=>{if(alive)setError(messageOf(e));});
    return ()=>{alive=false;};
  },[open,run.id,run.updated_at]);
  return <details className="dossier-trace" onToggle={e=>setOpen(e.currentTarget.open)}><summary>查看模型路由与用量</summary>
    {open && (error ? <p role="alert">{error}</p> : steps === undefined ? <p role="status">正在读取调用记录…</p> : steps.length ?
      <ul>{steps.map(s=><li key={s.id}><strong>{taskNames[s.request_meta?.task || ''] || s.request_meta?.task || '模型调用'}</strong> · {tierNames[s.request_meta?.tier || ''] || '历史调用'}<br/>
        请求模型 {s.request_meta?.model} · 服务返回 {s.request_meta?.reported_model || '未报告'} · {s.usage?.total_tokens != null ? s.usage.total_tokens + ' tokens' : '用量未知'}
        {s.request_meta?.routing_reason && <p>{s.request_meta.routing_reason}</p>}</li>)}</ul> : <p>本次任务还没有已记录的模型调用。</p>)}
  </details>;
}
