import React,{useCallback,useEffect,useRef,useState} from 'react';
import {Link} from 'react-router-dom';
import {api} from '../api/client';
import {MemoryCandidate} from '../lib/projectIntelligence';
import {messageOf} from '../lib/questionWorkshop';
import {Modal} from './ui';
import SourceCitations from './SourceCitations';

const FACT_LABELS:Record<string,string>={CODE_OBSERVED:'代码中观察到',INFERRED:'待验证推断',DOC_CLAIM:'文档声明',UNASSESSED:'尚未判断'};
export default function ProjectMemoryCandidates({projectId,onReviewed,refreshKey=0}: {projectId?:number;onReviewed?:()=>void|Promise<void>;refreshKey?:number|string}) {
  const [items,setItems]=useState<MemoryCandidate[]>([]);const [selected,setSelected]=useState<Record<number,number>>({});
  const [loading,setLoading]=useState(true);const [busy,setBusy]=useState(false);const [error,setError]=useState('');const [notice,setNotice]=useState('');
  const [editing,setEditing]=useState<MemoryCandidate|null>(null);const [content,setContent]=useState('');
  const request=useRef(0);const pending=useRef(false);
  const stale=(id:number)=>items.some(c=>c.id===id && c.freshness && c.freshness!=='CURRENT');
  const selectedStale=Object.keys(selected).some(id=>stale(Number(id)));
  const load=useCallback(async()=>{const version=++request.current;setLoading(true);try{const data=await api.get<{items:MemoryCandidate[]}>(projectId?`/projects/${projectId}/memory-candidates`:'/memory-candidates');if(version===request.current)setItems(data.items.filter(c=>c.status==='PENDING_REVIEW' || c.status==='CANDIDATE'));}catch(e){if(version===request.current)setError(messageOf(e));}finally{if(version===request.current)setLoading(false);}},[projectId]);
  useEffect(()=>{setItems([]);setSelected({});setEditing(null);setError('');return()=>{request.current++;};},[load]);
  useEffect(()=>{void load();},[load,refreshKey]);
  const review=async(action:'confirm'|'ignore',selection:Record<number,number>,edited?:string)=>{
    if(pending.current || !Object.keys(selection).length)return;pending.current=true;setBusy(true);setError('');setNotice('');
    if(action==='confirm' && Object.keys(selection).some(id=>stale(Number(id)))){pending.current=false;setBusy(false);setError('所选候选来源已变化，需要重新核对，当前不能确认。');return;}
    const submitted=Object.entries(selection).map(([id,revision])=>({candidate_id:Number(id),expected_revision:revision,action,...(edited!==undefined?{edited_content:edited}:{})}));
    try{
      const result=await api.post<{results:{id:number;status:string;error?:string;revision?:number}[]}>('/memory-candidates/review',{items:submitted});
      const success=result.results.filter(r=>!r.error && ['CONFIRMED','IGNORED','ACTIVE','DISMISSED','SUCCESS'].includes(r.status));
      const failed=result.results.filter(r=>!success.includes(r));
      const missing=submitted.filter(s=>!result.results.some(r=>r.id===s.candidate_id));
      if(failed.length || missing.length)setError([...failed.map(r=>`#${r.id}：${r.error || '审核未完成，请刷新后核对'}`),...missing.map(r=>`#${r.candidate_id}：未收到审核结果`)].join('；'));
      if(success.length){setNotice(`已${action==='confirm'?'确认':'忽略'} ${success.length} 条记忆。`);setSelected(old=>Object.fromEntries(Object.entries(old).filter(([id])=>!success.some(r=>r.id===Number(id)))));if(editing && success.some(r=>r.id===editing.id)){setEditing(null);setContent('');}await load();await onReviewed?.();}
    }catch(e){setError(messageOf(e));}finally{pending.current=false;setBusy(false);}
  };
  return <section className="card" id="project-memory-candidates" style={{padding:18,marginTop:18}}>
    <div style={{display:'flex',gap:12,justifyContent:'space-between',alignItems:'center'}}><h3>待确认的项目记忆</h3><button className="btn btn-ghost btn-sm" disabled={busy || loading} onClick={()=>{setError('');void load();}}>刷新候选</button></div>
    <p className="hint">先核对来源和内容，再确认用于后续项目补题。候选不会自动成为你的经历，也不会提高能力评分。</p>
    {loading && <p role="status">正在读取候选记忆…</p>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!!items.length && <div style={{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
      <span>{items.length} 条待确认 · 已选 {Object.keys(selected).length} 条</span>
      <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>setSelected(Object.fromEntries(items.map(c=>[c.id,c.revision])))}>选择当前列表</button>
      <button className="btn btn-primary btn-sm" disabled={busy || !Object.keys(selected).length || selectedStale} onClick={()=>review('confirm',selected)}>确认所选记忆</button>
      <button className="btn btn-ghost btn-sm" disabled={busy || !Object.keys(selected).length} onClick={()=>review('ignore',selected)}>忽略所选记忆</button>
    </div>}
    {items.map(c=><article key={c.id} style={{padding:'14px 0',borderTop:'1px solid var(--border)',marginTop:12}}>
      <label style={{display:'flex',gap:8,alignItems:'center'}}><input type="checkbox" aria-label={`选择候选记忆 ${c.id}`} disabled={busy} checked={selected[c.id]!==undefined} onChange={e=>setSelected(old=>{const next={...old};if(e.target.checked)next[c.id]=c.revision;else delete next[c.id];return next;})}/><strong>{FACT_LABELS[c.fact_type] || '待核对的项目认知'}</strong><span className="chip chip-slate">待确认</span></label>
      <p style={{whiteSpace:'pre-wrap'}}>{c.content}</p>
      {stale(c.id) && <p className="demo-banner">来源已变化，需要重新核对。本条候选不能确认生效，可忽略或等待新的报告。</p>}
      {c.change_reason && <p className="hint">为何提出：{c.change_reason}</p>}{c.suggested_use && <p className="hint">确认后用途：{c.suggested_use}</p>}
      {!projectId && <Link to={`/assets/project/${c.project_id}#project-memory-candidates`}>回到项目报告 →</Link>}
      <details><summary>查看来源与版本</summary><p className="hint">报告 #{c.report_id} · 候选版本 {c.revision}{c.base_memory_id?` · 基于记忆 #${c.base_memory_id} 的版本 ${c.base_memory_revision}`:''}</p><SourceCitations citations={c.citations || []} sourceExcerpts={c.source_excerpts}/></details>
      <div style={{display:'flex',gap:8,marginTop:10}}><button className="btn btn-primary btn-sm" disabled={busy || stale(c.id)} onClick={()=>review('confirm',{[c.id]:c.revision})}>确认记忆</button><button className="btn btn-outline btn-sm" disabled={busy || stale(c.id)} onClick={()=>{setEditing(c);setContent(c.content);setError('');}}>编辑后确认</button><button className="btn btn-ghost btn-sm" disabled={busy} onClick={()=>review('ignore',{[c.id]:c.revision})}>忽略</button></div>
    </article>)}
    {!loading && !error && !items.length && <p className="hint">当前没有待确认的项目记忆。报告与候选分阶段保存；可查看任务状态或刷新候选核对。</p>}
    {editing && <Modal title="编辑并确认项目记忆" onClose={()=>{if(!busy)setEditing(null);}} footer={<><button className="btn btn-outline" disabled={busy} onClick={()=>setEditing(null)}>取消</button><button className="btn btn-primary" disabled={busy || !content.trim() || stale(editing.id)} onClick={()=>review('confirm',{[editing.id]:editing.revision},content.trim())}>确认这条记忆</button></>}><p className="hint">基于候选版本 {editing.revision} 保存。若来源已变化，需要重新核对，系统不会覆盖你的输入。</p><textarea className="textarea" aria-label="候选记忆内容" rows={7} value={content} onChange={e=>setContent(e.target.value)}/>{error && <p role="alert">{error}。输入内容已保留。</p>}</Modal>}
  </section>;
}
