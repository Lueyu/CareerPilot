import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigationType, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { ORIGINAL_SCENARIO_LABEL } from '../lib/bankSource';
import { messageOf } from '../lib/questionWorkshop';
import { experiencedPracticePath } from '../lib/experiencedBankPath';

export const EXPERIENCE_LABELS = { mid: '2–4 年', senior: '4 年以上' };
type ExperiencedLevel = keyof typeof EXPERIENCE_LABELS;
export const EXPERIENCED_SOURCE_LABELS = { legacy: '原题库', authored: ORIGINAL_SCENARIO_LABEL };
type ExperiencedSource = keyof typeof EXPERIENCED_SOURCE_LABELS;
const PAGE_SIZE = 10;
const FACETS = ['domain', 'level', 'source', 'q', 'company'] as const;
type Facet = typeof FACETS[number];
const FACET_LABELS = {
  domain: '能力域', level: '建议年限', source: '题目来源', q: '搜索', company: '公司',
} satisfies Record<Facet, string>;

export function experiencedLevelLabel({ level, source }: Pick<ExperiencedQuestion, 'level' | 'source'>) {
  return source === 'legacy' ? `建议 · ${EXPERIENCE_LABELS[level]}` : EXPERIENCE_LABELS[level];
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
  | { kind: 'error'; message: string }
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

export default function ExperiencedBank({
  practiceBusy,
  practiceError,
  onPractice,
  onReference,
}: {
  practiceBusy: string | null;
  practiceError: {id: string; message: string} | null;
  onPractice: (question: ExperiencedQuestion) => void;
  onReference: (question: ExperiencedQuestion, domainName: string) => void;
}) {
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
        throw new Error('题单格式异常');
      }
      if (current) setCatalog({ kind: 'ready', domains: [...data.domains].sort((a, b) => a.order - b.order) });
    }).catch(error => {
      if (current) setCatalog({ kind: 'error', message: messageOf(error) });
    });
    return () => { current = false; };
  }, [attempt]);

  const domains = catalog.kind === 'ready' ? catalog.domains : null;
  const indexed = useMemo(() => (domains ?? []).map(domain => ({
    ...domain,
    entries: domain.questions.map(question => ({
      question,
      companies: companiesOf(question.company),
      searchable: normalizeSearch([question.title, question.short_code, ...question.tags].join(' ')),
    })),
  })), [domains]);
  const companies = Array.from(new Set(indexed.flatMap(domain => domain.entries.flatMap(entry => entry.companies))))
    .sort((a, b) => a.localeCompare(b, 'zh-CN'));
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
  const results = filtered
    .filter(domain => !domainKey || domain.key === domainKey)
    .flatMap(domain => domain.matches.map(({ question }) => ({ question, domainName: domain.name })));
  const totalPages = Math.max(1, Math.ceil(results.length / PAGE_SIZE));
  const pageParam = params.get('page') ?? '';
  const page = /^\d+$/.test(pageParam) ? Math.min(totalPages, Math.max(1, Number(pageParam))) : 1;
  const start = (page - 1) * PAGE_SIZE;
  const conditions = {
    domain: activeDomain?.name ?? domainKey,
    level: level ? EXPERIENCE_LABELS[level] : '',
    source: source ? EXPERIENCED_SOURCE_LABELS[source] : '',
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
          <input
            className="inp"
            type="search"
            aria-label="搜索题目、标签或题号"
            placeholder="搜索题目、标签或题号"
            value={query}
            onBlur={() => { editingSearch.current = false; }}
            onChange={event => {
              const value = event.currentTarget.value;
              selectFilter('q', value, editingSearch.current && Boolean(value));
              editingSearch.current = Boolean(value);
            }}
          />
          {query && <button className="btn btn-outline" type="button" onClick={() => selectFilter('q', '')}>清空搜索</button>}
        </form>
        <button
          className="btn btn-outline"
          aria-expanded={filtersOpen}
          aria-controls="bank-catalog-filters"
          onClick={() => setFiltersOpen(value => !value)}
        >筛选</button>
        {resumePath && <Link className="btn btn-primary" to={resumePath}>继续本次练习</Link>}
      </div>
      <div id="bank-catalog-filters" className="experienced-filters" hidden={!filtersOpen}>
        <label>建议年限
          <select className="inp" aria-label="建议年限" value={level} onChange={event => selectFilter('level', event.currentTarget.value)}>
            <option value="">全部年限</option>
            {LEVELS.map(value => <option key={value} value={value}>{EXPERIENCE_LABELS[value]}</option>)}
          </select>
        </label>
        <label>公司
          <select className="inp" aria-label="公司" value={company} onChange={event => selectFilter('company', event.currentTarget.value)}>
            <option value="">全部公司</option>
            {company && !companies.includes(company) && <option value={company}>{company}</option>}
            {companies.map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label>题目来源
          <select className="inp" aria-label="题目来源" value={source} onChange={event => selectFilter('source', event.currentTarget.value)}>
            <option value="">全部来源</option>
            {Object.entries(EXPERIENCED_SOURCE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
      </div>
      {hasConditions && (
        <div className="bank-actions" role="group" aria-label="已应用条件">
          {FACETS.map(key => conditions[key] && (
            <button
              key={key}
              className="btn btn-outline btn-sm experienced-condition"
              aria-label={`移除${FACET_LABELS[key]}条件 ${conditions[key]}`}
              onClick={() => selectFilter(key, '')}
            >{FACET_LABELS[key]} · {conditions[key]} ×</button>
          ))}
          <button className="btn btn-outline btn-sm" onClick={clearFilters}>清除筛选</button>
        </div>
      )}
      {catalog.kind === 'loading' && <p role="status" className="card bank-section">正在加载题目…</p>}
      {catalog.kind === 'error' && (
        <section className="card bank-section">
          <p role="alert">题库加载失败：{catalog.message}</p>
          <div>
            <button className="btn btn-outline" onClick={() => {
              setCatalog({ kind: 'loading' });
              setAttempt(value => value + 1);
            }}>重试加载题库</button>
          </div>
        </section>
      )}
      {catalog.kind === 'ready' && (
        <div className="experienced-layout">
          <nav className="card bank-section experienced-domains" aria-label="能力域">
            <h2>能力域</h2>
            <div className="experienced-domain-list">
              <button
                className="experienced-domain"
                aria-pressed={!domainKey}
                onClick={() => selectFilter('domain', '')}
              >
                全部分类
              </button>
              {filtered.map(domain => (
                <button
                  key={domain.key}
                  className="experienced-domain"
                  aria-label={domain.name}
                  aria-describedby={`bank-domain-count-${domain.key}`}
                  aria-pressed={domain.key === domainKey}
                  onClick={() => selectFilter('domain', domain.key)}
                >
                  <span>{domain.name}</span><small id={`bank-domain-count-${domain.key}`}>{domain.matches.length} 题</small>
                </button>
              ))}
            </div>
          </nav>
          <section className="card bank-section experienced-questions" aria-labelledby="bank-catalog-title">
            <div className="bank-actions experienced-list-head">
              <h2 id="bank-catalog-title">题目列表</h2>
              <span className="hint" role="status">{results.length} 题</span>
            </div>
            {results.length === 0 ? <p>没有符合条件的题目。</p> : (
              <ul className="experienced-question-list">
                {results.slice(start, start + PAGE_SIZE).map(({ question, domainName }) => (
                  <li key={question.id} className="experienced-question">
                    <div>
                      <h3 id={`experienced-question-${question.id}`} className="experienced-prompt">{question.title}</h3>
                      <div className="q-meta">
                        <span>{domainName}</span>
                        <span className="chip chip-slate">{experiencedLevelLabel(question)}</span>
                        <span className="experienced-question-code">{question.short_code}</span>
                      </div>
                    </div>
                    <div className="bank-actions">
                      <button
                        className="btn btn-primary btn-sm"
                        disabled={practiceBusy !== null}
                        aria-describedby={`experienced-question-${question.id}`}
                        onClick={() => onPractice(question)}
                      >{practiceBusy === question.short_code ? '准备练习…' : practiceError?.id === question.short_code ? '重试练习' : '开始练习'}</button>
                      <button className="btn btn-outline btn-sm" aria-describedby={`experienced-question-${question.id}`} onClick={() => onReference(question, domainName)}>
                        查看参考
                      </button>
                    </div>
                    {practiceError?.id === question.short_code && <p role="alert">{practiceError.message}</p>}
                  </li>
                ))}
              </ul>
            )}
            {totalPages > 1 && (
              <nav className="bank-actions experienced-pagination" aria-label="题目分页">
                <button className="btn btn-outline btn-sm" disabled={page === 1} onClick={() => selectFilter('page', page === 2 ? '' : String(page - 1))}>上一页</button>
                <span className="hint" role="status">第 {page} / {totalPages} 页</span>
                <button className="btn btn-outline btn-sm" disabled={page === totalPages} onClick={() => selectFilter('page', String(page + 1))}>下一页</button>
              </nav>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
