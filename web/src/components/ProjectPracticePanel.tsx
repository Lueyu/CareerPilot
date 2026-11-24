import {useT} from '../i18n';
import React,{useEffect,useRef,useState} from 'react';
import {ProjectCopyError} from '../lib/projectIntelligence';
import {api} from '../api/client';
import {LearningActivity,LearningSubmission,LearningSubmissionAck,LEARNING_PENDING} from '../lib/learningPath';

function practiceError(error: unknown, t: ReturnType<typeof useT>) {
  if (error instanceof ProjectCopyError) return t(error.localeKey);
  if (typeof error === 'string') return error;
  if (error && typeof error === 'object' && 'message' in error) return String(error.message);
  return t('projects.learningOperationFailed');
}

type Source='files'|'patch'|'github_commit';
interface FileInput {path:string;content:string|null}
interface ProjectPreview {snapshot_id:number;content_hash:string;baseline_revision:string;submission_revision:string|null;files:{path:string;diff:string;before_hash?:string;after_hash?:string}[];excluded_paths:string[];test_evidence:unknown;limits?:Record<string,unknown>}
interface ProjectOption {id:number;name:string;analysis_id:number;baseline_revision:string;options:{id:string;kind:string;title:string;paths:string[]}[]}
const freshKey=()=>globalThis.crypto?.randomUUID?.() || `project-practice-${Date.now()}-${Math.random().toString(36).slice(2)}`;

export default function ProjectPracticePanel({activity,latest,onSubmitted}:{activity:LearningActivity;latest:LearningSubmission|null;onSubmitted:(ack:LearningSubmissionAck)=>void}){
  const t = useT();
  const [source,setSource]=useState<Source>('files'),[files,setFiles]=useState<FileInput[]>([]),[patch,setPatch]=useState(''),[commit,setCommit]=useState(''),[explanation,setExplanation]=useState(''),[logs,setLogs]=useState(''),[ai,setAi]=useState(false);
  const [preview,setPreview]=useState<ProjectPreview|null>(null),[failure,setError]=useState<{detail: unknown}>(),[busy,setBusy]=useState(false),[submitted,setSubmitted]=useState(false);
  const alive=useRef(true),pending=useRef(false),request=useRef<{snapshot:number;key:string}|null>(null);
  const contract=activity.project_contract;
  const running=!!latest && LEARNING_PENDING.has(latest.status);
  const disabled=busy || running || !activity.allowed_actions.includes('submit');
  useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  const invalidate=()=>{setPreview(null);request.current=null;setSubmitted(false);setError(undefined);};
  const perform=async(action:()=>Promise<void>)=>{if(pending.current)return;pending.current=true;setBusy(true);setError(undefined);try{await action();}catch(e){if(alive.current)setError({detail: e});}finally{pending.current=false;if(alive.current)setBusy(false);}};
  const chooseFiles=async(list:FileList|null)=>{
    invalidate();if(!list)return;
    await perform(async()=>{
      const selected=Array.from(list);
      if(selected.length>12 || selected.reduce((sum,f)=>sum+f.size,0)>3*1024*1024)throw new ProjectCopyError('projects.tooManyPracticeFiles');
      const values=await Promise.all(selected.map(async(file)=>({path:file.webkitRelativePath || file.name,content:await file.text()})));
      if(values.some(file=>(file.content?.length || 0)>60000))throw new ProjectCopyError('projects.practiceFileTooLong');
      if(alive.current)setFiles(values);
    });
  };
  const makePreview=()=>perform(async()=>{
    if(!contract)return;
    const body={base_revision:contract.baseline_revision,source_kind:source,explanation,test_logs:logs,
      ...(source==='files'?{files}:source==='patch'?{patch}:{commit_sha:commit.trim()})};
    const value=await api.post<ProjectPreview>(`/learning/activities/${activity.id}/project-preview`,body);
    if(!alive.current)return;
    if(!value.snapshot_id || !Array.isArray(value.files))throw new ProjectCopyError('projects.missingPracticeSnapshot');
    setPreview(value);setSubmitted(false);request.current={snapshot:value.snapshot_id,key:freshKey()};
  });
  const submit=()=>perform(async()=>{
    if(!preview)return;
    if(request.current?.snapshot!==preview.snapshot_id)request.current={snapshot:preview.snapshot_id,key:freshKey()};
    const result=await api.post<LearningSubmissionAck>(`/learning/activities/${activity.id}/project-submit`,{snapshot_id:preview.snapshot_id,request_key:request.current.key,expected_activity_revision:activity.revision,parent_submission_id:latest?.id || null,assistance:{...activity.assistance,ai_declared:ai}});
    if(!alive.current)return;if(!result.submission_id)throw new ProjectCopyError('projects.missingPracticeSubmissionId');
    setSubmitted(true);onSubmitted(result);
  });
  if(!contract)return <p role="alert">{t('projects.missingPracticeBaseline')}</p>;
  const canPreview=source==='files'?files.length>0 && files.every(f=>f.path.trim()):source==='patch'?!!patch.trim():!!commit.trim();
  return <section aria-label={t('projects.practiceSubmission')} style={{marginTop:14}}>
    <div className="demo-banner"><div><b>{t('projects.pinnedBaseline')}</b> <code>{contract.baseline_revision}</code><p>{t('projects.allowedPracticePaths', {paths: contract.allowed_paths.join(t('projects.zhListSeparator')) || t('projects.unspecified')})}</p><p>{t('projects.practiceAcceptanceCriteria', {criteria: Array.isArray(contract.acceptance) ? contract.acceptance.join(t('projects.sentenceListSeparator')) : contract.acceptance})}</p><p className="hint">{t('projects.readOnlyPracticeNotice')}</p></div></div>
    {failure && <p role="alert">{t('projects.practiceErrorPreserved', {error: practiceError(failure.detail, t)})}</p>}
    <label>{t('projects.submissionMethod')} <select aria-label={t('projects.submissionMethodLabel')} value={source} disabled={disabled} onChange={e=>{setSource(e.target.value as Source);invalidate();}}><option value="files">{t('projects.selectLocalFiles')}</option><option value="patch">{t('projects.pastePatch')}</option><option value="github_commit">{t('projects.pushedGithubCommit')}</option></select></label>
    {source==='files' && <div><p className="hint">{t('projects.localFileScope')}</p><input type="file" multiple aria-label={t('projects.localSubmissionFiles')} disabled={disabled} onChange={e=>void chooseFiles(e.target.files)}/>{files.map((file,index)=><div key={index} style={{margin:'8px 0'}}><label>{t('projects.repositoryPath')} <input aria-label={t('projects.practiceFilePath', {number: index + 1})} value={file.path} disabled={disabled} onChange={e=>{setFiles(old=>old.map((f,i)=>i===index?{...f,path:e.target.value}:f));invalidate();}}/></label><span className="hint">{t('projects.practiceCharacterCount', {count: file.content?.length ?? 0})}</span><button className="btn btn-ghost btn-sm" disabled={disabled} onClick={()=>{setFiles(old=>old.filter((_,i)=>i!==index));invalidate();}}>{t('projects.removeFile')}</button></div>)}</div>}
    {source==='patch' && <label style={{display:'block',marginTop:10}}>{t('projects.projectPatch')}<textarea className="textarea" rows={7} aria-label={t('projects.projectPatch')} value={patch} disabled={disabled} onChange={e=>{setPatch(e.target.value);invalidate();}} placeholder={t('projects.patchPlaceholder')}/></label>}
    {source==='github_commit' && <label style={{display:'block',marginTop:10}}>{t('projects.commitSha')}<input className="input" aria-label={t('projects.submissionSha')} value={commit} disabled={disabled} onChange={e=>{setCommit(e.target.value);invalidate();}} placeholder={t('projects.pushedCommitPlaceholder')}/></label>}
    <label style={{display:'block',marginTop:10}}>{t('projects.describeChanges')}<textarea className="textarea" aria-label={t('projects.changeDescriptionLabel')} rows={3} value={explanation} disabled={disabled} onChange={e=>{setExplanation(e.target.value);invalidate();}}/></label>
    <details style={{margin:'10px 0'}}><summary>{t('projects.attachTestLog')}</summary><p className="hint">{t('projects.testLogNotice')}</p><textarea className="textarea" aria-label={t('projects.userTestLog')} rows={4} value={logs} disabled={disabled} onChange={e=>{setLogs(e.target.value);invalidate();}}/></details>
    <label style={{display:'block',margin:'10px 0'}}><input type="checkbox" aria-label={t('projects.aiAssistedLabel')} checked={ai} disabled={disabled} onChange={e=>{setAi(e.target.checked);setSubmitted(false);request.current=null;}}/>{t('projects.aiAssistedWork')}</label>
    <button className="btn btn-outline" disabled={disabled || !canPreview} onClick={makePreview}>{busy?t('projects.savingDraft'):t('projects.previewSubmission')}</button>
    {preview && <div className="card" style={{marginTop:14}}><h4>{t('projects.reviewSnapshot')}</h4><p>{t('projects.baseline')} <code>{preview.baseline_revision}</code> {t('projects.previewSubmissionRevision')} <code>{preview.submission_revision || t('projects.localSnapshot')}</code></p><p className="hint">{t('projects.unverifiedTestNotice')}</p><p>{t('projects.excludedPracticePaths', {paths: preview.excluded_paths?.join(t('projects.zhListSeparator')) || t('projects.none')})}</p>{preview.files.map((file,index)=><details key={index}><summary>{file.path}</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{file.diff}</pre><p className="hint">{t('projects.practiceFileHashes', {before: file.before_hash || t('projects.none'), after: file.after_hash || t('projects.none')})}</p></details>)}<p className="hint">{t('projects.snapshotFingerprint')} <code>{preview.content_hash}</code>{t('projects.snapshotConfirmationNotice')}</p>{submitted?<p role="status">{t('projects.submissionQueued')}</p>:<button className="btn btn-primary" disabled={disabled || !activity.allowed_actions.includes('submit')} onClick={submit}>{t('projects.confirmSubmitSnapshot')}</button>}</div>}
  </section>;
}

export function ProjectActivityPicker({goalId,onCreated}:{goalId:number;onCreated:(activityId:number)=>void}){
  const t = useT();
  const [projects,setProjects]=useState<ProjectOption[]|null>(null),[choice,setChoice]=useState(''),[reason,setReason]=useState(''),[extra,setExtra]=useState(''),[failure,setError]=useState<{detail: unknown}>(),[blocked,setBlocked]=useState(''),[busy,setBusy]=useState(false);
  const pending=useRef(false),alive=useRef(true);useEffect(()=>{alive.current=true;return()=>{alive.current=false;};},[]);
  const selected=projects?.flatMap(project=>project.options.map(option=>({project,option,key:`${project.id}:${option.id}`}))).find(item=>item.key===choice);
  const load=async()=>{if(pending.current)return;pending.current=true;setBusy(true);setError(undefined);try{const data=await api.get<{projects:ProjectOption[];blocked_reason?:string}>(`/learning/goals/${goalId}/project-options`);if(alive.current){setProjects(data.projects);setBlocked(data.blocked_reason || '');}}catch(e){if(alive.current)setError({detail: e});}finally{pending.current=false;if(alive.current)setBusy(false);}};
  const create=async()=>{if(!selected || pending.current)return;pending.current=true;setBusy(true);setError(undefined);try{const created=await api.post<{activity_id:number}>(`/learning/goals/${goalId}/project-activities`,{project_id:selected.project.id,analysis_id:selected.project.analysis_id,option_id:selected.option.id,additional_paths:extra.split('\n').map(s=>s.trim()).filter(Boolean),reason});if(alive.current){if(!created.activity_id)throw new ProjectCopyError('projects.missingActivityId');setChoice('');onCreated(created.activity_id);}}catch(e){if(alive.current)setError({detail: e});}finally{pending.current=false;if(alive.current)setBusy(false);}};
  return <details className="card" style={{marginTop:16}}><summary>{t('projects.chooseProjectPractice')}</summary><p className="hint">{t('projects.practicePriorities')}</p>{failure && <p role="alert">{practiceError(failure.detail, t)}</p>}{!projects?<button className="btn btn-outline" disabled={busy} onClick={load}>{t('projects.viewAvailableTasks')}</button>:<>{blocked && <p>{blocked}</p>}{!projects.length && <p>{t('projects.noEligibleTasks')}</p>}<label>{t('projects.projectTask')} <select aria-label={t('projects.selectPracticeTask')} value={choice} disabled={busy} onChange={e=>setChoice(e.target.value)}><option value="">{t('projects.selectOption')}</option>{projects.flatMap(project=>project.options.map(option=><option key={`${project.id}:${option.id}`} value={`${project.id}:${option.id}`}>{project.name} · {option.title}</option>))}</select></label>{selected && <><p>{t('projects.baseline')} <code>{selected.project.baseline_revision}</code> {t('projects.agreedPaths', {paths: selected.option.paths.join(t('projects.zhListSeparator'))})}</p><label>{t('projects.selectionReason')}<textarea className="textarea" value={reason} onChange={e=>setReason(e.target.value)} disabled={busy}/></label><details><summary>{t('projects.additionalPaths')}</summary><p className="hint">{t('projects.additionalPathsDescription')}</p><textarea className="textarea" value={extra} onChange={e=>setExtra(e.target.value)} disabled={busy}/></details><button className="btn btn-primary" disabled={busy} onClick={create}>{t('projects.attachPractice')}</button></>}</>}</details>;
}
