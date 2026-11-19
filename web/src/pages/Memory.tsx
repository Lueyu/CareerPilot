import React, {useCallback, useEffect, useState} from 'react';
import {Link, useSearchParams} from 'react-router-dom';
import {useAppState} from '../components/AppState';
import {Modal, PageHead} from '../components/ui';
import {SourceCitations} from '../components/ProjectAnalysisPanel';
import {api} from '../api/client';
import {ProjectMemory} from '../lib/projectIntelligence';
import {messageOf} from '../lib/questionWorkshop';
import ProjectMemoryCandidates from '../components/ProjectMemoryCandidates';

type MemoryEntry = ProjectMemory & { eligible_for_bank_generation?: boolean };
const TYPE_NAMES: Record<string,string>={CareerGoal:'职业目标',Preference:'偏好',KnowledgeGapSignal:'待诊断线索',ProjectDecision:'项目决策',ProjectUnderstanding:'项目理解',ProjectIssue:'项目问题',Feedback:'反馈',BankReviewFeedback:'题库审核反馈',LearningObservation:'学习观察',LearningPreference:'局部教学观察',TaskCompletion:'学习进度'};
const TYPE_IC: Record<string,string>={CareerGoal:'🎯',Preference:'❤️',KnowledgeGapSignal:'🔎',ProjectDecision:'🧭',ProjectUnderstanding:'🗂',ProjectIssue:'📍',Feedback:'💬',BankReviewFeedback:'💬'};
const eligible=(item:ProjectMemory)=>item.eligible_for_personalization ?? !(item.meta?.project_id || item.meta?.goal_id);
const learningMemory=(item:ProjectMemory)=>!!item.meta?.goal_id || ['LearningObservation','LearningPreference'].includes(item.mtype);
const sourceName=(item:ProjectMemory)=>item.mtype==='BankReviewFeedback'?'题库审核':item.meta?.daily_plan_item_key?'每日计划作答':item.meta?.exam_kind?'考试作答':item.mtype==='TaskCompletion'?'学习任务完成':learningMemory(item)?'作答与评价':item.source==='DIALOG_IMPORT'?'对话导入':item.source==='USER'?'用户纠正':'系统记录';
function memoryStatus(item: MemoryEntry) {
  if (item.status === 'ACTIVE') {
    if (item.eligible_for_bank_generation) return { label: '仅题库补题使用', active: true };
    if (item.mtype === 'TaskCompletion') return { label: '已确认', active: true };
    if (eligible(item)) return { label: '当前有效', active: true };
    return { label: '需要重新确认', active: false };
  }
  const label = item.status === 'SUPERSEDED' ? '历史版本' : item.status === 'CANDIDATE' ? '待确认' : item.status;
  return { label, active: false };
}
function completionSource(item:ProjectMemory) {
  const meta=item.meta;
  if (meta?.exam_kind && meta.exam_id) return <Link to={`/exam/diagnosis/report?id=${meta.exam_id}`}>查看原卷 #{meta.exam_id}</Link>;
  if (meta?.daily_plan_date && meta.daily_plan_item_key) {
    const query=new URLSearchParams({plan:'day',date:meta.daily_plan_date,item:meta.daily_plan_item_key});
    return <Link to={`/learning?${query}`}>查看 {meta.daily_plan_date} 的练习作答</Link>;
  }
  return meta?.source_task_id && <Link to={`/learning/task/${meta.source_task_id}`}>查看来源任务 #{meta.source_task_id}</Link>;
}

export default function Memory() {
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
      if (decision?.status!==(action==='confirm'?'CONFIRMED':'IGNORED')) throw new Error(decision?.error || '候选已改变，请返回列表后刷新。');
    } else if(action==='confirm') await api.post('/memory/'+m.id+'/confirm');
    else await api.del('/memory/'+m.id);
    toast('✓',action==='confirm'?'已确认记忆':'已忽略候选');setParams({});await reload();
  });
  const remove=(m:ProjectMemory)=>perform(async()=>{
    await api.del('/memory/'+m.id);toast('✓','记忆 #'+m.id+' 已停用，不再用于新任务');await reload();
  });
  const correct=()=>perform(async()=>{
    if(!correcting || !correctText.trim())return;
    await api.post('/memory/'+correcting.id+'/correct',{content:correctText.trim(),...(correcting.revision!==undefined?{expected_revision:correcting.revision}:{})});
    setCorrecting(null);setCorrectText('');toast('✓','已纠正为新的记忆，旧版本保留在历史中');await reload();
  });
  const extract=()=>perform(async()=>{
    const result=await api.post<{candidates:ProjectMemory[]}>('/memory/import-dialog',{text:pasteText.trim()});
    toast('✓','读取到 '+result.candidates.length+' 条待确认记忆');await reload();
  });
  const timeline=items.filter(m=>m.status!=='CANDIDATE');
  const reviewOnly=params.get('type')==='bank-review';
  const filteredTimeline=reviewOnly ? timeline.filter(m=>m.mtype==='BankReviewFeedback') : timeline;
  const candidates=items.filter(m=>m.status==='CANDIDATE' && !m.meta?.project_id);
  const candidateKey=params.get('candidate');
  const candidate=candidates.find(m=>String(m.id)===candidateKey);
  const renderMemory=(m:MemoryEntry)=>{const status=memoryStatus(m);return <article className="mem-item" key={m.id}>
    <div className="mem-ic">{TYPE_IC[m.mtype] || '📌'}</div>
    <div className="mem-body">
      <div className="q-meta"><span className="chip chip-slate">{TYPE_NAMES[m.mtype] || m.mtype}</span><span className={'chip '+(status.active?'chip-green':'chip-slate')}>{status.label}</span><small>{m.created_at?new Date(m.created_at).toLocaleString():''} · {sourceName(m)}</small></div>
      <p style={{whiteSpace:'pre-wrap'}}>{m.content}</p>
      {m.mtype==='TaskCompletion' && <p className="hint">仅记录学习进度，不影响能力分数。 {completionSource(m)}</p>}
      {m.mtype==='BankReviewFeedback' && <p className="hint">题库补题可将有效反馈作为同技能的历史案例。 <Link to="/bank?review=1">查看待审核队列</Link></p>}
      {m.meta?.goal_id && <div><p className="hint">这是有限范围的学习观察，不代表永久短板或整个能力域已掌握；确认不会改分。</p>{m.eligible_for_personalization!==true && <p className="hint">尚未确认或来源需要复核，当前不用于新任务。</p>}<Link to={m.meta.source_task_id?`/learning/task/${m.meta.source_task_id}`:'/learning'}>返回学习路径核对目标 #{m.meta.goal_id}</Link></div>}
      {m.meta?.project_id && <div>
        {m.eligible_for_personalization!==true && <p className="hint">{m.ineligible_reason || '来源尚未确认或已发生变化'}；当前不用于新任务。请到项目报告核对新的候选记忆。</p>}
        <p className="hint"><Link to={'/assets/project/'+m.meta.project_id}>项目 #{m.meta.project_id}</Link>{m.meta.analysis_id?' · 分析 #'+m.meta.analysis_id:''}{m.meta.source_revision && <> · 代码版本 <code>{m.meta.source_revision}</code></>}</p>
        <p className="hint">这是项目认知，不代表本人职责或已掌握。用户纠正保留来源版本；需结合最新代码判断是否仍适用。</p>
        {!!m.meta.citations?.length && <SourceCitations citations={m.meta.citations}/>}
      </div>}
      {m.superseded_by && <p className="hint">已被记忆 #{m.superseded_by} 替代。</p>}
      {(m.status==='ACTIVE' || m.status==='CANDIDATE') && <div className="mem-actions">
        {m.status==='ACTIVE' && <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>{setCorrecting(m);setCorrectText(m.content);setError('');}}>纠正</button>}
        <button className="btn btn-ghost btn-sm" disabled={busy} onClick={()=>remove(m)}>{m.status==='CANDIDATE'?'忽略':'删除'}</button>
      </div>}
    </div>
  </article>;};
  if(candidateKey) return <div className="memory-page">
    <PageHead title="核对一条候选记忆" sub="确认前可以检查来源和原始上下文。"><Link to="/memory" className="btn btn-outline">返回记忆中心</Link></PageHead>
    {loading && <p role="status">正在读取候选…</p>}
    {(loadError || error) && <p role="alert">{loadError || error}</p>}
    {!loading && !loadError && !candidate && <p>这条候选已处理或不在当前工作空间。</p>}
    {candidate && <>
      <section className="card memory-panel"><div className="hint">来源</div><h2>{sourceName(candidate)}</h2>
        {completionSource(candidate)}
        {candidate.meta?.exam_id && !candidate.meta?.exam_kind && <Link to={`/exam/diagnosis/report?id=${candidate.meta.exam_id}`}>查看评价 #{candidate.meta.exam_id}</Link>}
        {candidate.meta?.completed_at && <p>记录完成于 {new Date(candidate.meta.completed_at).toLocaleString()}</p>}
        {candidate.meta?.submission_id && <p>提交 #{candidate.meta.submission_id}{candidate.meta.scoring_run_id && ` · 反馈运行 #${candidate.meta.scoring_run_id}`}</p>}
        {candidate.source_ref && <p className="hint">来源记录：{candidate.source_ref}</p>}
      </section>
      <section className="card memory-panel"><h2>候选内容</h2><p style={{whiteSpace:'pre-wrap'}}>{candidate.content}</p>
        <p className="hint">{candidate.mtype==='TaskCompletion'?'此记录仅保存学习进度。':'确认只接受这条描述，能力变化由独立测评产生。'}</p>
        <div className="mem-actions"><button className="btn btn-primary" disabled={busy || (candidate.freshness!=null && candidate.freshness!=='CURRENT')} onClick={()=>review(candidate,'confirm')}>确认并返回</button><button className="btn btn-outline" disabled={busy} onClick={()=>review(candidate,'ignore')}>忽略并返回</button></div>
      </section>
    </>}
  </div>;
  return <div className="memory-page">
    <PageHead title="哪些学习记录值得记住？" sub="从完成的任务回到当时的依据。确认后保留记录，适用的观察可用于后续个性化。"><button className="btn btn-outline" disabled={busy || loading} onClick={reload}>刷新记忆</button></PageHead>
    {loading && <p role="status">正在读取记忆…</p>}
    {loadError && <div role="alert" className="demo-banner demo-banner-err">无法读取记忆：{loadError}。请重试，当前未显示任何替代数据。</div>}
    {error && <div role="alert" className="demo-banner demo-banner-err">操作失败：{error}</div>}
    {!loading && !loadError && <section className="card memory-panel memory-pending" aria-label="候选记忆">
      <div className="hint">待审核 · {candidates.length} 条</div>
      {candidates.map(m=><article key={m.id}><h2>{m.content}</h2><p className="hint">来源：{sourceName(m)} · 状态：待审核</p><Link className="btn btn-primary btn-sm" to={`?candidate=${m.id}`}>核对候选</Link></article>)}
      {!candidates.length && <><h2>没有待审核的学习或对话记忆</h2><p className="hint">完成学习任务后可核对进度记录，实际作答与评价也可能生成学习观察。</p><Link className="btn btn-outline btn-sm" to="/learning">查看学习计划</Link></>}
    </section>}
    <div className="memory-sections">
      <section className="card">
        <div className="card-head"><span className="card-title">已确认与历史记录</span><span className="card-sub">{filteredTimeline.filter(m=>m.status==='ACTIVE').length} 条已保存 · 历史版本不参与新任务</span></div>
        <label className="field"><span>记忆类型</span><select className="inp" value={reviewOnly?'bank-review':''} onChange={event=>{
          const next=new URLSearchParams(params);
          if(event.target.value) next.set('type',event.target.value);
          else next.delete('type');
          setParams(next,{replace:true});
        }}><option value="">全部类型</option><option value="bank-review">题库审核反馈</option></select></label>
        {filteredTimeline.map(renderMemory)}
        {!loading && !loadError && !filteredTimeline.length && <div className="memory-panel">
          <h2>{reviewOnly?'还没有题库审核反馈':'还没有已确认的记忆'}</h2>
          {reviewOnly ? <Link to="/bank?review=1">查看待审核题目</Link> : <p className="hint">确认候选后，记忆和来源会出现在这里。</p>}
        </div>}
      </section>
      <div style={{display:'flex',flexDirection:'column',gap:18}}>
        <section className="card" style={{padding:18}}><h3>从对话提取候选记忆</h3><textarea className="textarea" aria-label="对话记录" rows={5} placeholder="粘贴需要整理的对话记录，检查候选内容后再确认。" value={pasteText} onChange={e=>setPasteText(e.target.value)}/><button className="btn btn-outline" style={{marginTop:10}} disabled={busy || !pasteText.trim()} onClick={extract}>{busy?'处理中…':'提取候选记忆'}</button><p className="hint">对话提取与项目代码分析是两个来源。候选记忆确认后才生效。</p></section>
        <ProjectMemoryCandidates onReviewed={reload}/>
      </div>
    </div>
    {correcting && <Modal title={'纠正记忆 #'+correcting.id} onClose={()=>{if(!busy)setCorrecting(null);}} footer={<><button className="btn btn-outline" disabled={busy} onClick={()=>setCorrecting(null)}>取消</button><button className="btn btn-primary" disabled={busy || !correctText.trim()} onClick={correct}>{busy?'保存中…':'保存纠正'}</button></>}>
      <p className="hint">保存新版本并保留原记忆及其来源。已创建的补题任务仍使用原快照。</p>
      <label className="field"><span>原记忆</span><p style={{whiteSpace:'pre-wrap'}}>{correcting.content}</p></label>
      <label className="field"><span>纠正为</span><textarea className="textarea" rows={6} aria-label="纠正记忆内容" value={correctText} onChange={e=>setCorrectText(e.target.value)}/></label>
      {error && <p role="alert">保存失败：{error}。输入内容已保留。</p>}
    </Modal>}
  </div>;
}
