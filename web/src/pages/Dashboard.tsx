import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { DemoBanner, PageHead, SegBars, StatusChip, StatCard, plainLevel } from '../components/ui';
import { useApi, DashboardData } from '../api/client';
import { DiagnosisModelReadiness } from './Onboarding';


const TYPE_CLS: Record<string, string> = { UNDERSTAND: 'chip-blue', PRACTICE: 'chip-purple', RETEST: 'chip-amber' };
const TYPE_NAME: Record<string, string> = { UNDERSTAND: '理解', PRACTICE: '实践', RETEST: '复测' };

type DashboardWithRetests = DashboardData & {
  due_retests: { id: number; title: string; due_date: string; parent_id: number; parent_title: string }[];
};

export default function Dashboard() {
  const navigate = useNavigate();
  const { data, loading, error } = useApi<DashboardWithRetests>('/dashboard');

  const domains = data?.domains ?? [];
  const dueRetests = data?.due_retests ?? [];
  const nextRetest = dueRetests[0];
  const stats = data?.stats;
  const statCards = [
    { label: '证据覆盖', value: stats?.evidence_total ?? '—', delta: '', ic: '🗂' },
    { label: '待验证能力', value: stats?.pending_skills ?? '—', delta: '', ic: '⚠️' },
    { label: '今日任务', value: stats?.today_tasks ?? '—', delta: '', ic: '📌' },
    { label: '待审核面经', value: stats?.pending_imports ?? '—', delta: '', ic: '📥' },
  ];
  const todayTasks = (data?.today_tasks ?? []).map(t => ({ id: t.id, title: t.title, kind: t.kind, minutes: t.est_minutes, domain: t.skill_name || '' }));
  const pendingImports = (data?.pending_imports ?? []).map(p => ({ id: p.short_code, title: `面经导入 ${p.short_code}`, items: p.items ?? 0, time: (p.created_at || '').slice(0, 10) }));
  const timeline = (data?.recent_activity ?? []).map(a => ({ date: (a.completed_at || '').slice(0, 10), text: `${a.title} · ${a.status || ''}${a.score != null ? ` · 得分 ${Math.round(a.score)}` : ''}` }));
  const gap = data?.gap == null ? '未知' : data.gap.toFixed(1);
  const wsName = data?.workspace.name ?? '当前工作空间';
  const role = data?.workspace.target_role || '待设置';
  const level = data?.workspace.target_level || '待设置';
  const jdCount = data?.benchmark?.jd_sample_count ?? '—';
  const benchVersion = data?.benchmark?.version ?? '—';
  const evidenceTotal = stats?.evidence_total ?? '—';

  // ---- v3.1 M2: 最重要缺口(权重 × 差距) ----
  const gaps = domains
    .filter((d: any) => d.level != null && d.target_level != null && d.target_level > d.level)
    .map((d: any) => ({ ...d, priority: (d.weight_pct / 100) * (d.target_level - d.level) }))
    .sort((a: any, b: any) => b.priority - a.priority);
  const topGap = gaps[0];
  const plainStatus = topGap
    ? `你在「${topGap.name}」上离目标最远——先补这里收益最大。`
    : '尚无可排序的能力差距；未知状态需要独立作答证据，不能据此判断已达标。';
  const defaultCta = todayTasks.length
    ? { label: '开始今天的学习', to: '/learning' }
    : { label: '开始能力诊断', to: '/exam/diagnosis' };
  const primaryCta = nextRetest
    ? { label: '去做已到期的复测', to: `/learning/task/${nextRetest.id}` }
    : defaultCta;

  return (
    <div>
      <PageHead
        title="把下一步，变得更清晰"
        sub={`目标 ${role} · 对标 ${level} · 基于 ${evidenceTotal} 条证据`}
      >
        <button className="btn btn-outline" onClick={() => navigate('/bank?import=1')}>
          📥 导入面经
        </button>
        <button className="btn btn-primary" onClick={() => navigate(primaryCta.to)}>
          ▶ {primaryCta.label}
        </button>
      </PageHead>
      <DemoBanner />

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>正在从后端加载实时数据…</div>}
      {error && <div className="demo-banner demo-banner-err" style={{ marginBottom: 12 }}>后端不可用（{error}），无法读取当前空间数据，请稍后重试</div>}
      {data?.workspace.is_demo === false && data.recent_activity?.length === 0 && <DiagnosisModelReadiness />}

      {/* 深色 hero 卡: 目标 + 一句通俗状态 + 差距 */}
      <div className="hero-card" style={{ marginBottom: 16 }}>
        <div className="hero-top">
          <div>
            <div className="hero-label">当前目标 · {wsName}</div>
            <div className="hero-title">
              {role}
              <span className="hero-chip">对标 {level}</span>
            </div>
            <div className="hero-plain">{plainStatus}</div>
          </div>
          <div className="hero-score">
            <div className="big">差距 {gap}<span>/5</span></div>
            <div className="note">加权能力差距</div>
          </div>
        </div>
        <div className="hero-body">
          <SegBars items={domains.map((d: any) => ({ name: d.name, current: d.level, weight: d.weight_pct }))} />
        </div>
        <div className="hero-foot">
          <span className="note">评估详情：{jdCount} 份 JD · 种子假设 · benchmark {benchVersion}</span>
          <button className="btn btn-outline btn-sm" style={{ background: 'rgba(255,255,255,0.08)', borderColor: 'rgba(255,255,255,0.2)', color: '#fff' }} onClick={() => navigate('/exam/diagnosis')}>
            开始一次独立诊断 →
          </button>
        </div>
      </div>

      <div className="grid grid-2-1">
        {/* 左列: 行动聚焦 */}
        <div className="stack" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* 最重要缺口 */}
          <div className="card gap-focus">
            <div className="card-head">
              <span className="card-title">
                <span className="chip chip-red" style={{ marginRight: 8 }}>最重要缺口</span>
                {topGap ? topGap.name : '暂无法判断整体缺口'}
              </span>
            </div>
            {topGap ? (
              <>
                <div className="gap-focus-plain">
                  现在的水平：{plainLevel(topGap.level)}（{topGap.level.toFixed(1)}/5，目标 {topGap.target_level}）。
                  这个能力在目标岗位权重 {topGap.weight_pct}%，补上它对缩小整体差距收益最大。
                </div>
                <div className="gap-focus-foot">
                  <StatusChip status={topGap.status} />
                  <button className="btn btn-primary btn-sm" onClick={() => navigate('/learning')}>
                    开始补这个缺口 →
                  </button>
                </div>
              </>
            ) : (
              <div className="gap-focus-plain">能力估计可能仍为未知。已有作答中的具体失分项可在报告和学习任务中查看。</div>
            )}
          </div>

          {dueRetests.length > 0 && (
            <div className="card">
              <div className="card-head">
                <span className="card-title">到期复测</span>
              </div>
              {dueRetests.map(retest => (
                <div className="list-row" key={retest.id}>
                  <div className="list-main">
                    <Link className="link list-title" to={`/learning/task/${retest.id}`}>{retest.title}</Link>
                    <div className="list-meta">
                      <span>到期日 {retest.due_date}</span>
                      <Link className="link" to={`/learning/task/${retest.parent_id}`}>{retest.parent_title}</Link>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* 今日任务(1-3) */}
          <div className="card">
            <div className="card-head">
              <span className="card-title">今日任务 <span className="chip chip-purple" style={{ marginLeft: 6 }}>{todayTasks.length}</span></span>
              <Link to="/learning" className="link">全部任务 →</Link>
            </div>
            {todayTasks.length === 0 && (
              <div className="list-row">
                <div className="list-ic">🎉</div>
                <div className="list-main">
                  <div className="list-title">今天没有待办任务</div>
                  {!nextRetest && <div className="list-sub">做一次诊断，让系统重新规划</div>}
                </div>
                {!nextRetest && (
                  <button className="btn btn-primary btn-sm" onClick={() => navigate('/exam/diagnosis')}>开始诊断</button>
                )}
              </div>
            )}
            {todayTasks.slice(0, 3).map((t: any) => (
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

          {/* 次级统计行 */}
          <div className="stat-row">
            {statCards.map((s) => (
              <div className="stat-mini" key={s.label}>
                <div className="stat-mini-label">{s.ic} {s.label}</div>
                <div className="stat-mini-value">{s.value}{s.delta && <small className="stat-delta delta-up">{s.delta}</small>}</div>
              </div>
            ))}
          </div>
        </div>

        {/* 右列: 最近进展 + 待审核 */}
        <div className="stack" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="card">
            <div className="card-head">
              <span className="card-title">最近一次进展</span>
            </div>
            {timeline[0] ? (
              <div className="tl-item" style={{ padding: '4px 4px 0' }}>
                <div className="tl-date">{timeline[0].date}</div>
                <div className="tl-text">{timeline[0].text}</div>
              </div>
            ) : (
              <div className="list-row">
                <div className="list-ic">🌱</div>
                <div className="list-main">
                  <div className="list-title">还没有记录</div>
                  <div className="list-sub">完成第一个任务后出现在这里</div>
                </div>
              </div>
            )}
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
