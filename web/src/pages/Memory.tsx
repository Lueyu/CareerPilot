import { getLocale, t, useT } from '../i18n';
import { formatProgressDate } from '../lib/coreLoop';
import React, {useCallback, useEffect, useState} from 'react';
import {Link, useSearchParams} from 'react-router-dom';
import {useAppState} from '../components/AppState';
import {Modal, PageHead} from '../components/ui';
import {SourceCitations} from '../components/ProjectAnalysisPanel';
import {api} from '../api/client';
import {ProjectMemory} from '../lib/projectIntelligence';
import {messageOf} from '../lib/questionWorkshop';
import ProjectMemoryCandidates from '../components/ProjectMemoryCandidates';
import MemoryTimeline, {learningMemory, sourceName, type MemoryEntry} from '../components/MemoryTimeline';

function completionSource(item:ProjectMemory) {
  const meta=item.meta;
  if (meta?.exam_kind && meta.exam_id) return <Link to={`/exam/diagnosis/report?id=${meta.exam_id}`}>{t('progress.memoryOriginalExam', { id: meta.exam_id })}</Link>;
  if (meta?.daily_plan_date && meta.daily_plan_item_key) {
    const query=new URLSearchParams({plan:'day',date:meta.daily_plan_date,item:meta.daily_plan_item_key});
    return <Link to={`/learning?${query}`}>{t('progress.memoryDailyAnswer', { date: formatProgressDate(meta.daily_plan_date) })}</Link>;
  }
  return meta?.source_task_id && <Link to={`/learning/task/${meta.source_task_id}`}>{t('progress.memorySourceTask', { id: meta.source_task_id })}</Link>;
}

export default function Memory() {
  const t = useT();
  const {toast}=useAppState();
  const [params,setParams]=useSearchParams();
  const [items,setItems]=useState<MemoryEntry[]>([]);
  const [loading,setLoading]=useState(true);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [loadError,setLoadError]=useState('');
  const [pasteText,setPasteText]=useState('');
  const [correcting,setCorrecting]=useState<ProjectMemory|null>(null);
  const [correctText,setCorrectText]=useState('');
  const reload=useCallback(async()=>{
    setLoading(true);
    try{const data=await api.get<{items:MemoryEntry[]}>('/memory');setItems(data.items);setLoadError('');}
    catch(e){setLoadError(messageOf(e));setItems([]);}
    finally{setLoading(false);}
  },[]);
  useEffect(()=>{void reload();},[reload]);
  const perform=async(fn:()=>Promise<void>)=>{setBusy(true);setError('');try{await fn();}catch(e){setError(messageOf(e));}finally{setBusy(false);}};
  const review=(m:ProjectMemory, action:'confirm'|'ignore')=>perform(async()=>{
    if (learningMemory(m)) {
      const result=await api.post<{results:{status:string;error?:string}[]}>(`/learning/goals/${m.meta?.goal_id}/memory-review`,{items:[{candidate_id:m.id,expected_revision:m.revision,action}]});
      const decision=result.results[0];
      if (decision?.status!==(action==='confirm'?'CONFIRMED':'IGNORED')) throw new Error(decision?.error || t('progress.memoryStaleCandidate'));
    } else if(action==='confirm') await api.post('/memory/'+m.id+'/confirm');
    else await api.del('/memory/'+m.id);
    toast('✓',action==='confirm'?t('progress.memoryConfirmed'):t('progress.memoryIgnored'));setParams({});await reload();
  });
  const remove=(m:ProjectMemory)=>perform(async()=>{
    await api.del('/memory/'+m.id);toast('✓',t('progress.memoryDeactivated', { id: m.id }));await reload();
  });
  const correct=()=>perform(async()=>{
    if(!correcting || !correctText.trim())return;
    await api.post('/memory/'+correcting.id+'/correct',{content:correctText.trim(),...(correcting.revision!==undefined?{expected_revision:correcting.revision}:{})});
    setCorrecting(null);setCorrectText('');toast('✓',t('progress.memoryCorrectionSaved'));await reload();
  });
  const extract=()=>perform(async()=>{
    const result=await api.post<{candidates:ProjectMemory[]}>('/memory/import-dialog',{text:pasteText.trim()});
    toast('✓',t('progress.memoryFoundCandidates', { count: result.candidates.length }));await reload();
  });
  const timeline=items.filter(m=>m.status!=='CANDIDATE');
  const reviewOnly=params.get('type')==='bank-review';
  const filteredTimeline=reviewOnly ? timeline.filter(m=>m.mtype==='BankReviewFeedback') : timeline;
  const candidates=items.filter(m=>m.status==='CANDIDATE' && !m.meta?.project_id);
  const candidateKey=params.get('candidate');
  const candidate=candidates.find(m=>String(m.id)===candidateKey);
  const renderDetails=(m:MemoryEntry)=><>
      {m.mtype==='TaskCompletion' && <p className="hint">{t('progress.memoryProgressScope') + ' '}{completionSource(m)}</p>}
      {m.mtype==='BankReviewFeedback' && <p className="hint">{t('progress.memoryBankFeedbackScope') + ' '}<Link to="/bank?review=1">{t('progress.memoryReviewQueue')}</Link></p>}
      {m.meta?.goal_id && <div><p className="hint">{t('progress.memoryObservationScope')}</p>{m.eligible_for_personalization!==true && <p className="hint">{t('progress.memoryObservationStale')}</p>}<Link to={m.meta.source_task_id?`/learning/task/${m.meta.source_task_id}`:'/learning'}>{t('progress.memoryReviewGoal', { id: m.meta.goal_id })}</Link></div>}
      {m.meta?.project_id && <div>
        {m.eligible_for_personalization!==true && <p className="hint">{m.ineligible_reason || t('progress.memorySourceStale')}{t('progress.memoryStaleEnding')}</p>}
        <p className="hint"><Link to={'/assets/project/'+m.meta.project_id}>{t('progress.memoryProject', { id: m.meta.project_id })}</Link>{m.meta.analysis_id?t('progress.memoryAnalysis', { id: m.meta.analysis_id }):''}{m.meta.source_revision && <>{t('progress.memoryCodeRevision')}<code>{m.meta.source_revision}</code></>}</p>
        <p className="hint">{t('progress.memoryProjectContext')}</p>
        {!!m.meta.citations?.length && <SourceCitations citations={m.meta.citations}/>}
      </div>}
      {m.superseded_by && <p className="hint">{t('progress.memorySuperseded', { id: m.superseded_by })}</p>}
      {(m.status==='ACTIVE' || m.status==='CANDIDATE') && <div className="mem-actions">
        {m.status==='ACTIVE' && <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>{setCorrecting(m);setCorrectText(m.content);setError('');}}>{t('progress.memoryCorrect')}</button>}
        <button className="btn btn-ghost btn-sm" disabled={busy} onClick={()=>remove(m)}>{m.status==='CANDIDATE'?t('progress.memoryDismiss'):t('progress.memoryDelete')}</button>
      </div>}
  </>;
  if(candidateKey) return <div className="memory-page">
    <PageHead title={t('progress.memoryCandidateHeading')} sub={t('progress.memoryCandidateSubtitle')}><Link to="/memory" className="btn btn-outline">{t('progress.memoryBack')}</Link></PageHead>
    {loading && <p role="status">{t('progress.memoryCandidateLoading')}</p>}
    {(loadError || error) && <p role="alert">{loadError || error}</p>}
    {!loading && !loadError && !candidate && <p>{t('progress.memoryCandidateMissing')}</p>}
    {candidate && <>
      <section className="card memory-panel"><div className="hint">{t('progress.memorySource')}</div><h2>{sourceName(candidate)}</h2>
        {completionSource(candidate)}
        {candidate.meta?.exam_id && !candidate.meta?.exam_kind && <Link to={`/exam/diagnosis/report?id=${candidate.meta.exam_id}`}>{t('progress.memoryViewAssessment', { id: candidate.meta.exam_id })}</Link>}
        {candidate.meta?.completed_at && <p>{t('progress.memoryCompletedOn', { date: new Date(candidate.meta.completed_at).toLocaleString(getLocale()) })}</p>}
        {candidate.meta?.submission_id && <p>{t('progress.memorySubmission', { id: candidate.meta.submission_id })}{candidate.meta.scoring_run_id && t('progress.memoryScoringRun', { id: candidate.meta.scoring_run_id })}</p>}
        {candidate.source_ref && <p className="hint">{t('progress.memorySourceRecord')}{candidate.source_ref}</p>}
      </section>
      <section className="card memory-panel"><h2>{t('progress.memoryCandidateContent')}</h2><p style={{whiteSpace:'pre-wrap'}}>{candidate.content}</p>
        <p className="hint">{candidate.mtype==='TaskCompletion'?t('progress.memoryProgressOnly'):t('progress.memoryConfirmScope')}</p>
        <div className="mem-actions"><button className="btn btn-primary" disabled={busy || (candidate.freshness!=null && candidate.freshness!=='CURRENT')} onClick={()=>review(candidate,'confirm')}>{t('progress.memoryConfirmAndReturn')}</button><button className="btn btn-outline" disabled={busy} onClick={()=>review(candidate,'ignore')}>{t('progress.memoryDismissAndReturn')}</button></div>
      </section>
    </>}
  </div>;
  return <div className="memory-page">
    <PageHead title={t('progress.memoryHeading')} sub={t('progress.memorySubtitle')}><button className="btn btn-outline" disabled={busy || loading} onClick={reload}>{t('progress.memoryRefresh')}</button></PageHead>
    {loading && <p role="status">{t('progress.memoryLoading')}</p>}
    {loadError && <div role="alert" className="demo-banner demo-banner-err">{t('progress.memoryLoadError', { error: loadError })}</div>}
    {error && <div role="alert" className="demo-banner demo-banner-err">{t('progress.memoryActionError')}{error}</div>}
    {!loading && !loadError && <section className="card memory-panel memory-pending" aria-label={t('progress.memoryCandidates')}>
      <div className="hint">{t('progress.memoryPendingCount', { count: candidates.length })}</div>
      {candidates.map(m=><article key={m.id}><h2>{m.content}</h2><p className="hint">{t('progress.memoryPendingSource', { source: sourceName(m) })}</p><Link className="btn btn-primary btn-sm" to={`?candidate=${m.id}`}>{t('progress.memoryReviewCandidate')}</Link></article>)}
      {!candidates.length && <><h2>{t('progress.memoryNoCandidates')}</h2><p className="hint">{t('progress.memoryNoCandidatesDescription')}</p><Link className="btn btn-outline btn-sm" to="/learning">{t('progress.memoryViewPlan')}</Link></>}
    </section>}
    <div className="memory-sections">
      <MemoryTimeline
        items={filteredTimeline}
        title={t('progress.memoryHistory')}
        summary={t('progress.memorySavedCount', { count: filteredTimeline.filter(m => m.status==='ACTIVE').length })}
        details={renderDetails}
        filters={<label className="field"><span>{t('progress.memoryType')}</span><select className="inp" value={reviewOnly?'bank-review':''} onChange={event=>{
          const next=new URLSearchParams(params);
          if(event.target.value) next.set('type',event.target.value);
          else next.delete('type');
          setParams(next,{replace:true});
        }}><option value="">{t('progress.memoryAllTypes')}</option><option value="bank-review">{t('progress.memoryBankFeedback')}</option></select></label>}
        empty={!loading && !loadError && <div className="memory-panel">
          <h2>{reviewOnly?t('progress.memoryNoBankFeedback'):t('progress.memoryNoConfirmed')}</h2>
          {reviewOnly ? <Link to="/bank?review=1">{t('progress.memoryPendingQuestions')}</Link> : <p className="hint">{t('progress.memoryNoConfirmedDescription')}</p>}
        </div>}
      />
      <div style={{display:'flex',flexDirection:'column',gap:18}}>
        <section className="card" style={{padding:18}}><h3>{t('progress.memoryExtractHeading')}</h3><textarea className="textarea" aria-label={t('progress.memoryConversationLabel')} rows={5} placeholder={t('progress.memoryExtractPlaceholder')} value={pasteText} onChange={e=>setPasteText(e.target.value)}/><button className="btn btn-outline" style={{marginTop:10}} disabled={busy || !pasteText.trim()} onClick={extract}>{busy?t('progress.memoryExtracting'):t('progress.memoryExtract')}</button><p className="hint">{t('progress.memoryExtractScope')}</p></section>
        <ProjectMemoryCandidates onReviewed={reload}/>
      </div>
    </div>
    {correcting && <Modal title={t('progress.memoryCorrectTitle', { id: correcting.id })} onClose={()=>{if(!busy)setCorrecting(null);}} footer={<><button className="btn btn-outline" disabled={busy} onClick={()=>setCorrecting(null)}>{t('progress.cancel')}</button><button className="btn btn-primary" disabled={busy || !correctText.trim()} onClick={correct}>{busy?t('progress.memorySaving'):t('progress.memorySaveCorrection')}</button></>}>
      <p className="hint">{t('progress.memorySaveCorrectionScope')}</p>
      <label className="field"><span>{t('progress.memoryOriginal')}</span><p style={{whiteSpace:'pre-wrap'}}>{correcting.content}</p></label>
      <label className="field"><span>{t('progress.memoryCorrection')}</span><textarea className="textarea" rows={6} aria-label={t('progress.memoryCorrectionLabel')} value={correctText} onChange={e=>setCorrectText(e.target.value)}/></label>
      {error && <p role="alert">{t('progress.memoryCorrectionError', { error })}</p>}
    </Modal>}
  </div>;
}
