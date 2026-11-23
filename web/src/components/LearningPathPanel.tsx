import React,{useEffect,useRef,useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {api,getWorkspace,LearningTaskView} from '../api/client';
import {LearningAssistance,LearningPathData,LearningSubmission,LearningSubmissionAck,LEARNING_PENDING,LEARNING_RUN_LABELS,learningError} from '../lib/learningPath';
import ProjectPracticePanel,{ProjectActivityPicker} from './ProjectPracticePanel';
import LearningMemoryReview from './LearningMemoryReview';
import LearningPlanControls from './LearningPlanControls';

const verificationLabels:Record<string,string>={UNVERIFIED:'未验证',ASSISTED_CORRECT:'有辅助正确',INDEPENDENT_CORRECT:'独立正确',PRACTICE_CORRECT:'本题练习正确，独立理解仍待新题验证',NOT_SUBMITTED:'未提交',PENDING_REVIEW:'待检查',NEEDS_REVISION:'需修改',TASK_VERIFIED:'本任务已验证',CODE_REVIEWED:'代码检查已完成，运行效果未验证',PENDING_RETEST:'待复测',PASSED_THIS_ATTEMPT:'本次通过',NOT_DUE:'未到期'};
const emptyAssistance:LearningAssistance={hint_used:false,reference_viewed:false,ai_declared:false,feedback_seen:false};
const key=()=>globalThis.crypto?.randomUUID?.() || `learning-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const contentText=(value:unknown):string=>typeof value==='string'?value:Array.isArray(value)?value.filter(v=>typeof v==='string').join('\n'):value && typeof value==='object' && 'text' in value?String(value.text):'';

export default function LearningPathPanel({task,onProgressSaved}:{task:LearningTaskView;onProgressSaved?:()=>void}){
  const navigate=useNavigate();
  const [path,setPath]=useState<LearningPathData|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[refresh,setRefresh]=useState(0),[paused,setPaused]=useState(false);
  const [selected,setSelected]=useState<number|null>(null),[answer,setAnswer]=useState(''),[ai,setAi]=useState(false),[draftError,setDraftError]=useState('');
  const [detail,setDetail]=useState<LearningSubmission|null>(null),[help,setHelp]=useState<{kind:string;text:string}|null>(null),[notice,setNotice]=useState('');
  const currentTask=useRef(task.id);currentTask.current=task.id;
  const mounted=useRef(true),pending=useRef(false),requests=useRef<Record<string,string>>({});
  const pendingSubmission=useRef<number|null>(null);
  const activity=path?.activities.find(a=>a.id===selected) || path?.activities[0];
  const history=(path?.submissions || []).filter(s=>s.activity_id===activity?.id).sort((a,b)=>b.revision-a.revision || b.id-a.id);
  const latest=history[0] || activity?.latest_submission || null;
  const running=!!pendingSubmission.current || !!latest && LEARNING_PENDING.has(latest.status);
  const activityPaused=!!activity && (['DEFERRED','SKIPPED'].includes(activity.status) || path?.goal?.status!=='ACTIVE');
  const assistance=activity?.assistance || emptyAssistance;
  const draftKey=activity?`cp-learning-draft:${getWorkspace()}:${task.id}:${activity.id}:${activity.revision}`:'';
  const requestKey=(signature:string)=>requests.current[signature] ||= key();

  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{setPath(null);setSelected(null);setDetail(null);setHelp(null);setError('');setNotice('');pendingSubmission.current=null;requests.current={};},[task.id]);
  useEffect(()=>{
    let alive=true,reads=0,timer:ReturnType<typeof setTimeout>|undefined;
    const taskId=task.id;setLoading(true);setPaused(false);
    const read=async()=>{
      try{
        const data=await api.get<LearningPathData>(`/learning/tasks/${taskId}/path`);
        if(!alive)return;
        if(data.task_id!==taskId || !Array.isArray(data.activities) || !Array.isArray(data.submissions))throw new Error('学习路径响应不完整，请重试核对');
        setPath(data);setError('');setLoading(false);reads++;
        if(pendingSubmission.current && data.submissions.some(s=>s.id===pendingSubmission.current))pendingSubmission.current=null;
        if(data.submissions.some(s=>LEARNING_PENDING.has(s.status)) || pendingSubmission.current){
          if(reads<120)timer=setTimeout(()=>void read(),5000);else setPaused(true);
        }
      }catch(e){if(alive){setError(`学习路径读取失败：${learningError(e)}。若服务尚未升级，可从下方原任务入口查看已有教学；请启动配套新版 API 后再使用练习反馈`);setLoading(false);}}
    };
    void read();return()=>{alive=false;if(timer)clearTimeout(timer);};
  },[task.id,refresh]);
  useEffect(()=>{
    setDetail(null);setHelp(null);setNotice('');setDraftError('');
    if(!activity){setAnswer('');setAi(false);return;}
    try{
      const raw=localStorage.getItem(draftKey),draft=raw?JSON.parse(raw):null;
      setAnswer(typeof draft?.answer==='string'?draft.answer:latest?.payload?.text_answer || '');setAi(draft?.ai===true);
    }catch{setAnswer('');setAi(false);setDraftError('无法读取浏览器草稿，请保留一份自己的副本。');}
  },[draftKey]);
  const saveDraft=(text:string,aiDeclared:boolean)=>{
    setAnswer(text);setAi(aiDeclared);
    try{localStorage.setItem(draftKey,JSON.stringify({answer:text,ai:aiDeclared}));setDraftError('');}catch{setDraftError('浏览器草稿保存失败，请保留输入副本；提交后才保存到服务器。');}
  };
  const perform=async(action:()=>Promise<void>)=>{
    if(pending.current)return;pending.current=true;setBusy(true);setError('');setNotice('');
    try{await action();}catch(e){if(mounted.current)setError(learningError(e));}finally{pending.current=false;if(mounted.current)setBusy(false);}
  };
  const prepare=()=>perform(async()=>{
    const id=task.id;const data=await api.post<LearningPathData>(`/learning/tasks/${id}/prepare`);
    if(currentTask.current!==id)return;
    if(data.task_id!==id || !data.goal)throw new Error('尚未收到有效学习目标，请重试核对');
    setPath(data);
  });
  const submit=()=>perform(async()=>{
    if(!activity || !answer.trim() || running || activityPaused)return;
    const id=task.id,activityId=activity.id;
    const payload={text_answer:answer,ai_declared:ai};
    const result=await api.post<LearningSubmissionAck>(`/learning/activities/${activityId}/submissions`,{payload,expected_activity_revision:activity.revision,parent_submission_id:latest?.id || null,request_key:requestKey(JSON.stringify([activityId,activity.revision,latest?.id,payload]))});
    if(currentTask.current!==id)return;
    if(!result.submission_id)throw new Error('未收到提交记录，草稿已保留，请重试核对');
    pendingSubmission.current=result.submission_id;setDetail(null);setHelp(null);setNotice('练习已提交，等待本次后台检查；不会按提交次数提高能力。');setRefresh(x=>x+1);
  });
  const viewFeedback=(submission:LearningSubmission)=>perform(async()=>{
    const id=task.id;const value=await api.get<LearningSubmission>(`/learning/submissions/${submission.id}`);
    if(currentTask.current!==id)return;
    if(value.id!==submission.id || value.activity_id!==activity?.id)throw new Error('反馈不属于当前提交，请刷新核对');
    setDetail(value);
    setPath(old=>old?{...old,activities:old.activities.map(a=>a.id===value.activity_id?{...a,assistance:{...a.assistance,feedback_seen:true}}:a)}:old);
  });
  const askHelp=(kind:'HINT'|'REFERENCE')=>perform(async()=>{
    if(!activity || activityPaused)return;const id=task.id,activityId=activity.id;
    const result=await api.post<{assistance:LearningAssistance;content:unknown}>(`/learning/activities/${activityId}/assistance`,{kind,expected_activity_revision:activity.revision,request_key:requestKey(`assistance:${activityId}:${activity.revision}:${kind}`)});
    if(currentTask.current!==id)return;
    const displayed=contentText(result.content);if(!displayed || !result.assistance)throw new Error('辅助记录或内容不完整，暂不展示，请重试');
    setPath(old=>old?{...old,activities:old.activities.map(a=>a.id===activityId?{...a,assistance:result.assistance}:a)}:old);setHelp({kind,text:displayed});
  });
  const retry=()=>perform(async()=>{
    if(!latest?.can_retry)return;const id=task.id;
    const result=await api.post<LearningSubmissionAck>(`/learning/submissions/${latest.id}/retry`);
    if(currentTask.current!==id)return;
    if(result.submission_id!==latest.id)throw new Error('重试回执不属于本次提交，请刷新核对');
    pendingSubmission.current=result.submission_id;setRefresh(x=>x+1);
  });
  const complete=()=>perform(async()=>{
    const id=task.id;await api.post(`/learning/tasks/${id}/complete`,{artifact_text:answer});
    if(currentTask.current!==id)return;setNotice('学习进度已记录；理解、迁移和保持仍按各自验证结果显示。');setRefresh(x=>x+1);onProgressSaved?.();
  });
  const retest=()=>perform(async()=>{
    const id=task.id;const result=await api.post<{exam_id:number}>(`/learning/tasks/${id}/retest`);
    if(currentTask.current!==id)return;if(!result.exam_id)throw new Error('未收到复测试卷，请重试核对');
    navigate(`/exam/diagnosis?id=${result.exam_id}&return_task_id=${id}`);
  });

  if(!path)return <section className="card" style={{marginBottom:16}}><h3>学习路径</h3>{error?<p role="alert">{error}</p>:<p role="status">正在读取学习路径…</p>}{!loading && <button className="btn btn-outline" onClick={()=>setRefresh(x=>x+1)}>重读学习路径</button>}</section>;
  const goal=path.goal;
  return <section style={{marginBottom:18}} aria-label="学习路径">
    {error && <p role="alert" className="demo-banner demo-banner-err">{error}。已输入的练习内容保留。</p>}
    {notice && <p role="status">{notice}</p>}
    {paused && <p role="status">自动刷新已暂停，后台任务不会因此取消。<button className="btn btn-outline btn-sm" onClick={()=>setRefresh(x=>x+1)}>继续刷新</button></p>}
    {path.worker?.online===false && <p className="hint">本地 Worker 未在线，提交会保留等待；关闭电脑期间不会执行检查。</p>}
    {!goal?<div className="card"><h3>围绕这次失分建立学习目标</h3><p>{task.source_criterion || task.title}</p><p className="hint">先准备一组短练习，再提交自己的回答获得反馈。阅读页面不会自动调用模型。</p><button className="btn btn-primary" disabled={busy} onClick={prepare}>{busy?'正在准备…':'准备练习'}</button></div>:<>
      <div className="card" style={{marginBottom:16}}><div className="card-title">当前学习目标 <button className="btn btn-ghost btn-sm" disabled={busy || loading} onClick={()=>setRefresh(x=>x+1)}>刷新路径</button></div><p>{goal.target_behavior}</p><p className="hint">练习反馈不代表独立复测通过；同题修订保留过程，不增加独立观察覆盖。</p>
        <div style={{display:'flex',gap:12,flexWrap:'wrap'}}>{Object.entries(goal.verification).map(([name,value])=><span className="chip chip-slate" key={name}>{{understanding:'理解',practice:'实践',transfer:'迁移',retention:'保持'}[name] || name}：{verificationLabels[value] || value}</span>)}</div>
        {goal.evidence_status!=='VALID' && <p className="hint">来源状态：{goal.evidence_status}。需要核对来源，不能据此作可靠能力判断。</p>}
        <details style={{marginTop:12}}><summary>为什么安排与预定标准</summary><p>来源失分项：{task.source_criterion || '历史来源未完整记录'}</p>{task.content?.observation?.answer_excerpt && <blockquote>{task.content.observation.answer_excerpt}</blockquote>}<p className="hint">计划版本 {goal.plan_revision} · 成功标准 {goal.success_criteria_version || '未记录'}</p>{goal.origin_refs && <dl>{['exam_id','exam_question_id','answer_revision_id','scoring_run_id','criterion_id','rubric_version','judge_version'].map(name=>goal.origin_refs?.[name]!=null?<React.Fragment key={name}><dt>{name}</dt><dd>{String(goal.origin_refs[name])}</dd></React.Fragment>:null)}</dl>}</details>
      </div>
      {path.activities.length===0 && <div className="card" style={{marginBottom:16}}><h3>目标已保存，尚未准备练习</h3><p className="hint">按本目标的冻结评分标准准备短练习；不会因为打开页面就生成或评分。</p><button className="btn btn-primary" disabled={busy || !goal.allowed_actions.includes('prepare')} onClick={prepare}>{busy?'正在准备…':'准备练习'}</button></div>}
      {path.learning_content && <details className="card" style={{marginBottom:16}}><summary>短讲解：类比、例子与原理</summary>{path.learning_content.analogy && <p>{path.learning_content.analogy}</p>}{path.learning_content.analogy_limits && <p>类比的局限：{path.learning_content.analogy_limits}</p>}{path.learning_content.example && <p>{path.learning_content.example}</p>}{path.learning_content.principles?.map((p,i)=><p key={i}>{p}</p>)}</details>}
      {path.activities.length>1 && <details className="card" style={{marginBottom:16}}><summary>完整活动目录（{path.activities.length} 项）</summary>{path.activities.map(a=><button key={a.id} className={'btn btn-sm '+(a.id===activity?.id?'btn-primary':'btn-outline')} style={{margin:6}} onClick={()=>setSelected(a.id)} disabled={busy}>{a.position}. {a.title}</button>)}</details>}
      {activity && <div className="card"><div className="card-title">当前活动：{activity.title}</div><p style={{whiteSpace:'pre-wrap'}}>{activity.prompt}</p>{activity.authoring_source==='deterministic_prompt_template' && <p className="hint">按冻结标准编排的规则练习，未经校准；本组练习不是模型新生成的题目。</p>}{activity.required_output && <p>提交要求：{activity.required_output}</p>}<details><summary>本次检查标准</summary>{activity.success_criteria?.map((c,i)=>typeof c==='string'?<p key={i}>{c}</p>:<div key={i}><p><b>{c.title || c.criterion_id}</b>{c.detail?`：${c.detail}`:''}</p>{c.required_evidence?.map((point,n)=><p key={n}>{point}</p>)}{c.score_anchors?.map(a=><p className="hint" key={a.anchor_id}>{a.score} 档：{a.description}</p>)}</div>)}<p className="hint">活动版本 {activity.revision} · {activity.content_version || '内容版本未记录'}</p></details>
        {activityPaused && <p role="status">当前活动已暂停、已延后或已跳过，暂不继续提交。请在“调整当前计划”中说明原因并恢复；来源失效时需先复核。</p>}{activity.kind==='CONCEPT' && <>
          <div style={{display:'flex',gap:8,margin:'12px 0'}}><button className="btn btn-outline btn-sm" disabled={busy || running || activityPaused || !activity.allowed_actions.includes('hint')} onClick={()=>askHelp('HINT')}>给我最小提示</button><button className="btn btn-outline btn-sm" disabled={busy || running || activityPaused || !activity.allowed_actions.includes('reference')} onClick={()=>askHelp('REFERENCE')}>查看参考</button></div>
          <p className="hint">辅助记录：{assistance.hint_used?'已使用提示':'未使用提示'} · {assistance.reference_viewed?'已看参考':'未看参考'} · {assistance.feedback_seen?'已看反馈':'未看反馈'}。查看辅助后，同题后续修订会保留辅助标记。</p>
          {help && <div className="demo-banner" style={{whiteSpace:'pre-wrap'}}>辅助已记录 · {help.kind==='HINT'?'最小提示':'参考说明'}<p>{help.text}</p></div>}
          <label>我的练习答案<textarea className="textarea" aria-label="练习答案" rows={6} value={answer} disabled={busy || running || activityPaused} onChange={e=>saveDraft(e.target.value,ai)} placeholder="先用自己的话尝试；提交后才进入真实检查。"/></label>
          <label style={{display:'block',margin:'8px 0'}}><input type="checkbox" aria-label="本次使用了 AI 辅助" checked={ai} disabled={busy || running || activityPaused} onChange={e=>saveDraft(answer,e.target.checked)}/>本次使用了 AI 辅助</label>
          <p className="hint">未提交内容仅保存为本浏览器草稿。AI 辅助产物可以用于学习，个人理解仍需独立验证。</p>{draftError && <p role="alert">{draftError}</p>}
          <button className="btn btn-primary" disabled={busy || running || activityPaused || !answer.trim() || !activity.allowed_actions.includes('submit')} onClick={submit}>{busy?'正在保存…':latest?'提交修订':'提交练习'}</button>
        </>}
        {activity.kind==='PROJECT' && <ProjectPracticePanel key={`${activity.id}:${activity.revision}`} activity={{...activity,allowed_actions:activityPaused?[]:activity.allowed_actions}} latest={latest} onSubmitted={ack=>{pendingSubmission.current=ack.submission_id;setDetail(null);setRefresh(x=>x+1);}}/>}
        {latest && <div style={{marginTop:16}}><h4>本次提交 · 第 {latest.revision} 版</h4><p role="status">{LEARNING_RUN_LABELS[latest.status] || latest.status}</p>{latest.error && <p role="alert">{learningError(latest.error)}。这是系统检查状态，不表示你没有掌握。</p>}{latest.can_retry && <button className="btn btn-outline" disabled={busy} onClick={retry}>重试本次检查</button>}{latest.has_feedback && <button className="btn btn-primary" disabled={busy} onClick={()=>viewFeedback(latest)}>查看本次反馈</button>}</div>}
        {detail?.feedback && <div style={{marginTop:16}} aria-label="练习反馈"><h4>{detail.id===latest?.id?'本次反馈':'历史反馈'} · 提交第 {detail.revision} 版</h4><p className="hint">{detail.feedback.scope_note || '仅用于本题学习，不代表整个能力域判断。'}</p>{(detail.payload?.text_answer || detail.payload?.answer_text) && <details><summary>查看这版原始提交</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{detail.payload.text_answer || detail.payload.answer_text}</pre></details>}{detail.feedback.project_feedback && <div className="demo-banner"><div><p>本次只做代码检查，未执行项目测试，运行效果未验证；个人理解仍待验证。</p><p>{detail.feedback.project_feedback.scope_note}</p><p className="hint">测试日志：{detail.payload?.test_evidence?.user_log_supplied?'用户提供，未经系统执行核验':'未提供'} · 验收标准 {detail.feedback.project_feedback.acceptance_version || '未记录'}</p><h5>请准备解释你的实现</h5>{detail.feedback.project_feedback.defense_questions?.map((question,i)=><p key={i}>{question}</p>)}</div></div>}{detail.feedback.versions && <p className="hint">{Object.entries(detail.feedback.versions).map(([name,value])=>`${name}: ${value}`).join(' · ')} · 评分未校准</p>}<h5>已经做到</h5>{detail.feedback.correct.length?detail.feedback.correct.map((c,i)=><div key={i}>{c.quotes.map((quote,n)=><blockquote key={n}>{quote}</blockquote>)}<p>{c.why}</p></div>):<p>本次尚未记录明确满足的要点，查看下方具体缺口。</p>}<h5>还需要补充</h5>{detail.feedback.gaps.map((g,i)=><div key={i}><b>{g.kind==='MISSING_OR_UNCLEAR'?'遗漏或尚未说清楚':g.kind}</b>{g.quotes.map((quote,n)=><blockquote key={n}>{quote}</blockquote>)}<p>{g.why}</p>{g.minimal_hint && <p>最小提示：{g.minimal_hint}</p>}</div>)}<p>下一步：{detail.feedback.next_action==='REVISE'?'修改后再提交':detail.feedback.next_action==='NEW_CONTEXT_RETEST'?'完成学习进度后，用新的情境验证':detail.feedback.next_action}</p><p className="hint">查看反馈已记录辅助条件；原提交的辅助快照不会被追改。</p></div>}
        {history.length>0 && <details style={{marginTop:16}}><summary>提交历史（{history.length} 次）</summary>{history.map(s=><div key={s.id} style={{margin:'8px 0'}}>第 {s.revision} 版 · {LEARNING_RUN_LABELS[s.status] || s.status}{s.has_feedback && <button className="btn btn-ghost btn-sm" disabled={busy} onClick={()=>viewFeedback(s)}>查看这次反馈</button>}</div>)}</details>}
      </div>}
      <LearningPlanControls key={`plan-${goal.id}`} goalId={goal.id} revision={goal.plan_revision} status={goal.status} activityId={activity?.id} activityStatus={activity?.status} onChanged={()=>setRefresh(x=>x+1)}/><ProjectActivityPicker key={`project-${goal.id}`} goalId={goal.id} onCreated={id=>{setSelected(id);setRefresh(x=>x+1);}}/>
      <div className="card" style={{marginTop:16}}><h3>下一步验证</h3>{path.next_action && <p>{path.next_action.message}</p>}<p className="hint">记录学习完成只保存进度。新题要重新作答和评分；即时结果不代表延迟保持。</p>{goal.allowed_actions.some(action=>action==='complete' || action==='complete_reading') && <button className="btn btn-outline" disabled={busy || running} onClick={complete}>记录学习完成</button>}{goal.allowed_actions.includes('retest') && <button className="btn btn-primary" disabled={busy || running} onClick={retest}>验证一下</button>}{!goal.allowed_actions.includes('retest') && <p>完成当前学习进度后，再按可用验证安排开始新题。</p>}</div>
      {(path.source_memories?.length || path.plan_history?.length || path.evolution_candidates?.length)?<details className="card" style={{marginTop:16}}><summary>本路径引用的记忆、计划历史与策略候选</summary>{path.source_memories?.map(memory=><p key={`source-${memory.id}`}>引用记忆 #{memory.id} · 版本 {memory.revision}：{memory.content}</p>)}{path.plan_history?.map((entry,i)=><div key={`plan-${i}`}><b>计划版本 {entry.revision}</b><p>{entry.reason}</p>{entry.changes.map((change,n)=><p key={n}>{change.activity_id?`活动 #${change.activity_id}：`:'路径：'}{change.from} → {change.to}</p>)}<p className="hint">只调整学习安排，不更新能力判断。</p></div>)}{path.evolution_candidates?.map(candidate=><div key={`strategy-${candidate.id}`}><b>{candidate.title}</b><p>{candidate.reason}</p><p className="hint">候选状态：{candidate.status}。仅供后续评估，没有自动激活新策略。</p></div>)}</details>:null}
      <LearningMemoryReview key={`memory-${goal.id}`} goalId={goal.id} items={path.memory_candidates || []} onReviewed={()=>setRefresh(x=>x+1)}/>
    </>}
  </section>;
}
