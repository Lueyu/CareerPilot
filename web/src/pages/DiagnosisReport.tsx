import React, { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { DemoBanner, PageHead, plainLevel } from '../components/ui';
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
  const [deepBusy, setDeepBusy] = useState(false);
  const [deepErr, setDeepErr] = useState<string | null>(null);
  const toggle = (i: number) =>
    setOpen((o) => (o.includes(i) ? o.filter((x) => x !== i) : [...o, i]));

  const genDeep = async () => {
    if (!report) return;
    setDeepBusy(true);
    setDeepErr(null);
    try {
      const deep = await api.post<ExamReportData['diagnosis_deep']>(`/exams/${report.id}/report/deep`);
      setReport({ ...report, diagnosis_deep: deep, has_deep: true });
    } catch (e: any) {
      setDeepErr(String(e.message || e));
    } finally {
      setDeepBusy(false);
    }
  };

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
    const deep = r.diagnosis_deep;
    const ob = r.overall;
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
            <div className="hint" style={{ color: 'rgba(255,255,255,0.75)', marginTop: 6 }}>
              通俗地说：{r.score >= 85 ? '表现很好，这一域基本不用补' : r.score >= 70 ? '达到基本线，保持并查漏' : r.score >= 50 ? '有基础但还不够稳，建议优先补' : '这一块明显薄弱，优先补齐'}
            </div>
          </div>
          <div className="report-score">
            {Math.round(r.score)}<span>/100</span>
            <div className="note" style={{ fontSize: 12, color: 'rgba(255,255,255,0.5)', marginTop: 2 }}>本次答卷得分</div>
          </div>
        </div>

        {/* 诊断结论(确定性) */}
        {ob && (
          <div className="card" style={{ marginBottom: 18 }}>
            <div className="card-head">
              <span className="card-title">诊断结论</span>
              <span className="card-sub">基于本次答卷的确定性判定 · 评分 rule-v1</span>
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
        {!!r.skill_breakdown?.length && (
          <div className="card" style={{ marginBottom: 18 }}>
            <div className="card-head">
              <span className="card-title">能力域得分</span>
              <span className="card-sub">按本次各题得分率聚合 · 薄弱的排前面</span>
            </div>
            <div style={{ display: 'grid', gap: 12 }}>
              {r.skill_breakdown.map((g) => {
                const pct = Math.round(g.ratio * 100);
                const color = g.ratio >= 0.8 ? 'var(--green)' : g.ratio >= 0.6 ? 'var(--amber)' : 'var(--red)';
                return (
                  <div key={g.skill_key} style={{ display: 'grid', gridTemplateColumns: '150px 1fr 130px', gap: 14, alignItems: 'center' }}>
                    <div>
                      <div style={{ fontSize: 13.5, fontWeight: 600 }}>{g.skill_name}</div>
                      <div className="hint" style={{ margin: 0 }}>{g.q_count} 题 · Q{g.order_nos.join(' Q')}</div>
                    </div>
                    <div>
                      <div style={{ height: 8, borderRadius: 4, background: 'var(--surface-3)', overflow: 'hidden' }}>
                        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 4, transition: 'width .4s ease' }} />
                      </div>
                    </div>
                    <div style={{ fontSize: 12.5, color: 'var(--text-3)', textAlign: 'right' }}>
                      <b style={{ color }}>{g.earned}/{g.max}</b>
                      <span style={{ marginLeft: 8 }}>✓{g.verdicts.CORRECT} △{g.verdicts.PARTIAL} ✗{g.verdicts.WRONG}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* 深度诊断(LLM) */}
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
                AI 会逐题阅读你的作答与参考答案，指出差在哪、正确姿势是什么，并给出优先级行动清单（约 30 秒）。
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
              <div style={{ fontSize: 13, color: 'var(--text-3)' }}>AI 正在逐题分析你的作答…（约 30 秒，不要关闭页面）</div>
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

        <div className="card-head" style={{ margin: '0 0 12px' }}>
          <span className="card-title">逐题评分</span>
        </div>
        {r.questions.map((q, i) => {
          const a = q.answer;
          const ok = a?.verdict === 'CORRECT';
          const dq = deep?.per_question?.[String(q.order_no)];
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
                  {a?.feedback && (
                    <>
                      <p>
                        <b>命中要点：</b>
                        {a.feedback.hit_points?.length
                          ? a.feedback.hit_points.map((p: any) => (typeof p === 'string' ? p : p.title)).join('、')
                          : '无'}
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
                  {q.reference_answer && (
                    <div style={{ marginTop: 12, borderTop: '1px dashed var(--border-2)', paddingTop: 10 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>📖 参考答案（正确应该答到什么）</div>
                      <div className="bank-answer" dangerouslySetInnerHTML={{ __html: q.reference_answer }} />
                    </div>
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
      {err && <div className="demo-banner demo-banner-err">后端不可用或暂无已完成考试（{err}），以下为静态演示报告</div>}
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
          <div className="hint" style={{ color: 'rgba(255,255,255,0.75)', marginTop: 6 }}>
            通俗地说：{plainLevel(2.7)}
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
