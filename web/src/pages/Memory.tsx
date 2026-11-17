import React, {useCallback, useEffect, useState} from 'react';
import {Link} from 'react-router-dom';
import {useAppState} from '../components/AppState';
import {Modal, PageHead} from '../components/ui';
import {SourceCitations} from '../components/ProjectAnalysisPanel';
import {api} from '../api/client';
import {ProjectMemory} from '../lib/projectIntelligence';
import {messageOf} from '../lib/questionWorkshop';
import ProjectMemoryCandidates from '../components/ProjectMemoryCandidates';

const TYPE_NAMES: Record<string,string>={CareerGoal:'职业目标',Preference:'偏好',KnowledgeGapSignal:'待诊断线索',ProjectDecision:'项目决策',ProjectUnderstanding:'项目理解',ProjectIssue:'项目问题',Feedback:'反馈',LearningObservation:'学习观察',LearningPreference:'局部教学观察'};
const TYPE_IC: Record<string,string>={CareerGoal:'🎯',Preference:'❤️',KnowledgeGapSignal:'🔎',ProjectDecision:'🧭',ProjectUnderstanding:'🗂',ProjectIssue:'📍',Feedback:'💬'};
const eligible=(item:ProjectMemory)=>item.eligible_for_personalization ?? !(item.meta?.project_id || item.meta?.goal_id);
const learningMemory=(item:ProjectMemory)=>!!item.meta?.goal_id || ['LearningObservation','LearningPreference'].includes(item.mtype);

export default function Memory() {
  const {toast}=useAppState();
  const [items,setItems]=useState<ProjectMemory[]>([]);
  const [loading,setLoading]=useState(true);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [loadError,setLoadError]=useState('');
  const [pasteText,setPasteText]=useState('');
  const [correcting,setCorrecting]=useState<ProjectMemory|null>(null);
  const [correctText,setCorrectText]=useState('');
  const reload=useCallback(async()=>{
    setLoading(true);
    try{const data=await api.get<{items:ProjectMemory[]}>('/memory');setItems(data.items);setLoadError('');}
    catch(e){setLoadError(messageOf(e));setItems([]);}
    finally{setLoading(false);}
  },[]);
  useEffect(()=>{void reload();},[reload]);
  const perform=async(fn:()=>Promise<void>)=>{setBusy(true);setError('');try{await fn();}catch(e){setError(messageOf(e));}finally{setBusy(false);}};
  const confirm=(m:ProjectMemory)=>perform(async()=>{
    await api.post('/memory/'+m.id+'/confirm');toast('✓','已确认记忆 #'+m.id);await reload();
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
  const candidates=items.filter(m=>m.status==='CANDIDATE' && !m.meta?.project_id);
  const renderMemory=(m:ProjectMemory)=><article className="mem-item" key={m.id}>
    <div className="mem-ic">{TYPE_IC[m.mtype] || '📌'}</div>
    <div className="mem-body">
      <div className="q-meta"><span className="chip chip-slate">{TYPE_NAMES[m.mtype] || m.mtype}</span><span className={'chip '+(m.status==='ACTIVE' && eligible(m)?'chip-green':'chip-slate')}>{m.status==='ACTIVE'?(!eligible(m)?'需要重新确认':'当前有效'):m.status==='SUPERSEDED'?'历史版本':m.status==='CANDIDATE'?'待确认':m.status}</span><small>{m.created_at?new Date(m.created_at).toLocaleString():''} · {m.source==='USER'?'用户纠正':m.source==='DIALOG_IMPORT'?'对话导入':'系统记录'}</small></div>
      <p style={{whiteSpace:'pre-wrap'}}>{m.content}</p>
      {m.meta?.goal_id && <div><p className="hint">这是有限范围的学习观察，不代表永久短板或整个能力域已掌握；确认不会改分。</p>{m.eligible_for_personalization!==true && <p className="hint">尚未确认或来源需要复核，当前不用于新任务。</p>}<Link to={m.meta.source_task_id?`/learning/task/${m.meta.source_task_id}`:'/learning'}>返回学习路径核对目标 #{m.meta.goal_id}</Link></div>}
      {m.meta?.project_id && <div>
        {m.eligible_for_personalization!==true && <p className="hint">{m.ineligible_reason || '来源尚未确认或已发生变化'}；当前不用于新任务。请到项目报告核对新的候选记忆。</p>}
        <p className="hint"><Link to={'/assets/project/'+m.meta.project_id}>项目 #{m.meta.project_id}</Link>{m.meta.analysis_id?' · 分析 #'+m.meta.analysis_id:''}{m.meta.source_revision && <> · 代码版本 <code>{m.meta.source_revision}</code></>}</p>
        <p className="hint">这是项目认知，不代表本人职责或已掌握。用户纠正保留来源版本；需结合最新代码判断是否仍适用。</p>
        {!!m.meta.citations?.length && <SourceCitations citations={m.meta.citations}/>}
      </div>}
      {m.superseded_by && <p className="hint">已被记忆 #{m.superseded_by} 替代。</p>}
      {(m.status==='ACTIVE' || m.status==='CANDIDATE') && <div className="mem-actions">
        {m.status==='CANDIDATE' && !learningMemory(m) && <button className="btn btn-primary btn-sm" disabled={busy} onClick={()=>confirm(m)}>确认</button>}
        {m.status==='ACTIVE' && <button className="btn btn-outline btn-sm" disabled={busy} onClick={()=>{setCorrecting(m);setCorrectText(m.content);setError('');}}>纠正</button>}
        <button className="btn btn-ghost btn-sm" disabled={busy} onClick={()=>remove(m)}>{m.status==='CANDIDATE'?'忽略':'删除'}</button>
      </div>}
    </div>
  </article>;
  return <div>
    <PageHead title="记忆中心" sub="项目理解与用户陈述分别留源 · 可纠正、可停用、保留历史"><button className="btn btn-outline" disabled={busy || loading} onClick={reload}>刷新记忆</button></PageHead>
    <p className="hint">已确认且来源仍适用的项目记忆才用于新补题。项目分析和记忆更新不会提高能力分数；个人短板以实际作答和评分为依据。</p>
    <ProjectMemoryCandidates onReviewed={reload}/>
    {loading && <p role="status">正在读取记忆…</p>}
    {loadError && <div role="alert" className="demo-banner demo-banner-err">无法读取记忆：{loadError}。请重试，当前未显示任何替代数据。</div>}
    {error && <div role="alert" className="demo-banner demo-banner-err">操作失败：{error}</div>}
    <div className="grid grid-1-2">
      <section className="card">
        <div className="card-head"><span className="card-title">记忆时间线</span><span className="card-sub">{timeline.length} 条 · 历史版本不参与新任务</span></div>
        {timeline.map(renderMemory)}
        {!loading && !loadError && !timeline.length && <div className="empty" style={{padding:20}}><b>暂无记忆</b><p>可分析一个项目，或导入对话后确认候选记忆。</p></div>}
      </section>
      <div style={{display:'flex',flexDirection:'column',gap:18}}>
        <section className="card" style={{padding:18}}><h3>从对话提取候选记忆</h3><textarea className="textarea" aria-label="对话记录" rows={5} placeholder="粘贴需要整理的对话记录，检查候选内容后再确认。" value={pasteText} onChange={e=>setPasteText(e.target.value)}/><button className="btn btn-outline" style={{marginTop:10}} disabled={busy || !pasteText.trim()} onClick={extract}>{busy?'处理中…':'提取候选记忆'}</button><p className="hint">对话提取与项目代码分析是两个来源。候选记忆确认后才生效。</p></section>
        <section className="card"><div className="card-head"><span className="card-title">候选记忆</span><span className="card-sub">{candidates.length} 条待确认</span></div>{candidates.map(renderMemory)}{!loading && !loadError && !candidates.length && <p style={{padding:18}}>没有待确认的候选。</p>}</section>
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
