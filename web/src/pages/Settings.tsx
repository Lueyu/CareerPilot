import React, { useState } from 'react';
import { useAppState } from '../components/AppState';
import { DemoBanner, Modal, PageHead } from '../components/ui';
import { SETTINGS_RULES } from '../data/demo';

const TABS = [
  { key: 'account', name: '账号', ic: '👤' },
  { key: 'workspace', name: '工作空间', ic: '🗂️' },
  { key: 'models', name: '模型与 Agent 连接', ic: '🔌' },
  { key: 'data', name: '数据导出与删除', ic: '📦' },
  { key: 'rules', name: '自动化规则与发布权限', ic: '⚙️' },
];

const MODELS = [
  { kind: 'LLM', label: 'DeepSeek-V3', detail: '推理与生成 · 已连接', on: true },
  { kind: 'LLM', label: 'GLM-4.5', detail: '备用推理 · 已连接', on: true },
  { kind: 'STT', label: 'Whisper 本地', detail: '语音转写 · 已连接', on: true },
  { kind: 'Coding Agent', label: 'Claude Code', detail: '职业资产发布 · 已连接', on: true },
  { kind: '搜索', label: 'Tavily', detail: '证据检索 · 已连接', on: true },
  { kind: 'MCP 适配器', label: 'GitHub MCP', detail: 'PR 与仓库操作 · 已连接', on: true },
];

const AUDIT = [
  { time: '11-15 10:23', who: '自动化', what: '发布职业资产 v1.9（低风险，规则 r1）' },
  { time: '11-15 09:58', who: '自动化', what: '扫描 Squabble 变更并提取 5 条证据' },
  { time: '11-14 18:02', who: '李雷', what: '调整规则 r3：高风险转人工审批' },
  { time: '11-13 11:40', who: '自动化', what: '发布职业资产 v1.8（低风险，规则 r1）' },
];

export default function Settings() {
  const { toast } = useAppState();
  const [tab, setTab] = useState('workspace');
  const [rules, setRules] = useState(SETTINGS_RULES);
  const [delOpen, setDelOpen] = useState(false);
  const [exported, setExported] = useState(false);

  const toggle = (id: string) => {
    setRules((rs) => rs.map((r) => (r.id === id ? { ...r, on: !r.on } : r)));
    toast('⚙️', '自动化规则已更新（Demo）');
  };

  return (
    <div>
      <PageHead title="设置与工作空间" sub="管理账号、模型连接、数据与自动化权限" />
      <DemoBanner />

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
          {tab === 'account' && (
            <div className="card">
              <div className="card-head"><span className="card-title">账号</span></div>
              <div className="list-row">
                <div className="list-ic avatar">🧑</div>
                <div className="list-main">
                  <div className="list-title">李雷 · lilei@example.com</div>
                  <div className="list-sub">个人账号 · 上次登录 11-15 09:12</div>
                </div>
                <button className="btn btn-outline btn-sm" onClick={() => toast('✏️', '编辑账号（Demo）')}>编辑</button>
              </div>
            </div>
          )}

          {tab === 'workspace' && (
            <div className="card">
              <div className="card-head">
                <span className="card-title">工作空间信息</span>
                <button className="btn btn-outline btn-sm" onClick={() => toast('✏️', '编辑工作空间（Demo）')}>编辑</button>
              </div>
              <div className="ws-info" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px 24px', padding: '6px 0' }}>
                {[
                  ['名称', '求职作战室'],
                  ['用途', 'AI Agent 应用开发工程师求职'],
                  ['对标岗位', 'AI Agent 应用开发工程师 · P6'],
                  ['目标薪资', '65-75 万（当前约 50 万）'],
                  ['基准', 'benchmark v1.1'],
                  ['创建时间', '2026-10-01'],
                  ['证据数', '27 条'],
                ].map(([k, v]) => (
                  <div key={k} style={{ display: 'flex', gap: 10, fontSize: 13 }}>
                    <span style={{ color: 'var(--text-3)', width: 84, flexShrink: 0 }}>{k}</span>
                    <b style={{ fontWeight: 600 }}>{v}</b>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 10, borderTop: '1px solid var(--border-2)', paddingTop: 12 }}>
                <div className="card-sub" style={{ marginBottom: 8 }}>成员（1）</div>
                <div className="list-row">
                  <div className="list-ic avatar">🧑</div>
                  <div className="list-main">
                    <div className="list-title">李雷（所有者）</div>
                    <div className="list-sub">lilei@example.com</div>
                  </div>
                  <button className="btn btn-outline btn-sm" onClick={() => toast('👥', '邀请成员（Demo）')}>+ 邀请</button>
                </div>
              </div>
            </div>
          )}

          {tab === 'models' && (
            <div className="card">
              <div className="card-head">
                <span className="card-title">模型与 Agent 连接</span>
                <button className="btn btn-primary btn-sm" onClick={() => toast('🔌', '添加连接（Demo）')}>+ 添加</button>
              </div>
              {MODELS.map((m) => (
                <div className="list-row" key={m.label}>
                  <div className="list-ic">{m.kind === 'LLM' ? '🤖' : m.kind === 'STT' ? '🎙️' : m.kind === '搜索' ? '🔎' : m.kind === 'MCP 适配器' ? '🧩' : '⚡'}</div>
                  <div className="list-main">
                    <div className="list-title">{m.label}</div>
                    <div className="list-sub">{m.kind} · {m.detail}</div>
                  </div>
                  <span className={`chip ${m.on ? 'chip-green' : 'chip-slate'}`}>{m.on ? '已连接' : '未连接'}</span>
                  <button className="btn btn-ghost btn-sm" onClick={() => toast('⚙️', `配置 ${m.label}（Demo）`)}>配置</button>
                </div>
              ))}
            </div>
          )}

          {tab === 'data' && (
            <>
              <div className="card">
                <div className="card-head"><span className="card-title">数据导出</span></div>
                <p className="hint" style={{ margin: '4px 0 12px' }}>导出全部工作空间数据为 JSON，包含证据、记忆、任务与决策记录。</p>
                <button className="btn btn-outline" onClick={() => { setExported(true); toast('📦', '导出任务已创建（Demo）'); }}>
                  {exported ? '✅ 已导出（Demo）' : '⬇ 导出全部 JSON'}
                </button>
              </div>
              <div className="card" style={{ border: '1px solid var(--red-2, #fecaca)' }}>
                <div className="card-head"><span className="card-title" style={{ color: 'var(--red)' }}>危险区域</span></div>
                <p className="hint" style={{ margin: '4px 0 12px' }}>删除工作空间会永久移除全部证据、记忆与任务，且不可恢复。</p>
                <button className="btn btn-danger" onClick={() => setDelOpen(true)}>删除工作空间</button>
              </div>
            </>
          )}

          {tab === 'rules' && (
            <>
              <div className="card">
                <div className="card-head"><span className="card-title">自动化规则与发布权限</span></div>
                {rules.map((r) => (
                  <div className="list-row" key={r.id}>
                    <div className="list-main">
                      <div className="list-title">{r.name}</div>
                      <div className="list-sub">{r.desc}</div>
                    </div>
                    <button
                      className={`switch ${r.on ? 'on' : ''}`}
                      aria-label={r.name}
                      onClick={() => toggle(r.id)}
                      style={{ cursor: 'pointer' }}
                    />
                  </div>
                ))}
              </div>
              <div className="card">
                <div className="card-head"><span className="card-title">审计日志</span></div>
                {AUDIT.map((a) => (
                  <div className="list-row" key={a.time + a.what}>
                    <div className="list-ic">📜</div>
                    <div className="list-main">
                      <div className="list-title">{a.what}</div>
                      <div className="list-sub">{a.time} · {a.who}</div>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {delOpen && (
        <Modal title="删除工作空间？" size="sm" onClose={() => setDelOpen(false)}
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setDelOpen(false)}>取消</button>
              <button className="btn btn-danger" onClick={() => { setDelOpen(false); toast('🗑️', '工作空间已删除（Demo，未实际执行）'); }}>确认删除</button>
            </>
          }>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.7 }}>
            将永久删除「求职作战室」及其全部 <b>27 条证据、6 条记忆、3 条任务</b>。此操作不可恢复。
          </p>
        </Modal>
      )}
    </div>
  );
}
