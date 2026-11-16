import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAppState } from '../components/AppState';
import { DemoBanner, DiffLabel, Modal, PageHead, Stars } from '../components/ui';
import { api, BankData, ImportReview, QuestionDetail } from '../api/client';
import { trackImport } from '../lib/importTracker';
import bank from '../data/bank.json';

interface BankQ {
  id: string;
  topic: string;
  title: string;
  difficulty: number;
  tags: string[];
  company: string;
  source: string;
  kw?: string;
  mcq?: unknown[];
  html?: string;
}

const QUESTIONS = bank.questions as BankQ[];
const TOPICS = bank.topics as { code: string; name: string; icon: string; order: number; desc: string }[];
const TOPIC_NAME: Record<string, string> = Object.fromEntries(TOPICS.map((t) => [t.code, t.name]));

type ImportStage = 'upload' | 'review' | 'done';

/** 架构图箭头 marker: 题库 html 引用 #sd-arr 但源数据无 defs, 渲染时统一注入 */
const SD_MARKER_DEFS =
  '<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>' +
  '<marker id="sd-arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">' +
  '<path d="M0,0 L10,5 L0,10 z" fill="#5b5bd6"/></marker></defs></svg>';

/** 章节标题结构化: <p><b>N. 标题</b></p> → qa-h 带编号胶囊的标题条 */
function transformAnswer(html: string): string {
  return html.replace(
    /<p><b>(\d+)\. ?([^<]+)<\/b><\/p>/g,
    '<div class="qa-h"><span class="qa-no">$1</span><span class="qa-t">$2</span></div>',
  );
}

/** 后端题目行 -> 前端展示行(静态 bank.json 提供答案 html) */
interface Row {
  id: string;
  title: string;
  company: string;
  topic: string;
  difficulty: number;
  tags: string[];
  source_type: string;
  status: string;
  html?: string;
  isApi: boolean;
}

export default function Bank() {
  const navigate = useNavigate();
  const { toast } = useAppState();
  const [filter, setFilter] = useState<{ kind: 'all' | 'pending' | 'company' | 'topic'; value?: string }>({ kind: 'all' });
  const [detail, setDetail] = useState<Row | null>(null);
  const [zoomSvg, setZoomSvg] = useState<string | null>(null);
  const answerRef = useRef<HTMLDivElement>(null);
  const toggleQA = (open: boolean) => {
    answerRef.current?.querySelectorAll('details.qa').forEach((d) => { (d as HTMLDetailsElement).open = open; });
  };
  const [editQ, setEditQ] = useState<Row | null>(null);
  const [mergeQ, setMergeQ] = useState<Row | null>(null);
  const [importOpen, setImportOpen] = useState(window.location.search.includes('import=1'));
  const location = useLocation();

  // v3.1 M1: 在考试与面经 Tab 容器下, ?import=1 需要响应式触发
  useEffect(() => {
    if (location.search.includes('import=1')) setImportOpen(true);
  }, [location.search]);
  const [stage, setStage] = useState<ImportStage>('upload');
  const [approved, setApproved] = useState<string[]>([]);

  // 后端题库
  const [apiData, setApiData] = useState<BankData | null>(null);
  const [apiErr, setApiErr] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [q, setQ] = useState('');

  // 导入流水线状态
  const [pasteText, setPasteText] = useState('');
  const [review, setReview] = useState<ImportReview | null>(null);
  const [approveBusy, setApproveBusy] = useState(false);
  const [approveResult, setApproveResult] = useState<string | null>(null);

  // 真实文件上传
  const [files, setFiles] = useState<File[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [importErr, setImportErr] = useState<string | null>(null);
  const [company, setCompany] = useState('');
  const [level, setLevel] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  // 答案预览 / 重新生成
  const [openAns, setOpenAns] = useState<Record<number, boolean>>({});
  const [ansBusy, setAnsBusy] = useState<number | null>(null);

  // 题目详情(真实答案)
  const [detailHtml, setDetailHtml] = useState<string | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailErr, setDetailErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const params = new URLSearchParams();
    if (filter.kind === 'topic' && filter.value) params.set('topic', filter.value);
    if (filter.kind === 'company' && filter.value) params.set('company', filter.value);
    if (q.trim()) params.set('q', q.trim());
    api.get<BankData>(`/bank?${params.toString()}`)
      .then((d) => { if (alive) { setApiData(d); setApiErr(null); } })
      .catch((e) => { if (alive) setApiErr(String(e.message || e)); });
    return () => { alive = false; };
  }, [filter, q, reload]);

  // 后端题目行(含答案 html 关联)
  const apiRows: Row[] = useMemo(() => {
    if (!apiData) return [];
    const htmlMap = new Map(QUESTIONS.map((b) => [b.id, b.html]));
    const tagMap = new Map(QUESTIONS.map((b) => [b.id, b.tags]));
    const diffMap = new Map(QUESTIONS.map((b) => [b.id, b.difficulty]));
    return apiData.items.map((it) => ({
      id: it.short_code,
      title: it.title,
      company: it.company || '公开题库',
      topic: it.topic,
      difficulty: it.difficulty ?? diffMap.get(it.short_code) ?? 3,
      tags: it.tags?.length ? it.tags : tagMap.get(it.short_code) ?? [],
      source_type: it.source_type,
      status: it.status || (it.source_type === 'USER_PRIVATE' ? '已收录' : '已收录'),
      html: htmlMap.get(it.short_code),
      isApi: true,
    }));
  }, [apiData]);

  // 静态行(后端不可用时)
  const staticRows: Row[] = useMemo(() =>
    QUESTIONS.map((b) => ({
      id: b.id, title: b.title, company: b.company || '公开题库', topic: b.topic,
      difficulty: b.difficulty, tags: b.tags, source_type: 'PUBLIC_LICENSED', status: '已收录', html: b.html, isApi: false,
    })), []);

  const rows = apiRows.length || !apiErr ? apiRows : staticRows;

  // 待审核: 后端 pending imports(真实数据, 可能有多批)
  const [pendingImports, setPendingImports] = useState<ImportReview[] | null>(null);
  const [pendingBusy, setPendingBusy] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api.get<{ imports: ImportReview[] }>('/bank/imports/pending')
      .then((d) => { if (alive) setPendingImports(d.imports ?? []); })
      .catch(() => { if (alive) setPendingImports([]); });
    return () => { alive = false; };
  }, [reload]);

  const pendingRows = useMemo(() => {
    const list: { importId: number; shortCode: string; itemId: number; rowId: string; title: string; topic: string; confidence: string; dup: boolean; busy: boolean }[] = [];
    (pendingImports ?? []).forEach((imp) => {
      imp.items.forEach((it) => {
        list.push({
          importId: imp.id,
          shortCode: imp.short_code,
          itemId: it.id,
          rowId: `${imp.short_code}-${it.id}`,
          title: it.title,
          topic: it.suggested_skill_key,
          confidence: `${Math.round(it.extract_confidence * 100)}%`,
          dup: !!it.is_duplicate_of,
          busy: pendingBusy === `${imp.id}-${it.id}`,
        });
      });
    });
    return list;
  }, [pendingImports, pendingBusy]);

  const approveOne = async (importId: number, itemId: number, rowId: string) => {
    setPendingBusy(`${importId}-${itemId}`);
    try {
      const r = await api.post<{ created: string[] }>(`/bank/imports/${importId}/approve`, { item_ids: [itemId] });
      toast('✅', `${rowId.split('-')[0]} ${r.created?.[0] ?? ''} 已通过审核并加入题库`);
      setReload((x) => x + 1);
    } catch (e: any) {
      toast('⚠️', `审核失败：${e?.message || '请重试'}`);
    } finally {
      setPendingBusy(null);
    }
  };

  const openReviewFor = (imp: ImportReview) => {
    setReview(imp);
    setOpenAns({});
    setStage('review');
    setImportOpen(true);
  };

  const topicCount = useMemo(() => {
    const m: Record<string, number> = {};
    (apiData?.facets?.topic ?? Object.fromEntries(TOPICS.map((t) => {
      m[t.code] = rows.filter((r) => r.topic === t.code).length;
      return [t.code, m[t.code]];
    })));
    if (apiData?.facets?.topic) return apiData.facets.topic as Record<string, number>;
    const mm: Record<string, number> = {};
    rows.forEach((r) => (mm[r.topic] = (mm[r.topic] ?? 0) + 1));
    return mm;
  }, [rows, apiData]);

  const companies = useMemo(() => {
    if (apiData?.facets?.company) {
      return Object.entries(apiData.facets.company).sort((a, b) => b[1] - a[1]).slice(0, 6);
    }
    const m: Record<string, number> = {};
    rows.forEach((r) => {
      const c = (r.company || '').split(/[\/、,，]/)[0].trim();
      if (c) m[c] = (m[c] ?? 0) + 1;
    });
    return Object.entries(m).sort((a, b) => b[1] - a[1]).slice(0, 6);
  }, [rows, apiData]);

  /** 收集文件: 过滤超大/不支持的, 去重同名 */
  const addFiles = (list: FileList | File[] | null) => {
    if (!list) return;
    const incoming = Array.from(list);
    const ok: File[] = [];
    const bad: string[] = [];
    incoming.forEach((f) => {
      const ext = (f.name.split('.').pop() || '').toLowerCase();
      if (f.size > 12 * 1024 * 1024) { bad.push(`${f.name} 超过 12MB`); return; }
      if (!['pdf', 'docx', 'md', 'markdown', 'txt', 'text', 'jpg', 'jpeg', 'png'].includes(ext)) {
        bad.push(`${f.name} 格式暂不支持`);
        return;
      }
      if (!files.some((x) => x.name === f.name && x.size === f.size)) ok.push(f);
    });
    if (ok.length) setFiles((prev) => [...prev, ...ok]);
    setImportErr(bad.length ? bad.join('；') : null);
  };

  const startImport = async () => {
    const text = pasteText.trim();
    if (!files.length && !text) {
      setImportErr('请拖入/选择面经文件，或粘贴面经文本');
      toast('⚠️', '先选择文件或粘贴文本');
      return;
    }
    setImportErr(null);
    try {
      let start: ImportReview;
      if (files.length) {
        const form = new FormData();
        files.forEach((f) => form.append('files', f, f.name));
        if (text) form.append('pasted_text', text);
        if (company.trim()) form.append('company', company.trim());
        if (level.trim()) form.append('level', level.trim());
        start = await api.upload<ImportReview>('/bank/imports/upload', form);
      } else {
        start = await api.post<ImportReview>('/bank/imports', { pasted_text: text });
      }
      // 完全后台化: 注册全局追踪器(跨页面悬浮进度), 立即关闭弹窗不阻塞用户
      trackImport(start.id, start.short_code);
      setImportOpen(false);
      setStage('upload');
      setPasteText('');
      setFiles([]);
      setCompany('');
      setLevel('');
      toast('🚀', `面经已转入后台解析（${start.short_code}），可以继续其他操作，完成后会通知你`);
    } catch (e: any) {
      setImportErr(String(e.message || e));
      toast('⚠️', `提交失败: ${e.message}`);
    }
  };

  // 后台导入完成: 刷新待审核列表; 若正停留在面经库, 直接打开审核弹窗
  useEffect(() => {
    const h = (e: Event) => {
      setReload((x) => x + 1);
      const d = (e as CustomEvent).detail as { id: number; status: string } | undefined;
      if (d?.status === 'REVIEW') {
        api.get<ImportReview>(`/bank/imports/${d.id}`)
          .then((r) => { setReview(r); setStage('review'); setImportOpen(true); })
          .catch(() => { /* 列表已刷新, 用户可从待审核进入 */ });
      }
    };
    window.addEventListener('cp-import-finished', h);
    return () => window.removeEventListener('cp-import-finished', h);
  }, []);

  /** 重新生成某条导入项的答案 */
  const regenerateItem = async (itemId: number) => {    if (!review) return;
    setAnsBusy(itemId);
    try {
      const r = await api.post<{ answer_html: string; answer_source: string }>(
        `/bank/imports/${review.id}/items/${itemId}/answer?force=true`,
      );
      setReview((prev) => prev && {
        ...prev,
        items: prev.items.map((x) => x.id === itemId
          ? { ...x, draft_answer: r.answer_html, has_answer: true, answer_source: r.answer_source }
          : x),
      });
      setOpenAns((s) => ({ ...s, [itemId]: true }));
      toast('✨', '已重新生成解答');
    } catch (e: any) {
      toast('⚠️', `生成失败: ${e.message}`);
    } finally {
      setAnsBusy(null);
    }
  };

  /** 打开题目详情: 拉真实答案(私有题无静态 html) */
  const openDetail = async (r: Row) => {
    setDetail(r);
    setDetailHtml(r.html ?? null);
    setDetailErr(null);
    try {
      const d = await api.get<QuestionDetail>(`/bank/${encodeURIComponent(r.id)}`);
      if (d.answer_html) setDetailHtml(d.answer_html);
    } catch {
      /* 后端不可用时保留静态 html */
    }
  };

  /** 为题库题补生成答案 */
  const genAnswer = async () => {
    if (!detail) return;
    setDetailBusy(true);
    setDetailErr(null);
    try {
      const r = await api.post<{ answer_html: string; generated: boolean; source: string }>(
        `/bank/questions/${encodeURIComponent(detail.id)}/answer?force=false`,
      );
      setDetailHtml(r.answer_html);
      toast('✨', r.generated ? '已生成详细解答' : '该题已有答案');
      setReload((x) => x + 1);
    } catch (e: any) {
      setDetailErr(String(e.message || e));
    } finally {
      setDetailBusy(false);
    }
  };

  const approveAll = async () => {
    if (!review) {
      // 静态降级
      setApproved(['Q-018', 'Q-019', 'Q-020']);
      setStage('done');
      return;
    }
    setApproveBusy(true);
    try {
      const r = await api.post<{ created: string[]; import_id: number; status: string }>(`/bank/imports/${review.id}/approve`, {});
      const createdCount = Array.isArray(r.created) ? r.created.length : 0;
      const mergedCount = review.items.filter((i) => i.is_duplicate_of).length;
      setApproveResult(`新增 ${createdCount} 题 · 合并重复 ${mergedCount} 题`);
      setApproved(review.items.filter((i) => i.status !== 'MERGED' && !i.is_duplicate_of).map((i) => `${review.short_code}-${i.id}`));
      setStage('done');
      setReload((x) => x + 1);
    } catch (e: any) {
      toast('⚠️', `审核失败: ${e.message}`);
    } finally {
      setApproveBusy(false);
    }
  };

  const totalAll = (apiData?.total ?? rows.length) + pendingRows.length;

  return (
    <div>
      <PageHead
        title="面经库"
        sub={`共 ${totalAll} 题 · ${apiData?.total ?? rows.length} 道题库题 + ${pendingRows.length} 道待审核${apiErr ? ' · 后端不可用(静态)' : ''}`}
      >
        <button className="btn btn-outline" onClick={() => navigate('/exam')}>用面经组卷</button>
        <button className="btn btn-primary" onClick={() => { setImportOpen(true); setStage('upload'); setPasteText(''); setFiles([]); setCompany(''); setLevel(''); setImportErr(null); setDragOver(false); setReview(null); setApproveResult(null); }}>📥 导入面经</button>
      </PageHead>
      <DemoBanner text={apiErr ? 'DEMO · 后端不可用，当前为静态题库演示。' : 'DEMO · 导入、审核与合并为示例流程；公开题库为本地静态数据。'} />

      <div style={{ display: 'flex', gap: 26, alignItems: 'flex-start' }}>
        {/* 过滤侧栏 */}
        <div className="filter-side">
          <div className="filter-group">
            <div className="filter-title">题库</div>
            <div className={`filter-item ${filter.kind === 'all' ? 'active' : ''}`} onClick={() => setFilter({ kind: 'all' })}>
              <span>全部题目</span><span className="filter-count">{totalAll}</span>
            </div>
            <div className={`filter-item ${filter.kind === 'pending' ? 'active' : ''}`} onClick={() => setFilter({ kind: 'pending' })}>
              <span>待审核</span><span className="filter-count">{pendingRows.length}</span>
            </div>
          </div>
          <div className="filter-group">
            <div className="filter-title">按公司</div>
            {companies.map(([c, n]) => (
              <div key={c} className={`filter-item ${filter.kind === 'company' && filter.value === c ? 'active' : ''}`} onClick={() => setFilter({ kind: 'company', value: c })}>
                <span>{c}</span><span className="filter-count">{n}</span>
              </div>
            ))}
          </div>
          <div className="filter-group">
            <div className="filter-title">按主题</div>
            {TOPICS.map((t) => (
              <div key={t.code} className={`filter-item ${filter.kind === 'topic' && filter.value === t.code ? 'active' : ''}`} onClick={() => setFilter({ kind: 'topic', value: t.code })}>
                <span>{t.icon} {t.name}</span><span className="filter-count">{topicCount[t.code] ?? 0}</span>
              </div>
            ))}
          </div>
        </div>

        {/* 题目列表 */}
        <div className="card" style={{ flex: 1, minWidth: 0 }}>
          <div className="card-head">
            <span className="card-title">
              {filter.kind === 'all' ? '全部题目' : filter.kind === 'pending' ? '待审核题目' : filter.kind === 'topic' ? TOPIC_NAME[filter.value!] ?? filter.value : `${filter.value} 相关`}
            </span>
            <input
              className="inp"
              placeholder="搜索题目关键词…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              style={{ width: 220, padding: '6px 10px', fontSize: 13 }}
            />
          </div>

          {filter.kind === 'pending' ? (
            pendingImports === null ? (
              <div className="empty"><b>加载中…</b></div>
            ) : pendingRows.length === 0 ? (
              <div className="empty"><b>没有待审核的导入</b><p>从「导入面经」开始，提取结果会出现在这里等待审核</p></div>
            ) : (
              pendingRows.map((p) => (
                <div className="q-card" key={p.rowId}>
                  <div className="q-id">{p.shortCode}</div>
                  <div className="q-body">
                    <div className="q-title" onClick={() => {
                      const imp = (pendingImports ?? []).find((x) => x.id === p.importId);
                      if (imp) openReviewFor(imp);
                    }}>
                      {p.title}
                    </div>
                    <div className="q-meta">
                      <span className="chip chip-amber">待审核</span>
                      <span className="chip chip-slate">{TOPIC_NAME[p.topic] ?? p.topic}</span>
                      <span>提取置信度 {p.confidence}</span>
                      {p.dup && <span className="chip chip-red">疑似重复 · 将合并为变体</span>}
                    </div>
                  </div>
                  <div className="q-actions">
                    <button className="btn btn-primary btn-sm" disabled={p.busy} onClick={() => approveOne(p.importId, p.itemId, p.rowId)}>
                      {p.busy ? '入库中…' : '通过'}
                    </button>
                    <button className="btn btn-outline btn-sm" onClick={() => {
                      const imp = (pendingImports ?? []).find((x) => x.id === p.importId);
                      if (imp) openReviewFor(imp);
                    }}>审核解答</button>
                  </div>
                </div>
              ))
            )
          ) : rows.length === 0 ? (
            <div className="empty"><b>没有匹配的题目</b><p>换个筛选条件试试</p></div>
          ) : (
            rows.map((r) => (
              <div className="q-card" key={r.id}>
                <div className="q-id">{r.id}</div>
                <div className="q-body">
                  <div className="q-title" onClick={() => openDetail(r)}>{r.title}</div>
                  <div className="q-meta">
                    <span className="chip chip-green">{r.status}</span>
                    <span>{r.company}</span>
                    <span className="chip chip-slate">{TOPIC_NAME[r.topic] ?? r.topic}</span>
                    <span style={{ fontSize: 12 }}><Stars n={r.difficulty} /></span>
                    {r.source_type === 'USER_PRIVATE' && <span className="chip chip-purple">私有</span>}
                    {r.tags?.slice(0, 2).map((t) => <span key={t} className="chip chip-slate">#{t}</span>)}
                  </div>
                </div>
                <div className="q-actions">
                  <button className="btn btn-outline btn-sm" onClick={() => setEditQ(r)}>编辑</button>
                  <button className="btn btn-outline btn-sm" onClick={() => { toast('🧪', '已加入「面经考试」试卷'); navigate('/exam'); }}>加入考试</button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setMergeQ(r)}>合并重复</button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* —— 题目详情(全屏最大化) —— */}
      {detail && (
        <Modal title={`${detail.id} · 题目详情`} size="xl" onClose={() => setDetail(null)}
          footer={
            <>
              <button className="btn btn-outline" onClick={() => { setDetail(null); setEditQ(detail); }}>编辑</button>
              <button className="btn btn-primary" onClick={() => { toast('🧪', '已加入「面经考试」试卷'); setDetail(null); }}>加入考试</button>
            </>
          }>
          <div className="q-meta" style={{ marginBottom: 14 }}>
            <span className="chip chip-slate">{TOPIC_NAME[detail.topic] ?? detail.topic}</span>
            <span>{detail.company}</span>
            <Stars n={detail.difficulty} />
          </div>
          <h3 style={{ fontSize: 17, fontWeight: 650, marginBottom: 14 }}>{detail.title}</h3>
          {detailErr && <div className="demo-banner demo-banner-err" style={{ marginBottom: 10 }}>{detailErr}</div>}
          {detailHtml ? (
            <>
              {detailHtml.includes('<details') && (
                <div className="qa-tools">
                  <span className="qa-tools-label">💬 面试官追问 · 点击问题可展开/收起参考答案</span>
                  <button className="btn btn-ghost btn-sm" onClick={() => toggleQA(true)}>全部展开</button>
                  <button className="btn btn-ghost btn-sm" onClick={() => toggleQA(false)}>全部收起</button>
                </div>
              )}
              <div
                ref={answerRef}
                className="bank-answer"
                onClick={(e) => {
                  const svg = (e.target as HTMLElement).closest('svg.sdiagram') as SVGSVGElement | null;
                  if (svg) setZoomSvg(svg.outerHTML);
                }}
                dangerouslySetInnerHTML={{ __html: SD_MARKER_DEFS + transformAnswer(detailHtml) }}
              />
            </>
          ) : (
            <div className="ans-empty">
              <p>这道题还没有答案内容。</p>
              <p className="hint">可以让系统生成一份结构化的详细解答（一句话回答 / 通俗类比 / 面试官考什么 / 具体怎么做 / 常见坑 / 追问 / 评分要点）。</p>
              <button className="btn btn-primary" onClick={genAnswer} disabled={detailBusy}>
                {detailBusy ? '生成中…' : '✨ 生成详细解答'}
              </button>
            </div>
          )}
        </Modal>
      )}

      {/* —— 架构图放大查看 —— */}
      {zoomSvg && (
        <div className="svg-lightbox" onClick={() => setZoomSvg(null)}>
          <div className="svg-lightbox-inner" dangerouslySetInnerHTML={{ __html: zoomSvg }} />
          <div className="svg-lightbox-hint">点击任意位置关闭 · 架构图已放大</div>
        </div>
      )}

      {/* —— 编辑草稿 —— */}
      {editQ && (
        <Modal title="编辑题目草稿" size="lg" onClose={() => setEditQ(null)}
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setEditQ(null)}>取消</button>
              <button className="btn btn-primary" onClick={() => { toast('✅', '草稿已保存'); setEditQ(null); setReload((x) => x + 1); }}>保存</button>
            </>
          }>
          <div className="field">
            <label>题目 <span className="req">*</span></label>
            <input className="inp" defaultValue={editQ.title} />
          </div>
          <div className="form-row">
            <div className="field">
              <label>题型</label>
              <select className="select" defaultValue="text"><option>单选</option><option>文本</option><option>代码</option></select>
            </div>
            <div className="field">
              <label>难度</label>
              <select className="select" defaultValue={String(editQ.difficulty)}>
                {[1, 2, 3, 4, 5].map((d) => <option key={d} value={d}>{"★".repeat(d)}</option>)}
              </select>
            </div>
          </div>
          <div className="form-row">
            <div className="field">
              <label>公司</label>
              <input className="inp" defaultValue={editQ.company} />
            </div>
            <div className="field">
              <label>职级</label>
              <input className="inp" defaultValue="中级" />
            </div>
          </div>
          <div className="form-row">
            <div className="field">
              <label>日期</label>
              <input className="inp" defaultValue="2026-11-14" />
            </div>
            <div className="field">
              <label>技能映射</label>
              <select className="select" defaultValue={editQ.topic}>
                {TOPICS.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
              </select>
            </div>
          </div>
          <div className="field">
            <label>答案要点与 Rubric</label>
            <textarea className="textarea" style={{ minHeight: 110 }} defaultValue={`区分「请求超时」与「操作失败」；使用稳定的幂等键、持久化结果与原子写入；限制重试次数并验证并发场景。\n\n评分：幂等机制 40% / 失败分类 30% / 验证方法 30%`} />
          </div>
          <div className="hint">来源：{editQ.source_type === 'USER_PRIVATE' ? '私有导入' : '公开题库'} · 私有资料不会自动进入公共题库</div>
        </Modal>
      )}

      {/* —— 合并重复 —— */}
      {mergeQ && (
        <Modal title="合并重复问法" onClose={() => setMergeQ(null)}
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setMergeQ(null)}>取消</button>
              <button className="btn btn-primary" onClick={() => { toast('🔗', '已合并为问题变体'); setMergeQ(null); }}>合并为问法变体</button>
            </>
          }>
          <p style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.7 }}>
            发现相似题 <b>Q-018</b> · 相似度仅辅助判断。
            <br />
            “重试机制如何保证幂等？”可合并为标准问题的不同问法，保留两份来源。
          </p>
          <div className="field" style={{ marginTop: 16 }}>
            <label>合并到（canonical）</label>
            <select className="select" defaultValue={mergeQ.id}>
              <option value={mergeQ.id}>{mergeQ.id} · {mergeQ.title}</option>
              <option value="Q-018">Q-018 · 工具调用超时后，如何避免重复执行？</option>
            </select>
          </div>
          <div className="field">
            <label>保留的问法变体</label>
            <div className="list-row" style={{ borderBottom: 'none' }}>
              <span className="chip chip-slate">变体 1</span>
              <span style={{ fontSize: 13 }}>“重试机制如何保证幂等？”</span>
            </div>
            <div className="list-row">
              <span className="chip chip-slate">变体 2</span>
              <span style={{ fontSize: 13 }}>{mergeQ.title}</span>
            </div>
          </div>
        </Modal>
      )}

      {/* —— 导入面经 —— */}
      {importOpen && (
        <Modal
          title={stage === 'upload' ? '导入面经' : stage === 'review' ? '审核提取的问题' : '导入完成'}
          size="lg"
          onClose={() => setImportOpen(false)}
          footer={
            stage === 'upload' ? (
              <>
                <button className="btn btn-outline" onClick={() => setImportOpen(false)}>取消</button>
                <button className="btn btn-primary" onClick={startImport}>开始解析</button>
              </>
            ) : stage === 'review' ? (
              <>
                <button className="btn btn-outline" onClick={() => setImportOpen(false)}>稍后处理</button>
                <button className="btn btn-primary" onClick={approveAll} disabled={approveBusy}>
                  {approveBusy ? '正在写入…' : review ? '全部通过' : '全部通过（演示）'}
                </button>
              </>
            ) : stage === 'done' ? (
              <button className="btn btn-primary" onClick={() => setImportOpen(false)}>完成</button>
            ) : undefined
          }
        >
          {stage === 'upload' && (
            <>
              <div
                className={`dropzone ${dragOver ? 'dragover' : ''}`}
                role="button"
                tabIndex={0}
                onClick={() => fileRef.current?.click()}
                onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') fileRef.current?.click(); }}
                onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); setDragOver(true); }}
                onDragLeave={(e) => { e.preventDefault(); setDragOver(false); }}
                onDrop={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setDragOver(false);
                  addFiles(e.dataTransfer.files);
                }}
              >
                <div className="dropzone-ic">📥</div>
                <b>拖拽文件到这里，或点击选择</b>
                <p>支持 PDF / DOCX / Markdown / TXT / JPG / PNG，单文件 ≤ 12MB，可多选</p>
                <input
                  ref={fileRef}
                  type="file"
                  multiple
                  accept=".pdf,.docx,.md,.markdown,.txt,.text,.jpg,.jpeg,.png,application/pdf,text/markdown,text/plain,image/jpeg,image/png"
                  style={{ display: 'none' }}
                  onChange={(e) => { addFiles(e.target.files); e.target.value = ''; }}
                />
              </div>

              {files.length > 0 && (
                <div className="file-list">
                  {files.map((f, i) => (
                    <div className="file-item" key={`${f.name}-${f.size}-${i}`}>
                      <span className="file-ic">{f.name.endsWith('.pdf') ? '📕' : f.name.endsWith('.docx') ? '📘' : /\.(jpe?g|png)$/i.test(f.name) ? '🖼️' : '📄'}</span>
                      <span className="file-name">{f.name}</span>
                      <span className="file-size">{f.size < 1024 ? `${f.size} B` : `${(f.size / 1024).toFixed(0)} KB`}</span>
                      <button className="file-x" title="移除" onClick={(e) => { e.stopPropagation(); setFiles((prev) => prev.filter((_, j) => j !== i)); }}>×</button>
                    </div>
                  ))}
                </div>
              )}

              {importErr && <div className="demo-banner demo-banner-err" style={{ marginTop: 10 }}>{importErr}</div>}

              <div className="form-row" style={{ marginTop: 12 }}>
                <div className="field">
                  <label>公司（可选）</label>
                  <input className="inp" placeholder="如 字节跳动" value={company} onChange={(e) => setCompany(e.target.value)} />
                </div>
                <div className="field">
                  <label>职级（可选）</label>
                  <input className="inp" placeholder="如 P6 / 中级" value={level} onChange={(e) => setLevel(e.target.value)} />
                </div>
              </div>

              <div className="field" style={{ marginTop: 12 }}>
                <label>或粘贴文本（可与文件一起提交）</label>
                <textarea
                  className="textarea"
                  style={{ minHeight: 90 }}
                  placeholder="把面经原文贴到这里，例如：&#10;问：讲一下你在项目中如何设计 Agent 的记忆管理？&#10;答：……（有答案会直接保留，没有答案自动生成）"
                  value={pasteText}
                  onChange={(e) => setPasteText(e.target.value)}
                />
              </div>
              <div className="hint">私有资料仅当前 Workspace 可见，不会自动进入公共题库；解析结果需人工审核，缺答案的题会自动生成详细解答。</div>
            </>
          )}

          {stage === 'review' && (
            <div className="rev-split">
              {/* 左: 原文预览 */}
              <div className="rev-src">
                <div className="rev-pane-title">📄 原文预览</div>
                {(review?.meta?.files?.length
                  ? review.meta.files.map((f) => ({ name: f.name, sub: f.ok ? `${f.chars} 字符 · ${f.suffix || '文本'}` : (f.error || '解析失败'), ok: f.ok }))
                  : files.map((f, i) => ({ name: f.name, sub: `${(f.size / 1024).toFixed(1)} KB`, ok: true }))
                ).map((f, i) => (
                  <div className={`rev-src-file ${f.ok ? '' : 'bad'}`} key={`${f.name}-${i}`}>
                    <b>{f.ok ? '📄' : '⚠️'} {f.name}</b>
                    <span className="hint">{f.sub}</span>
                  </div>
                ))}
                {pasteText.trim() && (
                  <>
                    <div className="rev-pane-title" style={{ marginTop: 12 }}>📝 粘贴的文本</div>
                    <div className="rev-src-text">
                      {pasteText.slice(0, 600)}{pasteText.length > 600 ? '\n…（截断预览）' : ''}
                    </div>
                  </>
                )}
                {(review?.meta?.company || review?.meta?.level) && (
                  <div className="hint" style={{ marginTop: 10 }}>
                    来源标注：{review?.meta?.company || '—'} · {review?.meta?.level || '—'}
                  </div>
                )}
                <div className="hint" style={{ marginTop: 10 }}>
                  🔒 私有资料仅当前工作空间可见，不会自动进入公共题库；<b>审核通过前不会进入考试</b>。
                </div>
              </div>

              {/* 右: 提取结果 */}
              <div className="rev-items">
                {review ? (
                  <>
                    <div className="hint" style={{ marginBottom: 12 }}>
                      {review.short_code} · 共提取 {review.items.length} 道题 · 全部通过后进入题库
                    </div>
                    {review.meta?.files?.filter((f) => !f.ok).map((f) => (
                      <div className="demo-banner demo-banner-err" key={f.name} style={{ marginBottom: 8 }}>
                        {f.name}：{f.error || '解析失败'}
                      </div>
                    ))}
                    {review.items.map((it) => (
                      <div className="rev-item" key={it.id}>
                        <div className="q-body">
                          <div className="q-title">{it.title}</div>
                          <div className="q-meta">
                            <span className={`chip ${it.extract_confidence >= 0.5 ? 'chip-green' : 'chip-amber'}`}>
                              置信度 {Math.round(it.extract_confidence * 100)}%
                            </span>
                            <span className="chip chip-slate">{it.suggested_skill_key}</span>
                            {it.has_answer
                              ? (it.answer_source === 'llm'
                                  ? <span className="chip chip-purple">AI 草稿 · 大模型生成</span>
                                  : <span className="chip chip-green">解答草稿 · 规则模板</span>)
                              : <span className="chip chip-amber">暂无答案</span>}
                            {it.is_duplicate_of && <span className="chip chip-red">与题库重复 · 将合并为变体</span>}
                          </div>
                        </div>
                        <div className="q-actions">
                          <button
                            className="btn btn-outline btn-sm"
                            disabled={!it.draft_answer}
                            onClick={() => setOpenAns((s) => ({ ...s, [it.id]: !s[it.id] }))}
                          >
                            {openAns[it.id] ? '收起解答' : '查看解答'}
                          </button>
                          <button
                            className="btn btn-outline btn-sm"
                            disabled={ansBusy === it.id}
                            onClick={() => regenerateItem(it.id)}
                          >
                            {ansBusy === it.id ? '生成中…' : it.has_answer ? '重新生成' : '✨ 生成解答'}
                          </button>
                        </div>
                        {openAns[it.id] && it.draft_answer && (
                          <div className="ans-preview bank-answer" dangerouslySetInnerHTML={{ __html: it.draft_answer }} />
                        )}
                      </div>
                    ))}
                  </>
                ) : (
                  <div className="empty"><b>没有可审核的内容</b><p>请先上传文件或粘贴文本并开始解析</p></div>
                )}
              </div>
            </div>
          )}

          {stage === 'done' && (
            <div className="score-box" style={{ padding: '36px 20px' }}>
              <div style={{ fontSize: 34 }}>✅</div>
              <h3 style={{ fontSize: 17, fontWeight: 650, marginTop: 10 }}>
                {approveResult ?? `已通过 ${approved.length} 道题`}
              </h3>
              <p className="hint" style={{ textAlign: 'center', marginTop: 6 }}>
                已加入「个人题库」，可在面经库中查看；答案草稿已生成，等待你进一步编辑。
              </p>
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
