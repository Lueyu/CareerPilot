import React,{useCallback,useEffect,useRef,useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {useAppState} from '../components/AppState';
import {Modal,PageHead} from '../components/ui';
import {api} from '../api/client';
import {AutopilotSettings,AutopilotSummary} from '../lib/projectIntelligence';
import {AutopilotSettingsForm,ProjectActions,ProjectRunStatus} from '../components/ProjectAutopilot';
import {messageOf} from '../lib/questionWorkshop';

interface ProjectView {id:number;kind?:string;name:string;repo_url:string;owner:string;repo:string;branch?:string;autopilot?:AutopilotSummary}
export default function Assets(){
  const navigate=useNavigate();const {toast}=useAppState();
  const [projects,setProjects]=useState<ProjectView[]>([]);const [error,setError]=useState('');const [loading,setLoading]=useState(true);
  const [config,setConfig]=useState<AutopilotSettings|null>(null);const [settingsError,setSettingsError]=useState('');
  const [importOpen,setImportOpen]=useState(false);const [name,setName]=useState('');const [url,setUrl]=useState('');const [branch,setBranch]=useState('');const [importing,setImporting]=useState(false);const [importError,setImportError]=useState('');
  const [removing,setRemoving]=useState<ProjectView|null>(null);const [removeBusy,setRemoveBusy]=useState(false);const [removeError,setRemoveError]=useState('');const [syncing,setSyncing]=useState(false);
  const pending=useRef(false);const version=useRef(0);
  const reload=useCallback(async()=>{const token=++version.current;try{const data=await api.get<{items:ProjectView[]}>('/projects');if(token===version.current){setProjects(data.items);setError('');}}catch(e){if(token===version.current)setError(messageOf(e));}finally{if(token===version.current)setLoading(false);}},[]);
  useEffect(()=>{void reload();api.get<AutopilotSettings>('/project-autopilot/settings').then(setConfig).catch(e=>setSettingsError(messageOf(e)));return()=>{version.current++;};},[reload]);
  useEffect(()=>{const timer=setInterval(()=>{void reload();},5000);return()=>clearInterval(timer);},[reload]);
  const doImport=async()=>{
    if(pending.current || !url.trim() || !config?.enabled)return;pending.current=true;setImporting(true);setImportError('');
    try{await api.post('/projects',{repo_url:url.trim(),...(name.trim()?{name:name.trim()}:{}),...(branch.trim()?{branch:branch.trim()}: {})});setImportOpen(false);setName('');setUrl('');setBranch('');toast('✓','项目已导入，后台任务已登记。可在项目卡查看进度。');await reload();}
    catch(e){setImportError(messageOf(e));}finally{pending.current=false;setImporting(false);}
  };
  const remove=async()=>{if(!removing || removeBusy)return;setRemoveBusy(true);setRemoveError('');try{await api.del('/projects/'+removing.id);setRemoving(null);await reload();}catch(e){setRemoveError(messageOf(e));}finally{setRemoveBusy(false);}};
  const syncAssets=async()=>{setSyncing(true);try{const r=await api.post<{synced:string[];errors:string[]}>('/career-assets/sync');toast(r.errors.length?'⚠️':'✓','线上资产同步完成：'+r.synced.length+' 个文件'+(r.errors.length?'，'+r.errors.length+' 个失败':''));}catch(e){toast('⚠️',messageOf(e));}finally{setSyncing(false);}};
  const runPipeline=async()=>{try{await api.post('/career-assets/updates/run');navigate('/assets/pipeline');}catch(e){toast('⚠️',messageOf(e));}};
  return <div>
    <PageHead title="项目与职业资产" sub="导入项目后自动理解代码，核对报告并确认记忆，再用于面试准备"><button className="btn btn-primary" onClick={()=>{setImportError('');setImportOpen(true);}}>＋ 导入仓库</button><button className="btn btn-outline" onClick={()=>navigate('/bank/workshop')}>登记个人项目经历</button></PageHead>
    {loading && <p role="status">正在读取项目…</p>}{error && <div role="alert">无法读取最新项目状态：{error}<button className="btn btn-ghost btn-sm" onClick={()=>void reload()}>重试</button></div>}
    {config && !config.enabled && !importOpen && <details className="card" style={{padding:18,marginBottom:18}}><summary>启用项目自动分析</summary><AutopilotSettingsForm onSaved={s=>{setConfig(s);void reload();}}/></details>}
    {!loading && !error && !projects.length && <div className="card" style={{padding:30,textAlign:'center'}}><h3>还没有导入项目</h3><p>填写仓库链接即可排队分析。无需选择文件或编写分析指令，职责与经历可以之后补充。</p><button className="btn btn-primary" onClick={()=>setImportOpen(true)}>＋ 导入第一个仓库</button></div>}
    <div className="grid grid-2">{projects.map(p=><article className="card" key={p.id}>
      <div className="card-head"><div><div className="card-title">{p.name}</div><div className="card-sub">{p.repo_url || '本人填写的项目经历'}</div></div></div>
      {p.kind==='manual'?<><span className="chip chip-slate">手工项目</span><p className="hint">本人登记的项目，可补充职责与经历用于面试回答。</p><button className="btn btn-primary" onClick={()=>navigate('/bank/workshop?project='+p.id)}>编辑经历与准备面试</button></>:<>
        <p><strong>{p.autopilot?.user_status || '正在读取自动分析状态'}</strong></p>
        <p>{p.autopilot?.latest_valid_report?.summary || '正在了解项目'}</p>
        <ProjectRunStatus run={null} worker={p.autopilot?.worker} compact/>
        {p.autopilot?.latest_valid_report && <p className="hint">最近有效报告：{p.autopilot.latest_valid_report.as_of?new Date(p.autopilot.latest_valid_report.as_of).toLocaleString():'时间未记录'} · {p.autopilot.latest_valid_report.freshness==='CURRENT'?'对应最近检查的代码版本':p.autopilot.latest_valid_report.freshness==='UNKNOWN'?'新鲜度尚未确认':'代码已有变化，报告待更新'}</p>}
        <div style={{display:'flex',gap:10,flexWrap:'wrap'}}><button className="btn btn-primary" onClick={()=>navigate('/assets/project/'+p.id)}>查看项目报告</button><button className="btn btn-outline" onClick={()=>navigate('/assets/project/'+p.id+'#project-memory-candidates')}>确认记忆（{p.autopilot?.pending_candidate_count || 0}）</button></div>
        <ProjectActions projectId={p.id} run={p.autopilot?.latest_run || null} allowedActions={p.autopilot?.allowed_actions || []} onChanged={reload} onHistory={()=>navigate('/assets/project/'+p.id+'?history=1')} onRemove={()=>{setRemoveError('');setRemoving(p);}}/>
      </>}
    </article>)}</div>
    <details className="card mt-18" style={{padding:18}}><summary>职业展示与本地草稿</summary><p className="hint">根据项目资料生成职业展示草稿。公共发布保持关闭。</p><div style={{display:'flex',gap:10,flexWrap:'wrap'}}><button className="btn btn-outline" disabled={syncing} onClick={syncAssets}>{syncing?'同步中…':'同步线上资产'}</button><button className="btn btn-outline" onClick={runPipeline}>更新职业资产草稿</button><button className="btn btn-ghost" onClick={()=>navigate('/assets/pipeline')}>查看流水线状态</button></div></details>
    {importOpen && <Modal title="导入仓库（GitHub / GitCode）" onClose={()=>{if(!importing)setImportOpen(false);}} footer={<><button className="btn btn-outline" disabled={importing} onClick={()=>setImportOpen(false)}>取消</button><button className="btn btn-primary" disabled={importing || !url.trim() || !config?.enabled} onClick={doImport}>{importing?'导入中…':'导入项目'}</button></>}>
      <label className="field"><span>仓库地址</span><input className="inp" aria-label="仓库地址" placeholder="https://github.com/owner/repo 或 https://gitcode.com/owner/repo" value={url} onChange={e=>setUrl(e.target.value)}/></label>
      <details><summary>名称与跟踪分支（可选）</summary><label className="field"><span>项目名称</span><input className="inp" value={name} onChange={e=>setName(e.target.value)} placeholder="留空使用仓库名称"/></label><label className="field"><span>跟踪分支</span><input className="inp" value={branch} onChange={e=>setBranch(e.target.value)} placeholder="留空使用仓库实际默认分支"/></label></details>
      <p className="hint">读取远端已提交的代码，不包含本机未推送改动。后台服务运行时会自动处理；关闭页面不影响任务。</p>
      {!config?.enabled && <AutopilotSettingsForm compact onLoaded={s=>{setConfig(s);setSettingsError('');}} onSaved={s=>{setConfig(s);setSettingsError('');void reload();}}/>}
      {config?.enabled && <p className="hint">已启用自动分析，导入后按设置的预算排队。个人职责可稍后补充。</p>}
      {settingsError && !config && <p role="alert">自动分析设置尚未读取：{settingsError}</p>}{importError && <p role="alert">导入未完成：{importError}</p>}
    </Modal>}
    {removing && <Modal title="移除项目" onClose={()=>{if(!removeBusy)setRemoving(null);}} footer={<><button className="btn btn-outline" disabled={removeBusy} onClick={()=>setRemoving(null)}>保留项目</button><button className="btn btn-primary" disabled={removeBusy} onClick={remove}>移除项目</button></>}><p>将「{removing.name}」移出项目列表，停止自动分析并停用当前项目记忆。历史报告、确认记录和已经创建的题目与答卷会保留。</p>{removeError && <p role="alert">{removeError}</p>}</Modal>}
  </div>;
}
