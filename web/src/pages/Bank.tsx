import StudyEntryButton from '../components/StudyEntryButton';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAppState } from '../components/AppState';
import { DiffLabel, Modal, PageHead, Stars } from '../components/ui';
import { api, BankData, ImportReview, QuestionDetail } from '../api/client';
import { trackImport } from '../lib/importTracker';
import BankQuestionEditor from '../components/BankQuestionEditor';
import BankReviewQueue, { sanitizeReviewAnswer } from './BankReviewQueue';
import RetestMappingReview from '../components/RetestMappingReview';
import {beginQuestionPractice} from '../lib/questionWorkshop';
import {bankSourcePresentation} from '../lib/bankSource';
import ExperiencedBank, { EXPERIENCED_SOURCE_LABELS, experiencedLevelLabel, type ExperiencedQuestion } from './ExperiencedBank';
import { experiencedPracticePath, rememberExperiencedFilters } from '../lib/experiencedBankPath';
const bankUrl = new URL('../data/bank.json', import.meta.url).href;

type ImportStage = 'upload' | 'review';
type Facet = 'companies' | 'topics' | 'sources';
type Facets = Record<Facet, string[]>;
type FacetCount = {name: string; count: number};
type InterviewSet = { id: string; import_id: number; title: string; project_id: number; project_name: string; questions: {
  id: number; title: string; source_item_id: number; original_title: string; extracted_title: string; source_files: string[];
  evidence: {label: string; revision: string} | null;
  fact_refs?: {ref: string; label: string; text: string; provenance: string; revision?: string}[];
  rewrite_method?: 'model' | 'conditional_rules';
  rewrite_fallback_reason?: string | null;
}[] };
const emptyFacets: Facets = {companies: [], topics: [], sources: []};
const pageSize = 20;
const facetLabels: Record<Facet, string> = {companies: '公司', topics: '主题', sources: '来源'};
const sourceLabels: Record<string, string> = {PUBLIC_LICENSED: '共享题库', VERIFIED_PUBLIC: '已验证公开题', USER_PRIVATE: '个人收录', GENERATED: '生成题'};

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

/** 工作空间题目的列表数据。 */
interface Row {
  id: string;
  numericQuestionId: number;
  title: string;
  company: string;
  topic: string;
  difficulty: number;
  tags: string[];
  source_type: string;
  status: string;
}

type PracticeQuestion = Pick<Row, 'id' | 'numericQuestionId'>;
type ExperiencedReference = {
  id: string;
  numericQuestionId: number;
  title: string;
  domainName: string;
  level: ExperiencedQuestion['level'];
  source?: ExperiencedQuestion['source'];
};
type ReferenceQuestion = Row | ExperiencedReference;

type Scope = 'bank' | 'mine' | 'pending' | 'sets';

const TOPIC_NAME: Record<string, string> = {
  rag: 'RAG 检索增强生成',
  harness: 'Agent 编排与 Harness',
  ctx: '上下文工程与记忆',
  infer: '模型推理与部署',
  eval: '评估与可观测',
  sys: '系统设计与高可用',
  algo: '算法与训练原理',
  biz: '选型、安全与业务落地',
};
const TOPICS = Object.entries(TOPIC_NAME).map(([code, name]) => ({code, name}));

function bankScope(query: URLSearchParams): Scope {
  if (query.get('review') === '1') return 'pending';
  if (query.has('set') || query.get('scope') === 'sets') return 'sets';
  if (query.get('scope') === 'mine') return 'mine';
  return 'bank';
}

export default function Bank() {
  const navigate = useNavigate();
  const { toast } = useAppState();
  const location = useLocation();
  const query = new URLSearchParams(location.search);
  const scope = bankScope(query);
  const experienced = scope === 'bank';
  const hasImportLink = query.get('import') === '1';
  const requestedSet = scope === 'sets' ? query.get('set') : null;
  const [pendingCount, setPendingCount] = useState<number | null>(null);
  const [facets, setFacets] = useState<Facets>(emptyFacets);
  const [showFilters, setShowFilters] = useState(false);
  const [page, setPage] = useState(0);
  const [bankLoading, setBankLoading] = useState(true);
  const [detail, setDetail] = useState<ReferenceQuestion | null>(null);
  const generalDetail = detail && 'source_type' in detail ? detail : null;
  const [zoomSvg, setZoomSvg] = useState<string | null>(null);
  const answerRef = useRef<HTMLDivElement>(null);
  const toggleQA = (open: boolean) => {
    answerRef.current?.querySelectorAll('details').forEach((d) => { (d as HTMLDetailsElement).open = open; });
  };
  const [editQ, setEditQ] = useState<Row | null>(null);
  const [mappingQuestion, setMappingQuestion] = useState<{ id: number; title: string } | null>(null);
  const [mergeQ, setMergeQ] = useState<Row | null>(null);
  const [practiceBusy,setPracticeBusy] = useState<string|null>(null);
  const [practiceError, setPracticeError] = useState<{id: string; message: string} | null>(null);
  const practiceAttempts = useRef(new Map<string,string>());
  const startPractice = async (row: PracticeQuestion, catalogPractice = experienced) => {
    setPracticeBusy(row.id);
    setPracticeError(null);
    try {
      const questionId = row.numericQuestionId;
      const key=practiceAttempts.current.get(row.id) || crypto.randomUUID();
      practiceAttempts.current.set(row.id,key);
      const exam=await beginQuestionPractice(questionId,key,api,catalogPractice);
      practiceAttempts.current.delete(row.id);
      if (catalogPractice) {
        const filters = new URLSearchParams(location.search);
        rememberExperiencedFilters(exam.id, filters);
        navigate(experiencedPracticePath(exam.id, filters));
      } else navigate(`/exam/diagnosis?id=${exam.id}`);
    } catch (error) {
      const message = `无法开始练习：${error instanceof Error ? error.message : String(error)}`;
      if (catalogPractice) setPracticeError({id: row.id, message});
      else toast('⚠️', message);
    }
    finally {setPracticeBusy(null);}
  };
  const [importOpen, setImportOpen] = useState(hasImportLink);

  useEffect(() => {
    if (hasImportLink) setImportOpen(true);
  }, [hasImportLink]);

  useEffect(() => {
    const query = new URLSearchParams(location.search);
    if (query.has('track')) {
      query.delete('track');
      navigate({ search: query.toString(), hash: location.hash }, { replace: true, state: location.state });
    }
  }, [location.search, location.hash, location.state, navigate]);
  const [stage, setStage] = useState<ImportStage>('upload');

  // 后端题库
  const [apiData, setApiData] = useState<BankData | null>(null);
  const [apiErr, setApiErr] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [q, setQ] = useState('');

  // 导入流水线状态
  const [pasteText, setPasteText] = useState('');
  const [review, setReview] = useState<ImportReview | null>(null);

  // 真实文件上传
  const [files, setFiles] = useState<File[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [importErr, setImportErr] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [sets, setSets] = useState<InterviewSet[]>([]);
  const [setsError, setSetsError] = useState('');
  const [activeSet, setActiveSet] = useState<InterviewSet | null>(null);
  const [projects, setProjects] = useState<{id:number;name:string}[]>([]);
  const [projectId, setProjectId] = useState('');
  const [setTitle, setSetTitle] = useState('');
  const [selectedItems, setSelectedItems] = useState<number[]>([]);
  const [edits, setEdits] = useState<Record<number,string>>({});
  const [setBusy, setSetBusy] = useState(false);
  const [paperBusy, setPaperBusy] = useState(false);
  const [setError, setSetError] = useState('');
  const [duration, setDuration] = useState(30);
  const [promoted, setPromoted] = useState<number[]>([]);
  const setAttempts = useRef(new Map<string, string>());
  const paperAttempts = useRef(new Map<string, string>());
  const [company, setCompany] = useState('');
  const [level, setLevel] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  // 答案预览
  const [openAns, setOpenAns] = useState<Record<number, boolean>>({});

  // 题目详情(真实答案)
  const [detailHtml, setDetailHtml] = useState<string | null>(null);
  const [trustedDetail, setTrustedDetail] = useState(false);
  const [detailLoading,setDetailLoading]=useState(false);
  const detailRequest=useRef(0);
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailErr, setDetailErr] = useState<string | null>(null);

  useEffect(() => {
    if (scope !== 'mine') return;
    let alive = true;
    const params = new URLSearchParams({scope: 'mine', limit: String(pageSize), offset: String(page * pageSize)});
    for (const [facet, choices] of Object.entries(facets)) for (const choice of choices) params.append(facet, choice);
    if (q.trim()) params.set('q', q.trim());
    setBankLoading(true);
    api.get<BankData>(`/bank?${params.toString()}`)
      .then((d) => { if (alive) { setApiData(d); setApiErr(null); setPage((value) => Math.min(value, Math.max(0, Math.ceil(d.total / pageSize) - 1))); } })
      .catch((e) => { if (alive) { setApiErr(String(e.message || e)); setApiData(null); } })
      .finally(() => { if (alive) setBankLoading(false); });
    return () => { alive = false; };
  }, [scope, facets, q, reload, page]);

  useEffect(() => {
    if (scope !== 'sets') return;
    let alive = true;
    api.get<{items:InterviewSet[]}>('/bank/interview-sets').then(data => {
      if (alive) {
        setSets(data.items); setSetsError('');
        if (requestedSet) {
          const entry = data.items.find(item => item.id === requestedSet);
          if (entry) setActiveSet(entry);
          else setSetsError('未找到面经集');
        }
      }
    }).catch((error: Error) => { if (alive) setSetsError(error.message); });
    return () => { alive = false; };
  }, [scope, reload, requestedSet]);

  useEffect(() => {
    if (stage !== 'review' || !importOpen) return;
    let alive = true;
    api.get<{items:{id:number;name:string}[]}>('/projects').then(result => {
      if (alive) setProjects(result.items ?? []);
    }).catch((error:Error) => { if (alive) setSetError(`项目加载失败：${error.message}`); });
    return () => { alive = false; };
  }, [stage, importOpen]);

  const rows: Row[] = useMemo(() => (apiData?.items ?? []).map(item => ({
    id: item.short_code,
    numericQuestionId: item.id,
    title: item.title,
    company: bankSourcePresentation(item).company,
    topic: item.topic,
    difficulty: item.difficulty ?? 3,
    tags: item.tags ?? [],
    source_type: item.source_type,
    status: item.status || '已收录',
  })), [apiData]);
  const resultCount = apiData?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(resultCount / pageSize));
  const toggleFacet = (facet: Facet, name: string) => {
    setFacets(value => ({...value, [facet]: value[facet].includes(name) ? value[facet].filter(choice => choice !== name) : [...value[facet], name]}));
    setPage(0);
  };
  const facetOptions = (facet: Facet) => {
    const counts = (apiData?.facets?.[facet] ?? []) as unknown as FacetCount[];
    const options = new Map(counts.map(({name, count}) => [name, count]));
    for (const choice of facets[facet]) if (!options.has(choice)) options.set(choice, 0);
    return [...options.entries()];
  };
  const facetName = (facet: Facet, name: string) => {
    if (facet === 'topics') return TOPIC_NAME[name] ?? name;
    if (facet === 'sources') return sourceLabels[name] ?? name;
    return name === '所有公司' ? '原帖未限定公司' : name;
  };

  const openReviewFor = (imp: ImportReview) => {
    if (imp.meta?.generation_task_id) {
      navigate(`/bank/workshop?task=${imp.meta.generation_task_id}`);
      return;
    }
    setReview(imp);
    setSelectedItems(imp.items.filter(item => item.status !== 'REJECTED').map(item => item.id));
    setEdits({});
    setSetTitle(`${imp.meta?.company || '项目面试'} · ${imp.short_code}`);
    setSetError('');
    setOpenAns({});
    setStage('review');
    setImportOpen(true);
  };

  const selectScope = (value: Scope) => {
    const next = new URLSearchParams(location.search);
    for (const key of ['scope', 'review', 'item', 'set', 'import', 'track']) next.delete(key);
    if (value === 'pending') next.set('review', '1');
    if (value === 'mine' || value === 'sets') next.set('scope', value);
    setActiveSet(null);
    navigate({ search: next.toString() });
  };

  const openSet = (entry: InterviewSet) => {
    setActiveSet(entry);
    setSetError('');
    setPromoted([]);
    if (scope !== 'sets') {
      const next = new URLSearchParams(location.search);
      for (const key of ['review', 'item', 'import', 'track']) next.delete(key);
      next.set('scope', 'sets');
      navigate({ search: next.toString() });
    }
  };

  const closeSet = () => {
    setActiveSet(null);
    if (query.has('set')) {
      const next = new URLSearchParams(query);
      next.delete('set');
      next.set('scope', 'sets');
      navigate({ search: next.toString() }, { replace: true });
    }
  };

  const createSet = async () => {
    if (!review || !projectId || !selectedItems.length || !setTitle.trim()) return;
    const form = {project_id:Number(projectId), item_ids:selectedItems, title:setTitle,
      edits:Object.fromEntries(selectedItems.filter(id => edits[id] !== undefined).map(id => [id, edits[id]]))};
    const key = JSON.stringify([review.id, form]);
    const requestId = setAttempts.current.get(key) || crypto.randomUUID();
    setAttempts.current.set(key, requestId);
    setSetBusy(true);
    setSetError('');
    try {
      const entry = await api.post<InterviewSet>(`/bank/imports/${review.id}/interview-sets`, {...form, request_id:requestId});
      setImportOpen(false);
      openSet(entry);
      setReload(value => value + 1);
    } catch (error) { setSetError((error as Error).message); }
    finally { setSetBusy(false); }
  };

  const startPaper = async () => {
    if (!activeSet) return;
    const key = `${activeSet.id}:${duration}`;
    const requestId = paperAttempts.current.get(key) || crypto.randomUUID();
    paperAttempts.current.set(key, requestId);
    setPaperBusy(true);
    setSetError('');
    try {
      const exam = await api.post<{id:number}>(`/bank/imports/${activeSet.import_id}/interview-sets/${activeSet.id}/paper`, {request_id:requestId, duration_minutes:duration});
      paperAttempts.current.delete(key);
      navigate(`/exam/diagnosis?id=${exam.id}`);
    } catch (error) { setSetError((error as Error).message); }
    finally { setPaperBusy(false); }
  };

  const promoteOriginal = async (question: InterviewSet['questions'][number]) => {
    if (!activeSet) return;
    setSetBusy(true);
    setSetError('');
    try {
      const result = await api.post<{created:string[]}>(`/bank/imports/${activeSet.import_id}/approve`, {
        item_ids:[question.source_item_id], edits:{[question.source_item_id]:{title:question.original_title}},
      });
      setPromoted(value => [...value, question.id]);
      toast('✓', result.created.length ? '原题已收入个人通用题库' : '原题已处理或与通用题库重复');
      setReload(value => value + 1);
    } catch (error) { setSetError((error as Error).message); }
    finally { setSetBusy(false); }
  };

  /** 收集文件: 过滤超大/不支持的, 去重同名 */
  const addFiles = (list: FileList | File[] | null) => {
    if (!list) return;
    const incoming = Array.from(list);
    const ok: File[] = [];
    const bad: string[] = [];
    incoming.forEach((f) => {
      const ext = (f.name.split('.').pop() || '').toLowerCase();
      if (f.size > 12 * 1024 * 1024) { bad.push(`${f.name} 超过 12MB`); return; }
      if (!['pdf', 'docx', 'md', 'markdown', 'txt', 'text', 'jpg', 'jpeg', 'png', 'webp'].includes(ext)) {
        bad.push(`${f.name} 格式暂不支持`);
        return;
      }
      if (files.length + ok.length >= 12) { bad.push('每次最多上传 12 个文件'); return; }
      if (![...files, ...ok].some((x) => x.name === f.name && x.size === f.size)) ok.push(f);
    });
    if (ok.length) setFiles((prev) => [...prev, ...ok]);
    setImportErr(bad.length ? bad.join('；') : null);
  };

  const moveFile = (index:number, step:number) => setFiles(previous => {
    const next = [...previous];
    const target = index + step;
    if (target < 0 || target >= next.length) return previous;
    [next[index], next[target]] = [next[target], next[index]];
    return next;
  });

  const closeImport = () => {
    setImportOpen(false);
    if (hasImportLink) {
      const next = new URLSearchParams(query);
      next.delete('import');
      navigate({ search: next.toString() }, { replace: true, state: location.state });
    }
  };

  const openUpload = () => {
    setStage('upload');
    setImportErr(null);
    setImportOpen(true);
  };

  const startImport = async () => {
    const text = pasteText.trim();
    if (!files.length && !text) {
      setImportErr('请拖入/选择面经文件，或粘贴面经文本');
      toast('⚠️', '先选择文件或粘贴文本');
      return;
    }
    setImportErr(null);
    setUploading(true);
    try {
      const form = new FormData();
      files.forEach((f) => form.append('files', f, f.name));
      if (text) form.append('pasted_text', text);
      if (company.trim()) form.append('company', company.trim());
      if (level.trim()) form.append('level', level.trim());
      const start = await api.upload<ImportReview>('/bank/imports/upload', form);
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
    } finally { setUploading(false); }
  };

  useEffect(() => {
    const h = () => setReload((x) => x + 1);
    window.addEventListener('cp-import-finished', h);
    return () => window.removeEventListener('cp-import-finished', h);
  }, []);

  /** 打开题目详情: 拉真实答案(私有题无静态 html) */
  const openDetail = async (r: ReferenceQuestion) => {
    const request=++detailRequest.current;
    setDetail(r);setDetailHtml(null);setTrustedDetail(false);setDetailErr(null);setDetailLoading(true);
    try {
      const d = await api.get<QuestionDetail>(`/bank/${encodeURIComponent(r.id)}`);
      const answer = d.answer_html || null;
      if (request !== detailRequest.current) return;
      // The existing bundled copy lets seeded diagrams retain SVG without trusting arbitrary API HTML.
      const bundled = 'domainName' in r && r.source === 'legacy' && answer
        ? await fetch(bankUrl).then(async response => {
          if (!response.ok) return undefined;
          const content: {questions: {id: string; html?: string}[]} = await response.json();
          return content.questions.find(question => question.id === r.id)?.html;
        }).catch(() => undefined)
        : undefined;
      if (request !== detailRequest.current) return;
      setTrustedDetail(answer === bundled);
      setDetailHtml(answer);
    } catch(e) {
      if(request===detailRequest.current)setDetailErr(`参考读取失败：${e instanceof Error?e.message:String(e)}。辅助记录未确认，暂不展示参考内容。`);
    } finally {if(request===detailRequest.current)setDetailLoading(false);}
  };

  /** 为题库题补生成答案 */
  const genAnswer = async () => {
    if (!generalDetail) return;
    const request=detailRequest.current;
    setDetailBusy(true);
    setDetailErr(null);
    try {
      const r = await api.post<{ answer_html: string; generated: boolean; source: string }>(
        `/bank/questions/${encodeURIComponent(generalDetail.id)}/answer?force=false`,
      );
      if(request!==detailRequest.current)return;
      setTrustedDetail(false);
      setDetailHtml(r.answer_html);
      toast('✨', r.generated ? '已生成详细解答' : '该题已有答案');
      setReload((x) => x + 1);
    } catch (e: any) {
      if(request===detailRequest.current)setDetailErr(String(e.message || e));
    } finally {
      setDetailBusy(false);
    }
  };

  return (
    <div className="bank-page">
      <PageHead title="面试题库">
        <button className="btn btn-primary" onClick={openUpload}>导入面经</button>
      </PageHead>
      <nav className="bank-actions" aria-label="题库工作区">
        {([['bank', '全部题目'], ['mine', '我的题目'], ['sets', '面经集'], ['pending', pendingCount ? '待审核 · ' + pendingCount : '待审核']] as const).map(([value, label]) =>
          <button key={value} className={'btn ' + (scope === value ? 'btn-primary' : 'btn-outline')} aria-pressed={scope === value} onClick={() => selectScope(value)}>{label}</button>)}
      </nav>
      {experienced && (
        <ExperiencedBank
          practiceBusy={practiceBusy}
          practiceError={practiceError}
          onPractice={question => startPractice({id: question.short_code, numericQuestionId: question.id})}
          onReference={(question, domainName) => openDetail({
            id: question.short_code, numericQuestionId: question.id, title: question.title, level: question.level, source: question.source, domainName,
          })}
        />
      )}
      <div className="bank-workspace">
        {(scope === 'pending' || pendingCount !== null) && <BankReviewQueue
          requestedKey={new URLSearchParams(location.search).get('item')}
          visible={scope === 'pending'}
          reload={reload}
          topicNames={TOPIC_NAME}
          onCountChange={setPendingCount}
          onLibraryChange={() => setReload(value => value + 1)}
          onOpenImport={openReviewFor}
          onUpload={openUpload}
        />}
        {scope === 'sets' ? <section className="card bank-section">
          <div className="bank-actions">
            <Link className="btn btn-outline" to="/exam">用面经组卷</Link>
            <Link className="btn btn-outline" to="/bank/workshop">项目面经与智能补题</Link>
          </div>
          {setsError ? <p role="alert">面经集加载失败：{setsError}</p> : sets.length ? sets.map(entry =>
            <div className="bank-set-row" key={entry.id}><div><b>{entry.title}</b><p className="hint">{entry.project_name} · {entry.questions.length} 题</p></div><button className="btn btn-outline" onClick={() => openSet(entry)}>打开面经集</button></div>
          ) : <p className="hint">还没有面经集。导入后在「待审核」核对原问并选择项目。</p>}
        </section> : scope === 'mine' && <>
        <section className="card bank-section">
          <p className="hint">题库筛选</p>
          <div className="bank-actions">
            {Object.entries(facets).flatMap(([facet, names]) => names.map(name => <button key={`${facet}:${name}`} className="btn btn-outline btn-sm" aria-label={`移除${facetLabels[facet as Facet]}：${facetName(facet as Facet, name)}`} onClick={() => toggleFacet(facet as Facet, name)}>{facetLabels[facet as Facet]} · {facetName(facet as Facet, name)} ×</button>))}
            {q.trim() && <button className="btn btn-outline btn-sm" onClick={() => {setQ(''); setPage(0);}}>关键词 · {q} ×</button>}
          </div>
          <p className="hint">同类条件命中任一项即可，不同类条件需要同时满足。</p>
          <div className="bank-actions">
            <button className="btn btn-outline" aria-expanded={showFilters} onClick={() => setShowFilters(value => !value)}>调整筛选</button>
            <button className="btn btn-ghost" onClick={() => {setFacets(emptyFacets); setQ(''); setPage(0);}}>重置筛选</button>
          </div>
          {showFilters && (Object.keys(facetLabels) as Facet[]).map(facet => <fieldset className="bank-facet" key={facet}>
            <legend>{facetLabels[facet]}</legend>
            <div className="bank-actions">{facetOptions(facet).map(([name, count]) => <label key={name}><input type="checkbox" checked={facets[facet].includes(name)} onChange={() => toggleFacet(facet, name)} /> {facetName(facet, name)} ({count})</label>)}</div>
          </fieldset>)}
        </section>
        <div className="card">
          <div className="card-head">
            <span className="card-title">
              我的题目 · {bankLoading ? '加载中…' : `${resultCount} 题`}
            </span>
            <input
              className="inp"
              aria-label="搜索我的题目"
              placeholder="搜索题目关键词…"
              value={q}
              onChange={(e) => {setQ(e.target.value); setPage(0);}}
              style={{ width: 220, padding: '6px 10px', fontSize: 13 }}
            />
          </div>

          {bankLoading ? <p className="empty" role="status">正在加载我的题目…</p> : apiErr ? <p role="alert">我的题目加载失败：{apiErr} <button className="btn btn-outline btn-sm" onClick={() => setReload(value => value + 1)}>重试</button></p> : rows.length === 0 ? (
            <div className="empty"><b>没有匹配的题目</b><p>换个筛选条件试试</p></div>
          ) : (
            rows.map((r) => (
              <div className="q-card" key={r.id}>
                <div className="q-id">{r.id}</div>
                <div className="q-body">
                  <div className="q-title">{r.title}</div>
                  <div className="q-meta">
                    <span className="chip chip-green">{r.status}</span>
                    <span>{r.company}</span>
                    <span className="chip chip-slate">{TOPIC_NAME[r.topic] ?? r.topic}</span>
                    <span style={{ fontSize: 12 }}><Stars n={r.difficulty} /></span>
                    {bankSourcePresentation(r).isPrivate && <span className="chip chip-purple">私有</span>}
                    <span className="chip chip-slate">{bankSourcePresentation(r).label}</span>
                    {r.tags?.slice(0, 2).map((t) => <span key={t} className="chip chip-slate">#{t}</span>)}
                  </div>
                </div>
                <div className="q-actions">
                  <button className="btn btn-outline btn-sm" disabled={!bankSourcePresentation(r).editable} title={bankSourcePresentation(r).editable?'编辑个人题目信息':'共享题库只读，可导入面经后整理为个人题目'} onClick={() => setEditQ(r)}>{bankSourcePresentation(r).editable?'编辑':'共享题只读'}</button>
                  {r.numericQuestionId != null && <button className="btn btn-outline btn-sm" onClick={() => {
                    if (r.numericQuestionId != null) setMappingQuestion({ id: r.numericQuestionId, title: r.title });
                  }}>复测映射</button>}
                  <StudyEntryButton kind="question" id={r.numericQuestionId} bankKey={r.id}/>
                  <button className="btn btn-outline btn-sm" disabled={practiceBusy!==null} onClick={() => startPractice(r)}>{practiceBusy===r.id?'准备考试…':'开始练习'}</button>
                  <button className="btn btn-outline btn-sm" onClick={() => openDetail(r)}>查看参考</button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setMergeQ(r)}>合并重复</button>
                </div>
              </div>
            ))
          )}
          <div className="bank-actions bank-pagination">
            <button className="btn btn-outline btn-sm" disabled={bankLoading || page === 0} onClick={() => setPage(page - 1)}>上一页</button>
            <span className="hint">第 {page + 1} / {pageCount} 页</span>
            <button className="btn btn-outline btn-sm" disabled={bankLoading || page + 1 >= pageCount} onClick={() => setPage(page + 1)}>下一页</button>
          </div>
        </div>
        </>}
      </div>

      {/* —— 题目详情(全屏最大化) —— */}
      {detail && (
        <Modal title={generalDetail ? `${detail.id} · 题目详情` : '查看参考'} size="xl" onClose={() => {++detailRequest.current;setDetail(null);}}
          footer={
            <>
              {generalDetail && <>
                <button className="btn btn-outline" disabled={!bankSourcePresentation(generalDetail).editable} title={bankSourcePresentation(generalDetail).editable?'编辑个人题目信息':'共享题库只读，可导入面经后整理为个人题目'} onClick={() => { setDetail(null); setEditQ(generalDetail); }}>{bankSourcePresentation(generalDetail).editable?'编辑':'共享题只读'}</button>
                <StudyEntryButton kind="question" id={generalDetail.numericQuestionId} bankKey={generalDetail.id}/>
              </>}
              <button className="btn btn-primary" disabled={practiceBusy!==null} onClick={() => startPractice(detail, !generalDetail)}>{practiceBusy?'准备考试…':practiceError?.id === detail.id ? '重试练习' : '开始练习'}</button>
            </>
          }>
          <div className="q-meta" style={{ marginBottom: 14 }}>
            {'domainName' in detail ? <>
              <span className="chip chip-slate">{detail.domainName}</span>
              <span className="chip chip-slate">{experiencedLevelLabel(detail)}</span>
              <span>{EXPERIENCED_SOURCE_LABELS[detail.source ?? 'authored']} · 共享题库 · 只读</span>
            </> : <>
              <span className="chip chip-slate">{TOPIC_NAME[detail.topic] ?? detail.topic}</span>
              <span>{detail.company}</span>
              <Stars n={detail.difficulty} />
            </>}
          </div>
          <h3 className={generalDetail ? undefined : 'experienced-prompt'} style={{ fontSize: 17, fontWeight: 650, marginBottom: 14 }}>{detail.title}</h3>
          {generalDetail && <p className="hint">{bankSourcePresentation(generalDetail).isPrivate ? `个人题库 · ${bankSourcePresentation(generalDetail).label}`:'共享题库只读；需要结合项目改写时，可在面经工作台导入并审核为个人题目。'}</p>}
          {detailErr && <div role="alert" className="demo-banner demo-banner-err" style={{ marginBottom: 10 }}>{detailErr}</div>}
          {practiceError?.id === detail.id && <p role="alert">{practiceError.message}</p>}
          <p className="hint">{generalDetail ? '查看参考后，该题族不再用于未辅助独立复测；仍可用于带辅助的学习和练习。' : '读取参考会记录这道题的辅助条件。建议先作答，再对照参考。'}</p>
          {detailLoading?<p role="status">正在记录辅助条件并读取参考…</p>:detailErr?<button className="btn btn-outline" onClick={()=>openDetail(detail)}>重新读取参考</button>:detailHtml ? (
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
                dangerouslySetInnerHTML={{ __html: trustedDetail
                  ? SD_MARKER_DEFS + transformAnswer(detailHtml)
                  : sanitizeReviewAnswer(transformAnswer(detailHtml)) }}
              />
            </>
          ) : !generalDetail ? <p>这道题暂未发布参考内容。</p> : (
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

      {mappingQuestion && <Modal title="审核复测映射" onClose={() => setMappingQuestion(null)}>
        <h3>{mappingQuestion.title}</h3>
        <RetestMappingReview questionId={mappingQuestion.id} />
      </Modal>}
      {editQ && <BankQuestionEditor question={editQ} topics={TOPIC_NAME[editQ.topic] ? TOPICS : [...TOPICS, {code: editQ.topic, name: editQ.topic}]} onClose={() => setEditQ(null)} onSaved={() => { toast('✅', '题目信息已保存'); setEditQ(null); setReload(x => x + 1); }} />}

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
      {scope === 'sets' && activeSet && (!requestedSet || activeSet.id === requestedSet) && <Modal title={activeSet.title} size="lg" onClose={closeSet} footer={
        <button className="btn btn-outline" onClick={closeSet}>回到面经库</button>
      }>
        <p className="hint">{activeSet.project_name} · {activeSet.questions.length} 道练习题</p>
        <section className="card bank-section bank-set-questions">
          <p className="hint">题目清单</p>
          {activeSet.questions.map((item, index) => <article className="bank-set-question" key={item.id}>
            <h3>{String(index + 1).padStart(2, '0')}　{item.rewrite_method === 'model' || (!item.rewrite_method && item.evidence) ? '项目题' : '条件式追问'}</h3>
            <p className="bank-question-text">{item.rewrite_method === 'model' ? item.title : item.title.split('\n\n').slice(1).join('\n\n') || item.title}</p>
            <details><summary>查看原文与依据</summary>
              <p>原问：{item.original_title}</p>
              {item.extracted_title !== item.original_title && <p>识别文字：{item.extracted_title}</p>}
              <p>来源：{item.source_files.join('、') || '旧导入未记录文件'}</p>
              {item.rewrite_method && <p>{item.rewrite_method === 'model' ? '模型改写 · 请核对引用' : `规则生成 · 条件题（${item.rewrite_fallback_reason === 'model_unavailable' ? '模型暂不可用，可从原导入重新生成' : item.rewrite_fallback_reason === 'no_evidence' ? '项目缺少可用资料' : '模型没有返回可核对的相关依据'}）`}</p>}
              {item.fact_refs?.length ? <ul>{item.fact_refs.map(fact => <li key={fact.ref}>{fact.provenance === 'user_statement' ? '本人填写' : '仓库观察'} · {fact.label}{fact.revision ? ` · ${fact.revision.slice(0, 12)}` : ''}：{fact.text}</li>)}</ul> : <p>项目依据：{item.evidence ? `${item.evidence.label} · ${item.evidence.revision}` : '无可引用资料'}</p>}
              <button className="btn btn-outline btn-sm" disabled={setBusy || promoted.includes(item.id)} onClick={() => promoteOriginal(item)}>{promoted.includes(item.id) ? '通用题收录已处理' : '将原问另收为通用题'}</button>
            </details>
          </article>)}
        </section>
        {activeSet.questions.some(item => item.rewrite_method === 'conditional_rules') && <button className="btn btn-outline" disabled={setBusy} onClick={async () => {
          setSetBusy(true);
          try {
            const source = await api.get<ImportReview>(`/bank/imports/${activeSet.import_id}`);
            openReviewFor(source);
            setProjectId(String(activeSet.project_id));
            setSelectedItems(activeSet.questions.map(item => item.source_item_id));
            setEdits(Object.fromEntries(activeSet.questions.filter(item => item.original_title !== item.extracted_title).map(item => [item.source_item_id, item.original_title])));
            setSetTitle(activeSet.title);
            setAttempts.current.clear();
            closeSet();
          } catch (error) { toast('⚠️', `无法打开原导入：${(error as Error).message}`); }
          finally { setSetBusy(false); }
        }}>重新核对并生成</button>}
        <section className="card bank-section">
          <p className="hint">准备这场面试</p>
          <h2>按本面经集生成试卷</h2>
          <p className="hint">试卷冻结当前题目和来源，仅用于练习。</p>
          <div className="bank-actions">
            <label>时长 <select className="inp" value={duration} onChange={event => setDuration(Number(event.target.value))}>
              {[15, 30, 45, 60].map(minutes => <option key={minutes} value={minutes}>{minutes} 分钟</option>)}
            </select></label>
            <button className="btn btn-primary" disabled={paperBusy} onClick={startPaper}>{paperBusy ? '正在生成试卷…' : '生成并打开试卷'}</button>
          </div>
        </section>
        {setError && <p className="hint" role="alert">{setError}</p>}
      </Modal>}

      {importOpen && (
        <Modal
          title={stage === 'upload' ? '从截图导入原帖' : '核对原问与项目改写'}
          size="lg"
          onClose={closeImport}
          footer={
            stage === 'upload' ? (
              <>
                <button className="btn btn-outline" onClick={closeImport}>取消</button>
                <button className="btn btn-primary" disabled={uploading} onClick={startImport}>{uploading ? '正在上传…' : '识别文字并提取问题'}</button>
              </>
            ) : stage === 'review' ? (
              <>
                <button className="btn btn-outline" onClick={closeImport}>稍后处理</button>
                <button className="btn btn-outline" onClick={() => {setImportOpen(false); selectScope('pending');}}>打开待审核队列</button>
                <button className="btn btn-primary" onClick={createSet} disabled={setBusy || !review || !projectId || !selectedItems.length || !setTitle.trim()}>
                  {setBusy ? '正在核对项目依据…' : '结合项目并保存到面经集'}
                </button>
              </>
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
                  accept=".pdf,.docx,.md,.markdown,.txt,.text,.jpg,.jpeg,.png,.webp,application/pdf,text/markdown,text/plain,image/jpeg,image/png,image/webp"
                  style={{ display: 'none' }}
                  onChange={(e) => { addFiles(e.target.files); e.target.value = ''; }}
                />
              </div>

              {files.length > 0 && (
                <div className="file-list">
                  {files.map((f, i) => (
                    <div className="file-item" key={`${f.name}-${f.size}-${i}`}>
                      <span className="file-ic">{f.name.endsWith('.pdf') ? '📕' : f.name.endsWith('.docx') ? '📘' : /\.(jpe?g|png)$/i.test(f.name) ? '🖼️' : '📄'}</span>
                      <span className="file-name">{i + 1}. {f.name}</span>
                      <span className="file-size">{f.size < 1024 ? `${f.size} B` : `${(f.size / 1024).toFixed(0)} KB`}</span>
                      <button className="btn btn-ghost btn-sm" disabled={i === 0} aria-label={`上移 ${f.name}`} onClick={() => moveFile(i, -1)}>↑</button>
                      <button className="btn btn-ghost btn-sm" disabled={i === files.length - 1} aria-label={`下移 ${f.name}`} onClick={() => moveFile(i, 1)}>↓</button>
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
                      {review.short_code} · 提取 {review.items.length} 道原问。原帖经历属于原作者；项目追问使用你所选项目的材料。
                    </div>
                    <div className="form-row">
                      <label className="field">面经集名称<input className="inp" maxLength={120} value={setTitle} onChange={event => setSetTitle(event.target.value)} /></label>
                      <label className="field">我的项目<select className="inp" value={projectId} onChange={event => setProjectId(event.target.value)}>
                        <option value="">{projects.length ? '选择项目' : '暂无可选项目'}</option>
                        {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
                      </select></label>
                    </div>
                    {setError && <p role="alert" className="hint">{setError}</p>}
                    {review.meta?.files?.filter((f) => !f.ok).map((f) => (
                      <div className="demo-banner demo-banner-err" key={f.name} style={{ marginBottom: 8 }}>
                        {f.name}：{f.error || '解析失败'}
                      </div>
                    ))}
                    {review.items.map((it) => (
                      <div className="rev-item" key={it.id}>
                        <div className="q-body">
                          <label className="hint"><input type="checkbox" disabled={it.status === 'REJECTED'} checked={selectedItems.includes(it.id)} onChange={() => setSelectedItems(previous => previous.includes(it.id) ? previous.filter(id => id !== it.id) : [...previous, it.id])} /> 收录原问 · {it.generation_meta?.source_files?.join('、') || '导入记录'}{it.generation_meta?.extraction_method ? ` · ${it.generation_meta.extraction_method === 'model' ? '模型提取' : '规则提取'}` : ''}</label>
                          <textarea className="textarea bank-original-input" aria-label={`修正原问 ${it.id}`} maxLength={1000} value={edits[it.id] ?? it.title} onChange={event => setEdits(previous => ({...previous, [it.id]:event.target.value}))} />
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
                        </div>
                        {openAns[it.id] && it.draft_answer && (
                          <div className="ans-preview bank-answer" dangerouslySetInnerHTML={{ __html: sanitizeReviewAnswer(it.draft_answer) }} />
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

        </Modal>
      )}
    </div>
  );
}
