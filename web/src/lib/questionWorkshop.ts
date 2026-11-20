export const SKILLS = [
  { key: 'agent', name: 'Agent 系统' }, { key: 'tools', name: '工具调用' },
  { key: 'memory', name: '记忆与上下文' }, { key: 'rag', name: 'RAG' },
  { key: 'eval', name: '评估' }, { key: 'backend', name: '后端工程' },
  { key: 'reliability', name: '可靠性' }, { key: 'deploy', name: '部署' },
];
export const PROFILE_FIELDS = [
  ['background', '项目背景', '解决什么问题，面向谁'], ['role', '我的角色', '你在团队中的真实角色'],
  ['responsibilities', '我负责的工作', '自己设计、实现或验证了哪些部分'], ['stack', '技术方案', '实际使用的技术与实现方式'],
  ['decisions', '取舍与决策', '你做过的选择及其原因'], ['challenges', '问题与解决过程', '实际遇到的问题、排查和改进'],
  ['outcomes', '结果与依据', '有记录的结果；没有量化数据可留空'], ['boundaries', '经历边界', '哪些部分不是你做的，哪些仍未验证'],
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
  if (row.status !== 'DRAFT') return '这道题已经完成审核';
  const item = row.generation_meta?.item;
  if (!item) return '缺少生成资料，请重新读取草稿';
  if (item.question_mode === 'needs_context' || item.missing_context?.length) return '项目资料不足，请先补充真实资料并创建新任务';
  if (!row.draft_answer?.trim()) return '缺少答案，暂不能入库';
  return null;
}
export const TASK_LABELS: Record<string, string> = {READY: '等待执行', RUNNING: '正在生成', FAILED: '本次失败', REVIEW: '等待审核', COMPLETE: '已完成'};
export const ITEM_LABELS: Record<string, string> = {DRAFT:'待审核', APPROVED:'已入库', REJECTED:'已驳回', MERGED:'已合并重复'};
export const QUESTION_LABELS = {general:'通用题', project:'项目题', needs_context:'待补项目资料'};
export function messageOf(error: unknown): string { return error instanceof Error ? error.message : String(error); }

export async function beginQuestionPractice(questionId:number,requestKey:string,transport:{post:<T=any>(path:string,body?:unknown)=>Promise<T>}, experiencedBank = false) {
  if (!Number.isInteger(questionId) || questionId<=0) throw new Error('题目尚未正式入库，不能开始考试');
  return transport.post<{id:number}>('/exams',{kind:'bank_exam',question_ids:[questionId],question_count:1,duration_minutes:20,idempotency_key:requestKey,...(experiencedBank ? {experienced_bank:true} : {})});
}

export function assessedBatchQuestionIds(rows:WorkshopRow[]):number[] {
  return [...new Set(rows.filter(row=>row.status==='APPROVED' || row.status==='MERGED')
    .map(row=>row.question_id || row.merged_into).filter((id):id is number=>typeof id==='number' && Number.isInteger(id) && id>0))];
}
export async function beginProjectAssessment(rows:WorkshopRow[],requestKey:string,transport:{post:<T=any>(path:string,body?:unknown)=>Promise<T>},generationId:number) {
  const ids=assessedBatchQuestionIds(rows);
  if(!ids.length)throw new Error('请先审核入库本批次题目，再开始考核');
  if(!Number.isInteger(generationId) || generationId<=0)throw new Error('缺少本批次生成来源，请重新读取任务');
  return transport.post<{id:number}>('/exams',{kind:'bank_exam',project_generation_id:generationId,question_ids:ids,question_count:ids.length,duration_minutes:Math.min(120,Math.max(20,ids.length*10)),idempotency_key:requestKey});
}
