import {t, useT} from '../i18n';
import React,{useEffect,useRef,useState} from 'react';
import {Link} from 'react-router-dom';
import {api} from '../api/client';
import {AutopilotRun,AutopilotSettings,WorkerStatus,PROJECT_SOURCE_NOTICE,projectProviderName} from '../lib/projectIntelligence';
import {messageOf} from '../lib/questionWorkshop';

const RUN_LABEL_KEYS:Record<string,string>={WAITING_WORKER:'projects.waitingWorker',QUEUED:'projects.queuedAnalysis',RUNNING:'projects.runningAnalysis',RETRY_WAIT:'projects.analysisInterrupted',COMPLETED:'projects.analysisCompleted',PAUSED_USER:'projects.analysisPaused',PAUSED_BUDGET:'projects.analysisBudgetReached',BLOCKED:'projects.analysisInterrupted',CANCELLED:'projects.analysisCancelled',FAILED_FINAL:'projects.analysisFailed'};
export function projectRunStatusLabel(status: string) {
  const key = RUN_LABEL_KEYS[status];
  return key ? t(key) : undefined;
}

export function projectRunLabel(run: AutopilotRun) {
  if (run.report_id === run.id && run.coverage_status === 'COMPLETE' && run.status !== 'COMPLETED') {
    const preparing = ['RUNNING', 'QUEUED', 'RETRY_WAIT'].includes(run.status);
    return preparing ? t('projects.reportPreparingMemories') : t('projects.reportMissingMemories');
  }
  if (run.status === 'COMPLETED' && run.coverage_status === 'PARTIAL') return t('projects.analysisPartial');
  return projectRunStatusLabel(run.status) || t('projects.loadingRun');
}
const STAGES:Record<string,string>={resolve:'projects.stageResolve',manifest_page:'projects.stageManifestPage',manifest:'projects.stageManifest',plan_modules:'projects.stagePlanModules',read_source:'projects.stageReadSource',analyze_module:'projects.stageAnalyzeModule',module:'projects.stageSaveModule',cross_module:'projects.stageCrossModule',explain_section:'projects.stageExplainSection',report:'projects.stageReport',candidates:'projects.stageCandidates',fetch_repo:'projects.stageRepoTree',snapshot:'projects.stageRepoTree',inventory:'projects.stageInventory',select_files:'projects.stageSelectFiles',read_initial:'projects.stageInitialRead',followup_files:'projects.stageFindRelated',read_followup:'projects.stageReadRelated',review_modules:'projects.stageReviewModules',module_analysis:'projects.stageReviewModules',final_report:'projects.stageFinalReport',synthesis:'projects.stageSynthesis',validate_report:'projects.stageValidateReport',memory_candidates:'projects.stageCandidates'};
export function ProjectRunStatus({run,worker,compact=false}: {run:AutopilotRun|null;worker?:WorkerStatus;compact?:boolean}) {
  const t = useT();
  return <div>
    {worker && !worker.online && <p role="status" className="hint">{t('projects.workerNotStarted')}</p>}
    {run && <div role={run.error?'alert':'status'}>
      <p><strong>{projectRunLabel(run)}</strong></p>
      {!compact && <>
        {run.stage && <p className="hint">{t(run.error ? 'projects.stageStoppedAt' : 'projects.stageCurrent', {stage: t(STAGES[run.stage] || 'projects.stageBackground')})}</p>}
        {run.total_modules>0 && <p className="hint">{t(run.coverage_status === 'PARTIAL' ? 'projects.partialModulesSaved' : 'projects.modulesSaved', {completed: run.completed_modules, total: run.total_modules})}</p>}
      </>}
      {run.error && <p>{run.error.message}</p>}
    </div>}
  </div>;
}

export function ProjectActions({projectId,run,allowedActions,onChanged,onHistory,onRemove}: {projectId:number;run:AutopilotRun|null;allowedActions:string[];onChanged:()=>void|Promise<void>;onHistory?:()=>void;onRemove?:()=>void}) {
  const t = useT();
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');const pending=useRef(false);const fullKey=useRef('');
  const allowed=(action:string)=>allowedActions.includes(action) || !!run?.allowed_actions?.includes(action);
  const perform=async(action:string)=>{
    if(pending.current)return;pending.current=true;setBusy(true);setError('');
    try{
      if(action==='reanalyze'){if(!fullKey.current)fullKey.current=crypto.randomUUID();await api.post(`/projects/${projectId}/reanalyze`,{mode:'full',request_key:fullKey.current});fullKey.current='';}
      else if(action==='check_updates')await api.post(`/projects/${projectId}/check-updates`,{});
      else if(run)await api.post(`/analysis-runs/${run.id}/${action}`,{});
      await onChanged();
    }catch(e){setError(messageOf(e));}finally{pending.current=false;setBusy(false);}
  };
  return <div>
    {run?.allowed_actions?.includes('resume') && ['PAUSED_USER','PAUSED_BUDGET','BLOCKED','FAILED_FINAL'].includes(run.status) && <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>perform('resume')}>{t('projects.resumeAnalysis')}</button>}
    <details style={{marginTop:10}}><summary>{t('projects.moreActions')}</summary><div style={{display:'flex',gap:8,flexWrap:'wrap',marginTop:10}}>
      {allowed('check_updates') && <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>perform('check_updates')}>{t('projects.checkUpdates')}</button>}
      {allowed('reanalyze') && <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>perform('reanalyze')}>{t('projects.reanalyze')}</button>}
      {run && allowed('pause') && <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>perform('pause')}>{t('projects.pauseAnalysis')}</button>}
      {run && allowed('resume') && <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>perform('resume')}>{t('projects.continueAnalysis')}</button>}
      {run && allowed('cancel') && <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>perform('cancel')}>{t('projects.cancelRun')}</button>}
      {onHistory && <button className="btn btn-outline btn-sm" disabled={busy} onClick={onHistory}>{t('projects.analysisHistory')}</button>}
      {onRemove && allowed('remove') && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={onRemove}>{t('projects.removeProject')}</button>}
      <Link className="btn btn-ghost btn-sm" to="/settings?tab=rules">{t('projects.autopilotSettings')}</Link>
    </div></details>
    {error && <p role="alert">{t('projects.actionFailed', {error})}</p>}
  </div>;
}

export function AutopilotSettingsForm({onSaved,onLoaded,compact=false}: {onSaved?:(settings:AutopilotSettings)=>void;onLoaded?:(settings:AutopilotSettings)=>void;compact?:boolean}) {
  const t = useT();
  const [config,setConfig]=useState<AutopilotSettings|null>(null);const [error,setError]=useState('');
  const [invalidResponse, setInvalidResponse] = useState(false);const [busy,setBusy]=useState(false);
  const [ack,setAck]=useState(false);const [calls,setCalls]=useState('');const [bytes,setBytes]=useState('');const [interval,setIntervalHours]=useState(6);
  useEffect(()=>{let active=true;api.get<AutopilotSettings>('/project-autopilot/settings').then(s=>{if(active){if(!s.budget || typeof s.enabled!=='boolean'){setInvalidResponse(true);return;}setConfig(s);setCalls(String(s.budget.max_model_calls ?? ''));setBytes(String(s.budget.max_source_bytes ?? ''));setIntervalHours(s.check_interval_hours);onLoaded?.(s);}}).catch(e=>{if(active)setError(messageOf(e));});return()=>{active=false;};},[]);
  const save=async(enabled:boolean)=>{setBusy(true);setError('');try{const next=await api.put<AutopilotSettings>('/project-autopilot/settings',{enabled,acknowledge_source_transfer:ack || !!config?.consented_at,budget:{max_model_calls:calls ? Number(calls) : null,max_source_bytes:bytes ? Number(bytes) : null},check_interval_hours:interval});setConfig(next);onSaved?.(next);}catch(e){setError(messageOf(e));}finally{setBusy(false);}};
  const valid=[calls,bytes].every(raw=>raw === '' || (Number.isSafeInteger(Number(raw)) && Number(raw)>0)) && Number.isFinite(interval) && interval>0;
  return <section className={compact?undefined:'card'} style={{padding:compact?0:20}}>
    <h3>{t('projects.autopilotHeading')}</h3>
    {(error || invalidResponse) && <p role="alert">{t('projects.autopilotSettingsFailed', {error: invalidResponse ? t('projects.invalidAutopilotSettings') : error})}</p>}
    {!config && !error && !invalidResponse && <p role="status">{t('projects.loadingAutopilot')}</p>}
    {config && <>
      <p>{config.source_notice === PROJECT_SOURCE_NOTICE ? t('projects.configuredSourceTransferNotice') : config.source_notice || t('projects.sourceTransferNotice')}</p>
      <p className="hint">{t('projects.providerAuthorization', {provider: projectProviderName(config) || t('projects.providerUnconfigured')})}</p>
      {(!compact || !config.enabled) && <>
        {!config.consented_at && <label style={{display:'flex',gap:8,alignItems:'flex-start',margin:'12px 0'}}><input type="checkbox" aria-label={t('projects.sourceBudgetConsentLabel')} checked={ack} onChange={e=>setAck(e.target.checked)}/>{t('projects.sourceBudgetConsent')}</label>}
        <details><summary>{t('projects.budgetAndUpdates')}</summary><p className="hint">{t('projects.budgetBlank')}</p>
          <label className="field"><span>{t('projects.budgetModelCalls')}</span><input className="inp" type="number" min={1} placeholder={t('projects.unlimited')} aria-label={t('projects.budgetCallsLabel')} value={calls} onChange={e=>setCalls(e.target.value)}/></label>
          <label className="field"><span>{t('projects.budgetSourceBytes')}</span><input className="inp" type="number" min={1} placeholder={t('projects.unlimited')} value={bytes} onChange={e=>setBytes(e.target.value)}/></label><label className="field"><span>{t('projects.updateInterval')}</span><input className="inp" type="number" min={1} value={interval} onChange={e=>setIntervalHours(Number(e.target.value))}/></label></details>
        <p className="hint">{t('projects.budgetPauseNotice')}</p>
        <button className="btn btn-primary" disabled={busy || !valid || (!config.consented_at && !ack)} onClick={()=>save(true)}>{busy?t('projects.saving'):config.enabled?t('projects.saveAutopilot'):t('projects.enableAutopilot')}</button>
        {!config.enabled && <button className="btn btn-outline" disabled={busy || !valid} onClick={()=>save(false)}>{t('projects.saveBudgetOnly')}</button>}
        {config.enabled && <button className="btn btn-outline" style={{marginLeft:8}} disabled={busy} onClick={()=>save(false)}>{t('projects.disableAutoQueue')}</button>}
      </>}
      {compact && config.enabled && <p className="hint">{t('projects.autoQueueEnabled')}</p>}
    </>}
  </section>;
}
