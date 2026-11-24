import { t } from '../i18n';
export const SKILLS = [
  { key: 'agent', labelKey: 'bank.skillAgent' }, { key: 'tools', labelKey: 'bank.skillTools' },
  { key: 'memory', labelKey: 'bank.skillMemory' }, { key: 'rag', labelKey: 'bank.skillRag' },
  { key: 'eval', labelKey: 'bank.skillEval' }, { key: 'backend', labelKey: 'bank.skillBackend' },
  { key: 'reliability', labelKey: 'bank.skillReliability' }, { key: 'deploy', labelKey: 'bank.skillDeployment' },
];

export function workshopSkillLabel(key: string, fallback = key): string {
  const skill = SKILLS.find(candidate => candidate.key === key);
  return skill ? t(skill.labelKey) : fallback;
}

export const PROFILE_FIELDS = [
  ['background', 'bank.profileBackground', 'bank.profileBackgroundHint'], ['role', 'bank.profileRole', 'bank.profileRoleHint'],
  ['responsibilities', 'bank.profileOwnedWork', 'bank.profileOwnedWorkHint'], ['stack', 'bank.profileTechnical', 'bank.profileTechnicalHint'],
  ['decisions', 'bank.profileTradeoffs', 'bank.profileTradeoffsHint'], ['challenges', 'bank.profileChallenges', 'bank.profileChallengesHint'],
  ['outcomes', 'bank.profileResults', 'bank.profileResultsHint'], ['boundaries', 'bank.profileBoundaries', 'bank.profileBoundariesHint'],
] as const;
export type ProjectProfile = Partial<Record<typeof PROFILE_FIELDS[number][0], string>>;
export interface ProjectRecord { id: number; name: string; kind?: string; profile?: ProjectProfile; meta?: { kind?: string; profile?: ProjectProfile }; repo_url?: string }
export interface ContextProject extends ProjectRecord { facts: {id: string; text: string; source: string; memory_id?: number; analysis_id?: number; source_revision?: string; user_corrected?: boolean; citations?: {path: string; start_line: number; end_line: number}[]}[]; has_personal_context: boolean; snapshot_fingerprint: string }
export interface AnswerContent {
  tldr: string; elevator: string; analogy: string; examiner: string[];
  steps: {title: string; detail: string}[]; how_to_answer: string; pitfalls: string[];
  followups: {q: string; a: string}[]; key_points: {title: string; detail: string}[]; terms: {term: string; desc: string}[];
}
export interface GeneratedItem {
  title: string; original_question: string; skill_key: string;
  question_mode: 'general' | 'project' | 'needs_context'; project_id: number | null; fact_refs: string[];
  answer: AnswerContent; project_answer: string; hypothetical_answer: string; missing_context: string[];
  source_question_id: number | null;
}
export interface WorkshopRow {
  id: number; title: string; status: string; draft_answer?: string | null; draft_rubric?: unknown;
  review_revision?: number;
  question_id?: number | null; merged_into?: number | null; is_duplicate_of?: number | null;
  generation_meta?: { item: GeneratedItem; task_id?: number; source?: string } | null;
}
export interface WorkshopSnapshot {
  kind: string; count: number; skills: {key: string; name: string}[]; skill_keys: string[];
  project_context: ContextProject[]; purpose: string; source_text?: string;
  source_questions: {id: number; title: string; canonical_question_id?: number | null}[];
  source_analysis_id?: number | null; source_issue_ids?: string[];
  project_issue_targets?: {project_id: number; analysis_id: number; issue_id: string}[];
}
export interface WorkshopTask {
  id: number; kind: 'import' | 'supplement'; mode: 'api' | 'external'; status: string;
  prompt_text: string; input_snapshot: WorkshopSnapshot; raw_response?: string; error?: string | null;
  import_session_id?: number | null; created_at?: string;
  review?: {id: number; short_code: string; status: string; items: WorkshopRow[]};
}
export interface WorkshopSuggestion {
  id: string; title: string; reason: string; kind: 'import' | 'supplement'; count: number;
  skill_keys: string[]; project_ids: number[]; purpose: string;
  source_analysis_id?: number | null; source_issue_ids?: string[];
}
export function approvalBlock(row: WorkshopRow): string | null {
  if (row.status !== 'DRAFT') return t('bank.approvalReviewed');
  const item = row.generation_meta?.item;
  if (!item) return t('bank.approvalNoDraft');
  if (item.question_mode === 'needs_context' || item.missing_context?.length) return t('bank.approvalNeedsContext');
  if (!row.draft_answer?.trim()) return t('bank.approvalNoAnswer');
  return null;
}
export const TASK_LABELS: Record<string, string> = {READY: 'bank.taskReady', RUNNING: 'bank.taskRunning', FAILED: 'bank.taskFailed', REVIEW: 'bank.taskReview', COMPLETE: 'bank.taskComplete'};
export const ITEM_LABELS: Record<string, string> = {DRAFT:'bank.pendingReview', APPROVED:'bank.itemApproved', REJECTED:'bank.itemRejected', MERGED:'bank.itemMerged'};
export const QUESTION_LABELS = {general:'bank.generalQuestion', project:'bank.projectQuestion', needs_context:'bank.needsProjectContext'};
export function workshopQuestionLabel(mode: GeneratedItem['question_mode']): string {
  return Object.prototype.hasOwnProperty.call(QUESTION_LABELS, mode) ? t(QUESTION_LABELS[mode]) : mode ?? '';
}

export type BankMessage = string | { key: string; values?: Readonly<Record<string, string | number>> };

export function bankMessageText(message: BankMessage, translate = t): string {
  return typeof message === 'string' ? message : translate(message.key, message.values);
}

export function messageOf(error: unknown): string { return error instanceof Error ? error.message : String(error); }

export async function beginQuestionPractice(questionId:number,requestKey:string,transport:{post:<T=any>(path:string,body?:unknown)=>Promise<T>}, experiencedBank = false) {
  if (!Number.isInteger(questionId) || questionId<=0) throw new Error(t('bank.practiceRequiresApproval'));
  return transport.post<{id:number}>('/exams',{kind:'bank_exam',question_ids:[questionId],question_count:1,duration_minutes:20,idempotency_key:requestKey,...(experiencedBank ? {experienced_bank:true} : {})});
}

export function assessedBatchQuestionIds(rows:WorkshopRow[]):number[] {
  return [...new Set(rows.filter(row=>row.status==='APPROVED' || row.status==='MERGED')
    .map(row=>row.question_id || row.merged_into).filter((id):id is number=>typeof id==='number' && Number.isInteger(id) && id>0))];
}
export async function beginProjectAssessment(rows:WorkshopRow[],requestKey:string,transport:{post:<T=any>(path:string,body?:unknown)=>Promise<T>},generationId:number) {
  const ids=assessedBatchQuestionIds(rows);
  if(!ids.length)throw new Error(t('bank.assessmentRequiresApproval'));
  if(!Number.isInteger(generationId) || generationId<=0)throw new Error(t('bank.assessmentMissingSource'));
  return transport.post<{id:number}>('/exams',{kind:'bank_exam',project_generation_id:generationId,question_ids:ids,question_count:ids.length,duration_minutes:Math.min(120,Math.max(20,ids.length*10)),idempotency_key:requestKey});
}
