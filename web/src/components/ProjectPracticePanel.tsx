import React,{useEffect,useRef,useState} from 'react';
import {api} from '../api/client';
import {LearningActivity,LearningSubmission,LearningSubmissionAck,LEARNING_PENDING,learningError} from '../lib/learningPath';

type Source='files'|'patch'|'github_commit';
interface FileInput {path:string;content:string|null}
interface ProjectPreview {snapshot_id:number;content_hash:string;baseline_revision:string;submission_revision:string|null;files:{path:string;diff:string;before_hash?:string;after_hash?:string}[];excluded_paths:string[];test_evidence:unknown;limits?:Record<string,unknown>}
interface ProjectOption {id:number;name:string;analysis_id:number;baseline_revision:string;options:{id:string;kind:string;title:string;paths:string[]}[]}
const freshKey=()=>globalThis.crypto?.randomUUID?.() || `project-practice-${Date.now()}-${Math.random().toString(36).slice(2)}`;

export default function ProjectPracticePanel({activity,latest,onSubmitted}:{activity:LearningActivity;latest:LearningSubmission|null;onSubmitted:(ack:LearningSubmissionAck)=>void}){
  const [source,setSource]=useState<Source>('files'),[files,setFiles]=useState<FileInput[]>([]),[patch,setPatch]=useState(''),[commit,setCommit]=useState(''),[explanation,setExplanation]=useState(''),[logs,setLogs]=useState(''),[ai,setAi]=useState(false);
  const [preview,setPreview]=useState<ProjectPreview|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[submitted,setSubmitted]=useState(false);
  const alive=useRef(true),pending=useRef(false),request=useRef<{snapshot:number;key:string}|null>(null);
  const contract=activity.project_contract;
  const running=!!latest && LEARNING_PENDING.has(latest.status);
  const disabled=busy || running || !activity.allowed_actions.includes('submit');
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  const invalidate=()=>{setPreview(null);request.current=null;setSubmitted(false);setError('');};
  const perform=async(action:()=>Promise<void>)=>{if(pending.current)return;pending.current=true;setBusy(true);setError('');try{await action();}catch(e){if(alive.current)setError(learningError(e));}finally{pending.current=false;if(alive.current)setBusy(false);}};
  const chooseFiles=async(list:FileList|null)=>{
    invalidate();if(!list)return;
    await perform(async()=>{
      const selected=Array.from(list);
      if(selected.length>12 || selected.reduce((sum,f)=>sum+f.size,0)>3*1024*1024)throw new Error('请先缩小本次提交：最多 12 个文本文件，每个不超过 60,000 字符。');
      const values=await Promise.all(selected.map(async(file)=>({path:file.webkitRelativePath || file.name,content:await file.text()})));
      if(values.some(file=>(file.content?.length || 0)>60000))throw new Error('每个文件最多 60,000 字符，请缩小提交范围。');
      if(alive.current)setFiles(values);
    });
  };
  const makePreview=()=>perform(async()=>{
    if(!contract)return;
    const body={base_revision:contract.baseline_revision,source_kind:source,explanation,test_logs:logs,
      ...(source==='files'?{files}:source==='patch'?{patch}:{commit_sha:commit.trim()})};
    const value=await api.post<ProjectPreview>(`/learning/activities/${activity.id}/project-preview`,body);
    if(!alive.current)return;
    if(!value.snapshot_id || !Array.isArray(value.files))throw new Error('未收到可核对的提交快照，请重试');
    setPreview(value);setSubmitted(false);request.current={snapshot:value.snapshot_id,key:freshKey()};
  });
  const submit=()=>perform(async()=>{
    if(!preview)return;
    if(request.current?.snapshot!==preview.snapshot_id)request.current={snapshot:preview.snapshot_id,key:freshKey()};
    const result=await api.post<LearningSubmissionAck>(`/learning/activities/${activity.id}/project-submit`,{snapshot_id:preview.snapshot_id,request_key:request.current.key,expected_activity_revision:activity.revision,parent_submission_id:latest?.id || null,assistance:{...activity.assistance,ai_declared:ai}});
    if(!alive.current)return;if(!result.submission_id)throw new Error('没有收到本次提交编号，快照和输入已保留');
    setSubmitted(true);onSubmitted(result);
  });
  if(!contract)return <p role="alert">项目活动缺少固定基线和约定范围，当前不能提交；请核对活动来源。</p>;
  const canPreview=source==='files'?files.length>0 && files.every(f=>f.path.trim()):source==='patch'?!!patch.trim():!!commit.trim();
  return <section aria-label="项目实践提交" style={{marginTop:14}}>
    <div className="demo-banner"><div><b>固定项目基线</b> <code>{contract.baseline_revision}</code><p>允许范围：{contract.allowed_paths.join('、') || '尚未指定'}</p><p>验收标准：{Array.isArray(contract.acceptance)?contract.acceptance.join('；'):contract.acceptance}</p><p className="hint">只读检查提交内容，不会执行项目脚本或改写你的工作区。远端提交不包含尚未推送的本地改动。</p></div></div>
    {error && <p role="alert">{error}。本次输入已保留。</p>}
    <label>提交方式 <select aria-label="项目提交方式" value={source} disabled={disabled} onChange={e=>{setSource(e.target.value as Source);invalidate();}}><option value="files">显式选择本地文件</option><option value="patch">粘贴 patch</option><option value="github_commit">已推送的 GitHub commit</option></select></label>
    {source==='files' && <div><p className="hint">只读取你通过下方选择的文件。核对每个文件的仓库相对路径，再预览；未选择的本地内容不会被读取。</p><input type="file" multiple aria-label="选择本地提交文件" disabled={disabled} onChange={e=>void chooseFiles(e.target.files)}/>{files.map((file,index)=><div key={index} style={{margin:'8px 0'}}><label>仓库路径 <input aria-label={`文件 ${index+1} 的仓库路径`} value={file.path} disabled={disabled} onChange={e=>{setFiles(old=>old.map((f,i)=>i===index?{...f,path:e.target.value}:f));invalidate();}}/></label><span className="hint"> {file.content?.length ?? 0} 字符</span><button className="btn btn-ghost btn-sm" disabled={disabled} onClick={()=>{setFiles(old=>old.filter((_,i)=>i!==index));invalidate();}}>移除此文件</button></div>)}</div>}
    {source==='patch' && <label style={{display:'block',marginTop:10}}>项目 patch<textarea className="textarea" rows={7} aria-label="项目 patch" value={patch} disabled={disabled} onChange={e=>{setPatch(e.target.value);invalidate();}} placeholder="粘贴针对上方基线的 unified diff；不会自动执行其中任何命令。"/></label>}
    {source==='github_commit' && <label style={{display:'block',marginTop:10}}>提交 SHA<input className="input" aria-label="项目提交 SHA" value={commit} disabled={disabled} onChange={e=>{setCommit(e.target.value);invalidate();}} placeholder="已授权项目中已推送的 commit SHA"/></label>}
    <label style={{display:'block',marginTop:10}}>说明你的改动与边界<textarea className="textarea" aria-label="项目改动说明" rows={3} value={explanation} disabled={disabled} onChange={e=>{setExplanation(e.target.value);invalidate();}}/></label>
    <details style={{margin:'10px 0'}}><summary>附上自己的测试日志（可选）</summary><p className="hint">用户提供日志与系统核实执行是不同来源，附上日志不会自动变成“测试已核验”。</p><textarea className="textarea" aria-label="用户测试日志" rows={4} value={logs} disabled={disabled} onChange={e=>{setLogs(e.target.value);invalidate();}}/></details>
    <label style={{display:'block',margin:'10px 0'}}><input type="checkbox" aria-label="项目代码使用了 AI 辅助" checked={ai} disabled={disabled} onChange={e=>{setAi(e.target.checked);setSubmitted(false);request.current=null;}}/>本次项目产物使用了 AI 辅助</label>
    <button className="btn btn-outline" disabled={disabled || !canPreview} onClick={makePreview}>{busy?'正在保存…':'预览提交范围'}</button>
    {preview && <div className="card" style={{marginTop:14}}><h4>确认本次提交快照</h4><p>基线 <code>{preview.baseline_revision}</code> → 提交 <code>{preview.submission_revision || '本地内容快照'}</code></p><p className="hint">本系统未执行项目测试，运行结果未验证；代码检查与个人理解验证分开记录。</p><p>排除项：{preview.excluded_paths?.join('、') || '无'}</p>{preview.files.map((file,index)=><details key={index}><summary>{file.path}</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{file.diff}</pre><p className="hint">变更前 {file.before_hash || '无'} · 变更后 {file.after_hash || '无'}</p></details>)}<p className="hint">快照指纹 <code>{preview.content_hash}</code>。确认后只检查这个快照；之后修改不会覆盖本次提交。</p>{submitted?<p role="status">本次项目提交已保存，等待后台检查。</p>:<button className="btn btn-primary" disabled={disabled || !activity.allowed_actions.includes('submit')} onClick={submit}>确认快照并提交检查</button>}</div>}
  </section>;
}

export function ProjectActivityPicker({goalId,onCreated}:{goalId:number;onCreated:(activityId:number)=>void}){
  const [projects,setProjects]=useState<ProjectOption[]|null>(null),[choice,setChoice]=useState(''),[reason,setReason]=useState(''),[extra,setExtra]=useState(''),[error,setError]=useState(''),[blocked,setBlocked]=useState(''),[busy,setBusy]=useState(false);
  const pending=useRef(false),alive=useRef(true);useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  const selected=projects?.flatMap(project=>project.options.map(option=>({project,option,key:`${project.id}:${option.id}`}))).find(item=>item.key===choice);
  const load=async()=>{if(pending.current)return;pending.current=true;setBusy(true);setError('');try{const data=await api.get<{projects:ProjectOption[];blocked_reason?:string}>(`/learning/goals/${goalId}/project-options`);if(alive.current){setProjects(data.projects);setBlocked(data.blocked_reason || '');}}catch(e){if(alive.current)setError(learningError(e));}finally{pending.current=false;if(alive.current)setBusy(false);}};
  const create=async()=>{if(!selected || pending.current)return;pending.current=true;setBusy(true);setError('');try{const created=await api.post<{activity_id:number}>(`/learning/goals/${goalId}/project-activities`,{project_id:selected.project.id,analysis_id:selected.project.analysis_id,option_id:selected.option.id,additional_paths:extra.split('\n').map(s=>s.trim()).filter(Boolean),reason});if(alive.current){if(!created.activity_id)throw new Error('没有收到项目活动编号，请刷新核对');setChoice('');onCreated(created.activity_id);}}catch(e){if(alive.current)setError(learningError(e));}finally{pending.current=false;if(alive.current)setBusy(false);}};
  return <details className="card" style={{marginTop:16}}><summary>选择有实际价值的项目实践（可选）</summary><p className="hint">优先修复、验证已有机制或小实验。项目问题不等于个人短板，也不会为了练习自动新增功能。</p>{error && <p role="alert">{error}</p>}{!projects?<button className="btn btn-outline" disabled={busy} onClick={load}>查看可用项目任务</button>:<>{blocked && <p>{blocked}</p>}{!projects.length && <p>当前没有适合的已授权项目任务，可先完成短练习。</p>}<label>项目任务 <select aria-label="选择项目实践任务" value={choice} disabled={busy} onChange={e=>setChoice(e.target.value)}><option value="">请选择</option>{projects.flatMap(project=>project.options.map(option=><option key={`${project.id}:${option.id}`} value={`${project.id}:${option.id}`}>{project.name} · {option.title}</option>))}</select></label>{selected && <><p>基线 <code>{selected.project.baseline_revision}</code> · 约定路径 {selected.option.paths.join('、')}</p><label>选择理由<textarea className="textarea" value={reason} onChange={e=>setReason(e.target.value)} disabled={busy}/></label><details><summary>额外授权的关联路径（可选）</summary><p className="hint">仅填写本次必要的仓库相对路径，一行一条；服务端仍会检查范围。</p><textarea className="textarea" value={extra} onChange={e=>setExtra(e.target.value)} disabled={busy}/></details><button className="btn btn-primary" disabled={busy} onClick={create}>绑定这项项目实践</button></>}</>}</details>;
}
