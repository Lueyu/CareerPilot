import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router-dom';
import { PageHead } from '../components/ui';
import { api, ApiError } from '../api/client';
import { messageOf, PROFILE_FIELDS, ProjectProfile } from '../lib/questionWorkshop';
import ProjectAnalysisPanel, { type ProjectReportTab } from '../components/ProjectAnalysisPanel';
import ProjectResume from '../components/ProjectResume';
import ProjectDossier from '../components/ProjectDossier';
import ProjectScanButton from '../components/ProjectScanButton';
import {AutopilotRun,AutopilotSummary,ProjectReportResponse} from '../lib/projectIntelligence';

const REPORT_TABS = {
  overview: ['项目档案', '', '技术实践、关联面试题与下一步补强。'],
  process: ['分析过程', 'AI 怎么分析这个项目？', '查看本次任务的输入、输出，以及哪些部分还需要核实。'],
  evidence: ['结论依据', '结论的依据在哪里？', '区分代码观察、文档声明和待核实推断。'],
  improvements: ['竞争力补强', '下一步可以怎么补强？', '从报告里的观察出发，核对改进建议和可用于面试的追问。'],
  resume: ['简历素材', '项目经历怎么写进简历？', '确认本人职责与实际成果，再生成待检查的简历草稿。'],
} as const;

interface ProjectLite { id: number; name: string; owner: string; repo: string; branch?:string; description?: string | null; status?: string; kind?:string; profile?:ProjectProfile;autopilot?:AutopilotSummary;archived_at?:string|null }

type ProjectList =
  | { status: 'loading' }
  | { status: 'ready'; items: ProjectLite[] }
  | { status: 'error'; message: string };

export default function ProjectReview() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const pid = Number(id);
  const [loadedProject, setProject] = useState<ProjectLite | null>(null);
  const [projectList, setProjectList] = useState<ProjectList>({ status: 'loading' });
  const [listAttempt, setListAttempt] = useState(0);
  const [failure, setFailure] = useState<{ message: string; denied: boolean } | null>(null);
  const [loading, setLoading] = useState(true);
  const [observedRun, setObservedRun] = useState<{ projectId: number; run: AutopilotRun | null } | null>(null);
  const loadVersion = useRef(0);
  const validId = Number.isSafeInteger(pid) && pid > 0;
  const projects = projectList.status === 'ready' ? projectList.items : [];
  const listedProject = projects.find(item => item.id === pid);
  const reportProject = loadedProject?.id === pid ? loadedProject : null;
  const project = failure?.denied ? null
    : reportProject?.archived_at ? reportProject : listedProject ?? reportProject;
  const err = project ? null : failure?.message;
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
  const [tabLabel, tabTitle, tabDescription] = REPORT_TABS[tab];

  const reload = useCallback(() => {
    const version = ++loadVersion.current;
    setProject(null);
    setFailure(null);
    setObservedRun(null);
    if (!validId) {
      setFailure({ message: '无效的项目 id', denied: true });
      setLoading(false);
      return;
    }
    setLoading(true);
    api.get<ProjectReportResponse>(`/projects/${pid}/report`)
      .then(saved => {
        if (version !== loadVersion.current) return;
        if (saved.project_id !== pid || saved.project?.id !== pid) {
          setFailure({ message: '项目不存在或不属于当前工作空间', denied: false });
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
      <div className="project-review-crumb"><Link to="/assets">项目与简历</Link><span>/</span><span>{project?.name || '项目报告'}</span>{tab !== 'overview' && <><span>/</span><span>{tabLabel}</span></>}</div>
      <PageHead
        title={project ? tabTitle || project.name : '项目报告'}
        sub={project ? tabDescription : err ? '项目资料加载失败' : '正在读取项目资料'}
      >
        {project && <label className="project-review-picker">当前项目 <select className="inp" value={project.id} disabled={projectList.status !== 'ready'} onChange={event => navigate(`/assets/project/${event.target.value}?tab=${tab}`)}>
          {(project.archived_at || !listedProject) && <option value={project.id}>{project.name}{project.archived_at ? '（已移除）' : ''}</option>}
          {projects.filter(item => !project.archived_at || item.id !== project.id).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select></label>}

        <Link className="btn btn-outline" to="/assets">← 返回项目列表</Link>
      </PageHead>
      {project && projectList.status === 'loading' && <p role="status" className="hint">正在加载项目列表…</p>}
      {validId && projectList.status === 'error' && <div className="demo-banner demo-banner-err" role="status">
        <span>项目列表加载失败：{projectList.message}</span>
        <button className="btn btn-outline btn-sm" onClick={() => setListAttempt(attempt => attempt + 1)}>重试加载项目列表</button>
      </div>}
      {!project && loading && <div className="hint" style={{ marginBottom: 14 }}>正在从后端加载审查数据…</div>}
      {err && (
        <div className="card" role="alert" style={{ padding: '28px 20px', marginBottom: 16 }}>
          <b>⚠️ 无法加载项目审查</b>
          <p className="hint" style={{ marginTop: 6 }}>{err}</p>
          <button className="btn btn-outline btn-sm" style={{ marginTop: 10 }} onClick={reload}>重试</button>
        </div>
      )}
      {project && <>
        {!project.archived_at && project.kind !== 'manual' && <div className="dossier-scan-bar">
          <div><strong>代码扫描</strong><small>{analysis?.latest_valid_report?.source_revision ? '报告版本 '+analysis.latest_valid_report.source_revision.slice(0,12) : '从当前仓库的提交读取源码'}</small></div>
          <div className="dossier-scan-actions"><span title="沿用学习聊天的模型档位">Auto · 模型路由</span><ProjectScanButton key={project.id} projectId={pid} name={project.name} repository={[project.owner,project.repo].filter(Boolean).join('/')} branch={project.branch} analysis={analysis} onQueued={() => { reload(); setListAttempt(attempt => attempt + 1); }}/></div>
        </div>}
        <nav aria-label="项目报告视图" className="project-review-tabs">
          {(Object.keys(REPORT_TABS) as ProjectReportTab[]).map(key => <Link key={key} aria-current={key === tab ? 'page' : undefined} className={`btn ${key === tab ? 'btn-primary' : 'btn-outline'}`} to={`?tab=${key}`}>{REPORT_TABS[key][0]}</Link>)}
        </nav>
        {tab === 'overview' && !project.archived_at ? <ProjectDossier key={project.id} projectId={project.id} manual={project.kind === 'manual'} analysis={analysis} onRun={syncRun}/> :
          tab !== 'resume' && <ProjectAnalysisPanel key={project.id} projectId={project.id} tab={tab} manual={project.kind==='manual'} readOnly={!!project.archived_at} onRun={syncRun}/>}

        {tab === 'resume' && (project.archived_at ? <p className="demo-banner">项目已移除，不能生成新的简历草稿。</p> : <ProjectResume key={project.id} projectId={project.id}/>)}
      </>}
      {tab === 'overview' && project?.kind === 'manual' && listedProject && <section className="card" style={{padding:20}}><h3>本人填写的项目资料</h3><p className="hint">这些资料用于面经改写，不代表已经测评掌握。</p>{PROFILE_FIELDS.map(([key,label])=><div key={key} style={{marginBottom:14}}><strong>{label}</strong><p style={{whiteSpace:'pre-wrap'}}>{project.profile?.[key] || '尚未填写'}</p></div>)}</section>}
    </div>
  );
}
