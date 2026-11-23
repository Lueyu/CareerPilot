import React, {useEffect, useRef, useState} from 'react';
import {Link, useLocation, useNavigate} from 'react-router-dom';
import {api} from '../api/client';
import {PageHead} from '../components/ui';
import ProjectContextEditor from '../components/ProjectContextEditor';
import QuestionDraftEditor from '../components/QuestionDraftEditor';
import {approvalBlock, assessedBatchQuestionIds, beginProjectAssessment, beginQuestionPractice, GeneratedItem, ITEM_LABELS, messageOf, ProjectRecord, QUESTION_LABELS, SKILLS, TASK_LABELS, WorkshopRow, WorkshopSuggestion, WorkshopTask} from '../lib/questionWorkshop';
import {issueWorkshopPath, ProjectAnalysisRun} from '../lib/projectIntelligence';
import {SourceCitations} from '../components/ProjectAnalysisPanel';
import {sanitizeReviewAnswer} from './BankReviewQueue';

const panel: React.CSSProperties = {padding:20,marginBottom:20};
const toolbar: React.CSSProperties = {display:'flex',gap:10,flexWrap:'wrap',alignItems:'center'};

export default function QuestionWorkshop() {
  const location = useLocation();
  const navigate = useNavigate();
  const query = new URLSearchParams(location.search);
  const taskId = query.get('task');
  const sourceImportId = query.get('source_import');
  const projectId = Number(query.get('project'));
  const [projects,setProjects] = useState<ProjectRecord[]>([]);
  const [suggestions,setSuggestions] = useState<WorkshopSuggestion[]>([]);
  const [history,setHistory] = useState<WorkshopTask[]>([]);
  const [loading,setLoading] = useState(true);
  const [error,setError] = useState('');
  const [notice,setNotice] = useState('');
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
  const [sourceError,setSourceError]=useState('');
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
    const [p,s,h] = await Promise.all([api.get<{items:ProjectRecord[]}>('/projects'),api.get<{items:WorkshopSuggestion[]}>('/question-generation/suggestions'),api.get<{items:WorkshopTask[]}>('/question-generation')]);
    setProjects(p.items);setSuggestions(s.items);setHistory(h.items);
  };
  useEffect(()=>{
    let active = true;
    Promise.all([api.get<{items:ProjectRecord[]}>('/projects'),api.get<{items:WorkshopSuggestion[]}>('/question-generation/suggestions'),api.get<{items:WorkshopTask[]}>('/question-generation')])
      .then(([p,s,h])=>{if(active){setProjects(p.items);setSuggestions(s.items);setHistory(h.items);}})
      .catch(e=>{if(active)setError(`加载失败：${messageOf(e)}`);}).finally(()=>{if(active)setLoading(false);});
    return ()=>{active=false;};
  },[]);
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
    if(!sourceProjectId || !sourceIssueIds.length){setSourceError('项目问题来源不完整，请从项目分析报告重新选择。');setSourceLoading(false);return;}
    setSourceLoading(true);
    api.get<ProjectAnalysisRun>(`/projects/${sourceProjectId}/analyses/${sourceAnalysisId}`).then(run=>{
      if(!active)return;
      const completeReport=run.report_status==='VALID' && run.coverage_status==='COMPLETE';
      if(!run.report || (!completeReport && run.status!=='SUCCESS'))throw new Error('这次项目分析尚未形成完整有效报告，不能据此出题');
      if(run.is_latest_success===false)throw new Error('这份分析已被更新版本替代，请回到项目报告重新选择问题');
      const issues=run.report.issues.filter(issue=>sourceIssueIds.includes(issue.id));
      if(issues.length!==new Set(sourceIssueIds).size)throw new Error('所选项目问题不属于这份分析，请重新选择');
      if(!run.active_issue_ids || sourceIssueIds.some(id=>!run.active_issue_ids?.includes(id)))throw new Error('所选项目问题对应的记忆尚未确认、需要重新确认或已停用，请从项目报告核对后重新选择');
      setSourceAnalysis(run);setKind('supplement');setProjectIds(old=>[...new Set([sourceProjectId,...old])]);
      setSkills([...new Set(issues.map(issue=>issue.skill_key))]);
      setPurpose('围绕以下项目问题，按解释当前实现、技术取舍、故障分析、改进设计递进出题：'+issues.map(issue=>issue.title).join('；')+'。项目风险不代表本人已经存在能力短板，需实际作答后评分。');
    }).catch(e=>{if(active)setSourceError(messageOf(e));}).finally(()=>{if(active)setSourceLoading(false);});
    return ()=>{active=false;};
  },[sourceAnalysisId,sourceProjectId,issueSignature,taskId]);
  useEffect(()=>{
    let active = true;
    setTask(null);setEditingItem(null);setResponse('');setNotice('');setError('');
    if(taskId) api.get<WorkshopTask>(`/question-generation/${taskId}`).then(t=>{if(active){setTask(t);setResponse(t.raw_response || '');}}).catch(e=>{if(active)setError(messageOf(e));});
    return ()=>{active=false;};
  },[taskId]);
  useEffect(()=>{
    if(!task || task.status !== 'RUNNING') return;
    let active=true;
    const timer=setInterval(()=>{
      api.get<WorkshopTask>(`/question-generation/${task.id}`).then(t=>{if(active){setTask(t);if(t.status!=='RUNNING'){setResponse(t.raw_response || '');void loadResources().catch(()=>{});}}}).catch(e=>{if(active)setError(`状态读取失败：${messageOf(e)}。可刷新继续查看。`);});
    },2500);
    return ()=>{active=false;clearInterval(timer);};
  },[task?.id,task?.status]);
  const perform = async (fn:()=>Promise<void>) => {
    setBusy(true);setError('');setNotice('');
    try {await fn();} catch(e){setError(messageOf(e));} finally {setBusy(false);}
  };
  const refreshTask = async () => {
    if(task) setTask(await api.get<WorkshopTask>(`/question-generation/${task.id}`));
  };
  const createTask = () => perform(async()=>{
    if(sourceAnalysisId && (!sourceAnalysis || sourceError || sourceLoading))throw new Error('请先加载并核对项目问题来源');
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
    await refreshTask();setEditingItem(null);setNotice('草稿已保存并重新排版，尚未入库。');
  });
  const copyPrompt=()=>perform(async()=>{
    if(!task)return;
    if(!navigator.clipboard) throw new Error('浏览器不支持自动复制，请选中下方完整 Prompt 手动复制。');
    await navigator.clipboard.writeText(task.prompt_text);setNotice('完整 Prompt 已复制，可粘贴到你的 LLM 聊天会话。');
  });
  const practice=(row:WorkshopRow)=>perform(async()=>{
    if(!row.question_id) throw new Error('此题尚未入库');
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
      if(!boundProject || !issueIds.length){setError('原任务的项目问题来源不完整，请回到项目报告重新选择问题；未改为普通补题。');return;}
      path=issueWorkshopPath(boundProject,analysisId,issueIds);
      setProjectIds(contexts.map(p=>p.id));setMode(task.mode);setCount(snapshot.count);
    }
    createAttempt.current={payload:'',key:''};setNotice('');setError('');navigate(path);
  };
  const rows=task?.review?.items || [];
  const taskProjects=task?.input_snapshot?.project_context || [];

  return <div className="question-workshop">
    <PageHead title="面经与补题工作台" sub="结合真实项目整理面经，或按缺口补充题目；审核后进入个人题库。"><Link className="btn btn-outline" to="/bank">返回题库</Link></PageHead>
    <p className="hint" style={{marginBottom:18}}>通用问题直接回答，项目问题依据你的资料改写。生成的参考答案需要审核；新增题目不会改变能力分数。</p>
    {error && <div role="alert" className="demo-banner demo-banner-err" style={{marginBottom:16}}>{error}<button className="btn btn-ghost btn-sm" onClick={()=>setError('')}>关闭提示</button></div>}
    {notice && <div role="status" className="demo-banner" style={{marginBottom:16}}>{notice}</div>}
    {loading && <div className="card" style={panel}>正在加载项目、建议和生成记录…</div>}
    <div style={{display:'grid',gridTemplateColumns:'minmax(0,1fr)',gap:0}}>
      {editingProject!==undefined && <ProjectContextEditor key={editingProject?.id || 'new'} project={editingProject} onCancel={()=>setEditingProject(undefined)} onSaved={async()=>{setEditingProject(undefined);await loadResources();setNotice('项目资料已保存。已创建任务仍使用原有快照，请创建新任务使用新资料。');}} />}
      {!task && !taskId && <>
        <section className="card" style={panel}>
          <div style={{...toolbar,justifyContent:'space-between'}}><h3 style={{margin:0}}>1. 准备真实项目资料</h3><button className="btn btn-outline btn-sm" onClick={()=>setEditingProject(null)}>添加无代码项目</button></div>
          <p className="hint">勾选本次可以引用的项目。GitHub / GitCode 项目可先在项目中心导入，再在这里补充自己的角色和经历。</p>
          {!loading && projects.length===0 && <p>还没有项目。通用补题不需要项目；项目题可以先添加工作或个人项目。</p>}
          {projects.map(p=>{
            const profile=p.profile || p.meta?.profile || {};
            return <div key={p.id} style={{...toolbar,padding:'12px 0',borderBottom:'1px solid var(--border)'}}>
              <label style={{flex:1,display:'flex',gap:10,alignItems:'center'}}><input type="checkbox" checked={projectIds.includes(p.id)} disabled={!!sourceAnalysisId && p.id===sourceProjectId} onChange={e=>setProjectIds(old=>e.target.checked?[...old,p.id]:old.filter(id=>id!==p.id))}/><strong>{p.name}</strong><span className="chip chip-slate">{((p.kind || p.meta?.kind)==='manual')?'手工项目':((p.kind || p.meta?.kind)==='gitcode')?'GitCode 项目':'GitHub 项目'}</span></label>
              <span className="hint">{profile.role && profile.responsibilities ? '已填写角色与职责':'待补个人角色或职责'}</span><button className="btn btn-outline btn-sm" onClick={()=>setEditingProject(p)}>编辑真实经历</button>
            </div>;
          })}
        </section>
        <section className="card" style={panel}>
          <h3>2. 选择本次整理目标</h3>
          {sourceAnalysisId && <div className="demo-banner" style={{marginBottom:16}}>
            <b>从项目分析 #{sourceAnalysisId} 准备考核</b>
            {sourceLoading && <p role="status">正在读取所选项目问题…</p>}
            {sourceError && <p role="alert">无法使用项目来源：{sourceError}</p>}
            {sourceError && sourceProjectId && <Link to={`/assets/project/${sourceProjectId}`}>回到项目报告重新选择问题 →</Link>}
            {sourceAnalysis?.report?.issues.filter(issue=>sourceIssueIds.includes(issue.id)).map(issue=><div key={issue.id}><p><strong>{issue.title}</strong> · {issue.status==='hypothesis'?'待验证推断':'代码观察'}</p><p>{issue.detail}</p><SourceCitations citations={issue.citations}/></div>)}
            {sourceAnalysis?.issue_memories?.filter(m=>sourceIssueIds.includes(m.issue_id) && m.user_corrected).map(m=><div key={m.memory_id}><b>当前记忆已纠正 · #{m.memory_id}</b><p style={{whiteSpace:'pre-wrap'}}>{m.content}</p><p className="hint">创建任务时使用有效记忆，原分析保留作历史参考。</p></div>)}
            <p className="hint">项目问题不等于个人能力短板。本批次保留分析版本与问题来源，审核后再实际作答评分。</p>
            <button className="btn btn-ghost btn-sm" onClick={()=>{setSourceAnalysisId(null);setSourceIssueIds([]);setSourceAnalysis(null);setSourceError('');navigate('/bank/workshop',{replace:true});}}>取消项目问题来源，改为普通补题</button>
          </div>}
          {suggestions.length>0 && <details style={{marginBottom:18}} open><summary>系统建议 · 可选择后调整</summary><div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(240px,1fr))',gap:10,marginTop:12}}>{suggestions.map(s=><div key={s.id} style={{border:'1px solid var(--border)',borderRadius:8,padding:14}}><strong>{s.title}</strong><p className="hint">{s.reason}</p><button className="btn btn-outline btn-sm" onClick={()=>{setKind(s.kind);setCount(s.count);setSkills(s.skill_keys.length ? s.skill_keys : SKILLS.map(skill=>skill.key));setProjectIds(s.project_ids);setPurpose(s.purpose);setSourceAnalysisId(s.source_analysis_id || null);setSourceIssueIds(s.source_issue_ids || []);setSourceProjectId(s.project_ids[0] || null);setNotice(`已选择「${s.title}」，可以调整后创建任务。`);}}>使用此建议</button></div>)}</div></details>}
          <div className="form-row"><label className="field"><span>任务类型</span><select className="select" aria-label="任务类型" value={kind} onChange={e=>setKind(e.target.value as typeof kind)}><option value="import">整理面经，结合项目改写</option><option value="supplement">主动补充题库</option></select></label><label className="field"><span>生成题量</span><input className="inp" type="number" min={1} max={20} aria-label="生成题量" value={count} onChange={e=>setCount(Number(e.target.value))}/></label></div>
          {kind==='import' && <label className="field"><span>面经原文{sourceImportId ? `（将同时读取导入会话 #${sourceImportId}）`:''}</span><textarea className="textarea" aria-label="面经原文" rows={7} maxLength={60000} placeholder="粘贴面经帖子，保留原问题和相关背景。无需预先改写答案。" value={sourceText} onChange={e=>setSourceText(e.target.value)}/></label>}
          <label className="field"><span>补题目标或额外要求</span><textarea className="textarea" aria-label="补题目标" rows={3} maxLength={3000} placeholder="例如：补充检索失败排查、检索评估和项目取舍问题。" value={purpose} onChange={e=>setPurpose(e.target.value)}/></label>
          <fieldset style={{border:0,padding:0,marginBottom:18}}><legend style={{marginBottom:8}}>涉及技能（至少选择一项）</legend><div style={toolbar}>{SKILLS.map(s=><label key={s.key} style={{display:'flex',gap:5,alignItems:'center'}}><input type="checkbox" checked={skills.includes(s.key)} onChange={e=>setSkills(old=>e.target.checked?[...old,s.key]:old.filter(k=>k!==s.key))}/>{s.name}</label>)}</div></fieldset>
          <fieldset style={{border:0,padding:0}}><legend style={{marginBottom:8}}>执行方式</legend><div style={toolbar}><label><input type="radio" name="generation-mode" value="api" checked={mode==='api'} onChange={()=>setMode('api')}/> 使用已配置的 LLM API</label><label><input type="radio" name="generation-mode" value="external" checked={mode==='external'} onChange={()=>setMode('external')}/> 复制 Prompt 到聊天，再粘贴结果</label></div></fieldset>
          <p className="hint">创建任务会保存资料快照和完整 Prompt。API 生成需下一步启动；外部聊天回填只做校验与排版。</p>
          <button className="btn btn-primary" disabled={busy || loading || (!!sourceAnalysisId && (!sourceAnalysis || sourceLoading || !!sourceError)) || !skills.length || !Number.isInteger(count) || count<1 || count>20 || (kind==='import' && !sourceText.trim() && !sourceImportId)} onClick={createTask}>{busy?'正在准备…':'创建任务并检查输入'}</button>
        </section>
      </>}
      {taskId && !task && <div className="card" style={panel}>正在读取任务 #{taskId}… <Link to="/bank/workshop">返回新任务</Link></div>}
      {task && <>
        <section className="card" style={panel}>
          <div style={{...toolbar,justifyContent:'space-between'}}><h3 style={{margin:0}}>任务 #{task.id} · {TASK_LABELS[task.status] || task.status}</h3><button className="btn btn-outline btn-sm" disabled={busy} onClick={createNewTask}>创建新任务</button></div>
          <p className="hint">{task.kind==='import'?'面经优化':'题库补充'} · {task.mode==='api'?'API 生成':'外部聊天回填'} · 请求 {task.input_snapshot?.count} 题 · {task.created_at ? new Date(task.created_at).toLocaleString():''}</p>
          <details style={{marginBottom:16}}><summary>本次使用的资料快照与事实来源</summary>
            {task.input_snapshot.source_analysis_id && <p>来源项目分析 #{task.input_snapshot.source_analysis_id} · 项目问题：{task.input_snapshot.source_issue_ids?.join('、')}。项目风险须通过实际考核验证个人掌握。</p>}
            <p>{task.input_snapshot?.purpose || '未填写额外要求'}</p>
            {task.input_snapshot?.source_text && <details><summary>原始面经</summary><pre style={{whiteSpace:'pre-wrap',maxHeight:240,overflow:'auto'}}>{task.input_snapshot.source_text}</pre></details>}
            {taskProjects.length===0 && <p className="hint">本次未选择项目，将整理通用答案。</p>}
            {taskProjects.map(p=><div key={p.id} style={{marginTop:12}}><strong>{p.name}</strong><p className="hint">{p.has_personal_context?'已提供个人经历资料':'个人经历尚不完整，不能把代码观察写成本人贡献'}</p><ul>{p.facts.map(f=><li key={f.id} style={{marginBottom:8}}><span className="chip chip-slate">{f.source==='user_statement' ? '用户陈述':f.user_corrected?'用户纠正的项目观察':'仓库观察'}</span> {f.text}<small style={{display:'block',color:'var(--text-3)'}}>{f.id} · {f.source}{f.analysis_id?` · 分析 #${f.analysis_id}`:''}{f.source_revision?` · 版本 ${f.source_revision}`:''}</small>{!!f.citations?.length && <SourceCitations citations={f.citations}/>}</li>)}</ul></div>)}
          </details>
          {task.error && <p role="alert" className="demo-banner demo-banner-err">本次失败：{task.error}</p>}
          {task.status==='RUNNING' && <p role="status">模型正在生成和校验答案。可以离开此页，稍后从生成记录继续；请勿重复启动。</p>}
          <details open={task.mode==='external' && !task.import_session_id} style={{marginBottom:16}}><summary>完整 Prompt{task.mode==='external'?' · 复制到你的 LLM 聊天':''}</summary><p className="hint">包含上方选中的项目资料；请确认愿意交给所选模型。Prompt 已要求模型按固定格式返回，你无需手写 JSON。</p><textarea readOnly className="textarea" rows={10} aria-label="完整 Prompt" value={task.prompt_text || ''}/><button className="btn btn-outline btn-sm" disabled={busy} onClick={copyPrompt}>复制完整 Prompt</button></details>
          {task.mode==='api' && !task.import_session_id && task.status!=='RUNNING' && <button className="btn btn-primary" disabled={busy} onClick={run}>{busy?'提交中…':task.status==='FAILED'?'重试本次 API 生成':'启动 API 生成'}</button>}
          {task.mode==='external' && !task.import_session_id && <div><label className="field"><span>粘贴 LLM 的完整回复</span><textarea className="textarea" rows={10} aria-label="LLM 完整回复" placeholder="包括模型返回的 JSON 代码块也可以直接粘贴；系统会校验、排版后进入审核。" value={response} onChange={e=>setResponse(e.target.value)}/></label><button className="btn btn-primary" disabled={busy || !response.trim()} onClick={submitResponse}>{busy?'校验中…':'校验并生成审核草稿'}</button><p className="hint">此操作不会调用 LLM API。格式不符合要求时会保留原文，你可将错误和 Prompt 交给聊天模型修正后重新粘贴。</p></div>}
          {task.raw_response && <details style={{marginTop:14}}><summary>保留的原始模型输出</summary><pre style={{whiteSpace:'pre-wrap',maxHeight:300,overflow:'auto'}}>{task.raw_response}</pre></details>}
        </section>
        {task.review && <section className="card" style={panel}>
          <h3>审核答案 · {task.review.short_code}</h3>
          <p className="hint">检查具体答案、追问与评分要点，再选择入库。项目变体沿用原题关系，不能当成独立能力证据。</p>
          {task.input_snapshot.source_analysis_id && <div className="demo-banner" style={{marginBottom:14}}><b>项目问题专项考核</b><p>本批次已有 {assessedBatchQuestionIds(rows).length} 道审核通过的题目可用于考核。新答卷会经过现有评分与学习安排流程。</p><button className="btn btn-primary" disabled={busy || !assessedBatchQuestionIds(rows).length} onClick={assessBatch}>开始本批次考核</button></div>}
          <div style={{...toolbar,marginBottom:16}}><Link className="btn btn-outline btn-sm" to="/bank?review=1">打开待审核队列</Link><Link className="btn btn-ghost btn-sm" to="/bank?scope=mine">查看我的题目</Link></div>
          {rows.length===0 && <p>本次没有审核题目。</p>}
          {rows.map(row=>{
            const item=row.generation_meta?.item;
            const blocked=approvalBlock(row);
            const project=taskProjects.find(p=>p.id===item?.project_id);
            return <article key={row.id} style={{borderTop:'1px solid var(--border)',padding:'20px 0'}}>
              <div style={toolbar}><span className="chip chip-slate">{ITEM_LABELS[row.status] || row.status}</span>{item && <span className="chip chip-purple">{QUESTION_LABELS[item.question_mode]}</span>}<span className="hint">{row.generation_meta?.source==='api'?'API 生成':'外部聊天回填'}</span></div>
              <h3>{row.title}</h3>
              {item && <><p className="hint">原题：{item.original_question || '本批次新拟题'}{item.source_question_id ? ` · 来源题 #${item.source_question_id}`:''}</p>{project && <p>项目：{project.name}</p>}{item.fact_refs.length>0 && <details><summary>查看答案引用的项目事实</summary><ul>{item.fact_refs.map(ref=><li key={ref}>{project?.facts.find(f=>f.id===ref)?.text || '快照中无此事实'} <small>({ref})</small></li>)}</ul></details>}</>}
              {!!row.is_duplicate_of && <p className="hint">与题目 #{row.is_duplicate_of} 重复，通过后将合并，不新增独立题目。</p>}
              {blocked && row.status==='DRAFT' && <p role="status" className="demo-banner">{blocked}{item?.missing_context?.length ? `：${item.missing_context.join('；')}`:''}</p>}
              {row.status==='APPROVED' && row.question_id && <button className="btn btn-primary btn-sm" disabled={busy} onClick={()=>practice(row)}>{busy?'准备考试…':'开始练这题'}</button>}
              {editingItem===row.id && item ? <QuestionDraftEditor key={row.id} item={item} skills={task.input_snapshot.skills || SKILLS} projects={taskProjects} busy={busy} onCancel={()=>setEditingItem(null)} onSave={value=>saveItem(row,value)}/> : <>
                <details open={rows.length===1}><summary>查看完整答案、追问与评分要点</summary>{row.draft_answer ? <div className="bank-answer" dangerouslySetInnerHTML={{__html:sanitizeReviewAnswer(row.draft_answer)}}/>:<p>暂无答案</p>}</details>
                {row.status==='DRAFT' && <div style={{...toolbar,marginTop:14}}><Link className="btn btn-primary btn-sm" to={`/bank?review=1&item=${task.import_session_id}-${row.id}`}>审核这道题</Link><button className="btn btn-outline btn-sm" disabled={busy || !item || editingItem!==null} onClick={()=>setEditingItem(row.id)}>编辑答案与评分要点</button></div>}
              </>}
            </article>;
          })}
        </section>}
      </>}
      <section className="card" style={panel}><h3>生成记录</h3>{!loading && history.length===0 && <p className="hint">还没有生成任务。创建后可在这里恢复处理。</p>}{history.map(h=><div key={h.id} style={{...toolbar,justifyContent:'space-between',padding:'12px 0',borderBottom:'1px solid var(--border)'}}><Link to={`/bank/workshop?task=${h.id}`}>#{h.id} · {h.kind==='import'?'面经优化':'题库补充'} · {h.mode==='api'?'API':'外部聊天'}</Link><span className="hint">{TASK_LABELS[h.status] || h.status} · {h.created_at ? new Date(h.created_at).toLocaleString():''}</span></div>)}</section>
    </div>
  </div>;
}
