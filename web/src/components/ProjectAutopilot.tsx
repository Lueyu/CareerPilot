import React,{useEffect,useRef,useState} from 'react';
import {Link} from 'react-router-dom';
import {api} from '../api/client';
import {AutopilotRun,AutopilotSettings,WorkerStatus} from '../lib/projectIntelligence';
import {messageOf} from '../lib/questionWorkshop';

export const RUN_LABELS:Record<string,string>={QUEUED:'已导入，等待分析',RUNNING:'正在了解项目',RETRY_WAIT:'暂时无法继续，进度已保存',COMPLETED:'已完成，可查看报告',PAUSED_USER:'已暂停，进度已保存',PAUSED_BUDGET:'达到自动分析预算，进度已保存',BLOCKED:'暂时无法继续，进度已保存',CANCELLED:'分析任务已取消',FAILED_FINAL:'本次分析未完成，进度已保存'};
const STAGES:Record<string,string>={resolve:'读取仓库版本',manifest_page:'分页盘点项目文件',manifest:'整理文件目录',plan_modules:'划分分析模块',read_source:'阅读模块源码',analyze_module:'分析模块实现与问题',module:'保存模块分析',cross_module:'核对跨模块调用与数据流',explain_section:'整理项目报告章节',report:'汇总并核对完整报告',candidates:'整理待确认的项目记忆',fetch_repo:'读取仓库版本与目录',snapshot:'读取仓库版本与目录',inventory:'梳理目录与模块',select_files:'梳理重点模块',read_initial:'阅读首批源码',followup_files:'寻找关联代码',read_followup:'补读关联代码',review_modules:'分组分析模块与问题',module_analysis:'分组分析模块与问题',final_report:'形成架构与问题报告',synthesis:'汇总项目报告',validate_report:'核对报告与代码引用',memory_candidates:'整理待确认的项目记忆'};
export function ProjectRunStatus({run,worker,compact=false}: {run:AutopilotRun|null;worker?:WorkerStatus;compact?:boolean}) {
  return <div>
    {worker && !worker.online && <p role="status" className="hint">尚未启动本地后台服务。已有进度会保留；启动 Worker 后继续处理。</p>}
    {run && <div role={run.error?'alert':'status'}>
      <p><strong>{run.status==='COMPLETED' && run.coverage_status==='PARTIAL'?'已完成部分分析，仍有内容未覆盖':RUN_LABELS[run.status] || '正在读取任务状态'}</strong></p>
      {!compact && <>
        {run.stage && <p className="hint">{run.error?'停留阶段':'当前阶段'}：{STAGES[run.stage] || '后台处理项目资料'}</p>}
        {run.total_modules>0 && <p className="hint">已保存 {run.completed_modules} / {run.total_modules} 个模块的分析。{run.coverage_status==='PARTIAL'?'当前是部分分析，不能视为完整项目结论。':''}</p>}
      </>}
      {run.error && <p>{run.error.message}</p>}
    </div>}
  </div>;
}

export function ProjectActions({projectId,run,allowedActions,onChanged,onHistory,onRemove}: {projectId:number;run:AutopilotRun|null;allowedActions:string[];onChanged:()=>void|Promise<void>;onHistory?:()=>void;onRemove?:()=>void}) {
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
    {run?.allowed_actions?.includes('resume') && ['PAUSED_USER','PAUSED_BUDGET','BLOCKED','FAILED_FINAL'].includes(run.status) && <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>perform('resume')}>恢复分析</button>}
    <details style={{marginTop:10}}><summary>更多操作</summary><div style={{display:'flex',gap:8,flexWrap:'wrap',marginTop:10}}>
      {allowed('check_updates') && <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>perform('check_updates')}>检查更新</button>}
      {allowed('reanalyze') && <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>perform('reanalyze')}>完整重新分析</button>}
      {run && allowed('pause') && <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>perform('pause')}>暂停分析</button>}
      {run && allowed('resume') && <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>perform('resume')}>继续分析</button>}
      {run && allowed('cancel') && <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>perform('cancel')}>取消本次任务</button>}
      {onHistory && <button className="btn btn-outline btn-sm" disabled={busy} onClick={onHistory}>分析历史</button>}
      {onRemove && allowed('remove') && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={onRemove}>移除项目</button>}
      <Link className="btn btn-ghost btn-sm" to="/settings?tab=rules">自动分析设置</Link>
    </div></details>
    {error && <p role="alert">操作未完成：{error}</p>}
  </div>;
}

export function AutopilotSettingsForm({onSaved,onLoaded,compact=false}: {onSaved?:(settings:AutopilotSettings)=>void;onLoaded?:(settings:AutopilotSettings)=>void;compact?:boolean}) {
  const [config,setConfig]=useState<AutopilotSettings|null>(null);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
  const [ack,setAck]=useState(false);const [calls,setCalls]=useState(32);const [bytes,setBytes]=useState(8388608);const [interval,setIntervalHours]=useState(6);
  useEffect(()=>{let active=true;api.get<AutopilotSettings>('/project-autopilot/settings').then(s=>{if(active){if(!s.budget || typeof s.enabled!=='boolean')throw new Error('自动分析设置响应不完整，请重新打开后重试');setConfig(s);setCalls(s.budget.max_model_calls);setBytes(s.budget.max_source_bytes);setIntervalHours(s.check_interval_hours);onLoaded?.(s);}}).catch(e=>{if(active)setError(messageOf(e));});return()=>{active=false;};},[]);
  const save=async(enabled:boolean)=>{setBusy(true);setError('');try{const next=await api.put<AutopilotSettings>('/project-autopilot/settings',{enabled,acknowledge_source_transfer:ack || !!config?.consented_at,budget:{max_model_calls:calls,max_source_bytes:bytes},check_interval_hours:interval});setConfig(next);onSaved?.(next);}catch(e){setError(messageOf(e));}finally{setBusy(false);}};
  const valid=Number.isInteger(calls) && calls>0 && Number.isInteger(bytes) && bytes>0 && Number.isFinite(interval) && interval>0;
  return <section className={compact?undefined:'card'} style={{padding:compact?0:20}}>
    <h3>项目自动分析</h3>
    {error && <p role="alert">无法保存或读取自动分析设置：{error}</p>}
    {!config && !error && <p role="status">正在读取自动分析设置…</p>}
    {config && <>
      <p>{config.source_notice || '项目源码片段会发送给已配置的模型服务。本地运行不代表源码只留在本机。'}</p>
      <p className="hint">当前模型服务：{config.provider_display_name || '尚未配置模型，请到模型与 Agent 连接设置'}。请仅导入有权交给该服务分析的仓库。不会执行或修改仓库代码。</p>
      {(!compact || !config.enabled) && <>
        {!config.consented_at && <label style={{display:'flex',gap:8,alignItems:'flex-start',margin:'12px 0'}}><input type="checkbox" aria-label="确认源码发送与自动分析预算" checked={ack} onChange={e=>setAck(e.target.checked)}/>我了解源码片段将发送给配置的模型服务，并确认以下自动分析预算。</label>}
        <label className="field"><span>每次分析最多模型调用次数</span><input className="inp" type="number" min={1} aria-label="自动分析模型调用预算" value={calls} onChange={e=>setCalls(Number(e.target.value))}/></label>
        <details><summary>更多预算与检查设置</summary><label className="field"><span>每次分析源码读取上限（字节）</span><input className="inp" type="number" min={1} value={bytes} onChange={e=>setBytes(Number(e.target.value))}/></label><label className="field"><span>本地后台运行时检查更新间隔（小时）</span><input className="inp" type="number" min={1} value={interval} onChange={e=>setIntervalHours(Number(e.target.value))}/></label></details>
        <p className="hint">达到预算后保留进度并暂停，不会自动增加额度。费用取决于配置服务的计价；这里不估算金额。</p>
        <button className="btn btn-primary" disabled={busy || !valid || (!config.consented_at && !ack)} onClick={()=>save(true)}>{busy?'保存中…':config.enabled?'保存自动分析设置':'确认并启用自动分析'}</button>
        {config.enabled && <button className="btn btn-outline" style={{marginLeft:8}} disabled={busy} onClick={()=>save(false)}>关闭自动排队</button>}
      </>}
      {compact && config.enabled && <p className="hint">已启用自动分析，新导入仓库会排队处理。预算可在设置中调整。</p>}
    </>}
  </section>;
}
