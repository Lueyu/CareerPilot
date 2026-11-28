import { useT } from '../i18n';
import { BankQuestionRow, BankSearch, BankUploadPicker } from '../components/BankPresentation';
import StudyEntryButton from '../components/StudyEntryButton';
import AnswerGuide, {type TeachingGuide} from '../components/AnswerGuide';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAppState } from '../components/AppState';
import { Modal, PageHead, Stars } from '../components/ui';
import { api, hasOriginalBank, BankData, ImportReview, QuestionDetail } from '../api/client';
import ImportSourceReview, { importSourceText } from '../components/ImportSourceReview';
import { trackImport } from '../lib/importTracker';
import BankQuestionEditor from '../components/BankQuestionEditor';
import BankReviewQueue, { sanitizeReviewAnswer } from './BankReviewQueue';
import RetestMappingReview from '../components/RetestMappingReview';
import { bankMessageText, beginQuestionPractice, workshopSkillLabel, type BankMessage } from '../lib/questionWorkshop';
import {bankSourcePresentation} from '../lib/bankSource';
import ExperiencedBank, { EXPERIENCED_SOURCE_LABELS, experiencedDomainLabel, experiencedLevelLabel, type ExperiencedQuestion } from './ExperiencedBank';
import { experiencedPracticePath, rememberExperiencedFilters } from '../lib/experiencedBankPath';
import { loadLegacyBank } from './legacyBank';
import { PodcastQuestionAction, PodcastSelectionBar, usePodcastSelection } from '../components/PodcastQuestionAction';

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
const mergeExampleId = 'Q-018';
const mergeExampleQuestion = '“重试机制如何保证幂等？”';
const mergeExampleTitle = '工具调用超时后，如何避免重复执行？';
const facetLabels: Record<Facet, string> = {companies: 'bank.company', topics: 'bank.topic', sources: 'bank.source'};
const sourceLabels: Record<string, string> = {PUBLIC_LICENSED: 'bank.publicLicensed', VERIFIED_PUBLIC: 'bank.verifiedPublic', USER_PRIVATE: 'bank.userPrivate', GENERATED: 'bank.generatedSource'};

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
  reviewOnly?: boolean;
  studySessionId?: number | null;
  status: string;
}

type PracticeQuestion = Pick<Row, 'id' | 'numericQuestionId'>;
type ExperiencedReference = {
  id: string;
  numericQuestionId: number;
  title: string;
  domainName: string;
  domainKey: string;
  level: ExperiencedQuestion['level'];
  source?: ExperiencedQuestion['source'];
};
type ReferenceQuestion = Row | ExperiencedReference;

type Scope = 'bank' | 'mine' | 'pending' | 'sets';

const TOPIC_NAME: Record<string, string> = {
  rag: 'bank.topicRag',
  harness: 'bank.topicAgent',
  ctx: 'bank.topicContext',
  infer: 'bank.topicServing',
  eval: 'bank.topicEval',
  sys: 'bank.topicSystem',
  algo: 'bank.topicTraining',
  biz: 'bank.topicDelivery',
};

function bankScope(query: URLSearchParams): Scope {
  if (query.get('review') === '1') return 'pending';
  if (query.has('set') || query.get('scope') === 'sets') return 'sets';
  if (query.get('scope') === 'mine') return 'mine';
  return 'bank';
}

export default function Bank({ registered = false }: { registered?: boolean } = {}) {
  const t = useT();
  const topics = Object.entries(TOPIC_NAME).map(([code, key]) => ({code, name: t(key)}));
  const navigate = useNavigate();
  const { toast } = useAppState();
  const location = useLocation();
  const query = new URLSearchParams(location.search);
  const originalBank = hasOriginalBank();
  const requestedScope = bankScope(query);
  const scope = requestedScope === 'bank' && !originalBank ? 'mine' : requestedScope;
  const experienced = scope === 'bank';
  const [catalogSummary, setCatalogSummary] = useState<{questions: number; domains: number} | null>(null);
  const hasImportLink = query.get('import') === '1';
  const requestedSet = scope === 'sets' ? query.get('set') : null;
  const [pendingCount, setPendingCount] = useState<number | null>(null);
  const rememberPendingCount = useCallback((count: number | null) => {
    if (count !== null) setPendingCount(count);
  }, []);
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
      const message = error instanceof Error ? error.message : String(error);
      if (catalogPractice) setPracticeError({id: row.id, message});
      else toast('⚠️', t('bank.practiceFailed', { error: message }));
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
  const originalSource = importSourceText(review?.meta);

  // 真实文件上传
  const [files, setFiles] = useState<File[]>([]);
  const [importErr, setImportErr] = useState<BankMessage[]>([]);
  const [uploading, setUploading] = useState(false);
  const [sets, setSets] = useState<InterviewSet[]>([]);
  const [setsError, setSetsError] = useState<BankMessage>('');
  const [activeSet, setActiveSet] = useState<InterviewSet | null>(null);
  const [projects, setProjects] = useState<{id:number;name:string}[]>([]);
  const [projectId, setProjectId] = useState('');
  const [setTitle, setSetTitle] = useState('');
  const [selectedItems, setSelectedItems] = useState<number[]>([]);
  const podcastSelection = usePodcastSelection();
  const [edits, setEdits] = useState<Record<number,string>>({});
  const [setBusy, setSetBusy] = useState(false);
  const [paperBusy, setPaperBusy] = useState(false);
  const [setError, setSetError] = useState<BankMessage>('');
  const [duration, setDuration] = useState(30);
  const [promoted, setPromoted] = useState<number[]>([]);
  const setAttempts = useRef(new Map<string, string>());
  const paperAttempts = useRef(new Map<string, string>());
  const [company, setCompany] = useState('');
  const [level, setLevel] = useState('');

  // 答案预览
  const [openAns, setOpenAns] = useState<Record<number, boolean>>({});

  // 题目详情(真实答案)
  const [detailHtml, setDetailHtml] = useState<string | null>(null);
  const [trustedDetail, setTrustedDetail] = useState(false);
  const [guide, setGuide] = useState<TeachingGuide | null>(null);
  const [beginnerExample, setBeginnerExample] = useState<string | null>(null);
  const [detailLoading,setDetailLoading]=useState(false);
  const detailRequest=useRef(0);
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailErr, setDetailErr] = useState<BankMessage | null>(null);

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
          else setSetsError({ key: 'bank.setNotFound' });
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
    }).catch((error:Error) => { if (alive) setSetError({ key: 'bank.projectsFailed', values: { error: error.message } }); });
    return () => { alive = false; };
  }, [stage, importOpen]);

  const rows: Row[] = useMemo(() => (apiData?.items ?? []).map(item => ({
    id: item.short_code,
    numericQuestionId: item.id,
    title: item.title,
    company: item.company ?? '',
    topic: item.topic,
    difficulty: item.difficulty ?? 3,
    tags: item.tags ?? [],
    source_type: item.source_type,
    reviewOnly: item.review_only,
    studySessionId: item.study_session_id,
    status: item.status ?? '',
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
    const labels = facet === 'topics' ? TOPIC_NAME : facet === 'sources' ? sourceLabels : undefined;
    const key = labels && Object.prototype.hasOwnProperty.call(labels, name) ? labels[name] : undefined;
    if (key) return t(key);
    return name === '所有公司' ? t('bank.unspecifiedCompany') : name;
  };

  const openReviewFor = (imp: ImportReview) => {
    if (imp.meta?.generation_task_id) {
      navigate(`/bank/workshop?task=${imp.meta.generation_task_id}`);
      return;
    }
    setReview(imp);
    setSelectedItems(imp.items.filter(item => item.status !== 'REJECTED').map(item => item.id));
    setEdits({});
    setSetTitle(t('bank.defaultSetTitle', {
      company: imp.meta?.company || t('bank.projectInterview'), code: imp.short_code,
    }));
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
      toast('✓', result.created.length ? t('bank.originalAdded') : t('bank.originalAlreadyHandled'));
      setReload(value => value + 1);
    } catch (error) { setSetError((error as Error).message); }
    finally { setSetBusy(false); }
  };

  /** 收集文件: 过滤超大/不支持的, 去重同名 */
  const addFiles = (list: FileList | File[] | null) => {
    if (!list) return;
    const incoming = Array.from(list);
    const ok: File[] = [];
    const bad: BankMessage[] = [];
    incoming.forEach((f) => {
      const ext = (f.name.split('.').pop() || '').toLowerCase();
      if (f.size > 12 * 1024 * 1024) { bad.push({ key: 'bank.fileTooLarge', values: { name: f.name } }); return; }
      if (!['pdf', 'docx', 'md', 'markdown', 'txt', 'text', 'jpg', 'jpeg', 'png', 'webp'].includes(ext)) {
        bad.push({ key: 'bank.fileUnsupported', values: { name: f.name } });
        return;
      }
      if (files.length + ok.length >= 12) { bad.push({ key: 'bank.fileLimit' }); return; }
      if (![...files, ...ok].some((x) => x.name === f.name && x.size === f.size)) ok.push(f);
    });
    if (ok.length) setFiles((prev) => [...prev, ...ok]);
    setImportErr(bad);
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
    setImportErr([]);
    setImportOpen(true);
  };

  const startImport = async () => {
    const text = pasteText.trim();
    if (!files.length && !text) {
      setImportErr([{ key: 'bank.dropOrPaste' }]);
      toast('⚠️', t('bank.selectOrPasteFirst'));
      return;
    }
    setImportErr([]);
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
      toast('🚀', t('bank.importQueued', { code: start.short_code }));
    } catch (e: any) {
      setImportErr([String(e.message || e)]);
      toast('⚠️', t('bank.importSubmitFailed', { error: e.message }));
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
    setDetail(r);setDetailHtml(null);setGuide(null);setBeginnerExample(null);setTrustedDetail(false);setDetailErr(null);setDetailLoading(true);
    try {
      const d = await api.get<QuestionDetail>(`/bank/${encodeURIComponent(r.id)}`);
      const answer = d.answer_html || null;
      if (request !== detailRequest.current) return;
      // The existing bundled copy lets seeded diagrams retain SVG without trusting arbitrary API HTML.
      const bundled = 'domainName' in r && r.source === 'legacy' && answer
        ? await loadLegacyBank().then(content => {
          return content.questions.find(question => question.id === r.id)?.html;
        }).catch(() => undefined)
        : undefined;
      if (request !== detailRequest.current) return;
      if ('source_type' in r && r.reviewOnly) {
        const studySessionId = d.study_session_id ?? null;
        setDetail({ ...r, studySessionId });
        if (studySessionId !== r.studySessionId) {
          setApiData(data => data && {
            ...data,
            items: data.items.map(item => item.id === d.id && item.study_session_id === r.studySessionId
              ? { ...item, study_session_id: studySessionId }
              : item),
          });
        }
      }
      setTrustedDetail(answer === bundled);
      setGuide(d.teaching_guide ?? null);
      setBeginnerExample(d.beginner_example ?? null);
      setDetailHtml(answer);
    } catch(e) {
      if(request===detailRequest.current)setDetailErr({ key: 'bank.referenceFailed', values: { error: e instanceof Error ? e.message : String(e) } });
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
      toast('✨', r.generated ? t('bank.answerGenerated') : t('bank.answerAlreadyExists'));
      setReload((x) => x + 1);
    } catch (e: any) {
      if(request===detailRequest.current)setDetailErr(String(e.message || e));
    } finally {
      setDetailBusy(false);
    }
  };

  return (
    <div className="bank-page">
      <PageHead title={t('bank.bankTitle')} sub={experienced && catalogSummary ? t('bank.catalogSummary', catalogSummary) : undefined}>
        {registered && <Link className="btn btn-outline" to="/beta/study/chat">{t('bank.generateWithCoach')}</Link>}
        <button className="btn btn-primary" onClick={openUpload}>{t('bank.importInterview')}</button>
      </PageHead>
      <nav className="bank-actions" aria-label={t('bank.workspace')}>
        {([['bank', t('bank.allQuestions')], ['mine', t('bank.myQuestions')], ['sets', t('bank.interviewSets')], ['pending', pendingCount ? t('bank.pendingReviewCount', { count: pendingCount }) : t('bank.pendingReview')]] as const).filter(([value]) => value !== 'bank' || originalBank).map(([value, label]) =>
          <button key={value} className={'btn ' + (scope === value ? 'btn-primary' : 'btn-outline')} aria-pressed={scope === value} onClick={() => selectScope(value)}>{label}</button>)}
        {registered && <Link className="btn btn-ghost" to="/beta/bank">{t('bank.previousBankRecords')}</Link>}
      </nav>
      {experienced && (
        <ExperiencedBank
          onCatalogSummary={setCatalogSummary}
          practiceBusy={practiceBusy}
          practiceError={practiceError}
          onPractice={question => startPractice({id: question.short_code, numericQuestionId: question.id})}
          onReference={(question, domainName, domainKey) => openDetail({
            id: question.short_code, numericQuestionId: question.id, title: question.title, level: question.level, source: question.source, domainName, domainKey,
          })}
          onRetestMapping={({ id, title }) => setMappingQuestion({ id, title })}
        />
      )}
      <div className="bank-workspace">
        {scope === 'pending' && <BankReviewQueue
          requestedKey={query.get('item')}
          visible
          reload={reload}
          topicNames={Object.fromEntries(topics.map(({code, name}) => [code, name]))}
          onCountChange={rememberPendingCount}
          onLibraryChange={() => setReload(value => value + 1)}
          onOpenImport={openReviewFor}
          onUpload={openUpload}
        />}
        {scope === 'sets' ? <section className="card bank-section">
          <div className="bank-actions">
            <Link className="btn btn-outline" to="/exam">{t('bank.createExamFromNotes')}</Link>
            <Link className="btn btn-outline" to="/bank/workshop">{t('bank.openWorkshop')}</Link>
          </div>
          {setsError ? <p role="alert">{t('bank.setsFailed', { error: bankMessageText(setsError, t) })}</p> : sets.length ? sets.map(entry =>
            <div className="bank-set-row" key={entry.id}><div><b>{entry.title}</b><p className="hint">{t('bank.setMetadata', { name: entry.project_name, count: entry.questions.length })}</p></div><button className="btn btn-outline" onClick={() => openSet(entry)}>{t('bank.openSet')}</button></div>
          ) : <p className="hint">{t('bank.noInterviewSets')}</p>}
        </section> : scope === 'mine' && <>
        <section className="card bank-section">
          <p className="hint">{t('bank.bankFilters')}</p>
          <div className="bank-actions">
            {Object.entries(facets).flatMap(([facet, names]) => names.map(name => <button key={`${facet}:${name}`} className="btn btn-outline btn-sm" aria-label={t('bank.removeFacet', { facet: t(facetLabels[facet as Facet]), value: facetName(facet as Facet, name) })} onClick={() => toggleFacet(facet as Facet, name)}>{t('bank.selectedFacet', { facet: t(facetLabels[facet as Facet]), value: facetName(facet as Facet, name) })}</button>))}
            {q.trim() && <button className="btn btn-outline btn-sm" onClick={() => {setQ(''); setPage(0);}}>{t('bank.keywordFacet', { query: q })}</button>}
          </div>
          <p className="hint">{t('bank.filterLogic')}</p>
          <div className="bank-actions">
            <button className="btn btn-outline" aria-expanded={showFilters} onClick={() => setShowFilters(value => !value)}>{t('bank.adjustFilters')}</button>
            <button className="btn btn-ghost" onClick={() => {setFacets(emptyFacets); setQ(''); setPage(0);}}>{t('bank.resetFilters')}</button>
          </div>
          {showFilters && (Object.keys(facetLabels) as Facet[]).map(facet => <fieldset className="bank-facet" key={facet}>
            <legend>{t(facetLabels[facet])}</legend>
            <div className="bank-actions">{facetOptions(facet).map(([name, count]) => <label key={name}><input type="checkbox" checked={facets[facet].includes(name)} onChange={() => toggleFacet(facet, name)} /> {facetName(facet, name)} ({count})</label>)}</div>
          </fieldset>)}
        </section>
        <div className="card">
          <div className="card-head">
            <span className="card-title">
              {bankLoading ? t('bank.myQuestionsLoading') : t('bank.myQuestionCount', { count: resultCount })}
            </span>
            <BankSearch value={q} onChange={(value) => {setQ(value); setPage(0);}} />
            <PodcastSelectionBar selection={podcastSelection} />
          </div>

          {bankLoading ? <p className="empty" role="status">{t('bank.loadingMine')}</p> : apiErr ? <p role="alert">{t('bank.myQuestionsFailed', { error: apiErr })} <button className="btn btn-outline btn-sm" onClick={() => setReload(value => value + 1)}>{t('bank.retry')}</button></p> : rows.length === 0 ? (
            <div className="empty"><b>{t('bank.noMatches')}</b><p>{t('bank.tryDifferentFilters')}</p></div>
          ) : (
            rows.map((r) => (
              <BankQuestionRow key={r.id} id={r.id} title={r.title}
                metadata={<>
                    <span className="chip chip-green">{r.status || t('bank.alreadyCollected')}</span>
                    <span>{bankSourcePresentation(r).company}</span>
                    <span className="chip chip-slate">{facetName('topics', r.topic)}</span>
                    <span style={{ fontSize: 12 }}><Stars n={r.difficulty} /></span>
                    {bankSourcePresentation(r).isPrivate && <span className="chip chip-purple">{t('bank.private')}</span>}
                    <span className="chip chip-slate">{r.reviewOnly ? t('bank.fromStudyChat') : bankSourcePresentation(r).label}</span>
                    {r.tags?.slice(0, 2).map((t) => <span key={t} className="chip chip-slate">#{t}</span>)}
                </>}
                actions={<>
                  {r.reviewOnly ? <>
                    {r.studySessionId && <Link className="btn btn-outline btn-sm" to={`/study/chat/${r.studySessionId}`}>{t('bank.revisitStudyChat')}</Link>}
                    <button className="btn btn-outline btn-sm" onClick={() => openDetail(r)}>{t('bank.viewReference')}</button>
                  </> : <>
                  <button className="btn btn-outline btn-sm" disabled={!bankSourcePresentation(r).editable} title={bankSourcePresentation(r).editable?t('bank.editPersonalQuestion'):t('bank.sharedQuestionEditHelp')} onClick={() => setEditQ(r)}>{bankSourcePresentation(r).editable?t('bank.edit'):t('bank.sharedQuestionReadOnly')}</button>
                  {r.numericQuestionId != null && <button className="btn btn-outline btn-sm" onClick={() => {
                    if (r.numericQuestionId != null) setMappingQuestion({ id: r.numericQuestionId, title: r.title });
                  }}>{t('bank.retestMapping')}</button>}
                  <StudyEntryButton kind="question" id={r.numericQuestionId} bankKey={r.id}/>
                  {r.numericQuestionId != null && <PodcastQuestionAction id={r.numericQuestionId} title={r.title} selection={podcastSelection} />}
                  <button className="btn btn-outline btn-sm" disabled={practiceBusy!==null} onClick={() => startPractice(r)}>{practiceBusy===r.id?t('bank.preparingExam'):t('bank.startPractice')}</button>
                  <button className="btn btn-outline btn-sm" onClick={() => openDetail(r)}>{t('bank.viewReference')}</button>
                  <button className="btn btn-ghost btn-sm" onClick={() => setMergeQ(r)}>{t('bank.mergeDuplicates')}</button>
                  </>}
                </>}
              />
            ))
          )}
          <div className="bank-actions bank-pagination">
            <button className="btn btn-outline btn-sm" disabled={bankLoading || page === 0} onClick={() => setPage(page - 1)}>{t('bank.previousPage')}</button>
            <span className="hint">{t('bank.pageOf', { page: page + 1, total: pageCount })}</span>
            <button className="btn btn-outline btn-sm" disabled={bankLoading || page + 1 >= pageCount} onClick={() => setPage(page + 1)}>{t('bank.nextPage')}</button>
          </div>
        </div>
        </>}
      </div>

      {/* —— 题目详情(全屏最大化) —— */}
      {detail && (
        <Modal title={generalDetail ? t('bank.questionDetails', { id: detail.id }) : t('bank.viewReference')} size="xl" onClose={() => {++detailRequest.current;setDetail(null);}}
          footer={
            <>
              {generalDetail && !generalDetail.reviewOnly && <>
                <button className="btn btn-outline" disabled={!bankSourcePresentation(generalDetail).editable} title={bankSourcePresentation(generalDetail).editable?t('bank.editPersonalQuestion'):t('bank.sharedQuestionEditHelp')} onClick={() => { setDetail(null); setEditQ(generalDetail); }}>{bankSourcePresentation(generalDetail).editable?t('bank.edit'):t('bank.sharedQuestionReadOnly')}</button>
              </>}
              {!detailLoading && !detailErr && generalDetail?.reviewOnly && generalDetail.studySessionId && <Link className="btn btn-outline" to={`/study/chat/${generalDetail.studySessionId}`}>{t('bank.revisitStudyChat')}</Link>}
              {!generalDetail?.reviewOnly && <><StudyEntryButton key={detail.id} kind="question" id={detail.numericQuestionId} bankKey={detail.id}/>
              {detail.numericQuestionId != null && <PodcastQuestionAction id={detail.numericQuestionId} title={detail.title} />}
              <button className="btn btn-primary" disabled={practiceBusy!==null} onClick={() => startPractice(detail, !generalDetail)}>{practiceBusy?t('bank.preparingExam'):practiceError?.id === detail.id ? t('bank.retryPractice') : t('bank.startPractice')}</button></>}
            </>
          }>
          <div className="q-meta" style={{ marginBottom: 14 }}>
            {'domainName' in detail ? <>
              <span className="chip chip-slate">{experiencedDomainLabel(detail.domainKey, detail.domainName)}</span>
              <span className="chip chip-slate">{experiencedLevelLabel(detail)}</span>
              <span>{t('bank.experiencedSourceReadOnly', { source: t(EXPERIENCED_SOURCE_LABELS[detail.source ?? 'authored']) })}</span>
            </> : <>
              <span className="chip chip-slate">{facetName('topics', detail.topic)}</span>
              <span>{bankSourcePresentation(detail).company}</span>
              <Stars n={detail.difficulty} />
            </>}
          </div>
          <h3 className={generalDetail ? undefined : 'experienced-prompt'} style={{ fontSize: 17, fontWeight: 650, marginBottom: 14 }}>{detail.title}</h3>
          {generalDetail && <p className="hint">{generalDetail.reviewOnly ? t('bank.personalStudyQuestion') : bankSourcePresentation(generalDetail).isPrivate ? t('bank.personalQuestionSource', { source: bankSourcePresentation(generalDetail).label }):t('bank.sharedReferenceHelp')}</p>}
          {detailErr && <div role="alert" className="demo-banner demo-banner-err" style={{ marginBottom: 10 }}>{bankMessageText(detailErr, t)}</div>}
          {practiceError?.id === detail.id && <p role="alert">{t('bank.practiceFailed', { error: practiceError.message })}</p>}
          {guide ? <AnswerGuide guide={guide} beginnerExample={beginnerExample} /> : beginnerExample && <section className="teaching-guide" aria-label={t('bank.introExample')}><h3>{t('bank.showEverydayExample')}</h3><p>{beginnerExample}</p></section>}
          <p className="hint">{generalDetail ? t('bank.referenceAlreadyExposed') : t('bank.referenceExposureHelp')}</p>
          {detailLoading?<p role="status">{t('bank.readingReference')}</p>:detailErr?<button className="btn btn-outline" onClick={()=>openDetail(detail)}>{t('bank.reloadReference')}</button>:detailHtml ? (
            <>
              {detailHtml.includes('<details') && (
                <div className="qa-tools">
                  <span className="qa-tools-label">{t('bank.referenceFollowUpsHelp')}</span>
                  <button className="btn btn-ghost btn-sm" onClick={() => toggleQA(true)}>{t('bank.expandAll')}</button>
                  <button className="btn btn-ghost btn-sm" onClick={() => toggleQA(false)}>{t('bank.collapseAll')}</button>
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
          ) : !generalDetail ? <p>{t('bank.noPublishedReference')}</p> : (
            <div className="ans-empty">
              <p>{t('bank.noAnswerContent')}</p>
              <p className="hint">{t('bank.generateAnswerHelp')}</p>
              <button className="btn btn-primary" onClick={genAnswer} disabled={detailBusy}>
                {detailBusy ? t('bank.generating') : t('bank.generateDetailedAnswer')}
              </button>
            </div>
          )}
        </Modal>
      )}

      {/* —— 架构图放大查看 —— */}
      {zoomSvg && (
        <div className="svg-lightbox" onClick={() => setZoomSvg(null)}>
          <div className="svg-lightbox-inner" dangerouslySetInnerHTML={{ __html: zoomSvg }} />
          <div className="svg-lightbox-hint">{t('bank.closeDiagramHelp')}</div>
        </div>
      )}

      {mappingQuestion && <Modal title={t('bank.reviewRetestMapping')} onClose={() => setMappingQuestion(null)}>
        <h3>{mappingQuestion.title}</h3>
        <RetestMappingReview questionId={mappingQuestion.id} />
      </Modal>}
      {editQ && <BankQuestionEditor question={editQ} topics={Object.prototype.hasOwnProperty.call(TOPIC_NAME, editQ.topic) ? topics : [...topics, {code: editQ.topic, name: editQ.topic}]} onClose={() => setEditQ(null)} onSaved={() => { toast('✅', t('bank.questionSaved')); setEditQ(null); setReload(x => x + 1); }} />}

      {/* —— 合并重复 —— */}
      {mergeQ && (
        <Modal title={t('bank.mergeDuplicateVariants')} onClose={() => setMergeQ(null)}
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setMergeQ(null)}>{t('bank.cancel')}</button>
              <button className="btn btn-primary" onClick={() => { toast('🔗', t('bank.mergedAsVariant')); setMergeQ(null); }}>{t('bank.mergeAsVariant')}</button>
            </>
          }>
          <p style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.7 }}>
            {t('bank.similarQuestionFound', { code: mergeExampleId })}
            <br />
            {t('bank.mergeExampleHelp', { question: mergeExampleQuestion })}
          </p>
          <div className="field" style={{ marginTop: 16 }}>
            <label>{t('bank.canonicalTarget')}</label>
            <select className="select" defaultValue={mergeQ.id}>
              <option value={mergeQ.id}>{mergeQ.id} · {mergeQ.title}</option>
              <option value={mergeExampleId}>{mergeExampleId} · {mergeExampleTitle}</option>
            </select>
          </div>
          <div className="field">
            <label>{t('bank.retainedVariants')}</label>
            <div className="list-row" style={{ borderBottom: 'none' }}>
              <span className="chip chip-slate">{t('bank.variantIndex', { index: 1 })}</span>
              <span style={{ fontSize: 13 }}>{mergeExampleQuestion}</span>
            </div>
            <div className="list-row">
              <span className="chip chip-slate">{t('bank.variantIndex', { index: 2 })}</span>
              <span style={{ fontSize: 13 }}>{mergeQ.title}</span>
            </div>
          </div>
        </Modal>
      )}

      {/* —— 导入面经 —— */}
      {scope === 'sets' && activeSet && (!requestedSet || activeSet.id === requestedSet) && <Modal title={activeSet.title} size="lg" onClose={closeSet} footer={
        <button className="btn btn-outline" onClick={closeSet}>{t('bank.backToSets')}</button>
      }>
        <p className="hint">{t('bank.setPracticeMetadata', { name: activeSet.project_name, count: activeSet.questions.length })}</p>
        <section className="card bank-section bank-set-questions">
          <p className="hint">{t('bank.questionList')}</p>
          {activeSet.questions.map((item, index) => <article className="bank-set-question" key={item.id}>
            <h3>{String(index + 1).padStart(2, '0')}　{item.rewrite_method === 'model' || (!item.rewrite_method && item.evidence) ? t('bank.projectQuestion') : t('bank.conditionalFollowUp')}</h3>
            <p className="bank-question-text">{item.rewrite_method === 'model' ? item.title : item.title.split('\n\n').slice(1).join('\n\n') || item.title}</p>
            <StudyEntryButton kind="question" id={item.id}/>
            <details><summary>{t('bank.viewOriginalAndEvidence')}</summary>
              <p>{t('bank.originalQuestionValue', { question: item.original_title })}</p>
              {item.extracted_title !== item.original_title && <p>{t('bank.extractedQuestionValue', { question: item.extracted_title })}</p>}
              <p>{t('bank.sourceValue', { source: item.source_files.join(t('bank.listSeparator')) || t('bank.legacyNoFile') })}</p>
              {item.rewrite_method && <p>{item.rewrite_method === 'model' ? t('bank.modelRewrite') : t('bank.conditionalRewrite', { reason: t(item.rewrite_fallback_reason === 'model_unavailable' ? 'bank.rewriteModelUnavailable' : item.rewrite_fallback_reason === 'no_evidence' ? 'bank.rewriteNoEvidence' : 'bank.rewriteUnverifiable') })}</p>}
              {item.fact_refs?.length ? <ul>{item.fact_refs.map(fact => <li key={fact.ref}>{t('bank.factAttribution', { provenance: t(fact.provenance === 'user_statement' ? 'bank.evidenceSelfReported' : 'bank.repoObservation'), label: fact.label, revision: fact.revision ? ` · ${fact.revision.slice(0, 12)}` : '', fact: fact.text })}</li>)}</ul> : <p>{t('bank.projectEvidence', { evidence: item.evidence ? `${item.evidence.label} · ${item.evidence.revision}` : t('bank.noEvidence') })}</p>}
              <button className="btn btn-outline btn-sm" disabled={setBusy || promoted.includes(item.id)} onClick={() => promoteOriginal(item)}>{promoted.includes(item.id) ? t('bank.generalCollectionHandled') : t('bank.saveOriginalAsGeneral')}</button>
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
          } catch (error) { toast('⚠️', t('bank.openImportFailed', { error: (error as Error).message })); }
          finally { setSetBusy(false); }
        }}>{t('bank.reviewAndRegenerate')}</button>}
        <section className="card bank-section">
          <p className="hint">{t('bank.prepareInterview')}</p>
          <h2>{t('bank.examFromSet')}</h2>
          <p className="hint">{t('bank.examSnapshotHelp')}</p>
          <div className="bank-actions">
            <label>{t('bank.duration')} <select className="inp" value={duration} onChange={event => setDuration(Number(event.target.value))}>
              {[15, 30, 45, 60].map(minutes => <option key={minutes} value={minutes}>{t('bank.durationMinutes', { count: minutes })}</option>)}
            </select></label>
            <button className="btn btn-primary" disabled={paperBusy} onClick={startPaper}>{paperBusy ? t('bank.buildingExam') : t('bank.generateOpenExam')}</button>
          </div>
        </section>
        {setError && <p className="hint" role="alert">{bankMessageText(setError, t)}</p>}
      </Modal>}

      {importOpen && (
        <Modal
          title={stage === 'upload' ? t('bank.importFromScreenshots') : t('bank.reviewOriginalAndRewrite')}
          size="lg"
          onClose={closeImport}
          footer={
            stage === 'upload' ? (
              <>
                <button className="btn btn-outline" onClick={closeImport}>{t('bank.cancel')}</button>
                <button className="btn btn-primary" disabled={uploading} onClick={startImport}>{uploading ? t('bank.uploading') : t('bank.extractQuestions')}</button>
              </>
            ) : stage === 'review' ? (
              <>
                <button className="btn btn-outline" onClick={closeImport}>{t('bank.handleLater')}</button>
                <button className="btn btn-outline" onClick={() => {setImportOpen(false); selectScope('pending');}}>{t('bank.openReviewQueue')}</button>
                <button className="btn btn-primary" onClick={createSet} disabled={setBusy || !review || !projectId || !selectedItems.length || !setTitle.trim()}>
                  {setBusy ? t('bank.checkingProjectEvidence') : t('bank.rewriteAndSaveSet')}
                </button>
              </>
            ) : undefined
          }
        >
          {stage === 'upload' && (
            <>
              <BankUploadPicker
                multiple
                accept=".pdf,.docx,.md,.markdown,.txt,.text,.jpg,.jpeg,.png,.webp,application/pdf,text/markdown,text/plain,image/jpeg,image/png,image/webp"
                description={t('bank.fileFormats')}
                onFiles={addFiles}
              />

              {files.length > 0 && (
                <div className="file-list">
                  {files.map((f, i) => (
                    <div className="file-item" key={`${f.name}-${f.size}-${i}`}>
                      <span className="file-ic">{f.name.endsWith('.pdf') ? '📕' : f.name.endsWith('.docx') ? '📘' : /\.(jpe?g|png)$/i.test(f.name) ? '🖼️' : '📄'}</span>
                      <span className="file-name">{i + 1}. {f.name}</span>
                      <span className="file-size">{f.size < 1024 ? `${f.size} B` : `${(f.size / 1024).toFixed(0)} KB`}</span>
                      <button className="btn btn-ghost btn-sm" disabled={i === 0} aria-label={t('bank.moveFileUp', { name: f.name })} onClick={() => moveFile(i, -1)}>↑</button>
                      <button className="btn btn-ghost btn-sm" disabled={i === files.length - 1} aria-label={t('bank.moveFileDown', { name: f.name })} onClick={() => moveFile(i, 1)}>↓</button>
                      <button className="file-x" title={t('bank.remove')} onClick={(e) => { e.stopPropagation(); setFiles((prev) => prev.filter((_, j) => j !== i)); }}>×</button>
                    </div>
                  ))}
                </div>
              )}

              {importErr.length > 0 && <div className="demo-banner demo-banner-err" style={{ marginTop: 10 }}>{importErr.map(message => bankMessageText(message, t)).join(t('bank.clauseSeparator'))}</div>}

              <div className="form-row" style={{ marginTop: 12 }}>
                <div className="field">
                  <label>{t('bank.optionalCompany')}</label>
                  <input className="inp" placeholder={t('bank.companyExample')} value={company} onChange={(e) => setCompany(e.target.value)} />
                </div>
                <div className="field">
                  <label>{t('bank.optionalLevel')}</label>
                  <input className="inp" placeholder={t('bank.levelExample')} value={level} onChange={(e) => setLevel(e.target.value)} />
                </div>
              </div>

              <div className="field" style={{ marginTop: 12 }}>
                <label>{t('bank.pasteAlongsideFiles')}</label>
                <textarea
                  className="textarea"
                  style={{ minHeight: 90 }}
                  placeholder={t('bank.pasteInterviewPlaceholder')}
                  value={pasteText}
                  onChange={(e) => setPasteText(e.target.value)}
                />
              </div>
              <div className="hint">{t('bank.importPrivacyHelp')}</div>
            </>
          )}

          {stage === 'review' && (
            <div className="rev-split">
              {/* 左: 原文预览 */}
              <div className="rev-src">
                <div className="rev-pane-title">{t('bank.originalPreview')}</div>
                {(review?.meta?.files?.length
                  ? review.meta.files.map((f) => ({ name: f.name, sub: f.ok ? t('bank.parsedFileMetadata', { count: String(f.chars), format: f.suffix || t('bank.textFormat') }) : (f.error || t('bank.parseFailed')), ok: f.ok }))
                  : review ? [] : files.map((f) => ({ name: f.name, sub: `${(f.size / 1024).toFixed(1)} KB`, ok: true }))
                ).map((f, i) => (
                  <div className={`rev-src-file ${f.ok ? '' : 'bad'}`} key={`${f.name}-${i}`}>
                    <b>{f.ok ? '📄' : '⚠️'} {f.name}</b>
                    <span className="hint">{f.sub}</span>
                  </div>
                ))}
                <ImportSourceReview review={review?.meta?.source_review} />
                {originalSource && (
                  <div className="rev-src-text">{originalSource}</div>
                )}
                {(review?.meta?.company || review?.meta?.level) && (
                  <div className="hint" style={{ marginTop: 10 }}>
                    {t('bank.sourceMetadata', { company: review?.meta?.company || '—', level: review?.meta?.level || '—' })}
                  </div>
                )}
                <div className="hint" style={{ marginTop: 10 }}>
                  {t('bank.privateReviewHelp')}<b>{t('bank.approvedBeforeExam')}</b>{t('bank.sentenceEnd')}
                </div>
              </div>

              {/* 右: 提取结果 */}
              <div className="rev-items">
                {review ? (
                  <>
                    <div className="hint" style={{ marginBottom: 12 }}>
                      {t('bank.extractedSummary', { code: review.short_code, count: review.items.length })}
                    </div>
                    <div className="form-row">
                      <label className="field">{t('bank.setName')}<input className="inp" maxLength={120} value={setTitle} onChange={event => setSetTitle(event.target.value)} /></label>
                      <label className="field">{t('bank.myProject')}<select className="inp" value={projectId} onChange={event => setProjectId(event.target.value)}>
                        <option value="">{projects.length ? t('bank.chooseProject') : t('bank.noProjectsToChoose')}</option>
                        {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
                      </select></label>
                    </div>
                    {setError && <p role="alert" className="hint">{bankMessageText(setError, t)}</p>}
                    {review.meta?.files?.filter((f) => !f.ok).map((f) => (
                      <div className="demo-banner demo-banner-err" key={f.name} style={{ marginBottom: 8 }}>
                        {t('bank.parsedFileError', { name: f.name, error: f.error || t('bank.parseFailed') })}
                      </div>
                    ))}
                    {review.items.map((it) => (
                      <div className="rev-item" key={it.id}>
                        <div className="q-body">
                          <label className="hint"><input type="checkbox" disabled={it.status === 'REJECTED'} checked={selectedItems.includes(it.id)} onChange={() => setSelectedItems(previous => previous.includes(it.id) ? previous.filter(id => id !== it.id) : [...previous, it.id])} /> {t('bank.selectOriginalQuestion', { source: it.generation_meta?.source_files?.join(t('bank.listSeparator')) || t('bank.importRecord'), method: it.generation_meta?.extraction_method ? ` · ${t(it.generation_meta.extraction_method === 'model' ? 'bank.modelExtraction' : 'bank.ruleExtraction')}` : '' })}</label>
                          <textarea className="textarea bank-original-input" aria-label={t('bank.correctOriginalQuestion', { id: it.id })} maxLength={1000} value={edits[it.id] ?? it.title} onChange={event => setEdits(previous => ({...previous, [it.id]:event.target.value}))} />
                          <div className="q-meta">
                            <span className={`chip ${it.extract_confidence >= 0.5 ? 'chip-green' : 'chip-amber'}`}>
                              {t('bank.confidence', { percent: Math.round(it.extract_confidence * 100) })}
                            </span>
                            <span className="chip chip-slate">{workshopSkillLabel(it.suggested_skill_key)}</span>
                            {it.has_answer
                              ? (it.answer_source === 'llm'
                                  ? <span className="chip chip-purple">{t('bank.aiDraft')}</span>
                                  : <span className="chip chip-green">{t('bank.ruleDraft')}</span>)
                              : <span className="chip chip-amber">{t('bank.noAnswer')}</span>}
                            {it.is_duplicate_of && <span className="chip chip-red">{t('bank.duplicateVariant')}</span>}
                          </div>
                        </div>
                        <div className="q-actions">
                          <button
                            className="btn btn-outline btn-sm"
                            disabled={!it.draft_answer}
                            onClick={() => setOpenAns((s) => ({ ...s, [it.id]: !s[it.id] }))}
                          >
                            {openAns[it.id] ? t('bank.hideAnswer') : t('bank.showAnswer')}
                          </button>
                        </div>
                        {openAns[it.id] && it.draft_answer && (
                          <div className="ans-preview bank-answer" dangerouslySetInnerHTML={{ __html: sanitizeReviewAnswer(it.draft_answer) }} />
                        )}
                      </div>
                    ))}
                  </>
                ) : (
                  <div className="empty"><b>{t('bank.nothingToReview')}</b><p>{t('bank.importBeforeReview')}</p></div>
                )}
              </div>
            </div>
          )}

        </Modal>
      )}
    </div>
  );
}
