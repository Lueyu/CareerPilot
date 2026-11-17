export interface LearningAssistance {
  hint_used:boolean;reference_viewed:boolean;ai_declared:boolean;feedback_seen:boolean;
  events?:{kind:string;request_key?:string;at?:string}[];
}
export interface LearningFeedback {
  correct:{criterion_id:string;quotes:string[];why:string}[];
  gaps:{criterion_id:string;kind:string;quotes:string[];why:string;minimal_hint?:string}[];
  next_action:string;scope_note?:string;versions?:Record<string,string>;
  project_feedback?:{test_verification:string;understanding:string;scope_note?:string;defense_questions?:string[];acceptance_version?:string}|null;
}
export interface LearningCriterion {criterion_id?:string;title?:string;detail?:string;required_evidence?:string[];score_anchors?:{anchor_id:string;score:number;description:string}[]}
export interface LearningMemoryCandidate {id:number;revision:number;status:string;freshness:string;content:string;eligible_for_personalization:boolean;confirmed_at?:string|null;meta?:{goal_id?:number;source_task_id?:number;submission_id?:number;submission_revision?:number;scoring_run_id?:number;origin_refs?:Record<string,unknown>;[key:string]:unknown}}
export interface LearningSubmission {
  id:number;activity_id:number;revision:number;parent_submission_id?:number|null;status:string;
  scoring_run_id?:number|null;exam_id?:number;assistance:LearningAssistance;has_feedback?:boolean;
  feedback?:LearningFeedback|null;can_retry:boolean;error?:{code?:string;message:string}|string|null;
  payload?:{text_answer?:string;answer_text?:string;ai_declared?:boolean;test_evidence?:{execution?:string;user_log_supplied?:boolean;ci_verified?:boolean}};created_at?:string;
}
export interface LearningActivity {
  id:number;kind:'CONCEPT'|'PROJECT';title:string;status:string;position:number;revision:number;
  content_version?:string;authoring_source?:string;prompt:string;required_output?:string;success_criteria?:(string|LearningCriterion)[];
  allowed_actions:string[];assistance:LearningAssistance;latest_submission?:LearningSubmission|null;
  content?:Record<string,unknown>;
  project_contract?:{project_id:number;analysis_id:number;baseline_revision:string;allowed_paths:string[];acceptance:string[]|string;kind:string;version:string;limits?:Record<string,unknown>};
}
export interface LearningPathData {
  task_id:number;task_status?:string;
  goal:{id:number;target_behavior:string;status:string;evidence_status:string;plan_revision:number;
    success_criteria_version?:string;origin_refs?:Record<string,unknown>;allowed_actions:string[];
    verification:{understanding:string;practice:string;transfer:string;retention:string}}|null;
  activities:LearningActivity[];submissions:LearningSubmission[];
  learning_content?:{analogy?:string;analogy_limits?:string;example?:string;principles?:string[];source?:string}|null;
  candidates?:unknown[];plan_diff?:unknown;project_options?:unknown[];worker?:{online:boolean};
  memory_candidates?:LearningMemoryCandidate[];plan_history?:{revision:number;reason:string;changes:{activity_id?:number;from:string;to:string}[];at?:string}[];
  next_action?:{kind:string;message:string}|null;source_memories?:{id:number;revision:number;content:string}[];evolution_candidates?:{id:number;title:string;status:string;reason:string}[];
}
export interface LearningSubmissionAck {submission_id:number;exam_id?:number;scoring_run_id?:number;status:string;feedback_url?:string}
export const LEARNING_PENDING=new Set(['QUEUED','RUNNING','RETRY_WAIT','SCORING','SCORING_RETRY_WAIT','SUBMITTED']);
export const LEARNING_RUN_LABELS:Record<string,string>={QUEUED:'已保存，等待后台检查',RUNNING:'后台检查中',RETRY_WAIT:'等待后台重试',SCORING:'后台检查中',SCORING_RETRY_WAIT:'等待后台重试',SUBMITTED:'已提交，等待反馈',COMPLETED:'本次检查完成',FAILED:'本次检查失败',SCORING_FAILED:'本次检查失败',NEEDS_REVIEW:'本次结果需要复核',BLOCKED:'等待外部条件'};
export function learningError(error:unknown):string {
  if(typeof error==='string')return error;
  if(error && typeof error==='object' && 'message' in error)return String(error.message);
  return '无法完成本次操作，请稍后重试';
}
