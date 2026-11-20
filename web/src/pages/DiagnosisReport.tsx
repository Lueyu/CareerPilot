import StudyRetestResult from '../components/StudyRetestResult';
import StudyEntryButton from '../components/StudyEntryButton';
import React, { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { experiencedBankPath, isExperiencedPractice } from '../lib/experiencedBankPath';
import { DemoBanner, PageHead, plainLevel } from '../components/ui';
import { api, ExamReportData, ScoringTaskAck } from '../api/client';
import { clearExamCreation } from '../lib/coreLoop';
import AssessmentEvidence,{referenceText} from '../components/AssessmentEvidence';
import QuestionAmbiguityFeedback from '../components/QuestionAmbiguityFeedback';


const VERDICT_CHIP: Record<string, string> = {
  CORRECT: 'chip-green',
  PARTIAL: 'chip-amber',
  WRONG: 'chip-red',
};
const VERDICT_NAME: Record<string, string> = {
  CORRECT: '✓ 正确',
  PARTIAL: '部分得分',
  WRONG: '✗ 未达要点',
  SCORING_FAILED: '评分失败',
};
const JUDGE_CHIP: Record<string, { cls: string; name: string }> = {
  'judge-v1': { cls: 'chip-purple', name: 'AI 评分' },
  'judge-v2': { cls: 'chip-purple', name: 'AI 评分' },
  'rule-v1': { cls: 'chip-slate', name: '规则评分' },
  'rule-v2': { cls: 'chip-slate', name: '规则评分' },
};
const PENDING_STATUSES=['SUBMITTED','TRANSCRIPT_CONFIRMED','FROZEN','SCORING','SCORING_RETRY_WAIT'];
const STATUS_LABELS:Record<string,string>={COMPLETED:'本次评分已完成',SCORING_FAILED:'本次评分未完成',NEEDS_REVIEW:'评分结果需要复核',SCORING:'后台评分处理中',SCORING_RETRY_WAIT:'等待后台重试',SUBMITTED:'答卷已提交',FROZEN:'答卷已冻结',TRANSCRIPT_CONFIRMED:'答卷已确认',ABORTED:'考试已放弃',EXPIRED:'考试已超时'};
interface AnswerGap {order_no:number;exam_question_id?:number;title:string;detail:string;quote?:string|null;ratio:number;criterion_id:string}

export default function DiagnosisReport({ studyMode = false }: { studyMode?: boolean } = {}) {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const returnTask=params.get('return_task_id');
  const returnLink=returnTask && /^[1-9]\d*$/.test(returnTask) && Number.isSafeInteger(Number(returnTask))?<button className="btn btn-outline" onClick={()=>navigate(`/learning/task/${returnTask}`)}>返回这次学习路径</button>:null;
  const [open, setOpen] = useState<number[]>([0]);
  const [report, setReport] = useState<ExamReportData | null>(null);
  const [reload, setReload] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [deepBusy, setDeepBusy] = useState(false);
  const [deepErr, setDeepErr] = useState<string | null>(null);
  const [rescoreBusy, setRescoreBusy] = useState(false);
  const [rescoreErr, setRescoreErr] = useState<string | null>(null);
  const [pollPaused,setPollPaused]=useState(false);
  const reportId=params.get('id');const currentId=useRef(reportId);currentId.current=reportId;
  const experiencedPractice = !studyMode && (report && String(report.id) === reportId
    ? isExperiencedPractice(report, params)
    : params.get('track') === 'experienced');
  const reportTitle = '社招专项练习报告';
  const bankReturn = experiencedPractice ? <Link className="btn btn-outline" to={experiencedBankPath(params)}>返回社招题单</Link> : null;
  const retryPending=useRef(false);
  const toggle = (i: number) =>
    setOpen((o) => (o.includes(i) ? o.filter((x) => x !== i) : [...o, i]));

  // SCORING_FAILED 后安全重试: 重新评分 → 成功则整体替换报告
  const doRescore = async () => {
    if (!report || retryPending.current || (report.scoring_task && !report.scoring_task.can_retry)) return;
    retryPending.current=true;const id=report.id;
    setRescoreBusy(true);
    setRescoreErr(null);
    try {
      const task = await api.post<ScoringTaskAck>(`/exams/${id}/rescore`);
      if(currentId.current!==String(id))return;
      if(task.exam_id!==id)throw new Error('重试响应不属于本次考试，请刷新核对');
      setReport(old=>old && old.id===id?{...old,status:task.status,score:null,gained_points:null,overall:null,scoring_task:old.scoring_task?{...old.scoring_task,status:'QUEUED',can_retry:false}:null}:old);
      setReload(x=>x+1);
    } catch (e: any) {
      if(currentId.current===String(id))setRescoreErr(String(e.message || e));
    } finally {
      retryPending.current=false;setRescoreBusy(false);
    }
  };

  const genDeep = async () => {
    if (!report) return;
    setDeepBusy(true);
    setDeepErr(null);
    try {
      const deep = await api.post<ExamReportData['diagnosis_deep']>(`/exams/${report.id}/report/deep`);
      if(currentId.current===String(report.id))setReport(old=>old && old.id===report.id?{...old,diagnosis_deep:deep,has_deep:true}:old);
    } catch (e: any) {
      setDeepErr(String(e.message || e));
    } finally {
      setDeepBusy(false);
    }
  };

  useEffect(() => {
    setReport(null);setErr(null);setDeepErr(null);setRescoreErr(null);setOpen([0]);
  },[reportId]);
  useEffect(() => {
    let alive = true,reads=0,timer:ReturnType<typeof setTimeout>|undefined;
    setPollPaused(false);
    if (!reportId || !/^\d+$/.test(reportId)) { setErr('请从本次考试或历史记录打开带考试 ID 的报告。'); return; }
    const poll=async()=>{
      reads++;let again=false;
      try{const r=await api.get<ExamReportData>(`/exams/${reportId}/report`);if(!alive)return;if(String(r.id)!==reportId)throw new Error('收到其他考试的响应，本次结果未被替换');setReport(r);setErr(null);again=PENDING_STATUSES.includes(r.status);}
      catch(e){if(!alive)return;setErr(String(e instanceof Error?e.message:e));again=true;}
      if(!alive || !again)return;
      if(reads>=120){setPollPaused(true);return;}
      timer=setTimeout(()=>{void poll();},5000);
    };
    void poll();return()=>{alive=false;if(timer)clearTimeout(timer);};
  }, [reportId, reload]);

  useEffect(() => {
    if (report?.status === 'COMPLETED') clearExamCreation();
  }, [report]);

  // 后端报告
  if (report && String(report.id) === reportId) {
    const r = report;
    const studyPracticeId = r.questions[0]?.frozen?.study_practice_id;
    if (studyMode && r.status === 'COMPLETED' && Number.isSafeInteger(studyPracticeId) && studyPracticeId > 0) {
      return <StudyRetestResult key={r.id} report={r} practiceId={studyPracticeId} />;
    }
    const deep = r.diagnosis_deep;
    const ob = r.overall;
    const degraded = r.status !== 'COMPLETED';
    const canRetry=r.scoring_task?r.scoring_task.can_retry:r.status==='SCORING_FAILED';
    const pending=PENDING_STATUSES.includes(r.status);
    const reportTitles:Record<string,string>={diagnosis:'诊断报告',focused:'专项考试报告',bank_exam:'面经考试报告',project:'项目面试报告',practice:'练习反馈报告'};
    const adjacentQuestions=r.questions.filter(q=>q.frozen?.diagnostic_origin?.scope==='adjacent_scenario_diagnostic_not_course_completion_or_delayed_retest');
    const missingAssistanceDeclaration=adjacentQuestions.some(q=>q.frozen?.diagnostic_origin?.external_assistance_declaration==='NOT_COLLECTED');
    const failedSet = new Set(r.scoring_failed || []);
    const frozenMaximums=r.questions.map(q=>q.frozen?.rubric?.max_points);
    const totalPoints=typeof r.total_points==='number' && r.total_points>0?r.total_points:frozenMaximums.length>0 && frozenMaximums.every(value=>typeof value==='number' && Number.isFinite(value) && value>0)?frozenMaximums.reduce((sum,value)=>sum+value,0):'—';
    const judges = r.summary?.judges;
    const gaps=r.questions.flatMap<AnswerGap>(q=>{
      const detailed=q.answer?.feedback?.criterion_scores;
      if(detailed?.length)return detailed.filter(c=>c.score<c.max_score).map(c=>({order_no:q.order_no,exam_question_id:q.exam_question_id,title:c.title || q.frozen?.rubric?.criteria?.find((x:any)=>x.criterion_id===c.criterion_id)?.title || c.criterion_id,detail:c.missing_points.join('；') || c.why_not_higher,quote:c.answer_quotes[0],ratio:c.max_score?c.score/c.max_score:1,criterion_id:c.criterion_id}));
      return (q.answer?.feedback?.missed_points || []).map(p=>({order_no:q.order_no,exam_question_id:q.exam_question_id,title:typeof p==='string'?p:p.title,detail:'历史结果仅记录缺失要点；未保存逐项引用与评分锚点。',quote:null,ratio:1,criterion_id:''}));
    }).sort((a,b)=>a.ratio-b.ratio).slice(0,3);
    const strengths=r.questions.flatMap(q=>(q.answer?.feedback?.hit_points || []).map(p=>`Q${q.order_no}：${typeof p==='string'?p:p.title}`)).slice(0,3);
    const partialEvidence=r.questions.flatMap(q=>(q.answer?.feedback?.criterion_scores || []).filter(c=>c.score>0 && c.score<3).map(c=>({order_no:q.order_no,id:c.criterion_id,title:c.title || q.frozen?.rubric?.criteria?.find((item:any)=>item.criterion_id===c.criterion_id)?.title || c.criterion_id,score:c.score,maximum:c.max_score,quotes:c.answer_quotes || []}))).slice(0,3);
    const recordedStrengths=r.questions.flatMap(q=>{const value=(q.answer?.feedback as {strengths?:unknown}|null)?.strengths;return Array.isArray(value)?value.filter((item):item is string=>typeof item==='string').map(item=>`Q${q.order_no}：${item}`):[];}).slice(0,3);
    const scoringSource = judges && typeof judges === 'object'
      ? `AI 评分 ${judges.judge_v1 == null && judges.judge_v2 == null ? '未记录' : (judges.judge_v1 ?? 0) + (judges.judge_v2 ?? 0)} 题 · 规则评分 ${judges.rule_v1 == null && judges.rule_v2 == null ? '未记录' : (judges.rule_v1 ?? 0) + (judges.rule_v2 ?? 0)} 题`
      : '评分来源见逐题结果';
    return (
      <div>
        <PageHead title={experiencedPractice ? reportTitle : r.title?.trim() || reportTitles[r.kind || ''] || '本次答题报告'} sub={`${experiencedPractice ? '练习' : '考试'} #${r.id} · ${STATUS_LABELS[r.status] || r.status} · ${r.completed_at ? new Date(r.completed_at).toLocaleString('zh-CN', { hour12: false }) : '—'}`}>
          {bankReturn ?? <button className="btn btn-outline" onClick={() => navigate('/exam')}>返回考试中心</button>}
          {!degraded && !experiencedPractice && <button className="btn btn-primary" onClick={() => navigate('/learning')}>查看今日任务</button>}
          {returnLink}
        </PageHead>
        <DemoBanner />
        {adjacentQuestions.length>0 && <section className="card" style={{marginBottom:16}} aria-label="本次诊断范围"><h3>相邻场景诊断</h3><p>题目 {adjacentQuestions.map(q=>`Q${q.order_no}`).join('、')} 只记录当前场景的作答表现，未宣称课程完成，也不说明原失分项已通过；这不是延迟保持复测。</p>{missingAssistanceDeclaration && <p className="hint">尚未收集外部辅助声明（NOT_COLLECTED）。系统仅记录应用内已知曝光与既往作答，不能据此保证完全无辅助。</p>}</section>}
        {err && <p role="alert">最新状态读取失败：{err}。下方保留的是此前读取的本场结果。<button className="btn btn-outline btn-sm" onClick={()=>setReload(x=>x+1)}>刷新本次状态</button></p>}
        {pollPaused && <p role="status">自动刷新已暂停，后台任务不会因此停止。<button className="btn btn-outline btn-sm" onClick={()=>setReload(x=>x+1)}>继续刷新本次状态</button></p>}

        {/* SCORING_FAILED: 显式失败态, 不给假分, 提供重评入口 */}
        {degraded && (
          <div style={{ padding: '14px 18px', borderRadius: 12, marginBottom: 18, background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.3)' }}>
            <div role="status" style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>{STATUS_LABELS[r.status] || '本次评分尚未完成'}{experiencedPractice ? '，练习得分待确认' : '，当前尚无有效成绩，没有有效总分'}</div>
            <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.75, marginBottom: 10 }}>
              {pending?'答卷已冻结，后台任务将保存每题进度。':'失败题目：'+((r.scoring_failed || []).map(n=>`Q${n}`).join('、') || '见下方逐题状态')+'。'} {experiencedPractice ? '已完成的逐题结果会保留。' : '已完成的逐题结果保留；全部完成前不展示正式总分或完整能力结论。'}
              {canRetry?' 可以安全重试尚未成功的题目，不会重复评分已成功题目。':pending?(experiencedPractice?' 可保留当前报告链接，稍后回来查看。':' 可以关闭页面，之后从考试中心回来查看。'):' 当前未提供安全重试，请先按具体错误处理或等待复核。'}
            </div>
            {r.scoring_task && <div><p>已评分 {r.scoring_task.completed_count} / {r.scoring_task.total_count} 题{r.scoring_task.next_retry_at?`；下次重试：${new Date(r.scoring_task.next_retry_at).toLocaleString()}`:''}</p>{r.scoring_task.error && <p role="alert">{r.scoring_task.error.message}</p>}{r.scoring_task.questions.filter(q=>q.error).map(q=><p role="alert" key={q.order_no}>Q{q.order_no}：{q.error?.message}。{q.can_retry?'可安全重试':'请处理原因或等待复核'}</p>)}</div>}
            {rescoreErr && <div className="hint" style={{ color: 'var(--red)', marginBottom: 8 }}>上次重试失败：{rescoreErr}</div>}
            <button className="btn btn-primary" disabled={rescoreBusy} onClick={canRetry ? doRescore : () => setReload(x => x + 1)}>
              {rescoreBusy ? '正在登记重试…' : canRetry ? '重新评分失败题目' : '刷新本次状态'}
            </button>
          </div>
        )}

        <div className="report-hero">
          <div>
            <div className="hero-label">{experiencedPractice ? '本次练习得分' : `本次得分 · ${r.title}`}</div>
            <div style={{ fontSize: 24, fontWeight: 700, marginTop: 4 }}>
              {!degraded && r.score != null ? `${r.score} 分` : '评分未完成'}
              <span className="hero-chip" style={{ marginLeft: 10 }}>{r.questions.length} 题 · {!degraded?r.gained_points??'—':'—'}/{totalPoints} 分</span>
            </div>
            <div className="hint" style={{ color: 'rgba(255,255,255,0.55)', marginTop: 8 }}>
              {experiencedPractice ? '评分仅用于本题的练习与复盘。可保留当前报告链接，之后回来查看。' : r.selection_reason}
            </div>
            {!degraded && !experiencedPractice && (
              <div className="hint" style={{ color: 'rgba(255,255,255,0.75)', marginTop: 6 }}>
                本次答卷表现已记录；单题分数不能代表整个能力域。整体估计仍可能为未知，请查看具体失分项。
              </div>
            )}
          </div>
          <div className="report-score">
            {!degraded && r.score != null ? r.score : '—'}<span>/100</span>
            <div className="note" style={{ fontSize: 12, color: 'rgba(255,255,255,0.5)', marginTop: 2 }}>本次答卷得分</div>
          </div>
        </div>

        {!degraded && <section className="card" aria-label="本次回答的主要反馈" style={{marginBottom:18,padding:18}}>
          <h3>这次做到了什么，下一步补什么</h3>
          {strengths.length?<p><b>已记录达到评分要求的要点：</b>{strengths.join('；')}</p>:<p>本次尚未记录达到合格档位的要点；部分得分仍按逐项依据保留。</p>}
          {partialEvidence.length>0 && <div><h4>已有部分依据（不等于达到合格档位）</h4>{partialEvidence.map(item=><div key={`${item.order_no}:${item.id}`}><p>Q{item.order_no} · {item.title}：{item.score}/{item.maximum}</p>{item.quotes.map((quote,index)=><blockquote key={index} style={{whiteSpace:'pre-wrap'}}>{quote}</blockquote>)}</div>)}</div>}
          {recordedStrengths.length>0 && <p><b>评分器已记录的可取之处：</b>{recordedStrengths.join('；')}</p>}
          {gaps.length?gaps.map((gap,i)=>{
            const task=!experiencedPractice && gap.exam_question_id?r.learning_tasks?.find(t=>t.source_exam_question_id===gap.exam_question_id && (gap.criterion_id && t.criterion_id?t.criterion_id===gap.criterion_id:t.source_criterion===gap.title || (!!gap.criterion_id && t.source_criterion===gap.criterion_id))):undefined;
            return <article key={`${gap.order_no}:${i}`}><h4>{i+1}. Q{gap.order_no} · {gap.title}</h4><p>{gap.detail}</p>{gap.quote && <blockquote>{gap.quote}</blockquote>}
              <a href={`#exam-question-${gap.order_no}`} onClick={()=>setOpen(old=>[...new Set([...old,r.questions.findIndex(q=>q.order_no===gap.order_no)])])}>查看本题的原答与评分依据</a>
              {task && <button className="btn btn-primary btn-sm" style={{marginLeft:12}} onClick={()=>navigate(`/learning/task/${task.id}`)}>学习这一项 →</button>}
            </article>;
          }):<p>{experiencedPractice ? '本次没有记录缺失评分项。' : '本次没有记录缺失评分项；这不表示已证明整个能力域掌握。'}</p>}
          <p className="hint">这些结论只针对本次作答。评分标准未经过充分校准时，应结合原答和标准复核。</p>
        </section>}

        {/* 诊断结论(确定性) */}
        {!experiencedPractice && !degraded && ob && (
          <div className="card" style={{ marginBottom: 18 }}>
            <div className="card-head">
              <span className="card-title">诊断结论</span>
              <span className="card-sub">{scoringSource}</span>
            </div>
            <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.8 }}>{ob.conclusion}</p>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
              {ob.strong.map((s) => <span key={s} className="chip chip-green">✓ {s}</span>)}
              {ob.mid.map((s) => <span key={s} className="chip chip-amber">△ {s}</span>)}
              {ob.weak.map((s) => <span key={s} className="chip chip-red">✗ {s}</span>)}
            </div>
            {ob.priority && (
              <div style={{ marginTop: 14, padding: '12px 14px', borderRadius: 10, background: 'rgba(220,38,38,0.08)', border: '1px solid rgba(220,38,38,0.25)' }}>
                <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 4 }}>🎯 优先行动</div>
                <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.75 }}>{ob.priority.advice}</div>
              </div>
            )}
          </div>
        )}

        {/* 能力域得分 */}
        {!experiencedPractice && !!r.skill_breakdown?.length && (
          <div className="card" style={{ marginBottom: 18 }}>
            <div className="card-head">
              <span className="card-title">本次作答按能力域汇总</span>
              <span className="card-sub">按本次各题得分率聚合 · 薄弱的排前面</span>
            </div>
            <div style={{ display: 'grid', gap: 12 }}>
              {r.skill_breakdown.map((g) => {
                const pct = g.ratio==null?null:Math.round(g.ratio * 100);
                const color = g.ratio==null?'var(--text-3)':g.ratio >= 0.8 ? 'var(--green)' : g.ratio >= 0.6 ? 'var(--amber)' : 'var(--red)';
                return (
                  <div key={g.skill_key} style={{ display: 'grid', gridTemplateColumns: '150px 1fr 130px', gap: 14, alignItems: 'center' }}>
                    <div>
                      <div style={{ fontSize: 13.5, fontWeight: 600 }}>{g.skill_name}</div>
                      <div className="hint" style={{ margin: 0 }}>{g.q_count} 题 · Q{g.order_nos.join(' Q')}</div>
                    </div>
                    <div>
                      {pct==null?<span className="hint">评分未完成，不能按零分理解</span>:<div style={{ height: 8, borderRadius: 4, background: 'var(--surface-3)', overflow: 'hidden' }}>
                        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 4, transition: 'width .4s ease' }} />
                      </div>}
                    </div>
                    <div style={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'right' }}>
                      <b style={{ color }}>{g.earned??'—'}/{g.max}</b>
                      <span style={{ marginLeft: 8 }}>✓{g.verdicts.CORRECT} △{g.verdicts.PARTIAL} ✗{g.verdicts.WRONG}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* 深度诊断(LLM) */}
        {!experiencedPractice && !degraded && (
        <div className="card" style={{ marginBottom: 18 }}>
          <div className="card-head">
            <span className="card-title">AI 深度诊断</span>
            <span className="card-sub">
              {deep?.model ? `${deep.model} · ${(deep.generated_at || '').replace('T', ' ').slice(0, 16)}` : '逐题剖析作答内容 · 归纳强弱项 · 给出行动清单'}
            </span>
          </div>
          {!deep && !deepBusy && (
            <div style={{ textAlign: 'center', padding: '10px 0 4px' }}>
              <p className="hint" style={{ marginBottom: 12 }}>
                可另行生成学习解释。本次已经保存的评分依据在下方逐题结果中，无需再次调用模型才能查看。
              </p>
              <button className="btn btn-primary" onClick={genDeep}>✨ 生成深度诊断</button>
            </div>
          )}
          {deepBusy && (
            <div style={{ textAlign: 'center', padding: '14px 0' }}>
              <div style={{
                width: 28, height: 28, margin: '0 auto 10px', borderRadius: '50%',
                border: '3px solid var(--border-2)', borderTopColor: 'var(--primary)',
                animation: 'spin 0.9s linear infinite',
              }} />
              <style>{'@keyframes spin { to { transform: rotate(360deg); } }'}</style>
              <div style={{ fontSize: 13, color: 'var(--text-3)' }}>正在生成补充说明；本次评分报告已保存。</div>
            </div>
          )}
          {deepErr && (
            <div className="demo-banner demo-banner-err" style={{ marginBottom: 10 }}>深度诊断生成失败：{deepErr}</div>
          )}
          {deep && !deepBusy && (
            <>
              <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.85, whiteSpace: 'pre-wrap' }}>{deep.conclusion}</p>
              {!!deep.weaknesses.length && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>📉 薄弱项（为什么弱 / 还差什么）</div>
                  <div style={{ display: 'grid', gap: 10 }}>
                    {deep.weaknesses.map((w, i) => (
                      <div key={i} style={{ padding: '10px 14px', borderRadius: 10, background: 'rgba(220,38,38,0.07)', border: '1px solid rgba(220,38,38,0.2)' }}>
                        <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 4 }}>{w.skill}</div>
                        <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.75 }}>{w.why}</div>
                        {w.gap && <div style={{ fontSize: 12.5, color: 'var(--text-3)', lineHeight: 1.7, marginTop: 6 }}><b>还差：</b>{w.gap}</div>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {!!deep.strengths.length && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>📈 强项（保持住）</div>
                  <div style={{ display: 'grid', gap: 8 }}>
                    {deep.strengths.map((s, i) => (
                      <div key={i} style={{ padding: '8px 14px', borderRadius: 10, background: 'rgba(22,163,74,0.07)', border: '1px solid rgba(22,163,74,0.2)', fontSize: 13, lineHeight: 1.7 }}>
                        <b>{s.skill}</b><span style={{ color: 'var(--text-2)' }}> — {s.why}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {!!deep.actions.length && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 8 }}>🧭 行动清单（按优先级）</div>
                  <div style={{ display: 'grid', gap: 8 }}>
                    {deep.actions.map((a, i) => (
                      <div key={i} style={{ padding: '10px 14px', borderRadius: 10, background: 'var(--surface-2)', border: '1px solid var(--border-2)' }}>
                        <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 4 }}>{i + 1}. {a.title}</div>
                        <div style={{ fontSize: 12.5, color: 'var(--text-2)', lineHeight: 1.75 }}>{a.detail}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
        )}

        {!experiencedPractice && !degraded && <div className="card" style={{ marginBottom: 18 }}>
          <div className="card-head"><span className="card-title">本次失分对应任务</span><span className="card-sub">观察 {r.observations?.length ?? r.questions.filter(q => q.answer?.evidence_refs?.length).length} 条</span></div>
          {(r.learning_tasks ?? []).length === 0 && <p className="hint">本次暂无关联任务；这不表示整体能力已经达标。</p>}
          {(r.learning_tasks ?? []).map(task => <div className="list-row" key={task.id}>
            <div className="list-main"><div className="list-title">{task.title}</div><div className="list-sub">缺失评分项：{task.source_criterion || '查看任务来源'}</div></div>
            <button className="btn btn-primary btn-sm" onClick={() => navigate(`/learning/task/${task.id}`)}>学习这一项 →</button>
          </div>)}
        </div>}

        <div className="card-head" style={{ margin: '0 0 12px' }}>
          <span className="card-title">逐题评分</span>
        </div>
        {r.questions.map((q, i) => {
          const a = q.answer;
          const step=r.scoring_task?.questions.find(item=>item.order_no===q.order_no);
          const validScore=typeof a?.points==='number' && Number.isFinite(a.points) && a.points>=0 && typeof a.max_points==='number' && Number.isFinite(a.max_points) && a.max_points>0 && a.points<=a.max_points;
          const evaluated=validScore && ['CORRECT','PARTIAL','WRONG'].includes(a?.verdict || '') && !a?.feedback?.error && (step?step.status==='SUCCEEDED' && !!a?.feedback:!failedSet.has(q.order_no) && (!degraded || !!a?.feedback));
          const ok = evaluated && a?.verdict === 'CORRECT';
          const dq = evaluated?deep?.per_question?.[String(q.order_no)]:null;
          const jc = evaluated && a?.judge_version ? JUDGE_CHIP[a.judge_version] : null;
          const failedQ = !evaluated && (step?.status==='FAILED' || failedSet.has(q.order_no));
          const pendingLabel=({QUEUED:'待评分',RUNNING:'评分中',RETRY_WAIT:'等待重试',FAILED:'评分失败',NEEDS_REVIEW:'结果待复核',CANCELLED:'评分已取消'} as Record<string,string>)[step?.status || ''] || (r.status==='NEEDS_REVIEW'?'结果待复核':failedQ?'评分失败':'尚未有效评分');
          return (
            <div className="report-q" key={q.order_no} id={`exam-question-${q.order_no}`}>
              <div className="report-q-head" role="button" tabIndex={0} aria-expanded={open.includes(i)} onKeyDown={e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();toggle(i);}}} onClick={() => toggle(i)}>
                <span className={`chip ${failedQ ? 'chip-red' : evaluated?VERDICT_CHIP[a?.verdict || ''] || 'chip-slate':'chip-slate'}`}>{evaluated?VERDICT_NAME[a?.verdict || '']:pendingLabel}</span>
                {jc && <span className={`chip ${jc.cls}`} style={{ marginLeft: 6 }}>{jc.name}</span>}
                <span className="report-q-score" style={{ color: ok ? 'var(--green)' : 'var(--amber)' }}>{evaluated?a?.points:'—'}<small style={{ fontSize: 13, color: 'var(--text-4)' }}>/{q.frozen?.rubric?.max_points ?? a?.max_points ?? '—'}</small></span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 600 }}>Q{q.order_no} · {q.title}</div>
                  <div className="hint" style={{ margin: 0 }}>{experiencedPractice ? q.short_code : <>{q.skill} · 引用证据 {evaluated?(a?.evidence_refs || []).join(', ') || '—':'尚未生成有效评分证据'}</>}</div>
                </div>
                <span style={{ color: 'var(--text-4)', fontSize: 12 }}>{open.includes(i) ? '收起 ▴' : '展开 ▾'}</span>
              </div>
              {open.includes(i) && (
                <div className="report-q-body">
                  {!evaluated && (step?.error?.message || failedQ) && (
                    <div style={{ padding: '10px 14px', borderRadius: 10, background: 'rgba(220,38,38,0.07)', border: '1px solid rgba(220,38,38,0.25)', marginBottom: 12, fontSize: 13, lineHeight: 1.75 }}>
                      ⚠️ {step?.error?.message || (typeof a?.feedback?.error==='string'?a.feedback.error:'本题评分尚未成功，没有有效结果。')} {step?.can_retry || canRetry?'可在页面顶部安全重试尚未成功的题目。':'请按本次后台状态等待或处理原因。'}
                    </div>
                  )}
                  {dq && (
                    <>
                      <div style={{ padding: '10px 14px', borderRadius: 10, background: 'rgba(109,93,246,0.09)', border: '1px solid rgba(109,93,246,0.25)', marginBottom: 12 }}>
                        <div style={{ fontSize: 13, fontWeight: 650, marginBottom: 4 }}>🔍 AI 剖析 · 为什么是这个分</div>
                        <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.8 }}>{dq.why}</div>
                        <div style={{ fontSize: 13, fontWeight: 650, margin: '10px 0 4px', color: 'var(--primary)' }}>🛠 正确姿势 / 如何改进</div>
                        <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.8 }}>{dq.how}</div>
                      </div>
                    </>
                  )}
                  <p><b>你的回答：</b>{a?.text_answer || (a?.choice_index != null ? `选择题，选了第 ${a.choice_index + 1} 项` : '（未作答）')}</p>
                  <AssessmentEvidence question={q} evaluated={evaluated}/>
                  {evaluated && a?.feedback && !a.feedback.criterion_scores?.length && (
                    <>
                      <p>
                        <b>命中要点：</b>
                        {a.feedback.hit_points?.length
                          ? a.feedback.hit_points.map((p: any) => (typeof p === 'string' ? p : p.title)).join('、')
                          : '未记录达到要求的要点；请结合已保存的分数和说明核对。'}
                      </p>
                      <p>
                        <b>缺失要点：</b>
                        {a.feedback.missed_points?.length
                          ? a.feedback.missed_points.map((p: any) => (typeof p === 'string' ? p : p.title)).join('、')
                          : '无'}
                      </p>
                      {a.feedback.next && <p><b>建议下一步：</b>{a.feedback.next}</p>}
                    </>
                  )}
                  {evaluated && !!a?.feedback?.followups?.length && (
                    <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px dashed var(--border-2)' }}>
                      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>🔗 面试官可能这样追问（建议提前准备）</div>
                      <div style={{ display: 'grid', gap: 8 }}>
                        {a.feedback.followups.map((f, fi) => (
                          <div key={fi} style={{ padding: '9px 13px', borderRadius: 10, background: 'var(--surface-2)', border: '1px solid var(--border-2)' }}>
                            <div style={{ fontSize: 13, lineHeight: 1.7 }}>
                              <span className={`chip ${f.kind === 'Boundary' ? 'chip-amber' : f.kind === 'Trade-off' ? 'chip-purple' : f.kind === 'Expression' ? 'chip-red' : 'chip-blue'}`} style={{ marginRight: 8, fontSize: 11 }}>{f.label || f.kind}</span>
                              {f.question}
                            </div>
                            <div style={{ fontSize: 12, color: 'var(--text-3)', lineHeight: 1.65, marginTop: 5 }}>💡 {f.why}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {evaluated && q.reference_answer && (
                    <div style={{ marginTop: 12, borderTop: '1px dashed var(--border-2)', paddingTop: 10 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>📖 参考答案（正确应该答到什么）</div>
                      <div className="bank-answer" style={{whiteSpace:'pre-wrap'}}>{referenceText(q.reference_answer)}</div>
                    </div>
                  )}
                  {q.exam_question_id && <StudyEntryButton kind="exam_question" id={q.exam_question_id}/>}
                  {q.exam_question_id && <QuestionAmbiguityFeedback key={`${r.id}:${q.exam_question_id}`} examId={r.id} questionId={q.exam_question_id}/>}
                </div>
              )}
            </div>
          );
        })}

        {!experiencedPractice && !degraded && (
        <div className="card" style={{ marginTop: 18, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div>
            <div className="card-title" style={{ margin: 0 }}>下一步</div>
            <p className="hint" style={{ margin: '4px 0 0' }}>本次观察已记录。请打开上方具体任务；学习完成仅记录进度，需新题验证。</p>
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn btn-outline" onClick={() => navigate('/exam/diagnosis')}>再测一次</button>
            <button className="btn btn-primary" onClick={() => navigate('/learning')}>开始学习 →</button>
          </div>
        </div>
        )}
      </div>
    );
  }

  return <div>
    <PageHead title={experiencedPractice ? reportTitle : '本次诊断报告'} sub={reportId ? `${experiencedPractice ? '练习' : '考试'} #${reportId}` : '未指定考试'}>
      {bankReturn ?? <button className="btn btn-outline" onClick={() => navigate('/exam')}>返回考试中心</button>}
      {returnLink}
    </PageHead>
    <div className="card">{err ? <><p role="alert">无法读取本次报告：{err}</p>{reportId && <button className="btn btn-primary" onClick={() => setReload(x => x + 1)}>重试本次报告</button>}</> : <p>正在读取本次报告…</p>}</div>
  </div>;
}
