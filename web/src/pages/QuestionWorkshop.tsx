import { getLocale, t as translate, useLocale, useT, type Locale } from '../i18n';
import React, {useEffect, useRef, useState} from 'react';
import {Link, useLocation, useNavigate} from 'react-router-dom';
import {api} from '../api/client';
import {PageHead} from '../components/ui';
import ProjectContextEditor from '../components/ProjectContextEditor';
import QuestionDraftEditor from '../components/QuestionDraftEditor';
import { bankMessageText, type BankMessage, approvalBlock, assessedBatchQuestionIds, beginProjectAssessment, beginQuestionPractice, GeneratedItem, ITEM_LABELS, messageOf, ProjectRecord, SKILLS, TASK_LABELS, WorkshopRow, WorkshopSuggestion, WorkshopTask, workshopQuestionLabel } from '../lib/questionWorkshop';
import {issueWorkshopPath, ProjectAnalysisRun} from '../lib/projectIntelligence';
import {SourceCitations} from '../components/ProjectAnalysisPanel';
import {sanitizeReviewAnswer} from './BankReviewQueue';

const panel: React.CSSProperties = {padding:20,marginBottom:20};
const toolbar: React.CSSProperties = {display:'flex',gap:10,flexWrap:'wrap',alignItems:'center'};

export default function QuestionWorkshop() {
  const t = useT();
  const locale = useLocale();
  const taskStatus = (status: string) => Object.prototype.hasOwnProperty.call(TASK_LABELS, status) ? t(TASK_LABELS[status]) : status;
  const location = useLocation();
  const navigate = useNavigate();
  const query = new URLSearchParams(location.search);
  const taskId = query.get('task');
  const sourceImportId = query.get('source_import');
  const projectId = Number(query.get('project'));
  const [projects,setProjects] = useState<ProjectRecord[]>([]);
  const [suggestionResult, setSuggestionResult] = useState<{ locale: Locale; items: WorkshopSuggestion[] }>();
  const [suggestionRevision, setSuggestionRevision] = useState(0);
  const suggestions = suggestionResult?.locale === locale ? suggestionResult.items : [];
  const [history,setHistory] = useState<WorkshopTask[]>([]);
  const [loading,setLoading] = useState(true);
  const [error,setError] = useState<BankMessage>('');
  const [notice,setNotice] = useState<{key: string; suggestionId?: string} | null>(null);
  const suggestionName = suggestions.find(s => s.id === notice?.suggestionId)?.title;
  const [busy,setBusy] = useState(false);
  const [kind,setKind] = useState<'import'|'supplement'>(sourceImportId || (location.state as any)?.sourceText ? 'import':'supplement');
  const [mode,setMode] = useState<'api'|'external'>('api');
  const [count,setCount] = useState(5);
  const [skills,setSkills] = useState(['rag']);
  const [projectIds,setProjectIds] = useState<number[]>(projectId>0?[projectId]:[]);
  const [sourceAnalysisId,setSourceAnalysisId]=useState(Number(query.get('analysis')) || null);
  const [sourceProjectId,setSourceProjectId]=useState(projectId || null);
  const [sourceIssueIds,setSourceIssueIds]=useState((query.get('issues') || '').split(',').filter(Boolean));
  const [sourceAnalysis,setSourceAnalysis]=useState<ProjectAnalysisRun|null>(null);
  const [sourceError,setSourceError]=useState<BankMessage>('');
  const [sourceLoading,setSourceLoading]=useState(false);
  const [sourceText,setSourceText] = useState((location.state as any)?.sourceText || '');
  const [purpose,setPurpose] = useState('');
  const [editingProject,setEditingProject] = useState<ProjectRecord|null|undefined>();
  const [task,setTask] = useState<WorkshopTask|null>(null);
  const [response,setResponse] = useState('');
  const [editingItem,setEditingItem] = useState<number|null>(null);
  const createAttempt = useRef({payload:'',key:''});
  const practiceAttempts = useRef(new Map<number,string>());
  const assessmentAttempt=useRef({signature:'',key:''});
  const openedProject = useRef<number|null>(null);

  const loadResources = async () => {
    const [p,h] = await Promise.all([api.get<{items:ProjectRecord[]}>('/projects'),api.get<{items:WorkshopTask[]}>('/question-generation')]);
    setProjects(p.items);setHistory(h.items);
    setSuggestionRevision(revision => revision + 1);
  };
  useEffect(()=>{
    let active = true;
    Promise.all([api.get<{items:ProjectRecord[]}>('/projects'),api.get<{items:WorkshopTask[]}>('/question-generation')])
      .then(([p,h])=>{if(active){setProjects(p.items);setHistory(h.items);}})
      .catch(e=>{if(active)setError({ key: 'bank.workshopLoadFailed', values: { error: messageOf(e) } });}).finally(()=>{if(active)setLoading(false);});
    return ()=>{active=false;};
  },[]);
  useEffect(() => {
    if (getLocale() !== locale) return;
    let active = true;
    api.get<{ items: WorkshopSuggestion[] }>('/question-generation/suggestions')
      .then(({ items }) => {
        if (active) setSuggestionResult({ locale, items });
      })
      .catch(error => {
        if (active) setError({ key: 'bank.workshopLoadFailed', values: { error: messageOf(error) } });
      });
    return () => { active = false; };
  }, [locale, suggestionRevision]);
  useEffect(()=>{
    if(projectId>0 && !sourceAnalysisId && openedProject.current!==projectId){const chosen=projects.find(p=>p.id===projectId);if(chosen){openedProject.current=projectId;setEditingProject(chosen);}}
  },[projectId,projects,sourceAnalysisId]);
  useEffect(()=>{
    if(taskId)return;
    const q=new URLSearchParams(location.search);
    setSourceAnalysisId(Number(q.get('analysis')) || null);setSourceProjectId(Number(q.get('project')) || null);
    setSourceIssueIds((q.get('issues') || '').split(',').filter(Boolean));
  },[location.search,taskId]);
  const issueSignature=sourceIssueIds.join(',');
  useEffect(()=>{
    let active=true;
    setSourceAnalysis(null);setSourceError('');
    if(taskId){setSourceLoading(false);return;}
    if(!sourceAnalysisId){setSourceLoading(false);return;}
    if(!sourceProjectId || !sourceIssueIds.length){setSourceError({ key: 'bank.incompleteProjectSource' });setSourceLoading(false);return;}
    setSourceLoading(true);
    api.get<ProjectAnalysisRun>(`/projects/${sourceProjectId}/analyses/${sourceAnalysisId}`).then(run=>{
      if(!active)return;
      const completeReport=run.report_status==='VALID' && run.coverage_status==='COMPLETE';
      if(!run.report || (!completeReport && run.status!=='SUCCESS')){ setSourceError({ key: 'bank.invalidProjectReport' }); return; }
      if(run.is_latest_success===false){ setSourceError({ key: 'bank.supersededProjectAnalysis' }); return; }
      const issues=run.report.issues.filter(issue=>sourceIssueIds.includes(issue.id));
      if(issues.length!==new Set(sourceIssueIds).size){ setSourceError({ key: 'bank.issueNotInAnalysis' }); return; }
      if(!run.active_issue_ids || sourceIssueIds.some(id=>!run.active_issue_ids?.includes(id))){ setSourceError({ key: 'bank.issueMemoryUnavailable' }); return; }
      setSourceAnalysis(run);setKind('supplement');setProjectIds(old=>[...new Set([sourceProjectId,...old])]);
      setSkills([...new Set(issues.map(issue=>issue.skill_key))]);
      setPurpose(translate('bank.projectIssueGoalTemplate', { issues: issues.map(issue => issue.title).join(translate('bank.clauseSeparator')) }));
    }).catch(e=>{if(active)setSourceError(messageOf(e));}).finally(()=>{if(active)setSourceLoading(false);});
    return ()=>{active=false;};
  },[sourceAnalysisId,sourceProjectId,issueSignature,taskId]);
  useEffect(()=>{
    let active = true;
    setTask(null);setEditingItem(null);setResponse('');setNotice(null);setError('');
    if(taskId) api.get<WorkshopTask>(`/question-generation/${taskId}`).then(t=>{if(active){setTask(t);setResponse(t.raw_response || '');}}).catch(e=>{if(active)setError(messageOf(e));});
    return ()=>{active=false;};
  },[taskId]);
  useEffect(()=>{
    if(!task || task.status !== 'RUNNING') return;
    let active=true;
    const timer=setInterval(()=>{
      api.get<WorkshopTask>(`/question-generation/${task.id}`).then(t=>{if(active){setTask(t);if(t.status!=='RUNNING'){setResponse(t.raw_response || '');void loadResources().catch(()=>{});}}}).catch(e=>{if(active)setError({ key: 'bank.workshopPollFailed', values: { error: messageOf(e) } });});
    },2500);
    return ()=>{active=false;clearInterval(timer);};
  },[task?.id,task?.status]);
  const perform = async (fn:()=>Promise<void>) => {
    setBusy(true);setError('');setNotice(null);
    try {await fn();} catch(e){setError(messageOf(e));} finally {setBusy(false);}
  };
  const refreshTask = async () => {
    if(task) setTask(await api.get<WorkshopTask>(`/question-generation/${task.id}`));
  };
  const createTask = () => perform(async()=>{
    if(sourceAnalysisId && (!sourceAnalysis || sourceError || sourceLoading))throw new Error(t('bank.loadProjectIssueFirst'));
    const body = {kind,mode,count,skill_keys:skills,project_ids:projectIds,source_text:sourceText,purpose,...(sourceImportId && kind==='import' ? {source_import_id:Number(sourceImportId)}:{}),...(sourceAnalysisId?{source_analysis_id:sourceAnalysisId,source_issue_ids:sourceIssueIds}:{})};
    const signature = JSON.stringify(body);
    if(createAttempt.current.payload!==signature) createAttempt.current={payload:signature,key:crypto.randomUUID()};
    const created = await api.post<WorkshopTask>('/question-generation',{...body,request_key:createAttempt.current.key});
    createAttempt.current={payload:'',key:''};
    setTask(created);navigate(`/bank/workshop?task=${created.id}`);await loadResources();
  });
  const run = () => perform(async()=>{
    if(!task)return;
    setTask(await api.post<WorkshopTask>(`/question-generation/${task.id}/run`,{}));
    await loadResources();
  });
  const submitResponse = () => perform(async()=>{
    if(!task)return;
    try {setTask(await api.post<WorkshopTask>(`/question-generation/${task.id}/result`,{text:response}));await loadResources();}
    catch(e){await refreshTask();throw e;}
  });
  const saveItem = async(row:WorkshopRow,item:GeneratedItem) => perform(async()=>{
    if(!task)return;
    await api.patch(`/question-generation/${task.id}/items/${row.id}`,{item, revision: row.review_revision ?? 0});
    await refreshTask();setEditingItem(null);setNotice({key: 'bank.draftSaved'});
  });
  const copyPrompt=()=>perform(async()=>{
    if(!task)return;
    if(!navigator.clipboard) throw new Error(t('bank.manualPromptCopy'));
    await navigator.clipboard.writeText(task.prompt_text);setNotice({key: 'bank.promptCopied'});
  });
  const practice=(row:WorkshopRow)=>perform(async()=>{
    if(!row.question_id) throw new Error(t('bank.practiceNotApproved'));
    const key=practiceAttempts.current.get(row.question_id) || crypto.randomUUID();
    practiceAttempts.current.set(row.question_id,key);
    const exam=await beginQuestionPractice(row.question_id,key,api);
    practiceAttempts.current.delete(row.question_id);
    navigate(`/exam/diagnosis?id=${exam.id}`);
  });
  const assessBatch=()=>perform(async()=>{
    if(!task?.input_snapshot.source_analysis_id)return;
    const signature=JSON.stringify([task.id,assessedBatchQuestionIds(task.review?.items || [])]);
    if(assessmentAttempt.current.signature!==signature)assessmentAttempt.current={signature,key:crypto.randomUUID()};
    const exam=await beginProjectAssessment(task.review?.items || [],assessmentAttempt.current.key,api,task.id);
    assessmentAttempt.current={signature:'',key:''};navigate(`/exam/diagnosis?id=${exam.id}`);
  });
  const createNewTask=()=>{
    if(!task)return;
    const snapshot=task.input_snapshot;
    let path='/bank/workshop';
    if(snapshot?.source_analysis_id){
      const analysisId=snapshot.source_analysis_id;
      const issueIds=snapshot.source_issue_ids || [];
      const contexts=snapshot.project_context || [];
      const boundProject=snapshot.project_issue_targets?.find(target=>target.analysis_id===analysisId)?.project_id
        || contexts.find(p=>p.facts.some(f=>f.analysis_id===analysisId))?.id
        || (contexts.length===1?contexts[0].id:null);
      if(!boundProject || !issueIds.length){setError({ key: 'bank.previousTaskSourceIncomplete' });return;}
      path=issueWorkshopPath(boundProject,analysisId,issueIds);
      setProjectIds(contexts.map(p=>p.id));setMode(task.mode);setCount(snapshot.count);
    }
    createAttempt.current={payload:'',key:''};setNotice(null);setError('');navigate(path);
  };
  const rows=task?.review?.items || [];
  const taskProjects=task?.input_snapshot?.project_context || [];

  return <div className="question-workshop">
    <PageHead title={t('bank.workshopTitle')} sub={t('bank.workshopSubtitle')}><Link className="btn btn-outline" to="/bank">{t('bank.backToBank')}</Link></PageHead>
    <p className="hint" style={{marginBottom:18}}>{t('bank.workshopWarning')}</p>
    {error && <div role="alert" className="demo-banner demo-banner-err" style={{marginBottom:16}}>{bankMessageText(error, t)} <button className="btn btn-ghost btn-sm" onClick={()=>setError('')}>{t('bank.dismissNotice')}</button></div>}
    {notice && (!notice.suggestionId || suggestionName) && <div role="status" className="demo-banner" style={{marginBottom:16}}>{t(notice.key, { name: suggestionName ?? '' })}</div>}
    {loading && <div className="card" style={panel}>{t('bank.loadingWorkshop')}</div>}
    <div style={{display:'grid',gridTemplateColumns:'minmax(0,1fr)',gap:0}}>
      {editingProject!==undefined && <ProjectContextEditor key={editingProject?.id || 'new'} project={editingProject} onCancel={()=>setEditingProject(undefined)} onSaved={async()=>{setEditingProject(undefined);await loadResources();setNotice({key: 'bank.projectProfileSaved'});}} />}
      {!task && !taskId && <>
        <section className="card" style={panel}>
          <div style={{...toolbar,justifyContent:'space-between'}}><h3 style={{margin:0}}>{t('bank.prepareProjectMaterials')}</h3><button className="btn btn-outline btn-sm" onClick={()=>setEditingProject(null)}>{t('bank.addManualProject')}</button></div>
          <p className="hint">{t('bank.selectProjectsHelp')}</p>
          {!loading && projects.length===0 && <p>{t('bank.workshopNoProjects')}</p>}
          {projects.map(p=>{
            const profile=p.profile || p.meta?.profile || {};
            return <div key={p.id} style={{...toolbar,padding:'12px 0',borderBottom:'1px solid var(--border)'}}>
              <label style={{flex:1,display:'flex',gap:10,alignItems:'center'}}><input type="checkbox" checked={projectIds.includes(p.id)} disabled={!!sourceAnalysisId && p.id===sourceProjectId} onChange={e=>setProjectIds(old=>e.target.checked?[...old,p.id]:old.filter(id=>id!==p.id))}/><strong>{p.name}</strong><span className="chip chip-slate">{((p.kind || p.meta?.kind)==='manual')?t('bank.manualProject'):((p.kind || p.meta?.kind)==='gitcode')?t('bank.gitcodeProject'):t('bank.githubProject')}</span></label>
              <span className="hint">{profile.role && profile.responsibilities ? t('bank.roleAndWorkReady'):t('bank.roleAndWorkMissing')}</span><button className="btn btn-outline btn-sm" onClick={()=>setEditingProject(p)}>{t('bank.editExperience')}</button>
            </div>;
          })}
        </section>
        <section className="card" style={panel}>
          <h3>{t('bank.chooseTaskGoal')}</h3>
          {sourceAnalysisId && <div className="demo-banner" style={{marginBottom:16}}>
            <b>{t('bank.prepareAnalysisAssessment', { id: sourceAnalysisId })}</b>
            {sourceLoading && <p role="status">{t('bank.loadingIssue')}</p>}
            {sourceError && <p role="alert">{t('bank.projectSourceUnavailable', { error: bankMessageText(sourceError, t) })}</p>}
            {sourceError && sourceProjectId && <Link to={`/assets/project/${sourceProjectId}`}>{t('bank.reselectIssue')}</Link>}
            {sourceAnalysis?.report?.issues.filter(issue=>sourceIssueIds.includes(issue.id)).map(issue=><div key={issue.id}><p><strong>{issue.title}</strong> · {issue.status==='hypothesis'?t('bank.unverifiedInference'):t('bank.codeObservation')}</p><p>{issue.detail}</p><SourceCitations citations={issue.citations}/></div>)}
            {sourceAnalysis?.issue_memories?.filter(m=>sourceIssueIds.includes(m.issue_id) && m.user_corrected).map(m=><div key={m.memory_id}><b>{t('bank.correctedMemoryId', { id: m.memory_id })}</b><p style={{whiteSpace:'pre-wrap'}}>{m.content}</p><p className="hint">{t('bank.correctedMemoryHelp')}</p></div>)}
            <p className="hint">{t('bank.projectSourceWarning')}</p>
            <button className="btn btn-ghost btn-sm" onClick={()=>{setSourceAnalysisId(null);setSourceIssueIds([]);setSourceAnalysis(null);setSourceError('');navigate('/bank/workshop',{replace:true});}}>{t('bank.clearProjectIssue')}</button>
          </div>}
          {suggestions.length>0 && <details style={{marginBottom:18}} open><summary>{t('bank.workshopSuggestions')}</summary><div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(240px,1fr))',gap:10,marginTop:12}}>{suggestions.map(s=><div key={s.id} style={{border:'1px solid var(--border)',borderRadius:8,padding:14}}><strong>{s.title}</strong><p className="hint">{s.reason}</p><button className="btn btn-outline btn-sm" onClick={()=>{setKind(s.kind);setCount(s.count);setSkills(s.skill_keys.length ? s.skill_keys : SKILLS.map(skill=>skill.key));setProjectIds(s.project_ids);setPurpose(s.purpose);setSourceAnalysisId(s.source_analysis_id || null);setSourceIssueIds(s.source_issue_ids || []);setSourceProjectId(s.project_ids[0] || null);setNotice({key: 'bank.suggestionSelected', suggestionId: s.id});}}>{t('bank.useSuggestion')}</button></div>)}</div></details>}
          <div className="form-row"><label className="field"><span>{t('bank.taskType')}</span><select className="select" aria-label={t('bank.taskType')} value={kind} onChange={e=>setKind(e.target.value as typeof kind)}><option value="import">{t('bank.adaptInterviewNotes')}</option><option value="supplement">{t('bank.fillBankGaps')}</option></select></label><label className="field"><span>{t('bank.generationCount')}</span><input className="inp" type="number" min={1} max={20} aria-label={t('bank.generationCount')} value={count} onChange={e=>setCount(Number(e.target.value))}/></label></div>
          {kind==='import' && <label className="field"><span>{t('bank.originalNotes')}{sourceImportId ? t('bank.useImportSession', { id: sourceImportId }):''}</span><textarea className="textarea" aria-label={t('bank.originalNotes')} rows={7} maxLength={60000} placeholder={t('bank.pasteOriginalNotesHint')} value={sourceText} onChange={e=>setSourceText(e.target.value)}/></label>}
          <label className="field"><span>{t('bank.generationRequirements')}</span><textarea className="textarea" aria-label={t('bank.generationGoal')} rows={3} maxLength={3000} placeholder={t('bank.generationRequirementsHint')} value={purpose} onChange={e=>setPurpose(e.target.value)}/></label>
          <fieldset style={{border:0,padding:0,marginBottom:18}}><legend style={{marginBottom:8}}>{t('bank.chooseSkills')}</legend><div style={toolbar}>{SKILLS.map(s=><label key={s.key} style={{display:'flex',gap:5,alignItems:'center'}}><input type="checkbox" checked={skills.includes(s.key)} onChange={e=>setSkills(old=>e.target.checked?[...old,s.key]:old.filter(k=>k!==s.key))}/>{t(s.labelKey)}</label>)}</div></fieldset>
          <fieldset style={{border:0,padding:0}}><legend style={{marginBottom:8}}>{t('bank.executionMode')}</legend><div style={toolbar}><label><input type="radio" name="generation-mode" value="api" checked={mode==='api'} onChange={()=>setMode('api')}/> {t('bank.useConfiguredApi')}</label><label><input type="radio" name="generation-mode" value="external" checked={mode==='external'} onChange={()=>setMode('external')}/> {t('bank.useExternalChat')}</label></div></fieldset>
          <p className="hint">{t('bank.createTaskHelp')}</p>
          <button className="btn btn-primary" disabled={busy || loading || (!!sourceAnalysisId && (!sourceAnalysis || sourceLoading || !!sourceError)) || !skills.length || !Number.isInteger(count) || count<1 || count>20 || (kind==='import' && !sourceText.trim() && !sourceImportId)} onClick={createTask}>{busy?t('bank.preparingTask'):t('bank.createCheckTask')}</button>
        </section>
      </>}
      {taskId && !task && <div className="card" style={panel}>{t('bank.loadingTask', { id: taskId })} <Link to="/bank/workshop">{t('bank.backToNewTask')}</Link></div>}
      {task && <>
        <section className="card" style={panel}>
          <div style={{...toolbar,justifyContent:'space-between'}}><h3 style={{margin:0}}>{t('bank.taskWithStatus', { id: task.id, status: taskStatus(task.status) })}</h3><button className="btn btn-outline btn-sm" disabled={busy} onClick={createNewTask}>{t('bank.createNewTask')}</button></div>
          <p className="hint">{t('bank.taskSummary', { kind: t(task.kind === 'import' ? 'bank.adaptNotesKind' : 'bank.fillGapsKind'), mode: t(task.mode === 'api' ? 'bank.apiGeneration' : 'bank.externalChatImport'), count: task.input_snapshot?.count ?? '—', date: task.created_at ? new Date(task.created_at).toLocaleString(locale) : '' })}</p>
          <details style={{marginBottom:16}}><summary>{t('bank.snapshotAndEvidence')}</summary>
            {task.input_snapshot.source_analysis_id && <p>{t('bank.snapshotAnalysisSource', { id: task.input_snapshot.source_analysis_id, issues: task.input_snapshot.source_issue_ids?.join(t('bank.listSeparator')) ?? '' })}</p>}
            <p>{task.input_snapshot?.purpose || t('bank.noExtraRequirements')}</p>
            {task.input_snapshot?.source_text && <details><summary>{t('bank.snapshotOriginalNotes')}</summary><pre style={{whiteSpace:'pre-wrap',maxHeight:240,overflow:'auto'}}>{task.input_snapshot.source_text}</pre></details>}
            {taskProjects.length===0 && <p className="hint">{t('bank.generalAnswersOnly')}</p>}
            {taskProjects.map(p=><div key={p.id} style={{marginTop:12}}><strong>{p.name}</strong><p className="hint">{p.has_personal_context?t('bank.personalExperienceProvided'):t('bank.personalExperienceIncomplete')}</p><ul>{p.facts.map(f=><li key={f.id} style={{marginBottom:8}}><span className="chip chip-slate">{f.source==='user_statement' ? t('bank.userStatement'):f.user_corrected?t('bank.userCorrectedObservation'):t('bank.repoObservation')}</span> {f.text}<small style={{display:'block',color:'var(--text-3)'}}>{f.id} · {f.source}{f.analysis_id?t('bank.factAnalysisSuffix', { id: f.analysis_id }):''}{f.source_revision?t('bank.factVersionSuffix', { version: f.source_revision }):''}</small>{!!f.citations?.length && <SourceCitations citations={f.citations}/>}</li>)}</ul></div>)}
          </details>
          {task.error && <p role="alert" className="demo-banner demo-banner-err">{t('bank.taskFailure', { error: task.error })}</p>}
          {task.status==='RUNNING' && <p role="status">{t('bank.apiGenerationRunning')}</p>}
          <details open={task.mode==='external' && !task.import_session_id} style={{marginBottom:16}}><summary>{t(task.mode === 'external' ? 'bank.fullPromptExternal' : 'bank.fullPrompt')}</summary><p className="hint">{t('bank.promptContentsHelp')}</p><textarea readOnly className="textarea" rows={10} aria-label={t('bank.fullPrompt')} value={task.prompt_text || ''}/><button className="btn btn-outline btn-sm" disabled={busy} onClick={copyPrompt}>{t('bank.copyFullPrompt')}</button></details>
          {task.mode==='api' && !task.import_session_id && task.status!=='RUNNING' && <button className="btn btn-primary" disabled={busy} onClick={run}>{busy?t('bank.submittingEllipsis'):task.status==='FAILED'?t('bank.retryApiGeneration'):t('bank.startApiGeneration')}</button>}
          {task.mode==='external' && !task.import_session_id && <div><label className="field"><span>{t('bank.pasteLlmReply')}</span><textarea className="textarea" rows={10} aria-label={t('bank.fullLlmReply')} placeholder={t('bank.pasteLlmReplyHint')} value={response} onChange={e=>setResponse(e.target.value)}/></label><button className="btn btn-primary" disabled={busy || !response.trim()} onClick={submitResponse}>{busy?t('bank.validating'):t('bank.validateAndDraft')}</button><p className="hint">{t('bank.validationHelp')}</p></div>}
          {task.raw_response && <details style={{marginTop:14}}><summary>{t('bank.retainedModelOutput')}</summary><pre style={{whiteSpace:'pre-wrap',maxHeight:300,overflow:'auto'}}>{task.raw_response}</pre></details>}
        </section>
        {task.review && <section className="card" style={panel}>
          <h3>{t('bank.reviewBatch', { code: task.review.short_code })}</h3>
          <p className="hint">{t('bank.reviewBatchHelp')}</p>
          {task.input_snapshot.source_analysis_id && <div className="demo-banner" style={{marginBottom:14}}><b>{t('bank.projectIssueAssessment')}</b><p>{t('bank.batchAssessmentReady', { count: assessedBatchQuestionIds(rows).length })}</p><button className="btn btn-primary" disabled={busy || !assessedBatchQuestionIds(rows).length} onClick={assessBatch}>{t('bank.startBatchAssessment')}</button></div>}
          <div style={{...toolbar,marginBottom:16}}><Link className="btn btn-outline btn-sm" to="/bank?review=1">{t('bank.openReviewQueue')}</Link><Link className="btn btn-ghost btn-sm" to="/bank?scope=mine">{t('bank.viewMyQuestions')}</Link></div>
          {rows.length===0 && <p>{t('bank.noQuestionsInBatch')}</p>}
          {rows.map(row=>{
            const item=row.generation_meta?.item;
            const blocked=approvalBlock(row);
            const project=taskProjects.find(p=>p.id===item?.project_id);
            return <article key={row.id} style={{borderTop:'1px solid var(--border)',padding:'20px 0'}}>
              <div style={toolbar}><span className="chip chip-slate">{Object.prototype.hasOwnProperty.call(ITEM_LABELS, row.status) ? t(ITEM_LABELS[row.status]) : row.status}</span>{item && <span className="chip chip-purple">{workshopQuestionLabel(item.question_mode)}</span>}<span className="hint">{row.generation_meta?.source==='api'?t('bank.apiGeneration'):t('bank.externalChatImport')}</span></div>
              <h3>{row.title}</h3>
              {item && <><p className="hint">{t('bank.batchOriginalQuestion', { question: item.original_question || t('bank.newQuestionInBatch') })}{item.source_question_id ? t('bank.sourceQuestionId', { id: item.source_question_id }):''}</p>{project && <p>{t('bank.batchProjectName', { name: project.name })}</p>}{item.fact_refs.length>0 && <details><summary>{t('bank.viewCitedProjectFacts')}</summary><ul>{item.fact_refs.map(ref=><li key={ref}>{project?.facts.find(f=>f.id===ref)?.text || t('bank.factAbsentInSnapshot')} <small>({ref})</small></li>)}</ul></details>}</>}
              {!!row.is_duplicate_of && <p className="hint">{t('bank.approveDuplicate', { id: row.is_duplicate_of })}</p>}
              {blocked && row.status==='DRAFT' && <p role="status" className="demo-banner">{item?.missing_context?.length ? t('bank.approvalMissingContext', { reason: blocked, details: item.missing_context.join(t('bank.clauseSeparator')) }) : blocked}</p>}
              {row.status==='APPROVED' && row.question_id && <button className="btn btn-primary btn-sm" disabled={busy} onClick={()=>practice(row)}>{busy?t('bank.preparingExam'):t('bank.practiceThisQuestion')}</button>}
              {editingItem===row.id && item ? <QuestionDraftEditor key={row.id} item={item} skills={task.input_snapshot.skills || SKILLS.map(({key, labelKey}) => ({key, name: t(labelKey)}))} projects={taskProjects} busy={busy} onCancel={()=>setEditingItem(null)} onSave={value=>saveItem(row,value)}/> : <>
                <details open={rows.length===1}><summary>{t('bank.viewFullAnswer')}</summary>{row.draft_answer ? <div className="bank-answer" dangerouslySetInnerHTML={{__html:sanitizeReviewAnswer(row.draft_answer)}}/>:<p>{t('bank.noAnswer')}</p>}</details>
                {row.status==='DRAFT' && <div style={{...toolbar,marginTop:14}}><Link className="btn btn-primary btn-sm" to={`/bank?review=1&item=${task.import_session_id}-${row.id}`}>{t('bank.reviewThisQuestion')}</Link><button className="btn btn-outline btn-sm" disabled={busy || !item || editingItem!==null} onClick={()=>setEditingItem(row.id)}>{t('bank.editAnswerAndRubric')}</button></div>}
              </>}
            </article>;
          })}
        </section>}
      </>}
      <section className="card" style={panel}><h3>{t('bank.generationHistory')}</h3>{!loading && history.length===0 && <p className="hint">{t('bank.noGenerationHistory')}</p>}{history.map(h=><div key={h.id} style={{...toolbar,justifyContent:'space-between',padding:'12px 0',borderBottom:'1px solid var(--border)'}}><Link to={`/bank/workshop?task=${h.id}`}>{t('bank.taskHistoryLink', { id: h.id, kind: t(h.kind === 'import' ? 'bank.adaptNotesKind' : 'bank.fillGapsKind'), mode: h.mode === 'api' ? 'API' : t('bank.externalChat') })}</Link><span className="hint">{t('bank.taskHistoryStatus', { status: taskStatus(h.status), date: h.created_at ? new Date(h.created_at).toLocaleString(locale) : '' })}</span></div>)}</section>
    </div>
  </div>;
}
