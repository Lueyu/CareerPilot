import { useLocale } from '../../i18n';
import type { ExamDetail, ExamReportData } from '../../api/client';

export type ExamMode = 'test' | 'practice';
export type ExamSource = 'bank' | 'catalog' | 'library' | 'interview_set';
export type QuestionType = 'mcq' | 'open' | 'code';
export type Attempt = Omit<ExamDetail, 'questions'> & {
  questions: (ExamDetail['questions'][number] & { answer_format?: QuestionType | null })[];
  interview_agent?: boolean;
  origin_session_id?: number | null;
  deadline_at?: string | null;
  server_now?: string;
};
export type Report = Omit<ExamReportData, 'questions' | 'observations'> & {
  started_at?: string | null;
  submitted_at?: string | null;
  origin_session_id?: number | null;
  questions: (ExamReportData['questions'][number] & {
    correct_choice_index?: number | null;
    answer_format?: QuestionType | null;
    provenance?: {
      source?: ExamSource | null;
      question_version?: string | null;
      answer_revision?: number | null;
      rubric_version?: string | null;
      interview_reference?: { set_id: string; project_name?: string | null; source_item_id?: number | null } | null;
    };
  })[];
  observations?: { order_no: number; independent?: boolean; evidence_interpretation?: string }[];
};

const translations = {
  'zh-CN': (zh: string, _en: string) => zh,
  en: (_zh: string, en: string) => en,
};
export const useCopy = () => translations[useLocale()];

export function positiveId(value: string | number | null | undefined) {
  if (value == null || !/^\d+$/.test(String(value))) return undefined;
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : undefined;
}

export function chatHref(origin?: number | null, reportId?: number) {
  const id = positiveId(origin);
  return '/chat' + (id ? '/' + id : '') + (reportId ? '?report=' + reportId : '');
}

export function examHref(id?: number, report = false, session?: number) {
  return '/chat/exam' + (id ? '/' + id + (report ? '/report' : '') : '') + (session ? '?session=' + session : '');
}

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

export const pendingStatuses = new Set(['SUBMITTED', 'TRANSCRIPT_CONFIRMED', 'FROZEN', 'SCORING', 'SCORING_RETRY_WAIT']);
export const reportStatuses = new Set([...pendingStatuses, 'COMPLETED', 'SCORING_FAILED', 'NEEDS_REVIEW']);

export function answered(value: string | number | undefined | null) {
  return typeof value === 'number' ? value >= 0 : Boolean(value?.trim());
}

export function durationLabel(seconds: number) {
  const whole = Math.max(0, Math.ceil(seconds));
  return String(Math.floor(whole / 60)).padStart(2, '0') + ':' + String(whole % 60).padStart(2, '0');
}
