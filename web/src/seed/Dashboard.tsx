import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { DashboardCard, DashboardHero, DashboardStats } from '../components/DashboardLayout';
import { PageHead } from '../components/PageHead';
import { useWorkspaceApi } from './api';

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
  | { kind: 'error'; message: string };

function threadLink(thread: Practice) {
  return thread.mode === 'interview' ? `/exam?thread=${thread.id}` : `/study/chat/${thread.id}`;
}

export default function Dashboard() {
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
          const message = error instanceof Error ? error.message : '请稍后重试。';
          setResult({ request, value: { kind: 'error', message } });
        }
      },
    );
    return () => controller.abort();
  }, [request, attempt]);

  if (!current) return <p role="status">正在加载工作空间…</p>;
  if (current.kind === 'error') {
    return <DashboardCard title="暂时无法加载工作空间">
      <p role="alert">{current.message}</p>
      <button className="btn btn-outline" onClick={() => {
        setResult(null);
        setAttempt(value => value + 1);
      }}>重新加载</button>
    </DashboardCard>;
  }
  const { data } = current;
  const activity = data.latest_activity;
  const next = data.resume_interview ?? data.resume_study;
  const primary = !data.selected_role
    ? { to: '/onboarding', label: '设置目标岗位' }
    : next
      ? { to: threadLink(next), label: next.mode === 'interview' ? '继续面试' : '继续学习' }
      : data.question_count ? { to: '/learning', label: '查看学习计划' } : { to: '/bank', label: '准备第一道题' };

  return (
    <div>
      <PageHead title="把下一步，变得更清晰" sub={data.selected_role ? `目标 ${data.selected_role}` : undefined}>
        <Link className="btn btn-outline" to="/bank">打开题库</Link>
        <Link className="btn btn-primary" to={primary.to}>{primary.label}</Link>
      </PageHead>

      <DashboardHero
        label="当前目标 · 我的工作空间"
        title={data.selected_role ?? '待设置'}
        summary={activity ? `上次练习 · ${activity.title}` : '从题库选一道题开始练习。'}
        footer={<>
          <Link className="note" to="/onboarding">目标岗位 →</Link>
          <Link className="note" to="/settings">工作空间设置 →</Link>
        </>}
      />

      <div className="grid grid-2-1">
        <div className="stack" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <DashboardCard title="继续练习" action={<Link className="link" to="/memory">项目记忆 →</Link>}>
            {([
              { label: '面试练习', to: '/exam', thread: data.resume_interview },
              { label: '引导学习', to: '/study/chat', thread: data.resume_study },
            ]).map(({ label, to, thread }) => (
              <div className="list-row" key={to}>
                <div className="list-main">
                  <div className="list-title">{label}</div>
                  {thread && <div className="list-sub">{thread.title}</div>}
                </div>
                <Link className="btn btn-outline btn-sm" to={thread ? threadLink(thread) : to}>
                  {thread ? `继续${label}` : `开始${label}`}
                </Link>
              </div>
            ))}
          </DashboardCard>
          <DashboardStats cards={[
            { label: '目标岗位', value: data.role_count, ic: '🎯' },
            { label: '题库题目', value: data.question_count, ic: '🗂' },
            { label: '面经导入', value: data.import_count, ic: '📥' },
            { label: '练习记录', value: data.practice_count, ic: '📌' },
          ]} />
        </div>

        <div className="stack" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <DashboardCard title="最近一次进展">
            {activity ? (
              <div className="tl-item" style={{ padding: '4px 4px 0' }}>
                <div className="tl-date">{activity.updated_at.slice(0, 10)}</div>
                <Link className="link tl-text" to={threadLink(activity)}>{activity.title}</Link>
              </div>
            ) : <div className="list-row">还没有练习记录</div>}
          </DashboardCard>
          <DashboardCard
            title={<>待审核面经 <span className="chip chip-amber" style={{ marginLeft: 6 }}>{data.pending_import_count}</span></>}
            action={<Link className="link" to="/bank">去题库 →</Link>}
          >
            {data.pending_imports.length === 0 && <div className="list-row">没有待审核的导入</div>}
            {data.pending_imports.map(batch => (
              <div className="list-row" key={batch.id}>
                <div className="list-main">
                  <div className="list-title">{batch.filename ?? '面经导入'}</div>
                  <div className="list-sub">{batch.created_at.slice(0, 10)}</div>
                </div>
              </div>
            ))}
          </DashboardCard>
        </div>
      </div>
    </div>
  );
}
