import { t, useT } from '../i18n';
import { formatProgressDate } from '../lib/coreLoop';
import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { DemoBanner, PageHead, SegBars, StatusChip, plainLevel } from '../components/ui';
import { DashboardCard, DashboardHero, DashboardStats } from '../components/DashboardLayout';
import { useApi, DashboardData } from '../api/client';
import { DiagnosisModelReadiness } from './Onboarding';


const TYPE_CLS: Record<string, string> = { UNDERSTAND: 'chip-blue', PRACTICE: 'chip-purple', RETEST: 'chip-amber' };
const TYPE_NAME: Record<string, string> = {
  get UNDERSTAND() { return t('progress.understanding'); },
  get PRACTICE() { return t('progress.practice'); },
  get RETEST() { return t('progress.retest'); },
};

type DashboardWithRetests = DashboardData & {
  due_retests: { id: number; title: string; due_date: string; parent_id: number; parent_title: string }[];
};

export default function Dashboard() {
  const t = useT();
  const navigate = useNavigate();
  const { data, loading, error } = useApi<DashboardWithRetests>('/dashboard');

  const domains = data?.domains ?? [];
  const dueRetests = data?.due_retests ?? [];
  const nextRetest = dueRetests[0];
  const stats = data?.stats;
  const statCards = [
    { label: t('progress.dashboardEvidenceCoverage'), value: stats?.evidence_total ?? '—', delta: '', ic: '🗂' },
    { label: t('progress.dashboardUnverifiedSkills'), value: stats?.pending_skills ?? '—', delta: '', ic: '⚠️' },
    { label: t('progress.dashboardTodayTasks'), value: stats?.today_tasks ?? '—', delta: '', ic: '📌' },
    { label: t('progress.dashboardPendingImports'), value: stats?.pending_imports ?? '—', delta: '', ic: '📥' },
  ];
  const todayTasks = (data?.today_tasks ?? []).map(t => ({ id: t.id, title: t.title, kind: t.kind, minutes: t.est_minutes, domain: t.skill_name || '' }));
  const pendingImports = (data?.pending_imports ?? []).map(p => ({ id: p.short_code, title: t('progress.dashboardImportActivity', { code: p.short_code }), items: p.items ?? 0, time: formatProgressDate(p.created_at) }));
  const timeline = (data?.recent_activity ?? []).map(a => ({ date: formatProgressDate(a.completed_at), text: a.score == null ? t('progress.dashboardActivity', { title: a.title, status: a.status || '' }) : t('progress.dashboardScoredActivity', { title: a.title, status: a.status || '', score: Math.round(a.score) }) }));
  const gap = data?.gap == null ? t('progress.unknown') : data.gap.toFixed(1);
  const wsName = data?.workspace.name ?? t('progress.dashboardCurrentWorkspace');
  const role = data?.workspace.target_role || t('progress.notSet');
  const level = data?.workspace.target_level || t('progress.notSet');
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
    ? t('progress.dashboardTopGap', { domain: topGap.name })
    : t('progress.dashboardNoRankedGap');
  const defaultCta = todayTasks.length
    ? { label: t('progress.dashboardStartToday'), to: '/learning' }
    : { label: t('progress.dashboardStartDiagnosis'), to: '/exam/diagnosis' };
  const primaryCta = nextRetest
    ? { label: t('progress.dashboardDueRetest'), to: `/learning/task/${nextRetest.id}` }
    : defaultCta;

  return (
    <div>
      <PageHead
        title={t('progress.dashboardHeading')}
        sub={t('progress.dashboardSubtitle', { role: role, level: level, evidence: evidenceTotal })}
      >
        <button className="btn btn-outline" onClick={() => navigate('/bank?import=1')}>
          {t('progress.dashboardImport')}
        </button>
        <button className="btn btn-primary" onClick={() => navigate(primaryCta.to)}>
          ▶ {primaryCta.label}
        </button>
      </PageHead>
      <DemoBanner />

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>{t('progress.loadingLive')}</div>}
      {error && <div className="demo-banner demo-banner-err" style={{ marginBottom: 12 }}>{t('progress.workspaceLoadError', { error })}</div>}
      {data?.workspace.is_demo === false && data.recent_activity?.length === 0 && <DiagnosisModelReadiness />}

      <DashboardHero
        label={t('progress.dashboardCurrentGoal', { workspace: wsName })}
        title={role}
        badge={t('progress.dashboardBenchmark', { level: level })}
        summary={plainStatus}
        score={<>
          <div className="big">{t('progress.dashboardGap') + ' '}{gap}<span>/5</span></div>
          <div className="note">{t('progress.dashboardWeightedGap')}</div>
        </>}
        footer={<>
          <span className="note">{t('progress.dashboardAssessmentDetails', { count: jdCount, version: benchVersion })}</span>
          <button className="btn btn-outline btn-sm" style={{ background: 'rgba(255,255,255,0.08)', borderColor: 'rgba(255,255,255,0.2)', color: '#fff' }} onClick={() => navigate('/exam/diagnosis')}>
            {t('progress.dashboardDiagnosisLink')}
          </button>
        </>}
      >
        <SegBars items={domains.map((d: any) => ({ name: d.name, current: d.level, weight: d.weight_pct }))} />
      </DashboardHero>

      <div className="grid grid-2-1">
        {/* 左列: 行动聚焦 */}
        <div className="stack" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* 最重要缺口 */}
          <div className="card gap-focus">
            <div className="card-head">
              <span className="card-title">
                <span className="chip chip-red" style={{ marginRight: 8 }}>{t('progress.dashboardMainGap')}</span>
                {topGap ? topGap.name : t('progress.dashboardNoOverallGap')}
              </span>
            </div>
            {topGap ? (
              <>
                <div className="gap-focus-plain">
                  {t('progress.dashboardTopGapDetail', { level: plainLevel(topGap.level), score: topGap.level.toFixed(1), target: topGap.target_level, weight: topGap.weight_pct })}
                </div>
                <div className="gap-focus-foot">
                  <StatusChip status={topGap.status} />
                  <button className="btn btn-primary btn-sm" onClick={() => navigate('/learning')}>
                    {t('progress.dashboardWorkOnGap')}
                  </button>
                </div>
              </>
            ) : (
              <div className="gap-focus-plain">{t('progress.dashboardGapEvidence')}</div>
            )}
          </div>

          {dueRetests.length > 0 && (
            <div className="card">
              <div className="card-head">
                <span className="card-title">{t('progress.dashboardDueRetests')}</span>
              </div>
              {dueRetests.map(retest => (
                <div className="list-row" key={retest.id}>
                  <div className="list-main">
                    <Link className="link list-title" to={`/learning/task/${retest.id}`}>{retest.title}</Link>
                    <div className="list-meta">
                      <span>{t('progress.dashboardRetestDue', { date: formatProgressDate(retest.due_date) })}</span>
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
              <span className="card-title">{t('progress.dashboardTodayTasks') + ' '}<span className="chip chip-purple" style={{ marginLeft: 6 }}>{todayTasks.length}</span></span>
              <Link to="/learning" className="link">{t('progress.dashboardAllTasks')}</Link>
            </div>
            {todayTasks.length === 0 && (
              <div className="list-row">
                <div className="list-ic">🎉</div>
                <div className="list-main">
                  <div className="list-title">{t('progress.dashboardNoTasksToday')}</div>
                  {!nextRetest && <div className="list-sub">{t('progress.dashboardReplan')}</div>}
                </div>
                {!nextRetest && (
                  <button className="btn btn-primary btn-sm" onClick={() => navigate('/exam/diagnosis')}>{t('progress.dashboardAssess')}</button>
                )}
              </div>
            )}
            {todayTasks.slice(0, 3).map((task) => (
              <div className="list-row" key={task.id}>
                <div className="list-ic">📌</div>
                <div className="list-main">
                  <div className="list-title">{task.title}</div>
                  <div className="list-meta">
                    <span className={`chip ${TYPE_CLS[task.kind] || 'chip-slate'}`}>{TYPE_NAME[task.kind] || task.kind}</span>
                    <span>{t('progress.durationMinutes', { minutes: task.minutes })}</span>
                    {task.domain && <span>{task.domain}</span>}
                  </div>
                </div>
                <button className="btn btn-outline btn-sm" onClick={() => navigate(`/learning/task/${task.id}`)}>{t('progress.dashboardStart')}</button>
              </div>
            ))}
          </div>

          <DashboardStats cards={statCards} />
        </div>

        {/* 右列: 最近进展 + 待审核 */}
        <div className="stack" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <DashboardCard title={t('progress.dashboardLastProgress')}>
            {timeline[0] ? (
              <div className="tl-item" style={{ padding: '4px 4px 0' }}>
                <div className="tl-date">{timeline[0].date}</div>
                <div className="tl-text">{timeline[0].text}</div>
              </div>
            ) : (
              <div className="list-row">
                <div className="list-ic">🌱</div>
                <div className="list-main">
                  <div className="list-title">{t('progress.dashboardNoRecords')}</div>
                  <div className="list-sub">{t('progress.dashboardFirstTask')}</div>
                </div>
              </div>
            )}
          </DashboardCard>

          <DashboardCard
            title={<>{t('progress.dashboardPendingImports') + ' '}<span className="chip chip-amber" style={{ marginLeft: 6 }}>{pendingImports.length}</span></>}
            action={<Link to="/bank?review=1" className="link">{t('progress.dashboardGoReview')}</Link>}
          >
            {pendingImports.length === 0 && (
              <div className="list-row">
                <div className="list-ic">✅</div>
                <div className="list-main">
                  <div className="list-title">{t('progress.dashboardNoPendingImports')}</div>
                  <div className="list-sub">{t('progress.dashboardImportFromBank')}</div>
                </div>
              </div>
            )}
            {pendingImports.map((p: any) => (
              <div className="list-row" key={p.id}>
                <div className="list-ic">📄</div>
                <div className="list-main">
                  <div className="list-title">{p.title}</div>
                  <div className="list-sub">{t('progress.dashboardImportDetails', { id: p.id, count: p.items, date: p.time })}</div>
                </div>
                <button className="btn btn-primary btn-sm" onClick={() => navigate('/bank?review=1')}>{t('progress.dashboardReview')}</button>
              </div>
            ))}
          </DashboardCard>
        </div>
      </div>
    </div>
  );
}
