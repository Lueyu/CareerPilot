import {t as translate, useLocale, useT} from '../i18n';
import React,{useCallback,useEffect,useRef,useState} from 'react';
import {Link,useNavigate,useSearchParams} from 'react-router-dom';
import ProjectScanButton from '../components/ProjectScanButton';
import ProjectContextEditor from '../components/ProjectContextEditor';
import {useAppState} from '../components/AppState';
import {Modal,PageHead} from '../components/ui';
import {api} from '../api/client';
import {AutopilotSummary} from '../lib/projectIntelligence';
import {AutopilotSettingsForm,ProjectActions,projectRunLabel} from '../components/ProjectAutopilot';
import {messageOf} from '../lib/questionWorkshop';

interface ProjectView {profile?:{background?:string};description?:string;resume_draft_updated_at?:string|null;id:number;kind?:string;name:string;repo_url:string;owner:string;repo:string;branch?:string;autopilot?:AutopilotSummary}
export default function Assets(){
  const t = useT();
  const locale = useLocale();
  const navigate=useNavigate();const {toast}=useAppState();
  const [projects,setProjects]=useState<ProjectView[]>([]);const [error,setError]=useState('');const [loading,setLoading]=useState(true);
  const [importOpen,setImportOpen]=useState(false);const [name,setName]=useState('');const [url,setUrl]=useState('');const [branch,setBranch]=useState('');const [importing,setImporting]=useState(false);const [importError,setImportError]=useState('');
  const [removing,setRemoving]=useState<ProjectView|null>(null);const [removeBusy,setRemoveBusy]=useState(false);const [removeError,setRemoveError]=useState('');
  const pending=useRef(false);const version=useRef(0);
  const reload=useCallback(async()=>{const token=++version.current;try{const data=await api.get<{items:ProjectView[]}>('/projects');if(token===version.current){setProjects(data.items);setError('');}}catch(e){if(token===version.current)setError(messageOf(e));}finally{if(token===version.current)setLoading(false);}},[]);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      await reload();
      if (active) timer = setTimeout(poll, 5000);
    }
    void poll();
    return () => { active = false; version.current++; clearTimeout(timer); };
  }, [reload]);
  const doImport=async()=>{
    if(pending.current || !url.trim())return;pending.current=true;setImporting(true);setImportError('');
    try{await api.post('/projects',{repo_url:url.trim(),...(name.trim()?{name:name.trim()}:{}),...(branch.trim()?{branch:branch.trim()}: {})});setImportOpen(false);setName('');setUrl('');setBranch('');toast('✓',translate('projects.projectImported'));await reload();}
    catch(e){setImportError(messageOf(e));}finally{pending.current=false;setImporting(false);}
  };
  const remove=async()=>{if(!removing || removeBusy)return;setRemoveBusy(true);setRemoveError('');try{await api.del('/projects/'+removing.id);setRemoving(null);await reload();}catch(e){setRemoveError(messageOf(e));}finally{setRemoveBusy(false);}};
  const [params, setParams] = useSearchParams();
  const resume = params.get('view') === 'resume';
  const query = params.get('q') || '';
  const [registering, setRegistering] = useState(false);
  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, {replace: key === 'q'});
  }
  const matching = projects.filter(p =>
    [p.name, p.owner, p.repo].some(s => s?.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))
  );

  return <div className="project-library">
    <PageHead title={t('projects.projectsAndResume')} sub={t('projects.libraryIntro')}>
      <button className="btn btn-outline" onClick={() => setRegistering(true)}>{t('projects.registerExperience')}</button>
      <button className="btn btn-primary" onClick={() => {setImportError('');setImportOpen(true);}}>{t('projects.importRepo')}</button>
    </PageHead>
    <div className="project-library-tools">
      <nav aria-label={t('projects.projectLibraryViews')} className="project-library-buttons">
        <button className={resume ? 'btn btn-outline' : 'btn btn-primary'} aria-pressed={!resume} onClick={() => setFilter('view', '')}>{t('projects.projectOverviewTab')}</button>
        <button className={resume ? 'btn btn-primary' : 'btn btn-outline'} aria-pressed={resume} onClick={() => setFilter('view', 'resume')}>{t('projects.resumeMaterial')}</button>
      </nav>
      {(projects.length > 1 || query) && <input className="inp project-library-search" type="search" aria-label={t('projects.searchProjects')} placeholder={t('projects.searchProjects')} value={query} onChange={e => setFilter('q', e.target.value)} />}
    </div>

    {loading && <p role="status">{t('projects.loadingProjects')}</p>}
    {error && <div role="alert">{t('projects.assetsLoadError', {error})} <button className="btn btn-outline btn-sm" onClick={() => void reload()}>{t('projects.retry')}</button></div>}
    {!loading && !error && !matching.length && <div className="card project-library-card">
      <h2>{projects.length ? t('projects.noMatchingProjects') : t('projects.noProjects')}</h2>
      {projects.length ? <button className="btn btn-outline" onClick={() => setFilter('q', '')}>{t('projects.clearSearch')}</button> : <p>{t('projects.addProjectsPrompt')}</p>}
    </div>}
    <div className="project-library-list">
      {matching.map(p => {
        const manual = p.kind === 'manual';
        const report = p.autopilot?.latest_valid_report;
        const active = p.autopilot?.latest_run;
        const url = '/assets/project/' + p.id;
        const resumeUrl = url + '?tab=resume';
        const summary = manual ? p.profile?.background : report?.summary || p.description;
        const status = manual ? t('projects.manualExperience') : active && active.status !== 'COMPLETED' ? projectRunLabel(active) : report ? (report.freshness === 'NEEDS_REVIEW' ? t('projects.reportNeedsRefresh') : t('projects.aiReportAvailable')) : t('projects.awaitingScan');
        return <article className="card project-library-card" key={p.id} aria-labelledby={'project-name-' + p.id}>
          <div className="project-card-top">
            <span aria-hidden="true" className="project-mark"><i/></span>
            <span className={'project-pill' + (report ? ' project-pill-ready' : '')}>{status}</span>
          </div>
          <h2 id={'project-name-' + p.id}><Link to={resume ? resumeUrl : url}>{p.name}</Link></h2>
          <div className="project-library-meta">{manual ? t('projects.recordedProjectExperience') : [p.owner, p.repo].filter(Boolean).join('/') || p.repo_url}</div>
          {resume ? <>
            <p>{p.resume_draft_updated_at ? t('projects.draftLastSaved', {date: new Date(p.resume_draft_updated_at).toLocaleString(locale)}) : t('projects.reviewThenDraft')}</p>
            <div className="project-library-buttons">
              <Link className="btn btn-primary" to={p.resume_draft_updated_at ? resumeUrl + '&draft=1' : resumeUrl}>{p.resume_draft_updated_at ? t('projects.openLocalDraft') : t('projects.reviewResumeMaterial')}</Link>
              <Link className="btn btn-outline" to={url}>{t('projects.projectOverview')}</Link>
            </div>
          </> : <>
            {summary && <p className="project-library-summary">{summary}</p>}
            {active && !p.autopilot?.worker?.online && <small className="project-library-status">{t('projects.workerOffline')}</small>}
            {report?.source_revision && <div className="project-library-status"><code>{report.source_revision.slice(0,12)}</code></div>}
            <div className="project-library-buttons project-card-actions">
              <button className="btn btn-outline" onClick={() => navigate(url)}>{t('projects.viewDossier')}</button>
              {manual ? <button className="btn btn-outline" onClick={() => navigate('/bank/workshop?project=' + p.id)}>{t('projects.editExperienceInterviewPrep')}</button>
                : <ProjectScanButton projectId={p.id} name={p.name} repository={[p.owner,p.repo].filter(Boolean).join('/')} branch={p.branch} analysis={p.autopilot} onQueued={() => void reload()}/>}
              {!!p.autopilot?.pending_candidate_count && <Link className="btn btn-ghost" to={url + '#project-memory-candidates'}>{t('projects.memoryCandidatesCount', {count: p.autopilot.pending_candidate_count})}</Link>}
            </div>
            {!manual && <ProjectActions projectId={p.id} run={active || null} allowedActions={p.autopilot?.allowed_actions || []} onChanged={reload} onHistory={() => navigate(url + '?history=1')} onRemove={() => {setRemoveError('');setRemoving(p);}} />}
          </>}
        </article>;
      })}
    </div>
    {!resume && <section className="dossier-resume-strip"><div><h2>{t('projects.resumeMaterial')}</h2><p>{t('projects.resumeLibraryIntro')}</p></div><button className="btn btn-outline" onClick={() => setFilter('view','resume')}>{t('projects.viewResumeMaterial')}</button></section>}
    <details id="project-analysis-settings" className="project-library-more" open={params.get('settings') === '1' || undefined}>
      <summary>{t('projects.sourceSettingsAndBudget')}</summary>
      <AutopilotSettingsForm onSaved={() => {void reload();}} />
    </details>
    {registering && <Modal title={t('projects.registerPersonalProject')} size="lg" onClose={() => setRegistering(false)}>
      <ProjectContextEditor project={null} onCancel={() => setRegistering(false)} onSaved={async() => {setRegistering(false);setFilter('q','');await reload();}} />
    </Modal>}
    {importOpen && <Modal title={t('projects.importRepoModal')} onClose={()=>{if(!importing)setImportOpen(false);}} footer={<><button className="btn btn-outline" disabled={importing} onClick={()=>setImportOpen(false)}>{t('projects.cancel')}</button><button className="btn btn-primary" disabled={importing || !url.trim()} onClick={doImport}>{importing?t('projects.importingProject'):t('projects.importProject')}</button></>}>
      <label className="field"><span>{t('projects.repositoryUrl')}</span><input className="inp" aria-label={t('projects.repositoryUrl')} placeholder={t('projects.repositoryUrlPlaceholder')} value={url} onChange={e=>setUrl(e.target.value)}/></label>
      <details><summary>{t('projects.optionalImportDetails')}</summary><label className="field"><span>{t('projects.projectName')}</span><input className="inp" value={name} onChange={e=>setName(e.target.value)} placeholder={t('projects.defaultRepoName')}/></label><label className="field"><span>{t('projects.trackedBranch')}</span><input className="inp" value={branch} onChange={e=>setBranch(e.target.value)} placeholder={t('projects.defaultRepoBranch')}/></label></details>
      <p className="hint">{t('projects.importReadingScope')}</p>
      {importError && <p role="alert">{t('projects.projectImportError', {error: importError})}</p>}
    </Modal>}
    {removing && <Modal title={t('projects.removeProject')} onClose={()=>{if(!removeBusy)setRemoving(null);}} footer={<><button className="btn btn-outline" disabled={removeBusy} onClick={()=>setRemoving(null)}>{t('projects.keepProject')}</button><button className="btn btn-primary" disabled={removeBusy} onClick={remove}>{t('projects.removeProject')}</button></>}><p>{t('projects.removeNamedProject', {name: removing.name})}</p>{removeError && <p role="alert">{removeError}</p>}</Modal>}
  </div>;
}
