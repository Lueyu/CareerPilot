import React,{useCallback,useEffect,useRef,useState} from 'react';
import {Link,useLocation,useNavigate} from 'react-router-dom';
import {api} from '../api/client';
import {messageOf} from '../lib/questionWorkshop';
import {issueWorkshopPath,ProjectAnalysisRun,ProjectReportResponse,AutopilotReport} from '../lib/projectIntelligence';
import {ProjectActions,ProjectRunStatus,RUN_LABELS} from './ProjectAutopilot';
import ProjectMemoryCandidates from './ProjectMemoryCandidates';
import SourceCitations from './SourceCitations';
import {Modal} from './ui';
export {default as SourceCitations} from './SourceCitations';

const toolbar:React.CSSProperties={display:'flex',gap:10,flexWrap:'wrap',alignItems:'center'};
const ACTIVE_STATUSES=['QUEUED','RUNNING','RETRY_WAIT'];
const MAX_POLL_READS=120;
const FACT_LABELS:Record<string,string>={CODE_OBSERVED:'代码观察',INFERRED:'待验证推断',DOC_CLAIM:'文档声明',UNASSESSED:'尚未判断'};
const factLabel=(kind?:string)=>kind && FACT_LABELS[kind] || '来源类型未确认';
const hasCompleteReport=(run:ProjectAnalysisRun)=>run.report_status==='VALID' && run.coverage_status==='COMPLETE';
const hasReadableHistory=(run:ProjectAnalysisRun)=>hasCompleteReport(run) || (run.status==='SUCCESS' && !!run.report && run.coverage_status!=='PARTIAL');
export default function ProjectAnalysisPanel({projectId,manual=false,readOnly=false}: {projectId:number;manual?:boolean;readOnly?:boolean}) {
  const navigate=useNavigate();const location=useLocation();
  const [data,setData]=useState<ProjectReportResponse|null>(null);const [loading,setLoading]=useState(true);const [error,setError]=useState('');
  const [history,setHistory]=useState<ProjectAnalysisRun[]>([]);const [historyOpen,setHistoryOpen]=useState(false);const [historyError,setHistoryError]=useState('');const [historyBusy,setHistoryBusy]=useState(false);
  const [historicalReport,setHistoricalReport]=useState<AutopilotReport|null>(null);const [selected,setSelected]=useState<string[]>([]);const [removeOpen,setRemoveOpen]=useState(false);const [removeBusy,setRemoveBusy]=useState(false);const [removeError,setRemoveError]=useState('');
  const [pollEpoch,setPollEpoch]=useState(0);const [pollPaused,setPollPaused]=useState(false);
  const request=useRef(0);const historyRequest=useRef(0);
  const inFlight=useRef<{projectId:number;promise:Promise<ProjectReportResponse|undefined>}|null>(null);
  const load=useCallback(()=>{
    if(inFlight.current?.projectId===projectId)return inFlight.current.promise;
    const token=++request.current;setLoading(true);
    const promise:Promise<ProjectReportResponse|undefined>=api.get<ProjectReportResponse>('/projects/'+projectId+'/report')
      .then(value=>{if(token===request.current){setData(value);setError('');}return value;})
      .catch(e=>{if(token===request.current)setError(messageOf(e));return undefined;})
      .finally(()=>{if(token===request.current)setLoading(false);if(inFlight.current?.promise===promise)inFlight.current=null;});
    inFlight.current={projectId,promise};return promise;
  },[projectId]);
  useEffect(()=>{setData(null);setHistoricalReport(null);setSelected([]);setError('');setLoading(!manual);return()=>{request.current++;historyRequest.current++;};},[projectId,manual]);
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
  const loadHistory=useCallback(async()=>{setHistoryOpen(true);setHistoryBusy(true);setHistoryError('');const token=++historyRequest.current;try{const result=await api.get<{items:ProjectAnalysisRun[]}>('/projects/'+projectId+'/analyses');if(token===historyRequest.current)setHistory(result.items);}catch(e){if(token===historyRequest.current)setHistoryError(messageOf(e));}finally{if(token===historyRequest.current)setHistoryBusy(false);}},[projectId]);
  useEffect(()=>{if(new URLSearchParams(location.search).get('history')==='1' && !manual)void loadHistory();},[loadHistory,location.search,manual]);
  const showHistory=async(id:number)=>{const token=++historyRequest.current;setHistoryBusy(true);setHistoryError('');try{const result=await api.get<ProjectReportResponse>('/projects/'+projectId+'/report?analysis_id='+id);if(token===historyRequest.current){if(result.report && result.report.coverage_status!=='PARTIAL'){setHistoricalReport(result.report);setSelected([]);}else setHistoryError('这次任务没有可查看的完整报告。');}}catch(e){if(token===historyRequest.current)setHistoryError(messageOf(e));}finally{if(token===historyRequest.current)setHistoryBusy(false);}};
  const visible=historicalReport || data?.report;const latest=data?.latest_run;
  const locked=readOnly || !!data?.project?.archived_at;
  const historical=!!visible && (visible.id!==latest?.id || !!historicalReport);
  const canPrepare=!locked && !!visible && visible.is_latest_valid && visible.coverage_status!=='PARTIAL' && visible.freshness==='CURRENT';
  const selectedActive=selected.filter(id=>visible?.active_issue_ids?.includes(id));
  const remove=async()=>{setRemoveBusy(true);setRemoveError('');try{await api.del('/projects/'+projectId);navigate('/assets');}catch(e){setRemoveError(messageOf(e));}finally{setRemoveBusy(false);}};
  if(manual)return <section className="card" style={{padding:20}}><h3>项目资料与面试准备</h3>{locked?<p>项目已移除，历史资料仅供只读查看。</p>:<><p>手工项目没有可读取的仓库。完善本人职责与技术取舍后，可在补题工作台准备项目追问。</p><Link to={'/bank/workshop?project='+projectId}>完善资料并准备面试 →</Link></>}</section>;
  return <section aria-label="项目 AI 深度分析">
    <div className="card" style={{padding:20,marginBottom:18}}>
      <div style={{...toolbar,justifyContent:'space-between'}}><h2 style={{margin:0}}>项目报告</h2><button className="btn btn-outline btn-sm" disabled={loading} onClick={refresh}>刷新状态</button></div>
      {locked && <p className="demo-banner">项目已移除，历史报告仅供只读查看。自动分析和记忆确认已停用，原有题目与答卷快照保留。</p>}
      <p className="hint">自动分析远端已提交的代码，不包含本机未推送改动。个人职责可稍后补充；仓库事实不代表本人贡献。</p>
      <p><strong>项目问题不代表个人能力短板。</strong> 个人掌握情况需要通过新的作答和评分确认。</p>
      {loading && <p role="status">正在读取后台任务与报告…</p>}
      {error && <p role="alert">无法读取最新状态：{error}。下方保留的是此前读取的结果。</p>}
      {pollPaused && <p role="status" className="hint">自动刷新已暂停，后台任务不会因此停止。点击“刷新状态”可继续查看。</p>}
      {data && <ProjectRunStatus run={latest || null} worker={data.worker}/>}
      {data?.report?.id===latest?.id && data?.report?.coverage_status==='COMPLETE' && latest?.status!=='COMPLETED' && <p role="status" className="hint">报告已完成，候选记忆尚未完成。可先阅读报告，候选阶段的进度或错误见上方任务状态；候选仍需你确认才会生效。</p>}
      {!loading && !error && !latest && !visible && <p>尚无分析报告。启用自动分析并启动本地后台服务后，会处理已导入的项目。</p>}
      {data && (locked?<button className="btn btn-outline btn-sm" onClick={()=>void loadHistory()}>分析历史</button>:<ProjectActions projectId={projectId} run={latest || null} allowedActions={data.allowed_actions || []} onChanged={refresh} onHistory={()=>void loadHistory()} onRemove={()=>setRemoveOpen(true)}/>)}
      {historyOpen && <div style={{marginTop:16}}><h3>分析历史</h3>{historyBusy && <p role="status">正在读取历史…</p>}{historyError && <p role="alert">{historyError}</p>}<div style={toolbar}>{history.map(run=><button key={run.id} className="btn btn-outline btn-sm" disabled={historyBusy || !hasReadableHistory(run)} onClick={()=>showHistory(run.id)}>#{run.id} · {hasCompleteReport(run)?'完整报告 · ':''}{RUN_LABELS[run.status] || ({SUCCESS:'已有旧版报告',FAILED:'未完成',READY:'待处理'} as Record<string,string>)[run.status] || run.status}</button>)}</div>{!historyBusy && !history.length && <p>暂无历史任务。</p>}{historicalReport && <button className="btn btn-ghost" onClick={()=>{setHistoricalReport(null);setSelected([]);}}>返回最近有效报告</button>}</div>}
    </div>
    {data?.partial_preview && latest?.coverage_status==='PARTIAL' && <details className="card" style={{padding:18,marginBottom:18}}><summary>已保存的部分分析 · 不代表完整项目结论</summary><p className="hint">未覆盖的模块还不能下结论。部分结果不会自动成为有效记忆。</p>{data.partial_preview.summary && <p>{data.partial_preview.summary}</p>}{data.partial_preview.modules?.map((m,i)=><article key={i}><h4>{m.title || m.name || '模块'}</h4><p>{m.summary}</p></article>)}</details>}
    {visible?.report && <div className="card" style={{padding:20}}>
      <h3>{historical?'旧报告（历史成功报告）':'最近有效报告'} · #{visible.id}</h3>
      {historical && <p className="hint">这是先前完成的报告，不是最新任务的结果。{!visible.is_latest_valid?'已有更新版本，此报告仅供回顾。':''}</p>}
      {visible.coverage_status==='PARTIAL' && <p role="alert">此报告仅覆盖部分模块，不能视为完整分析，暂不能用于项目补题。</p>}
      {visible.freshness!=='CURRENT' && <p role="alert">{visible.freshness==='UNKNOWN'?'报告新鲜度尚未确认，请先检查远端更新。':'报告对应的代码已有变化，需更新报告并重新确认受影响的记忆。'}当前不能直接用旧结论创建新题。</p>}
      <p className="hint">报告时间：{visible.as_of?new Date(visible.as_of).toLocaleString():'未记录'}。分析观察不会证明测试已经执行或个人已经掌握。</p>
      <p style={{whiteSpace:'pre-wrap'}}>{visible.report.summary}</p>
      {visible.report.sections?.map(s=><article key={s.key}><h3>{s.title}</h3><p style={{whiteSpace:'pre-wrap'}}>{s.body}</p>{!!s.claim_ids?.length && <details><summary>查看本节依据</summary>{visible.report.claims?.filter(c=>s.claim_ids.includes(c.id)).map(c=><div key={c.id}><h4>{c.title}</h4><span className="chip chip-slate">{({CODE_OBSERVED:'代码观察',INFERRED:'待验证推断',DOC_CLAIM:'文档声明',UNASSESSED:'尚未判断'} as Record<string,string>)[c.kind] || '待核对观察'}</span><p>{c.text}</p><SourceCitations citations={c.citations} sourceExcerpts={visible.source_excerpts}/></div>)}</details>}</article>)}
      <h3>架构与关键链路</h3>
      {visible.report.architecture.map((item,i)=><article key={i}><h4>{item.title}</h4><span className="chip chip-slate">{factLabel(item.fact_type)}</span><p style={{whiteSpace:'pre-wrap'}}>{item.detail}</p><SourceCitations citations={item.citations} sourceExcerpts={visible.source_excerpts}/></article>)}
      <h3>项目问题与待验证风险</h3>
      {!visible.report.issues.length && <p>在本次已读范围内没有输出具体问题；这不代表项目已经没有风险。</p>}
      {visible.report.issues.map(issue=><article key={issue.id} style={{borderTop:'1px solid var(--border)',padding:'14px 0'}}>
        <div style={toolbar}><label style={{display:'flex',gap:8,alignItems:'center'}}><input type="checkbox" aria-label={'选择项目问题 '+issue.id} checked={selected.includes(issue.id)} disabled={!canPrepare || !visible.active_issue_ids?.includes(issue.id)} onChange={e=>setSelected(old=>e.target.checked?[...old,issue.id]:old.filter(id=>id!==issue.id))}/><strong>{issue.title}</strong></label><span className="chip chip-slate">{factLabel(visible.report.claims?.find(claim=>claim.id===issue.claim_id)?.kind)}</span><span className="chip chip-amber">{{high:'高优先级',medium:'中优先级',low:'低优先级'}[issue.severity]}</span></div>
        {!visible.active_issue_ids?.includes(issue.id) && <p className="hint">对应记忆尚未确认、需要重新确认，或已停用。先核对下方候选记忆，当前不用于新任务。</p>}
        {visible.issue_memories?.filter(m=>m.issue_id===issue.id && m.user_corrected).map(m=><div key={m.memory_id} className="demo-banner"><b>当前记忆已纠正 · #{m.memory_id}</b><p style={{whiteSpace:'pre-wrap'}}>{m.content}</p><p className="hint">已确认且仍适用的记忆可供新任务引用；下方原报告保留历史。</p></div>)}
        <p style={{whiteSpace:'pre-wrap'}}>{issue.detail}</p><p><b>影响：</b>{issue.impact}</p><p><b>改进建议：</b>{issue.recommendation}</p><SourceCitations citations={issue.citations} sourceExcerpts={visible.source_excerpts}/>
        {!!issue.interview_questions.length && <details><summary>可以如何追问</summary><ul>{issue.interview_questions.map((q,i)=><li key={i}>{q}</li>)}</ul></details>}
      </article>)}
      {!!visible.report.issues.length && <><button className="btn btn-primary" disabled={!canPrepare || !selectedActive.length} onClick={()=>navigate(issueWorkshopPath(projectId,visible.id,selectedActive))}>根据所选问题准备考核</button><p className="hint">仅使用已确认、仍适用的项目认知生成可审核的题目。实际考核仍需独立作答与评分。</p></>}
      {!!visible.report.unknowns.length && <details open><summary>尚未确认的部分与分析限制</summary><ul>{visible.report.unknowns.map((line,i)=><li key={i}>{line}</li>)}</ul></details>}
      {visible.report.changes && <details><summary>相比上一版的变化</summary>{Array.isArray(visible.report.changes)?<ul>{visible.report.changes.map((c,i)=><li key={i}>{c}</li>)}</ul>:<p>{visible.report.changes}</p>}</details>}
      <details style={{marginTop:14}}><summary>代码版本与读取范围</summary><p className="hint">代码版本：<code>{visible.source_revision || '未记录'}</code></p>{visible.coverage && <><p>已读 {visible.coverage.files_read ?? '未知'} / {visible.coverage.eligible_files ?? '未知'} 个候选文件。</p>{visible.coverage.limitations?.map((l,i)=><p key={i}>{l}</p>)}{visible.coverage.tree_truncated && <p>文件树已截断，覆盖不完整。</p>}{!!visible.coverage.unread_files?.length && <p>未读取：{visible.coverage.unread_files.join('、')}</p>}{!!visible.coverage.failed_files?.length && <p>读取失败：{visible.coverage.failed_files.join('、')}</p>}</>}</details>
      {!!visible.generations?.length && <div><h3>由本报告安排的练习与考核</h3>{visible.generations.map(batch=><div key={batch.id} style={{padding:'10px 0',borderTop:'1px solid var(--border)'}}><Link to={'/bank/workshop?task='+batch.id}>补题任务 #{batch.id}</Link><span className="hint"> · {batch.status} · {batch.question_ids.length} 道已入库题</span>{batch.assessments.map(exam=><p key={exam.id}><Link to={exam.status==='COMPLETED'?'/exam/diagnosis/report?id='+exam.id:'/exam/diagnosis?id='+exam.id}>考核 #{exam.id} · {exam.status==='COMPLETED'?'查看评分报告':'继续作答 / 查看状态'}</Link>{exam.status==='COMPLETED' && exam.score!=null && <span> · {exam.score} 分</span>}</p>)}</div>)}</div>}
    </div>}
    {!loading && !error && !visible && <p className="hint">尚无完整有效报告。后台进度与部分结果会独立保留。</p>}
    {!locked && <ProjectMemoryCandidates projectId={projectId} refreshKey={`${data?.report?.id || 0}:${latest?.id || 0}:${latest?.status || ''}:${pollEpoch}`} onReviewed={refresh}/>}
    {removeOpen && <Modal title="移除项目" onClose={()=>{if(!removeBusy)setRemoveOpen(false);}} footer={<><button className="btn btn-outline" disabled={removeBusy} onClick={()=>setRemoveOpen(false)}>保留项目</button><button className="btn btn-primary" disabled={removeBusy} onClick={remove}>移除项目</button></>}><p>移出列表并停止自动分析，停用当前项目记忆。历史报告、确认记录与既有题目和答卷保留。</p>{removeError && <p role="alert">{removeError}</p>}</Modal>}
  </section>;
}
