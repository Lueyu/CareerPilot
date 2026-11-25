import {useT} from '../i18n';
import React,{useCallback,useEffect,useRef,useState} from 'react';
import {Link} from 'react-router-dom';
import {api} from '../api/client';
import {MemoryCandidate} from '../lib/projectIntelligence';
import {messageOf} from '../lib/questionWorkshop';
import {Modal} from './ui';
import SourceCitations from './SourceCitations';

type ReviewProblem = {id: number; error?: string} | {id: number; missing: true};
type CandidateError = {detail: string} | {stale: true} | {problems: ReviewProblem[]};

const FACT_LABELS:Record<string,string>={CODE_OBSERVED:'projects.observedInCode',INFERRED:'projects.inferredFact',DOC_CLAIM:'projects.documentedClaim',UNASSESSED:'projects.unassessedFact'};
export default function ProjectMemoryCandidates({projectId,onReviewed,refreshKey=0}: {projectId?:number;onReviewed?:()=>void|Promise<void>;refreshKey?:number|string}) {
  const t = useT();
  const [items,setItems]=useState<MemoryCandidate[]>([]);const [selected,setSelected]=useState<Record<number,number>>({});
  const [loading,setLoading]=useState(true);const [busy,setBusy]=useState(false);const [failure,setError]=useState<CandidateError>();
  const [notice,setNotice]=useState<{action: 'confirm' | 'ignore'; count: number}>();
  const error = !failure ? '' : 'detail' in failure ? failure.detail : 'stale' in failure ? t('projects.staleSelection') : failure.problems.map(problem =>
    'missing' in problem ? t('projects.candidateNoReviewResult', {id: problem.id}) : t('projects.candidateReviewError', {id: problem.id, error: problem.error || t('projects.reviewIncomplete')})
  ).join(t('projects.sentenceListSeparator'));
  const [editing,setEditing]=useState<MemoryCandidate|null>(null);const [content,setContent]=useState('');
  const request=useRef(0);const pending=useRef(false);
  const stale=(id:number)=>items.some(c=>c.id===id && c.freshness && c.freshness!=='CURRENT');
  const selectedStale=Object.keys(selected).some(id=>stale(Number(id)));
  const load=useCallback(async()=>{const version=++request.current;setLoading(true);try{const data=await api.get<{items:MemoryCandidate[]}>(projectId?`/projects/${projectId}/memory-candidates`:'/memory-candidates');if(version===request.current)setItems(data.items.filter(c=>c.status==='PENDING_REVIEW' || c.status==='CANDIDATE'));}catch(e){if(version===request.current)setError({detail: messageOf(e)});}finally{if(version===request.current)setLoading(false);}},[projectId]);
  useEffect(()=>{setItems([]);setSelected({});setEditing(null);setError(undefined);return()=>{request.current++;};},[load]);
  useEffect(()=>{void load();},[load,refreshKey]);
  const review=async(action:'confirm'|'ignore',selection:Record<number,number>,edited?:string)=>{
    if(pending.current || !Object.keys(selection).length)return;pending.current=true;setBusy(true);setError(undefined);setNotice(undefined);
    if(action==='confirm' && Object.keys(selection).some(id=>stale(Number(id)))){pending.current=false;setBusy(false);setError({stale: true});return;}
    const submitted=Object.entries(selection).map(([id,revision])=>({candidate_id:Number(id),expected_revision:revision,action,...(edited!==undefined?{edited_content:edited}:{})}));
    try{
      const result=await api.post<{results:{id:number;status:string;error?:string;revision?:number}[]}>('/memory-candidates/review',{items:submitted});
      const success=result.results.filter(r=>!r.error && ['CONFIRMED','IGNORED','ACTIVE','DISMISSED','SUCCESS'].includes(r.status));
      const failed=result.results.filter(r=>!success.includes(r));
      const missing=submitted.filter(s=>!result.results.some(r=>r.id===s.candidate_id));
      if(failed.length || missing.length)setError({problems: [...failed, ...missing.map(r => ({id: r.candidate_id, missing: true as const}))]});
      if(success.length){setNotice({action, count: success.length});setSelected(old=>Object.fromEntries(Object.entries(old).filter(([id])=>!success.some(r=>r.id===Number(id)))));if(editing && success.some(r=>r.id===editing.id)){setEditing(null);setContent('');}await load();await onReviewed?.();}
    }catch(e){setError({detail: messageOf(e)});}finally{pending.current=false;setBusy(false);}
  };
  return <section className="card" id="project-memory-candidates" style={{padding:18,marginTop:18}}>
    <div style={{display:'flex',gap:12,justifyContent:'space-between',alignItems:'center'}}><h3>{t('projects.projectMemoryCandidates')}</h3><button className="btn btn-ghost btn-sm" disabled={busy || loading} onClick={()=>{setError(undefined);void load();}}>{t('projects.refreshCandidates')}</button></div>
    <p className="hint">{t('projects.memoryCandidateGuidance')}</p>
    {loading && <p role="status">{t('projects.loadingCandidates')}</p>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{t(notice.action === 'confirm' ? 'projects.memoriesConfirmed' : 'projects.memoriesIgnored', {count: notice.count})}</p>}
    {!!items.length && <div style={{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap'}}>
      <span>{t('projects.candidateSelectionCount', {total: items.length, selected: Object.keys(selected).length})}</span>
      <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>setSelected(Object.fromEntries(items.map(c=>[c.id,c.revision])))}>{t('projects.selectCurrentCandidates')}</button>
      <button className="btn btn-primary btn-sm" disabled={busy || !Object.keys(selected).length || selectedStale} onClick={()=>review('confirm',selected)}>{t('projects.confirmSelectedMemories')}</button>
      <button className="btn btn-ghost btn-sm" disabled={busy || !Object.keys(selected).length} onClick={()=>review('ignore',selected)}>{t('projects.ignoreSelectedMemories')}</button>
    </div>}
    {items.map(c=><article key={c.id} style={{padding:'14px 0',borderTop:'1px solid var(--border)',marginTop:12}}>
      <label style={{display:'flex',gap:8,alignItems:'center'}}><input type="checkbox" aria-label={t('projects.selectMemoryCandidate', {id: c.id})} disabled={busy} checked={selected[c.id]!==undefined} onChange={e=>setSelected(old=>{const next={...old};if(e.target.checked)next[c.id]=c.revision;else delete next[c.id];return next;})}/><strong>{t(FACT_LABELS[c.fact_type] || 'projects.unverifiedProjectKnowledge')}</strong><span className="chip chip-slate">{t('projects.pendingConfirmation')}</span></label>
      <p style={{whiteSpace:'pre-wrap'}}>{c.content}</p>
      {stale(c.id) && <p className="demo-banner">{t('projects.staleCandidateNotice')}</p>}
      {c.change_reason && <p className="hint">{t('projects.candidateChangeReason', {reason: c.change_reason})}</p>}{c.suggested_use && <p className="hint">{t('projects.candidateSuggestedUse', {use: c.suggested_use})}</p>}
      {!projectId && <Link to={`/assets/project/${c.project_id}#project-memory-candidates`}>{t('projects.backToProjectReport')}</Link>}
      <details><summary>{t('projects.viewSourceAndVersion')}</summary><p className="hint">{t('projects.candidateReportVersion', {id: c.report_id, revision: c.revision})}{c.base_memory_id ? t('projects.candidateBasedOnMemory', {id: c.base_memory_id, revision: c.base_memory_revision ?? 'null'}) : ''}</p><SourceCitations citations={c.citations || []} sourceExcerpts={c.source_excerpts}/></details>
      <div style={{display:'flex',gap:8,marginTop:10}}><button className="btn btn-primary btn-sm" disabled={busy || stale(c.id)} onClick={()=>review('confirm',{[c.id]:c.revision})}>{t('projects.confirmMemory')}</button><button className="btn btn-outline btn-sm" disabled={busy || stale(c.id)} onClick={()=>{setEditing(c);setContent(c.content);setError(undefined);}}>{t('projects.editAndConfirmMemory')}</button><button className="btn btn-ghost btn-sm" disabled={busy} onClick={()=>review('ignore',{[c.id]:c.revision})}>{t('projects.ignoreMemory')}</button></div>
    </article>)}
    {!loading && !error && !items.length && <p className="hint">{t('projects.noPendingMemories')}</p>}
    {editing && <Modal title={t('projects.editProjectMemory')} onClose={()=>{if(!busy)setEditing(null);}} footer={<><button className="btn btn-outline" disabled={busy} onClick={()=>setEditing(null)}>{t('projects.cancel')}</button><button className="btn btn-primary" disabled={busy || !content.trim() || stale(editing.id)} onClick={()=>review('confirm',{[editing.id]:editing.revision},content.trim())}>{t('projects.confirmThisMemory')}</button></>}><p className="hint">{t('projects.candidateEditNotice', {revision: editing.revision})}</p><textarea className="textarea" aria-label={t('projects.memoryCandidateContent')} rows={7} value={content} onChange={e=>setContent(e.target.value)}/>{error && <p role="alert">{t('projects.candidateInputPreserved', {error})}</p>}</Modal>}
  </section>;
}
