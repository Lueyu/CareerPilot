import React, { useCallback, useEffect, useState } from 'react';
import { useAppState } from '../components/AppState';
import { DemoBanner, Modal, PageHead } from '../components/ui';
import { api, ApiError } from '../api/client';
import type { AuditItem, PublicationRule, SettingsOverview } from '../api/client';

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

export default function Settings() {
  const { toast } = useAppState();
  const [tab, setTab] = useState<TabKey>('workspace');
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
              {tab === 'account' && <AccountTab ov={ov} />}
              {tab === 'workspace' && <WorkspaceTab ov={ov} reload={load} toast={toast} />}
              {tab === 'models' && <ModelsTab ov={ov} />}
              {tab === 'data' && <DataTab ov={ov} toast={toast} />}
              {tab === 'rules' && <RulesTab />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------------- 账号 ---------------- */
function AccountTab({ ov }: { ov: SettingsOverview }) {
  return (
    <div className="card">
      <div className="card-head"><span className="card-title">账号</span></div>
      <div className="list-row">
        <div className="list-ic avatar">🧑</div>
        <div className="list-main">
          <div className="list-title">{ov.account.display_name} · {ov.account.email}</div>
          <div className="list-sub">个人账号 · 拥有 {ov.members.length} 个成员身份</div>
        </div>
      </div>
      <p className="hint" style={{ marginTop: 8 }}>
        账号信息由本地数据库管理；当前版本为单用户本地部署，登录与邮箱修改暂未开放。
      </p>
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

/* ---------------- 模型与连接 ---------------- */
function ModelsTab({ ov }: { ov: SettingsOverview }) {
  const llm = ov.connections.llm;
  const gh = ov.connections.github_publish;
  return (
    <>
      <div className="card">
        <div className="card-head"><span className="card-title">已配置的连接</span></div>
        <div className="list-row">
          <div className="list-ic">🤖</div>
          <div className="list-main">
            <div className="list-title">LLM（推理与生成）</div>
            <div className="list-sub">{llm.active ? `${llm.provider} · ${llm.model} · 面经提取/答案草稿/评分增强走模型，失败自动降级规则` : '未配置 · 全部走规则引擎降级（server/.env 设置 LLM_PROVIDER / LLM_API_KEY 后启用）'}</div>
          </div>
          <span className={`chip ${llm.active ? 'chip-green' : 'chip-slate'}`}>{llm.active ? '已连接' : '未配置'}</span>
        </div>
        <div className="list-row">
          <div className="list-ic">🐙</div>
          <div className="list-main">
            <div className="list-title">GitHub 发布</div>
            <div className="list-sub">{gh.active ? `${gh.target_repo} · 内容目录 ${gh.content_dir} · 基线 ${gh.base_branch} · 低风险自动合并默认${gh.auto_merge_default ? '开' : '关'}` : '未配置 · server/.env 设置 GITHUB_TOKEN 后启用（公开内容发布需要）'}</div>
          </div>
          <span className={`chip ${gh.active ? 'chip-green' : 'chip-slate'}`}>{gh.active ? '已连接' : '未配置'}</span>
        </div>
        <p className="hint" style={{ marginTop: 8 }}>密钥只存本地 server/.env，接口不回显。</p>
      </div>
      <div className="card">
        <div className="card-head"><span className="card-title">尚未接入</span></div>
        {[
          ['🎙️', '语音转写（STT）', '考试语音输入暂为界面占位'],
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
  const BASE = 'http://127.0.0.1:8000/api/v1';

  const doExport = async () => {
    setExporting(true);
    try {
      const res = await fetch(`${BASE}/settings/export`, {
        headers: { 'X-Api-Token': 'cp-dev-token', 'X-Workspace': 'demo' },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
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
  const [rules, setRules] = useState<PublicationRule[] | null>(null);
  const [defaultAutoMerge, setDefaultAutoMerge] = useState(true);
  const [audit, setAudit] = useState<AuditItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [r, a, o] = await Promise.all([
        api.get<{ items: PublicationRule[] }>('/public-content/rules'),
        api.get<{ items: AuditItem[] }>('/public-content/audit'),
        api.get<SettingsOverview>('/settings/overview'),
      ]);
      setRules(r.items);
      setAudit(a.items);
      setDefaultAutoMerge(o.connections.github_publish.auto_merge_default);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const autoMerge = rules?.find((r) => r.key === 'auto_merge') || null;
  const autoMergeOn = autoMerge ? autoMerge.is_enabled : defaultAutoMerge;

  const toggleAutoMerge = async () => {
    setBusy(true);
    try {
      const next = !autoMergeOn;
      const saved = await api.put<PublicationRule>('/public-content/rules', { key: 'auto_merge', value: { note: '低风险内容自动合并 PR' }, is_enabled: next });
      setRules((rs) => {
        const base = rs || [];
        const idx = base.findIndex((r) => r.key === 'auto_merge');
        if (idx >= 0) { const copy = [...base]; copy[idx] = saved; return copy; }
        return [...base, saved];
      });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="card">
        <div className="card-head"><span className="card-title">发布权限</span></div>
        <div className="list-row">
          <div className="list-main">
            <div className="list-title">低风险内容自动合并 PR</div>
            <div className="list-sub">校验全过（PASS）且非 dry-run 时，发布后直接 squash 合并；关闭则一律保留 PR 等人工合并</div>
          </div>
          <button
            className={`switch ${autoMergeOn ? 'on' : ''}`}
            aria-label="低风险自动合并"
            disabled={busy || rules === null}
            onClick={() => void toggleAutoMerge()}
            style={{ cursor: busy ? 'wait' : 'pointer' }}
          />
        </div>
        <p className="hint" style={{ marginTop: 8 }}>
          未配置规则时默认跟随 server/.env 的 PORTAL_AUTO_MERGE；此处开关会覆盖默认值。
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
