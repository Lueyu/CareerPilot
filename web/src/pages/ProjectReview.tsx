import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAppState } from '../components/AppState';
import { PageHead } from '../components/ui';
import { api } from '../api/client';

const LEVEL_CLS: Record<string, string> = {
  VERIFIED: 'ev-verified',
  HIGH: 'ev-claim',
  UNVERIFIED_CLAIM: 'ev-claim',
};
const LEVEL_TEXT: Record<string, string> = {
  VERIFIED: '已核验', HIGH: '结构证据', UNVERIFIED_CLAIM: '未验证声明',
};
const LEVEL_ICON: Record<string, string> = { VERIFIED: '✅', HIGH: '🔶', UNVERIFIED_CLAIM: '⚠️' };

interface FindingView {
  id: number; domain: string; level: string; title: string; detail: string;
  file_path: string | null; evidence_ref: string | null;
}
interface ProjectLite { id: number; name: string; owner: string; repo: string; description: string | null; last_scanned_at: string | null; status: string }
interface FindingsData { items: FindingView[]; matrix: { key: string; level: number; findings: number }[]; stats: Record<string, unknown> }

export default function ProjectReview() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { toast } = useAppState();
  const pid = Number(id);
  const [project, setProject] = useState<ProjectLite | null>(null);
  const [data, setData] = useState<FindingsData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  const reload = useCallback(() => {
    if (!pid) { setErr('无效的项目 id'); setLoading(false); return; }
    setLoading(true);
    Promise.all([
      api.get<{ items: ProjectLite[] }>('/projects'),
      api.get<FindingsData>(`/projects/${pid}/findings`),
    ])
      .then(([pl, fd]) => {
        setProject(pl.items.find((x) => x.id === pid) ?? null);
        setData(fd);
        setErr(null);
      })
      .catch((e) => setErr(e.message || String(e)))
      .finally(() => setLoading(false));
  }, [pid]);

  useEffect(() => { reload(); }, [reload]);

  const runUpdate = async () => {
    setRunning(true);
    try {
      const r = await api.post<{ short_code: string; risk_level: string }>(`/career-assets/updates/run?project_id=${pid}`);
      toast('⚙', `已生成内容草稿（${r.short_code}，风险 ${r.risk_level}）`);
      navigate('/assets/pipeline');
    } catch (e: any) {
      toast('⚠️', `生成失败：${e.message || e}`);
    } finally {
      setRunning(false);
    }
  };

  const items = data?.items ?? [];
  const verified = items.filter((f) => f.level === 'VERIFIED').length;
  const high = items.filter((f) => f.level === 'HIGH').length;
  const claim = items.filter((f) => f.level === 'UNVERIFIED_CLAIM').length;

  return (
    <div>
      <PageHead
        title={`${project ? `${project.name} · 项目审查` : '项目审查'}`}
        sub={`${project?.owner}/${project?.repo}${project?.last_scanned_at ? ` · 最近扫描 ${new Date(project.last_scanned_at).toLocaleString('zh-CN')}` : ' · 尚未扫描'}`}
      >
        <button className="btn btn-outline" onClick={() => navigate('/assets')}>← 返回项目资产</button>
        <button className="btn btn-primary" onClick={runUpdate} disabled={running || items.length === 0}>
          {running ? '生成中…' : '更新职业展示 →'}
        </button>
      </PageHead>
      {loading && <div className="hint" style={{ marginBottom: 14 }}>正在从后端加载审查数据…</div>}
      {err && (
        <div className="card" style={{ padding: '28px 20px', marginBottom: 16 }}>
          <b>⚠️ 无法加载项目审查</b>
          <p className="hint" style={{ marginTop: 6 }}>{err}</p>
          <button className="btn btn-outline btn-sm" style={{ marginTop: 10 }} onClick={reload}>重试</button>
        </div>
      )}
      {!loading && !err && items.length === 0 && (
        <div className="card" style={{ padding: '28px 20px' }}>
          <b>还没有扫描发现</b>
          <p className="hint" style={{ marginTop: 6 }}>先在「项目与职业资产」页执行一次扫描。</p>
        </div>
      )}

      {items.length > 0 && (
        <>
          <div className="grid grid-4" style={{ marginBottom: 18 }}>
            <div className="mini-stat"><b>{items.length}</b><span>发现项</span></div>
            <div className="mini-stat"><b>{verified}</b><span>已核验(内容确认)</span></div>
            <div className="mini-stat"><b>{high}</b><span>结构证据</span></div>
            <div className="mini-stat"><b>{claim}</b><span>未验证声明</span></div>
          </div>

          <div className="card" style={{ marginBottom: 18 }}>
            <div className="card-head">
              <span className="card-title">能力矩阵</span>
              <span className="card-sub">确定性计分: VERIFIED=1.0 · HIGH=0.6 · UNVERIFIED=0.25, 上限 5</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {(data?.matrix ?? []).filter((m) => m.findings > 0).map((m) => (
                <div className="dim-row" key={m.key}>
                  <span className="dim-name" style={{ width: 180 }}>{m.key}</span>
                  <div className="dim-track">
                    <div className="dim-fill" style={{ width: `${(m.level / 5) * 100}%` }} />
                  </div>
                  <span className="dim-val">{m.level} <span className="hint">({m.findings} 条)</span></span>
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <span className="card-title">证据发现</span>
              <span className="card-sub">每条发现都引用文件路径与等级</span>
            </div>
            {items.map((f) => (
              <div className="list-row" key={f.id} style={{ alignItems: 'flex-start' }}>
                <div className="list-ic">{LEVEL_ICON[f.level] || '·'}</div>
                <div className="list-main">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span className={`ev-chip ${LEVEL_CLS[f.level] || ''}`}>{f.level}</span>
                    <span className="chip chip-slate">{f.domain}</span>
                    <b style={{ fontSize: 13.5 }}>{f.title}</b>
                  </div>
                  <div className="list-sub" style={{ marginTop: 4 }}>{f.detail}</div>
                  <div className="q-meta">
                    <span className="chip chip-slate">📄 {f.file_path || '-'}</span>
                    <span className="chip chip-slate">{LEVEL_TEXT[f.level] || f.level}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
