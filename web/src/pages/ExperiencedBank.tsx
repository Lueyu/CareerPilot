import { getLocale, t, useLocale, useT } from '../i18n';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigationType, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import StudyEntryButton from '../components/StudyEntryButton';
import { bankMessageText, messageOf, type BankMessage } from '../lib/questionWorkshop';
import { experiencedPracticePath } from '../lib/experiencedBankPath';

export const EXPERIENCE_LABELS = { mid: 'bank.experienceMid', senior: 'bank.experienceSenior' };
type ExperiencedLevel = keyof typeof EXPERIENCE_LABELS;
export const EXPERIENCED_SOURCE_LABELS = { legacy: 'bank.legacySource', authored: 'bank.originalScenario' };
type ExperiencedSource = keyof typeof EXPERIENCED_SOURCE_LABELS;
const PAGE_SIZE = 10;
const FACETS = ['domain', 'level', 'source', 'q', 'company'] as const;
type Facet = typeof FACETS[number];
const FACET_LABELS = {
  domain: 'bank.domain', level: 'bank.recommendedExperience', source: 'bank.questionSource', q: 'bank.search', company: 'bank.company',
} satisfies Record<Facet, string>;

export function experiencedLevelLabel({ level, source }: Pick<ExperiencedQuestion, 'level' | 'source'>) {
  const experience = t(EXPERIENCE_LABELS[level]);
  return source === 'legacy' ? t('bank.legacyExperience', { experience }) : experience;
}
const DOMAIN_LABELS: Record<string, string> = {
  context: 'bank.domainContext',
  delivery: 'bank.domainDelivery',
  engineering: 'bank.domainEngineering',
  evaluation: 'bank.domainEvaluation',
  governance: 'bank.domainGovernance',
  models: 'bank.domainModels',
  orchestration: 'bank.domainOrchestration',
  production: 'bank.domainProduction',
  rag: 'bank.domainRag',
  tools: 'bank.domainTools',
  training: 'bank.domainTraining',
};

export function experiencedDomainLabel(key: string, name: string): string {
  const labelKey = Object.prototype.hasOwnProperty.call(DOMAIN_LABELS, key) ? DOMAIN_LABELS[key] : undefined;
  return labelKey && getLocale() === 'en' ? t(labelKey) : name;
}

const LEVELS: ExperiencedLevel[] = ['mid', 'senior'];

export interface ExperiencedQuestion {
  id: number;
  short_code: string;
  title: string;
  level: ExperiencedLevel;
  source?: ExperiencedSource;
  company?: string | null;
  tags: string[];
}
interface ExperiencedDomain {
  key: string;
  name: string;
  order: number;
  focus: string;
  questions: ExperiencedQuestion[];
}
type CatalogState =
  | { kind: 'loading' }
  | { kind: 'error'; message: BankMessage }
  | { kind: 'ready'; domains: ExperiencedDomain[] };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

function isQuestion(value: unknown): value is ExperiencedQuestion {
  return isRecord(value) && typeof value.id === 'number' && Number.isSafeInteger(value.id) && value.id > 0 &&
    typeof value.short_code === 'string' && typeof value.title === 'string' &&
    (value.level === 'mid' || value.level === 'senior') &&
    (value.source === undefined || value.source === 'legacy' || value.source === 'authored') &&
    (value.company == null || typeof value.company === 'string') &&
    Array.isArray(value.tags) && value.tags.every(tag => typeof tag === 'string');
}

function isDomain(value: unknown): value is ExperiencedDomain {
  return isRecord(value) && typeof value.key === 'string' && typeof value.name === 'string' &&
    typeof value.order === 'number' && Number.isFinite(value.order) && typeof value.focus === 'string' &&
    Array.isArray(value.questions) && value.questions.every(isQuestion);
}

function companiesOf(company: ExperiencedQuestion['company']): string[] {
  return (company ?? '')
    .replace(/\([^)]*\)|（[^）]*）/g, '')
    .split(/[/／、,，]/)
    .map(name => name.trim())
    .filter(name => name && !/^(所有公司|全部公司|不限公司|通用|不限|无|未知|[-—]+)$/.test(name));
}

const normalizeSearch = (value: string) => value.normalize('NFKC').toLowerCase();

function QuestionActions({ question, onRetestMapping }: {
  question: ExperiencedQuestion;
  onRetestMapping: (question: ExperiencedQuestion) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const actionsId = `experienced-actions-${question.id}`;

  return <>
    <button
      type="button"
      className="btn btn-ghost btn-sm experienced-more"
      aria-describedby={`experienced-question-${question.id}`}
      aria-expanded={open}
      aria-controls={open ? actionsId : undefined}
      onClick={() => setOpen(value => !value)}
    >{t('bank.moreActions')}</button>
    {open && <div id={actionsId} className="bank-actions experienced-extra-actions">
      {question.source === 'legacy' && <button type="button" className="btn btn-outline btn-sm" onClick={() => onRetestMapping(question)}>{t('bank.retestMapping')}</button>}
      <StudyEntryButton kind="question" id={question.id} bankKey={question.short_code} />
    </div>}
  </>;
}

export default function ExperiencedBank({
  practiceBusy,
  practiceError,
  onPractice,
  onReference,
  onRetestMapping,
  onCatalogSummary,
}: {
  practiceBusy: string | null;
  practiceError: {id: string; message: string} | null;
  onPractice: (question: ExperiencedQuestion) => void;
  onReference: (question: ExperiencedQuestion, domainName: string, domainKey: string) => void;
  onRetestMapping: (question: ExperiencedQuestion) => void;
  onCatalogSummary?: (summary: { questions: number; domains: number }) => void;
}) {
  const t = useT();
  const locale = useLocale();
  const [catalog, setCatalog] = useState<CatalogState>({ kind: 'loading' });
  const [attempt, setAttempt] = useState(0);
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navigationType = useNavigationType();
  const [filtersOpen, setFiltersOpen] = useState(false);
  const editingSearch = useRef(false);
  const levelParam = params.get('level');
  const level = levelParam === 'mid' || levelParam === 'senior' ? levelParam : '';
  const sourceParam = params.get('source');
  const source = sourceParam === 'legacy' || sourceParam === 'authored' ? sourceParam : '';
  const domainKey = params.get('domain') ?? '';
  const company = params.get('company') ?? '';
  const query = params.get('q') ?? '';
  const resumeId = location.state?.experiencedPracticeId;
  const resumePath = typeof resumeId === 'number' && Number.isSafeInteger(resumeId) && resumeId > 0
    ? experiencedPracticePath(resumeId, params)
    : null;
  const selectFilter = (key: Facet | 'page', value: string, replace = false) => {
    const next = new URLSearchParams(params);
    next.delete('track');
    if (key !== 'page') next.delete('page');
    if (value) next.set(key, value);
    else next.delete(key);
    if (next.toString() !== params.toString()) {
      setParams(next, { replace, state: location.state });
    }
  };
  const clearFilters = () => {
    const next = new URLSearchParams(params);
    for (const key of [...FACETS, 'page', 'track']) next.delete(key);
    editingSearch.current = false;
    setParams(next, { state: location.state });
  };

  useEffect(() => {
    if (navigationType === 'POP') editingSearch.current = false;
  }, [location.key, navigationType]);

  useEffect(() => {
    let current = true;
    api.get<unknown>('/bank/experienced').then(data => {
      if (!isRecord(data) || !Array.isArray(data.domains) || !data.domains.every(isDomain)) {
        setCatalog({ kind: 'error', message: { key: 'bank.invalidCatalog' } });
        return;
      }
      if (current) setCatalog({ kind: 'ready', domains: [...data.domains].sort((a, b) => a.order - b.order) });
    }).catch(error => {
      if (current) setCatalog({ kind: 'error', message: messageOf(error) });
    });
    return () => { current = false; };
  }, [attempt]);

  const domains = catalog.kind === 'ready' ? catalog.domains : null;
  useEffect(() => {
    if (domains && onCatalogSummary) {
      onCatalogSummary({
        questions: domains.reduce((total, domain) => total + domain.questions.length, 0),
        domains: domains.length,
      });
    }
  }, [domains, onCatalogSummary]);

  const indexed = useMemo(() => (domains ?? []).map(domain => ({
    ...domain,
    entries: domain.questions.map(question => ({
      question,
      companies: companiesOf(question.company),
      searchable: normalizeSearch([question.title, question.short_code, ...question.tags].join(' ')),
    })),
  })), [domains]);
  const companies = Array.from(new Set(indexed.flatMap(domain => domain.entries.flatMap(entry => entry.companies))))
    .sort((a, b) => a.localeCompare(b, locale));
  const terms = normalizeSearch(query).trim().split(/\s+/).filter(Boolean);
  const filtered = indexed.map(domain => ({
    ...domain,
    matches: domain.entries.filter(({ question, companies: listed, searchable }) =>
      (!level || question.level === level) &&
      (!source || (question.source ?? 'authored') === source) &&
      (!company || listed.includes(company)) &&
      terms.every(term => searchable.includes(term)),
    ),
  }));
  const activeDomain = filtered.find(domain => domain.key === domainKey);
  const allMatchCount = filtered.reduce((total, domain) => total + domain.matches.length, 0);
  const results = filtered
    .filter(domain => !domainKey || domain.key === domainKey)
    .flatMap(domain => domain.matches.map(({ question }) => ({ question, domainName: domain.name, referenceDomainKey: domain.key })));
  const totalPages = Math.max(1, Math.ceil(results.length / PAGE_SIZE));
  const pageParam = params.get('page') ?? '';
  const page = /^\d+$/.test(pageParam) ? Math.min(totalPages, Math.max(1, Number(pageParam))) : 1;
  const start = (page - 1) * PAGE_SIZE;
  const conditions = {
    domain: activeDomain ? experiencedDomainLabel(activeDomain.key, activeDomain.name) : domainKey,
    level: level ? t(EXPERIENCE_LABELS[level]) : '',
    source: source ? t(EXPERIENCED_SOURCE_LABELS[source]) : '',
    q: query.trim(),
    company,
  };
  const hasConditions = FACETS.some(key => Boolean(conditions[key]));

  return (
    <div className="bank-workspace experienced-catalog">
      <div className="experienced-controls">
        <form role="search" className="experienced-search" onSubmit={event => {
          event.preventDefault();
          selectFilter('q', query.trim(), editingSearch.current);
          editingSearch.current = false;
        }}>
          <svg className="experienced-search-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
            <circle cx="10.5" cy="10.5" r="6.5" />
            <path d="m15.5 15.5 4.5 4.5" strokeLinecap="round" />
          </svg>
          <input
            className="inp"
            type="search"
            aria-label={t('bank.catalogSearchPlaceholder')}
            placeholder={t('bank.catalogSearchPlaceholder')}
            value={query}
            onBlur={() => { editingSearch.current = false; }}
            onChange={event => {
              const value = event.currentTarget.value;
              selectFilter('q', value, editingSearch.current && Boolean(value));
              editingSearch.current = Boolean(value);
            }}
          />
          {query && (
            <button className="experienced-clear-search" type="button" aria-label={t('bank.clearSearch')} onClick={() => selectFilter('q', '')}>
              <span aria-hidden="true">×</span>
            </button>
          )}
        </form>
        <button
          className="btn btn-outline"
          aria-label={t('bank.filter')}
          aria-expanded={filtersOpen}
          aria-controls="bank-catalog-filters"
          onClick={() => setFiltersOpen(value => !value)}
        >
          <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round">
            <path d="M3 6h18M3 12h18M3 18h18" />
            <circle cx="8" cy="6" r="2" fill="var(--card)" />
            <circle cx="16" cy="12" r="2" fill="var(--card)" />
            <circle cx="10" cy="18" r="2" fill="var(--card)" />
          </svg>
          {t('bank.filter')}
        </button>
      </div>
      {resumePath && <div><Link className="btn btn-primary" to={resumePath}>{t('bank.resumePractice')}</Link></div>}
      <div id="bank-catalog-filters" className="experienced-filters" hidden={!filtersOpen}>
        <label>{t('bank.recommendedExperience')}
          <select className="inp" aria-label={t('bank.recommendedExperience')} value={level} onChange={event => selectFilter('level', event.currentTarget.value)}>
            <option value="">{t('bank.anyExperience')}</option>
            {LEVELS.map(value => <option key={value} value={value}>{t(EXPERIENCE_LABELS[value])}</option>)}
          </select>
        </label>
        <label>{t('bank.company')}
          <select className="inp" aria-label={t('bank.company')} value={company} onChange={event => selectFilter('company', event.currentTarget.value)}>
            <option value="">{t('bank.anyCompany')}</option>
            {company && !companies.includes(company) && <option value={company}>{company}</option>}
            {companies.map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label>{t('bank.questionSource')}
          <select className="inp" aria-label={t('bank.questionSource')} value={source} onChange={event => selectFilter('source', event.currentTarget.value)}>
            <option value="">{t('bank.anySource')}</option>
            {Object.entries(EXPERIENCED_SOURCE_LABELS).map(([value, label]) => <option key={value} value={value}>{t(label)}</option>)}
          </select>
        </label>
      </div>
      {hasConditions && (
        <div className="bank-actions" role="group" aria-label={t('bank.appliedFilters')}>
          {FACETS.map(key => conditions[key] && (
            <button
              key={key}
              className="btn btn-outline btn-sm experienced-condition"
              aria-label={t('bank.removeCatalogFilter', { facet: t(FACET_LABELS[key]), value: conditions[key] })}
              onClick={() => selectFilter(key, '')}
            >{t('bank.selectedFacet', { facet: t(FACET_LABELS[key]), value: conditions[key] })}</button>
          ))}
          <button className="btn btn-outline btn-sm" onClick={clearFilters}>{t('bank.clearFilters')}</button>
        </div>
      )}
      {catalog.kind === 'loading' && <p role="status" className="card bank-section">{t('bank.loadingCatalog')}</p>}
      {catalog.kind === 'error' && (
        <section className="card bank-section">
          <p role="alert">{t('bank.catalogLoadFailed', { error: bankMessageText(catalog.message, t) })}</p>
          <div>
            <button className="btn btn-outline" onClick={() => {
              setCatalog({ kind: 'loading' });
              setAttempt(value => value + 1);
            }}>{t('bank.retryCatalog')}</button>
          </div>
        </section>
      )}
      {catalog.kind === 'ready' && (
        <div className="experienced-layout">
          <nav className="card bank-section experienced-domains" aria-label={t('bank.domain')}>
            <div className="experienced-domain-list">
              <h2>{t('bank.domain')}</h2>
              <button
                className="experienced-domain"
                aria-label={t('bank.allCategories')}
                aria-describedby="bank-domain-count-all"
                aria-pressed={!domainKey}
                onClick={() => selectFilter('domain', '')}
              >
                <span>{t('bank.allCategories')}</span><small id="bank-domain-count-all">{allMatchCount}</small>
              </button>
              {filtered.map(domain => (
                <button
                  key={domain.key}
                  className="experienced-domain"
                  aria-label={experiencedDomainLabel(domain.key, domain.name)}
                  aria-describedby={`bank-domain-count-${domain.key}`}
                  aria-pressed={domain.key === domainKey}
                  onClick={() => selectFilter('domain', domain.key)}
                >
                  <span>{experiencedDomainLabel(domain.key, domain.name)}</span><small id={`bank-domain-count-${domain.key}`}>{domain.matches.length}</small>
                </button>
              ))}
            </div>
            <label className="experienced-domain-picker">
              <span>{t('bank.domain')}</span>
              <select aria-label={t('bank.domain')} value={domainKey} onChange={event => selectFilter('domain', event.currentTarget.value)}>
                <option value="">{t('bank.allCategories')}</option>
                {domainKey && !activeDomain && <option value={domainKey}>{domainKey}</option>}
                {filtered.map(domain => <option key={domain.key} value={domain.key}>{experiencedDomainLabel(domain.key, domain.name)}</option>)}
              </select>
            </label>
          </nav>
          <section className="card bank-section experienced-questions" aria-labelledby="bank-catalog-title">
            <div className="experienced-list-head">
              <h2 id="bank-catalog-title">{t('bank.catalogQuestions')}</h2>
              <span className="hint" role="status">{t('bank.catalogResultCount', { count: results.length })}</span>
            </div>
            {results.length === 0 ? <p className="experienced-empty">{t('bank.noCatalogMatches')}</p> : (
              <ul className="experienced-question-list">
                {results.slice(start, start + PAGE_SIZE).map(({ question, domainName, referenceDomainKey }) => (
                  <li key={question.id} className="experienced-question">
                    <div>
                      <h3 id={`experienced-question-${question.id}`} className="experienced-prompt">{question.title}</h3>
                      <div className="q-meta">
                        <span>{experiencedDomainLabel(referenceDomainKey, domainName)}</span>
                        <span>{experiencedLevelLabel(question)}</span>
                        <span className="experienced-question-code">{question.short_code}</span>
                      </div>
                    </div>
                    <div className="bank-actions">
                      <button
                        className="btn btn-primary btn-sm"
                        disabled={practiceBusy !== null}
                        aria-describedby={`experienced-question-${question.id}`}
                        onClick={() => onPractice(question)}
                      >{practiceBusy === question.short_code ? t('bank.preparingPractice') : practiceError?.id === question.short_code ? t('bank.retryPractice') : t('bank.startPractice')}</button>
                      <button className="btn btn-outline btn-sm" aria-describedby={`experienced-question-${question.id}`} onClick={() => onReference(question, domainName, referenceDomainKey)}>
                        {t('bank.viewReference')}
                      </button>
                      <QuestionActions question={question} onRetestMapping={onRetestMapping} />
                    </div>
                    {practiceError?.id === question.short_code && <p role="alert">{t('bank.practiceFailed', { error: practiceError.message })}</p>}
                  </li>
                ))}
              </ul>
            )}
            {totalPages > 1 && (
              <nav className="bank-actions experienced-pagination" aria-label={t('bank.questionPagination')}>
                <button className="btn btn-outline btn-sm" disabled={page === 1} onClick={() => selectFilter('page', page === 2 ? '' : String(page - 1))}>{t('bank.previousPage')}</button>
                <span className="hint" role="status">{t('bank.pageOf', { page, total: totalPages })}</span>
                <button className="btn btn-outline btn-sm" disabled={page === totalPages} onClick={() => selectFilter('page', String(page + 1))}>{t('bank.nextPage')}</button>
              </nav>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
