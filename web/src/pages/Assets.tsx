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
    try{await api.post('/projects',{repo_url:url.trim(),...(name.trim()?{name:name.trim()}:{}),...(branch.trim()?{branch:branch.trim()}: {})});setImportOpen(false);setName('');setUrl('');setBranch('');toast('✓','项目已导入，可以打开项目档案或开始 AI 扫描。');await reload();}
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
    <PageHead title="项目与简历" sub="从项目代码准备面试，把亲自完成的工作整理成简历。">
      <button className="btn btn-outline" onClick={() => setRegistering(true)}>登记个人经历</button>
      <button className="btn btn-primary" onClick={() => {setImportError('');setImportOpen(true);}}>导入仓库</button>
    </PageHead>
    <div className="project-library-tools">
      <nav aria-label="项目资料视图" className="project-library-buttons">
        <button className={resume ? 'btn btn-outline' : 'btn btn-primary'} aria-pressed={!resume} onClick={() => setFilter('view', '')}>项目总览</button>
        <button className={resume ? 'btn btn-primary' : 'btn btn-outline'} aria-pressed={resume} onClick={() => setFilter('view', 'resume')}>简历素材</button>
      </nav>
      {(projects.length > 1 || query) && <input className="inp project-library-search" type="search" aria-label="搜索项目名称或仓库" placeholder="搜索项目名称或仓库" value={query} onChange={e => setFilter('q', e.target.value)} />}
    </div>

    {loading && <p role="status">正在读取项目…</p>}
    {error && <div role="alert">无法读取最新项目状态：{error} <button className="btn btn-outline btn-sm" onClick={() => void reload()}>重试</button></div>}
    {!loading && !error && !matching.length && <div className="card project-library-card">
      <h2>{projects.length ? '没有找到相关项目' : '还没有项目'}</h2>
      {projects.length ? <button className="btn btn-outline" onClick={() => setFilter('q', '')}>清除搜索</button> : <p>可以导入仓库，也可以登记工作经历。</p>}
    </div>}
    <div className="project-library-list">
      {matching.map(p => {
        const manual = p.kind === 'manual';
        const report = p.autopilot?.latest_valid_report;
        const active = p.autopilot?.latest_run;
        const url = '/assets/project/' + p.id;
        const resumeUrl = url + '?tab=resume';
        const summary = manual ? p.profile?.background : report?.summary || p.description;
        const status = manual ? '手工经历' : active && active.status !== 'COMPLETED' ? projectRunLabel(active) : report ? (report.freshness === 'NEEDS_REVIEW' ? '报告待更新' : '已有 AI 报告') : '待扫描';
        return <article className="card project-library-card" key={p.id} aria-labelledby={'project-name-' + p.id}>
          <div className="project-card-top">
            <span aria-hidden="true" className="project-mark"><i/></span>
            <span className={'project-pill' + (report ? ' project-pill-ready' : '')}>{status}</span>
          </div>
          <h2 id={'project-name-' + p.id}><Link to={resume ? resumeUrl : url}>{p.name}</Link></h2>
          <div className="project-library-meta">{manual ? '本人登记的项目经历' : [p.owner, p.repo].filter(Boolean).join('/') || p.repo_url}</div>
          {resume ? <>
            <p>{p.resume_draft_updated_at ? '草稿保存于 ' + new Date(p.resume_draft_updated_at).toLocaleString() : '核对本人职责与贡献，再预览和保存这个项目的简历草稿。'}</p>
            <div className="project-library-buttons">
              <Link className="btn btn-primary" to={p.resume_draft_updated_at ? resumeUrl + '&draft=1' : resumeUrl}>{p.resume_draft_updated_at ? '打开本地草稿' : '核对简历素材'}</Link>
              <Link className="btn btn-outline" to={url}>项目概览</Link>
            </div>
          </> : <>
            {summary && <p className="project-library-summary">{summary}</p>}
            {active && !p.autopilot?.worker?.online && <small className="project-library-status">后台服务离线，进度已保存</small>}
            {report?.source_revision && <div className="project-library-status"><code>{report.source_revision.slice(0,12)}</code></div>}
            <div className="project-library-buttons project-card-actions">
              <button className="btn btn-outline" onClick={() => navigate(url)}>查看项目档案</button>
              {manual ? <button className="btn btn-outline" onClick={() => navigate('/bank/workshop?project=' + p.id)}>编辑经历与准备面试</button>
                : <ProjectScanButton projectId={p.id} name={p.name} repository={[p.owner,p.repo].filter(Boolean).join('/')} branch={p.branch} analysis={p.autopilot} onQueued={() => void reload()}/>}
              {!!p.autopilot?.pending_candidate_count && <Link className="btn btn-ghost" to={url + '#project-memory-candidates'}>待核对记忆（{p.autopilot.pending_candidate_count}）</Link>}
            </div>
            {!manual && <ProjectActions projectId={p.id} run={active || null} allowedActions={p.autopilot?.allowed_actions || []} onChanged={reload} onHistory={() => navigate(url + '?history=1')} onRemove={() => {setRemoveError('');setRemoving(p);}} />}
          </>}
        </article>;
      })}
    </div>
    {!resume && <section className="dossier-resume-strip"><div><h2>简历素材</h2><p>按项目核对本人职责和结果，打开已保存的简历草稿。</p></div><button className="btn btn-outline" onClick={() => setFilter('view','resume')}>查看简历素材</button></section>}
    <details id="project-analysis-settings" className="project-library-more" open={params.get('settings') === '1' || undefined}>
      <summary>源码分析设置与预算</summary>
      <AutopilotSettingsForm onSaved={() => {void reload();}} />
    </details>
    {registering && <Modal title="登记个人项目经历" size="lg" onClose={() => setRegistering(false)}>
      <ProjectContextEditor project={null} onCancel={() => setRegistering(false)} onSaved={async() => {setRegistering(false);setFilter('q','');await reload();}} />
    </Modal>}
    {importOpen && <Modal title="导入仓库（GitHub / GitCode）" onClose={()=>{if(!importing)setImportOpen(false);}} footer={<><button className="btn btn-outline" disabled={importing} onClick={()=>setImportOpen(false)}>取消</button><button className="btn btn-primary" disabled={importing || !url.trim()} onClick={doImport}>{importing?'导入中…':'导入项目'}</button></>}>
      <label className="field"><span>仓库地址</span><input className="inp" aria-label="仓库地址" placeholder="https://github.com/owner/repo 或 https://gitcode.com/owner/repo" value={url} onChange={e=>setUrl(e.target.value)}/></label>
      <details><summary>名称与跟踪分支（可选）</summary><label className="field"><span>项目名称</span><input className="inp" value={name} onChange={e=>setName(e.target.value)} placeholder="留空使用仓库名称"/></label><label className="field"><span>跟踪分支</span><input className="inp" value={branch} onChange={e=>setBranch(e.target.value)} placeholder="留空使用仓库实际默认分支"/></label></details>
      <p className="hint">读取远端已提交的代码。未启用定期扫描时，导入后可按项目启动 AI 扫描。</p>
      {importError && <p role="alert">导入未完成：{importError}</p>}
    </Modal>}
    {removing && <Modal title="移除项目" onClose={()=>{if(!removeBusy)setRemoving(null);}} footer={<><button className="btn btn-outline" disabled={removeBusy} onClick={()=>setRemoving(null)}>保留项目</button><button className="btn btn-primary" disabled={removeBusy} onClick={remove}>移除项目</button></>}><p>将「{removing.name}」移出项目列表，停止自动分析并停用当前项目记忆。历史报告、确认记录和已经创建的题目与答卷会保留。</p>{removeError && <p role="alert">{removeError}</p>}</Modal>}
  </div>;
}
