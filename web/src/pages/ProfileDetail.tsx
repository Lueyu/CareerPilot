import React from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { DemoBanner, PageHead, StatusChip, plainConfidence, plainLevel, OBSERVATION_COVERAGE_NOTE } from '../components/ui';
import { useApi, ProfileDetailData } from '../api/client';

function evidenceReportLink(sourceRef: string | undefined) {
  const match = /^exam:([1-9]\d*)#q([1-9]\d*)$/.exec(sourceRef ?? '');
  if (!match) return null;
  const [, examId, order] = match;
  if (!Number.isSafeInteger(Number(examId)) || !Number.isSafeInteger(Number(order))) return null;
  return `/exam/diagnosis/report?id=${examId}#exam-question-${order}`;
}

export default function ProfileDetail() {
  const { domain } = useParams();
  const navigate = useNavigate();
  const { data, loading, error } = useApi<ProfileDetailData>(domain ? `/profile/${encodeURIComponent(domain)}` : null);
  const state = data?.state;
  return <div>
    <PageHead title={data?.skill.name || '能力详情'} sub={`独立证据覆盖 ${state?.evidence_coverage ?? '—'} · 观察记录 ${data?.evidence.length ?? '—'}`}>
      <button className="btn btn-outline" onClick={() => navigate('/profile')}>← 返回能力画像</button>
      <button className="btn btn-primary" onClick={() => navigate('/exam/diagnosis')}>补充证据</button>
    </PageHead>
    <DemoBanner />
    {loading && <p className="hint">正在读取能力详情…</p>}
    {error && <div role="alert" className="demo-banner demo-banner-err">读取失败：{error}</div>}
    {!loading && !error && !data && <p className="hint">未指定能力域。</p>}
    {data && state && <>
      <div className="grid grid-2" style={{ marginBottom: 18 }}>
        <div className="card">
          <div className="card-head"><span className="card-title">估计与观察覆盖</span><StatusChip status={state.status} /></div>
          <div style={{ fontSize: 44, fontWeight: 750 }}>{state.level == null ? '未知' : `${state.level.toFixed(1)}/5`}</div>
          <p className="plain-hint">{plainLevel(state.level)}</p>
          <p className="hint">{plainConfidence(state.confidence)}。{OBSERVATION_COVERAGE_NOTE}</p>
          <p className="hint">最后评估 {state.last_assessed_at?.slice(0, 10) || '—'}。单题表现不能代表整个能力域。</p>
        </div>
        <div className="card">
          <div className="card-head"><span className="card-title">维度分解</span></div>
          {Object.entries(state.dimension_scores || {}).length === 0 && <p className="hint">尚无可靠维度估计。</p>}
          {Object.entries(state.dimension_scores || {}).map(([name, score]) => <div key={name} className="dim-row">
            <span className="dim-name">{name}</span><div className="dim-track"><div className="dim-fill" style={{ width: `${score / 5 * 100}%` }} /></div><span className="dim-val">{score.toFixed(1)}</span>
          </div>)}
        </div>
      </div>
      <div className="card">
        <div className="card-head"><span className="card-title">作答观察与证据</span><span className="card-sub">{data.evidence.length} 条</span></div>
        {data.evidence.some(e=>e.grade==='OBSERVED_UNCALIBRATED') && <p className="hint">未校准观察只记录本次表现，不等于可靠能力判断。</p>}
        {data.evidence.length === 0 && <p className="hint">尚无作答观察；未知不代表零分或达标。</p>}
        {data.evidence.map((e, i) => {
          const reportLink = evidenceReportLink(e.source_ref);
          return <div className="list-row" key={e.id || i}>
            <div className="list-ic">🧾</div><div className="list-main"><div className="list-title">{e.title}</div><div className="list-sub">{e.detail}</div><div className="list-sub">{e.short_code} · {(e.occurred_at || e.created_at)?.slice(0, 10)} · {e.grade==='OBSERVED_UNCALIBRATED'?'未校准观察':e.grade}</div>
              {e.observation && <><p>本次表现：{e.observation.score ?? '—'}/{e.observation.max_points ?? '—'}</p><p>原答摘录：{e.observation.answer_excerpt || '（未作答）'}</p><p>缺失评分项：{(e.observation.criteria || []).filter(c => c.status === 'missing').map(c => c.title).join('、') || '未记录'}</p></>}
              {reportLink && <Link to={reportLink}>查看本题的原答与评分依据</Link>}
            </div>
          </div>;
        })}
        <button className="btn btn-outline btn-sm" onClick={() => navigate('/learning')}>查看具体失分任务 →</button>
      </div>
    </>}
  </div>;
}
