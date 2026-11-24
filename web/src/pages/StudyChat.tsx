import { useT } from '../i18n';
import { memo, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, readMessageStream, type MessageStreamEvent } from '../api/client';
import StudyShell, { draftKey, stored, remember } from '../components/StudyShell';
import { StudyComposerInput, StudyTurn, submitKey } from '../components/StudyConversation';
import StudyText from '../components/StudyText';
import StudyPracticePanel, { type PracticeRequest } from '../components/StudyPracticePanel';
import StudyInterviewQuestion from '../components/StudyInterviewQuestion';
import { useSpeechRecognition } from '../lib/speech';
import './studyChat.css';

type StudyMessage = { key: string; values?: Record<string, string | number> };

interface Source {kind:string;id:number;label:string;text:string;revision?:string;path?:string;line_start?:number;line_end?:number;symbol?:string;provenance?:string}
interface Message {id:number;role:string;content:string;status:string;attempt?:number;error?:string;sources:Source[];action?:string;model?:string;request_id?:string;revision_of?:number;parent_message_id?:number;blocks?:any[];trace?:Record<string,unknown>}
interface Session {mode?:string;project_id?:number;id:number;title:string;status:string;context:{topic:string;source_type:string;sources:Source[]};messages:Message[]}
interface Job {id:number;status:string;note_id:number;version_id?:number;message_ids:number[];conflict:boolean;error?:string}
interface TurnTicket {user_message_id:number;assistant_message_id:number;status:string;attempt?:number}
const actions = [
  { id: "simplify", label: "study.explainSimply", prompt: "讲得直白一点" },
  { id: "example", label: "study.giveAnotherExample", prompt: "换个例子" },
  { id: "project", label: "study.relateToProject", prompt: "结合我的项目" },
  { id: "mechanism", label: "study.explainMechanism", prompt: "讲实现机制" },
  { id: "why", label: "study.explainDesignRationale", prompt: "讲设计缘由" },
  { id: "diagram", label: "study.drawDiagram", prompt: "画图讲解" },
  { id: "demo", label: "study.interactiveDemo", prompt: "交互演示" },
  { id: "interview", label: "study.askInterviewFollowUp", prompt: "面试官追问" },
  { id: "critique", label: "study.critiqueMyAnswer", prompt: "点评我的表达" },
  { id: "jd", label: "study.compareJobDescription", prompt: "对照岗位 JD" },
  { id: "improve", label: "study.suggestProjectImprovements", prompt: "项目补强建议" },
] as const;
function RunDetails({trace}:{trace:Record<string,unknown>}){
  const t = useT();

  const calls=Array.isArray(trace.calls)?trace.calls:[];
  return <details>
    <summary>{t("study.modelRunDetails", { tier: trace.tier ? ' · '+String(trace.tier) : '' })}</summary>
    <p>{String(trace.reason || t("study.noRoutingReason"))}</p>
    {!!trace.policy_version && <p>{t("study.routingPolicy", { policy: String(trace.policy_version) })}{!!trace.preferred_tier && <>{t("study.preferredTier", { tier: String(trace.preferred_tier) })}</>}{!!trace.initial_tier && <>{t("study.initialTier", { tier: String(trace.initial_tier) })}</>}
    </p>}
    {!!trace.error_kind && <p>{t("study.traceErrorKind", { kind: String(trace.error_kind) })}</p>}
    {calls.map((call,i) => <dl className="coach-trace" key={i}>
      <dt>{t("study.traceCall", { callNumber: i+1, tier: call.tier ? ' · '+call.tier : '', escalation: call.escalation ? t("study.traceEscalated") : '' })}</dt>
      <dd>{t("study.traceProviderModels", { provider: call.provider || t("study.unknown"), requestedModel: call.requested_model || t("study.unknown"), reportedModel: call.reported_model || t("study.unverified") })}</dd>
      {typeof call.first_reasoning_ms === 'number' && <dd>{t("study.firstReasoningLatency", { milliseconds: Math.round(call.first_reasoning_ms) })}</dd>}
      {typeof call.first_nonwhitespace_content_ms === 'number' && <dd>{t("study.firstNonEmptyContentLatency", { milliseconds: Math.round(call.first_nonwhitespace_content_ms) })}</dd>}
      {typeof call.first_visible_body_ms === 'number' && <dd>{t("study.firstVisibleBodyLatency", { milliseconds: Math.round(call.first_visible_body_ms) })}</dd>}
      <dd>{t("study.traceUsageAndCost", { duration: typeof call.elapsed_ms === 'number' ? call.elapsed_ms+' ms' : t("study.unknown"), usage: call.usage ? JSON.stringify(call.usage) : t("study.unknown"), cost: call.cost != null ? JSON.stringify(call.cost) : t("study.unknown") })}</dd>
      {call.error && <dd>{t("study.traceError", { reason: String(call.error) })}</dd>}
    </dl>)}
  </details>;
}
const MemoRunDetails=memo(RunDetails);
function SourceMeta({source:s}:{source:Source}){
  const t = useT();
  if(!s.revision&&!s.path&&!s.provenance)return null;return <div className="coach-source-meta"><code>{s.path||s.label}{s.line_start?':'+s.line_start+(s.line_end?'–'+s.line_end:''):''} {s.symbol}</code><small>{t("study.traceSourceMeta", { revision: s.revision||t("study.unverified"), source: s.provenance||t("study.sourceMaterial") })}</small></div>}
const stages:Record<string,string>={queued:"study.queuedForGeneration",starting:"study.queuedForGeneration",retrieving:"study.retrievingProjectCode",routing:"study.routingModel",escalating:"study.escalatingModel",repairing:"study.repairingDiagram",generating:"study.generatingReply",completed:"study.saved",failed:"study.generationFailed",interrupted:"study.interrupted"};
function ThinkingDots(){return <span className="coach-dots" aria-hidden="true"><i/><i/><i/></span>}
// 实时思考过程: 自动滚动到底, 显示字数与已用时长; 正文开始后自动折叠, 用户可手动展开/收起。
function ThinkingPanel({text,startedAt,open}:{text:string;startedAt:number;open:boolean}){
  const t = useT();

  const box=useRef<HTMLPreElement>(null);
  const [override,setOverride]=useState<boolean|null>(null);
  const [now,setNow]=useState(()=>Date.now());
  const expanded=override??open;
  useEffect(()=>{const el=box.current;if(el)el.scrollTop=el.scrollHeight},[text]);
  useEffect(()=>{if(!expanded)return;const t=setInterval(()=>setNow(Date.now()),1000);return ()=>clearInterval(t)},[expanded]);
  const seconds=Math.max(0,Math.round((now-startedAt)/1000));
  return <details className="coach-reasoning" open={expanded} onClick={e=>{if((e.target as HTMLElement).tagName==='SUMMARY')setOverride(o=>o===null?!expanded:!o)}}><summary>{open?<><ThinkingDots/>{t("study.thinkingNow")}</>:<>{t("study.reasoning")}</>}{t("study.thinkingStats", { characters: text.length, seconds })}</summary><pre ref={box}>{text}</pre></details>;
}
function Sources({items}:{items:Source[]}) {
  const t = useT();

  if(!items?.length)return null;
  return <details><summary>{t("study.materialsReadCount", { count: items.length })}</summary>{items.map((s,i)=><div key={i}><strong>{s.label} #{s.id}</strong><SourceMeta source={s}/><p className="study-content">{s.text}</p></div>)}</details>;
}
const MemoSources=memo(Sources);
function SessionView({id}:{id:number}) {
  const t = useT();

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
  const [error,setError]=useState<string | StudyMessage>(location.state?.error || '');
  const [loadError,setLoadError]=useState('');
  const [live,setLive]=useState<MessageStreamEvent|null>(null);
  const [pollReply,setPollReply]=useState(false);
  const [cardJob,setCardJob]=useState(()=>Number(stored(draftKey(id)+':cards'))||0);
  const [cardState,setCardState]=useState<StudyMessage | null>(null);
  const [cardRetry,setCardRetry]=useState(0);
  const [selectNote,setSelectNote]=useState('');
  const [notes,setNotes]=useState<{id:number;title:string}[]>([]);
  const [choosingNote,setChoosingNote]=useState(false);
  const keys=useRef(new Map<string,string>());
  const version=useRef(0);
  const noteScope=useRef<{request?:Promise<void>}|null>(null);
  const liveLatest=useRef<MessageStreamEvent|null>(null);
  const liveFrame=useRef<number|null>(null);
  const liveStartedAt=useRef(0);
  const speech=useSpeechRecognition(delta=>setContent(prev=>prev+delta));
  async function refreshJobs(afterWrite=false){
    const scope=noteScope.current;
    if(!scope)return;
    if(afterWrite)await scope.request;
    if(noteScope.current!==scope||scope.request)return;
    const request=api.get<{items:Job[]}>(path+'/note-jobs').then(
      result=>{if(noteScope.current===scope)setJobs(result.items)},
      error=>{if(noteScope.current===scope)setLoadError(error.message)},
    );
    scope.request=request;
    try{await request}finally{scope.request=undefined}
  }
  async function load(afterNoteWrite=false){
    const current=++version.current;
    const sessionRequest=api.get<Session>(path);
    void refreshJobs(afterNoteWrite);
    const s=await sessionRequest;
    if(current===version.current){setData(s);setLoadError('');try{const pending=JSON.parse(stored(draftKey(id)+':pending')||'null');const accepted=pending&&s.messages.find(m=>m.role==='user'&&m.request_id===pending.ticket);if(accepted){remember(draftKey(id)+':pending','');setContent(prev=>prev.trim()===accepted.content?'':prev)}}catch{/* Storage may be unavailable. */}}
  }
  useEffect(()=>{
    noteScope.current={};
    load().catch(e=>setLoadError(e.message));
    return ()=>{noteScope.current=null;++version.current};
  },[path]);
  const generating=data?.messages.some(m=>m.status==='generating');
  const job=jobs[0];
  const ongoing=job && ['QUEUED','RUNNING'].includes(job.status);
  const generatingMessage=data?.messages.filter(m=>m.status==='generating').pop();
  // 流式读取最新一条生成中的回复; 完成后重新载入持久化快照。
  // 用 requestAnimationFrame 批处理, 每帧至多一次 setState, 无固定延迟 → 吐字平滑。
  useEffect(()=>{
    if(!generatingMessage)return;
    setLive(null);
    setPollReply(false);
    if(liveFrame.current!==null){cancelAnimationFrame(liveFrame.current);liveFrame.current=null}
    liveLatest.current=null;
    liveStartedAt.current=Date.now();
    let live=true;
    const controller=new AbortController();
    const flush=()=>{
      liveFrame.current=null;
      const event=liveLatest.current;
      liveLatest.current=null;
      if(!live||!event)return;
      setLive(event);
      if(event.status!=='generating'){
        live=false;
        setPollReply(true);
      }
    };
    const schedule=()=>{if(liveFrame.current===null)liveFrame.current=requestAnimationFrame(flush)};
    const onEvent=(event:MessageStreamEvent)=>{
      if(!live||event.id!==generatingMessage.id||event.attempt<(generatingMessage.attempt??1))return;
      if(event.status!=='generating'){
        liveLatest.current=event;
        if(liveFrame.current!==null){cancelAnimationFrame(liveFrame.current);liveFrame.current=null}
        flush();
        return;
      }
      liveLatest.current=event;
      schedule();
    };
    // SSE 断线自动重连(指数退避), 仍失败才退回轮询。
    let attempt=0;
    const connect=async()=>{
      try{
        await readMessageStream(path+'/messages/'+generatingMessage.id+'/stream',controller.signal,onEvent);
      }catch(e){
        if(!live||controller.signal.aborted)return;
        if(attempt<2){
          attempt+=1;
          await new Promise(r=>setTimeout(r,400*2**attempt));
          if(live&&!controller.signal.aborted)return connect();
        }
        setPollReply(true);setLive(null);
      }
    };
    connect();
    return ()=>{live=false;controller.abort();if(liveFrame.current!==null){cancelAnimationFrame(liveFrame.current);liveFrame.current=null}};
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[path,generatingMessage?.id,generatingMessage?.attempt]);
  // 收到终态或重连耗尽后读取已存回复；读取失败时继续轮询。
  useEffect(()=>{
    if(!((generating&&pollReply)||ongoing))return;
    let active=true;
    let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{await load()}catch(e){if(active)setLoadError((e as Error).message)}if(active)timer=setTimeout(poll,1000)};
    timer=setTimeout(poll,generating&&pollReply?0:650);
    return ()=>{active=false;clearTimeout(timer)};
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[generating,ongoing,pollReply,path]);
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
  async function perform(label:string,fn:(key:string)=>Promise<void>,afterNoteWrite=false){
    if(locked.current)return;locked.current=true;setBusy(true);setError('');
    const key=keys.current.get(label)||crypto.randomUUID();keys.current.set(label,key);
    try{await fn(key);keys.current.delete(label);await load(afterNoteWrite)}catch(e){setError((e as Error).message)}finally{locked.current=false;setBusy(false)}
  }
  function streamFor(message:Message){
    return message.status==='generating'&&live?.id===message.id&&live.attempt>=(message.attempt??1)?live:null;
  }
  function stop(message:Message){
    const attempt=streamFor(message)?.attempt??message.attempt??1;
    return perform('stop',async()=>{
      await api.post(path+'/messages/'+message.id+'/interrupt?expected_attempt='+attempt);
    });
  }
  function send(action:string,question:string,parent=anchor,rev=revision){
    const q=question.trim(); if(!q||generating||busy)return;
    if(action==='explain' && !/(不要|不用|别|不想).{0,5}(测|考|练|复述|提问)/.test(q)){
      const mode=/(让我|我想|请让我).{0,5}(试着解释|复述|用自己的话解释)/.test(q)?'study-restate':
        /^(请|现在|接下来|可以|先|帮我|来|想)?[，, ]*(测一下|测测我|考考我|考我一道|出一道.{0,5}题|给我一道.{0,5}题)/.test(q)?'study-quiz':
        /^(?:please\s+)?(?:let me|i (?:want|would like) to) (?:try (?:to )?)?(?:explain|restate|summarize)\b/i.test(q)?'study-restate':
        /^(?:please\s+)?(?:quiz me|test me|ask me (?:a|one) (?:practice |interview )?question)\b/i.test(q)?'study-quiz':null;
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
      const accepted=await api.post<TurnTicket>(path+'/messages',{content:q,action,request_id:ticket,mode:data?.mode||'auto',project_id:project?Number(project):undefined,parent_message_id:parent,revision_of:rev});
      if(accepted.status==='generating'){
        ++version.current;
        setData(current=>{
          if(!current||current.messages.some(message=>message.id===accepted.assistant_message_id))return current;
          const user:Message={id:accepted.user_message_id,role:'user',content:q,status:'completed',sources:[],request_id:ticket,action,parent_message_id:parent,revision_of:rev};
          const reply:Message={id:accepted.assistant_message_id,role:'assistant',content:'',status:accepted.status,sources:[],attempt:accepted.attempt,parent_message_id:user.id,revision_of:rev};
          return {...current,messages:[...current.messages,user,reply]};
        });
      }
      remember(pendingKey,'');if(content.trim()===q)setContent(prev=>prev.trim()===q?'':prev);setAnchor(undefined);setRevision(undefined);follow.current=true;setAway(false);window.dispatchEvent(new Event('study-history-changed'));
    });
  }
  const cardTarget=jobs.find(j=>j.id===cardJob);
  useEffect(()=>{if(!cardJob||!cardTarget||cardTarget.status!=='SUCCEEDED'||!cardTarget.version_id)return;let live=true;
    if(cardTarget.conflict){setCardState({ key: "study.chooseNoteVersionBeforeCards" });return}
    setCardState({ key: "study.generatingCandidateCards" });api.post('/study/notes/'+cardTarget.note_id+'/cards',{version_id:cardTarget.version_id}).then(result=>{if(live){setCardState({ key: "study.candidateCardsGenerated", values: { version: cardTarget.version_id ?? "—", count: result.items.length } });remember(draftKey(id)+':cards','');setCardJob(0)}}).catch(e=>{if(live)setCardState({ key: "study.cardGenerationError", values: { reason: e.message } })});return()=>{live=false}
  },[cardTarget?.id,cardTarget?.status,cardTarget?.version_id,cardRetry,cardJob,id]);
  function organize(end:boolean,cards=false,focus=anchor){
    perform('note-'+(data?.messages.length||0)+'-'+selectNote+'-'+end+'-'+(focus||'')+'-'+cards,async key=>{
      const queued=await api.post<Job>(path+'/note-jobs',{request_id:key,focus_message_id:focus,note_id:selectNote?Number(selectNote):undefined});
      if(cards){remember(draftKey(id)+':cards',String(queued.id));setCardJob(queued.id);setCardState({ key: "study.organizingNoteThenCards" })}
      if(end)await api.patch(path,{status:'ended'});
    },true);
  }
  if(!data)return <div className="card">{loadError||t("study.openingSession")} {loadError&&<button onClick={()=>load().catch(e=>setLoadError(e.message))}>{t("study.retry")}</button>}</div>;
  const ended=data.status==='ended';
  if(cardQuiz)return <div className="study-page coach-card-review">
    <header><Link to={'/study/chat/'+id}>{t("study.backToStudySession")}</Link><h1>{t("study.reviewCardPractice")}</h1><p>{data.title}</p></header>
    <StudyPracticePanel sessionId={id} ended={ended} cardId={cardId}/>
  </div>;
  const savedReply=[...data.messages].reverse().find(m=>m.role==='assistant'&&m.status==='completed');
  const canOrganize=data.messages.some(m=>m.status==='completed')&&!busy&&!ongoing&&!generating;
  const successfulJob=jobs.find(item=>item.status==='SUCCEEDED');
  const linkedProject=projects.find(item=>item.id===data.project_id);
  const modelLabel=({auto:t("study.autoModel"),flash:t("study.flashModel"),strong:t("study.strongModel")} as Record<string,string>)[data.mode||'auto']||data.mode;
  const comparePath=(savedNoteId:number,messageId:number)=>`/study/notes/${savedNoteId}?view=compare&session=${id}&message=${messageId}`;
  const openTools=()=>{setPracticeRequest(undefined);document.getElementById('coach-tools-'+id)?.showPopover?.()};
  const changeSetting=(body:Record<string,unknown>)=>perform('settings',async()=>{await api.patch(path,body)});
  const setContext=(m:Message,edit=false)=>{setAnchor(edit?m.parent_message_id:m.id);setRevision(edit?m.id:undefined);if(edit)setContent(m.content);composer.current?.focus()};
  return <div className="study-page coach-session">
    <header className="coach-session-head"><div className="coach-head-inner">
      <div className="coach-title"><small>{t("study.studySessionStatus", { status: ended?t("study.sessionEnded"):'' })}</small><h1>{data.title}</h1><div className="coach-session-meta"><span>{t("study.currentModel", { model: modelLabel ?? t("study.autoModel") })}</span><span>{t("study.currentProject", { project: linkedProject?.name||linkedProject?.title||(data.project_id?`#${data.project_id}`:t("study.noLinkedProject")) })}</span></div></div>
      <div className="coach-header-actions">
        <button className="btn btn-ghost" onClick={openTools}>{t("study.notesAndPractice")}</button>
        <details className="coach-settings"><summary>{t("study.sessionSettings")}</summary><div className="coach-settings-panel">
          {editing?<form className="study-actions" onSubmit={e=>{e.preventDefault();perform('rename',async()=>{await api.patch(path,{title:title.trim()});setEditing(false);window.dispatchEvent(new Event('study-history-changed'))})}}><input aria-label={t("study.sessionTitle")} value={title} maxLength={160} onChange={e=>setTitle(e.target.value)}/><button className="btn" disabled={!title.trim()||busy}>{t("study.saveTitle")}</button><button type="button" className="btn btn-ghost" onClick={()=>setEditing(false)}>{t("study.cancel")}</button></form>:<button className="btn btn-ghost" onClick={()=>{setTitle(data.title);setEditing(true)}}>{t("study.editTitle")}</button>}
          <label>{t("study.modelLabel")}<select aria-label={t("study.modelMode")} disabled={busy||generating} value={data.mode||'auto'} onChange={e=>changeSetting({mode:e.target.value})}><option value="auto">{t("study.autoModel")}</option><option value="flash">{t("study.flashModel")}</option><option value="strong">{t("study.strongModel")}</option></select></label>
          <label>{t("study.linkedProjectLabel")}<select aria-label={t("study.linkedProject")} disabled={busy||generating} value={project} onChange={e=>changeSetting({project_id:e.target.value?Number(e.target.value):null})}><option value="">{t("study.noLinkedProject")}</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name||p.title}</option>)}</select></label>
        </div></details>
      </div>
    </div></header>
    <div ref={scroll} className="coach-scroll" onScroll={e=>{const el=e.currentTarget;follow.current=el.scrollHeight-el.scrollTop-el.clientHeight<60;setAway(!follow.current)}}><div className="coach-inner">
    <MemoSources items={data.context.sources}/>{!data.messages.length&&<p className="coach-empty">{t("study.startSessionPrompt")}</p>}
    {loadError && <p role="alert">{t("study.syncFailed", { reason: loadError })}</p>}
    <section className="study-thread" aria-label={t("study.chatTranscript")}>{data.messages.map(m=>{
      const stream=streamFor(m);
      const status=stream?.status||m.status;
      const content=stream?.content??m.content;
      const quickAction = m.role === 'user' && actions.find(action => m.action === action.id && content === action.prompt);
      const stage=stream?.stage||m.trace?.stage;
      const error=stream?.error||m.error;
      return <StudyTurn speaker={m.role} className={(status==='generating'?' is-generating':'')+(target===String(m.id)?' is-focused':'')} key={m.id} id={'study-message-'+m.id} tabIndex={-1}>
      <h4>{m.role==='user'?t("study.me"):t("study.studyAssistant")} <small>#{m.id} · {status==='generating'?<span className="coach-status-live"><ThinkingDots/>{t("study.generating")}</span>:({completed:t("study.saved"),failed:t("study.failed"),interrupted:t("study.interrupted")} as Record<string,string>)[status]||status}{m.trace?.tier?' · '+String(m.trace.tier):''}{status==='generating'&&stage?' · '+(stages[String(stage)] ? t(stages[String(stage)]) : String(stage)):''}</small></h4>
      {m.role==='assistant'&&status==='generating'&&(stream?.reasoning?<ThinkingPanel text={stream.reasoning} startedAt={liveStartedAt.current||Date.now()} open={!content}/>:!content?<p className="coach-reasoning-hint"><ThinkingDots/>{t("study.thinkingHint")}</p>:null)}
      {m.role==='assistant'?((content||(m.blocks?.length??0))?<StudyText {...{text:content,blocks:m.blocks,sources:m.sources,streaming:status==='generating'}}/>:null):<div className="study-content">{quickAction ? t(quickAction.label) : content}</div>}{m.role==='assistant'&&<MemoSources items={m.sources}/>}
      {error&&<p role="alert">{error}</p>}{status==='generating'&&<button className="btn btn-outline" disabled={busy} onClick={()=>stop(m)}>{t("study.interruptReply")}</button>}
      {['interrupted','failed'].includes(status)&&!ended&&<button className="btn btn-outline" disabled={busy||generating} onClick={()=>perform('retry',async()=>{await api.post(path+'/messages/'+m.id+'/retry')})}>{t("study.retryReply")}</button>}
      {m.revision_of&&<small>{t("study.messageRevisionOrigin", { messageId: m.revision_of })}</small>}
      {m.role==='assistant'&&status==='completed'&&<div className={'coach-note-bridge'+(m.id===savedReply?.id?' is-latest':'')}>
        <div className="study-actions"><button className={'btn '+(m.id===savedReply?.id?'btn-primary':'btn-outline')} disabled={!canOrganize} onClick={()=>organize(false,false,m.id)}>{job&&ongoing&&job.message_ids.includes(m.id)?t("study.organizing"):t("study.organizeThisReply")}</button>
        {!ended&&<button className="btn btn-outline" disabled={busy||generating} onClick={()=>requestPractice('explain',m.id)}>{t("study.tryExplaining")}</button>}</div>
        {successfulJob?.message_ids.includes(m.id)&&<div className="study-actions coach-note-result"><small>{successfulJob.conflict?t("study.replyInUpdateDraft"):t("study.replyInNote")}</small><Link to={'/study/notes/'+successfulJob.note_id} state={{sourceSession:id,sourceMessage:m.id}}>{t("study.openNote")}</Link>{!successfulJob.conflict&&<Link to={comparePath(successfulJob.note_id,m.id)}>{t("study.compareOriginalChat")}</Link>}</div>}
      </div>}
      {m.trace&&Object.keys(m.trace).length>0&&<MemoRunDetails trace={m.trace}/>}
      <div className="coach-turn-actions"><button onClick={async()=>{try{await navigator.clipboard.writeText(content);setCopied(m.id)}catch{setError({ key: "study.copyMessageFailed" })}}}>{copied===m.id?t("study.copied"):t("study.copy")}</button>{!ended&&status==='completed'&&<><button onClick={()=>setContext(m)}>{t("study.followUpOnMessage")}</button>{m.role==='user'?<button onClick={()=>setContext(m,true)}>{t("study.editAndResend")}</button>:<button disabled={busy||generating} onClick={()=>{const original=data.messages.find(x=>x.role==='user'&&x.request_id===m.request_id);if(original)send(original.action||'explain',original.content,original.id,m.id)}}>{t("study.regenerate")}</button>}</>}</div>
    </StudyTurn>})}</section>
    <StudyInterviewQuestion sessionId={id} canOrganize={!!savedReply && !busy && !generating} />
    </div></div>
    <footer className="coach-compose">
    {away&&<button className="coach-latest" onClick={()=>{follow.current=true;if(scroll.current)scroll.current.scrollTop=scroll.current.scrollHeight;setAway(false)}}>{t("study.backToLatestMessage")}</button>}
    {error&&<small role="alert">{typeof error === "string" ? error : t(error.key, error.values)}</small>}{!ended?<><div className="coach-chips">{actions.filter(({ id })=>['simplify','mechanism','project','diagram'].includes(id)).map(({ id, label, prompt })=><button disabled={busy||generating} key={id} onClick={()=>send(id,prompt)}>{t(label)}</button>)}<button aria-expanded={more} onClick={()=>setMore(!more)}>{t("study.more")}</button></div>
    {more&&<div className="coach-chips"><button disabled={busy||ongoing||!data.messages.length||!!cardJob} onClick={()=>organize(false,true)}>{t("study.createReviewCards")}</button><button disabled={busy||generating} onClick={()=>requestPractice('quiz')}>{t("study.quizMe")}</button>{actions.filter(({ id })=>!['simplify','mechanism','project','diagram'].includes(id)).map(({ id, label, prompt })=><button disabled={busy||generating} key={id} onClick={()=>send(id,prompt)}>{t(label)}</button>)}</div>}
    {(anchor||revision)&&<small>{t("study.referringToMessage", { messageId: revision ?? anchor ?? "", editing: revision?t("study.editingMessageCopy"):t("study.followUp") })}<button onClick={()=>{setAnchor(undefined);setRevision(undefined)}}>{t("study.unlinkMessage")}</button></small>}
    {(cardJob||cardState)&&<small role="status">{cardTarget?.status==='FAILED'?t("study.retryNoteFromTools"):cardState && t(cardState.key, cardState.values)}{cardState?.key === "study.cardGenerationError"&&<button onClick={()=>setCardRetry(r=>r+1)}>{t("study.retryCards")}</button>}{!!cardJob&&<button onClick={()=>{remember(draftKey(id)+':cards','');setCardJob(0);setCardState(null)}}>{t("study.cancelFurtherGeneration")}</button>}</small>}
    {job&&(ongoing||job.status==='FAILED')&&<small role="status" className="coach-note-status">{ongoing?<><ThinkingDots/>{t("study.organizingFrozenMessages", { count: job.message_ids.length })}</>:<><span>{t("study.organizeFailed", { reason: job.error||t("study.pleaseRetry") })}</span><button className="btn btn-outline" disabled={busy} onClick={()=>perform('retry-note',async()=>{await api.post('/study/note-jobs/'+job.id+'/retry')},true)}>{t("study.retryOrganizing")}</button></>}</small>}
    <div className="coach-input"><StudyComposerInput ref={composer} aria-label={t("study.askFollowUp")} rows={2} maxLength={8000} value={content} onSend={()=>send('explain',content)} onChange={e=>setContent(e.target.value)} placeholder={t("study.followUpPlaceholder")}/><div className="study-actions">{speech.supported&&<button className="btn btn-ghost" onClick={speech.listening?speech.stop:speech.start}>{speech.listening?t("study.stopDictation"):t("study.dictation")}</button>}{generatingMessage?<button className="btn btn-outline" disabled={busy} onClick={()=>stop(generatingMessage)}>{t("study.stopGenerating")}</button>:<button className="btn btn-primary" disabled={busy||!content.trim()} onClick={()=>send('explain',content)}>{t("study.send")}</button>}</div></div>{speech.interim||speech.error}</>:<div className="study-actions"><button className="btn btn-primary" onClick={()=>perform('resume',async()=>{await api.patch(path,{status:'active'})})}>{t("study.continueStudying")}</button><button className="btn" onClick={()=>document.getElementById('coach-tools-'+id)?.showPopover?.()}>{t("study.notesAndPractice")}</button></div>}</footer>
    <div ref={toolsPanel} className="coach-tools" id={'coach-tools-'+id} {...{popover:"auto"}}><button className="btn btn-ghost" onClick={()=>document.getElementById('coach-tools-'+id)?.hidePopover?.()}>{t("study.close")}</button>    <section className="card"><div className="study-actions"><button className="btn btn-primary" disabled={busy||ongoing||!data.messages.length} onClick={()=>organize(false)}>{t("study.createNote")}</button>
      {!ended&&<button className="btn btn-outline" disabled={busy||ongoing||generating||!data.messages.length} onClick={()=>organize(true)}>{t("study.endAndCreateNote")}</button>}
      <button className="btn btn-ghost" onClick={()=>{setChoosingNote(!choosingNote);if(!choosingNote)api.get('/study/notes').then(d=>setNotes(d.items)).catch(e=>setError(e.message))}}>{t("study.linkExistingNote")}</button>
      {choosingNote&&<select aria-label={t("study.linkExistingNote")} value={selectNote} onChange={e=>setSelectNote(e.target.value)}><option value="">{t("study.noteForSession")}</option>{notes.map(n=><option key={n.id} value={n.id}>{n.title}</option>)}</select>}</div>
      {job&&<p role="status">{t("study.noteJobSummary", { status: ongoing?t("study.organizingNote"):job.status==='FAILED'?t("study.organizeFailedRetryAvailable"):job.conflict?t("study.noteUpdateDraftGenerated"):t("study.noteGenerated"), version: job.version_id?t("study.noteVersionSuffix", { version: job.version_id }):"", count: job.message_ids.length })}{job.status==='SUCCEEDED'&&<><Link to={'/study/notes/'+job.note_id}>{t("study.openNoteWithSpace")}</Link>{!job.conflict&&savedReply&&job.message_ids.includes(savedReply.id)&&<Link to={comparePath(job.note_id,savedReply.id)}>{t("study.compareOriginalChatWithSpace")}</Link>}</>}{job.status==='FAILED'&&<><span> {job.error}</span><button className="btn btn-outline" onClick={()=>perform('retry-note',async()=>{await api.post('/study/note-jobs/'+job.id+'/retry')},true)}>{t("study.retryOrganizing")}</button></>}</p>}</section>
    {toolsVisible && <StudyPracticePanel initialRequest={practiceRequest} focusMessageId={practiceRequest?.messageId??anchor} sessionId={id} ended={ended} cardId={practiceRequest?undefined:cardId}/>}
</div></div>
}
export default function StudyChat({ questionBuilderPath }: { questionBuilderPath?: string } = {}){
  const t = useT();

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
      catch(ex){navigate(path,{state:{unsent:q,error:{ key: "study.sendUnconfirmedWithReason", values: { reason: ex instanceof Error ? ex.message : String(ex) } }}})}
    }catch(e){setErr((e as Error).message)}finally{creating.current=false;setBusy(false)}
  }
  return <StudyShell><div className="study-page coach-welcome"><small>{t("study.interviewStudyCoach")}</small><h1>{t("study.whatToStudy")}</h1><p>{t("study.studyCoachIntro")}</p>
    <form className="study-compose coach-welcome-compose" onSubmit={e=>{e.preventDefault();start()}}><textarea aria-label={t("study.studyQuestion")} onKeyDown={e=>submitKey(e,start)} rows={3} maxLength={8000} value={topic} onChange={e=>setTopic(e.target.value)} placeholder={t("study.studyTopicPlaceholder")}/>
      <div className="coach-welcome-actions"><div>{speech.supported&&<button type="button" className="btn btn-ghost" onClick={speech.listening?speech.stop:speech.start}>{speech.listening?t("study.stopDictation"):t("study.dictation")}</button>}</div><button className="btn btn-primary" disabled={!topic.trim()||busy}>{busy?t("study.opening"):t("study.beginStudying")}</button></div>
      {(speech.error||speech.interim)&&<small role={speech.error?'alert':'status'}>{speech.error||speech.interim}</small>}
    </form>
    {questionBuilderPath && <Link className="btn btn-outline" to={questionBuilderPath}>{t("study.createQuestionsWithCoach")}</Link>}
    {latestChat&&<Link className="coach-resume-row" to={'/study/chat/'+latestChat.id}><span><small>{t("study.recentSessions")}</small><strong>{latestChat.title}</strong></span><span>{t("study.resumeStudying")}<span aria-hidden="true">→</span></span></Link>}
    {err&&<p role="alert">{err}</p>}</div></StudyShell>
}
