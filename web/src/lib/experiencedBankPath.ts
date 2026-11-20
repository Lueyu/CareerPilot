import { getWorkspace } from '../api/client';

export const EXPERIENCED_BANK_PATH = '/bank';

export function isExperiencedPractice(
  exam: { kind?: string; experienced_bank_practice?: boolean },
  params: URLSearchParams,
): boolean {
  return exam.experienced_bank_practice ?? (exam.kind === 'practice' && params.get('track') === 'experienced');
}

export function experiencedBankFilters(params: URLSearchParams): Record<string, string> {
  const filters: Record<string, string> = {};
  const level = params.get('level');
  const domain = params.get('domain');
  const source = params.get('source');
  if (level === 'mid' || level === 'senior') filters.level = level;
  if (domain) filters.domain = domain;
  if (source === 'legacy' || source === 'authored') filters.source = source;
  for (const key of ['q', 'company']) {
    const value = params.get(key)?.trim();
    if (value) filters[key] = value;
  }
  const page = params.get('page');
  if (page && /^\d+$/.test(page) && Number.isSafeInteger(Number(page)) && Number(page) > 1) {
    filters.page = String(Number(page));
  }
  return filters;
}

export function experiencedBankPath(params: URLSearchParams): string {
  const query = new URLSearchParams(experiencedBankFilters(params)).toString();
  return query ? EXPERIENCED_BANK_PATH + '?' + query : EXPERIENCED_BANK_PATH;
}

function practiceFiltersKey(id: number) {
  return `cp-experienced-filters:${getWorkspace()}:${id}`;
}

export function rememberExperiencedFilters(id: number, params: URLSearchParams) {
  try {
    localStorage.setItem(practiceFiltersKey(id), new URLSearchParams(experiencedBankFilters(params)).toString());
  } catch {
    // The in-flow URLs still carry the filters when storage is unavailable.
  }
}

export function experiencedHistoryPath(id: number, report: boolean): string {
  let filters = new URLSearchParams();
  try {
    filters = new URLSearchParams(localStorage.getItem(practiceFiltersKey(id)) ?? '');
  } catch {
    // History links also work without saved filters.
  }
  const query = new URLSearchParams({ id: String(id), ...experiencedBankFilters(filters) });
  return `/exam/diagnosis${report ? '/report' : ''}?${query}`;
}

export function experiencedPracticePath(id: number, params: URLSearchParams): string {
  return `/exam/diagnosis?${new URLSearchParams({ id: String(id), ...experiencedBankFilters(params) })}`;
}
