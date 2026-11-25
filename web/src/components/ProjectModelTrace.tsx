import {useT} from '../i18n';
import React, {useEffect, useState} from 'react';
import {api} from '../api/client';
import {messageOf} from '../lib/questionWorkshop';
import type {AutopilotRun} from '../lib/projectIntelligence';

interface Step {
  id:number;
  request_meta?:{routing_mode?:string;requested_tier?:string;tier?:string;routing_reason?:string;task?:string;model?:string;reported_model?:string};
  usage?:{total_tokens?:number};
}
const routingReasonKeys = new Map([
  ['校验失败，按诊断修复结构化结果','projects.routingRepair'],
  ['分析项目实现与证据','projects.routingEvidence'],
  ['整理清单或提取候选记录','projects.routingInventory'],
]);
const routingFallbackSuffix = '；首选档位未配置，使用另一已配置档位';

function routingReason(reason: string, t: ReturnType<typeof useT>) {
  const fallback = reason.endsWith(routingFallbackSuffix);
  const key = routingReasonKeys.get(fallback ? reason.slice(0, -routingFallbackSuffix.length) : reason);
  return key ? t(fallback ? 'projects.routingFallback' : 'projects.routingReason', {reason: t(key)}) : reason;
}

const taskNames: Record<string,string> = {
  plan_modules:'projects.tracePlanModules', analyze_module:'projects.traceAnalyzeModule', verify_cross_module:'projects.traceCrossModule',
  explain_project_section:'projects.traceWriteReport', extract_memory_candidates:'projects.traceExtractMemory', analyze_change_impact:'projects.traceAnalyzeChanges',
};
const tierNames: Record<string,string> = {flash:'projects.fastTier',strong:'projects.deepTier'};
export default function ProjectModelTrace({run}: {run:AutopilotRun}) {
  const t = useT();
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
  return <details className="dossier-trace" onToggle={e=>setOpen(e.currentTarget.open)}><summary>{t('projects.modelRoutingAndUsage')}</summary>
    {open && (error ? <p role="alert">{error}</p> : steps === undefined ? <p role="status">{t('projects.loadingModelCalls')}</p> : steps.length ?
      <ul>{steps.map(s=><li key={s.id}><strong>{s.request_meta?.task && !taskNames[s.request_meta.task] ? s.request_meta.task : t(taskNames[s.request_meta?.task || ''] || 'projects.modelCall')}</strong> · {t(tierNames[s.request_meta?.tier || ''] || 'projects.historicalCall')}<br/>
        {t('projects.modelCallUsage', {requested: s.request_meta?.model ?? '', reported: s.request_meta?.reported_model || t('projects.modelNotReported'), usage: s.usage?.total_tokens != null ? t('projects.tokenUsage', {count: s.usage.total_tokens}) : t('projects.usageUnknown')})}
        {s.request_meta?.routing_reason && <p>{routingReason(s.request_meta.routing_reason, t)}</p>}</li>)}</ul> : <p>{t('projects.noModelCalls')}</p>)}
  </details>;
}
