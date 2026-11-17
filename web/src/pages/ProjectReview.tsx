import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAppState } from '../components/AppState';
import { PageHead } from '../components/ui';
import { api } from '../api/client';
import {PROFILE_FIELDS, ProjectProfile} from '../lib/questionWorkshop';
import ProjectAnalysisPanel from '../components/ProjectAnalysisPanel';
import {ScanCoverage,ProjectReportResponse} from '../lib/projectIntelligence';

const LEVEL_CLS: Record<string, string> = {
  VERIFIED: 'ev-verified',
  HIGH: 'ev-claim',
  UNVERIFIED_CLAIM: 'ev-claim',
};
const LEVEL_TEXT: Record<string, string> = {
  VERIFIED: '内容关键词匹配', HIGH: '路径匹配', UNVERIFIED_CLAIM: 'README 声明',
};
const LEVEL_ICON: Record<string, string> = { VERIFIED: '📄', HIGH: '📁', UNVERIFIED_CLAIM: '📝' };

interface FindingView {
  id: number; domain: string; level: string; title: string; detail: string;
  file_path: string | null; evidence_ref: string | null;
}
interface ProjectLite { id: number; name: string; owner: string; repo: string; description?: string | null; last_scanned_at?: string | null; status?: string; kind?:string; profile?:ProjectProfile;archived_at?:string|null }
interface FindingsData { items: FindingView[]; coverage?: ScanCoverage; stats: Record<string, unknown> }

export default function ProjectReview() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { toast } = useAppState();
  const pid = Number(id);
  const [loadedProject, setProject] = useState<ProjectLite | null>(null);
  const [loadedData, setData] = useState<{projectId: number; value: FindingsData} | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [findingsErr,setFindingsErr]=useState<string|null>(null);
  const [loading, setLoading] = useState(true);
  const [findingsLoading,setFindingsLoading]=useState(true);
  const [running, setRunning] = useState(false);
  const loadVersion=useRef(0);
  const project=loadedProject?.id===pid?loadedProject:null;
  const data=loadedData?.projectId===pid?loadedData.value:null;

  const reload = useCallback(() => {
    const version=++loadVersion.current;
    setProject(null);setData(null);setErr(null);setFindingsErr(null);
    if (!Number.isInteger(pid) || pid<=0) { setErr('无效的项目 id'); setLoading(false);setFindingsLoading(false); return; }
    setLoading(true);setFindingsLoading(true);
    api.get<{items:ProjectLite[]}>('/projects')
      .then(async pl=>{
        if(version!==loadVersion.current)return;
        const found=pl.items.find(p=>p.id===pid);
        if(found)setProject(found);
        else {
          const saved=await api.get<ProjectReportResponse>(`/projects/${pid}/report`);
          if(version!==loadVersion.current)return;
          if(saved.project?.id===pid && saved.project.archived_at)setProject(saved.project);
          else setErr('项目不存在或不属于当前工作空间');
        }
      })
      .catch(e=>{if(version===loadVersion.current)setErr(e.message || String(e));})
      .finally(()=>{if(version===loadVersion.current)setLoading(false);});
    api.get<FindingsData>(`/projects/${pid}/findings`)
      .then(fd=>{if(version===loadVersion.current)setData({projectId:pid,value:fd});})
      .catch(e=>{if(version===loadVersion.current)setFindingsErr(e.message || String(e));})
      .finally(()=>{if(version===loadVersion.current)setFindingsLoading(false);});
  }, [pid]);

  useEffect(() => { reload();return()=>{loadVersion.current+=1;}; }, [reload]);

  const runUpdate = async () => {
    if(!project)return;
    setRunning(true);
    try {
      const r = await api.post<{ short_code: string; risk_level: string }>(`/career-assets/updates/run?project_id=${pid}`);
      toast('⚙', `已生成内容草稿（${r.short_code}，风险 ${r.risk_level}）`);
      navigate('/assets/pipeline');
    } catch (e: any) {
      toast('⚠️', `生成失败：${e.message || e}`);
    } finally {
      setRunning(false);
    }
  };

  const items = project?(data?.items ?? []):[];
  const verified = items.filter((f) => f.level === 'VERIFIED').length;
  const high = items.filter((f) => f.level === 'HIGH').length;
  const claim = items.filter((f) => f.level === 'UNVERIFIED_CLAIM').length;

  return (
    <div>
      <PageHead
        title={`${project ? `${project.name} · 项目报告` : '项目报告'}`}
        sub={project?.kind==='manual'?'手工登记的个人经历 · 不需要仓库分析':project ? `${project.owner}/${project.repo} · 自动分析远端已提交的项目代码`:err?'项目资料加载失败':'正在读取项目资料'}
      >
        <button className="btn btn-outline" onClick={() => navigate('/assets')}>← 返回项目资产</button>
        {project && !project.archived_at && <details><summary>个人经历与职业展示</summary><p className="hint">职责可之后填写，不影响仓库分析。</p><button className="btn btn-outline btn-sm" onClick={()=>navigate(`/bank/workshop?project=${project.id}`)}>编辑经历与准备面试</button><button className="btn btn-outline btn-sm" onClick={runUpdate} disabled={running || loading || findingsLoading || !project || !!findingsErr || items.length === 0}>{running ? '生成中…' : '更新职业展示草稿'}</button></details>}
      </PageHead>
      {loading && <div className="hint" style={{ marginBottom: 14 }}>正在从后端加载审查数据…</div>}
      {err && (
        <div className="card" style={{ padding: '28px 20px', marginBottom: 16 }}>
          <b>⚠️ 无法加载项目审查</b>
          <p className="hint" style={{ marginTop: 6 }}>{err}</p>
          <button className="btn btn-outline btn-sm" style={{ marginTop: 10 }} onClick={reload}>重试</button>
        </div>
      )}
      {project && <ProjectAnalysisPanel key={project.id} projectId={project.id} manual={project.kind==='manual'} readOnly={!!project.archived_at}/>}
      {project && !project.archived_at && findingsLoading && <p className="hint">正在读取历史结构扫描…</p>}
      {project && !project.archived_at && findingsErr && <div role="alert" className="demo-banner demo-banner-err" style={{marginBottom:18}}>结构扫描暂不可用：{findingsErr}。项目资料和 AI 深度分析仍可使用。<button className="btn btn-outline btn-sm" onClick={reload}>重试读取</button></div>}
      {!loading && !err && project?.kind==='manual' && <section className="card" style={{padding:20}}><h3>本人填写的项目资料</h3><p className="hint">这些资料用于面经改写，不代表已经测评掌握。</p>{PROFILE_FIELDS.map(([key,label])=><div key={key} style={{marginBottom:14}}><strong>{label}</strong><p style={{whiteSpace:'pre-wrap'}}>{project.profile?.[key] || '尚未填写'}</p></div>)}</section>}
      {items.length > 0 && (
        <details className="card" style={{padding:18,marginBottom:18}}>
          <summary style={{cursor:'pointer',fontWeight:600}}>结构扫描与历史文件盘点（{items.length}项）</summary>
          <div className="card" style={{ marginTop:16,marginBottom:18 }}>
            <div className="card-head">
              <span className="card-title">结构扫描覆盖</span>
              <span className="card-sub">仅文件与关键词盘点，不评价代码质量或个人掌握</span>
            </div>
            <div className="grid grid-4" style={{margin:'14px 0'}}>
              <div className="mini-stat"><b>{items.length}</b><span>扫描发现</span></div>
              <div className="mini-stat"><b>{verified}</b><span>内容关键词匹配</span></div>
              <div className="mini-stat"><b>{high}</b><span>仅路径匹配</span></div>
              <div className="mini-stat"><b>{claim}</b><span>仅 README 声明</span></div>
            </div>
            <p className="hint">文件总数：{data?.coverage?.files_total ?? String(data?.stats?.files ?? '未知')} · 实际读取：{data?.coverage?.files_read ?? '历史记录未记录'} · 读取失败：{data?.coverage?.files_failed ?? '未知'}</p>
            {data?.coverage?.tree_truncated && <p className="hint">文件树被截断，扫描覆盖不完整。</p>}
            {data?.coverage?.history_unknown && <p className="hint">历史扫描缺少读取范围记录，可重新结构扫描获取覆盖信息。</p>}
            {data?.coverage?.limitations?.map((line,i)=><p className="hint" key={i}>{line}</p>)}
            {!!data?.coverage?.domains?.length && <ul>{data.coverage.domains.filter(d=>d.path_matches || d.content_matches || d.readme_claims).map(d=><li key={d.key}>{d.key}：路径 {d.path_matches} 条 · 内容关键词 {d.content_matches} 条 · README 声明 {d.readme_claims} 条</li>)}</ul>}
          </div>

          <div className="card">
            <div className="card-head">
              <span className="card-title">结构扫描发现</span>
              <span className="card-sub">关键词命中不验证实现正确性；深入问题见上方 AI 分析</span>
            </div>
            {items.map((f) => (
              <div className="list-row" key={f.id} style={{ alignItems: 'flex-start' }}>
                <div className="list-ic">{LEVEL_ICON[f.level] || '·'}</div>
                <div className="list-main">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span className={`ev-chip ${LEVEL_CLS[f.level] || ''}`}>{LEVEL_TEXT[f.level] || f.level}</span>
                    <span className="chip chip-slate">{f.domain}</span>
                    <b style={{ fontSize: 13.5 }}>{f.title}</b>
                  </div>
                  <div className="list-sub" style={{ marginTop: 4 }}>{f.detail}</div>
                  <div className="q-meta">
                    <span className="chip chip-slate">📄 {f.file_path || '-'}</span>
                    <span className="chip chip-slate">{LEVEL_TEXT[f.level] || f.level}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
