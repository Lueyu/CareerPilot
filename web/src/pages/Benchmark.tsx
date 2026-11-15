import React from 'react';
import { useNavigate } from 'react-router-dom';
import { DemoBanner, PageHead, SegBars, StatusChip, plainConfidence, plainLevel } from '../components/ui';
import { useApi, BenchmarkData } from '../api/client';
import { WS, DOMAINS } from '../data/demo';

export default function Benchmark() {
  const navigate = useNavigate();
  const { data, loading, error } = useApi<BenchmarkData>('/benchmark');

  const rows = data?.rows?.length
    ? data.rows.map((r: any) => ({
        skill_key: r.skill?.key ?? r.skill_key,
        skill: r.skill?.name ?? r.skill,
        weight_pct: r.weight_pct,
        target_level: r.target_level,
        current_level: r.current_level,
        status: r.status as string,
        evidence_coverage: r.evidence_coverage,
        confidence: r.confidence,
        next_step: r.next_step,
      }))
    : DOMAINS.map((d) => ({
        skill_key: d.key, skill: d.name, weight_pct: d.weight, target_level: d.target,
        current_level: d.current, status: d.status as string, evidence_coverage: d.evidence,
        confidence: d.confidence, next_step: d.next,
      }));

  const role = data?.standard?.role || WS.role;
  const level = data?.standard?.level || WS.level;
  const jd = data?.standard?.jd_sample_count ?? WS.jdCount;
  const version = data?.standard?.version ? `benchmark ${data.standard.version}` : WS.benchmark;

  const nBelow = rows.filter((r) => r.status === 'BELOW_TARGET' || r.status === 'NEEDS_VALIDATION' || r.status === 'UNKNOWN').length;
  const nMeets = rows.filter((r) => r.status === 'MEETS_TARGET').length;
  const nAbove = rows.filter((r) => r.status === 'ABOVE_TARGET').length;

  // 加权差距
  const wSum = rows.reduce((s, r) => s + r.weight_pct, 0) || 1;
  const gap = rows.reduce((s, r) => s + r.weight_pct * Math.max(0, r.target_level - (r.current_level ?? 0)), 0) / wSum;

  return (
    <div>
      <PageHead
        title="岗位对标"
        sub="以真实 JD 为基准，量化你的能力差距 · 种子假设，非统计结论"
      >
        <button className="btn btn-outline" onClick={() => navigate('/benchmark')}>↻ 更新对标</button>
        <button className="btn btn-primary" onClick={() => navigate('/exam/diagnosis')}>开始独立诊断</button>
      </PageHead>
      <DemoBanner text="DEMO · 对标权重为种子假设（benchmark v1.0），不代表行业统计。" />

      {/* 对标总卡 */}
      <div className="card" style={{ marginBottom: 18 }}>
        <div className="bench-head">
          <div>
            <div className="hero-label" style={{ color: 'var(--text-4)' }}>当前目标岗位</div>
            <h2 style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>
              {role}
              <span className="chip chip-purple" style={{ marginLeft: 10 }}>对标 {level}</span>
            </h2>
            <div className="bench-meta">
              <span>公司样本 <b>{jd}</b> 份 JD</span>
              <i className="dot-sep" />
              <span>权重来源 <b>种子假设</b></span>
              <i className="dot-sep" />
              <span>{version}</span>
            </div>
          </div>
          <div className="bench-gap">
            <div className="big">{gap.toFixed(1)}<span>/5</span></div>
            <div className="note">加权能力差距</div>
          </div>
        </div>
        <div className="grid grid-4" style={{ marginTop: 20 }}>
          <div className="mini-stat"><b>{rows.length}</b><span>能力域</span></div>
          <div className="mini-stat"><b>{nBelow}</b><span>低于目标/待验证</span></div>
          <div className="mini-stat"><b>{nMeets}</b><span>达到目标</span></div>
          <div className="mini-stat"><b>{nAbove}</b><span>高于目标</span></div>
        </div>
      </div>

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>正在从后端加载实时数据…</div>}
      {error && <div className="demo-banner demo-banner-err" style={{ marginBottom: 12 }}>后端不可用（{error}），以下为静态演示数据</div>}

      <div className="card">
        <div className="card-head">
          <span className="card-title">能力域对标明细</span>
          <span className="card-sub">权重 × 差距 = 优先学习顺序</span>
        </div>
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>能力项</th>
                <th>权重</th>
                <th>目标等级</th>
                <th>当前估计</th>
                <th>状态</th>
                <th>证据覆盖</th>
                <th>置信度</th>
                <th>下一步</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.skill_key}>
                  <td className="strong" style={{ cursor: 'pointer' }} onClick={() => navigate(`/profile/${r.skill_key}`)}>{r.skill}</td>
                  <td className="num">{r.weight_pct}%</td>
                  <td className="num">{r.target_level}</td>
                  <td>
                    <span className="num">{r.current_level != null ? r.current_level.toFixed(2) : '—'}</span>
                    <div className="plain-hint">{plainLevel(r.current_level)}</div>
                  </td>
                  <td><StatusChip status={r.status} /></td>
                  <td className="num">{r.evidence_coverage} 条</td>
                  <td>
                    <span className="num">{r.confidence != null ? r.confidence.toFixed(2) : '—'}</span>
                    <div className="plain-hint">{plainConfidence(r.confidence)}</div>
                  </td>
                  <td><span className="action-link" onClick={() => navigate('/learning')}>{r.next_step}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card mt-18">
        <div className="card-head">
          <span className="card-title">差距加权排序</span>
          <span className="card-sub">优先投入：权重高 × 差距大</span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {[...rows]
            .map((r) => ({ ...r, priority: r.weight_pct * Math.max(0, r.target_level - (r.current_level ?? 0)) }))
            .sort((a, b) => b.priority - a.priority)
            .map((r) => (
              <div key={r.skill_key} className="bench-row">
                <div className="bench-row-name">{r.skill}</div>
                <div className="bench-bar">
                  <div className="bench-bar-fill" style={{ width: `${Math.min(100, (r.priority / 40) * 100)}%`, background: r.status === 'MEETS_TARGET' || r.status === 'ABOVE_TARGET' ? 'var(--green)' : 'var(--accent-grad)' }} />
                </div>
                <div className="bench-row-val">{r.priority.toFixed(1)}</div>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}
