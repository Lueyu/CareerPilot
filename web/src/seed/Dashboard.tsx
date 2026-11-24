import { useEffect, useState } from 'react';
import { useLocale, useT, type Locale } from '../i18n';
import { Link } from 'react-router-dom';
import { DashboardCard, DashboardHero, DashboardStats } from '../components/DashboardLayout';
import { PageHead } from '../components/PageHead';
import { messageFor, renderMessage, useWorkspaceApi, type UiMessage } from './api';

type Practice = {
  id: number;
  mode: 'interview' | 'study';
  title: string;
  updated_at: string;
};

export type DashboardSummary = {
  selected_role: string | null;
  role_count: number;
  question_count: number;
  import_count: number;
  pending_import_count: number;
  practice_count: number;
  latest_activity: Practice | null;
  resume_interview: Practice | null;
  resume_study: Practice | null;
  pending_imports: { id: number; filename: string | null; created_at: string }[];
};

type LoadResult =
  | { kind: 'ready'; data: DashboardSummary }
  | { kind: 'error'; message: UiMessage };

function threadLink(thread: Practice) {
  return thread.mode === 'interview' ? `/beta/exam?thread=${thread.id}` : `/beta/study/chat/${thread.id}`;
}

function formatDate(timestamp: string, locale: Locale) {
  const date = timestamp.slice(0, 10);
  if (locale === 'zh-CN') return date;
  const time = Date.parse(date);
  return Number.isNaN(time) ? date : new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeZone: 'UTC' }).format(time);
}

export default function Dashboard() {
  const locale = useLocale();
  const t = useT();
  const { request } = useWorkspaceApi();
  const [result, setResult] = useState<{ request: typeof request; value: LoadResult } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const current = result?.request === request ? result.value : null;

  useEffect(() => {
    const controller = new AbortController();
    void request<DashboardSummary>('/dashboard', { signal: controller.signal }).then(
      data => {
        if (!controller.signal.aborted) setResult({ request, value: { kind: 'ready', data } });
      },
      error => {
        if (!controller.signal.aborted) {
          const message = messageFor(error, 'registered.tryAgainLater');
          setResult({ request, value: { kind: 'error', message } });
        }
      },
    );
    return () => controller.abort();
  }, [request, attempt]);

  if (!current) return <p role="status">{t('registered.workspaceLoading')}</p>;
  if (current.kind === 'error') {
    return <DashboardCard title={t('registered.workspaceLoadFailed')}>
      <p role="alert">{renderMessage(current.message, t)}</p>
      <button className="btn btn-outline" onClick={() => {
        setResult(null);
        setAttempt(value => value + 1);
      }}>{t('registered.reloadWorkspace')}</button>
    </DashboardCard>;
  }
  const { data } = current;
  const activity = data.latest_activity;
  const formatCount = new Intl.NumberFormat(locale);
  const next = data.resume_interview ?? data.resume_study;
  const primary = !data.selected_role
    ? { to: '/onboarding', label: t('registered.setTargetRole') }
    : next
      ? { to: threadLink(next), label: next.mode === 'interview' ? t('registered.continueInterview') : t('registered.continueLearning') }
      : data.question_count ? { to: '/beta/learning', label: t('registered.viewLearningPlan') } : { to: '/beta/bank', label: t('registered.prepareFirstQuestion') };

  return (
    <div>
      <PageHead title={t('registered.dashboardTitle')} sub={data.selected_role ? t('registered.dashboardTargetRole', { role: data.selected_role }) : undefined}>
        <Link className="btn btn-outline" to="/beta/bank">{t('registered.openQuestionBank')}</Link>
        <Link className="btn btn-primary" to={primary.to}>{primary.label}</Link>
      </PageHead>

      <DashboardHero
        label={t('registered.workspaceGoal')}
        title={data.selected_role ?? t('registered.roleNotSet')}
        summary={activity ? t('registered.lastPractice', { title: activity.title }) : t('registered.startFromBank')}
        footer={<>
          <Link className="note" to="/onboarding">{t('registered.targetRoleLink')}</Link>
          <Link className="note" to="/settings">{t('registered.workspaceSettingsLink')}</Link>
        </>}
      />

      <div className="grid grid-2-1">
        <div className="stack" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <DashboardCard title={t('registered.continuePractice')} action={<Link className="link" to="/beta/memory">{t('registered.projectMemoryLink')}</Link>}>
            {([
              { label: t('registered.interviewPractice'), to: '/beta/exam', thread: data.resume_interview,
                action: t(data.resume_interview ? 'registered.resumeInterviewPractice' : 'registered.startInterviewPractice') },
              { label: t('registered.guidedStudy'), to: '/beta/study/chat', thread: data.resume_study,
                action: t(data.resume_study ? 'registered.resumeGuidedStudy' : 'registered.startGuidedStudy') },
            ]).map(({ label, to, thread, action }) => (
              <div className="list-row" key={to}>
                <div className="list-main">
                  <div className="list-title">{label}</div>
                  {thread && <div className="list-sub">{thread.title}</div>}
                </div>
                <Link className="btn btn-outline btn-sm" to={thread ? threadLink(thread) : to}>
                  {action}
                </Link>
              </div>
            ))}
          </DashboardCard>
          <DashboardStats cards={[
            { label: t('registered.targetRole'), value: formatCount.format(data.role_count), ic: '🎯' },
            { label: t('registered.bankQuestionCount'), value: formatCount.format(data.question_count), ic: '🗂' },
            { label: t('registered.interviewNotesImport'), value: formatCount.format(data.import_count), ic: '📥' },
            { label: t('registered.practiceRecordCount'), value: formatCount.format(data.practice_count), ic: '📌' },
          ]} />
        </div>

        <div className="stack" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <DashboardCard title={t('registered.latestProgress')}>
            {activity ? (
              <div className="tl-item" style={{ padding: '4px 4px 0' }}>
                <div className="tl-date">{formatDate(activity.updated_at, locale)}</div>
                <Link className="link tl-text" to={threadLink(activity)}>{activity.title}</Link>
              </div>
            ) : <div className="list-row">{t('registered.noPracticeHistory')}</div>}
          </DashboardCard>
          <DashboardCard
            title={<>{t('registered.importsToReview')} <span className="chip chip-amber" style={{ marginLeft: 6 }}>{formatCount.format(data.pending_import_count)}</span></>}
            action={<Link className="link" to="/beta/bank">{t('registered.goToBankLink')}</Link>}
          >
            {data.pending_imports.length === 0 && <div className="list-row">{t('registered.noImportsToReview')}</div>}
            {data.pending_imports.map(batch => (
              <div className="list-row" key={batch.id}>
                <div className="list-main">
                  <div className="list-title">{batch.filename ?? t('registered.interviewNotesImportName')}</div>
                  <div className="list-sub">{formatDate(batch.created_at, locale)}</div>
                </div>
              </div>
            ))}
          </DashboardCard>
        </div>
      </div>
    </div>
  );
}
