import type { ReactNode } from 'react';
import type { ProjectMemory } from '../lib/projectIntelligence';
import '../pages/memory.css';

export type MemoryEntry = ProjectMemory & { eligible_for_bank_generation?: boolean };

const TYPE_NAMES: Record<string, string> = {
  CareerGoal: '职业目标', Preference: '偏好', KnowledgeGapSignal: '待诊断线索',
  ProjectDecision: '项目决策', ProjectUnderstanding: '项目理解', ProjectIssue: '项目问题',
  Feedback: '反馈', BankReviewFeedback: '题库审核反馈', LearningObservation: '学习观察',
  LearningPreference: '局部教学观察', TaskCompletion: '学习进度', InterviewExperience: '项目经历',
};
const TYPE_ICONS: Record<string, string> = {
  CareerGoal: '🎯', Preference: '❤️', KnowledgeGapSignal: '🔎', ProjectDecision: '🧭',
  ProjectUnderstanding: '🗂', ProjectIssue: '📍', Feedback: '💬', BankReviewFeedback: '💬',
};

export function learningMemory(item: ProjectMemory) {
  return !!item.meta?.goal_id || ['LearningObservation', 'LearningPreference'].includes(item.mtype);
}

export function sourceName(item: ProjectMemory) {
  if (item.mtype === 'InterviewExperience') return '本人陈述';
  if (item.mtype === 'BankReviewFeedback') return '题库审核';
  if (item.meta?.daily_plan_item_key) return '每日计划作答';
  if (item.meta?.exam_kind) return '考试作答';
  if (item.mtype === 'TaskCompletion') return '学习任务完成';
  if (learningMemory(item)) return '作答与评价';
  if (item.source === 'DIALOG_IMPORT') return '对话导入';
  if (item.source === 'USER') return '用户纠正';
  return '系统记录';
}

function memoryStatus(item: MemoryEntry) {
  if (item.status === 'ACTIVE') {
    if (item.eligible_for_bank_generation) return { label: '仅题库补题使用', active: true };
    if (item.mtype === 'TaskCompletion' || item.mtype === 'InterviewExperience') {
      return { label: '已确认', active: true };
    }
    const eligible = item.eligible_for_personalization ?? !(item.meta?.project_id || item.meta?.goal_id);
    return eligible ? { label: '当前有效', active: true } : { label: '需要重新确认', active: false };
  }
  const label = item.status === 'SUPERSEDED' ? '历史版本' : item.status === 'CANDIDATE' ? '待确认' : item.status;
  return { label, active: false };
}

export default function MemoryTimeline({
  items, title, summary, filters, empty, details,
}: {
  items: MemoryEntry[];
  title: string;
  summary: string;
  filters?: ReactNode;
  empty?: ReactNode;
  details: (item: MemoryEntry) => ReactNode;
}) {
  return <section className="card memory-timeline" aria-label={title}>
    <div className="card-head">
      <span className="card-title">{title}</span>
      <span className="card-sub">{summary}</span>
    </div>
    {filters}
    {items.map(item => {
      const status = memoryStatus(item);
      const timestamp = item.mtype === 'InterviewExperience' ? item.confirmed_at : item.created_at;
      return <article className="mem-item" key={item.id}>
        <div className="mem-ic">{TYPE_ICONS[item.mtype] || '📌'}</div>
        <div className="mem-body">
          <div className="q-meta">
            <span className="chip chip-slate">{TYPE_NAMES[item.mtype] || item.mtype}</span>
            <span className={'chip ' + (status.active ? 'chip-green' : 'chip-slate')}>{status.label}</span>
            <small>{timestamp ? new Date(timestamp).toLocaleString() : ''} · {sourceName(item)}</small>
          </div>
          <p style={{whiteSpace: 'pre-wrap'}}>{item.content}</p>
          {details(item)}
        </div>
      </article>;
    })}
    {items.length === 0 && empty}
  </section>;
}
