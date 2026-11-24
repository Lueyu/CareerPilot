import {t as translate, useLocale, useT} from '../i18n';
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
const RISK_LABEL_KEYS: Record<string, string> = { LOW: 'projects.riskLow', MEDIUM: 'projects.riskMedium', HIGH: 'projects.riskHigh' };
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
const STATUS_KEYS: Partial<Record<string, string>> = {
  PENDING_DIFF: 'projects.pipelinePendingDiff', DRAFT_CREATED: 'projects.pipelineDraftCreated',
  PUBLISHED: 'projects.pipelinePublished', FAILED: 'projects.analysisRunFailed', ROLLED_BACK: 'projects.pipelineRolledBack',
  CREATED: 'projects.pipelineCreated', PR_CREATED: 'projects.pipelinePrCreated', BUILDING: 'projects.pipelineBuilding',
  DRY_RUN: 'projects.pipelineDryRun', DRAFT: 'projects.draft', APPROVED: 'projects.pipelineApproved',
  REJECTED: 'projects.pipelineRejected', ARCHIVED: 'projects.pipelineArchived',
  NONE: 'projects.none', PENDING: 'projects.pendingAnalysis', RUNNING: 'projects.pipelineRunning',
  SUCCESS: 'projects.pipelineSucceeded', FAILURE: 'projects.analysisRunFailed',
};

const REMOVED_DIFF_PREFIX = '移除';
const STEPS = ['projects.stepDetectChange', 'projects.stepExtractEvidence', 'projects.stepMapSkills', 'projects.stepDraftContent', 'projects.stepRiskReview', 'projects.stepCreatePr', 'projects.stepPublish', 'projects.stepFinish'];

export default function Pipeline() {
  const t = useT();
  const locale = useLocale();
  const riskLabel = (risk: string) => RISK_LABEL_KEYS[risk] ? t(RISK_LABEL_KEYS[risk]) : risk;
  const statusLabel = (status: string) => locale === 'en' ? t(STATUS_KEYS[status] ?? 'projects.statusUnknown', {status}) : status;
  const navigate = useNavigate();
  const { toast } = useAppState();
  const [runs, setRuns] = useState<RunView[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [rollbackOpen, setRollbackOpen] = useState<RunView | null>(null);

  const reload = useCallback(() => {
    setLoading(true);
    api.get<{ items: RunView[] }>('/career-assets/updates')
      .then((d) => { setRuns(d.items); setErr(null); })
      .catch((e) => { setErr(e.message || String(e)); setRuns(null); })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const rollback = async (r: RunView) => {
    setBusy(true);
    try {
      await api.post(`/career-assets/updates/${r.id}/rollback`);
      toast('↩️', translate('projects.rollbackSucceeded'));
      setRollbackOpen(null);
      reload();
    } catch (e: any) {
      toast('⚠️', translate('projects.rollbackFailed', {error: String(e instanceof Error ? e.message : e)}));
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
        title={t('projects.portfolioPipeline')}
        sub={latest?.project ? `${latest.project.name} · ${latest.short_code}` : t('projects.pipelineSubtitle')}
      >
        <button className="btn btn-outline" onClick={() => navigate('/assets')}>{t('projects.backToProjectAssets')}</button>
      </PageHead>
      {loading && <div className="hint" style={{ marginBottom: 14 }}>{t('projects.loadingPipeline')}</div>}
      {err && <div className="card" style={{ padding: '24px 20px', marginBottom: 16 }}><b>{t('projects.pipelineLoadFailed')}</b><p className="hint" style={{ marginTop: 6 }}>{err}</p></div>}
      {!loading && !err && (!runs || runs.length === 0) && (
        <div className="card" style={{ padding: '28px 20px' }}>
          <b>{t('projects.noAssetUpdates')}</b>
          <p className="hint" style={{ marginTop: 6 }}>{t('projects.startAssetUpdate')}</p>
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
                    {i < step ? '✓' : i === step ? '⟳' : i + 1} {t(s)}
                  </span>
                </React.Fragment>
              ))}
            </div>
          </div>

          <div className="card" style={{ marginBottom: 18 }}>
            <div className="card-head">
              <span className="card-title">{latest.draft?.title || t('projects.contentDraft')}</span>
              <span className={`chip ${RISK_CLS[latest.risk_level] || 'chip-slate'}`}>{t('projects.riskClassification', {risk: riskLabel(latest.risk_level)})}</span>
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
            <div className="hint" style={{ marginBottom: 6 }}>{t('projects.diffFromPrevious')}</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {(latest.diff_markdown || '').split('\n').filter(Boolean).slice(0, 12).map((c, i) => (
                <div className="list-row" key={i} style={{ padding: '7px 2px' }}>
                  <span className={`chip ${c.startsWith(REMOVED_DIFF_PREFIX) ? 'chip-red' : 'chip-green'}`}>{t(c.startsWith(REMOVED_DIFF_PREFIX) ? 'projects.diffRemoved' : 'projects.diffChanged')}</span>
                  <span style={{ fontSize: 13 }}>{c.replace(/^移除:\s*/, '')}</span>
                </div>
              ))}
            </div>
          </div>

          {latest.publication && (
            <div className="card" style={{ marginBottom: 18 }}>
              <div className="card-head">
                <span className="card-title">{t('projects.publicationNumber', {code: latest.publication.short_code})}</span>
                <span className={`chip ${latest.publication.status === 'PUBLISHED' ? 'chip-green' : latest.publication.status === 'DRY_RUN' ? 'chip-blue' : 'chip-amber'}`}>
                  {statusLabel(latest.publication.status)}
                </span>
              </div>
              <div className="q-meta" style={{ marginBottom: 8 }}>
                {latest.publication.pr_url && <a className="chip chip-slate" href={latest.publication.pr_url} target="_blank" rel="noreferrer">{t('projects.pipelinePrNumber', {number: latest.publication.pr_number ?? ''})}</a>}
                {latest.publication.commit_sha && <span className="chip chip-slate">{t('projects.pipelineCommit', {sha: latest.publication.commit_sha.slice(0, 8)})}</span>}
                <span className="chip chip-slate">{t('projects.buildState', {status: statusLabel(latest.publication.build_status)})}</span>
                {latest.draft && <span className="chip chip-slate">{t('projects.pipelineDraftDetails', {code: latest.draft.short_code, status: statusLabel(latest.draft.status)})}</span>}
              </div>
              {latest.publication.status === 'DRY_RUN' && (
                <div className="hint">{t('projects.dryRunNotice')}</div>
              )}
            </div>
          )}

          <div className="card"><div className="card-title">{t('projects.publishingDisabled')}</div><p className="hint">{t('projects.localDraftsOnly')}</p></div>
        </>
      )}

      {runs && runs.length > 1 && (
        <div className="card mt-18">
          <div className="card-head"><span className="card-title">{t('projects.runHistory')}</span></div>
          {runs.map((r) => (
            <div className="list-row" key={r.id}>
              <div className="list-ic">{r.status === 'PUBLISHED' ? '🚀' : r.status === 'ROLLED_BACK' ? '↩️' : r.status === 'FAILED' ? '⚠️' : '📝'}</div>
              <div className="list-main">
                <div className="list-title">{r.short_code} · {r.project?.name || t('projects.noLinkedProject')}</div>
                <div className="list-sub">
                  {t('projects.pipelineRunDetails', {date: r.created_at ? new Date(r.created_at).toLocaleString(locale) : '', draft: r.draft?.short_code || '-', risk: riskLabel(r.risk_level)})}
                  {r.publication?.pr_number ? t('projects.pipelinePrSuffix', {number: r.publication.pr_number}) : ''}
                </div>
              </div>
              <span className={`chip ${r.status === 'PUBLISHED' ? 'chip-green' : r.status === 'ROLLED_BACK' ? 'chip-slate' : 'chip-amber'}`}>{statusLabel(r.status)}</span>
            </div>
          ))}
        </div>
      )}

      {rollbackOpen && (
        <Modal title={t('projects.rollbackTitle')} size="sm" onClose={() => setRollbackOpen(null)}
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setRollbackOpen(null)}>{t('projects.cancel')}</button>
              <button className="btn btn-danger" disabled={busy} onClick={() => rollback(rollbackOpen)}>{t('projects.confirmRollback')}</button>
            </>
          }>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.7 }}>
            {t('projects.pipelineRollbackDescription')}
          </p>
        </Modal>
      )}
    </div>
  );
}
