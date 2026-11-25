import { t as translate, useT } from '../i18n';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError, ImportReview } from '../api/client';
import { bankMessageText, messageOf, workshopSkillLabel, type BankMessage } from '../lib/questionWorkshop';

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
  | { kind: 'uncertain'; request: ReviewRequest; error: BankMessage };
type QueueLoad =
  | { kind: 'loading' }
  | { kind: 'error'; message: BankMessage }
  | { kind: 'ready'; imports: ReviewImport[]; refreshError?: BankMessage };

const actionLabels: Record<ReviewAction, string> = {
  approve: 'bank.approveToBank', revise: 'bank.regenerateFromFeedback', reject: 'bank.reject',
};
const outcomeLabels = {
  approved: 'bank.outcomeApproved', merged: 'bank.outcomeMerged', rejected: 'bank.outcomeRejected', revised: 'bank.outcomeRevised',
};
const feedbackLabels: Record<FeedbackAction, string> = { revise: 'bank.revisionFeedback', reject: 'bank.rejectionReason' };

const answerTags = new Set('a b blockquote br caption code dd del details div dl dt em figcaption figure h1 h2 h3 h4 h5 h6 hr i kbd li mark ol p pre s samp small span strong sub summary sup table tbody td tfoot th thead tr u ul var'.split(' '));
const discardTags = new Set('audio embed iframe img input link math noscript object script select style svg template textarea video'.split(' '));
const answerClasses = new Set('answer-flow ans-context-missing ans-elevator ans-elevator-label ans-foot ans-rubric ans-script ans-script-label ans-terms ans-tldr ans-tldr-label an b-ana b-det b-fast b-fol b-term badge block company fu-a fu-a-missing fu-item fu-list fu-q note qa qa-h qa-no qa-t qu tag term-item terms-grid'.split(' '));

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

function errorMessage(error: unknown): BankMessage {
  if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
    return { key: 'bank.reviewPermissionError' };
  }
  return messageOf(error);
}

function reviewProgress(row: ReviewRow, operation?: ReviewOperation) {
  if (operation?.kind === 'uncertain') return { text: translate('bank.reviewUnknownResult'), color: 'amber' };
  if (operation?.kind === 'submitting') {
    return { text: operation.request.action === 'revise' ? translate('bank.regenerating') : translate('bank.submitting'), color: 'blue' };
  }
  if (row.item.review_state === 'running') return { text: translate('bank.regenerating'), color: 'blue' };
  if (row.item.review_state === 'failed') return { text: translate('bank.regenerationFailed'), color: 'red' };
  return { text: translate('bank.pendingReview'), color: 'amber' };
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
  const t = useT();
  const [queue, setQueue] = useState<QueueLoad>({ kind: 'loading' });
  const [refreshing, setRefreshing] = useState(false);
  const [search, setSearch] = useState('');
  const [selection, setSelection] = useState<string | null>(null);
  const [linkedKey, setLinkedKey] = useState<string | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [feedback, setFeedback] = useState<{ key: string; action: FeedbackAction; text: string } | null>(null);
  const [operations, setOperations] = useState<Record<string, ReviewOperation>>({});
  const [actionError, setActionError] = useState<{ key: string; message: BankMessage } | null>(null);
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
        throw new Error(translate('bank.reviewResponseInvalid'));
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
  const skillName = (key: string) => workshopSkillLabel(key, topicNames[key] ?? key);
  const filtered = rows.filter(({ item, batch }) => [item.title, item.question_text, item.original_question,
    readRecord(item.generation_meta?.item)?.original_question, item.suggested_skill_key, skillName(item.suggested_skill_key), topicNames[item.suggested_skill_key],
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
        throw new Error(translate('bank.reviewOutcomeInvalid'));
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
  const approvalReason = generatedItem?.question_mode === 'needs_context' ? t('bank.reviewNeedsProject')
    : !answerHtml || !canApproveContent ? t('bank.reviewNeedsAnswerRubric') : '';

  return <div className="bank-review" hidden={!visible}>
    {notice && <p className="bank-review-notice" role="status">{t(notice)}</p>}
    {queue.kind === 'loading' && <section className="card bank-review-empty" role="status">{t('bank.loadingReviewQueue')}</section>}
    {queue.kind === 'error' && <section className="card bank-review-empty" role="alert">
      <h2>{t('bank.loadQueueFailed')}</h2><p>{bankMessageText(queue.message, t)}</p>
      <button className="btn btn-outline" onClick={refresh} disabled={refreshing}>{refreshing ? t('bank.loading') : t('bank.reload')}</button>
    </section>}
    {queue.kind === 'ready' && queue.refreshError && <div className="bank-review-warning" role="alert">
      <p>{t('bank.refreshQueueFailed')}{bankMessageText(queue.refreshError, t)}</p><button className="btn btn-outline btn-sm" onClick={refresh} disabled={refreshing}>{t('bank.retryRefresh')}</button>
    </div>}
    {queue.kind === 'ready' && rows.length === 0 && <section className="card bank-review-empty">
      <h2>{t('bank.queueEmpty')}</h2><p>{t('bank.queueSourcesHelp')}</p>
      <button className="btn btn-outline" onClick={onUpload}>{t('bank.importInterview')}</button>
    </section>}
    {queue.kind === 'ready' && rows.length > 0 && <div className="bank-review-layout" data-detail-open={detailOpen && !!selected}>
      <section className="card bank-review-list" aria-labelledby="bank-review-list-title">
        <div className="bank-review-list-head">
          <div className="bank-review-list-title"><h2 id="bank-review-list-title">{t('bank.questionsForReview')}</h2><span className="chip chip-slate">{t('bank.reviewQueueCount', { count: rows.length })}</span></div>
          <input ref={pendingSearch} className="inp" type="search" aria-label={t('bank.searchReview')} placeholder={t('bank.searchReviewPlaceholder')} value={search} onChange={event => setSearch(event.target.value)} />
        </div>
        {filtered.length ? <ul className="bank-review-rows">{filtered.map(row => {
          const status = reviewProgress(row, operations[row.key]);
          return <li key={row.key}><button className="bank-review-row" aria-current={row.key === selectedKey ? true : undefined} onClick={() => select(row)}>
            <span className="bank-review-row-meta"><span>{row.batch.short_code}</span><span className={`chip chip-${status.color}`}>{status.text}</span></span>
            <span className="bank-review-row-title">{row.item.title}</span>
            <span className="bank-review-row-topic">{skillName(row.item.suggested_skill_key)}</span>
          </button></li>;
        })}</ul> : <p className="bank-review-no-matches" role="status">{t('bank.noReviewMatches')}</p>}
      </section>
      <article className="card bank-review-detail" aria-label={t('bank.reviewQuestionDetails')}>
        {selected ? <>
          <div className="bank-review-detail-body" ref={detailBody}>
            <header className="bank-review-detail-head">
              <button className="btn btn-ghost btn-sm bank-review-back" onClick={backToQueue}>{t('bank.backToQueue')}</button>
              <div className="bank-review-meta"><span className={`chip chip-${reviewProgress(selected, operation).color}`}>{reviewProgress(selected, operation).text}</span><span>{selected.batch.short_code}</span><span>{skillName(selected.item.suggested_skill_key)}</span></div>
              <h2 ref={detailHeading} tabIndex={-1}>{questionText}</h2>
              {originalQuestion && originalQuestion !== questionText?.trim() && <div className="bank-review-original"><h3>{t('bank.originalInterviewQuestion')}</h3><p>{originalQuestion}</p></div>}
              <dl className="bank-review-facts">
                {selected.batch.meta?.company && <div><dt>{t('bank.company')}</dt><dd>{selected.batch.meta.company}</dd></div>}
                {selected.batch.meta?.level && <div><dt>{t('bank.level')}</dt><dd>{selected.batch.meta.level}</dd></div>}
                {!generationTaskId && Number.isFinite(selected.item.extract_confidence) && <div><dt>{t('bank.extractionConfidence')}</dt><dd>{Math.round(selected.item.extract_confidence * 100)}%</dd></div>}
                <div><dt>{t('bank.source')}</dt><dd>{generationTaskId ? t('bank.workshopSource') : t('bank.importSource')}</dd></div>
                {!!selected.item.review_revision && <div><dt>{t('bank.reviewVersion')}</dt><dd>{selected.item.review_revision}</dd></div>}
                {!!selected.item.generation_meta?.source_files?.length && <div><dt>{t('bank.sourceFile')}</dt><dd>{selected.item.generation_meta.source_files.join('、')}</dd></div>}
              </dl>
              {selected.item.is_duplicate_of != null && <p className="bank-review-duplicate">{t('bank.reviewDuplicate')}</p>}
              {generationTaskId ? <Link className="link" to={`/bank/workshop?task=${generationTaskId}`}>{t('bank.openWorkshopBatch')}</Link>
                : <button className="btn btn-ghost btn-sm bank-review-source" onClick={() => onOpenImport(selected.batch)}>{t('bank.organizeImportedSet')}</button>}
            </header>
            {revising && <p className="bank-review-notice" role="status">{t('bank.regeneratingOriginalVisible')}</p>}
            {failed && !revising && <div className="bank-review-warning" role="alert"><p>{t('bank.regenerationErrorDetail', { detail: selected.item.review_error || t('bank.draftRetained') })}</p></div>}
            {!pendingFeedback && selected.item.review_feedback && <section className="bank-review-original"><h3>{t('bank.latestReviewFeedback')}</h3><p>{selected.item.review_feedback}</p></section>}
            <section className="bank-review-reading" aria-label={t('bank.draftAnswer')}>
              <h3>{revising || failed ? t('bank.originalDraftAnswer') : t('bank.draftAnswer')}</h3>
              {answerHtml ? <div className="bank-answer bank-review-answer" dangerouslySetInnerHTML={{ __html: answerHtml }} /> : <p className="hint">{t('bank.noDraftAnswer')}</p>}
            </section>
            <section className="bank-review-reading" aria-label={t('bank.rubric')}>
              <div className="bank-review-rubric-head"><h3>{t('bank.rubric')}</h3>{rubric.maxPoints !== null && <span className="hint">{t('bank.rubricMaxPoints', { points: rubric.maxPoints })}</span>}</div>
              {rubric.points.length ? <ul className="bank-review-rubric">{rubric.points.map((point, index) => <li key={index}>
                {point.title && <strong>{point.title}</strong>}{point.detail && <p>{point.detail}</p>}
                {point.keywords.length > 0 && <div className="bank-review-keywords">{point.keywords.map((keyword, position) => <span className="chip chip-slate" key={position}>{keyword}</span>)}</div>}
              </li>)}</ul> : <p className="hint">{t('bank.noRubric')}</p>}
            </section>
          </div>
          <footer className="bank-review-actions">
            {actionError && actionError.key === selectedKey && <p className="bank-review-action-error" role="alert">{t('bank.reviewActionErrorDetail', { error: bankMessageText(actionError.message, t) })}</p>}
            {operation?.kind === 'uncertain' && <div className="bank-review-warning" role="alert"><p>{t('bank.reviewUnconfirmedDetail', { error: bankMessageText(operation.error, t) })}</p>
              <button className="btn btn-outline btn-sm" onClick={() => void submit(selected, operation.request)}>{t('bank.retryReviewAction')}</button>
            </div>}
            {pendingFeedback && <form className="bank-review-feedback" onSubmit={event => {
              event.preventDefault();
              if (pendingFeedback.text.trim() && !operation && !revising) startReview(selected, pendingFeedback.action, pendingFeedback.text);
            }}>
              <label htmlFor="bank-review-feedback">{t(feedbackLabels[pendingFeedback.action])} <span className="hint">{t('bank.required')}</span></label>
              <textarea ref={feedbackInput} id="bank-review-feedback" className="textarea" rows={3} required maxLength={1000}
                value={pendingFeedback.text} disabled={!!operation || revising}
                onChange={event => setFeedback({ ...pendingFeedback, text: event.target.value })} />
              <p className="hint">{t('bank.reviewFeedbackHelp')}</p>
              <div className="bank-review-action-row">
                <button type="button" className="btn btn-ghost" disabled={!!operation || revising} onClick={() => setFeedback(null)}>{t('bank.cancel')}</button>
                <button className={`btn ${pendingFeedback.action === 'reject' ? 'bank-review-reject' : 'btn-primary'}`} disabled={!pendingFeedback.text.trim() || !!operation || revising}>
                  {operation?.kind === 'submitting' ? t('bank.submittingEllipsis') : pendingFeedback.action === 'reject' ? t('bank.confirmReject') : failed ? t('bank.retryGeneration') : t('bank.submitAndRegenerate')}
                </button>
              </div>
            </form>}
            {!pendingFeedback && <>
              {approvalReason && <p className="hint">{approvalReason}</p>}
              <div className="bank-review-action-row">
                <button className="btn btn-primary" disabled={!!approvalReason || !!operation || revising} onClick={() => startReview(selected, 'approve')}>
                  {operation?.kind === 'submitting' && operation.request.action === 'approve' ? t('bank.approving') : t(actionLabels.approve)}
                </button>
                <button className="btn btn-outline" disabled={!!operation || revising} onClick={() => setFeedback({ key: selected.key, action: 'revise', text: failed ? selected.item.review_feedback ?? '' : '' })}>{t(actionLabels.revise)}</button>
                <button className="btn bank-review-reject" disabled={!!operation || revising} onClick={() => setFeedback({ key: selected.key, action: 'reject', text: '' })}>{t(actionLabels.reject)}</button>
              </div>
            </>}
          </footer>
        </> : <div className="bank-review-empty"><p>{missingLinkedItem ? t('bank.reviewLinkGone') : t('bank.chooseReviewQuestion')}</p></div>}
      </article>
    </div>}
  </div>;
}
