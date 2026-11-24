import {t, useLocale, useT} from '../i18n';
import React,{useCallback,useEffect,useRef,useState} from 'react';
import ProjectModelTrace from './ProjectModelTrace';
import {Link,useLocation,useNavigate} from 'react-router-dom';
import {api} from '../api/client';
import {messageOf} from '../lib/questionWorkshop';
import {issueWorkshopPath,ProjectAnalysisRun,ProjectReportResponse,AutopilotReport} from '../lib/projectIntelligence';
import {ProjectActions,ProjectRunStatus,projectRunStatusLabel} from './ProjectAutopilot';
import ProjectMemoryCandidates from './ProjectMemoryCandidates';
import SourceCitations from './SourceCitations';
import {Modal} from './ui';
export {default as SourceCitations} from './SourceCitations';

const toolbar:React.CSSProperties={display:'flex',gap:10,flexWrap:'wrap',alignItems:'center'};
const ACTIVE_STATUSES=['WAITING_WORKER','QUEUED','RUNNING','RETRY_WAIT'];
const MAX_POLL_READS=120;
const GENERATION_STATUS_KEYS: Partial<Record<string, string>> = {
  READY: 'projects.generationReady', RUNNING: 'projects.generationRunning', FAILED: 'projects.analysisRunFailed',
  REVIEW: 'projects.generationReview', COMPLETE: 'projects.stepFinish',
};
const FACT_LABELS:Record<string,string>={CODE_OBSERVED:'projects.codeObservation',INFERRED:'projects.inferredFact',DOC_CLAIM:'projects.documentedClaim',UNASSESSED:'projects.unassessedFact'};
const factLabel=(kind?:string)=>t((kind && FACT_LABELS[kind]) || 'projects.unknownFactSource');
const hasCompleteReport=(run:ProjectAnalysisRun)=>run.report_status==='VALID' && run.coverage_status==='COMPLETE';
const hasReadableHistory=(run:ProjectAnalysisRun)=>hasCompleteReport(run) || (run.status==='SUCCESS' && !!run.report && run.coverage_status!=='PARTIAL');
export type ProjectReportTab = 'overview' | 'process' | 'evidence' | 'improvements' | 'resume';

export default function ProjectAnalysisPanel({projectId,tab='process',manual=false,readOnly=false,onRun}: {projectId:number;tab?:ProjectReportTab;manual?:boolean;readOnly?:boolean;onRun?:(run:ProjectReportResponse['latest_run'])=>void}) {
  const t = useT();
  const locale = useLocale();
  const navigate=useNavigate();const location=useLocation();
  const [data,setData]=useState<ProjectReportResponse|null>(null);const [loading,setLoading]=useState(true);const [error,setError]=useState('');
  const [history,setHistory]=useState<ProjectAnalysisRun[]>([]);const [historyOpen,setHistoryOpen]=useState(false);const [historyError,setHistoryError]=useState<{detail: string} | {key: string}>();const [historyBusy,setHistoryBusy]=useState(false);
  const [historicalReport,setHistoricalReport]=useState<AutopilotReport|null>(null);const [selected,setSelected]=useState<string[]>([]);const [removeOpen,setRemoveOpen]=useState(false);const [removeBusy,setRemoveBusy]=useState(false);const [removeError,setRemoveError]=useState('');
  const [pollEpoch,setPollEpoch]=useState(0);const [pollPaused,setPollPaused]=useState(false);
  const request=useRef(0);const historyRequest=useRef(0);
  const inFlight=useRef<{projectId:number;promise:Promise<ProjectReportResponse|undefined>}|null>(null);
  const load=useCallback(()=>{
    if(inFlight.current?.projectId===projectId)return inFlight.current.promise;
    const token=++request.current;setLoading(true);
    const promise:Promise<ProjectReportResponse|undefined>=api.get<ProjectReportResponse>('/projects/'+projectId+'/report')
      .then(value=>{if(token===request.current){setData(value);onRun?.(value.latest_run);setError('');}return value;})
      .catch(e=>{if(token===request.current)setError(messageOf(e));return undefined;})
      .finally(()=>{if(token===request.current)setLoading(false);if(inFlight.current?.promise===promise)inFlight.current=null;});
    inFlight.current={projectId,promise};return promise;
  },[projectId,onRun]);
  useEffect(()=>{setData(null);setHistoricalReport(null);setSelected([]);setError('');setLoading(!manual);return()=>{request.current++;historyRequest.current++;inFlight.current=null;};},[projectId,manual]);
  useEffect(()=>{
    if(manual)return;
    let cancelled=false,reads=0,timer:ReturnType<typeof setTimeout>|undefined;
    setPollPaused(false);
    const poll=async()=>{
      reads++;const value=await load();if(cancelled)return;
      if(value && (!value.latest_run || !ACTIVE_STATUSES.includes(value.latest_run.status)))return;
      if(reads>=MAX_POLL_READS){setPollPaused(true);return;}
      timer=setTimeout(()=>{void poll();},5000);
    };
    void poll();return()=>{cancelled=true;if(timer)clearTimeout(timer);};
  },[load,manual,pollEpoch]);
  const refresh=()=>{setPollEpoch(v=>v+1);};
  const loadHistory=useCallback(async()=>{setHistoryOpen(true);setHistoryBusy(true);setHistoryError(undefined);const token=++historyRequest.current;try{const result=await api.get<{items:ProjectAnalysisRun[]}>('/projects/'+projectId+'/analyses');if(token===historyRequest.current)setHistory(result.items);}catch(e){if(token===historyRequest.current)setHistoryError({detail: messageOf(e)});}finally{if(token===historyRequest.current)setHistoryBusy(false);}},[projectId]);
  useEffect(()=>{if(new URLSearchParams(location.search).get('history')==='1' && !manual)void loadHistory();},[loadHistory,location.search,manual]);
  const showHistory=async(id:number)=>{const token=++historyRequest.current;setHistoryBusy(true);setHistoryError(undefined);try{const result=await api.get<ProjectReportResponse>('/projects/'+projectId+'/report?analysis_id='+id);if(token===historyRequest.current){if(result.report && result.report.coverage_status!=='PARTIAL'){setHistoricalReport(result.report);setSelected([]);}else setHistoryError({key: 'projects.noReadableHistory'});}}catch(e){if(token===historyRequest.current)setHistoryError({detail: messageOf(e)});}finally{if(token===historyRequest.current)setHistoryBusy(false);}};
  const visible=historicalReport || data?.report;const latest=data?.latest_run;
  const locked=readOnly || !!data?.project?.archived_at;
  const reportMatchesLatest=!!latest && !!data?.report && (data.report.run_id===latest.id || latest.report_id===data.report.id);
  const historical=!!visible && (!!historicalReport || (!!latest && !reportMatchesLatest));
  const canPrepare=!locked && !historicalReport && !!visible && visible.is_latest_valid && visible.coverage_status!=='PARTIAL' && visible.freshness==='CURRENT';
  const firstClaim=visible?.report.claims?.find(claim=>claim.kind==='CODE_OBSERVED' && claim.citations.length);
  const citedArchitecture=visible?.report.architecture.find(item=>item.citations.length && item.fact_type==='CODE_OBSERVED')
    || visible?.report.architecture.find(item=>item.citations.length);
  const firstLocation=(firstClaim || citedArchitecture)?.citations[0];
  const selectedActive=selected.filter(id=>visible?.active_issue_ids?.includes(id));
  useEffect(()=>{
    if(typeof document==='undefined')return;
    if((tab==='evidence' && location.hash.startsWith('#report-claim-')) || (tab==='improvements' && location.hash==='#project-memory-candidates')) {
      document.getElementById(location.hash.slice(1))?.scrollIntoView?.();
    }
  },[tab,location.hash,visible]);
  const remove=async()=>{setRemoveBusy(true);setRemoveError('');try{await api.del('/projects/'+projectId);navigate('/assets');}catch(e){setRemoveError(messageOf(e));}finally{setRemoveBusy(false);}};
  if(tab==='resume')return null;
  if(manual)return <section className="project-report-card"><h2>{t('projects.projectInterviewPrep')}</h2>{locked?<p>{t('projects.archivedManual')}</p>:<><p>{t('projects.manualProjectPrep')}</p><div className="project-report-actions"><Link className="btn btn-outline" to="?tab=resume">{t('projects.reviewResumeMaterial')}</Link><Link className="btn btn-outline" to={'/bank/workshop?project='+projectId}>{t('projects.prepareFollowups')}</Link></div></>}</section>;
  return <section className="project-report-stack" aria-label={t('projects.deepAnalysis')}>
    {locked && <p className="demo-banner">{t('projects.archivedAnalysis')}</p>}
    {tab==='overview' && <>
      <article className="project-report-card project-report-feature">
        <p className="project-report-label">{t('projects.thisAnalysis')}</p>
        <h2>{visible?.report.summary || (loading ? t('projects.loadingReport') : t('projects.noCompleteReport'))}</h2>
        <p>{t('projects.processOverview')}</p>
        <div className="project-report-actions"><Link className="btn btn-primary" to="?tab=process">{t('projects.viewProcess')}</Link>{!visible && !locked && <Link className="btn btn-outline" to="/assets?settings=1#project-analysis-settings">{t('projects.adjustScanSettings')}</Link>}</div>
      </article>
      <article className="project-report-card">
        <p className="project-report-label">{t('projects.codeFindings')}</p>
        <h2>{firstClaim?.title || citedArchitecture?.title || (visible ? t('projects.verifyReportSources') : t('projects.evidenceAfterComplete'))}</h2>
        {firstLocation && <p>{t('projects.evidenceLinks')}　<code>{firstLocation.path}:{firstLocation.start_line}–{firstLocation.end_line}</code></p>}
        <p>{t('projects.codePersonalBoundary')}</p>
        <div className="project-report-actions"><Link className="btn btn-outline" to="?tab=evidence">{t('projects.expandEvidence')}</Link><Link className="btn btn-outline" to="?tab=improvements">{t('projects.viewImprovementAdvice')}</Link></div>
      </article>
      <article className="project-report-card">
        <p className="project-report-label">{t('projects.interviewPrep')}</p><h2>{t('projects.explainTradeoffs')}</h2>
        <Link className="btn btn-outline" to="?tab=resume">{t('projects.reviewResumeMaterial')}</Link>
      </article>
      {latest && latest.status!=='COMPLETED' && <div className="project-report-card"><ProjectRunStatus run={latest} compact/></div>}
      {data?.partial_preview && <p className="hint">{t('projects.partialResultsSaved')}<Link to="?tab=process">{t('projects.viewProcess')}</Link></p>}
    </>}
    {tab==='process' && <>
      <div className="project-report-steps">
        <article><strong>{t('projects.readSource')}</strong><p>{t('projects.readSourceDescription')}</p></article>
        <article><strong>{t('projects.crossCheck')}</strong><p>{t('projects.crossCheckDescription')}</p></article>
        <article><strong>{t('projects.reportAndCandidates')}</strong><p>{t('projects.reportAndCandidatesDescription')}</p></article>
      </div>
      <div className="project-report-card">
      <div style={{...toolbar,justifyContent:'space-between'}}><h2>{latest ? t('projects.currentJobNumber', {id: latest.id}) : t('projects.thisRun')}</h2><button className="btn btn-outline btn-sm" disabled={loading} onClick={refresh}>{t('projects.refreshStatus')}</button></div>
      <p className="hint">{t('projects.remoteAnalysisScope')}</p>
      <p><strong>{t('projects.projectIssueBoundary')}</strong> {t('projects.verifyPersonalMastery')}</p>
      {loading && <p role="status">{t('projects.loadingJobAndReport')}</p>}
      {pollPaused && <p role="status" className="hint">{t('projects.pollingPaused')}</p>}
      {data && <ProjectRunStatus run={latest || null} worker={data.worker}/>}
      {latest && reportMatchesLatest && data?.report?.coverage_status==='COMPLETE' && latest.status!=='COMPLETED' && <p role="status" className="hint">{t('projects.reportReadyCandidatePending')}</p>}
      {!loading && !error && !latest && !visible && <><p>{t('projects.noScans')}</p>{!locked && <Link className="btn btn-primary" to="/assets?settings=1#project-analysis-settings">{t('projects.adjustScanSettings')}</Link>}</>}
      {data && (locked?<button className="btn btn-outline btn-sm" onClick={()=>void loadHistory()}>{t('projects.analysisHistory')}</button>:<ProjectActions projectId={projectId} run={latest || null} allowedActions={data.allowed_actions || []} onChanged={refresh} onHistory={()=>void loadHistory()} onRemove={()=>setRemoveOpen(true)}/>)}
      {latest && <ProjectModelTrace key={latest.id} run={latest}/>}
      {historyOpen && <div style={{marginTop:16}}><h3>{t('projects.analysisHistory')}</h3>{historyBusy && <p role="status">{t('projects.loadingHistory')}</p>}{historyError && <p role="alert">{'key' in historyError ? t(historyError.key) : historyError.detail}</p>}<div style={toolbar}>{history.map(run=><button key={run.id} className="btn btn-outline btn-sm" disabled={historyBusy || !hasReadableHistory(run)} onClick={()=>showHistory(run.id)}>{t(hasCompleteReport(run) ? 'projects.completeHistoryRow' : 'projects.historyRow', {id: run.id, status: projectRunStatusLabel(run.status) || ({SUCCESS:t('projects.legacyReportReady'),FAILED:t('projects.incompleteAnalysis'),READY:t('projects.pendingAnalysis')} as Record<string,string>)[run.status] || run.status})}</button>)}</div>{!historyBusy && !history.length && <p>{t('projects.noHistory')}</p>}</div>}
      </div>
    {data?.partial_preview && latest?.coverage_status==='PARTIAL' && <details className="card" style={{padding:18,marginBottom:18}}><summary>{t('projects.savedPartial')}</summary><p className="hint">{t('projects.partialLimitations')}</p>{data.partial_preview.summary && <p>{data.partial_preview.summary}</p>}{data.partial_preview.modules?.map((m,i)=><article key={i}><h4>{m.title || m.name || t('projects.moduleFallback')}</h4><p>{m.summary}</p></article>)}</details>}
    </>}
    {error && <p role="alert" className="demo-banner demo-banner-err">{t(visible ? 'projects.latestStatusErrorWithReport' : 'projects.latestStatusError', {error})} <button className="btn btn-outline btn-sm" disabled={loading} onClick={refresh}>{t('projects.retryRead')}</button></p>}
    {visible?.report && <div className={tab==='overview' ? 'project-report-version' : 'project-report-card'}>
      <h3>{t('projects.analysisReportNumber', {label: t(historical ? 'projects.legacyReport' : 'projects.latestValidReport'), id: visible.id})}</h3>
      {historical && <p className="hint">{t(visible.is_latest_valid ? 'projects.previousReportNotice' : 'projects.supersededReport')}</p>}
      {historicalReport && <button className="btn btn-ghost" onClick={()=>{setHistoricalReport(null);setSelected([]);}}>{t('projects.backToLatestReport')}</button>}
      {visible.coverage_status==='PARTIAL' && <p role="alert">{t('projects.partialReportNotice')}</p>}
      {visible.freshness!=='CURRENT' && <p role="alert">{visible.freshness==='UNKNOWN'?t('projects.reportFreshnessUnknown'):t('projects.reportNeedsUpdate')}</p>}
      <p className="hint">{t('projects.reportTimeNotice', {date: visible.as_of ? new Date(visible.as_of).toLocaleString(locale) : t('projects.notRecorded')})}</p>
      {tab==='process' && <>
        <p className="project-report-label">{t('projects.sourceLimits')}</p><h2>{t('projects.scopeDescription')}</h2>
        <p className="project-report-prose">{visible.report.summary}</p>
        <p>{t('projects.reportRevision')}<code>{visible.source_revision || t('projects.notRecorded')}</code></p>
        {visible.coverage ? <><p>{t(visible.coverage.tree_truncated ? 'projects.truncatedFileReadCoverage' : 'projects.fileReadCoverage', {read: visible.coverage.files_read ?? t('projects.unknownValue'), eligible: visible.coverage.eligible_files ?? t('projects.unknownValue')})}</p>{visible.coverage.limitations?.map((line,index)=><p key={index}>{line}</p>)}{(['unread_files','failed_files','truncated_files'] as const).map((key,index)=> {
          const files = visible.coverage?.[key] || [];
          return files.length > 0 && <details key={key}><summary>{t('projects.fileCoverageCount', {label: t(['projects.unreadFiles','projects.failedReads','projects.truncatedReads'][index]), count: files.length})}</summary><ul>{files.map(path=><li key={path}><code>{path}</code></li>)}</ul></details>;
        })}</> : <p>{t('projects.noCoverageRecorded')}</p>}
        {visible.report.sections?.map(section=><article key={section.key}><h3>{section.title}</h3><p className="project-report-prose">{section.body}</p>{visible.report.claims?.filter(claim=>section.claim_ids?.includes(claim.id)).map(claim=><p key={claim.id}><Link to={`?tab=evidence#report-claim-${claim.id}`}>{claim.title}</Link></p>)}</article>)}
        <div className="project-report-actions"><Link className="btn btn-outline" to="?tab=evidence">{t('projects.viewFindings')}</Link><Link className="btn btn-outline" to="?tab=overview">{t('projects.backToProject')}</Link></div>
      </>}
      {tab==='evidence' && <>
        <div className="project-report-steps">
          <article><strong>{t('projects.codeLocations')}</strong><p>{firstLocation ? <code>{firstLocation.path}:{firstLocation.start_line}–{firstLocation.end_line}</code> : t('projects.inspectLocations')}</p></article>
          <article><strong>{t('projects.codeObservation')}</strong><p>{firstClaim?.title || t('projects.checkObservations')}</p></article>
          <article><strong>{t('projects.personalVerification')}</strong><p>{t('projects.verificationBoundary')}</p></article>
        </div>
        {visible.report.claims?.map(claim=><article key={claim.id} id={`report-claim-${claim.id}`} className="project-report-entry"><span className="chip chip-slate">{factLabel(claim.kind)}</span><h3>{claim.title}</h3><p className="project-report-prose">{claim.text}</p>{claim.citations.length ? <SourceCitations citations={claim.citations} sourceExcerpts={visible.source_excerpts}/> : <p className="hint">{t('projects.noCitations')}</p>}</article>)}
        {!!visible.report.architecture.length && <details open={!visible.report.claims?.length}><summary>{t('projects.architecture')}</summary>{visible.report.architecture.map((item,index)=><article key={index}><h4>{item.title}</h4><span className="chip chip-slate">{factLabel(item.fact_type)}</span><p className="project-report-prose">{item.detail}</p><SourceCitations citations={item.citations} sourceExcerpts={visible.source_excerpts}/></article>)}</details>}
        {!visible.report.claims?.length && !visible.report.architecture.length && <p>{t('projects.noStandaloneFindings')}</p>}
        <div className="project-report-actions"><Link className="btn btn-outline" to="?tab=improvements">{t('projects.viewEngineeringAdvice')}</Link><Link className="btn btn-outline" to="?tab=overview">{t('projects.backToReport')}</Link></div>
      </>}
      {tab==='improvements' && <>
      <h3>{t('projects.issuesAndRisks')}</h3>
      <p><strong>{t('projects.projectIssueBoundary')}</strong> {t('projects.verifyPersonalMastery')}</p>
      {!visible.report.issues.length && <p>{t('projects.noSpecificIssues')}</p>}
      {visible.report.issues.map(issue=><article key={issue.id} style={{borderTop:'1px solid var(--border)',padding:'14px 0'}}>
        <div style={toolbar}><label style={{display:'flex',gap:8,alignItems:'center'}}><input type="checkbox" aria-label={t('projects.selectIssue', {id: issue.id})} checked={selected.includes(issue.id)} disabled={!canPrepare || !visible.active_issue_ids?.includes(issue.id)} onChange={e=>setSelected(old=>e.target.checked?[...old,issue.id]:old.filter(id=>id!==issue.id))}/><strong>{issue.title}</strong></label><span className="chip chip-slate">{factLabel(visible.report.claims?.find(claim=>claim.id===issue.claim_id)?.kind)}</span><span className="chip chip-amber">{{high:t('projects.priorityHigh'),medium:t('projects.priorityMedium'),low:t('projects.priorityLow')}[issue.severity]}</span></div>
        {!visible.active_issue_ids?.includes(issue.id) && <p className="hint">{t('projects.unconfirmedIssueMemory')}</p>}
        {visible.issue_memories?.filter(m=>m.issue_id===issue.id && m.user_corrected).map(m=><div key={m.memory_id} className="demo-banner"><b>{t('projects.correctedMemoryNumber', {id: m.memory_id})}</b><p style={{whiteSpace:'pre-wrap'}}>{m.content}</p><p className="hint">{t('projects.confirmedIssueMemory')}</p></div>)}
        <div className="project-report-card project-report-feature"><p className="project-report-label">{t('projects.implementationAdvice')}</p><h2>{issue.recommendation || t('projects.noImplementationSteps')}</h2><p className="project-report-prose">{issue.detail}</p><p><b>{t('projects.issueImpact')}</b>{issue.impact}</p><SourceCitations citations={issue.citations} sourceExcerpts={visible.source_excerpts}/></div>
        {issue.claim_id && visible.report.claims?.some(claim=>claim.id===issue.claim_id) && <p><Link to={`?tab=evidence#report-claim-${issue.claim_id}`}>{t('projects.viewTriggeringObservation')}</Link></p>}
        {!!issue.interview_questions.length && <details><summary>{t('projects.suggestedFollowups')}</summary><ul>{issue.interview_questions.map((q,i)=><li key={i}>{q}</li>)}</ul></details>}
      </article>)}
      {!!visible.report.issues.length && <><button className="btn btn-primary" disabled={!canPrepare || !selectedActive.length} onClick={()=>navigate(issueWorkshopPath(projectId,visible.id,selectedActive))}>{t('projects.prepareAssessment')}</button><p className="hint">{t('projects.assessmentScope')}</p></>}
      <div className="project-report-actions"><Link className="btn btn-outline" to="?tab=resume">{t('projects.reviewResumeMaterial')}</Link><Link className="btn btn-outline" to="?tab=evidence">{t('projects.reviewCodeEvidence')}</Link></div>
      {!!visible.generations?.length && <div><h3>{t('projects.linkedPractice')}</h3>{visible.generations.map(batch=><div key={batch.id} style={{padding:'10px 0',borderTop:'1px solid var(--border)'}}><Link to={'/bank/workshop?task='+batch.id}>{t('projects.generationTaskNumber', {id: batch.id})}</Link><span className="hint">{t('projects.generationBankedQuestions', {status: locale === 'en' ? t(GENERATION_STATUS_KEYS[batch.status] ?? 'projects.statusUnknown', {status: batch.status}) : batch.status, count: batch.question_ids.length})}</span>{batch.assessments.map(exam=><p key={exam.id}><Link to={exam.status==='COMPLETED'?'/exam/diagnosis/report?id='+exam.id:'/exam/diagnosis?id='+exam.id}>{t('projects.linkedAssessment', {id: exam.id, action: t(exam.status === 'COMPLETED' ? 'projects.viewScoreReport' : 'projects.continueAssessment')})}</Link>{exam.status==='COMPLETED' && exam.score!=null && <span>{t('projects.assessmentScore', {score: exam.score})}</span>}</p>)}</div>)}</div>}
      </>}
      {(tab==='process' || tab==='evidence') && !!visible.report.unknowns.length && <details><summary>{t('projects.uncertainties')}</summary><ul>{visible.report.unknowns.map((line,i)=><li key={i}>{line}</li>)}</ul></details>}
      {tab==='process' && visible.report.changes && <details><summary>{t('projects.changesFromPrevious')}</summary>{Array.isArray(visible.report.changes)?<ul>{visible.report.changes.map((c,i)=><li key={i}>{c}</li>)}</ul>:<p>{visible.report.changes}</p>}</details>}
    </div>}
    {(tab==='evidence' || tab==='improvements') && !visible && !error && <div className="project-report-card">
      <p role={loading ? 'status' : undefined}>{loading ? t('projects.loadingReport') : t('projects.noValidatedReport')}</p>
      <div className="project-report-actions"><Link className="btn btn-outline" to="?tab=process">{t('projects.viewRunSettings')}</Link>{!locked && <Link className="btn btn-primary" to="/assets?settings=1#project-analysis-settings">{t('projects.adjustScanSettings')}</Link>}</div>
    </div>}
    {!locked && tab==='improvements' && <ProjectMemoryCandidates projectId={projectId} refreshKey={`${data?.report?.id || 0}:${latest?.id || 0}:${latest?.status || ''}:${pollEpoch}`} onReviewed={refresh}/>}
    {removeOpen && <Modal title={t('projects.removeProject')} onClose={()=>{if(!removeBusy)setRemoveOpen(false);}} footer={<><button className="btn btn-outline" disabled={removeBusy} onClick={()=>setRemoveOpen(false)}>{t('projects.keepProject')}</button><button className="btn btn-primary" disabled={removeBusy} onClick={remove}>{t('projects.removeProject')}</button></>}><p>{t('projects.removeProjectDescription')}</p>{removeError && <p role="alert">{removeError}</p>}</Modal>}
  </section>;
}
