import {useT} from '../i18n';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { PageHead } from '../components/ui';
import { api, ApiError } from '../api/client';
import { messageOf, PROFILE_FIELDS, ProjectProfile } from '../lib/questionWorkshop';
import ProjectAnalysisPanel, { type ProjectReportTab } from '../components/ProjectAnalysisPanel';
import ProjectResume from '../components/ProjectResume';
import ProjectDossier from '../components/ProjectDossier';
import ProjectScanButton from '../components/ProjectScanButton';
import {AutopilotRun,AutopilotSummary,ProjectReportResponse,PROJECT_PROFILE_COPY} from '../lib/projectIntelligence';

const REPORT_TABS = {
  overview: ['projects.projectDossier', '', 'projects.dossierTabDescription'],
  process: ['projects.processTab', 'projects.processTabTitle', 'projects.processTabDescription'],
  evidence: ['projects.evidenceTab', 'projects.evidenceTabTitle', 'projects.evidenceTabDescription'],
  improvements: ['projects.strengthenProject', 'projects.improvementsTabTitle', 'projects.improvementsTabDescription'],
  resume: ['projects.resumeMaterial', 'projects.resumeTabTitle', 'projects.resumeTabDescription'],
} as const;

interface ProjectLite { id: number; name: string; owner: string; repo: string; branch?:string; description?: string | null; status?: string; kind?:string; profile?:ProjectProfile;autopilot?:AutopilotSummary;archived_at?:string|null }

type ProjectList =
  | { status: 'loading' }
  | { status: 'ready'; items: ProjectLite[] }
  | { status: 'error'; message: string };

export default function ProjectReview() {
  const t = useT();
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const pid = Number(id);
  const [loadedProject, setProject] = useState<ProjectLite | null>(null);
  const [projectList, setProjectList] = useState<ProjectList>({ status: 'loading' });
  const [listAttempt, setListAttempt] = useState(0);
  const [failure, setFailure] = useState<({ message: string } | { localeKey: string }) & { denied: boolean }>();
  const [loading, setLoading] = useState(true);
  const [observedRun, setObservedRun] = useState<{ projectId: number; run: AutopilotRun | null } | null>(null);
  const loadVersion = useRef(0);
  const validId = Number.isSafeInteger(pid) && pid > 0;
  const projects = projectList.status === 'ready' ? projectList.items : [];
  const listedProject = projects.find(item => item.id === pid);
  const reportProject = loadedProject?.id === pid ? loadedProject : null;
  const project = failure?.denied ? null
    : reportProject?.archived_at ? reportProject : listedProject ?? reportProject;
  const err = project || !failure ? null : 'localeKey' in failure ? t(failure.localeKey) : failure.message;
  const autopilot = project?.autopilot;
  const analysis = autopilot && observedRun?.projectId === pid
    ? { ...autopilot, latest_run: observedRun.run } : autopilot;
  const syncRun = useCallback((run: AutopilotRun | null) => {
    setObservedRun({ projectId: pid, run });
  }, [pid]);
  const params = new URLSearchParams(location.search);
  const rawTab = params.get('tab');
  const tab: ProjectReportTab = rawTab && Object.prototype.hasOwnProperty.call(REPORT_TABS, rawTab)
    ? rawTab as ProjectReportTab
    : location.hash === '#project-memory-candidates' ? 'improvements' : params.get('history') === '1' ? 'process' : 'overview';
  const [tabLabelKey, tabTitleKey, tabDescriptionKey] = REPORT_TABS[tab];

  const reload = useCallback(() => {
    const version = ++loadVersion.current;
    setProject(null);
    setFailure(undefined);
    setObservedRun(null);
    if (!validId) {
      setFailure({ localeKey: 'projects.invalidProjectId', denied: true });
      setLoading(false);
      return;
    }
    setLoading(true);
    api.get<ProjectReportResponse>(`/projects/${pid}/report`)
      .then(saved => {
        if (version !== loadVersion.current) return;
        if (saved.project_id !== pid || saved.project?.id !== pid) {
          setFailure({ localeKey: 'projects.projectNotInWorkspace', denied: false });
          return;
        }
        setProject(saved.project);
      })
      .catch(error => {
        if (version !== loadVersion.current) return;
        const denied = error instanceof ApiError && [401, 403, 404].includes(error.status);
        setFailure({ message: messageOf(error), denied });
      })
      .finally(() => { if (version === loadVersion.current) setLoading(false); });
  }, [pid, validId]);

  useEffect(() => {
    reload();
    return () => { loadVersion.current += 1; };
  }, [reload]);

  useEffect(() => {
    if (!validId) return;
    let active = true;
    setProjectList({ status: 'loading' });
    api.get<{ items: ProjectLite[] }>('/projects')
      .then(({ items }) => { if (active) setProjectList({ status: 'ready', items }); })
      .catch(error => { if (active) setProjectList({ status: 'error', message: messageOf(error) }); });
    return () => { active = false; };
  }, [pid, validId, listAttempt]);

  return (
    <div className="project-review">
      <div className="project-review-crumb"><Link to="/assets">{t('projects.projectsAndResume')}</Link><span>/</span><span>{project?.name || t('projects.projectReport')}</span>{tab !== 'overview' && <><span>/</span><span>{t(tabLabelKey)}</span></>}</div>
      <PageHead
        title={project ? tabTitleKey ? t(tabTitleKey) : project.name : t('projects.projectReport')}
        sub={project ? t(tabDescriptionKey) : err ? t('projects.projectDetailsLoadFailed') : t('projects.loadingProjectDetails')}
      >
        {project && <label className="project-review-picker">{t('projects.currentProject')} <select className="inp" value={project.id} disabled={projectList.status !== 'ready'} onChange={event => navigate(`/assets/project/${event.target.value}?tab=${tab}`)}>
          {(project.archived_at || !listedProject) && <option value={project.id}>{project.archived_at ? t('projects.removedProjectName', {name: project.name}) : project.name}</option>}
          {projects.filter(item => !project.archived_at || item.id !== project.id).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>}

        <Link className="btn btn-outline" to="/assets">{t('projects.backToProjectsArrow')}</Link>
      </PageHead>
      {project && projectList.status === 'loading' && <p role="status" className="hint">{t('projects.loadingProjectList')}</p>}
      {validId && projectList.status === 'error' && <div className="demo-banner demo-banner-err" role="status">
        <span>{t('projects.projectListError', {error: projectList.message})}</span>
        <button className="btn btn-outline btn-sm" onClick={() => setListAttempt(attempt => attempt + 1)}>{t('projects.retryProjectList')}</button>
      </div>}
      {!project && loading && <div className="hint" style={{ marginBottom: 14 }}>{t('projects.loadingProjectReview')}</div>}
      {err && (
        <div className="card" role="alert" style={{ padding: '28px 20px', marginBottom: 16 }}>
          <b>{t('projects.projectReviewUnavailable')}</b>
          <p className="hint" style={{ marginTop: 6 }}>{err}</p>
          <button className="btn btn-outline btn-sm" style={{ marginTop: 10 }} onClick={reload}>{t('projects.retry')}</button>
        </div>
      )}
      {project && <>
        {!project.archived_at && project.kind !== 'manual' && <div className="dossier-scan-bar">
          <div><strong>{t('projects.codeScan')}</strong><small>{analysis?.latest_valid_report?.source_revision ? t('projects.reportRevisionShort', {revision: analysis.latest_valid_report.source_revision.slice(0,12)}) : t('projects.scanReadCurrentCommit')}</small></div>
          <div className="dossier-scan-actions"><span title={t('projects.reuseStudyRouting')}>{t('projects.autoModelRouting')}</span><ProjectScanButton key={project.id} projectId={pid} name={project.name} repository={[project.owner,project.repo].filter(Boolean).join('/')} branch={project.branch} analysis={analysis} onQueued={() => { reload(); setListAttempt(attempt => attempt + 1); }}/></div>
        </div>}
        <nav aria-label={t('projects.reportViews')} className="project-review-tabs">
          {(Object.keys(REPORT_TABS) as ProjectReportTab[]).map(key => <Link key={key} aria-current={key === tab ? 'page' : undefined} className={`btn ${key === tab ? 'btn-primary' : 'btn-outline'}`} to={`?tab=${key}`}>{t(REPORT_TABS[key][0])}</Link>)}
        </nav>
        {tab === 'overview' && !project.archived_at ? <ProjectDossier key={project.id} projectId={project.id} manual={project.kind === 'manual'} analysis={analysis} onRun={syncRun}/> :
          tab !== 'resume' && <ProjectAnalysisPanel key={project.id} projectId={project.id} tab={tab} manual={project.kind==='manual'} readOnly={!!project.archived_at} onRun={syncRun}/>}

        {tab === 'resume' && (project.archived_at ? <p className="demo-banner">{t('projects.archivedResumeNotice')}</p> : <ProjectResume key={project.id} projectId={project.id}/>)}
      </>}
      {tab === 'overview' && project?.kind === 'manual' && listedProject && <section className="card" style={{padding:20}}><h3>{t('projects.selfReportedProjectDetails')}</h3><p className="hint">{t('projects.selfReportedDetailsNotice')}</p>{PROFILE_FIELDS.map(([key])=><div key={key} style={{marginBottom:14}}><strong>{t(PROJECT_PROFILE_COPY[key][0])}</strong><p style={{whiteSpace:'pre-wrap'}}>{project.profile?.[key] || t('projects.notFilled')}</p></div>)}</section>}
    </div>
  );
}
