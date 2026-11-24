import { useLocale, useT, type Locale } from '../i18n';
import React, { useCallback, useEffect, useState } from 'react';
import { useAppState } from '../components/AppState';
import { DemoBanner, Modal, PageHead } from '../components/ui';
import { api, ApiError, downloadWorkspaceExport } from '../api/client';
import type { AuditItem, PublicationRule, SettingsOverview } from '../api/client';
import {Link, useSearchParams} from 'react-router-dom';
import {AutopilotSettingsForm} from '../components/ProjectAutopilot';

const TABS = [
  { key: 'account', name: 'shell.account', ic: '👤' },
  { key: 'workspace', name: 'shell.workspace', ic: '🗂️' },
  { key: 'models', name: 'shell.modelAgentConnections', ic: '🔌' },
  { key: 'data', name: 'shell.dataExportDelete', ic: '📦' },
  { key: 'rules', name: 'shell.automationRules', ic: '⚙️' },
] as const;

type TabKey = (typeof TABS)[number]['key'];
type SettingsToast = ReturnType<typeof useAppState>['toast'];

const fmtDate = (locale: Locale, iso?: string | null) =>
  iso ? new Date(iso).toLocaleString(locale, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';

const ACTION_META: Record<string, { label: string; cls: string }> = {
  PUBLISH: { label: 'shell.auditPublish', cls: 'chip-green' },
  APPROVE: { label: 'shell.auditApprove', cls: 'chip-green' },
  ROLLBACK: { label: 'shell.auditRollback', cls: 'chip-amber' },
  REJECT: { label: 'shell.auditReject', cls: 'chip-red' },
  CREATE: { label: 'shell.auditCreate', cls: 'chip-slate' },
  UPDATE: { label: 'shell.auditUpdate', cls: 'chip-slate' },
  DELETE: { label: 'shell.auditDelete', cls: 'chip-red' },
  VALIDATE: { label: 'shell.auditValidate', cls: 'chip-slate' },
};

export default function Settings({ registered = false }: { registered?: boolean }) {
  const t = useT();
  const { toast } = useAppState();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = TABS.find(t => t.key === searchParams.get('tab'))?.key ?? 'workspace';
  const setTab = (key: TabKey) => setSearchParams(previous => {
    const next = new URLSearchParams(previous);
    next.set('tab', key);
    return next;
  });
  const [ov, setOv] = useState<SettingsOverview | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadErr(null);
    try {
      setOv(await api.get<SettingsOverview>('/settings/overview'));
    } catch (e) {
      setLoadErr(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  return (
    <div>
      <PageHead title={t('shell.settingsWorkspace')} sub={t('shell.settingsSubtitle')} />
      {registered && <nav className="bank-actions" aria-label={t('shell.accountAndBeta')}>
        <Link to="/settings/account">{t('shell.accountProfile')}</Link>
        <Link to="/beta">{t('shell.betaRecords')}</Link>
        <Link to="/beta/bank?import=1">{t('shell.betaImport')}</Link>
        <Link to="/beta/study/chat">{t('shell.betaCoach')}</Link>
        <Link to="/beta/study/notes">{t('shell.betaNotes')}</Link>
        <Link to="/beta/memory">{t('shell.betaMemory')}</Link>
        <Link to="/beta/learning">{t('shell.betaCalendar')}</Link>
      </nav>}
      {ov?.workspace.is_demo && <DemoBanner />}

      <div className="settings-layout">
        {/* 左侧设置导航 */}
        <div className="card" style={{ padding: 8 }}>
          {TABS.map((item) => (
            <button
              key={item.key}
              className={`settings-tab ${tab === item.key ? 'active' : ''}`}
              onClick={() => setTab(item.key)}
              style={{ display: 'flex', alignItems: 'center', gap: 10, width: '100%', padding: '11px 14px', borderRadius: 10, border: 'none', background: 'transparent', cursor: 'pointer', textAlign: 'left', fontSize: 13.5 }}
            >
              <span>{item.ic}</span>
              <span>{t(item.name)}</span>
            </button>
          ))}
        </div>

        {/* 右侧面板 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {loading && <div className="card"><p className="hint">{t('shell.loading')}</p></div>}
          {loadErr && !loading && (
            <div className="card">
              <div className="card-head"><span className="card-title" style={{ color: 'var(--red)' }}>{t('shell.backendUnavailable')}</span></div>
              <p className="hint" style={{ margin: '4px 0 12px' }}>{t('shell.backendRequirement', { error: loadErr })}</p>
              <button className="btn btn-outline btn-sm" onClick={() => void load()}>{t('shell.retry')}</button>
            </div>
          )}
          {!loading && !loadErr && ov && (
            <>
              {tab === 'account' && <AccountTab ov={ov} reload={load} toast={toast} />}
              {tab === 'workspace' && <WorkspaceTab ov={ov} reload={load} toast={toast} />}
              {tab === 'models' && <ModelsTab toast={toast} />}
              {tab === 'data' && <DataTab ov={ov} toast={toast} />}
              {tab === 'rules' && <><AutopilotSettingsForm/><RulesTab /></>}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------------- 账号(可编辑) ---------------- */
function AccountTab({ ov, reload, toast }: { ov: SettingsOverview; reload: () => Promise<void>; toast: SettingsToast }) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(ov.account.display_name || '');
  const [email, setEmail] = useState(ov.account.email || '');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!name.trim() || !email.trim()) { toast('⚠️', t => t('shell.accountRequired')); return; }
    if (!email.includes('@')) { toast('⚠️', t => t('shell.emailInvalid')); return; }
    setSaving(true);
    try {
      await api.patch('/settings/account', { display_name: name.trim(), email: email.trim() });
      await reload();
      setEditing(false);
      toast('✅', t => t('shell.accountUpdated'));
    } catch (e) {
      toast('❌', t => t('shell.saveFailed', { error: e instanceof ApiError ? e.message : String(e) }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">{t('shell.account')}</span>
        {!editing && <button className="btn btn-outline btn-sm" onClick={() => { setName(ov.account.display_name || ''); setEmail(ov.account.email || ''); setEditing(true); }}>{t('shell.edit')}</button>}
      </div>
      <div className="list-row">
        <div className="list-ic avatar">🧑</div>
        {!editing ? (
          <div className="list-main">
            <div className="list-title">{ov.account.display_name} · {ov.account.email}</div>
            <div className="list-sub">{t('shell.accountMemberships', { count: ov.members.length })}</div>
          </div>
        ) : (
          <div className="list-main" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <div className="field"><label>{t('shell.displayName')}</label><input className="inp" value={name} onChange={(e) => setName(e.target.value)} /></div>
            <div className="field"><label>{t('shell.email')}</label><input className="inp" value={email} onChange={(e) => setEmail(e.target.value)} /></div>
          </div>
        )}
      </div>
      {editing && (
        <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
          <button className="btn btn-primary btn-sm" disabled={saving} onClick={() => void save()}>{saving ? t('shell.saving') : t('shell.save')}</button>
          <button className="btn btn-outline btn-sm" onClick={() => setEditing(false)}>{t('shell.cancel')}</button>
        </div>
      )}
      <p className="hint" style={{ marginTop: 8 }}>{t('shell.accountStorage')}</p>
    </div>
  );
}

/* ---------------- 工作空间 ---------------- */
function WorkspaceTab({ ov, reload, toast }: { ov: SettingsOverview; reload: () => Promise<void>; toast: SettingsToast }) {
  const t = useT();
  const locale = useLocale();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(ov.workspace.name);
  const [role, setRole] = useState(ov.workspace.target_role);
  const [level, setLevel] = useState(ov.workspace.target_level);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!name.trim() || !role.trim() || !level.trim()) { toast('⚠️', t => t('shell.workspaceFieldsRequired')); return; }
    setSaving(true);
    try {
      await api.patch('/settings/workspace', { name: name.trim(), target_role: role.trim(), target_level: level.trim() });
      await reload();
      setEditing(false);
      toast('✅', t => t('shell.workspaceUpdated'));
    } catch (e) {
      toast('❌', t => t('shell.saveFailed', { error: e instanceof ApiError ? e.message : String(e) }));
    } finally {
      setSaving(false);
    }
  };

  const c = ov.counts;
  return (
    <>
      <div className="card">
        <div className="card-head">
          <span className="card-title">{t('shell.workspaceDetails')}</span>
          {!editing && <button className="btn btn-outline btn-sm" onClick={() => { setName(ov.workspace.name); setRole(ov.workspace.target_role); setLevel(ov.workspace.target_level); setEditing(true); }}>{t('shell.edit')}</button>}
        </div>
        {!editing ? (
          <div className="ws-info" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px 24px', padding: '6px 0' }}>
            {([
              ['shell.name', ov.workspace.name],
              ['shell.slug', ov.workspace.slug],
              ['shell.targetRole', t('shell.roleAndLevel', { role: ov.workspace.target_role, level: ov.workspace.target_level })],
              ['shell.workspaceType', ov.workspace.is_demo ? t('shell.workspaceTypeDemo') : t('shell.workspaceTypePersonal')],
              ['shell.createdAt', fmtDate(locale, ov.workspace.created_at)],
              ['shell.totalEvidence', t('shell.evidenceCount', { count: c.evidence })],
            ] as [string, string][]).map(([k, v]) => (
              <div key={k} style={{ display: 'flex', gap: 10, fontSize: 13 }}>
                <span style={{ color: 'var(--text-3)', width: 84, flexShrink: 0 }}>{t(k)}</span>
                <b style={{ fontWeight: 600 }}>{v}</b>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '6px 0' }}>
            <div className="form-row" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div className="field"><label>{t('shell.name')}</label><input className="inp" value={name} onChange={(e) => setName(e.target.value)} /></div>
              <div className="field"><label>{t('shell.targetLevel')}</label><input className="inp" value={level} onChange={(e) => setLevel(e.target.value)} placeholder={t('shell.levelExample')} /></div>
            </div>
            <div className="field"><label>{t('shell.targetRole')}</label><input className="inp" value={role} onChange={(e) => setRole(e.target.value)} /></div>
            <div style={{ display: 'flex', gap: 10 }}>
              <button className="btn btn-primary btn-sm" disabled={saving} onClick={() => void save()}>{saving ? t('shell.saving') : t('shell.save')}</button>
              <button className="btn btn-outline btn-sm" onClick={() => setEditing(false)}>{t('shell.cancel')}</button>
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-head"><span className="card-title">{t('shell.membersCount', { count: ov.members.length })}</span></div>
        {ov.members.map((m) => (
          <div className="list-row" key={m.email}>
            <div className="list-ic avatar">🧑</div>
            <div className="list-main">
              <div className="list-title">{t('shell.memberWithRole', { name: m.display_name, role: t(m.role === 'owner' ? 'shell.workspaceOwner' : m.role === 'admin' ? 'shell.workspaceAdmin' : 'shell.workspaceMember') })}</div>
              <div className="list-sub">{m.email}</div>
            </div>
          </div>
        ))}
        <p className="hint" style={{ marginTop: 8 }}>{t('shell.noInvitations')}</p>
      </div>
    </>
  );
}

/* ---------------- 模型与连接(全可配置) ---------------- */
type LlmCfg = {
  active: boolean; provider: string | null; base_url: string | null; model: string | null;
  api_key_set: boolean; source: 'db' | 'env' | 'none';
};
type LlmTestResult = { ok: true; latency_ms: number } | { ok: false; error: string };
type GhCfg = {
  active: boolean; token_hint: string | null; source: 'db' | 'env' | 'none';
  target_repo?: string; base_branch?: string; content_dir?: string;
};

function ModelsTab({ toast }: { toast: SettingsToast }) {
  const t = useT();
  // ---- LLM 配置 ----
  const [llm, setLlm] = useState<LlmCfg | null>(null);
  const [provider, setProvider] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [llmSaving, setLlmSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testRes, setTestRes] = useState<LlmTestResult | 'unavailable' | null>(null);
  const llmBusy = llmSaving || testing;
  const [llmErr, setLlmErr] = useState<string | null>(null);

  // ---- GitHub 配置 ----
  const [gh, setGh] = useState<GhCfg | null>(null);
  const [ghToken, setGhToken] = useState('');
  const [ghSaving, setGhSaving] = useState(false);

  const loadLlm = useCallback(async () => {
    try {
      const c = await api.get<LlmCfg>('/settings/llm');
      setLlm(c);
      setProvider(c.provider || 'gateway');
      setBaseUrl(c.base_url || '');
      setModel(c.model || '');
      setApiKey(''); // 密钥不回显, 空=保留现有
      setLlmErr(null);
    } catch (e) {
      setLlmErr(e instanceof ApiError ? e.message : String(e));
    }
  }, []);
  const loadGh = useCallback(async () => {
    try { setGh(await api.get<GhCfg>('/settings/github')); } catch { /* 忽略, 卡片显示未配置 */ }
  }, []);
  useEffect(() => { void loadLlm(); void loadGh(); }, [loadLlm, loadGh]);

  const saveLlm = async () => {
    setLlmSaving(true);
    setTestRes(null);
    try {
      await api.put('/settings/llm', {
        provider: provider.trim() || undefined,
        base_url: baseUrl.trim() || undefined,
        model: model.trim() || undefined,
        api_key: apiKey.trim(), // 空字符串 = 保留现有密钥
      });
      await loadLlm();
      toast('✅', t => t('shell.llmSaved'));
    } catch (e) {
      toast('❌', t => t('shell.saveFailed', { error: e instanceof ApiError ? e.message : String(e) }));
    } finally {
      setLlmSaving(false);
    }
  };

  const testLlm = async () => {
    setTesting(true);
    setTestRes(null);
    try {
      const r = await api.post<LlmTestResult>('/settings/llm/test', {
        provider: provider.trim() || undefined,
        base_url: baseUrl.trim() || undefined,
        model: model.trim() || undefined,
        api_key: apiKey.trim() || undefined, // 测试允许用未保存的新 key 试连
      });
      setTestRes(r);
    } catch {
      setTestRes('unavailable');
    } finally {
      setTesting(false);
    }
  };

  const resetLlm = async () => {
    setLlmSaving(true);
    setTestRes(null);
    try {
      await api.del('/settings/llm');
      await loadLlm();
      toast('↩️', t => t('shell.llmReset'));
    } catch (e) {
      toast('❌', t => t('shell.resetFailed', { error: String(e) }));
    } finally {
      setLlmSaving(false);
    }
  };

  const saveGh = async () => {
    if (!ghToken.trim()) { toast('⚠️', t => t('shell.githubTokenRequired')); return; }
    setGhSaving(true);
    try {
      await api.put('/settings/github', { token: ghToken.trim() });
      setGhToken('');
      await loadGh();
      toast('✅', t => t('shell.githubTokenSaved'));
    } catch (e) {
      toast('❌', t => t('shell.saveFailed', { error: e instanceof ApiError ? e.message : String(e) }));
    } finally {
      setGhSaving(false);
    }
  };

  const resetGh = async () => {
    try {
      await api.del('/settings/github');
      await loadGh();
      toast('↩️', t => t('shell.githubTokenReset'));
    } catch (e) {
      toast('❌', t => t('shell.resetFailed', { error: String(e) }));
    }
  };

  const srcChip = (s?: string) => s === 'db' ? <span className="chip chip-purple">{t('shell.sourceSettings')}</span> : s === 'env' ? <span className="chip chip-slate">{t('shell.sourceEnv')}</span> : null;

  return (
    <>
      <div className="card">
        <div className="card-head">
          <span className="card-title">{t('shell.llmConnection')}</span>
          {llm && <span className={`chip ${llm.active ? 'chip-blue' : 'chip-slate'}`}>{llm.active ? t('shell.configured') : t('shell.unconfigured')}</span>}
        </div>
        {llmErr && <p className="hint" style={{ color: 'var(--red)' }}>{t('shell.loadFailed', { error: llmErr })}</p>}
        {llm && (
          <>
            <p className="hint" style={{ margin: '4px 0 12px' }}>
              {t('shell.activeModel', {
                provider: llm.provider || '—',
                model: llm.model || '—',
                keyStatus: t(llm.api_key_set ? 'shell.secretSet' : 'shell.secretUnset'),
              })} {srcChip(llm.source)}
              <br />{t('shell.llmTestHint')}
            </p>
            <div className="form-row" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div className="field">
                <label>{t('shell.providerLabel')}</label>
                <input className="inp" value={provider} disabled={llmBusy} onChange={(e) => { setProvider(e.target.value); setTestRes(null); }} placeholder="gateway / deepseek / glm" />
              </div>
              <div className="field">
                <label>{t('shell.modelName')}</label>
                <input className="inp" value={model} disabled={llmBusy} onChange={(e) => { setModel(e.target.value); setTestRes(null); }} placeholder={t('shell.modelExample')} />
              </div>
            </div>
            <div className="field">
              <label>{t('shell.baseUrl')}</label>
              <input className="inp" value={baseUrl} disabled={llmBusy} onChange={(e) => { setBaseUrl(e.target.value); setTestRes(null); }} placeholder="https://…/v1" />
            </div>
            <div className="field">
              <label>{t('shell.apiKey')}{' '}{llm.api_key_set && <span className="hint" style={{ display: 'inline' }}>{t('shell.keepSecretHint')}</span>}</label>
              <input className="inp" type="password" value={apiKey} disabled={llmBusy} onChange={(e) => { setApiKey(e.target.value); setTestRes(null); }} placeholder={t('shell.newKeyPlaceholder')} />
            </div>
            <div style={{ display: 'flex', gap: 10, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <button className="btn btn-primary btn-sm" disabled={llmBusy} onClick={() => void saveLlm()}>{llmSaving ? t('shell.saving') : t('shell.save')}</button>
              <button className="btn btn-outline btn-sm" disabled={llmBusy} onClick={() => void testLlm()}>{testing ? t('shell.testingConnection') : t('shell.testConnection')}</button>
              {llm.source === 'db' && <button className="btn btn-ghost btn-sm" disabled={llmBusy} onClick={() => void resetLlm()}>{t('shell.restoreEnv')}</button>}
            </div>
            {testRes && (
              <div className="hint" role="status" style={{ marginTop: 10, color: testRes !== 'unavailable' && testRes.ok ? 'var(--green)' : 'var(--red)' }}>
                {testRes !== 'unavailable' && testRes.ok
                  ? t('shell.connectionPassed', { latency: testRes.latency_ms })
                  : t('shell.connectionFailed', { error: testRes === 'unavailable' ? t('shell.llmTestUnavailable') : testRes.error })}
              </div>
            )}
          </>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <span className="card-title">{t('shell.githubPublishing')}</span>
          {gh && <span className={`chip ${gh.active ? 'chip-green' : 'chip-slate'}`}>{t('shell.publicWritesOff')}</span>}
        </div>
        {gh && (
          <>
            <p className="hint" style={{ margin: '4px 0 12px' }}>
              {t('shell.activeGithubToken', { token: gh.token_hint || t('shell.secretUnset') })} {srcChip(gh.source)}
              {gh.active && t('shell.githubTarget', {
                repo: gh.target_repo ?? '',
                directory: gh.content_dir ?? '',
                branch: gh.base_branch ?? '',
              })}
              <br />{t('shell.githubTokenPermissions')}
            </p>
            <div className="field">
              <label>{t('shell.githubPat')}{' '}{gh.token_hint && <span className="hint" style={{ display: 'inline' }}>{t('shell.githubTokenSet', { token: gh.token_hint })}</span>}</label>
              <input className="inp" type="password" value={ghToken} onChange={(e) => setGhToken(e.target.value)} placeholder={t('shell.githubTokenPlaceholder')} />
            </div>
            <div style={{ display: 'flex', gap: 10, marginTop: 10 }}>
              <button className="btn btn-primary btn-sm" disabled={ghSaving || !ghToken.trim()} onClick={() => void saveGh()}>{ghSaving ? t('shell.saving') : t('shell.save')}</button>
              {gh.source === 'db' && <button className="btn btn-ghost btn-sm" onClick={() => void resetGh()}>{t('shell.restoreEnv')}</button>}
            </div>
          </>
        )}
      </div>

      <div className="card">
        <div className="card-head"><span className="card-title">{t('shell.integrationUnavailableTitle')}</span></div>
        {[
          ['⚡', 'shell.codingAgent', 'shell.codingAgentHint'],
          ['🔎', 'shell.searchIntegration', 'shell.searchIntegrationHint'],
          ['🧩', 'shell.mcpAdapter', 'shell.mcpAdapterHint'],
        ].map(([ic, nameKey, descKey]) => (
          <div className="list-row" key={nameKey}>
            <div className="list-ic">{ic}</div>
            <div className="list-main">
              <div className="list-title">{t(nameKey)}</div>
              <div className="list-sub">{t(descKey)}</div>
            </div>
            <span className="chip chip-slate">{t('shell.integrationUnavailable')}</span>
          </div>
        ))}
      </div>
    </>
  );
}

/* ---------------- 数据导出与删除 ---------------- */
function DataTab({ ov, toast }: { ov: SettingsOverview; toast: SettingsToast }) {
  const t = useT();
  const [delOpen, setDelOpen] = useState(false);
  const [confirm, setConfirm] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [slugBefore, slugAfter] = t('shell.confirmSlug', { slug: '{slug}' }).split('{slug}');

  const doExport = async () => {
    setExporting(true);
    try {
      const blob = await downloadWorkspaceExport();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `careerpilot-export-${ov.workspace.slug}-${new Date().toISOString().slice(0, 10).split('-').join('')}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
      toast('📦', t => t('shell.exportDone'));
    } catch (e) {
      toast('❌', t => t('shell.exportFailed', { error: String(e) }));
    } finally {
      setExporting(false);
    }
  };

  const doDelete = async () => {
    if (confirm !== ov.workspace.slug) { toast('⚠️', t => t('shell.confirmSlugError', { slug: ov.workspace.slug })); return; }
    setDeleting(true);
    try {
      await api.del('/settings/workspace', { confirm_slug: confirm });
      toast('🗑️', t => t('shell.workspaceDeleted'));
      setDelOpen(false);
      setTimeout(() => window.location.reload(), 1200);
    } catch (e) {
      toast('❌', t => t('shell.deleteFailed', { error: e instanceof ApiError ? e.message : String(e) }));
    } finally {
      setDeleting(false);
    }
  };

  const c = ov.counts;
  return (
    <>
      <div className="card">
        <div className="card-head"><span className="card-title">{t('shell.dataExport')}</span></div>
        <p className="hint" style={{ margin: '4px 0 12px' }}>
          {t('shell.exportSummary', c)}
        </p>
        <button className="btn btn-outline" disabled={exporting} onClick={() => void doExport()}>
          {exporting ? t('shell.exporting') : t('shell.exportJson')}
        </button>
      </div>
      <div className="card" style={{ border: '1px solid var(--red-2, #fecaca)' }}>
        <div className="card-head"><span className="card-title" style={{ color: 'var(--red)' }}>{t('shell.dangerZone')}</span></div>
        <p className="hint" style={{ margin: '4px 0 12px' }}>
          {t('shell.deleteWorkspaceHint')}
        </p>
        <button className="btn btn-danger" onClick={() => { setConfirm(''); setDelOpen(true); }}>{t('shell.deleteWorkspace')}</button>
      </div>

      {delOpen && (
        <Modal title={t('shell.deleteWorkspaceTitle')} size="sm" onClose={() => setDelOpen(false)}
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setDelOpen(false)}>{t('shell.cancel')}</button>
              <button className="btn btn-danger" disabled={deleting || confirm !== ov.workspace.slug} onClick={() => void doDelete()}>
                {deleting ? t('shell.deleting') : t('shell.confirmDelete')}
              </button>
            </>
          }>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.7 }}>
            {t('shell.deleteSummary', { ...c, name: ov.workspace.name })}
          </p>
          <div className="field" style={{ marginTop: 10 }}>
            <label>{slugBefore}<b>{ov.workspace.slug}</b>{slugAfter}</label>
            <input className="inp" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder={ov.workspace.slug} />
          </div>
        </Modal>
      )}
    </>
  );
}

/* ---------------- 自动化规则与审计 ---------------- */
const BUILTIN_CHECKS = [
  ['shell.privacyScan', 'shell.privacyScanHint'],
  ['shell.factCheck', 'shell.factCheckHint'],
  ['shell.copyrightSources', 'shell.copyrightSourcesHint'],
  ['shell.markdownCheck', 'shell.markdownCheckHint'],
];

function RulesTab() {
  const t = useT();
  const locale = useLocale();
  const [, setRules] = useState<PublicationRule[] | null>(null);
  const [audit, setAudit] = useState<AuditItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [r, a] = await Promise.all([
        api.get<{ items: PublicationRule[] }>('/public-content/rules'),
        api.get<{ items: AuditItem[] }>('/public-content/audit'),
        api.get<SettingsOverview>('/settings/overview'),
      ]);
      setRules(r.items);
      setAudit(a.items);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  return (
    <>
      <div className="card">
        <div className="card-head"><span className="card-title">{t('shell.publishingPermissions')}</span></div>
        <div className="list-row">
          <div className="list-main">
            <div className="list-title">{t('shell.publicContentDisabled')}</div>
            <div className="list-sub">{t('shell.localDraftsOnly')}</div>
          </div>
          <button
            className="switch"
            aria-label={t('shell.publicPublishDisabled')}
            disabled
            style={{ cursor: busy ? 'wait' : 'pointer' }}
          />
        </div>
        <p className="hint" style={{ marginTop: 8 }}>
          {t('shell.serverBlocksPublishing')}
        </p>
      </div>

      <div className="card">
        <div className="card-head"><span className="card-title">{t('shell.builtInChecks')}</span></div>
        {BUILTIN_CHECKS.map(([nameKey, descKey]) => (
          <div className="list-row" key={nameKey}>
            <div className="list-main">
              <div className="list-title">{t(nameKey)}</div>
              <div className="list-sub">{t(descKey)}</div>
            </div>
            <span className="chip chip-green">{t('shell.checkActive')}</span>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="card-head"><span className="card-title">{t('shell.auditLog')}</span></div>
        {err && <p className="hint" style={{ color: 'var(--red)' }}>{t('shell.loadFailed', { error: err })}</p>}
        {!err && (audit?.length ?? 0) === 0 && <p className="hint">{t('shell.auditEmpty')}</p>}
        {audit?.slice(0, 30).map((a) => {
          const meta = ACTION_META[a.action];
          const label = meta ? t(meta.label) : a.action;
          const d = a.detail || {};
          const what = [d.pr ? `PR #${d.pr}` : '', d.status || d.to || d.from || '', d.dry_run ? t('shell.auditDryRun') : '']
            .filter(Boolean).join(' · ');
          const action = what ? t('shell.auditDetail', { action: label, detail: what }) : label;
          return (
            <div className="list-row" key={a.id}>
              <div className="list-ic">📜</div>
              <div className="list-main">
                <div className="list-title">{a.draft_id ? t('shell.auditDraft', { action, id: a.draft_id }) : action}</div>
                <div className="list-sub">{fmtDate(locale, a.created_at)}</div>
              </div>
              <span className={`chip ${meta?.cls ?? 'chip-slate'}`}>{label}</span>
            </div>
          );
        })}
      </div>
    </>
  );
}
