import React,{useRef,useState} from 'react';
import {api} from '../api/client';
import {LearningMemoryCandidate,learningError} from '../lib/learningPath';
import {Modal} from './ui';

export default function LearningMemoryReview({goalId,items,onReviewed}:{goalId:number;items:LearningMemoryCandidate[];onReviewed:()=>void|Promise<void>}){
  const [selected,setSelected]=useState<Record<number,number>>({}),[editing,setEditing]=useState<LearningMemoryCandidate|null>(null),[content,setContent]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const pending=useRef(false);
  const eligible=(item:LearningMemoryCandidate)=>item.status==='CANDIDATE' && item.freshness==='CURRENT';
  const selectedStale=Object.entries(selected).some(([id,revision])=>!items.some(item=>item.id===Number(id) && item.revision===revision && eligible(item)));
  const review=async(action:'confirm'|'ignore',selection:Record<number,number>,edited?:string)=>{
    if(pending.current || !Object.keys(selection).length)return;pending.current=true;setBusy(true);setError('');setNotice('');
    const submitted=Object.entries(selection).map(([id,revision])=>({candidate_id:Number(id),expected_revision:revision,action,...(edited!==undefined?{edited_content:edited}:{})}));
    try{
      const result=await api.post<{results:{id:number;status:string;revision?:number;error?:string}[]}>(`/learning/goals/${goalId}/memory-review`,{items:submitted});
      const success=result.results.filter(r=>['CONFIRMED','IGNORED'].includes(r.status) && !r.error);
      const failure=submitted.filter(item=>!success.some(r=>r.id===item.candidate_id));
      if(failure.length)setError(failure.map(item=>result.results.find(r=>r.id===item.candidate_id)?.error || `候选 #${item.candidate_id} 尚未确认，请刷新后核对`).join('；'));
      if(success.length){setNotice(`已${action==='confirm'?'确认':'忽略'} ${success.length} 条描述；不提高能力分数。`);setSelected(old=>Object.fromEntries(Object.entries(old).filter(([id])=>!success.some(r=>r.id===Number(id)))));if(editing && success.some(r=>r.id===editing.id)){setEditing(null);setContent('');}await onReviewed();}
    }catch(e){setError(learningError(e));}finally{pending.current=false;setBusy(false);}
  };
  return <section className="card" style={{marginTop:16}} aria-label="学习候选记忆"><h3>阶段小结与待确认记忆</h3><p className="hint">原始提交和反馈已保存；以下概括经你确认后才作为长期记忆。确认只表示接受描述，不代表整个能力域已掌握，也不提高能力分数。</p>{error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}{!items.length && <p className="hint">目前没有学习记忆候选。没有反馈或候选时不填充示例结论。</p>}
    {items.some(eligible) && <div style={{display:'flex',gap:8,flexWrap:'wrap'}}><button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>setSelected(Object.fromEntries(items.filter(eligible).slice(0,50).map(item=>[item.id,item.revision])))}>选择可确认候选</button><button className="btn btn-primary btn-sm" disabled={busy || !Object.keys(selected).length || selectedStale} onClick={()=>review('confirm',selected)}>确认所选描述</button></div>}
    {items.map(item=><article key={item.id} style={{marginTop:12,paddingTop:12,borderTop:'1px solid var(--border)'}}>{item.status==='CANDIDATE' && <input type="checkbox" aria-label={`选择学习候选 ${item.id}`} checked={selected[item.id]!==undefined} disabled={busy || !eligible(item)} onChange={e=>setSelected(old=>{const next={...old};if(e.target.checked)next[item.id]=item.revision;else delete next[item.id];return next;})}/>}<span className="chip chip-slate">{item.status==='CANDIDATE'?'待确认':item.eligible_for_personalization?'已确认且来源有效':'历史或来源待复核'}</span><p style={{whiteSpace:'pre-wrap'}}>{item.content}</p>{item.freshness!=='CURRENT' && <p className="hint">来源已变化，需要复核；不能确认或用于新的个性化任务。</p>}<details><summary>来源与描述版本</summary><p>描述版本 {item.revision} · 提交 #{item.meta?.submission_id || '—'} 第 {item.meta?.submission_revision || '—'} 版 · 反馈运行 #{item.meta?.scoring_run_id || '—'}</p>{item.meta?.origin_refs && <p>来源考试 #{String(item.meta.origin_refs.exam_id ?? '—')} · 评分项 {String(item.meta.origin_refs.criterion_id ?? '未记录')}</p>}</details>{item.status==='CANDIDATE' && <div style={{display:'flex',gap:8,marginTop:8}}><button className="btn btn-primary btn-sm" disabled={busy || !eligible(item)} onClick={()=>review('confirm',{[item.id]:item.revision})}>确认记忆</button><button className="btn btn-outline btn-sm" disabled={busy || !eligible(item)} onClick={()=>{setEditing(item);setContent(item.content);setError('');}}>编辑后确认</button><button className="btn btn-ghost btn-sm" disabled={busy} onClick={()=>review('ignore',{[item.id]:item.revision})}>忽略</button></div>}</article>)}
    {editing && <Modal title="编辑学习候选描述" onClose={()=>{if(!busy)setEditing(null);}} footer={<><button className="btn btn-outline" disabled={busy} onClick={()=>setEditing(null)}>取消</button><button className="btn btn-primary" disabled={busy || !content.trim()} onClick={()=>review('confirm',{[editing.id]:editing.revision},content.trim())}>保存并确认</button></>}><p className="hint">保留本次观察的范围、来源和不确定性；确认不会改分。</p><textarea className="textarea" rows={6} aria-label="学习候选记忆内容" value={content} disabled={busy} onChange={e=>setContent(e.target.value)}/>{error && <p role="alert">{error}。编辑内容已保留。</p>}</Modal>}
  </section>;
}
