import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError, ImportReview } from '../api/client';
import { messageOf, SKILLS } from '../lib/questionWorkshop';

type ReviewItem = ImportReview['items'][number] & {
  question_text?: string | null;
  original_question?: string | null;
  has_reviewable_answer?: boolean;
  review_revision?: number;
  review_state?: 'idle' | 'running' | 'failed';
  review_feedback?: string | null;
  review_error?: string | null;
};
type ReviewImport = Omit<ImportReview, 'items'> & { items: ReviewItem[] };
type ReviewRow = { key: string; batch: ReviewImport; item: ReviewItem };
type ReviewAction = 'approve' | 'revise' | 'reject';
type FeedbackAction = Exclude<ReviewAction, 'approve'>;
type ReviewRequest = { action: ReviewAction; feedback?: string; request_id: string; revision: number };
type ReviewResult = {
  item: ReviewItem;
  outcome: 'approved' | 'merged' | 'rejected' | 'revised' | 'running' | 'failed';
};
type ReviewOperation =
  | { kind: 'submitting'; request: ReviewRequest }
  | { kind: 'uncertain'; request: ReviewRequest; error: string };
type QueueLoad =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; imports: ReviewImport[]; refreshError?: string };

const actionLabels: Record<ReviewAction, string> = {
  approve: '批准入库', revise: '按反馈重生成', reject: '驳回',
};
const outcomeLabels = {
  approved: '已批准入库', merged: '已合并至已有题目', rejected: '已驳回并保存反馈', revised: '已更新草稿，等待审核',
};
const feedbackLabels: Record<FeedbackAction, string> = { revise: '修改意见', reject: '驳回原因' };
const skillNames = Object.fromEntries(SKILLS.map(skill => [skill.key, skill.name]));

const answerTags = new Set('a b blockquote br caption code dd del details div dl dt em figcaption figure h1 h2 h3 h4 h5 h6 hr i kbd li mark ol p pre s samp small span strong sub summary sup table tbody td tfoot th thead tr u ul var'.split(' '));
const discardTags = new Set('audio embed iframe img input link math noscript object script select style svg template textarea video'.split(' '));
const answerClasses = new Set('ans-context-missing ans-elevator ans-elevator-label ans-foot ans-rubric ans-script ans-script-label ans-terms ans-tldr ans-tldr-label an b-ana b-det b-fast b-fol b-term badge block company fu-a fu-a-missing fu-item fu-list fu-q note qa qa-h qa-no qa-t qu tag term-item terms-grid'.split(' '));

export function sanitizeReviewAnswer(html: string): string {
  if (typeof document === 'undefined') return html.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const template = document.createElement('template');
  template.innerHTML = html;
  for (const element of Array.from(template.content.querySelectorAll('*'))) {
    const tag = element.localName;
    if (element.namespaceURI !== 'http://www.w3.org/1999/xhtml' || discardTags.has(tag)) {
      element.remove();
      continue;
    }
    if (!answerTags.has(tag)) {
      element.replaceWith(...Array.from(element.childNodes));
      continue;
    }
    for (const attribute of Array.from(element.attributes)) {
      const { name, value } = attribute;
      if (name === 'class') {
        const classes = value.split(/\s+/).filter(part => answerClasses.has(part));
        if (classes.length) element.setAttribute('class', classes.join(' '));
        else element.removeAttribute(name);
      } else if (tag === 'a' && name === 'href' && /^https?:\/\//i.test(value.trim())) {
        element.setAttribute('target', '_blank');
        element.setAttribute('rel', 'noopener noreferrer');
      } else if (!((['td', 'th'].includes(tag) && ['colspan', 'rowspan'].includes(name) || tag === 'ol' && name === 'start') && /^\d{1,3}$/.test(value))) {
        element.removeAttribute(name);
      }
    }
    if (tag === 'details') element.setAttribute('open', '');
  }
  return template.innerHTML;
}

function readRecord(value: unknown): Record<string, unknown> | undefined {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return undefined;
  return Object.fromEntries(Object.entries(value));
}

function readText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function readRubric(value: unknown) {
  const rubric = readRecord(value);
  const rawPoints = rubric?.points ?? rubric?.criteria ?? value;
  const points = Array.isArray(rawPoints) ? rawPoints.flatMap((raw: unknown) => {
    const point = readRecord(raw);
    const title = readText(point?.title ?? point?.label ?? raw);
    const detail = readText(point?.detail ?? point?.description);
    const keywords = Array.isArray(point?.keywords) ? point.keywords.map(readText).filter(Boolean) : [];
    return title || detail ? [{ title, detail, keywords }] : [];
  }) : [];
  const maxPoints = rubric?.max_points;
  return { points, maxPoints: typeof maxPoints === 'number' ? maxPoints : null };
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    return '当前工作空间没有访问权限，请重新登录或切换工作空间后重试。';
  }
  return messageOf(error);
}

function reviewProgress(row: ReviewRow, operation?: ReviewOperation) {
  if (operation?.kind === 'uncertain') return { text: '结果待确认', color: 'amber' };
  if (operation?.kind === 'submitting') {
    return { text: operation.request.action === 'revise' ? '重生成中' : '正在提交', color: 'blue' };
  }
  if (row.item.review_state === 'running') return { text: '重生成中', color: 'blue' };
  if (row.item.review_state === 'failed') return { text: '重生成失败', color: 'red' };
  return { text: '待审核', color: 'amber' };
}

interface Props {
  requestedKey?: string | null;
  visible: boolean;
  reload: number;
  topicNames: Record<string, string>;
  onCountChange: (count: number | null) => void;
  onLibraryChange: () => void;
  onOpenImport: (batch: ImportReview) => void;
  onUpload: () => void;
}

export default function BankReviewQueue({ requestedKey, visible, reload, topicNames, onCountChange, onLibraryChange, onOpenImport, onUpload }: Props) {
  const [queue, setQueue] = useState<QueueLoad>({ kind: 'loading' });
  const [refreshing, setRefreshing] = useState(false);
  const [search, setSearch] = useState('');
  const [selection, setSelection] = useState<string | null>(null);
  const [linkedKey, setLinkedKey] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [feedback, setFeedback] = useState<{ key: string; action: FeedbackAction; text: string } | null>(null);
  const [operations, setOperations] = useState<Record<string, ReviewOperation>>({});
  const [actionError, setActionError] = useState<{ key: string; message: string } | null>(null);
  const [notice, setNotice] = useState('');
  const fetchSequence = useRef(0);
  const submitting = useRef(new Set<string>());
  const detailBody = useRef<HTMLDivElement>(null);
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const pendingSearch = useRef<HTMLInputElement>(null);
  const focusDetail = useRef(false);
  const feedbackInput = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!requestedKey) return;
    setSelection(requestedKey);
    setLinkedKey(requestedKey);
    setDetailOpen(true);
    setSearch('');
  }, [requestedKey]);

  const refresh = useCallback(async () => {
    const sequence = ++fetchSequence.current;
    setRefreshing(true);
    try {
      const result = await api.get<{ imports: ReviewImport[] }>('/bank/imports/pending');
      if (!Array.isArray(result?.imports) || result.imports.some(batch => !Array.isArray(batch?.items))) {
        throw new Error('待审核队列返回的格式无法读取。');
      }
      if (sequence === fetchSequence.current) setQueue({ kind: 'ready', imports: result.imports });
    } catch (error) {
      if (sequence !== fetchSequence.current) return;
      const message = errorMessage(error);
      const denied = error instanceof ApiError && (error.status === 401 || error.status === 403);
      setQueue(previous => previous.kind === 'ready' && !denied
        ? { ...previous, refreshError: message }
        : { kind: 'error', message });
    } finally {
      if (sequence === fetchSequence.current) setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    return () => { ++fetchSequence.current; };
  }, [refresh, reload, visible]);

  const rows = useMemo<ReviewRow[]>(() => queue.kind === 'ready' ? queue.imports.flatMap(batch => batch.items
    .filter(item => item.status === 'DRAFT')
    .map(item => ({ key: `${batch.id}-${item.id}`, batch, item }))) : [], [queue]);
  const runningRows = rows.some(row => row.item.review_state === 'running');
  useEffect(() => {
    if (!visible || !runningRows || refreshing) return;
    const timer = window.setTimeout(refresh, 2400);
    return () => window.clearTimeout(timer);
  }, [visible, runningRows, refreshing, refresh]);
  useEffect(() => {
    onCountChange(queue.kind === 'ready' ? rows.length : null);
  }, [queue.kind, rows.length, onCountChange]);

  const term = search.trim().toLowerCase();
  const filtered = rows.filter(({ item, batch }) => [item.title, item.question_text, item.original_question,
    readRecord(item.generation_meta?.item)?.original_question, item.suggested_skill_key, skillNames[item.suggested_skill_key], topicNames[item.suggested_skill_key],
    batch.short_code, batch.meta?.company].some(value => readText(value).toLowerCase().includes(term)));
  const missingLinkedItem = !!linkedKey && queue.kind === 'ready' && !rows.some(row => row.key === linkedKey);
  const selected = missingLinkedItem ? undefined : filtered.find(row => row.key === selection) ?? filtered[0];
  const selectedKey = selected?.key;
  const activeKey = useRef<string>();
  const pendingFeedback = feedback?.key === selectedKey ? feedback : null;
  useEffect(() => {
    activeKey.current = selectedKey;
    if (detailBody.current) detailBody.current.scrollTop = 0;
    if (visible && focusDetail.current) {
      detailHeading.current?.focus();
      focusDetail.current = false;
    }
  }, [selectedKey, detailOpen, visible]);
  useEffect(() => { if (pendingFeedback) feedbackInput.current?.focus(); }, [pendingFeedback?.action, pendingFeedback?.key]);

  const select = (row: ReviewRow) => {
    focusDetail.current = row.key !== selectedKey || !detailOpen;
    setLinkedKey(null);
    setSelection(row.key);
    setDetailOpen(true);
  };
  const backToQueue = () => {
    setDetailOpen(false);
    requestAnimationFrame(() => pendingSearch.current?.focus());
  };

  const updateItem = (row: ReviewRow, item: ReviewItem) => {
    ++fetchSequence.current;
    setRefreshing(false);
    setQueue(previous => previous.kind === 'ready' ? {
      ...previous,
      imports: previous.imports.map(batch => batch.id === row.batch.id
        ? { ...batch, items: batch.items.map(current => current.id === item.id ? item : current) }
        : batch),
    } : previous);
  };

  const submit = async (row: ReviewRow, request: ReviewRequest) => {
    if (submitting.current.has(row.key)) return;
    submitting.current.add(row.key);
    setOperations(previous => ({ ...previous, [row.key]: { kind: 'submitting', request } }));
    setActionError(previous => previous?.key === row.key ? null : previous);
    setNotice('');
    let uncertain = false;
    try {
      const result = await api.post<ReviewResult>(`/bank/imports/${row.batch.id}/items/${row.item.id}/review`, request);
      if (result.item?.id !== row.item.id || !['approved', 'merged', 'rejected', 'revised', 'running', 'failed'].includes(result.outcome)) {
        throw new Error('服务返回的审核结果无法确认。');
      }
      updateItem(row, result.item);
      setFeedback(previous => previous?.key === row.key ? null : previous);
      if (result.outcome === 'running' || result.outcome === 'failed') return;
      setNotice(outcomeLabels[result.outcome]);
      if (result.outcome !== 'revised') {
        const index = filtered.findIndex(candidate => candidate.key === row.key);
        const next = filtered[index + 1] ?? filtered[index - 1];
        if (activeKey.current === row.key) focusDetail.current = true;
        setSelection(previous => previous === row.key || previous === null ? next?.key ?? null : previous);
        onLibraryChange();
      }
    } catch (error) {
      const message = errorMessage(error);
      if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408) {
        setActionError({ key: row.key, message });
        if ([401, 403, 404, 409].includes(error.status)) void refresh();
      } else {
        uncertain = true;
        setOperations(previous => ({ ...previous, [row.key]: { kind: 'uncertain', request, error: message } }));
      }
    } finally {
      submitting.current.delete(row.key);
      if (!uncertain) setOperations(previous => {
        const next = { ...previous };
        delete next[row.key];
        return next;
      });
    }
  };

  const startReview = (row: ReviewRow, action: ReviewAction, text?: string) => void submit(row, {
    action,
    feedback: text?.trim(),
    request_id: crypto.randomUUID(),
    revision: row.item.review_revision ?? 0,
  });

  const operation = selected && operations[selected.key];
  const revising = selected?.item.review_state === 'running' || operation?.kind === 'submitting' && operation.request.action === 'revise';
  const failed = selected?.item.review_state === 'failed';
  const generatedItem = readRecord(selected?.item.generation_meta?.item);
  const questionText = selected?.item.question_text?.trim() || selected?.item.title;
  const originalQuestion = selected?.item.original_question?.trim() || readText(generatedItem?.original_question);
  const generationTaskId = selected?.batch.meta?.generation_task_id ?? selected?.item.generation_meta?.task_id;
  const answer = selected?.item.draft_answer;
  const answerHtml = useMemo(() => answer?.trim() ? sanitizeReviewAnswer(answer) : '', [answer]);
  const rubric = readRubric(selected?.item.draft_rubric);
  const canApproveContent = selected?.item.has_reviewable_answer ?? rubric.points.some(point => point.title && point.detail);
  const approvalReason = generatedItem?.question_mode === 'needs_context' ? '补齐项目资料后才能批准。'
    : !answerHtml || !canApproveContent ? '草稿需要完整的答案和评分要点才能批准。' : '';

  return <div className="bank-review" hidden={!visible}>
    {notice && <p className="bank-review-notice" role="status">{notice}</p>}
    {queue.kind === 'loading' && <section className="card bank-review-empty" role="status">正在加载待审核题目…</section>}
    {queue.kind === 'error' && <section className="card bank-review-empty" role="alert">
      <h2>无法加载待审核队列</h2><p>{queue.message}</p>
      <button className="btn btn-outline" onClick={refresh} disabled={refreshing}>{refreshing ? '加载中…' : '重新加载'}</button>
    </section>}
    {queue.kind === 'ready' && queue.refreshError && <div className="bank-review-warning" role="alert">
      <p>刷新队列失败。{queue.refreshError}</p><button className="btn btn-outline btn-sm" onClick={refresh} disabled={refreshing}>重试刷新</button>
    </div>}
    {queue.kind === 'ready' && rows.length === 0 && <section className="card bank-review-empty">
      <h2>没有待审核的题目</h2><p>面经导入与补题工作台的草稿会出现在这里。</p>
      <button className="btn btn-outline" onClick={onUpload}>导入面经</button>
    </section>}
    {queue.kind === 'ready' && rows.length > 0 && <div className="bank-review-layout" data-detail-open={detailOpen && !!selected}>
      <section className="card bank-review-list" aria-labelledby="bank-review-list-title">
        <div className="bank-review-list-head">
          <div className="bank-review-list-title"><h2 id="bank-review-list-title">待审核题目</h2><span className="chip chip-slate">{rows.length} 题</span></div>
          <input ref={pendingSearch} className="inp" type="search" aria-label="搜索待审核题目" placeholder="搜索题目、批次或技能" value={search} onChange={event => setSearch(event.target.value)} />
        </div>
        {filtered.length ? <ul className="bank-review-rows">{filtered.map(row => {
          const status = reviewProgress(row, operations[row.key]);
          return <li key={row.key}><button className="bank-review-row" aria-current={row.key === selectedKey ? true : undefined} onClick={() => select(row)}>
            <span className="bank-review-row-meta"><span>{row.batch.short_code}</span><span className={`chip chip-${status.color}`}>{status.text}</span></span>
            <span className="bank-review-row-title">{row.item.title}</span>
            <span className="bank-review-row-topic">{skillNames[row.item.suggested_skill_key] ?? topicNames[row.item.suggested_skill_key] ?? row.item.suggested_skill_key}</span>
          </button></li>;
        })}</ul> : <p className="bank-review-no-matches" role="status">没有匹配的待审核题目</p>}
      </section>
      <article className="card bank-review-detail" aria-label="待审核题目详情">
        {selected ? <>
          <div className="bank-review-detail-body" ref={detailBody}>
            <header className="bank-review-detail-head">
              <button className="btn btn-ghost btn-sm bank-review-back" onClick={backToQueue}>← 返回待审核列表</button>
              <div className="bank-review-meta"><span className={`chip chip-${reviewProgress(selected, operation).color}`}>{reviewProgress(selected, operation).text}</span><span>{selected.batch.short_code}</span><span>{skillNames[selected.item.suggested_skill_key] ?? topicNames[selected.item.suggested_skill_key] ?? selected.item.suggested_skill_key}</span></div>
              <h2 ref={detailHeading} tabIndex={-1}>{questionText}</h2>
              {originalQuestion && originalQuestion !== questionText?.trim() && <div className="bank-review-original"><h3>原始面经问题</h3><p>{originalQuestion}</p></div>}
              <dl className="bank-review-facts">
                {selected.batch.meta?.company && <div><dt>公司</dt><dd>{selected.batch.meta.company}</dd></div>}
                {selected.batch.meta?.level && <div><dt>级别</dt><dd>{selected.batch.meta.level}</dd></div>}
                {!generationTaskId && Number.isFinite(selected.item.extract_confidence) && <div><dt>提取置信度</dt><dd>{Math.round(selected.item.extract_confidence * 100)}%</dd></div>}
                <div><dt>来源</dt><dd>{generationTaskId ? '补题工作台' : '面经导入'}</dd></div>
                {!!selected.item.review_revision && <div><dt>审核版本</dt><dd>{selected.item.review_revision}</dd></div>}
                {!!selected.item.generation_meta?.source_files?.length && <div><dt>来源文件</dt><dd>{selected.item.generation_meta.source_files.join('、')}</dd></div>}
              </dl>
              {selected.item.is_duplicate_of != null && <p className="bank-review-duplicate">此题可能与已有题目重复，批准时可能合并。</p>}
              {generationTaskId ? <Link className="link" to={`/bank/workshop?task=${generationTaskId}`}>打开补题工作台批次</Link>
                : <button className="btn btn-ghost btn-sm bank-review-source" onClick={() => onOpenImport(selected.batch)}>核对导入原问并整理面经集</button>}
            </header>
            {revising && <p className="bank-review-notice" role="status">正在按反馈重生成。当前显示原草稿。</p>}
            {failed && !revising && <div className="bank-review-warning" role="alert"><p>重生成失败。{selected.item.review_error || '原草稿与反馈仍已保留。'}</p></div>}
            {!pendingFeedback && selected.item.review_feedback && <section className="bank-review-original"><h3>最近的审核反馈</h3><p>{selected.item.review_feedback}</p></section>}
            <section className="bank-review-reading" aria-label="答案草稿">
              <h3>{revising || failed ? '答案草稿 · 原稿' : '答案草稿'}</h3>
              {answerHtml ? <div className="bank-answer bank-review-answer" dangerouslySetInnerHTML={{ __html: answerHtml }} /> : <p className="hint">这道题还没有答案草稿。</p>}
            </section>
            <section className="bank-review-reading" aria-label="评分要点">
              <div className="bank-review-rubric-head"><h3>评分要点</h3>{rubric.maxPoints !== null && <span className="hint">满分 {rubric.maxPoints}</span>}</div>
              {rubric.points.length ? <ul className="bank-review-rubric">{rubric.points.map((point, index) => <li key={index}>
                {point.title && <strong>{point.title}</strong>}{point.detail && <p>{point.detail}</p>}
                {point.keywords.length > 0 && <div className="bank-review-keywords">{point.keywords.map((keyword, position) => <span className="chip chip-slate" key={position}>{keyword}</span>)}</div>}
              </li>)}</ul> : <p className="hint">这道题还没有评分要点。</p>}
            </section>
          </div>
          <footer className="bank-review-actions">
            {actionError && actionError.key === selectedKey && <p className="bank-review-action-error" role="alert">操作失败。{actionError.message}</p>}
            {operation?.kind === 'uncertain' && <div className="bank-review-warning" role="alert"><p>无法确认操作结果。{operation.error}</p>
              <button className="btn btn-outline btn-sm" onClick={() => void submit(selected, operation.request)}>重试原操作</button>
            </div>}
            {pendingFeedback && <form className="bank-review-feedback" onSubmit={event => {
              event.preventDefault();
              if (pendingFeedback.text.trim() && !operation && !revising) startReview(selected, pendingFeedback.action, pendingFeedback.text);
            }}>
              <label htmlFor="bank-review-feedback">{feedbackLabels[pendingFeedback.action]} <span className="hint">必填</span></label>
              <textarea ref={feedbackInput} id="bank-review-feedback" className="textarea" rows={3} required maxLength={1000}
                value={pendingFeedback.text} disabled={!!operation || revising}
                onChange={event => setFeedback({ ...pendingFeedback, text: event.target.value })} />
              <p className="hint">反馈会保存在记忆中心，供同技能的题库补题参考。</p>
              <div className="bank-review-action-row">
                <button type="button" className="btn btn-ghost" disabled={!!operation || revising} onClick={() => setFeedback(null)}>取消</button>
                <button className={`btn ${pendingFeedback.action === 'reject' ? 'bank-review-reject' : 'btn-primary'}`} disabled={!pendingFeedback.text.trim() || !!operation || revising}>
                  {operation?.kind === 'submitting' ? '提交中…' : pendingFeedback.action === 'reject' ? '确认驳回' : failed ? '重试生成' : '提交反馈并重生成'}
                </button>
              </div>
            </form>}
            {!pendingFeedback && <>
              {approvalReason && <p className="hint">{approvalReason}</p>}
              <div className="bank-review-action-row">
                <button className="btn btn-primary" disabled={!!approvalReason || !!operation || revising} onClick={() => startReview(selected, 'approve')}>
                  {operation?.kind === 'submitting' && operation.request.action === 'approve' ? '批准中…' : actionLabels.approve}
                </button>
                <button className="btn btn-outline" disabled={!!operation || revising} onClick={() => setFeedback({ key: selected.key, action: 'revise', text: failed ? selected.item.review_feedback ?? '' : '' })}>{actionLabels.revise}</button>
                <button className="btn bank-review-reject" disabled={!!operation || revising} onClick={() => setFeedback({ key: selected.key, action: 'reject', text: '' })}>{actionLabels.reject}</button>
              </div>
            </>}
          </footer>
        </> : <div className="bank-review-empty"><p>{missingLinkedItem ? '链接中的题目已不在待审核队列。请从列表选择其他题目。' : '搜索并选择要审核的题目。'}</p></div>}
      </article>
    </div>}
  </div>;
}
