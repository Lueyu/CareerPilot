import React, { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { DemoBanner, PageHead } from '../components/ui';
import { api, ExamReportData } from '../api/client';
import { QUIZ_REPORT } from '../data/demo';

const VERDICT_CHIP: Record<string, string> = {
  CORRECT: 'chip-green',
  PARTIAL: 'chip-amber',
  WRONG: 'chip-red',
};
const VERDICT_NAME: Record<string, string> = {
  CORRECT: '✓ 正确',
  PARTIAL: '部分得分',
  WRONG: '✗ 未达要点',
};

export default function DiagnosisReport() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [open, setOpen] = useState<number[]>([0]);
  const [report, setReport] = useState<ExamReportData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const toggle = (i: number) =>
    setOpen((o) => (o.includes(i) ? o.filter((x) => x !== i) : [...o, i]));

  // 优先读 ?id=, 否则取最近一次 COMPLETED 考试
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const id = params.get('id');
        if (id) {
          const r = await api.get<ExamReportData>(`/exams/${id}/report`);
          if (alive) setReport(r);
          return;
        }
        const list = await api.get<any>('/exams');
        const done = (list.history || []).find((h: any) => h.status === 'COMPLETED');
        if (done) {
          const r = await api.get<ExamReportData>(`/exams/${done.id}/report`);
          if (alive) setReport(r);
        } else {
          setErr('还没有已完成的考试');
        }
      } catch (e: any) {
        if (alive) setErr(String(e.message || e));
      }
    })();
    return () => { alive = false; };
  }, [params]);

  // 后端报告
  if (report) {
    const r = report;
    return (
      <div>
        <PageHead title="独立诊断报告" sub={`${(r.completed_at || '').replace('T', ' ').slice(0, 16)} · 答卷已冻结并计入证据`}>
          <button className="btn btn-outline" onClick={() => navigate('/exam')}>返回考试中心</button>
          <button className="btn btn-primary" onClick={() => navigate('/learning')}>查看今日任务</button>
        </PageHead>
        <DemoBanner />

        <div className="report-hero">
          <div>
            <div className="hero-label">本次得分 · {r.title}</div>
            <div style={{ fontSize: 24, fontWeight: 700, marginTop: 4 }}>
              {Math.round(r.score)} 分
              <span className="hero-chip" style={{ marginLeft: 10 }}>{r.questions.length} 题 · {r.gained_points}/{r.total_points} 分</span>
            </div>
            <div className="hint" style={{ color: 'rgba(255,255,255,0.55)', marginTop: 8 }}>
              {r.selection_reason}
            </div>
          </div>
          <div className="report-score">
            {Math.round(r.score)}<span>/100</span>
            <div className="note" style={{ fontSize: 12, color: 'rgba(255,255,255,0.5)', marginTop: 2 }}>本次答卷得分</div>
          </div>
        </div>

        <div className="card" style={{ marginBottom: 18 }}>
          <div className="card-head">
            <span className="card-title">能力小结</span>
            <span className="card-sub">答卷已作为证据写入能力画像, 能力估计与学习任务已同步更新</span>
          </div>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.75 }}>
            {r.questions.map((q) => `${q.short_code} ${q.answer?.verdict === 'CORRECT' ? '✓' : q.answer?.verdict === 'PARTIAL' ? '△' : '✗'}(${q.answer?.points ?? 0}/${q.answer?.max_points ?? 5})`).join(' · ')}
          </p>
        </div>

        <div className="card-head" style={{ margin: '0 0 12px' }}>
          <span className="card-title">逐题评分</span>
        </div>
        {r.questions.map((q, i) => {
          const a = q.answer;
          const ok = a?.verdict === 'CORRECT';
          return (
            <div className="report-q" key={q.order_no}>
              <div className="report-q-head" onClick={() => toggle(i)}>
                <span className={`chip ${VERDICT_CHIP[a?.verdict || 'WRONG']}`}>{VERDICT_NAME[a?.verdict || 'WRONG']}</span>
                <span className="report-q-score" style={{ color: ok ? 'var(--green)' : 'var(--amber)' }}>{a?.points ?? 0}<small style={{ fontSize: 13, color: 'var(--text-4)' }}>/{a?.max_points ?? 5}</small></span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 600 }}>Q{q.order_no} · {q.title}</div>
                  <div className="hint" style={{ margin: 0 }}>{q.skill} · 引用证据 {(a?.evidence_refs || []).join(', ') || '—'}</div>
                </div>
                <span style={{ color: 'var(--text-4)', fontSize: 12 }}>{open.includes(i) ? '收起 ▴' : '展开 ▾'}</span>
              </div>
              {open.includes(i) && (
                <div className="report-q-body">
                  {a?.feedback && (
                    <>
                      <p><b>命中要点：</b>{a.feedback.hit_points?.length ? a.feedback.hit_points.join('、') : '无'}</p>
                      <p><b>缺失要点：</b>{a.feedback.missed_points?.length ? a.feedback.missed_points.join('、') : '无'}</p>
                      <p><b>你的回答：</b>{a.text_answer || '（未作答）'}</p>
                      {a.feedback.next && <p><b>建议下一步：</b>{a.feedback.next}</p>}
                    </>
                  )}
                </div>
              )}
            </div>
          );
        })}

        <div className="card" style={{ marginTop: 18, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div>
            <div className="card-title" style={{ margin: 0 }}>下一步</div>
            <p className="hint" style={{ margin: '4px 0 0' }}>证据已写入画像 → 今日学习任务已按差距重新生成</p>
          </div>
          <div style={{ display: 'flex', gap: 10 }}>
            <button className="btn btn-outline" onClick={() => navigate('/exam/diagnosis')}>再测一次</button>
            <button className="btn btn-primary" onClick={() => navigate('/learning')}>开始学习 →</button>
          </div>
        </div>
      </div>
    );
  }

  // 降级: 静态 demo 报告
  const total = QUIZ_REPORT.scores.reduce((a, s) => a + s.score, 0);
  const max = QUIZ_REPORT.scores.reduce((a, s) => a + s.max, 0);
  return (
    <div>
      <PageHead title="独立诊断报告" sub={err ? err : '2026-11-15 14:22 · 答卷已冻结并计入证据'}>
        <button className="btn btn-outline" onClick={() => navigate('/exam')}>返回考试中心</button>
        <button className="btn btn-primary" onClick={() => navigate('/learning')}>查看今日任务</button>
      </PageHead>
      {err && <div className="demo-banner" style={{ background: '#fef2f2', color: '#b91c1c' }}>后端不可用或暂无已完成考试（{err}），以下为静态演示报告</div>}
      <DemoBanner />

      <div className="report-hero">
        <div>
          <div className="hero-label">整体估计（Agent Evaluation）</div>
          <div style={{ fontSize: 24, fontWeight: 700, marginTop: 4 }}>
            {QUIZ_REPORT.overall}
            <span className="hero-chip" style={{ marginLeft: 10 }}>置信度 {QUIZ_REPORT.confidence}</span>
          </div>
          <div className="hint" style={{ color: 'rgba(255,255,255,0.55)', marginTop: 8 }}>
            基于 {QUIZ_REPORT.evidence} 条证据 · 2.7/5
          </div>
        </div>
        <div className="report-score">
          {total}<span>/{max} 分</span>
          <div className="note" style={{ fontSize: 12, color: 'rgba(255,255,255,0.5)', marginTop: 2 }}>本次答卷得分</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 18 }}>
        <div className="card-head">
          <span className="card-title">能力小结</span>
        </div>
        <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.75 }}>{QUIZ_REPORT.summary}</p>
        <p style={{ fontSize: 13, color: 'var(--primary)', lineHeight: 1.75, marginTop: 8 }}>👉 {QUIZ_REPORT.next}</p>
      </div>

      <div className="card-head" style={{ margin: '0 0 12px' }}>
        <span className="card-title">逐题评分</span>
      </div>
      {QUIZ_REPORT.scores.map((s, i) => (
        <div className="report-q" key={s.q}>
          <div className="report-q-head" onClick={() => toggle(i)}>
            <span className={`chip ${s.correct ? 'chip-green' : 'chip-amber'}`}>{s.correct ? '✓ 正确' : '部分得分'}</span>
            <span className="report-q-score" style={{ color: s.correct ? 'var(--green)' : 'var(--amber)' }}>{s.score}<small style={{ fontSize: 13, color: 'var(--text-4)' }}>/{s.max}</small></span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 14, fontWeight: 600 }}>{s.q} · {s.title}</div>
              <div className="hint" style={{ margin: 0 }}>{s.type} · 引用证据 {s.evidence.join(', ')}</div>
            </div>
            <span style={{ color: 'var(--text-4)', fontSize: 12 }}>{open.includes(i) ? '收起 ▴' : '展开 ▾'}</span>
          </div>
          {open.includes(i) && (
            <div className="report-q-body">
              <p><b>评分说明：</b>{s.note}</p>
              <p><b>建议：</b>{s.next}</p>
            </div>
          )}
        </div>
      ))}

      <div className="card" style={{ marginTop: 18, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div className="card-title" style={{ margin: 0 }}>下一步</div>
          <p className="hint" style={{ margin: '4px 0 0' }}>已完成 2 项 → 今日任务已生成，包含 1 项实践与 1 项复测</p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn btn-outline" onClick={() => navigate('/exam/diagnosis')}>再测一次</button>
          <button className="btn btn-primary" onClick={() => navigate('/learning')}>开始学习 →</button>
        </div>
      </div>
    </div>
  );
}
