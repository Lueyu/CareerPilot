export const EXPERIENCED_BANK_PATH = '/bank?track=experienced';

export function isExperiencedPractice(
  exam: { kind?: string; experienced_bank_practice?: boolean },
  params: URLSearchParams,
): boolean {
  return exam.experienced_bank_practice ?? (exam.kind === 'practice' && params.get('track') === 'experienced');
}

export function experiencedBankFilters(params: URLSearchParams): Record<string, string> {
  const filters: Record<string, string> = { track: 'experienced' };
  const level = params.get('level');
  const domain = params.get('domain');
  if (level === 'mid' || level === 'senior') filters.level = level;
  if (domain) filters.domain = domain;
  return filters;
}

export function experiencedBankPath(params: URLSearchParams): string {
  return `/bank?${new URLSearchParams(experiencedBankFilters(params))}`;
}

export function experiencedPracticePath(id: number, params: URLSearchParams): string {
  return `/exam/diagnosis?${new URLSearchParams({ id: String(id), ...experiencedBankFilters(params) })}`;
}
