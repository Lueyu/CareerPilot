import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { DemoBanner, PageHead } from '../components/ui';
import { api, useApi, type ExamHistoryPage } from '../api/client';


const STATUS_CLS: Record<string, string> = {
  COMPLETED: 'chip-green',
  ACTIVE: 'chip-amber',
  SCORING: 'chip-purple',
  SUBMITTED: 'chip-slate',
  TRANSCRIPT_CONFIRMED: 'chip-slate',
  FROZEN: 'chip-slate',
  CREATED: 'chip-slate',
  ABORTED: 'chip-slate',
  EXPIRED: 'chip-slate',
  SCORING_FAILED: 'chip-red',
  SCORING_RETRY_WAIT:'chip-amber',NEEDS_REVIEW:'chip-amber',
};
const STATUS_NAME: Record<string, string> = {
  COMPLETED: '已完成', ACTIVE: '进行中', SCORING: '评分中',
  SUBMITTED: '已提交', TRANSCRIPT_CONFIRMED: '誊写确认', FROZEN: '已冻结', CREATED: '已创建',
  ABORTED: '已放弃', EXPIRED: '已超时', SCORING_FAILED: '评分失败',
  SCORING_RETRY_WAIT:'等待后台重试',NEEDS_REVIEW:'评分待复核',
};
const KIND_NAME: Record<string, string> = {
  diagnosis: '独立诊断', focused: '专项考试', bank_exam: '面经考试', project: '项目面试',
};
const KIND_ICON: Record<string, string> = {
  diagnosis: '🎯', focused: '🧪', bank_exam: '📚', project: '💼',
};
const KIND_DESC: Record<string, string> = {
  diagnosis: '自适应选题的全域诊断，快速定位能力差距',
  focused: '针对单一能力域的深度考察',
  bank_exam: '从面经库抽题组卷，还原真实面试',
  project: '基于你的项目经历的结构化面试',
};

const HISTORY_PATH = '/exams/history';

export default function ExamCenter() {
  const navigate = useNavigate();
  const { data, loading, error } = useApi<ExamHistoryPage>(HISTORY_PATH);
  const [older, setOlder] = useState<Pick<ExamHistoryPage, 'history' | 'next_cursor'> | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState<string | null>(null);
  const nextCursor = older ? older.next_cursor : data?.next_cursor;

  async function loadOlder() {
    if (nextCursor == null || loadingOlder) return;
    setLoadingOlder(true);
    setOlderError(null);
    try {
      const page = await api.get<ExamHistoryPage>(`${HISTORY_PATH}?before_id=${nextCursor}`);
      setOlder(previous => {
        if (previous && previous.next_cursor !== nextCursor) return previous;
        return {
          history: [...(previous?.history ?? []), ...page.history],
          next_cursor: page.next_cursor,
        };
      });
    } catch (cause) {
      setOlderError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingOlder(false);
    }
  }

  const kinds = data?.kinds
    ? data.kinds.filter(k => k.kind === 'diagnosis').map((k) => ({
        id: k.kind, icon: KIND_ICON[k.kind] || '📝', title: k.title,
        desc: k.desc || KIND_DESC[k.kind] || '',
        meta: [`${k.default_minutes} 分钟`, `${k.history_count} 次记录`],
        primary: k.kind === 'diagnosis',
      }))
    : [];
  const history = [...(data?.history ?? []), ...(older?.history ?? [])].map(h => ({
    id: h.id, name: h.title, type: KIND_NAME[h.kind] || h.kind,
    status: h.status, score: h.score, time: (h.completed_at || h.started_at || '').slice(0, 10),
    report: ['COMPLETED', 'SCORING_FAILED', 'SCORING', 'SCORING_RETRY_WAIT','NEEDS_REVIEW','SUBMITTED','FROZEN','TRANSCRIPT_CONFIRMED'].includes(h.status),
  }));

  return (
    <div>
      <PageHead
        title="考试中心"
        sub="独立诊断与学习任务新题验证，记录每次真实作答"
      >
        <button className="btn btn-outline" onClick={() => navigate('/bank')}>查看题库</button>
        <button className="btn btn-primary" onClick={() => navigate('/exam/diagnosis')}>开始独立诊断</button>
      </PageHead>
      <DemoBanner />

      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        {kinds.map(e => (
          <div className="exam-card" key={e.id}>
            <div className={`exam-ic ${e.primary ? '' : 'alt'}`}>{e.icon}</div>
            <div className="exam-title">{e.title}</div>
            <div className="exam-desc">{e.desc}</div>
            <div className="exam-meta">
              {e.meta.map((m: string) => (
                <span key={m}>{m}</span>
              ))}
            </div>
            <button
              className={`btn ${e.primary ? 'btn-primary' : 'btn-outline'}`}
              onClick={() => (e.id === 'diagnosis' ? navigate('/exam/diagnosis') : navigate('/exam/diagnosis'))}
            >
              {e.primary ? '开始诊断' : '开始考试'}
            </button>
          </div>
        ))}
      </div>

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>正在从后端加载实时数据…</div>}
      {error && <div className="demo-banner demo-banner-err" style={{ marginBottom: 12 }}>后端不可用（{error}），无法读取当前空间考试记录</div>}

      <div className="card">
        <div className="card-head">
          <span className="card-title">历史记录</span>
          {data && <span className="hint" role="status">{`${history.length} / ${data.total_count} 条记录`}</span>}
        </div>
        <div className="table-wrap">
          <table className="tbl">
            <thead>
              <tr>
                <th>考试名称</th>
                <th>类型</th>
                <th>状态</th>
                <th>得分</th>
                <th>时间</th>
                <th></th>
              </tr>
            </thead>
            <tbody>{!loading && !error && history.length === 0 && <tr><td colSpan={6}>还没有考试记录。开始一次独立诊断。</td></tr>}
              {history.map(h => (
                <tr key={h.id}>
                  <td className="strong">{h.name}</td>
                  <td><span className="chip chip-slate">{h.type}</span></td>
                  <td><span className={`chip ${STATUS_CLS[h.status] || 'chip-slate'}`}>{STATUS_NAME[h.status] || h.status}</span></td>
                  <td className="num">{h.status!=='COMPLETED' || h.score === null || h.score === undefined ? '—' : <b style={{ color: (h.score ?? 0) >= 70 ? 'var(--green)' : 'var(--amber)' }}>{Math.round(h.score)}</b>}</td>
                  <td className="num">{h.time || '—'}</td>
                  <td className="right">
                    {h.report ? (
                      <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/exam/diagnosis/report?id=${h.id}`)}>{h.status==='COMPLETED'?'查看报告':'查看本次状态'}</button>
                    ) : (
                      h.status === 'ACTIVE' ? <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/exam/diagnosis?id=${h.id}`)}>继续考试</button> : <span>—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {olderError && <p className="demo-banner demo-banner-err" role="alert">无法加载更早记录（{olderError}）</p>}
        {nextCursor != null && (
          <button className="btn btn-outline" type="button" disabled={loadingOlder} onClick={loadOlder}
            style={{ marginTop: 12 }}>
            {loadingOlder ? '正在加载…' : '加载更早记录'}
          </button>
        )}
      </div>
    </div>
  );
}
