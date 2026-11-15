import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { DemoBanner, PageHead, StatCard, SegBars, StatusChip } from '../components/ui';
import { api, useApi, DashboardData } from '../api/client';
import { WS, STATS, DOMAINS, TODAY_TASKS, TIMELINE, PENDING_REVIEWS } from '../data/demo';

const TYPE_CLS: Record<string, string> = { UNDERSTAND: 'chip-blue', PRACTICE: 'chip-purple', RETEST: 'chip-amber' };
const TYPE_NAME: Record<string, string> = { UNDERSTAND: '理解', PRACTICE: '实践', RETEST: '复测' };

export default function Dashboard() {
  const navigate = useNavigate();
  const { data, loading, error } = useApi<DashboardData>('/dashboard');

  // ---- 数据源: API 优先, 失败/未加载时降级到 demo 静态数据 ----
  const domains = data?.domains?.length ? data.domains : DOMAINS.map((d) => ({
    key: d.key, name: d.name, weight_pct: d.weight, level: d.current, target_level: d.target,
    status: d.status, confidence: d.confidence, evidence_coverage: d.evidence,
  }));
  const stats = data?.stats;
  const statCards = stats
    ? [
        { label: '证据覆盖', value: String(stats.evidence_total), delta: '+3', dir: 'up' as const, foot: '近 7 天新增', ic: '🗂' },
        { label: '待验证能力', value: String(stats.pending_skills), foot: '需要更多证据', ic: '⚠️' },
        { label: '今日任务', value: String(stats.today_tasks), foot: '由差距驱动生成', ic: '📌' },
        { label: '待审核面经', value: String(stats.pending_imports), foot: '导入后等待确认', ic: '📥' },
      ]
    : STATS.map((s) => ({ label: s.label, value: s.value, delta: s.delta, dir: 'up' as const, foot: s.foot, ic: s.ic }));

  const todayTasks = data?.today_tasks?.length
    ? data.today_tasks.map((t) => ({
        id: t.id, title: t.title, kind: t.kind, minutes: t.est_minutes,
        domain: t.skill_name || '',
      }))
    : TODAY_TASKS.map((t) => ({ id: t.id, title: t.title, kind: t.type, minutes: t.minutes, domain: t.domain }));

  const pendingImports = data?.pending_imports?.length
    ? data.pending_imports.map((p) => ({ id: p.short_code, title: `面经导入 ${p.short_code}`, items: p.items ?? 0, time: (p.created_at || '').slice(0, 10) }))
    : PENDING_REVIEWS.map((p) => ({ id: p.id, title: p.title, items: p.items, time: p.time }));

  const timeline = data?.recent_activity?.length
    ? data.recent_activity.map((a) => ({
        date: (a.completed_at || '').slice(5, 10) || '今天',
        text: `<b>${a.title}</b> 完成${a.score != null ? ` · 得分 ${Math.round(a.score)}` : ''}`,
      }))
    : TIMELINE;

  const gap = data?.gap != null ? data.gap.toFixed(1) : String(WS.gap);
  const wsName = data?.workspace?.name || WS.name;
  const role = data?.workspace?.target_role || WS.role;
  const level = data?.workspace?.target_level || WS.level;
  const jdCount = data?.benchmark?.jd_sample_count ?? WS.jdCount;
  const benchVersion = data?.benchmark?.version ?? WS.benchmark;
  const evidenceTotal = stats?.evidence_total ?? WS.evidence;

  return (
    <div>
      <PageHead
        title="把下一步，变得更清晰"
        sub={`基于 ${evidenceTotal} 条证据的岗位差距分析 · ${new Date().toISOString().slice(0, 10)} 更新`}
      >
        <button className="btn btn-outline" onClick={() => navigate('/bank?import=1')}>
          📥 导入面经
        </button>
        <button className="btn btn-primary" onClick={() => navigate('/exam/diagnosis')}>
          🎯 开始独立诊断
        </button>
      </PageHead>
      <DemoBanner />

      <div className="grid grid-4" style={{ marginBottom: 16 }}>
        {statCards.map((s) => (
          <StatCard key={s.label} label={s.label} value={s.value} delta={s.delta} dir={s.dir as 'up'} foot={s.foot} ic={s.ic} />
        ))}
      </div>

      {/* 深色 hero 卡 */}
      <div className="hero-card" style={{ marginBottom: 16 }}>
        <div className="hero-top">
          <div>
            <div className="hero-label">当前目标 · {wsName}</div>
            <div className="hero-title">
              {role}
              <span className="hero-chip">对标 {level}</span>
            </div>
          </div>
          <div className="hero-score">
            <div className="big">差距 {gap}<span>/5</span></div>
            <div className="note">加权能力差距</div>
          </div>
        </div>
        <div className="hero-body">
          <SegBars items={domains.map((d: any) => ({ name: d.name, current: d.level ?? 0, weight: d.weight_pct }))} />
          <div className="seg-gap">
            <span>权重：</span>
            {domains.map((d: any) => (
              <span key={d.key} style={{ color: 'rgba(255,255,255,0.75)' }}>{d.name.split(' ')[0]} {d.weight_pct}%</span>
            ))}
          </div>
        </div>
        <div className="hero-foot">
          <span className="note">来自 {jdCount} 份 JD · 种子假设 · benchmark {benchVersion}</span>
          <button className="btn btn-outline btn-sm" style={{ background: 'rgba(255,255,255,0.08)', borderColor: 'rgba(255,255,255,0.2)', color: '#fff' }} onClick={() => navigate('/exam/diagnosis')}>
            开始一次独立诊断 →
          </button>
        </div>
      </div>

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>正在从后端加载实时数据…</div>}
      {error && <div className="demo-banner" style={{ marginBottom: 12, background: '#fef2f2', color: '#b91c1c' }}>后端不可用（{error}），以下为静态演示数据</div>}

      <div className="grid grid-2-1">
        {/* 左列 */}
        <div className="stack" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="card">
            <div className="card-head">
              <span className="card-title">岗位能力差距</span>
              <Link to="/benchmark" className="link">查看详情 →</Link>
            </div>
            <div className="table-wrap">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>能力项</th>
                    <th>当前估计</th>
                    <th>目标</th>
                    <th>状态</th>
                    <th>置信度</th>
                    <th>证据</th>
                  </tr>
                </thead>
                <tbody>
                  {domains.slice(0, 6).map((d: any) => (
                    <tr key={d.key}>
                      <td className="strong">{d.name}</td>
                      <td className="num">{d.level != null ? d.level.toFixed(2) : '—'}</td>
                      <td className="num">{d.target_level ?? '—'}</td>
                      <td><StatusChip status={d.status} /></td>
                      <td className="num">{d.confidence != null ? d.confidence.toFixed(2) : '—'}</td>
                      <td className="num">{d.evidence_coverage}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <span className="card-title">今日任务 <span className="chip chip-purple" style={{ marginLeft: 6 }}>{todayTasks.length}</span></span>
              <Link to="/learning" className="link">全部任务 →</Link>
            </div>
            {todayTasks.map((t: any) => (
              <div className="list-row" key={t.id}>
                <div className="list-ic">📌</div>
                <div className="list-main">
                  <div className="list-title">{t.title}</div>
                  <div className="list-meta">
                    <span className={`chip ${TYPE_CLS[t.kind] || 'chip-slate'}`}>{TYPE_NAME[t.kind] || t.kind}</span>
                    <span>{t.minutes} 分钟</span>
                    {t.domain && <span>{t.domain}</span>}
                  </div>
                </div>
                <button className="btn btn-outline btn-sm" onClick={() => navigate(`/learning/task/${t.id}`)}>开始</button>
              </div>
            ))}
          </div>
        </div>

        {/* 右列 */}
        <div className="stack" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="card">
            <div className="card-head">
              <span className="card-title">近期进展</span>
            </div>
            <div className="timeline" style={{ padding: '6px 4px 0' }}>
              {timeline.map((t: any, i: number) => (
                <div className="tl-item" key={i}>
                  <div className="tl-date">{t.date}</div>
                  <div className="tl-text" dangerouslySetInnerHTML={{ __html: t.text }} />
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <span className="card-title">待审核面经 <span className="chip chip-amber" style={{ marginLeft: 6 }}>{pendingImports.length}</span></span>
              <Link to="/bank" className="link">去审核 →</Link>
            </div>
            {pendingImports.length === 0 && (
              <div className="list-row">
                <div className="list-ic">✅</div>
                <div className="list-main">
                  <div className="list-title">没有待审核的导入</div>
                  <div className="list-sub">从面经库页导入新的面经</div>
                </div>
              </div>
            )}
            {pendingImports.map((p: any) => (
              <div className="list-row" key={p.id}>
                <div className="list-ic">📄</div>
                <div className="list-main">
                  <div className="list-title">{p.title}</div>
                  <div className="list-sub">{p.id} · {p.items} 题 · {p.time}</div>
                </div>
                <button className="btn btn-primary btn-sm" onClick={() => navigate('/bank')}>审核</button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
