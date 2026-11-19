import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import StudyShell, { draftKey, stored, remember, submitKey } from '../components/StudyShell';
import StudyText from '../components/StudyText';
import StudyPracticePanel, { type PracticeRequest } from '../components/StudyPracticePanel';
import { useSpeechRecognition } from '../lib/speech';
import './studyChat.css';

interface Source {kind:string;id:number;label:string;text:string;revision?:string;path?:string;line_start?:number;line_end?:number;symbol?:string;provenance?:string}
interface Message {id:number;role:string;content:string;status:string;error?:string;sources:Source[];action?:string;model?:string;request_id?:string;revision_of?:number;parent_message_id?:number;blocks?:any[];trace?:Record<string,unknown>}
interface Session {mode?:string;project_id?:number;id:number;title:string;status:string;context:{topic:string;source_type:string;sources:Source[]};messages:Message[]}
interface Job {id:number;status:string;note_id:number;version_id?:number;message_ids:number[];conflict:boolean;error?:string}
const actions=[['simplify','讲得直白一点'],['example','换个例子'],['project','结合我的项目'],['mechanism','讲实现机制'],['why','讲设计缘由'],['diagram','画图讲解'],['demo','交互演示'],['interview','面试官追问'],['critique','点评我的表达'],['jd','对照岗位 JD'],['improve','项目补强建议']] as const;
function RunDetails({trace}:{trace:Record<string,unknown>}){
  const calls=Array.isArray(trace.calls)?trace.calls:[];
  return <details><summary>模型与运行记录{trace.tier?' · '+String(trace.tier):''}</summary><p>{String(trace.reason||'未记录路由原因')}</p>{!!trace.error_kind&&<p>异常：{String(trace.error_kind)}</p>}{calls.map((call,i)=><dl className="coach-trace" key={i}><dt>调用 {i+1}{call.escalation ? " · 已升级" : ""}</dt><dd>提供商：{call.provider||'未知'} · 请求：{call.requested_model||'未知'} · 实际报告：{call.reported_model||'未验证'}</dd><dd>耗时：{typeof call.elapsed_ms==='number'?call.elapsed_ms+' ms':'未知'} · 用量：{call.usage?JSON.stringify(call.usage):'未知'} · 费用：{call.cost!=null?JSON.stringify(call.cost):'未知'}</dd>{call.error&&<dd>错误：{String(call.error)}</dd>}</dl>)}</details>;
}
function SourceMeta({source:s}:{source:Source}){if(!s.revision&&!s.path&&!s.provenance)return null;return <div className="coach-source-meta"><code>{s.path||s.label}{s.line_start?':'+s.line_start+(s.line_end?'–'+s.line_end:''):''} {s.symbol}</code><small>版本：{s.revision||'未验证'} · {s.provenance||'来源材料'}</small></div>}
const stages:Record<string,string>={starting:'等待生成',retrieving:'正在检索项目代码',routing:'正在选择模型',escalating:'正在升级模型重新生成',generating:'正在生成回复',completed:'已保存',failed:'生成失败'};
function Sources({items}:{items:Source[]}) {
  if(!items?.length)return null;
  return <details><summary>本次读取的材料（{items.length}）</summary>{items.map((s,i)=><div key={i}><strong>{s.label} #{s.id}</strong><SourceMeta source={s}/><p className="study-content">{s.text}</p></div>)}</details>;
}
function SessionView({id}:{id:number}) {
  const path='/study/sessions/'+id;
  const [params]=useSearchParams();
  const cardId=Number(params.get('card'))||undefined;
  const cardQuiz=!!cardId&&params.get('practice')==='quiz';
  const [data,setData]=useState<Session|null>(null);
  const [jobs,setJobs]=useState<Job[]>([]);
  const [projects,setProjects]=useState<{id:number;name:string;title?:string}[]>([]);
  const [project,setProject]=useState('');
  const location=useLocation();
  const [content,setContent]=useState<string>(stored(draftKey(id)) || location.state?.unsent || '');
  const [anchor,setAnchor]=useState<number>();
  const [revision,setRevision]=useState<number>();
  const [practiceRequest,setPracticeRequest]=useState<PracticeRequest>();
  const [toolsVisible,setToolsVisible]=useState(false);
  const toolsPanel=useRef<HTMLDivElement>(null);
  const [more,setMore]=useState(false);
  const [copied,setCopied]=useState(0);
  const [away,setAway]=useState(false);
  const scroll=useRef<HTMLDivElement>(null), follow=useRef(!params.has('message')), composer=useRef<HTMLTextAreaElement>(null), locked=useRef(false);
  useEffect(()=>{remember(draftKey(id),content)},[content,id]);
  useEffect(()=>{const box=scroll.current;if(!box||!data)return;const resize=new ResizeObserver(()=>{if(follow.current)box.scrollTop=box.scrollHeight});if(box.firstElementChild)resize.observe(box.firstElementChild);if(follow.current)box.scrollTop=box.scrollHeight;return()=>resize.disconnect()},[!!data]);
  useEffect(()=>{if(data)setProject(String(data.project_id||''))},[data?.project_id]);
  const [title,setTitle]=useState('');
  const [editing,setEditing]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState<string>(location.state?.error || '');
  const [loadError,setLoadError]=useState('');
  const [cardJob,setCardJob]=useState(()=>Number(stored(draftKey(id)+':cards'))||0);
  const [cardState,setCardState]=useState('');
  const [cardRetry,setCardRetry]=useState(0);
  const [selectNote,setSelectNote]=useState('');
  const [notes,setNotes]=useState<{id:number;title:string}[]>([]);
  const [choosingNote,setChoosingNote]=useState(false);
  const keys=useRef(new Map<string,string>());
  const version=useRef(0);
  const speech=useSpeechRecognition(delta=>setContent(prev=>prev+delta));
  async function load(){
    const current=++version.current;
    const [s,j]=await Promise.all([api.get<Session>(path),api.get<{items:Job[]}>(path+'/note-jobs')]);
    if(current===version.current){setData(s);setJobs(j.items);setLoadError('');try{const pending=JSON.parse(stored(draftKey(id)+':pending')||'null');const accepted=pending&&s.messages.find(m=>m.role==='user'&&m.request_id===pending.ticket);if(accepted){remember(draftKey(id)+':pending','');setContent(prev=>prev.trim()===accepted.content?'':prev)}}catch{/* Storage may be unavailable. */}}
  }
  useEffect(()=>{
    load().catch(e=>setLoadError(e.message));
    return ()=>{++version.current};
  },[path]);
  const generating=data?.messages.some(m=>m.status==='generating');
  const job=jobs[0];
  const ongoing=job && ['QUEUED','RUNNING'].includes(job.status);
  useEffect(()=>{
    if(!generating&&!ongoing)return;
    let live=true;
    let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{await load()}catch(e){if(live)setLoadError((e as Error).message)}if(live)timer=setTimeout(poll,1000)};
    timer=setTimeout(poll,650);
    return ()=>{live=false;clearTimeout(timer)};
  },[generating,ongoing,path]);
  useEffect(()=>{const sync=()=>load().catch(e=>setLoadError(e.message));window.addEventListener('online',sync);window.addEventListener('study-history-changed',sync);return()=>{window.removeEventListener('online',sync);window.removeEventListener('study-history-changed',sync)}},[id]);
  const target=params.get('message');
  const ready=!!data;
  useEffect(()=>{
    const panel=toolsPanel.current;
    if(!ready || !panel || cardQuiz)return;
    const sync=()=>{
      const visible=panel.matches(':popover-open');
      setToolsVisible(visible);
      if(!visible)setPracticeRequest(undefined);
    };
    panel.addEventListener('toggle',sync);
    if(params.get('practice'))panel.showPopover();
    return ()=>panel.removeEventListener('toggle',sync);
  },[ready,id,cardQuiz,params]);
  function requestPractice(kind: 'explain' | 'quiz', messageId = anchor) {
    setPracticeRequest({ kind, messageId, key: crypto.randomUUID() });
    toolsPanel.current?.showPopover();
  }
  useEffect(()=>{if(target && ready)document.getElementById('study-message-'+target)?.scrollIntoView({block:'center'})},[target,ready]);
  useEffect(()=>{api.get('/projects').then(d=>setProjects(d.items||[])).catch(()=>{})},[]);
  async function perform(label:string,fn:(key:string)=>Promise<void>){
    if(locked.current)return;locked.current=true;setBusy(true);setError('');
    const key=keys.current.get(label)||crypto.randomUUID();keys.current.set(label,key);
    try{await fn(key);keys.current.delete(label);await load()}catch(e){setError((e as Error).message)}finally{locked.current=false;setBusy(false)}
  }
  function send(action:string,question:string,parent=anchor,rev=revision){
    const q=question.trim(); if(!q||generating||busy)return;
    if(action==='explain' && !/(不要|不用|别|不想).{0,5}(测|考|练|复述|提问)/.test(q)){
      const mode=/(让我|我想|请让我).{0,5}(试着解释|复述|用自己的话解释)/.test(q)?'study-restate':
        /^(请|现在|接下来|可以|先|帮我|来|想)?[，, ]*(测一下|测测我|考考我|考我一道|出一道.{0,5}题|给我一道.{0,5}题)/.test(q)?'study-quiz':null;
      if(mode){
        requestPractice(mode==='study-restate'?'explain':'quiz');
        setContent('');
        return;
      }
    }
    const signature=JSON.stringify([action,q,project,data?.mode||'auto',parent,rev]);
    perform('send-'+signature,async key=>{
      const pendingKey=draftKey(id)+':pending';let ticket=key;
      try {const pending=JSON.parse(stored(pendingKey)||'null');if(pending?.signature===signature)ticket=pending.ticket}catch{/* A stale local draft is optional. */}
      remember(pendingKey,JSON.stringify({signature,ticket}));
      await api.post(path+'/messages',{content:q,action,request_id:ticket,mode:data?.mode||'auto',project_id:project?Number(project):undefined,parent_message_id:parent,revision_of:rev});
      remember(pendingKey,'');if(content.trim()===q)setContent(prev=>prev.trim()===q?'':prev);setAnchor(undefined);setRevision(undefined);follow.current=true;setAway(false);window.dispatchEvent(new Event('study-history-changed'));
    });
  }
  const cardTarget=jobs.find(j=>j.id===cardJob);
  useEffect(()=>{if(!cardJob||!cardTarget||cardTarget.status!=='SUCCEEDED'||!cardTarget.version_id)return;let live=true;
    if(cardTarget.conflict){setCardState('手工编辑与更新草稿冲突，请打开笔记选择版本再生成卡片。');return}
    setCardState('正在从本次笔记版本提炼候选卡…');api.post('/study/notes/'+cardTarget.note_id+'/cards',{version_id:cardTarget.version_id}).then(result=>{if(live){setCardState(`已从版本 #${cardTarget.version_id} 生成 ${result.items.length} 张候选卡`);remember(draftKey(id)+':cards','');setCardJob(0)}}).catch(e=>{if(live)setCardState('卡片生成失败：'+e.message)});return()=>{live=false}
  },[cardTarget?.id,cardTarget?.status,cardTarget?.version_id,cardRetry,cardJob,id]);
  function organize(end:boolean,cards=false,focus=anchor){
    perform('note-'+(data?.messages.length||0)+'-'+selectNote+'-'+end+'-'+(focus||'')+'-'+cards,async key=>{
      const queued=await api.post<Job>(path+'/note-jobs',{request_id:key,focus_message_id:focus,note_id:selectNote?Number(selectNote):undefined});
      if(cards){remember(draftKey(id)+':cards',String(queued.id));setCardJob(queued.id);setCardState('正在整理本次笔记，完成后生成候选卡…')}
      if(end)await api.patch(path,{status:'ended'});
    });
  }
  if(!data)return <div className="card">{loadError||'正在打开学习会话…'} {loadError&&<button onClick={()=>load().catch(e=>setLoadError(e.message))}>重试</button>}</div>;
  const ended=data.status==='ended';
  if(cardQuiz)return <div className="study-page coach-card-review">
    <header><Link to={'/study/chat/'+id}>← 返回学习会话</Link><h1>复习卡练习</h1><p>{data.title}</p></header>
    <StudyPracticePanel sessionId={id} ended={ended} cardId={cardId}/>
  </div>;
  const savedReply=[...data.messages].reverse().find(m=>m.role==='assistant'&&m.status==='completed');
  const canOrganize=data.messages.some(m=>m.status==='completed')&&!busy&&!ongoing&&!generating;
  const successfulJob=jobs.find(item=>item.status==='SUCCEEDED');
  const linkedProject=projects.find(item=>item.id===data.project_id);
  const modelLabel=({auto:'Auto · 自动',flash:'Flash · 快速',strong:'Strong · 深度'} as Record<string,string>)[data.mode||'auto']||data.mode;
  const comparePath=(savedNoteId:number,messageId:number)=>`/study/notes/${savedNoteId}?view=compare&session=${id}&message=${messageId}`;
  const openTools=()=>{setPracticeRequest(undefined);document.getElementById('coach-tools-'+id)?.showPopover?.()};
  const changeSetting=(body:Record<string,unknown>)=>perform('settings',async()=>{await api.patch(path,body)});
  const setContext=(m:Message,edit=false)=>{setAnchor(edit?m.parent_message_id:m.id);setRevision(edit?m.id:undefined);if(edit)setContent(m.content);composer.current?.focus()};
  return <div className="study-page coach-session">
    <header className="coach-session-head"><div className="coach-head-inner">
      <div className="coach-title"><small>学习会话{ended?' · 已结束':''}</small><h1>{data.title}</h1><div className="coach-session-meta"><span>模型：{modelLabel}</span><span>项目：{linkedProject?.name||linkedProject?.title||(data.project_id?`#${data.project_id}`:'未关联')}</span></div></div>
      <div className="coach-header-actions">
        <button className="btn btn-ghost" onClick={openTools}>笔记与练习</button>
        <details className="coach-settings"><summary>会话设置</summary><div className="coach-settings-panel">
          {editing?<form className="study-actions" onSubmit={e=>{e.preventDefault();perform('rename',async()=>{await api.patch(path,{title:title.trim()});setEditing(false);window.dispatchEvent(new Event('study-history-changed'))})}}><input aria-label="会话标题" value={title} maxLength={160} onChange={e=>setTitle(e.target.value)}/><button className="btn" disabled={!title.trim()||busy}>保存标题</button><button type="button" className="btn btn-ghost" onClick={()=>setEditing(false)}>取消</button></form>:<button className="btn btn-ghost" onClick={()=>{setTitle(data.title);setEditing(true)}}>编辑标题</button>}
          <label>模型 <select aria-label="模型模式" disabled={busy||generating} value={data.mode||'auto'} onChange={e=>changeSetting({mode:e.target.value})}><option value="auto">Auto · 自动</option><option value="flash">Flash · 快速</option><option value="strong">Strong · 深度</option></select></label>
          <label>关联项目 <select aria-label="关联项目" disabled={busy||generating} value={project} onChange={e=>changeSetting({project_id:e.target.value?Number(e.target.value):null})}><option value="">未关联</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name||p.title}</option>)}</select></label>
        </div></details>
      </div>
    </div></header>
    <div ref={scroll} className="coach-scroll" onScroll={e=>{const el=e.currentTarget;follow.current=el.scrollHeight-el.scrollTop-el.clientHeight<60;setAway(!follow.current)}}><div className="coach-inner">
    <Sources items={data.context.sources}/>{!data.messages.length&&<p className="coach-empty">输入一个问题，开始这次学习。</p>}
    {loadError && <p role="alert">同步失败：{loadError}</p>}
    <section className="study-thread" aria-label="聊天记录">{data.messages.map(m=><article className={'study-turn '+m.role+(target===String(m.id)?' is-focused':'')} key={m.id} id={'study-message-'+m.id} tabIndex={-1}>
      <h4>{m.role==='user'?'我':'学习助手'} <small>#{m.id} · {({completed:'已保存',generating:'生成中',failed:'失败',interrupted:'已中断'} as Record<string,string>)[m.status]||m.status}{m.trace?.tier?' · '+String(m.trace.tier):''}{m.status==='generating'&&m.trace?.stage?' · '+(stages[String(m.trace.stage)]||String(m.trace.stage)):''}</small></h4>
      {m.role==='assistant'?<StudyText {...{text:m.content,blocks:m.blocks,sources:m.sources,streaming:m.status==='generating'}}/>:<div className="study-content">{m.content}</div>}{m.role==='assistant'&&<Sources items={m.sources}/>}
      {m.error&&<p role="alert">{m.error}</p>}{m.status==='generating'&&<button className="btn btn-outline" disabled={busy} onClick={()=>perform('stop',async()=>{await api.post(path+'/messages/'+m.id+'/interrupt')})}>中断回复</button>}
      {['interrupted','failed'].includes(m.status)&&!ended&&<button className="btn btn-outline" disabled={busy||generating} onClick={()=>perform('retry',async()=>{await api.post(path+'/messages/'+m.id+'/retry')})}>重试回复</button>}
      {m.revision_of&&<small>本版源自消息 #{m.revision_of} · 原文保留</small>}
      {m.role==='assistant'&&m.status==='completed'&&<div className={'coach-note-bridge'+(m.id===savedReply?.id?' is-latest':'')}>
        <div className="study-actions"><button className={'btn '+(m.id===savedReply?.id?'btn-primary':'btn-outline')} disabled={!canOrganize} onClick={()=>organize(false,false,m.id)}>整理这条回复</button>
        {!ended&&<button className="btn btn-outline" disabled={busy||generating} onClick={()=>requestPractice('explain',m.id)}>试着解释</button>}</div>
        {successfulJob?.message_ids.includes(m.id)&&<div className="study-actions coach-note-result"><small>{successfulJob.conflict?'更新草稿包含这条回复':'笔记已收录这条回复'}</small><Link to={'/study/notes/'+successfulJob.note_id} state={{sourceSession:id,sourceMessage:m.id}}>打开笔记</Link>{!successfulJob.conflict&&<Link to={comparePath(successfulJob.note_id,m.id)}>对照原聊</Link>}</div>}
      </div>}
      {m.trace&&Object.keys(m.trace).length>0&&<RunDetails trace={m.trace}/>}
      <div className="coach-turn-actions"><button onClick={async()=>{try{await navigator.clipboard.writeText(m.content);setCopied(m.id)}catch{setError('复制失败，可以手动选择消息文字。')}}}>{copied===m.id?'已复制':'复制'}</button>{!ended&&m.status==='completed'&&<><button onClick={()=>setContext(m)}>围绕这条追问</button>{m.role==='user'?<button onClick={()=>setContext(m,true)}>编辑并重新发送</button>:<button disabled={busy||generating} onClick={()=>{const original=data.messages.find(x=>x.role==='user'&&x.request_id===m.request_id);if(original)send(original.action||'explain',original.content,original.id,m.id)}}>重新生成</button>}</>}</div>
    </article>)}</section>
    </div></div>
    <footer className="coach-compose">
    {away&&<button className="coach-latest" onClick={()=>{follow.current=true;if(scroll.current)scroll.current.scrollTop=scroll.current.scrollHeight;setAway(false)}}>↓ 回到最新消息</button>}
    {error&&<small role="alert">{error}</small>}{!ended?<><div className="coach-chips">{actions.filter(([a])=>['simplify','mechanism','project','diagram'].includes(a)).map(([a,label])=><button disabled={busy||generating} key={a} onClick={()=>send(a,label)}>{label}</button>)}<button aria-expanded={more} onClick={()=>setMore(!more)}>更多</button></div>
    {more&&<div className="coach-chips"><button disabled={busy||ongoing||!data.messages.length||!!cardJob} onClick={()=>organize(false,true)}>整理复习卡</button><button disabled={busy||generating} onClick={()=>requestPractice('quiz')}>测一下</button>{actions.filter(([a])=>!['simplify','mechanism','project','diagram'].includes(a)).map(([a,label])=><button disabled={busy||generating} key={a} onClick={()=>send(a,label)}>{label}</button>)}</div>}
    {(anchor||revision)&&<small>围绕消息 #{revision||anchor} {revision?'编辑副本，保留原文':'追问'} <button onClick={()=>{setAnchor(undefined);setRevision(undefined)}}>取消关联</button></small>}
    {(cardJob||cardState)&&<small role="status">{cardTarget?.status==='FAILED'?'本次笔记失败，请在笔记与练习中重试本次整理。':cardState}{cardState.startsWith('卡片生成失败')&&<button onClick={()=>setCardRetry(r=>r+1)}>重试卡片</button>}{!!cardJob&&<button onClick={()=>{remember(draftKey(id)+':cards','');setCardJob(0);setCardState('')}}>取消后续生成</button>}</small>}
    <div className="coach-input"><textarea ref={composer} aria-label="继续问" rows={2} maxLength={8000} value={content} onKeyDown={e=>submitKey(e,()=>send('explain',content))} onChange={e=>setContent(e.target.value)} placeholder="继续问，或用自己的话解释……"/><div className="study-actions">{speech.supported&&<button className="btn btn-ghost" onClick={speech.listening?speech.stop:speech.start}>{speech.listening?'停止听写':'语音输入'}</button>}{generating?<button className="btn btn-outline" disabled={busy} onClick={()=>perform('stop',async()=>{await api.post(path+'/messages/'+data.messages.find(m=>m.status==='generating')!.id+'/interrupt')})}>停止生成</button>:<button className="btn btn-primary" disabled={busy||!content.trim()} onClick={()=>send('explain',content)}>发送</button>}</div></div>{speech.interim||speech.error}</>:<div className="study-actions"><button className="btn btn-primary" onClick={()=>perform('resume',async()=>{await api.patch(path,{status:'active'})})}>继续学习</button><button className="btn" onClick={()=>document.getElementById('coach-tools-'+id)?.showPopover?.()}>笔记与练习</button></div>}</footer>
    <div ref={toolsPanel} className="coach-tools" id={'coach-tools-'+id} {...{popover:"auto"}}><button className="btn btn-ghost" onClick={()=>document.getElementById('coach-tools-'+id)?.hidePopover?.()}>关闭</button>    <section className="card"><div className="study-actions"><button className="btn btn-primary" disabled={busy||ongoing||!data.messages.length} onClick={()=>organize(false)}>整理成笔记</button>
      {!ended&&<button className="btn btn-outline" disabled={busy||ongoing||generating||!data.messages.length} onClick={()=>organize(true)}>结束并整理</button>}
      <button className="btn btn-ghost" onClick={()=>{setChoosingNote(!choosingNote);if(!choosingNote)api.get('/study/notes').then(d=>setNotes(d.items)).catch(e=>setError(e.message))}}>关联已有笔记</button>
      {choosingNote&&<select aria-label="关联已有笔记" value={selectNote} onChange={e=>setSelectNote(e.target.value)}><option value="">本会话的笔记</option>{notes.map(n=><option key={n.id} value={n.id}>{n.title}</option>)}</select>}</div>
      {job&&<p role="status">{ongoing?'笔记整理中':job.status==='FAILED'?'整理失败，可重试':job.conflict?'已生成更新草稿，保留了手工编辑':'笔记已生成'}  {job.version_id?` · 笔记版本 #${job.version_id}`:""} · 冻结 {job.message_ids.length} 条消息
        {job.status==='SUCCEEDED'&&<><Link to={'/study/notes/'+job.note_id}> 打开笔记</Link>{!job.conflict&&savedReply&&job.message_ids.includes(savedReply.id)&&<Link to={comparePath(job.note_id,savedReply.id)}> 对照原聊</Link>}</>}
        {job.status==='FAILED'&&<><span> {job.error}</span><button className="btn btn-outline" onClick={()=>perform('retry-note',async()=>{await api.post('/study/note-jobs/'+job.id+'/retry')})}>重试整理</button></>}</p>}</section>
    {toolsVisible && <StudyPracticePanel initialRequest={practiceRequest} focusMessageId={practiceRequest?.messageId??anchor} sessionId={id} ended={ended} cardId={practiceRequest?undefined:cardId}/>}
</div></div>
}
export default function StudyChat(){
  const {sessionId}=useParams(); const navigate=useNavigate();
  const [params]=useSearchParams();
  const cardQuiz=!!Number(params.get('card'))&&params.get('practice')==='quiz';
  const [topic,setTopic]=useState(()=>stored(draftKey('new')));const [err,setErr]=useState('');const [busy,setBusy]=useState(false);const creating=useRef(false);
  const [latestChat,setLatestChat]=useState<{id:number;title:string}>();
  const speech=useSpeechRecognition(s=>setTopic(t=>t+s));
  useEffect(()=>{remember(draftKey('new'),topic)},[topic]);
  useEffect(()=>{
    if(sessionId)return;
    let request=0;
    const loadLatest=()=>{
      const current=++request;
      setLatestChat(undefined);
      api.get<{items:{id:number;title:string}[]}>('/study/sessions?limit=1')
        .then(({items})=>{if(request===current)setLatestChat(items[0])})
        .catch(()=>{});
    };
    loadLatest();
    window.addEventListener('study-history-changed',loadLatest);
    return ()=>{request++;window.removeEventListener('study-history-changed',loadLatest)};
  },[sessionId]);
  if(sessionId)return <StudyShell id={sessionId} section={cardQuiz?'review':undefined}><SessionView key={sessionId} id={Number(sessionId)}/></StudyShell>;
  async function start(){
    const q=topic.trim();if(!q||creating.current)return;creating.current=true;setBusy(true);setErr('');
    try{const s=await api.post('/study/sessions',{source_type:'free',topic:q});
      const path='/study/chat/'+s.id;
      const ticket=crypto.randomUUID();remember(draftKey(s.id)+':pending',JSON.stringify({signature:JSON.stringify(['explain',q,'','auto',null,null]),ticket}));
      try{await api.post('/study/sessions/'+s.id+'/messages',{request_id:ticket,content:q,action:'explain',mode:'auto'});remember(draftKey(s.id)+':pending','');setTopic('');navigate(path)}
      catch(ex){navigate(path,{state:{unsent:q,error:'发送状态尚未确认，请核对聊天记录。'+(ex as Error).message}})}
    }catch(e){setErr((e as Error).message)}finally{creating.current=false;setBusy(false)}
  }
  return <StudyShell><div className="study-page coach-welcome"><small>面试学习教练</small><h1>今天想弄懂什么？</h1><p>从一个概念、一段代码，或一道面试题开始。可以一路追问，也可以随时整理笔记。</p>
    <form className="study-compose coach-welcome-compose" onSubmit={e=>{e.preventDefault();start()}}><textarea aria-label="学习问题" onKeyDown={e=>submitKey(e,start)} rows={3} maxLength={8000} value={topic} onChange={e=>setTopic(e.target.value)} placeholder="例如：Agent 重试工具调用时，怎样避免重复写入？"/>
      <div className="coach-welcome-actions"><div>{speech.supported&&<button type="button" className="btn btn-ghost" onClick={speech.listening?speech.stop:speech.start}>{speech.listening?'停止听写':'语音输入'}</button>}</div><button className="btn btn-primary" disabled={!topic.trim()||busy}>{busy?'打开中…':'开始学习'}</button></div>
      {(speech.error||speech.interim)&&<small role={speech.error?'alert':'status'}>{speech.error||speech.interim}</small>}
    </form>
    {latestChat&&<Link className="coach-resume-row" to={'/study/chat/'+latestChat.id}><span><small>最近会话</small><strong>{latestChat.title}</strong></span><span>继续学习 <span aria-hidden="true">→</span></span></Link>}
    {err&&<p role="alert">{err}</p>}</div></StudyShell>
}
