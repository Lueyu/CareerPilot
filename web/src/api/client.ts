// CareerPilot API client — 后端 FastAPI (127.0.0.1:8000)
// 认证: X-Api-Token; 工作空间: X-Workspace (默认 demo)

const BASE = (import.meta as any).env?.VITE_API_BASE || 'http://127.0.0.1:8000/api/v1';
const TOKEN = (import.meta as any).env?.VITE_API_TOKEN || 'cp-dev-token';
const WS = (import.meta as any).env?.VITE_WORKSPACE || 'demo';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      'X-Api-Token': TOKEN,
      'X-Workspace': WS,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j).slice(0, 200);
    } catch { /* ignore */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

/** multipart 上传(文件): 不设 Content-Type, 交给浏览器带 boundary。 */
async function reqForm<T = any>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'X-Api-Token': TOKEN, 'X-Workspace': WS },
    body: form,
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j).slice(0, 200);
    } catch { /* ignore */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T = any>(path: string) => req<T>('GET', path),
  post: <T = any>(path: string, body?: unknown) => req<T>('POST', path, body ?? {}),
  patch: <T = any>(path: string, body?: unknown) => req<T>('PATCH', path, body ?? {}),
  put: <T = any>(path: string, body?: unknown) => req<T>('PUT', path, body ?? {}),
  del: <T = any>(path: string, body?: unknown) => req<T>('DELETE', path, body),
  upload: <T = any>(path: string, form: FormData) => reqForm<T>(path, form),
};

// ---------- 类型(与后端响应对应, 仅声明用到字段) ----------

export interface DashboardData {
  workspace: { id: number; slug: string; name: string; target_role: string; target_level: string; is_demo: boolean };
  stats: { evidence_total: number; evidence_delta?: number; pending_skills: number; today_tasks: number; pending_imports: number };
  gap: number | null;
  benchmark?: { version?: string; jd_sample_count?: number; is_seed?: boolean };
  domains: {
    key: string; name: string; weight_pct: number; target_level: number | null;
    level: number | null; status: string; confidence: number | null; evidence_coverage: number; next?: string;
  }[];
  today_tasks: { id: number; title: string; kind: string; est_minutes: number; skill_name?: string | null; status: string }[];
  pending_imports: { id: number; short_code: string; items?: number; created_at?: string }[];
  recent_activity: { id: number; title: string; kind: string; status?: string; score?: number | null; completed_at?: string | null }[];
}

export interface BenchmarkData {
  standard: { role: string; level: string; version: string; jd_sample_count: number; is_seed: boolean; note?: string };
  rows: {
    skill_key: string; skill: string; weight_pct: number; target_level: number;
    current_level: number | null; status: string; evidence_coverage: number;
    confidence: number | null; next_step: string;
  }[];
}

export interface ProfileData {
  overall: { level: number | null; confidence: number | null; evidence_total: number };
  focus?: { key: string; name: string; level: number | null };
  domains: {
    key: string; name: string; level: number | null; status: string; confidence: number | null;
    evidence_coverage: number; weight_pct?: number; dimension_scores?: Record<string, number>;
    last_assessed_at?: string | null;
  }[];
  evidence: EvidenceItem[];
}

export interface ProfileDetailData {
  skill: { key: string; name: string; description?: string };
  state: {
    level: number | null; confidence: number | null; status: string; evidence_coverage: number;
    dimension_scores: Record<string, number>; last_assessed_at?: string | null;
  };
  evidence: EvidenceItem[];
}

export interface EvidenceItem {
  id?: number; short_code?: string; kind: string; title: string; detail?: string | null;
  grade: string; support?: number; occurred_at?: string | null; skill?: string | null;
}

export interface ExamQuestionView {
  order_no: number; short_code: string; title: string; qtype: string;
  options: string[] | null; difficulty?: number; skill: string | null;
  rubric_visible?: boolean;
  answer?: { choice_index?: number | null; text_answer?: string | null; transcript?: string | null } | null;
}

export interface ExamDetail {
  id: number; title: string; kind: string; status: string; duration_minutes: number;
  selection_reason?: string | null; started_at?: string | null;
  questions: ExamQuestionView[];
}

export interface ExamReportData {
  id: number; title: string; status: string; score: number;
  total_points: number; gained_points: number;
  selection_reason?: string | null;
  summary?: { score?: number; verdicts?: { order_no: number; title: string; verdict: string }[] } | null;
  completed_at?: string | null;
  questions: {
    order_no: number; short_code: string; title: string; qtype: string; options: string[] | null;
    skill: string | null; reference_answer?: string | null; frozen?: any;
    answer?: {
      choice_index?: number | null; text_answer?: string | null; points: number; max_points: number;
      verdict: string; feedback: { hit_points: { title: string; keywords: string[] }[]; missed_points: { title: string; keywords: string[] }[]; advice?: string; next?: string };
      evidence_refs?: string[] | null;
    } | null;
  }[];
}

export interface ExamsData {
  kinds: { kind: string; title: string; desc?: string; default_minutes: number; history_count: number }[];
  history: {
    id: number; title: string; kind: string; status: string; question_count?: number;
    score: number | null; started_at?: string | null; completed_at?: string | null;
  }[];
}

export interface LearningData {
  today: LearningTaskView[];
  retest_plan: { id: number; title: string; round: number; due_date: string; due_in_days: number; skill_name?: string | null }[];
  stats?: { total?: number; done?: number; pending?: number };
}

export interface LearningTaskView {
  id: number; title: string; kind: string; why?: string | null; why_ref?: string | null;
  est_minutes: number; acceptance?: string | null; status: string;
  skill_name?: string | null; due_date?: string | null; retest_round?: number;
  content?: { steps?: string[]; principles?: string[]; verify?: string } | null;
}

export interface BankData {
  total: number;
  items: {
    id: number; short_code: string; title: string; qtype: string; topic: string;
    difficulty: number; company?: string | null; source_type: string;
    skill_key?: string | null; skill?: string | null; tags?: string[]; status?: string;
  }[];
  facets?: Record<string, Record<string, number>>;
}

export interface MemoryData {
  items: { id: number; mtype: string; content: string; source: string; source_ref?: string | null; extract_confidence?: number | null; status: string; created_at: string; confirmed_at?: string | null }[];
}

export interface ObservatoryData {
  stats: { total_runs: number; success_rate: number; avg_latency_ms: number; cost_cny: number; failures: number };
  engines: {
    key: string; name: string; desc?: string; trace_kind?: string; health: string; health_note?: string;
    runs?: number;
    last_run?: { short_code: string; title: string; status: string; finished_at: string } | string | null;
  }[];
  recent_failures: { short_code: string; title: string; fail_reason?: string | null; finished_at?: string | null }[];
}

export interface TracesData {
  items: {
    id: number; short_code: string; kind: string; title: string; status: string;
    duration_ms: number; cost_cny?: number; fail_reason?: string | null;
    started_at?: string | null; span_count?: number;
    spans?: { id: number; name: string; status: string; duration_ms: number; cost_cny?: number | null; detail?: any }[];
  }[];
}

export interface ImportFileMeta {
  name: string; suffix: string; ok: boolean; chars: number; error?: string; warning?: string;
}

export interface ImportReview {
  id: number; short_code: string; status: string;
  meta?: { source?: string; chars?: number; files?: ImportFileMeta[]; company?: string | null; level?: string | null };
  items: {
    id: number; title: string; extract_confidence: number; suggested_skill_key: string;
    draft_answer?: string | null; draft_rubric?: any; is_duplicate_of?: number | null; status: string;
    has_answer?: boolean; answer_source?: string | null;
  }[];
}

export interface QuestionDetail {
  id: number; short_code: string; title: string; qtype: string; topic: string;
  skill?: string | null; difficulty: number; options?: string[] | null;
  answer_html?: string | null; rubric?: any;
  company?: string | null; role?: string | null; level?: string | null;
  occurred_on?: string | null; source_type: string; tags?: string[]; keywords?: string[];
}

// ---------- 设置与工作空间 ----------

export interface SettingsOverview {
  account: { display_name: string; email: string };
  workspace: {
    id: number; name: string; slug: string; target_role: string; target_level: string;
    is_demo: boolean; created_at: string;
  };
  members: { display_name: string; email: string; role: string }[];
  counts: {
    evidence: number; memory: number; tasks: number; questions: number;
    imports_review: number; exams: number; drafts: number; publications: number;
  };
  connections: {
    llm: { active: boolean; provider: string | null; model: string | null };
    github_publish: {
      active: boolean; target_repo: string; base_branch: string;
      content_dir: string; auto_merge_default: boolean;
    };
  };
}

export interface PublicationRule {
  id: number; key: string; value: string; is_enabled: boolean; description?: string | null;
}

export interface AuditItem {
  id: number; action: string; draft_id?: number | null; publication_id?: number | null;
  detail?: any; created_at: string;
}

// ---------- hooks ----------

import { useEffect, useState } from 'react';

/** 拉取 API 数据; 失败时 fallback 到传入的静态数据(前端仍可演示)。 */
export function useApi<T>(path: string | null, fallback?: T): { data: T | undefined; loading: boolean; error: string | null } {
  const [data, setData] = useState<T | undefined>(undefined);
  const [loading, setLoading] = useState(!!path);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    setLoading(true);
    api.get<T>(path)
      .then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e) => { if (alive) { setError(String(e.message || e)); if (fallback !== undefined) setData(fallback); } })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);
  return { data: data ?? fallback, loading, error };
}
