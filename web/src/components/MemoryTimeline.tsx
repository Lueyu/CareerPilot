import { t, useLocale } from '../i18n';
import type { ReactNode } from 'react';
import type { ProjectMemory } from '../lib/projectIntelligence';
import '../pages/memory.css';

export type MemoryEntry = ProjectMemory & { eligible_for_bank_generation?: boolean };

const TYPE_NAMES: Record<string, string> = {
  get CareerGoal() { return t('progress.memoryTimelineTypeCareerGoal'); },
  get Preference() { return t('progress.memoryTimelineTypePreference'); },
  get KnowledgeGapSignal() { return t('progress.memoryTimelineTypeKnowledgeGapSignal'); },
  get ProjectDecision() { return t('progress.memoryTimelineTypeProjectDecision'); },
  get ProjectUnderstanding() { return t('progress.memoryTimelineTypeProjectUnderstanding'); },
  get ProjectIssue() { return t('progress.memoryTimelineTypeProjectIssue'); },
  get Feedback() { return t('progress.memoryTimelineTypeFeedback'); },
  get BankReviewFeedback() { return t('progress.memoryTimelineTypeBankReviewFeedback'); },
  get LearningObservation() { return t('progress.memoryTimelineTypeLearningObservation'); },
  get LearningPreference() { return t('progress.memoryTimelineTypeLearningPreference'); },
  get TaskCompletion() { return t('progress.memoryTimelineTypeTaskCompletion'); },
  get InterviewExperience() { return t('progress.memoryTimelineTypeInterviewExperience'); },
};
const TYPE_ICONS: Record<string, string> = {
  CareerGoal: '🎯', Preference: '❤️', KnowledgeGapSignal: '🔎', ProjectDecision: '🧭',
  ProjectUnderstanding: '🗂', ProjectIssue: '📍', Feedback: '💬', BankReviewFeedback: '💬',
};

export function learningMemory(item: ProjectMemory) {
  return !!item.meta?.goal_id || ['LearningObservation', 'LearningPreference'].includes(item.mtype);
}

export function sourceName(item: ProjectMemory) {
  if (item.mtype === 'InterviewExperience') return t('progress.memoryTimelineSourceSelfReport');
  if (item.mtype === 'BankReviewFeedback') return t('progress.memoryTimelineSourceBankReview');
  if (item.meta?.daily_plan_item_key) return t('progress.memoryTimelineSourceDailyAnswer');
  if (item.meta?.exam_kind) return t('progress.memoryTimelineSourceExam');
  if (item.mtype === 'TaskCompletion') return t('progress.memoryTimelineSourceTask');
  if (learningMemory(item)) return t('progress.memoryTimelineSourceAssessment');
  if (item.source === 'DIALOG_IMPORT') return t('progress.memoryTimelineSourceImport');
  if (item.source === 'USER') return t('progress.memoryTimelineSourceUser');
  return t('progress.memoryTimelineSourceSystem');
}

function memoryStatus(item: MemoryEntry) {
  if (item.status === 'ACTIVE') {
    if (item.eligible_for_bank_generation) return { label: t('progress.memoryTimelineBankOnly'), active: true };
    if (item.mtype === 'TaskCompletion' || item.mtype === 'InterviewExperience') {
      return { label: t('progress.memoryTimelineConfirmed'), active: true };
    }
    const eligible = item.eligible_for_personalization ?? !(item.meta?.project_id || item.meta?.goal_id);
    return eligible ? { label: t('progress.memoryTimelineActive'), active: true } : { label: t('progress.memoryTimelineReconfirm'), active: false };
  }
  const label = item.status === 'SUPERSEDED' ? t('progress.memoryTimelineHistorical') : item.status === 'CANDIDATE' ? t('progress.memoryTimelinePending') : item.status;
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
  const locale = useLocale();
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
            <small>{timestamp ? new Date(timestamp).toLocaleString(locale) : ''} · {sourceName(item)}</small>
          </div>
          <p style={{whiteSpace: 'pre-wrap'}}>{item.content}</p>
          {details(item)}
        </div>
      </article>;
    })}
    {items.length === 0 && empty}
  </section>;
}
