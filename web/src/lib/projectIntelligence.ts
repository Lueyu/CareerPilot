import {t} from '../i18n';
export class ProjectCopyError extends Error {
  constructor(readonly localeKey: string) { super(); }
}

export interface SourceCitation { path: string; start_line: number; end_line: number;source_id?:string;source_version?:string }
export interface SourceExcerpt extends SourceCitation { excerpt: string; excerpt_truncated: boolean }
export interface ProjectIssue {
  id: string; title: string; skill_key: string; severity: 'low'|'medium'|'high';
  claim_id?: string;
  detail: string; impact: string; recommendation: string; status: 'observed'|'hypothesis';
  citations: SourceCitation[]; interview_questions: string[];
}
export interface ProjectAnalysisReport {
  schema_version: number; summary: string;
  architecture: {title: string; detail: string; citations: SourceCitation[];fact_type?:string}[];
  issues: ProjectIssue[]; unknowns: string[];
  sections?: {key: string; title: string; body: string; claim_ids: string[]}[];
  claims?: {id:string;title:string;text:string;kind:string;citations:SourceCitation[]}[];
  changes?: string[]|string;
}
export interface AnalysisCoverage {
  files_read?: number; eligible_files?: number; tree_truncated?: boolean;
  unread_files?: string[]; truncated_files?: string[]; failed_files?: string[];
  limitations?: string[];
}
export interface ProjectAnalysisRun {
  id: number; project_id: number; status: 'READY'|'RUNNING'|'SUCCESS'|'FAILED';
  focus?: string; source_revision?: string|null; report?: ProjectAnalysisReport|null;
  error?: string|null; coverage?: AnalysisCoverage|null; result_meta?: Record<string, unknown>;
  created_at?: string; finished_at?: string|null; memory_ids?: number[]; active_issue_ids?: string[];
  generations?: {id: number; status: string; issue_ids: string[]; question_ids: number[]; assessments: {id: number; status: string; score?: number|null}[]}[];
  issue_memories?: {issue_id: string; memory_id: number; content: string; user_corrected: boolean; source_revision: string}[];
  source_excerpts?: SourceExcerpt[];
  is_latest_success?: boolean;
  report_status?:string;coverage_status?:string;
}
export interface ScanCoverage {
  domains: {key: string; path_matches: number; content_matches: number; readme_claims: number}[];
  files_total?: number|null; files_sampled?: number|null; files_read?: number|null;
  files_failed?: number|null; files_truncated?: number|null; tree_truncated?: boolean;
  history_unknown?: boolean; limitations?: string[];
}
export interface ProjectMemory {
  id: number; mtype: string; content: string; source: string; source_ref?: string|null;
  extract_confidence?: number|null; status: string; created_at: string; confirmed_at?: string|null;
  superseded_by?: number|null; revision?:number;
  freshness?: string; eligible_for_personalization?: boolean; ineligible_reason?: string|null;
  meta?: {project_id?: number; analysis_id?: number; source_revision?: string; kind?: string; issue_id?: string; citations?: SourceCitation[];goal_id?:number;source_task_id?:number;completed_at?:string;submission_id?:number;scoring_run_id?:number;exam_id?:number;exam_kind?:string;daily_plan_date?:string;daily_plan_item_key?:string}|null;
}
export type AutopilotRunStatus='WAITING_WORKER'|'QUEUED'|'RUNNING'|'RETRY_WAIT'|'COMPLETED'|'PAUSED_USER'|'PAUSED_BUDGET'|'BLOCKED'|'CANCELLED'|'FAILED_FINAL';
export interface AutopilotRun {
  id:number; project_id:number; status:AutopilotRunStatus; stage?:string|null; coverage_status:string;
  completed_modules:number; total_modules:number; error?:{code:string;message:string;recoverable:boolean}|null;
  allowed_actions:string[]; created_at?:string; updated_at?:string; report_id?:number|null;
}
export interface WorkerStatus {online:boolean;last_seen_at?:string|null}
export interface AutopilotReport {
  id:number;run_id:number|null;as_of:string|null;source_revision:string|null;coverage_status:string;
  is_latest_valid:boolean;freshness:string;report:ProjectAnalysisReport;
  source_excerpts?:SourceExcerpt[];active_issue_ids:string[];issue_memories?:ProjectAnalysisRun['issue_memories'];
  generations?:ProjectAnalysisRun['generations'];coverage?:AnalysisCoverage;
}
export interface ProjectReportResponse {
  project_id:number;latest_run:AutopilotRun|null;report:AutopilotReport|null;
  project?:{id:number;name:string;owner:string;repo:string;kind:string;archived_at:string|null};
  partial_preview?:{summary?:string;modules?:{title?:string;summary?:string;name?:string}[]}|null;
  worker:WorkerStatus;allowed_actions:string[];
}
export interface AutopilotSummary {
  user_status:string;enabled:boolean;consent_required:boolean;worker:WorkerStatus;latest_run:AutopilotRun|null;
  latest_valid_report:{id:number;as_of:string|null;summary:string;source_revision:string|null;coverage_status:string;freshness:string}|null;
  pending_candidate_count:number;allowed_actions:string[];
}
export const PROJECT_SOURCE_NOTICE = '启用后，本空间已导入但尚无报告的仓库，以及以后新导入的仓库，会逐个自动分析；必要源码片段将发送给你当前配置的模型服务。不会执行仓库代码；报告自动保存，长期记忆需你确认。默认不限累计调用次数和读取字节；如设置单次分析预算，达到上限时暂停。';

export function projectProviderName(settings?: AutopilotSettings) {
  const name = settings?.provider_display_name;
  return name === '尚未配置模型' ? t('projects.noModelConfigured') : name;
}

export interface AutopilotSettings {
  enabled:boolean;consent_version:string;consented_at:string|null;source_notice:string;provider_display_name:string;
  budget:{max_model_calls?:number|null;max_source_bytes?:number|null};check_interval_hours:number;
}
export interface MemoryCandidate {
  id:number;revision:number;status:string;report_id:number;project_id:number;
  freshness?:string;
  base_memory_id:number|null;base_memory_revision:number|null;kind:string;fact_type:string;content:string;
  citations:SourceCitation[];source_excerpts?:SourceExcerpt[];change_reason:string;suggested_use:string;
}
export const PROJECT_PROFILE_COPY = {
  background: ['projects.profileBackground', 'projects.profileBackgroundHint'],
  role: ['projects.profileRole', 'projects.profileRoleHint'],
  responsibilities: ['projects.profileResponsibilities', 'projects.profileResponsibilitiesHint'],
  stack: ['projects.profileStack', 'projects.profileStackHint'],
  decisions: ['projects.profileDecisions', 'projects.profileDecisionsHint'],
  challenges: ['projects.profileChallenges', 'projects.profileChallengesHint'],
  outcomes: ['projects.profileOutcomes', 'projects.profileOutcomesHint'],
  boundaries: ['projects.profileBoundaries', 'projects.profileBoundariesHint'],
} as const;
export function issueWorkshopPath(projectId: number, analysisId: number, issueIds: string[]) {
  const query=new URLSearchParams({project:String(projectId),analysis:String(analysisId),issues:issueIds.join(',')});
  return `/bank/workshop?${query}`;
}
