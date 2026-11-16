import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppState } from '../components/AppState';
import { Modal, PageHead } from '../components/ui';
import { api } from '../api/client';

interface RunView {
  id: number; short_code: string; status: string; risk_level: string; risk_reason: string | null;
  diff_markdown: string | null; created_at: string | null;
  project: { id: number; name: string; owner: string; repo: string } | null;
  draft: { id: number; short_code: string; title: string; slug: string; status: string; privacy_scan_status: string; fact_check_status: string; validation_errors: { check: string; level: string; message: string }[] | null } | null;
  publication: { id: number; short_code: string; status: string; pr_number: number | null; pr_url: string | null; build_status: string; commit_sha: string | null } | null;
  stats: Record<string, unknown> | null;
}

const RISK_CLS: Record<string, string> = { LOW: 'chip-green', MEDIUM: 'chip-amber', HIGH: 'chip-red' };
const RISK_TEXT: Record<string, string> = { LOW: '低风险', MEDIUM: '中风险', HIGH: '高风险' };
const DRAFT_CHECK_CLS: Record<string, string> = { PASS: 'chip-green', WARN: 'chip-amber', FAIL: 'chip-red', info: 'chip-green', warning: 'chip-amber', error: 'chip-red' };

/** 由 run 状态推导流水线步骤(0=检测变更 … 7=发布完成)。 */
function stepOf(run: RunView | null): number {
  if (!run) return 0;
  if (run.status === 'ROLLED_BACK') return 8;
  if (run.status === 'FAILED') return 4;
  if (run.publication?.status === 'PUBLISHED') return 7;
  if (run.publication?.pr_number) return 6;
  if (run.publication?.status === 'DRY_RUN' || run.publication?.status === 'PR_CREATED') return 6;
  if (run.draft?.status === 'APPROVED') return 5;
  if (run.status === 'DRAFT_CREATED') return 4;
  return 0;
}
const STEPS = ['检测项目变更', '提取证据', '映射能力', '生成内容草稿', '风险分级与审核', '创建 PR', '发布', '完成'];

export default function Pipeline() {
  const navigate = useNavigate();
  const { toast } = useAppState();
  const [runs, setRuns] = useState<RunView[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [rollbackOpen, setRollbackOpen] = useState<RunView | null>(null);
  const [dryRun, setDryRun] = useState(false);

  const reload = useCallback(() => {
    setLoading(true);
    api.get<{ items: RunView[] }>('/career-assets/updates')
      .then((d) => { setRuns(d.items); setErr(null); })
      .catch((e) => { setErr(e.message || String(e)); setRuns(null); })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const approve = async (r: RunView, autoMerge: boolean) => {
    setBusy(true);
    try {
      const q = `auto_merge=${autoMerge}&dry_run=${dryRun}`;
      await api.post(`/career-assets/updates/${r.id}/approve?${q}`);
      toast(dryRun ? '🧪' : '✅', autoMerge ? (dryRun ? 'Dry-run 发布计划已生成（未写 GitHub）' : '已发布：PR 创建成功') : '草稿已批准，可在公开内容页手动发布');
      reload();
    } catch (e: any) {
      toast('⚠️', `审批失败：${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  const rollback = async (r: RunView) => {
    setBusy(true);
    try {
      await api.post(`/career-assets/updates/${r.id}/rollback`);
      toast('↩️', '已回滚：站点恢复上一可用版本');
      setRollbackOpen(null);
      reload();
    } catch (e: any) {
      toast('⚠️', `回滚失败：${e.message || e}`);
      setRollbackOpen(null);
    } finally {
      setBusy(false);
    }
  };

  const latest = runs && runs.length > 0 ? runs[0] : null;
  const step = stepOf(latest);

  return (
    <div>
      <PageHead
        title="职业资产更新流水线"
        sub={latest?.project ? `${latest.project.name} · ${latest.short_code}` : '项目扫描 → 草稿 → 风险分级 → 发布'}
      >
        <button className="btn btn-outline" onClick={() => navigate('/assets')}>← 返回项目资产</button>
      </PageHead>
      {loading && <div className="hint" style={{ marginBottom: 14 }}>正在从后端加载流水线状态…</div>}
      {err && <div className="card" style={{ padding: '24px 20px', marginBottom: 16 }}><b>⚠️ 无法加载流水线</b><p className="hint" style={{ marginTop: 6 }}>{err}</p></div>}
      {!loading && !err && (!runs || runs.length === 0) && (
        <div className="card" style={{ padding: '28px 20px' }}>
          <b>还没有资产更新记录</b>
          <p className="hint" style={{ marginTop: 6 }}>先扫描一个仓库，然后在「项目与职业资产」页点「更新职业资产」。</p>
        </div>
      )}

      {latest && (
        <>
          <div className="card" style={{ marginBottom: 18 }}>
            <div className="pipeline">
              {STEPS.map((s, i) => (
                <React.Fragment key={s}>
                  {i > 0 && <span className="pl-arrow">→</span>}
                  <span className={`pl-step ${i < step ? 'done' : i === step ? 'now' : ''}`}>
                    {i < step ? '✓' : i === step ? '⟳' : `${i + 1}`} {s}
                  </span>
                </React.Fragment>
              ))}
            </div>
          </div>

          <div className="card" style={{ marginBottom: 18 }}>
            <div className="card-head">
              <span className="card-title">{latest.draft?.title || '内容草稿'}</span>
              <span className={`chip ${RISK_CLS[latest.risk_level] || 'chip-slate'}`}>风险分级：{RISK_TEXT[latest.risk_level] || latest.risk_level}</span>
            </div>
            {latest.risk_reason && <div className="hint" style={{ margin: '4px 0 10px' }}>{latest.risk_reason}</div>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 12 }}>
              {(latest.draft?.validation_errors ?? []).map((v, i) => (
                <div className="list-row" key={i} style={{ padding: '6px 2px' }}>
                  <span className={`chip ${DRAFT_CHECK_CLS[v.level] || 'chip-slate'}`}>{v.check}</span>
                  <span style={{ fontSize: 13 }}>{v.message}</span>
                </div>
              ))}
            </div>
            <div className="hint" style={{ marginBottom: 6 }}>与上一版差异</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {(latest.diff_markdown || '').split('\n').filter(Boolean).slice(0, 12).map((c, i) => (
                <div className="list-row" key={i} style={{ padding: '7px 2px' }}>
                  <span className={`chip ${c.startsWith('移除') ? 'chip-red' : 'chip-green'}`}>{c.startsWith('移除') ? '移除' : '变更'}</span>
                  <span style={{ fontSize: 13 }}>{c.replace(/^移除:\s*/, '')}</span>
                </div>
              ))}
            </div>
          </div>

          {latest.publication && (
            <div className="card" style={{ marginBottom: 18 }}>
              <div className="card-head">
                <span className="card-title">发布记录 · {latest.publication.short_code}</span>
                <span className={`chip ${latest.publication.status === 'PUBLISHED' ? 'chip-green' : latest.publication.status === 'DRY_RUN' ? 'chip-blue' : 'chip-amber'}`}>
                  {latest.publication.status}
                </span>
              </div>
              <div className="q-meta" style={{ marginBottom: 8 }}>
                {latest.publication.pr_url && <a className="chip chip-slate" href={latest.publication.pr_url} target="_blank" rel="noreferrer">🔀 PR #{latest.publication.pr_number}</a>}
                {latest.publication.commit_sha && <span className="chip chip-slate">commit {latest.publication.commit_sha.slice(0, 8)}</span>}
                <span className="chip chip-slate">构建 {latest.publication.build_status}</span>
                {latest.draft && <span className="chip chip-slate">草稿 {latest.draft.short_code} · {latest.draft.status}</span>}
              </div>
              {latest.publication.status === 'DRY_RUN' && (
                <div className="hint">这是一次 dry-run：只生成发布计划（分支名/写入路径），未写入 GitHub。</div>
              )}
            </div>
          )}

          <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
            <div>
              <div className="card-title" style={{ margin: 0 }}>发布操作</div>
              <p className="hint" style={{ margin: '4px 0 0' }}>
                低风险可自动发布 · 高风险强制人工审核（不可跳过）· dry-run 只出计划不写 GitHub
              </p>
            </div>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
              <label className="hint" style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} /> dry-run
              </label>
              {latest.draft?.status === 'DRAFT' && (
                <button className="btn btn-primary" disabled={busy} onClick={() => approve(latest, latest.risk_level === 'LOW')}>
                  {latest.risk_level === 'LOW' ? '批准并发布' : '批准草稿'}
                </button>
              )}
              {latest.status === 'PUBLISHED' && (
                <button className="btn btn-danger" onClick={() => setRollbackOpen(latest)}>↩ 回滚发布</button>
              )}
            </div>
          </div>
        </>
      )}

      {runs && runs.length > 1 && (
        <div className="card mt-18">
          <div className="card-head"><span className="card-title">历史运行</span></div>
          {runs.map((r) => (
            <div className="list-row" key={r.id}>
              <div className="list-ic">{r.status === 'PUBLISHED' ? '🚀' : r.status === 'ROLLED_BACK' ? '↩️' : r.status === 'FAILED' ? '⚠️' : '📝'}</div>
              <div className="list-main">
                <div className="list-title">{r.short_code} · {r.project?.name || '未关联项目'}</div>
                <div className="list-sub">
                  {r.created_at ? new Date(r.created_at).toLocaleString('zh-CN') : ''} · 草稿 {r.draft?.short_code || '-'} · 风险 {RISK_TEXT[r.risk_level] || r.risk_level}
                  {r.publication?.pr_number ? ` · PR #${r.publication.pr_number}` : ''}
                </div>
              </div>
              <span className={`chip ${r.status === 'PUBLISHED' ? 'chip-green' : r.status === 'ROLLED_BACK' ? 'chip-slate' : 'chip-amber'}`}>{r.status}</span>
            </div>
          ))}
        </div>
      )}

      {rollbackOpen && (
        <Modal title="回滚发布？" size="sm" onClose={() => setRollbackOpen(null)}
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setRollbackOpen(null)}>取消</button>
              <button className="btn btn-danger" disabled={busy} onClick={() => rollback(rollbackOpen)}>确认回滚</button>
            </>
          }>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.7 }}>
            将职业展示回滚至<b>上一个可用发布版本</b>（创建回滚分支 + PR，不自动合并）。
            本次发布产生的证据仍保留，不会删除能力画像数据。
          </p>
        </Modal>
      )}
    </div>
  );
}
