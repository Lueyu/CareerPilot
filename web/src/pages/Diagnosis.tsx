import React, { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { DemoBanner, Modal } from '../components/ui';
import { api, ExamDetail, ScoringTaskAck } from '../api/client';
import { loadOrCreateExam, clearExamCreation, submitExam } from '../lib/coreLoop';
import {useExamDraft,clearSavedDraft} from '../lib/useExamDraft';
import { useSpeechRecognition } from '../lib/speech';

type Phase = 'loading' | 'quiz' | 'scoring' | 'uncertain' | 'closed' | 'error';

export default function Diagnosis({ studyMode = false }: { studyMode?: boolean } = {}) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const requestedId = params.get('id');
  const returnTask=params.get('return_task_id');
  const validReturnTask=returnTask && /^[1-9]\d*$/.test(returnTask) && Number.isSafeInteger(Number(returnTask))?returnTask:null;
  const reportPath=(id:number)=>`${studyMode ? '/study/retest' : '/exam/diagnosis'}/report?id=${id}${validReturnTask?`&return_task_id=${validReturnTask}`:''}`;
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const [phase, setPhase] = useState<Phase>('loading');
  const [exam, setExam] = useState<ExamDetail | null>(null);
  const [createErr, setCreateErr] = useState<string | null>(null);
  const [idx, setIdx] = useState(0);
  const locked=useRef(false);
  const draft=useExamDraft(exam,phase==='quiz' && !busy);
  const answers=draft.entries.map(e=>e.value);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [abortOpen, setAbortOpen] = useState(false);

  const [secs, setSecs] = useState(30 * 60);
  useEffect(() => {
    let alive = true;
    setPhase('loading');
    setCreateErr(null);
    loadOrCreateExam(requestedId).then(d => {
      if (!alive) return;
      if (d.status !== 'ACTIVE') {
        clearExamCreation();
        if(['ABORTED','EXPIRED'].includes(d.status)){setExam(d);setPhase('closed');return;}
        navigate(reportPath(d.id), { replace: true });
        return;
      }
      if (!d.questions.length) throw new Error('本次考试没有可用题目');
      setExam(d);
      setIdx(0);
      const elapsed = d.started_at ? (Date.now() - new Date(d.started_at).getTime()) / 1000 : 0;
      setSecs(Math.max(0, Math.round(d.duration_minutes * 60 - elapsed)));
      setPhase('quiz');
      if (!requestedId) setParams({ id: String(d.id),...(validReturnTask?{return_task_id:validReturnTask}:{}) }, { replace: true });
    }).catch(e => {
      if (alive) { setCreateErr(String(e.message || e)); setPhase('error'); }
    });
    return () => { alive = false; };
  }, [requestedId, retry]);

  // 计时
  useEffect(() => {
    if (phase !== 'quiz') return;
    const deadline=exam?.started_at?new Date(exam.started_at).getTime()+(exam.duration_minutes*60000):Date.now()+secs*1000;
    const t = setInterval(() => setSecs(Math.max(0,Math.ceil((deadline-Date.now())/1000))), 1000);
    return () => clearInterval(t);
  }, [phase]);

  const qs = exam?.questions ?? [];
  const q = qs[idx];
  const isText = q?.qtype !== 'mcq';
  const answered = isText ? String(answers[idx] ?? '').trim().length > 0 : Number(answers[idx] ?? -1) >= 0;
  const mm = String(Math.floor(secs / 60)).padStart(2, '0');
  const ss = String(secs % 60).padStart(2, '0');

  const { supported: speechSupported, listening, interim, error: speechError, start: startSpeech, stop: stopSpeech } =
    useSpeechRecognition((delta) => {
      if(locked.current || phase!=='quiz')return;
      const cur=String(draft.getEntries()[idx]?.value??'');draft.edit(idx,cur+delta);
    });
  // 切换题目时停止录音，避免识别结果串到下一题。
  useEffect(() => { stopSpeech(); }, [idx, stopSpeech]);
  useEffect(()=>{
    const server=draft.serverExam;if(!server || server.id!==exam?.id || server.status==='ACTIVE')return;
    locked.current=true;stopSpeech();
    if(['EXPIRED','ABORTED'].includes(server.status)){setExam(server);setPhase('closed');}
    else navigate(reportPath(server.id),{replace:true});
  },[draft.serverExam,exam?.id,navigate,stopSpeech]);

  const changeQuestion = async (nextIndex: number) => {
    if (!exam || busy || locked.current || listening || interim) return;
    locked.current=true;stopSpeech();
    setBusy(true); setActionError(null);
    try { await draft.flush(); if(draft.getEntries().some(e=>e.conflict))throw new Error('请先处理答案版本冲突');setIdx(nextIndex); }
    catch (e: any) { setActionError(`保存失败：${e.message || e}。输入已保留，请重试保存。`); }
    finally { locked.current=false;setBusy(false); }
  };

  const doAbort = async () => {
    if (!exam || busy) return;
    setAbortOpen(false); setBusy(true); setActionError(null);
    try {
      await api.post(`/exams/${exam.id}/abort`);
      clearExamCreation(); clearSavedDraft(exam.id); navigate(studyMode ? '/study/notes?tab=review' : '/exam');
    } catch (e: any) { setActionError(`放弃失败：${e.message || e}。考试仍保留。`); }
    finally { setBusy(false); }
  };

  const checkSubmission = async () => {
    if(!exam)return;
    setBusy(true);
    try{
      const current=await api.get<ExamDetail>(`/exams/${exam.id}`);
      if(current.status==='ACTIVE'){draft.reconcile(current);locked.current=false;setPhase('quiz');}
      else if(['ABORTED','EXPIRED'].includes(current.status)){setExam(current);setPhase('closed');}
      else {clearExamCreation();clearSavedDraft(exam.id);navigate(reportPath(exam.id),{replace:true});}
    }catch(e){setActionError(`暂时无法确认提交状态：${e instanceof Error?e.message:String(e)}。答卷保持只读，本地输入仍保留。`);setPhase('uncertain');}
    finally{setBusy(false);}
  };
  const submit = async () => {
    if (!exam || busy || locked.current || listening || interim || draft.entries.some(e=>e.conflict)) return;
    locked.current=true;stopSpeech();
    setConfirmOpen(false); setBusy(true); setActionError(null); setPhase('scoring');
    try {
      await draft.waitForSave();
      const finalEntries=draft.getEntries();
      const task=await submitExam(exam,finalEntries.map(e=>e.value),api,finalEntries.map(e=>e.revision));
      if(task.exam_id!==exam.id)throw new Error('提交响应缺少本场考试标识，需核对服务器状态');
      clearExamCreation(); clearSavedDraft(exam.id);
      navigate(reportPath(exam.id));
    } catch (e: any) {
      setActionError(`本次考试 #${exam.id} 提交尚未确认：${e.message || e}。输入已保留，正在核对是否已冻结。`);
      setPhase('uncertain');await checkSubmission();
    } finally { setBusy(false); }
  };

  if (phase === 'error') return <div className="card"><h2>无法加载本次考试</h2><p role="alert">{createErr}</p><button className="btn btn-primary" onClick={() => setRetry(x => x + 1)}>重试</button><button className="btn btn-outline" onClick={() => navigate(studyMode ? '/study/notes?tab=review' : '/exam')}>{studyMode ? '返回复习与练习' : '返回考试中心'}</button></div>;
  if(phase==='uncertain')return <div className="card"><h2>正在核对本次提交状态</h2><p role="alert">{actionError}</p><p>确认服务器状态前保持答卷只读，避免修改已经冻结的答案。</p><button className="btn btn-primary" disabled={busy} onClick={checkSubmission}>重新核对本次状态</button></div>;
  if(phase==='closed')return <div className="card"><h2>{exam?.status==='EXPIRED'?'本次考试已超时':'本次考试已放弃'}</h2><p>已保存的作答保留，本次没有有效成绩。当前不能继续修改或提交。</p><button className="btn btn-outline" onClick={()=>navigate(studyMode ? '/study/notes?tab=review' : '/exam')}>{studyMode ? '返回复习与练习' : '返回考试中心'}</button></div>;

  if (phase === 'loading') {
    return (
      <div>
        <div className="quiz-top">
          <div>
            <div className="page-title" style={{ fontSize: 22 }}>{studyMode ? '新情境复测' : '独立诊断'}</div>
            <div className="page-sub">{studyMode ? '正在读取本场试题…' : '正在生成自适应试卷…'}</div>
          </div>
        </div>
        <div className="score-box">
          <div className="score-ring">
            <svg width="110" height="110" viewBox="0 0 110 110">
              <circle cx="55" cy="55" r="46" fill="none" stroke="#e8e8f5" strokeWidth="8" />
              <circle className="ring-spin" cx="55" cy="55" r="46" fill="none" stroke="var(--primary)" strokeWidth="8" strokeLinecap="round"
                strokeDasharray="289" strokeDashoffset="80" transform="rotate(-90 55 55)" />
            </svg>
            <div className="ring-label">{studyMode ? '读取中' : '选题中'}</div>
          </div>
          <h3 style={{ fontSize: 18, fontWeight: 650, marginTop: 18 }}>{studyMode ? '正在打开复测' : '自适应选题引擎运行中…'}</h3>
          <p className="hint" style={{ textAlign: 'center' }}>{studyMode ? '沿用出卷时保存的题目和评价标准' : '优先覆盖状态未知 / 待验证 / 低于目标的能力域'}</p>
        </div>
      </div>
    );
  }

  if (phase === 'scoring') {
    return (
      <div>
        <div className="quiz-top">
          <div>
            <div className="page-title" style={{ fontSize: 22 }}>{studyMode ? '新情境复测' : '独立诊断'}</div>
            <div className="page-sub">正在保存并冻结本次答卷…</div>
          </div>
        </div>
        <DemoBanner />
        <div className="score-box">
          <div className="score-ring">
            <svg width="110" height="110" viewBox="0 0 110 110">
              <circle cx="55" cy="55" r="46" fill="none" stroke="#e8e8f5" strokeWidth="8" />
              <circle className="ring-spin" cx="55" cy="55" r="46" fill="none" stroke="var(--primary)" strokeWidth="8" strokeLinecap="round"
                strokeDasharray="289" strokeDashoffset="80" transform="rotate(-90 55 55)" />
            </svg>
            <div className="ring-label">提交中</div>
          </div>
          <h3 style={{ fontSize: 18, fontWeight: 650, marginTop: 18 }}>等待服务器确认保存与冻结</h3>
          <p className="hint" style={{ textAlign: 'center' }}>确认后会进入本次后台评分状态页；这里还不代表评分已完成。</p>
          <div className="steps" style={{ maxWidth: 520, margin: '22px auto 0' }}>
            <div className="step now"><span className="step-ic">…</span>保存与提交</div>
            <div className="step"><span className="step-ic">·</span>冻结文本答卷</div>
            <div className="step"><span className="step-ic">·</span>后台评分</div>
            <div className="step"><span className="step-ic">·</span>生成报告</div>
          </div>
        </div>
      </div>
    );
  }

  if (!q || !exam) return null;
  const interviewSetId = exam.interview_set_id;

  return (
    <div>
      <div className="quiz-top">
        <div>
          <div className="page-title" style={{ fontSize: 22 }}>{interviewSetId ? '练习这套面经' : studyMode ? '新情境复测' : '独立诊断'}</div>
          <div className="page-sub">
            {interviewSetId ? `来源：${exam.title} · 题目版本已冻结` : <>题 {idx + 1}/{qs.length} · {q.skill || q.title.slice(0, 24)}<span className="chip chip-green" style={{ marginLeft: 8 }}>真实选题</span></>}

          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div className="quiz-timer">
            <span className="timer-ic">⏱</span>
            <b>{mm}:{ss}</b>
            <span className="chip chip-purple">剩余时间</span>
          </div>
          <button className="btn btn-outline btn-sm" onClick={() => setAbortOpen(true)}>放弃考试</button>
        </div>
      </div>
      {!interviewSetId && <DemoBanner />}

      {actionError && <div role="alert" className="demo-banner demo-banner-err">{actionError}<button className="btn btn-outline btn-sm" onClick={() => navigate(reportPath(exam.id))}>查看本次状态</button></div>}
      <div role="status" className="hint">{draft.saving?'正在保存到服务器…':draft.entries.some(e=>e.dirty)?'有尚未保存到服务器的编辑，浏览器草稿已保留。':'已保存到服务器。关闭页面后可从考试中心继续。'}</div>
      {draft.saveError && <div role="alert" className="demo-banner demo-banner-err">{draft.saveError}<button className="btn btn-outline btn-sm" disabled={busy || draft.saving} onClick={()=>void draft.flush().catch(()=>{})}>重试保存</button></div>}
      {draft.entries.map((entry,i)=>entry.conflict && <div role="alert" className="demo-banner" key={i}>第 {i+1} 题服务器已有不同版本，本地编辑未被覆盖。<p>服务器答案：{String(entry.serverValue??'未作答')}</p><button className="btn btn-outline btn-sm" onClick={()=>draft.resolveConflict(i,false)}>使用服务器答案</button><button className="btn btn-outline btn-sm" onClick={()=>draft.resolveConflict(i,true)}>按最新版本保存我的编辑</button></div>)}
      <div className="quiz-card">
        <div className={interviewSetId ? 'bank-quiz-stem' : undefined}>
        <div className="quiz-q-head">
          <span className="chip chip-purple">第 {idx + 1} 题</span>
          <span className="chip chip-slate">{isText ? '文本作答' : '单选'}</span>
          <span className="chip chip-slate">{q.short_code}</span>
          {isText && <span className="chip chip-amber">建议 3-5 分钟</span>}
        </div>
        <h3 className="quiz-stem">{q.title}</h3>
        </div>

        {isText ? (
          <div className={interviewSetId ? 'bank-quiz-answer' : undefined}>
            {interviewSetId && <label className="hint" htmlFor="bank-practice-answer">我的作答</label>}
            <textarea
              id={interviewSetId ? 'bank-practice-answer' : undefined}
              className="textarea"
              style={{ minHeight: 150, marginTop: 16 }}
              placeholder="输入你的回答，或点击下方麦克风语音输入…"
              value={String(answers[idx] ?? '')}
              disabled={busy}
              aria-label={`第 ${idx+1} 题回答`}
              onChange={(e) => draft.edit(idx,e.target.value)}
            />
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10 }}>
              {speechSupported ? (
                <button
                  className={listening ? 'btn btn-danger' : 'btn btn-outline'}
                  disabled={busy}
                  onClick={() => (listening ? stopSpeech() : startSpeech())}
                  title={listening ? '点击停止语音识别' : '点击开始语音输入（中文，Chrome / Edge 可用）'}
                >
                  {listening ? '⏹ 停止录音' : '🎙️ 语音输入'}
                </button>
              ) : (
                <button className="btn btn-outline" disabled title="当前浏览器不支持语音识别（需 Chrome / Edge）">🎙️ 语音输入不可用</button>
              )}
              {listening ? (
                <span className="hint">{interim ? `识别中：${interim}` : '正在聆听，请对着麦克风说话…'}</span>
              ) : speechError ? (
                <span className="hint" role="alert">{speechError}</span>
              ) : (
                <span className="hint">点击麦克风语音输入，识别结果自动追加到回答框（中文）。</span>
              )}
            </div>
            {interviewSetId && <button className="btn btn-outline btn-sm" disabled={busy || listening || !!interim} onClick={async () => {
              try { await draft.flush(); if (draft.getEntries().some(entry => entry.conflict)) throw new Error('请先处理答案版本冲突'); navigate(`/bank?set=${interviewSetId}`); }
              catch (error) { setActionError(`保存失败：${(error as Error).message}`); }
            }}>返回面经集</button>}
          </div>
        ) : (
          <div className="opts" style={{ marginTop: 18 }}>
            {(q.options || []).map((op, i) => (
              <div
                className={`opt ${answers[idx] === i ? 'sel' : ''}`}
                key={op}
                role="radio" aria-checked={answers[idx]===i} aria-disabled={busy} tabIndex={busy?-1:0}
                onClick={() => {if(!busy)draft.edit(idx,i);}}
                onKeyDown={e=>{if(!busy && ['Enter',' '].includes(e.key)){e.preventDefault();draft.edit(idx,i);}}}
              >
                <span className="opt-radio">{answers[idx] === i ? '●' : '○'}</span>
                <span>{op}</span>
              </div>
            ))}
          </div>
        )}

        <div className="quiz-nav">
          <button className="btn btn-outline" disabled={idx === 0 || busy || listening || !!interim} onClick={() => changeQuestion(idx - 1)}>← 上一题</button>
          <div style={{ display: 'flex', gap: 8 }}>
            {qs.map((_, i) => (
              <span key={i} className={`dot-q ${i === idx ? 'now' : answers[i] !== undefined && (typeof answers[i] === 'number' ? answers[i] >= 0 : (answers[i] as string).length > 0) ? 'done' : ''}`} />
            ))}
          </div>
          {idx < qs.length - 1 ? (
            <button className="btn btn-primary" disabled={!answered || busy || listening || !!interim} onClick={() => changeQuestion(idx + 1)}>下一题 →</button>
          ) : (
            <button className="btn btn-primary" disabled={!answered || busy || listening || !!interim || draft.entries.some(e=>e.conflict)} onClick={() => setConfirmOpen(true)}>提交答卷</button>
          )}
        </div>
      </div>

      {abortOpen && (
        <Modal title="放弃考试" onClose={() => setAbortOpen(false)} size="sm"
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setAbortOpen(false)}>继续作答</button>
              <button className="btn btn-danger" onClick={doAbort}>确认放弃</button>
            </>
          }>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.7 }}>
            放弃后本场考试标记为「已放弃」，已作答内容不会计入能力证据，也不会生成报告。
          </p>
          <p className="hint" style={{ marginTop: 6 }}>关闭页面后可从考试中心继续，未成功保存的输入保留在本浏览器会话。</p>
        </Modal>
      )}
      {confirmOpen && (
        <Modal title="提交答卷" onClose={() => setConfirmOpen(false)} size="sm"
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setConfirmOpen(false)}>再检查一下</button>
              <button className="btn btn-primary" disabled={busy || listening || !!interim || draft.entries.some(e=>e.conflict)} onClick={submit}>确认提交</button>
            </>
          }>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.7 }}>
            共 <b>{qs.length}</b> 题，本次编辑会一并保存并冻结；版本冲突会阻止提交并保留输入。
            冻结后不能修改原答，后台评分全部完成后才会形成有效成绩、表现证据和对应学习任务。
          </p>
          <div className="steps" style={{ marginTop: 16 }}>
            {qs.map((_, i) => (
              <div key={i} className={`step ${answers[i] !== undefined && (typeof answers[i] === 'number' ? answers[i] >= 0 : (answers[i] as string).length > 0) ? 'done' : ''}`}>
                <span className="step-ic">✓</span>第 {i + 1} 题
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}
