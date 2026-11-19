import { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';

interface Submission {
  submission_id:number; revision:number; status:string; can_retry:boolean; error?:{message?:string};
  payload:{text_answer:string}; feedback?:Record<string,unknown>; assistance:{hint_used:boolean;reference_viewed:boolean;feedback_seen:boolean};
}
interface Practice {
  id:number; session_id:number; question:string; knowledge_point:string; skill_key:string; skill_name:string;
  card_id?:number; kind:'quiz'|'explain'; status:string; submissions:Submission[]; retest_exam_id?:number; assistance:{hint_used:boolean;reference_viewed:boolean};
}
function Feedback({value,answer}:{value:Record<string,unknown>;answer:string}) {
  const criteria=Array.isArray(value.criterion_scores) ? value.criterion_scores : [];
  return <section className="study-practice-feedback" aria-label="本次评价"><h4>本次评价</h4>
    {criteria.map((row,index)=>{
      const quotes=[...new Set<string>((row.answer_quotes||[]).filter((quote:unknown)=>typeof quote==='string' && quote.trim() && answer.includes(quote)))];
      const gaps=[...new Set<string>([row.why_not_higher,...(row.missing_points||[])].filter(Boolean))];
      return <div key={row.criterion_id}><h5>评分要点 {index+1} · {row.score}/4</h5>
        {quotes.length>0 && <p>原答引用：{quotes.map(quote=><q key={quote}>{quote}</q>)}</p>}
        {row.reasoning_summary && <p>{row.score>0?'答对之处':'本项表现'}：{row.reasoning_summary}</p>}
        {row.score<4 && gaps.length>0 && <p>仍有缺口：{gaps.join('；')}</p>}
      </div>;
    })}
    {typeof value.scope_note==='string' && <p className="hint">{value.scope_note}</p>}
  </section>;
}
const statusLabel:Record<string,string>={COMPLETED:'已评价',FAILED:'评价失败',NEEDS_REVIEW:'待人工复核',CANCELLED:'评价已取消',QUEUED:'等待评价',RUNNING:'正在评价'};

export interface PracticeRequest { kind: 'quiz' | 'explain'; messageId?: number; key: string }
export default function StudyPracticePanel({sessionId,cardId,ended,focusMessageId,initialRequest}:{sessionId:number;cardId?:number;ended:boolean;focusMessageId?:number;initialRequest?:PracticeRequest}) {
  const base='/study/sessions/'+sessionId+'/practices';
  const [params]=useSearchParams();
  const cardQuiz=cardId && params.get('practice')==='quiz' ? cardId : undefined;
  const scope=base+'/'+(cardQuiz??'');
  const [items,setItems]=useState<Practice[]>([]);
  const [loadedBase,setLoadedBase]=useState('');
  const [selected,setSelected]=useState<{scope:string;id:number}>();
  const [skills,setSkills]=useState<{key:string;name:string}[]>([]);
  const [skill,setSkill]=useState('');
  const [answer,setAnswer]=useState('');
  const [help,setHelp]=useState('');
  const [err,setErr]=useState('');
  const [busy,setBusy]=useState(false);
  const [result,setResult]=useState<any>(null);
  const lastKey=useRef(new Map<string,string>());
  const sequence=useRef(0);
  const inFlight=useRef(false);
  const autoAttempt=useRef('');
  const currentBase=useRef(base);
  currentBase.current=base;
  const currentScope=useRef(scope);
  currentScope.current=scope;
  const ready=loadedBase===base;
  const matching=ready && cardQuiz ? items.find(item=>item.card_id===cardQuiz && item.kind==='quiz') : undefined;
  const p=ready ? (selected?.scope===scope ? items.find(item=>item.id===selected.id) : undefined) ?? (cardQuiz ? matching : items[0]) : undefined;
  const history=ready ? items.filter(item=>item.id!==p?.id) : [];
  async function refresh() {
    if(currentBase.current!==base)return;
    const version=++sequence.current;
    const value=await api.get(base);
    if(version===sequence.current && currentBase.current===base){setItems(value.items);setLoadedBase(base)}
  }
  useEffect(()=>{
    refresh().catch(e=>{if(currentBase.current===base)setErr(e.message)});
    api.get('/profile').then(data=>setSkills(data.domains||[])).catch(()=>{});
    return ()=>{sequence.current++};
  },[base]);
  const pending=p?.submissions.some(s=>!['COMPLETED','FAILED','NEEDS_REVIEW','CANCELLED'].includes(s.status));
  useEffect(()=>{
    if(!pending)return;
    const timer=setInterval(()=>refresh().catch(e=>setErr(e.message)),1800);
    return ()=>clearInterval(timer);
  },[pending,base]);
  useEffect(()=>{
    let live=true;
    if(p?.retest_exam_id)api.get('/study/practices/'+p.id+'/retest').then(v=>{if(live)setResult(v)}).catch(e=>{if(live)setErr(e.message)});
    else setResult(null);
    return ()=>{live=false};
  },[p?.retest_exam_id,p?.id]);
  async function perform(op:string,fn:(key:string)=>Promise<void>,stableKey?:string) {
    if(inFlight.current)return;
    inFlight.current=true;
    setErr('');setBusy(true);
    try{const key=stableKey||lastKey.current.get(op)||crypto.randomUUID(); lastKey.current.set(op,key);
      await fn(key);lastKey.current.delete(op);await refresh()}catch(e){if(currentScope.current===scope)setErr((e as Error).message)}finally{inFlight.current=false;setBusy(false)}
  }
  function start(kind:'quiz'|'explain', requestKey?:string) {
    if(!ready || ended)return;
    if(kind==='quiz' && matching){setSelected({scope,id:matching.id});return}
    const stableKey=kind==='quiz' && cardQuiz ? 'study-card-quiz-'+cardQuiz : requestKey;
    perform('start-'+kind+'-'+skill+'-'+(cardId||'')+'-'+(focusMessageId||''),async key=>{
      const item:Practice=await api.post(base,{kind,skill_key:cardQuiz ? undefined : skill||undefined,card_id:cardId,...(!cardId&&focusMessageId?{focus_message_id:focusMessageId}:{}),request_key:key});
      if(currentScope.current===scope){
        setItems(current=>[item,...current.filter(existing=>existing.id!==item.id)]);
        setSelected({scope,id:item.id});
      }
    },stableKey);
  }
  const startedRequest = useRef('');
  useEffect(() => {
    if (!ready || ended || busy || !initialRequest || startedRequest.current === initialRequest.key) return;
    startedRequest.current = initialRequest.key;
    start(initialRequest.kind, initialRequest.key);
  }, [ready, ended, busy, initialRequest]);
  useEffect(()=>{setErr('')},[scope]);
  useEffect(()=>{setAnswer('');setHelp('')},[scope,p?.id]);
  useEffect(()=>{
    if(!cardQuiz || ended || busy || !ready || matching || autoAttempt.current===scope)return;
    autoAttempt.current=scope;
    start('quiz');
  },[scope,cardQuiz,ended,busy,ready,matching]);
  const sub=p?.submissions[p.submissions.length-1];
  const path='/study/practices/'+p?.id;
  const active=p && p.status!=='SKIPPED';
  return <section className="card study-practice" aria-label="主动练习">
    <div className="study-actions"><h3>主动练习</h3>{!cardId&&focusMessageId&&<small>练习依据：消息 #{focusMessageId}</small>}
      {!ended && <><label>练习知识点 <select value={skill} onChange={e=>setSkill(e.target.value)}><option value="">沿用当前主题</option>{skills.map(s=><option key={s.key} value={s.key}>{s.name}</option>)}</select></label>
      <button className="btn btn-outline" disabled={!ready||busy} id="study-restate" onClick={()=>start('explain')}>让我试着解释</button>
      <button className="btn btn-outline" disabled={!ready||busy} id="study-quiz" onClick={()=>start('quiz')}>{cardId?'用这张卡测一下':'测一下'}</button></>}
    </div>
    {err && <p role="alert">{err}</p>}
    {!ready && !err && <p role="status">正在加载练习…</p>}
    {!p && ready && busy && <p role="status">正在准备练习…</p>}
    {p && <article><p className="hint">{p.knowledge_point} · {p.skill_name||'未关联能力项'}</p><div className="study-practice-question"><small>当前问题</small><p className="study-content">{p.question}</p></div>
    {p.submissions.map(s=><details key={s.submission_id} open><summary>{s.revision===1?'首次原答':'修正 #'+s.revision} · {statusLabel[s.status]||'评价中'}</summary>
      <blockquote>{s.payload.text_answer}</blockquote>
      <p className="hint">{[s.assistance?.hint_used?'使用过提示':'',s.assistance?.reference_viewed?'看过答案':'',s.assistance?.feedback_seen?'已见反馈':''].filter(Boolean).join(' · ')||'提交时未记录辅助'}</p>
      {s.status==='COMPLETED' && s.feedback && <Feedback value={s.feedback} answer={s.payload.text_answer}/>}
      {s.error && <p role="alert">{s.error.message||'评价失败'}</p>}
      {s.can_retry && <button className="btn btn-outline" onClick={()=>perform('retry',async()=>{await api.post('/learning/submissions/'+s.submission_id+'/retry')})}>重试评价</button>}
    </details>)}
    {active && !ended && <><label>{sub?'修正回答':'我的原答'}<textarea value={answer} onChange={e=>setAnswer(e.target.value)} rows={3}/></label>
      <div className="study-actions"><button className="btn btn-primary" disabled={busy||pending||!answer.trim()} onClick={()=>perform('answer-'+p.id+'-'+answer,async key=>{
        await api.post(path+'/answers',{text_answer:answer.trim(),request_key:key,parent_submission_id:sub?.submission_id});setAnswer('');
      })}>{sub?'提交修正':'提交原答'}</button>
      {(['hint','reference'] as const).map(kind=><button key={kind} className="btn btn-outline" disabled={busy} onClick={()=>perform(kind,async key=>{
        const data=await api.post(path+'/assistance',{kind,request_key:key});setHelp(data.content)
      })}>{kind==='hint'?'请求提示':'查看参考答案'}</button>)}
      <button className="btn btn-ghost" disabled={busy} onClick={()=>perform('skip',async()=>{await api.post(path+'/skip');setHelp('')})}>跳过并继续讲解</button></div></>}
    {help && <p className="study-content">{help}</p>}
    {sub && active && (!p.retest_exam_id || ['ABORTED','EXPIRED'].includes(result?.status)) && !ended && <button className="btn btn-outline" disabled={busy||pending} onClick={()=>perform('retest',async()=>{await api.post(path+'/retest')})}>生成新情境复测</button>}
    {p.retest_exam_id && <p><Link to={'/study/retest'+(result?.status==='COMPLETED'?'/report':'')+'?id='+p.retest_exam_id}>打开新情境复测</Link> {result && <span> · {result.status} · {result.skill_key} · 有效证据 {result.evidence_ids.length} 条 · 当前等级 {result.skill_state?.estimated_level??'待验证'}</span>}</p>}
    </article>}
    {history.length>0 && <details><summary>其他练习（{history.length}）</summary>{history.map(r=><div key={r.id}><strong>{r.question}</strong>{r.submissions.map(s=><p key={s.submission_id}>{s.revision===1?'原答':'修正'}：{s.payload.text_answer} · {statusLabel[s.status]||'评价中'}</p>)}{r.retest_exam_id&&<Link to={'/study/retest?id='+r.retest_exam_id}>查看复测 #{r.retest_exam_id}</Link>}</div>)}</details>}
  </section>
}
