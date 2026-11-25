import {useT} from '../i18n';
import React, {useEffect, useState} from 'react';
import {Link} from 'react-router-dom';
import {api} from '../api/client';
import {messageOf} from '../lib/questionWorkshop';
import type {AutopilotRun, AutopilotSummary, WorkerStatus, SourceCitation, SourceExcerpt} from '../lib/projectIntelligence';
import SourceCitations from './SourceCitations';
import StudyEntryButton from './StudyEntryButton';
import {ProjectCopyError} from '../lib/projectIntelligence';
import {ProjectRunStatus} from './ProjectAutopilot';

interface Technology { name:string; use:string; limitations:string | string[]; citations:SourceCitation[]; claim_id?:string }
interface Dossier {
  project_id:number;
  latest_run?:AutopilotRun|null;
  worker?:WorkerStatus;
  practice_questions?:{id:number;short_code:string;title:string;set_id:string;set_title:string}[];
  report:{id:number; summary?:string; source_revision:string|null; freshness:string; as_of:string|null} | null;
  technologies_assessed?:boolean;
  technologies:Technology[];
  bank_questions:{id:number;short_code:string;title:string}[];
  suggested_questions:{id:string;title:string;issue_id?:string;citations:SourceCitation[]}[];
  recommendations:{id:string;title:string;detail:string;impact:string;citations:SourceCitation[]}[];
  limitations:string[];
  source_excerpts:SourceExcerpt[];
}

export default function ProjectDossier({projectId,manual,analysis,onRun}: {projectId:number;manual:boolean;analysis?:AutopilotSummary;onRun?:(run:AutopilotRun|null)=>void}) {
  const t = useT();
  const [data,setData] = useState<Dossier>();
  const [failure,setFailure] = useState<{detail: unknown}>();
  const error = failure && (failure.detail instanceof ProjectCopyError ? t(failure.detail.localeKey) : messageOf(failure.detail));
  const [attempt,setAttempt] = useState(0);
  const [expanded,setExpanded] = useState(false);
  const [allTechnology,setAllTechnology] = useState(false);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function read() {
      try {
        const result = await api.get<Dossier>('/projects/' + projectId + '/dossier');
        if (result.project_id !== projectId || !Array.isArray(result.bank_questions)) throw new ProjectCopyError('projects.incompleteDossier');
        if (alive) {
          setData(result); setFailure(undefined);
          if (result.latest_run !== undefined) onRun?.(result.latest_run);
          if (result.latest_run && ['WAITING_WORKER','QUEUED','RETRY_WAIT','RUNNING'].includes(result.latest_run.status)) timer = setTimeout(read, 6000);
        }
      } catch (e) { if (alive) setFailure({detail: e}); }
    }
    void read();
    return () => { alive = false; clearTimeout(timer); };
  }, [projectId, attempt, onRun]);

  const run = data && data.latest_run !== undefined ? data.latest_run : analysis?.latest_run;
  const practice = data?.practice_questions ?? [];
  const report = data?.report;
  const newerRun = run && report && run.id !== report.id;
  const scanning = run && ['WAITING_WORKER','QUEUED','RETRY_WAIT','RUNNING'].includes(run.status);
  const excerpts = data?.source_excerpts || [];
  const cite = (citations: SourceCitation[]) => citations?.length > 0 && <SourceCitations citations={citations} sourceExcerpts={excerpts}/>;
  return <section aria-label={t('projects.projectDossier')} className="dossier">
    {error && <p role="alert">{t('projects.dossierLoadError', {error})} <button className="btn btn-outline btn-sm" onClick={() => setAttempt(x=>x+1)}>{t('projects.retry')}</button></p>}
    {!data && !error && <p role="status">{t('projects.loadingDossier')}</p>}
    {data && <>
      <section className="dossier-lead">
        <div className="dossier-lead-meta"><span className="project-pill">{manual ? t('projects.manualProject') : t('projects.aiScan')}</span>
          {report?.source_revision && <code title={report.source_revision}>{report.source_revision.slice(0,12)}</code>}
          {report && <small>{t(report.freshness === 'NEEDS_REVIEW' ? 'projects.reportNumberNeedsReview' : report.freshness === 'UNKNOWN' ? 'projects.reportNumberFreshnessUnknown' : 'projects.reportNumber', {label: t(newerRun ? 'projects.earlierValidatedReport' : 'projects.report'), id: report.id})}</small>}
        </div>
        <h2>{manual ? t('projects.myProjectExperience') : report ? t('projects.projectInterpretation') : scanning ? t('projects.preparingReport') : t('projects.scanToStart')}</h2>
        <p>{manual ? t('projects.manualDossierIntro') : report?.summary || (report ? t('projects.reportDossierIntro') : t('projects.scanDossierIntro'))}</p>
        <div className="dossier-shortcuts">
          {!manual && <Link to="?tab=process">{t('projects.analysisProcessLink')}</Link>}
          {report && <Link to="?tab=evidence">{t('projects.codeEvidenceLink')}</Link>}

        </div>
      </section>
      {run && run.status !== 'COMPLETED' && <div className="dossier-run"><ProjectRunStatus run={run} worker={data.worker || analysis?.worker} compact/>
        <Link to="?tab=process">{t('projects.viewPartialResults')}</Link></div>}
      <div className="dossier-grid">
        <section className="dossier-card dossier-tech">
          <header><h2>{t('projects.technologiesUsed')}</h2><span className="dossier-count">{data.technologies.length}</span></header>
          {(allTechnology ? data.technologies : data.technologies.slice(0, 2)).map((tech,i)=><article key={tech.name + i}><h3>{tech.name}</h3><p>{tech.use}</p>{tech.limitations?.length > 0 && <p className="dossier-muted">{Array.isArray(tech.limitations) ? tech.limitations.join(t('projects.sentenceListSeparator')) : tech.limitations}</p>}{cite(tech.citations)}</article>)}
          {data.technologies.length>2 && <button className="btn btn-outline btn-sm" onClick={()=>setAllTechnology(!allTechnology)}>{allTechnology?t('projects.collapseTechnologies'):t('projects.showAllTechnologies', {count: data.technologies.length})}</button>}
          {!data.technologies.length && <p>{manual ? t('projects.manualNoSource') : !report ? t('projects.waitingForSourceEvidence') : data.technologies_assessed ? t('projects.noTechnologiesFound') : t('projects.noTechnologySection')}</p>}
          {report && <Link to="?tab=evidence">{t('projects.viewReportEvidence')}</Link>}
        </section>
        <section className="dossier-card dossier-questions">
          <header><h2>{t('projects.projectInterviewQuestions')}</h2><span className="dossier-count">{data.bank_questions.length + practice.length}</span></header>
          {data.bank_questions.length ? <><small>{t('projects.inQuestionBank')}</small>{(expanded ? data.bank_questions : data.bank_questions.slice(0,3)).map(q=><article key={q.id}><small>{q.short_code}</small><h3>{q.title}</h3><StudyEntryButton kind="question" id={q.id}/></article>)}
            {data.bank_questions.length>3 && <button className="btn btn-outline btn-sm" onClick={()=>setExpanded(!expanded)}>{expanded?t('projects.collapse'):t('projects.showAllQuestions', {count: data.bank_questions.length})}</button>}</> : <p>{t('projects.noBankedProjectQuestions')}</p>}
          {practice.length>0 && <details><summary>{t('projects.practiceQuestionCount', {count: practice.length})}</summary>{practice.map(q=><article key={q.id}><h3>{q.title}</h3><Link to={'/bank?set=' + encodeURIComponent(q.set_id)}>{t('projects.openQuestionSet', {name: q.set_title})}</Link></article>)}</details>}
          {data.suggested_questions.length>0 && <details><summary>{t('projects.suggestedQuestionCount', {count: data.suggested_questions.length})}</summary>{data.suggested_questions.map((q,i)=><article key={q.id || i}><h3>{q.title}</h3>{cite(q.citations)}</article>)}</details>}
          <Link to={'/bank/workshop?project='+projectId}>{t('projects.prepareProjectQuestions')}</Link>
        </section>
        <section className="dossier-card dossier-next">
          <header><h2>{t('projects.strengthenProject')}</h2><span className="dossier-count">{data.recommendations.length}</span></header>
          {data.recommendations.slice(0,1).map((r,i)=><article key={r.id || i}><h3>{r.title}</h3><p>{r.detail}</p>{r.impact && <p className="dossier-muted">{r.impact}</p>}{cite(r.citations)}</article>)}
          {!data.recommendations.length && <p>{report ? t('projects.noTraceableSuggestions') : t('projects.suggestionsAfterReport')}</p>}
          {report && <Link to="?tab=improvements">{t('projects.allSuggestionsEvidence')}</Link>}
        </section>
      </div>
      <section className="dossier-resume-strip"><div><h2>{t('projects.resumeMaterial')}</h2><p>{t('projects.resumeMaterialIntro')}</p></div><Link className="btn btn-outline" to="?tab=resume">{t('projects.reviewResumeMaterial')}</Link></section>
      {!!data.limitations.length && <details className="dossier-limits"><summary>{t('projects.analysisScope')}</summary><ul>{data.limitations.map((item,index)=><li key={index}>{item}</li>)}</ul></details>}
    </>}
  </section>;
}
