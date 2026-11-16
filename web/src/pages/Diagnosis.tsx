import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { DemoBanner, Modal } from '../components/ui';
import { api, ExamDetail } from '../api/client';
import { QUIZ } from '../data/demo';

type Phase = 'loading' | 'quiz' | 'scoring' | 'error';

export default function Diagnosis() {
  const navigate = useNavigate();
  const [phase, setPhase] = useState<Phase>('loading');
  const [exam, setExam] = useState<ExamDetail | null>(null);
  const [createErr, setCreateErr] = useState<string | null>(null);
  const [idx, setIdx] = useState(0);
  const [answers, setAnswers] = useState<(number | string)[]>([]);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [abortOpen, setAbortOpen] = useState(false);
  const [recording, setRecording] = useState(false);
  const [secs, setSecs] = useState(30 * 60);
  const recTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 创建/恢复考试(真实后端) — 失败则降级为本地 demo 试卷
  // 说明: React StrictMode 下 effect 双触发, 若直接 POST /exams 会建出两条考试;
  // 改为「先恢复进行中的诊断考试, 没有才新建」。
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const list = await api.get<{ history: any[] }>('/exams');
        const active = (list.history || []).find((x: any) => x.kind === 'diagnosis' && x.status === 'ACTIVE');
        if (active) {
          const d = await api.get<ExamDetail>(`/exams/${active.id}`);
          if (!alive) return;
          setExam(d);
          const elapsed = d.started_at ? (Date.now() - new Date(d.started_at).getTime()) / 1000 : 0;
          setSecs(Math.max(60, Math.round((d.duration_minutes || 30) * 60 - elapsed)));
          setAnswers(d.questions.map((q) => {
            if (q.answer) return q.qtype === 'mcq' ? (q.answer.choice_index ?? -1) : (q.answer.text_answer ?? '');
            return q.qtype === 'mcq' ? -1 : '';
          }));
          setPhase('quiz');
          return;
        }
        const d = await api.post<ExamDetail>('/exams', { kind: 'diagnosis' });
        if (!alive) return;
        setExam(d);
        setSecs((d.duration_minutes || 30) * 60);
        setAnswers(d.questions.map((q) => (q.qtype === 'mcq' ? -1 : '')));
        setPhase('quiz');
      } catch (e: any) {
        if (!alive) return;
        setCreateErr(String(e.message || e));
        setSecs(30 * 60);
        setAnswers(QUIZ.map((q) => (q.type === 'text' ? '' : -1)));
        setPhase('quiz');
      }
    })();
    return () => { alive = false; };
  }, []);

  // 计时
  useEffect(() => {
    if (phase !== 'quiz') return;
    const t = setInterval(() => setSecs((s) => (s > 0 ? s - 1 : 0)), 1000);
    return () => clearInterval(t);
  }, [phase]);

  const qs = exam?.questions ?? QUIZ.map((q, i) => ({
    order_no: i + 1, short_code: `DEMO-${i + 1}`, title: q.stem,
    qtype: q.type === 'text' ? 'open' : 'mcq', options: q.options ?? null,
    difficulty: 3, skill: q.context, rubric_visible: false, answer: null,
  }));
  const q = qs[idx];
  const isText = q.qtype === 'open' || q.qtype === 'text' || (q as any).type === 'text';
  const answered = isText ? String(answers[idx] ?? '').trim().length > 0 : (answers[idx] as number) >= 0;

  // 切题时保存到后端
  const saveAnswer = async (orderNo: number) => {
    if (!exam) return;
    const a = answers[orderNo - 1];
    try {
      if (isText) {
        await api.post(`/exams/${exam.id}/answers`, { order_no: orderNo, text_answer: String(a ?? '') });
      } else {
        await api.post(`/exams/${exam.id}/answers`, { order_no: orderNo, choice_index: a });
      }
    } catch { /* 离线降级: 静默 */ }
  };

  const mm = String(Math.floor(secs / 60)).padStart(2, '0');
  const ss = String(secs % 60).padStart(2, '0');

  const toggleRecord = () => {
    if (recording) {
      setRecording(false);
      return;
    }
    setRecording(true);
    recTimer.current = setTimeout(() => {
      setRecording(false);
      setAnswers((a) => {
        const next = [...a];
        next[idx] = (next[idx] as string) + (next[idx] ? ' ' : '') + '（语音转写）我认为应该先写入 observation 再决定下一步，同时考虑退避与幂等。';
        return next;
      });
    }, 2200);
  };

  useEffect(() => () => { if (recTimer.current) clearTimeout(recTimer.current); }, []);

  const doAbort = async () => {
    setAbortOpen(false);
    if (exam) {
      try { await api.post(`/exams/${exam.id}/abort`); } catch { /* 尽力而为 */ }
    }
    navigate('/exam');
  };

  const submit = async () => {
    setConfirmOpen(false);
    setPhase('scoring');
    // 保存最后一题 + 提交
    if (exam) {
      try {
        await saveAnswer(qs[idx].order_no);
        await api.post(`/exams/${exam.id}/submit`);
        setTimeout(() => navigate(`/exam/diagnosis/report?id=${exam.id}`), 2400);
        return;
      } catch { /* 降级到 demo 报告 */ }
    }
    setTimeout(() => navigate('/exam/diagnosis/report'), 2400);
  };

  if (phase === 'loading') {
    return (
      <div>
        <div className="quiz-top">
          <div>
            <div className="page-title" style={{ fontSize: 22 }}>独立诊断</div>
            <div className="page-sub">正在生成自适应试卷…</div>
          </div>
        </div>
        <div className="score-box">
          <div className="score-ring">
            <svg width="110" height="110" viewBox="0 0 110 110">
              <circle cx="55" cy="55" r="46" fill="none" stroke="#e8e8f5" strokeWidth="8" />
              <circle className="ring-spin" cx="55" cy="55" r="46" fill="none" stroke="var(--primary)" strokeWidth="8" strokeLinecap="round"
                strokeDasharray="289" strokeDashoffset="80" transform="rotate(-90 55 55)" />
            </svg>
            <div className="ring-label">选题中</div>
          </div>
          <h3 style={{ fontSize: 18, fontWeight: 650, marginTop: 18 }}>自适应选题引擎运行中…</h3>
          <p className="hint" style={{ textAlign: 'center' }}>优先覆盖状态未知 / 待验证 / 低于目标的能力域</p>
        </div>
      </div>
    );
  }

  if (phase === 'scoring') {
    return (
      <div>
        <div className="quiz-top">
          <div>
            <div className="page-title" style={{ fontSize: 22 }}>独立诊断</div>
            <div className="page-sub">正在评分答卷…</div>
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
            <div className="ring-label">评分中</div>
          </div>
          <h3 style={{ fontSize: 18, fontWeight: 650, marginTop: 18 }}>评分引擎正在对照 rubric 评分…</h3>
          <p className="hint" style={{ textAlign: 'center' }}>每题引用对应证据，评分完成后将生成诊断报告</p>
          <div className="steps" style={{ maxWidth: 520, margin: '22px auto 0' }}>
            <div className="step done"><span className="step-ic">✓</span>提交答卷</div>
            <div className="step done"><span className="step-ic">✓</span>冻结转录</div>
            <div className="step now"><span className="step-ic">…</span>对照 rubric 评分</div>
            <div className="step"><span className="step-ic">·</span>生成报告</div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="quiz-top">
        <div>
          <div className="page-title" style={{ fontSize: 22 }}>独立诊断</div>
          <div className="page-sub">
            题 {idx + 1}/{qs.length} · {q.skill || q.title.slice(0, 24)}
            {exam && <span className="chip chip-green" style={{ marginLeft: 8 }}>真实选题</span>}
            {!exam && createErr && <span className="chip chip-amber" style={{ marginLeft: 8 }}>后端不可用 · 演示试卷</span>}
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
      <DemoBanner />

      <div className="quiz-card">
        <div className="quiz-q-head">
          <span className="chip chip-purple">第 {idx + 1} 题</span>
          <span className="chip chip-slate">{isText ? '文本作答' : '单选'}</span>
          <span className="chip chip-slate">{q.short_code}</span>
          {isText && <span className="chip chip-amber">建议 3-5 分钟</span>}
        </div>
        <h3 className="quiz-stem">{q.title}</h3>

        {isText ? (
          <div>
            <textarea
              className="textarea"
              style={{ minHeight: 150, marginTop: 16 }}
              placeholder="输入你的回答…（支持语音输入）"
              value={String(answers[idx] ?? '')}
              onChange={(e) => setAnswers((a) => { const n = [...a]; n[idx] = e.target.value; return n; })}
            />
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10 }}>
              <button className={`btn ${recording ? 'btn-danger' : 'btn-outline'}`} onClick={toggleRecord}>
                {recording ? <span className="rec-pulse" /> : '🎙️'} {recording ? '录音中…点击停止' : '语音输入'}
              </button>
              {recording && <span className="hint">正在聆听并转写，请口述你的回答</span>}
            </div>
          </div>
        ) : (
          <div className="opts" style={{ marginTop: 18 }}>
            {(q.options || []).map((op, i) => (
              <div
                className={`opt ${answers[idx] === i ? 'sel' : ''}`}
                key={op}
                onClick={() => setAnswers((a) => { const n = [...a]; n[idx] = i; return n; })}
              >
                <span className="opt-radio">{answers[idx] === i ? '●' : '○'}</span>
                <span>{op}</span>
              </div>
            ))}
          </div>
        )}

        <div className="quiz-nav">
          <button className="btn btn-outline" disabled={idx === 0} onClick={() => { saveAnswer(qs[idx].order_no); setIdx(idx - 1); }}>← 上一题</button>
          <div style={{ display: 'flex', gap: 8 }}>
            {qs.map((_, i) => (
              <span key={i} className={`dot-q ${i === idx ? 'now' : answers[i] !== undefined && (typeof answers[i] === 'number' ? answers[i] >= 0 : (answers[i] as string).length > 0) ? 'done' : ''}`} />
            ))}
          </div>
          {idx < qs.length - 1 ? (
            <button className="btn btn-primary" disabled={!answered} onClick={() => { saveAnswer(qs[idx].order_no); setIdx(idx + 1); }}>下一题 →</button>
          ) : (
            <button className="btn btn-primary" disabled={!answered} onClick={() => setConfirmOpen(true)}>提交答卷</button>
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
          <p className="hint" style={{ marginTop: 6 }}>提示：只是想跳出可以先交卷，报告会照常生成。</p>
        </Modal>
      )}
      {confirmOpen && (
        <Modal title="提交答卷" onClose={() => setConfirmOpen(false)} size="sm"
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setConfirmOpen(false)}>再检查一下</button>
              <button className="btn btn-primary" onClick={submit}>确认提交</button>
            </>
          }>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.7 }}>
            共 <b>{qs.length}</b> 题已完成，提交后将进入评分流程。
            答卷会作为证据写入你的能力画像，并同步生成今日学习任务。
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
