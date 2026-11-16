import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppState } from '../components/AppState';
import { DemoBanner, Modal, PageHead } from '../components/ui';
import { api } from '../api/client';
import { PROJECTS } from '../data/demo';

const SKILL_NAMES: Record<string, string> = {
  agent: 'Agent 架构与规划', tools: '工具调用 / MCP', memory: '状态管理与 Memory',
  rag: 'RAG / 上下文工程', eval: 'Agent Evaluation', backend: '后端与系统设计',
  reliability: '可靠性与安全', deploy: '部署与可观测性',
};

interface ScanView { id: number; status: string; commit_sha: string | null; error: string | null; created_at: string | null }
interface MatrixRow { key: string; level: number; findings: number }
interface ProjectView {
  id: number; name: string; repo_url: string; owner: string; repo: string; branch: string;
  description: string | null; status: string; last_scanned_at: string | null;
  latest_scan: ScanView | null; findings: { matrix: MatrixRow[]; stats: Record<string, unknown> } | null;
}

const STATUS_CHIP: Record<string, string> = {
  SUCCESS: 'chip-green', RUNNING: 'chip-amber', FAILED: 'chip-red', NEVER: 'chip-slate',
};
const STATUS_TEXT: Record<string, string> = {
  SUCCESS: '已扫描', RUNNING: '扫描中', FAILED: '扫描失败', NEVER: '未扫描',
};

export default function Assets() {
  const navigate = useNavigate();
  const { toast } = useAppState();
  const [projects, setProjects] = useState<ProjectView[] | null>(null);
  const [apiErr, setApiErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [importOpen, setImportOpen] = useState(false);
  const [importName, setImportName] = useState('');
  const [importRepo, setImportRepo] = useState('');
  const [importBranch, setImportBranch] = useState('master');
  const [importing, setImporting] = useState(false);
  const [scanningId, setScanningId] = useState<number | null>(null);
  const [syncing, setSyncing] = useState(false);

  const reload = useCallback(() => {
    setLoading(true);
    api.get<{ items: ProjectView[] }>('/projects')
      .then((d) => { setProjects(d.items); setApiErr(null); })
      .catch((e) => { setApiErr(e.message || String(e)); setProjects(null); })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const scan = async (p: ProjectView) => {
    setScanningId(p.id);
    try {
      await api.post(`/projects/${p.id}/scan`);
      toast('✅', `扫描完成：${p.name}`);
      reload();
    } catch (e: any) {
      toast('⚠️', `扫描失败：${e.message || e}`);
      reload();
    } finally {
      setScanningId(null);
    }
  };

  const doImport = async () => {
    if (!importName.trim() || !importRepo.trim()) { toast('⚠️', '请填写项目名和仓库地址'); return; }
    setImporting(true);
    try {
      const p = await api.post<ProjectView>('/projects', { name: importName.trim(), repo_url: importRepo.trim(), branch: importBranch.trim() || 'master' });
      toast('📦', `仓库已导入：${p.owner}/${p.repo}，正在扫描（约 5~20 秒）…`);
      setImportOpen(false);
      setImportName(''); setImportRepo('');
      reload(); // 先让卡片立刻出现(未扫描态), 扫描完再刷新一次
      try {
        await api.post(`/projects/${p.id}/scan`);
        toast('✅', `扫描完成：${p.name}，能力矩阵已生成`);
      } catch (se: any) {
        toast('⚠️', `扫描失败：${se.message || se}（可在卡片上点「重新分析」重试）`);
      }
      reload();
    } catch (e: any) {
      if (e?.status === 409) {
        toast('⚠️', '该仓库已导入过，无需重复导入；可在项目卡上点「重新分析」更新扫描');
      } else {
        toast('⚠️', `导入失败：${e.message || e}`);
      }
    } finally {
      setImporting(false);
    }
  };

  const syncAssets = async () => {
    setSyncing(true);
    try {
      const r = await api.post<{ synced: string[]; errors: string[] }>('/career-assets/sync');
      toast(r.errors.length ? '⚠️' : '🔄', `线上资产同步完成：${r.synced.length} 个文件${r.errors.length ? `，${r.errors.length} 个失败` : ''}`);
      reload();
    } catch (e: any) {
      toast('⚠️', `同步失败：${e.message || e}`);
    } finally {
      setSyncing(false);
    }
  };

  const runPipeline = async () => {
    try {
      const r = await api.post<{ risk_level: string; short_code: string }>('/career-assets/updates/run');
      toast('⚙', `流水线已执行（${r.short_code}，风险 ${r.risk_level}），生成内容草稿`);
      navigate('/assets/pipeline');
    } catch (e: any) {
      toast('⚠️', `流水线执行失败：${e.message || e}`);
    }
  };

  const demoMode = apiErr !== null;
  const list = demoMode ? PROJECTS.map((p, i) => ({
    id: -(i + 1), name: p.name, repo_url: `https://github.com/${p.github}`, owner: '', repo: '', branch: '',
    description: p.summary, status: 'SUCCESS', last_scanned_at: p.scanned, latest_scan: null,
    findings: { matrix: p.matrix.map((m) => ({ key: m.key, level: m.level, findings: 0 })), stats: {} },
  })) : (projects ?? []);

  return (
    <div>
      <PageHead
        title="项目与职业资产"
        sub="项目证据自动映射为职业展示 · 草稿 → 风险分级 → 发布，全流程可审计"
      >
        <button className="btn btn-outline" onClick={() => setImportOpen(true)}>＋ 导入仓库</button>
        <button className="btn btn-outline" onClick={syncAssets} disabled={syncing}>{syncing ? '同步中…' : '🔄 同步线上资产'}</button>
        <button className="btn btn-primary" onClick={runPipeline}>⚙ 更新职业资产</button>
      </PageHead>
      {loading && <div className="hint" style={{ marginBottom: 14 }}>正在从后端加载项目…</div>}
      {demoMode && <DemoBanner text={`DEMO · 后端不可用（${apiErr}），以下为静态演示数据。`} />}
      {!demoMode && !loading && list.length === 0 && (
        <div className="card" style={{ padding: '36px 20px', textAlign: 'center' }}>
          <div style={{ fontSize: 28, marginBottom: 8 }}>📦</div>
          <b>还没有导入任何仓库</b>
          <p className="hint" style={{ marginTop: 6 }}>
            导入 GitHub 仓库后，CareerPilot 会做只读扫描（文件树 / README / 语言分布），
            用确定性规则映射到 8 个能力域，再生成可发布的项目能力证据页。
          </p>
          <button className="btn btn-primary" style={{ marginTop: 12 }} onClick={() => setImportOpen(true)}>＋ 导入第一个仓库</button>
        </div>
      )}

      <div className="grid grid-2">
        {list.map((p) => (
          <div className="card" key={p.id}>
            <div className="card-head">
              <div>
                <div className="card-title">{p.name}</div>
                <div className="card-sub">{p.description || `${p.owner ? p.owner + '/' : ''}${p.repo}`}</div>
              </div>
              <span className={`chip ${STATUS_CHIP[p.status] || 'chip-slate'}`}>{STATUS_TEXT[p.status] || p.status}</span>
            </div>
            <div className="q-meta" style={{ margin: '10px 0' }}>
              <span className="chip chip-slate">🔗 {p.repo_url}</span>
              {p.branch && <span className="chip chip-slate">{p.branch}</span>}
            </div>
            {p.latest_scan?.status === 'FAILED' && (
              <div className="hint" style={{ color: 'var(--red)', marginBottom: 8 }}>上次扫描失败：{p.latest_scan.error}</div>
            )}
            <div className="hint" style={{ marginBottom: 10 }}>
              {p.last_scanned_at ? `最近扫描：${new Date(p.last_scanned_at).toLocaleString('zh-CN')}` : '尚未扫描'} · 能力矩阵
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 14 }}>
              {(p.findings?.matrix ?? []).filter((m) => m.findings > 0 || m.level > 0).map((m) => (
                <div className="dim-row" key={m.key}>
                  <span className="dim-name" style={{ width: 160 }}>{SKILL_NAMES[m.key] || m.key}</span>
                  <div className="dim-track">
                    <div className="dim-fill" style={{ width: `${(m.level / 5) * 100}%` }} />
                  </div>
                  <span className="dim-val">{m.level}</span>
                </div>
              ))}
              {(!p.findings || p.findings.matrix.length === 0) && (
                <div className="hint">还没有扫描数据；点「重新分析」生成能力矩阵。</div>
              )}
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              {p.id > 0 && (
                <>
                  <button className="btn btn-primary" onClick={() => navigate(`/assets/project/${p.id}`)}>项目审查</button>
                  <button className="btn btn-outline" onClick={() => scan(p)} disabled={scanningId === p.id}>
                    {scanningId === p.id ? '扫描中…' : '重新分析'}
                  </button>
                </>
              )}
              {p.id < 0 && <span className="hint">演示数据 · 启动后端后自动替换</span>}
            </div>
          </div>
        ))}
      </div>

      <div className="card mt-18">
        <div className="card-head">
          <span className="card-title">职业资产更新流水线</span>
          <button className="btn btn-ghost btn-sm" onClick={() => navigate('/assets/pipeline')}>查看流水线状态 →</button>
        </div>
        <div className="pipeline">
          {['扫描项目变更', '提取证据', '映射能力', '生成内容草稿', '风险分级', '创建 PR', '发布', '发布历史可回滚'].map((s, i) => (
            <React.Fragment key={s}>
              {i > 0 && <span className="pl-arrow">→</span>}
              <span className={`pl-step ${i === 0 ? 'now' : ''}`}>⟳ {s}</span>
            </React.Fragment>
          ))}
        </div>
        <div className="hint" style={{ marginTop: 10 }}>扫描成功后点「更新职业资产」生成内容草稿并自动风险分级。</div>
      </div>

      {importOpen && (
        <Modal title="导入 GitHub 仓库" size="sm" onClose={() => setImportOpen(false)}
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setImportOpen(false)}>取消</button>
              <button className="btn btn-primary" onClick={doImport} disabled={importing}>{importing ? '导入中…' : '导入并扫描'}</button>
            </>
          }>
          <div className="field">
            <label>项目名称</label>
            <input className="inp" value={importName} onChange={(e) => setImportName(e.target.value)} placeholder="如：CareerPilot 公开门户" />
          </div>
          <div className="field">
            <label>仓库地址（github.com 链接）</label>
            <input className="inp" value={importRepo} onChange={(e) => setImportRepo(e.target.value)} placeholder="https://github.com/owner/repo" />
          </div>
          <div className="field">
            <label>分支（默认 master）</label>
            <input className="inp" value={importBranch} onChange={(e) => setImportBranch(e.target.value)} />
          </div>
          <p className="hint" style={{ marginTop: 8 }}>
            只做只读分析：仓库元信息 / README / 文件树 / 语言分布 / 关键文件内容确认；不会写入你的仓库。
          </p>
        </Modal>
      )}
    </div>
  );
}
