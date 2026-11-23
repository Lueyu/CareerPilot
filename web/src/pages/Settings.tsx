import React, { useCallback, useEffect, useState } from 'react';
import { useAppState } from '../components/AppState';
import { DemoBanner, Modal, PageHead } from '../components/ui';
import { api, ApiError, downloadWorkspaceExport } from '../api/client';
import type { AuditItem, PublicationRule, SettingsOverview } from '../api/client';
import {Link, useSearchParams} from 'react-router-dom';
import {AutopilotSettingsForm} from '../components/ProjectAutopilot';

const TABS = [
  { key: 'account', name: '账号', ic: '👤' },
  { key: 'workspace', name: '工作空间', ic: '🗂️' },
  { key: 'models', name: '模型与 Agent 连接', ic: '🔌' },
  { key: 'data', name: '数据导出与删除', ic: '📦' },
  { key: 'rules', name: '自动化规则与发布权限', ic: '⚙️' },
] as const;

type TabKey = (typeof TABS)[number]['key'];

const fmtDate = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';

const ACTION_META: Record<string, { label: string; cls: string }> = {
  PUBLISH: { label: '发布', cls: 'chip-green' },
  APPROVE: { label: '审核通过', cls: 'chip-green' },
  ROLLBACK: { label: '回滚', cls: 'chip-amber' },
  REJECT: { label: '拒绝', cls: 'chip-red' },
  CREATE: { label: '创建', cls: 'chip-slate' },
  UPDATE: { label: '更新', cls: 'chip-slate' },
  DELETE: { label: '删除', cls: 'chip-red' },
  VALIDATE: { label: '校验', cls: 'chip-slate' },
};

export default function Settings({ registered = false }: { registered?: boolean }) {
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
      <PageHead title="设置与工作空间" sub="管理账号、模型连接、数据与自动化权限" />
      {registered && <nav className="bank-actions" aria-label="账户和 Beta 记录">
        <Link to="/settings/account">账户与资料</Link>
        <Link to="/beta">Beta 记录</Link>
        <Link to="/beta/bank?import=1">Beta 导入</Link>
        <Link to="/beta/study/chat">Beta 教练</Link>
        <Link to="/beta/study/notes">Beta 笔记</Link>
        <Link to="/beta/memory">Beta 记忆</Link>
        <Link to="/beta/learning">Beta 学习日历</Link>
      </nav>}
      {ov?.workspace.is_demo && <DemoBanner />}

      <div className="settings-layout" style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 18, alignItems: 'start' }}>
        {/* 左侧设置导航 */}
        <div className="card" style={{ padding: 8 }}>
          {TABS.map((t) => (
            <button
              key={t.key}
              className={`settings-tab ${tab === t.key ? 'active' : ''}`}
              onClick={() => setTab(t.key)}
              style={{ display: 'flex', alignItems: 'center', gap: 10, width: '100%', padding: '11px 14px', borderRadius: 10, border: 'none', background: 'transparent', cursor: 'pointer', textAlign: 'left', fontSize: 13.5 }}
            >
              <span>{t.ic}</span>
              <span>{t.name}</span>
            </button>
          ))}
        </div>

        {/* 右侧面板 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          {loading && <div className="card"><p className="hint">加载中…</p></div>}
          {loadErr && !loading && (
            <div className="card">
              <div className="card-head"><span className="card-title" style={{ color: 'var(--red)' }}>无法连接后端</span></div>
              <p className="hint" style={{ margin: '4px 0 12px' }}>{loadErr}（设置页读取真实数据，需要后端 127.0.0.1:8000 可用）</p>
              <button className="btn btn-outline btn-sm" onClick={() => void load()}>重试</button>
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
function AccountTab({ ov, reload, toast }: { ov: SettingsOverview; reload: () => Promise<void>; toast: (ic: string, msg: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(ov.account.display_name || '');
  const [email, setEmail] = useState(ov.account.email || '');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!name.trim() || !email.trim()) { toast('⚠️', '昵称与邮箱都不能为空'); return; }
    if (!email.includes('@')) { toast('⚠️', '邮箱格式不正确'); return; }
    setSaving(true);
    try {
      await api.patch('/settings/account', { display_name: name.trim(), email: email.trim() });
      await reload();
      setEditing(false);
      toast('✅', '账号信息已更新');
    } catch (e) {
      toast('❌', `保存失败: ${e instanceof ApiError ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card">
      <div className="card-head">
        <span className="card-title">账号</span>
        {!editing && <button className="btn btn-outline btn-sm" onClick={() => { setName(ov.account.display_name || ''); setEmail(ov.account.email || ''); setEditing(true); }}>编辑</button>}
      </div>
      <div className="list-row">
        <div className="list-ic avatar">🧑</div>
        {!editing ? (
          <div className="list-main">
            <div className="list-title">{ov.account.display_name} · {ov.account.email}</div>
            <div className="list-sub">个人账号 · 拥有 {ov.members.length} 个成员身份</div>
          </div>
        ) : (
          <div className="list-main" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <div className="field"><label>昵称</label><input className="inp" value={name} onChange={(e) => setName(e.target.value)} /></div>
            <div className="field"><label>邮箱</label><input className="inp" value={email} onChange={(e) => setEmail(e.target.value)} /></div>
          </div>
        )}
      </div>
      {editing && (
        <div style={{ display: 'flex', gap: 10, marginTop: 4 }}>
          <button className="btn btn-primary btn-sm" disabled={saving} onClick={() => void save()}>{saving ? '保存中…' : '保存'}</button>
          <button className="btn btn-outline btn-sm" onClick={() => setEditing(false)}>取消</button>
        </div>
      )}
      <p className="hint" style={{ marginTop: 8 }}>账号信息存本地数据库（单用户本地部署）；邮箱用于数据导出标识。</p>
    </div>
  );
}

/* ---------------- 工作空间 ---------------- */
function WorkspaceTab({ ov, reload, toast }: { ov: SettingsOverview; reload: () => Promise<void>; toast: (ic: string, msg: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(ov.workspace.name);
  const [role, setRole] = useState(ov.workspace.target_role);
  const [level, setLevel] = useState(ov.workspace.target_level);
  const [saving, setSaving] = useState(false);

  const save = async () => {
    if (!name.trim() || !role.trim() || !level.trim()) { toast('⚠️', '名称 / 目标岗位 / 对标职级都不能为空'); return; }
    setSaving(true);
    try {
      await api.patch('/settings/workspace', { name: name.trim(), target_role: role.trim(), target_level: level.trim() });
      await reload();
      setEditing(false);
      toast('✅', '工作空间已更新');
    } catch (e) {
      toast('❌', `保存失败: ${e instanceof ApiError ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const c = ov.counts;
  return (
    <>
      <div className="card">
        <div className="card-head">
          <span className="card-title">工作空间信息</span>
          {!editing && <button className="btn btn-outline btn-sm" onClick={() => { setName(ov.workspace.name); setRole(ov.workspace.target_role); setLevel(ov.workspace.target_level); setEditing(true); }}>编辑</button>}
        </div>
        {!editing ? (
          <div className="ws-info" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px 24px', padding: '6px 0' }}>
            {([
              ['名称', ov.workspace.name],
              ['标识', ov.workspace.slug],
              ['目标岗位', `${ov.workspace.target_role} · ${ov.workspace.target_level}`],
              ['性质', ov.workspace.is_demo ? '演示工作空间（is_demo）' : '正式工作空间'],
              ['创建时间', fmtDate(ov.workspace.created_at)],
              ['证据总数', `${c.evidence} 条`],
            ] as [string, string][]).map(([k, v]) => (
              <div key={k} style={{ display: 'flex', gap: 10, fontSize: 13 }}>
                <span style={{ color: 'var(--text-3)', width: 84, flexShrink: 0 }}>{k}</span>
                <b style={{ fontWeight: 600 }}>{v}</b>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, padding: '6px 0' }}>
            <div className="form-row" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div className="field"><label>名称</label><input className="inp" value={name} onChange={(e) => setName(e.target.value)} /></div>
              <div className="field"><label>对标职级</label><input className="inp" value={level} onChange={(e) => setLevel(e.target.value)} placeholder="如 P6" /></div>
            </div>
            <div className="field"><label>目标岗位</label><input className="inp" value={role} onChange={(e) => setRole(e.target.value)} /></div>
            <div style={{ display: 'flex', gap: 10 }}>
              <button className="btn btn-primary btn-sm" disabled={saving} onClick={() => void save()}>{saving ? '保存中…' : '保存'}</button>
              <button className="btn btn-outline btn-sm" onClick={() => setEditing(false)}>取消</button>
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-head"><span className="card-title">成员（{ov.members.length}）</span></div>
        {ov.members.map((m) => (
          <div className="list-row" key={m.email}>
            <div className="list-ic avatar">🧑</div>
            <div className="list-main">
              <div className="list-title">{m.display_name}（{m.role === 'owner' ? '所有者' : m.role === 'admin' ? '管理员' : '成员'}）</div>
              <div className="list-sub">{m.email}</div>
            </div>
          </div>
        ))}
        <p className="hint" style={{ marginTop: 8 }}>当前版本为单用户本地部署，邀请成员暂未开放。</p>
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

function ModelsTab({ toast }: { toast: (ic: string, msg: string) => void }) {
  // ---- LLM 配置 ----
  const [llm, setLlm] = useState<LlmCfg | null>(null);
  const [provider, setProvider] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [llmSaving, setLlmSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testRes, setTestRes] = useState<LlmTestResult | null>(null);
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
      toast('✅', 'LLM 连接已保存并立即生效');
    } catch (e) {
      toast('❌', `保存失败: ${e instanceof ApiError ? e.message : String(e)}`);
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
      setTestRes({ ok: false, error: '无法完成连接测试，请稍后重试。' });
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
      toast('↩️', '已恢复 server/.env 默认 LLM 配置');
    } catch (e) {
      toast('❌', `重置失败: ${String(e)}`);
    } finally {
      setLlmSaving(false);
    }
  };

  const saveGh = async () => {
    if (!ghToken.trim()) { toast('⚠️', '粘贴新的 GitHub token（留空不保存）'); return; }
    setGhSaving(true);
    try {
      await api.put('/settings/github', { token: ghToken.trim() });
      setGhToken('');
      await loadGh();
      toast('✅', 'GitHub token 已保存并立即生效');
    } catch (e) {
      toast('❌', `保存失败: ${e instanceof ApiError ? e.message : String(e)}`);
    } finally {
      setGhSaving(false);
    }
  };

  const resetGh = async () => {
    try {
      await api.del('/settings/github');
      await loadGh();
      toast('↩️', '已恢复 server/.env 默认 GITHUB_TOKEN');
    } catch (e) {
      toast('❌', `重置失败: ${String(e)}`);
    }
  };

  const srcChip = (s?: string) => s === 'db' ? <span className="chip chip-purple">页面配置</span> : s === 'env' ? <span className="chip chip-slate">.env 默认</span> : null;

  return (
    <>
      <div className="card">
        <div className="card-head">
          <span className="card-title">LLM 连接（推理与生成）</span>
          {llm && <span className={`chip ${llm.active ? 'chip-blue' : 'chip-slate'}`}>{llm.active ? '已配置' : '未配置'}</span>}
        </div>
        {llmErr && <p className="hint" style={{ color: 'var(--red)' }}>加载失败: {llmErr}</p>}
        {llm && (
          <>
            <p className="hint" style={{ margin: '4px 0 12px' }}>
              当前生效：{llm.provider || '—'} · {llm.model || '—'} · 密钥 {llm.api_key_set ? '已设置' : '未设置'} {srcChip(llm.source)}
              <br />保存后立即生效。点击「测试连接」会用当前表单值发起一次模型请求。
            </p>
            <div className="form-row" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div className="field">
                <label>Provider（标识名，任意）</label>
                <input className="inp" value={provider} disabled={llmBusy} onChange={(e) => { setProvider(e.target.value); setTestRes(null); }} placeholder="gateway / deepseek / glm" />
              </div>
              <div className="field">
                <label>模型名</label>
                <input className="inp" value={model} disabled={llmBusy} onChange={(e) => { setModel(e.target.value); setTestRes(null); }} placeholder="如 deepseek-v4-flash" />
              </div>
            </div>
            <div className="field">
              <label>Base URL（OpenAI 兼容 /v1）</label>
              <input className="inp" value={baseUrl} disabled={llmBusy} onChange={(e) => { setBaseUrl(e.target.value); setTestRes(null); }} placeholder="https://…/v1" />
            </div>
            <div className="field">
              <label>API Key {llm.api_key_set && <span className="hint" style={{ display: 'inline' }}>（已设置，留空保留）</span>}</label>
              <input className="inp" type="password" value={apiKey} disabled={llmBusy} onChange={(e) => { setApiKey(e.target.value); setTestRes(null); }} placeholder="粘贴新 Key，留空保留现有" />
            </div>
            <div style={{ display: 'flex', gap: 10, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <button className="btn btn-primary btn-sm" disabled={llmBusy} onClick={() => void saveLlm()}>{llmSaving ? '保存中…' : '保存'}</button>
              <button className="btn btn-outline btn-sm" disabled={llmBusy} onClick={() => void testLlm()}>{testing ? '测试中…' : '🔌 测试连接'}</button>
              {llm.source === 'db' && <button className="btn btn-ghost btn-sm" disabled={llmBusy} onClick={() => void resetLlm()}>恢复 .env 默认</button>}
            </div>
            {testRes && (
              <div className="hint" role="status" style={{ marginTop: 10, color: testRes.ok ? 'var(--green)' : 'var(--red)' }}>
                {testRes.ok
                  ? `✅ 本次测试连接成功 · ${testRes.latency_ms}ms`
                  : `❌ 测试失败：${testRes.error}`}
              </div>
            )}
          </>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <span className="card-title">GitHub 发布</span>
          {gh && <span className={`chip ${gh.active ? 'chip-green' : 'chip-slate'}`}>{'公共写入已关闭'}</span>}
        </div>
        {gh && (
          <>
            <p className="hint" style={{ margin: '4px 0 12px' }}>
              当前：token {gh.token_hint || '未设置'} {srcChip(gh.source)}
              {gh.active && <> · 仓库 {gh.target_repo} · 目录 {gh.content_dir} · 基线 {gh.base_branch}</>}
              <br />token 需要 contents:write + pull requests:write 权限；只存本地，接口不回显。
            </p>
            <div className="field">
              <label>Personal Access Token {gh.token_hint && <span className="hint" style={{ display: 'inline' }}>（已设置 {gh.token_hint}）</span>}</label>
              <input className="inp" type="password" value={ghToken} onChange={(e) => setGhToken(e.target.value)} placeholder="ghp_… / gho_…，留空保留现有" />
            </div>
            <div style={{ display: 'flex', gap: 10, marginTop: 10 }}>
              <button className="btn btn-primary btn-sm" disabled={ghSaving || !ghToken.trim()} onClick={() => void saveGh()}>{ghSaving ? '保存中…' : '保存'}</button>
              {gh.source === 'db' && <button className="btn btn-ghost btn-sm" onClick={() => void resetGh()}>恢复 .env 默认</button>}
            </div>
          </>
        )}
      </div>

      <div className="card">
        <div className="card-head"><span className="card-title">尚未接入</span></div>
        {[
          ['⚡', '编程 Agent', '项目扫描当前用仓库元数据，不含 AI 审查'],
          ['🔎', '搜索', '岗位智能（Job Intelligence）暂未接入搜索源'],
          ['🧩', 'MCP 适配器', '暂无 MCP 工具接入'],
        ].map(([ic, name, desc]) => (
          <div className="list-row" key={name}>
            <div className="list-ic">{ic}</div>
            <div className="list-main">
              <div className="list-title">{name}</div>
              <div className="list-sub">{desc}</div>
            </div>
            <span className="chip chip-slate">未接入</span>
          </div>
        ))}
      </div>
    </>
  );
}

/* ---------------- 数据导出与删除 ---------------- */
function DataTab({ ov, toast }: { ov: SettingsOverview; toast: (ic: string, msg: string) => void }) {
  const [delOpen, setDelOpen] = useState(false);
  const [confirm, setConfirm] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [exporting, setExporting] = useState(false);


  const doExport = async () => {
    setExporting(true);
    try {
      const blob = await downloadWorkspaceExport();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `careerpilot-export-${ov.workspace.slug}-${new Date().toISOString().slice(0, 10).split('-').join('')}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
      toast('📦', '导出完成（真实数据 JSON）');
    } catch (e) {
      toast('❌', `导出失败: ${String(e)}`);
    } finally {
      setExporting(false);
    }
  };

  const doDelete = async () => {
    if (confirm !== ov.workspace.slug) { toast('⚠️', `请输入工作空间标识 "${ov.workspace.slug}" 确认`); return; }
    setDeleting(true);
    try {
      await api.del('/settings/workspace', { confirm_slug: confirm });
      toast('🗑️', '工作空间已删除（含全部数据）。刷新后可重新 seed 演示数据。');
      setDelOpen(false);
      setTimeout(() => window.location.reload(), 1200);
    } catch (e) {
      toast('❌', `删除失败: ${e instanceof ApiError ? e.message : String(e)}`);
    } finally {
      setDeleting(false);
    }
  };

  const c = ov.counts;
  return (
    <>
      <div className="card">
        <div className="card-head"><span className="card-title">数据导出</span></div>
        <p className="hint" style={{ margin: '4px 0 12px' }}>
          导出本工作空间全部真实数据为 JSON：证据 {c.evidence} · 记忆 {c.memory} · 任务 {c.tasks} · 考试 {c.exams} · 题目 {c.questions} · 发布记录 {c.publications} 等。
        </p>
        <button className="btn btn-outline" disabled={exporting} onClick={() => void doExport()}>
          {exporting ? '导出中…' : '⬇ 导出全部 JSON'}
        </button>
      </div>
      <div className="card" style={{ border: '1px solid var(--red-2, #fecaca)' }}>
        <div className="card-head"><span className="card-title" style={{ color: 'var(--red)' }}>危险区域</span></div>
        <p className="hint" style={{ margin: '4px 0 12px' }}>
          删除工作空间会永久移除全部证据、记忆、任务、考试与发布记录（数据库级联），且不可恢复。
        </p>
        <button className="btn btn-danger" onClick={() => { setConfirm(''); setDelOpen(true); }}>删除工作空间</button>
      </div>

      {delOpen && (
        <Modal title="删除工作空间？" size="sm" onClose={() => setDelOpen(false)}
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setDelOpen(false)}>取消</button>
              <button className="btn btn-danger" disabled={deleting || confirm !== ov.workspace.slug} onClick={() => void doDelete()}>
                {deleting ? '删除中…' : '确认删除'}
              </button>
            </>
          }>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.7 }}>
            将永久删除「{ov.workspace.name}」及其全部数据：证据 {c.evidence} 条、记忆 {c.memory} 条、任务 {c.tasks} 条、考试 {c.exams} 场、题目 {c.questions} 道。此操作不可恢复。
          </p>
          <div className="field" style={{ marginTop: 10 }}>
            <label>输入工作空间标识 <b>{ov.workspace.slug}</b> 以确认</label>
            <input className="inp" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder={ov.workspace.slug} />
          </div>
        </Modal>
      )}
    </>
  );
}

/* ---------------- 自动化规则与审计 ---------------- */
const BUILTIN_CHECKS = [
  ['隐私扫描', '发布前检测手机号 / 邮箱 / 身份证号 / PII 标记词，命中即阻断'],
  ['事实检查', '无引用的量化声明标记为待人工确认'],
  ['版权与来源', '来源不明或许可不清的内容不能自动发布'],
  ['Markdown 检查', '标题结构 / 空链接等格式校验'],
];

function RulesTab() {
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
        <div className="card-head"><span className="card-title">发布权限</span></div>
        <div className="list-row">
          <div className="list-main">
            <div className="list-title">公共内容写入已禁用</div>
            <div className="list-sub">当前仅保留本地草稿与预览；不会创建或合并 PR。</div>
          </div>
          <button
            className="switch"
            aria-label="公共发布已禁用"
            disabled
            style={{ cursor: busy ? 'wait' : 'pointer' }}
          />
        </div>
        <p className="hint" style={{ marginTop: 8 }}>
          本轮核心闭环修复期间，后端统一阻止公共写入。
        </p>
      </div>

      <div className="card">
        <div className="card-head"><span className="card-title">内置校验（不可关闭）</span></div>
        {BUILTIN_CHECKS.map(([name, desc]) => (
          <div className="list-row" key={name}>
            <div className="list-main">
              <div className="list-title">{name}</div>
              <div className="list-sub">{desc}</div>
            </div>
            <span className="chip chip-green">执行中</span>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="card-head"><span className="card-title">审计日志</span></div>
        {err && <p className="hint" style={{ color: 'var(--red)' }}>加载失败: {err}</p>}
        {!err && (audit?.length ?? 0) === 0 && <p className="hint">暂无审计记录（发布过内容后这里会出现完整流水）。</p>}
        {audit?.slice(0, 30).map((a) => {
          const meta = ACTION_META[a.action] || { label: a.action, cls: 'chip-slate' };
          const d = a.detail || {};
          const what = [d.pr ? `PR #${d.pr}` : '', d.status || d.to || d.from || '', d.dry_run ? 'dry-run' : '']
            .filter(Boolean).join(' · ');
          return (
            <div className="list-row" key={a.id}>
              <div className="list-ic">📜</div>
              <div className="list-main">
                <div className="list-title">{meta.label}{what ? ` — ${what}` : ''}{a.draft_id ? `（草稿 CT-${a.draft_id}）` : ''}</div>
                <div className="list-sub">{fmtDate(a.created_at)}</div>
              </div>
              <span className={`chip ${meta.cls}`}>{meta.label}</span>
            </div>
          );
        })}
      </div>
    </>
  );
}
