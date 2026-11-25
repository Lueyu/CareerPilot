import { getLocale, t, useT } from '../i18n';
import { formatProgressDate } from '../lib/coreLoop';
import React, { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { DemoBanner, PageHead, Modal } from '../components/ui';
import { useApi, LearningData } from '../api/client';
import DailyLearningPlan from './DailyLearningPlan';


const TYPE_CLS: Record<string, string> = { UNDERSTAND: 'chip-blue', PRACTICE: 'chip-purple', RETEST: 'chip-amber' };
const TYPE_IC: Record<string, string> = { UNDERSTAND: '📖', PRACTICE: '🛠️', RETEST: '🔁' };
const TYPE_NAME: Record<string, string> = {
  get UNDERSTAND() { return t('progress.understanding'); },
  get PRACTICE() { return t('progress.practice'); },
  get RETEST() { return t('progress.retest'); },
};

const HELP_ITEMS = [
  { key: 'simple', ic: '💡', get label() { return t('progress.learningHelpSimplify'); }, get text() { return t('progress.learningHelpSimplifyAnswer'); } },
  { key: 'analogy', ic: '🔁', get label() { return t('progress.learningHelpAnalogy'); }, get text() { return t('progress.learningHelpAnalogyAnswer'); } },
  { key: 'stuck', ic: '🙋', get label() { return t('progress.learningHelpStillConfused'); }, get text() { return t('progress.learningHelpStillConfusedAnswer'); } },
  { key: 'flow', ic: '📊', get label() { return t('progress.learningHelpDiagram'); }, get text() { return t('progress.learningHelpDiagramAnswer'); } },
];

export default function Learning() {
  const [params] = useSearchParams();
  return params.get('plan') === 'history' ? <LearningHistory /> : <DailyLearningPlan />;
}

function LearningHistory() {
  const t = useT();
  const navigate = useNavigate();
  const [helpOpen, setHelpOpen] = useState(false);
  const [helpItem, setHelpItem] = useState<(typeof HELP_ITEMS)[number] | null>(null);
  const { data, loading, error } = useApi<LearningData>('/learning');

  const today = (data?.today ?? []).map(t => ({ ...t, domain: t.skill_name || t.skill || '' }));
  const retest = (data?.retest_plan ?? []).map(r => ({ day: r.due_in_days == null ? t('progress.learningNotScheduled') : r.due_in_days <= 0 ? t('progress.due') : r.due_in_days === 1 ? t('progress.learningDueTomorrow') : t('progress.learningDueIn', { days: r.due_in_days }), date: formatProgressDate(r.due_date) || t('progress.learningToBeDetermined'), tasks: [r.title], id: r.id, due: r.due_in_days != null && r.due_in_days <= 0 }));

  return (
    <div>
      <PageHead
        title={t('progress.learningHeading')}
        sub={t('progress.learningDescription')}
      >
        <Link className="btn btn-outline" to="/learning">{t('progress.backToPlan')}</Link>
        <button className="btn btn-outline" onClick={() => navigate('/profile')}>{t('progress.learningViewProfile')}</button>
        <button className="btn btn-primary" onClick={() => navigate('/exam/diagnosis')}>{t('progress.learningAssess')}</button>
      </PageHead>
      <DemoBanner />

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>{t('progress.loadingLive')}</div>}
      {error && <div className="demo-banner demo-banner-err" style={{ marginBottom: 12 }}>{t('progress.learningLoadError', { error })}</div>}

      <div className="card-head" style={{ margin: '0 0 10px' }}>
        <span className="card-title">{t('progress.learningToday') + ' '}<span className="chip chip-purple" style={{ marginLeft: 6 }}>{today.length}</span></span>
      </div>
      <div className="grid grid-3" style={{ marginBottom: 22 }}>
        {!loading && !error && today.length === 0 && <div className="card">{t('progress.learningNoTasks')}</div>}
        {today.map((task) => (
          <div className="task-card" key={task.id}>
            <div className="task-head">
              <div className="exam-ic" style={{ width: 36, height: 36, fontSize: 16 }}>{TYPE_IC[task.kind] || '📖'}</div>
              <span className={`chip ${TYPE_CLS[task.kind] || 'chip-slate'}`}>{TYPE_NAME[task.kind] || task.kind}</span>
            </div>
            <div className="task-title" style={{ marginTop: 10 }}>{task.title}</div>
            {task.why && <div className="task-why">{t('progress.learningWhy')}<b>{task.why}</b></div>}
            <div className="task-meta">
              <span>{t('progress.learningEstimatedTime', { minutes: task.est_minutes })}</span>
              {task.domain && <><span className="sep" /><span>{t('progress.learningDomain')}{task.domain}</span></>}
            </div>
            {task.acceptance && (
              <div className="hint" style={{ margin: '8px 0 12px' }}>
                <b>{t('progress.learningCriteria')}</b>{task.acceptance}
              </div>
            )}
            <button className="btn btn-primary" style={{ width: '100%' }} onClick={() => navigate(`/learning/task/${task.id}`)}>
              {task.kind === 'RETEST' ? t('progress.learningStartRetest') : t('progress.learningStartTask')}
            </button>
          </div>
        ))}
      </div>

      {!!data?.completed?.length && <div className="card" style={{ marginBottom: 18 }}>
        <div className="card-head"><span className="card-title">{t('progress.learningHistory')}</span><span className="card-sub">{t('progress.learningProgressScope')}</span></div>
        {data.completed.map(task => <div className="list-row" key={task.id}>
          <div className="list-main"><div className="list-title">{task.title}</div><div className="list-sub">{t('progress.learningRecordedProgress')}</div></div>
          <button className="btn btn-outline" onClick={() => navigate(`/learning/task/${task.id}`)}>{task.kind === 'RETEST' ? t('progress.learningViewRetest') : t('progress.learningViewTask')}</button>
        </div>)}
      </div>}

      <div className="grid grid-1-2">
        <div className="card">
          <div className="card-head">
            <span className="card-title">{t('progress.learningSchedule')}</span>
            <span className="card-sub">{t('progress.learningIntervals')}</span>
          </div>
          {!loading && !error && retest.length === 0 && <p className="hint">{t('progress.learningNoRetests')}</p>}
          {retest.map((r: any) => (
            <div className="list-row" key={r.id || r.day}>
              <div className="list-ic">📅</div>
              <div className="list-main">
                <div className="list-title">{r.day} · {r.date}</div>
                <div className="list-sub">{r.tasks.join(getLocale() === 'zh-CN' ? '、' : ', ')}</div>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/learning/task/${r.id}`)}>{r.due ? t('progress.learningTakeRetest') : t('progress.learningViewSchedule')}</button>
            </div>
          ))}
        </div>

        <div className="card">
          <div className="card-head">
            <span className="card-title">{t('progress.learningTips')}</span>
            <span className="card-sub">{t('progress.learningExample')}</span>
          </div>
          <div className="list-row">
            <div className="list-ic">💡</div>
            <div className="list-main">
              <div className="list-title">{t('progress.learningUseReports')}</div>
              <div className="list-sub">{t('progress.learningPrioritize')}</div>
            </div>
          </div>
          <div className="list-row">
            <div className="list-ic">🎯</div>
            <div className="list-main">
              <div className="list-title">{t('progress.learningWhyRetest')}</div>
              <div className="list-sub">{t('progress.learningRetestExplanation')}</div>
            </div>
          </div>
        </div>
      </div>

      {/* 学习帮助浮动按钮 */}
      <div className="help-fab" onClick={() => setHelpOpen(!helpOpen)} title={t('progress.learningHelp')}>
        ❓
      </div>
      {helpOpen && (
        <div className="help-menu">
          <div className="help-menu-head">
            <b>{t('progress.learningNeedHelp')}</b>
            <button className="modal-x" aria-label={t('progress.close')} onClick={() => setHelpOpen(false)}>✕</button>
          </div>
          {HELP_ITEMS.map((h) => (
            <div
              className="pop-item"
              key={h.key}
              onClick={() => {
                setHelpItem(h);
                setHelpOpen(false);
              }}
            >
              <span className="pop-item-ic">{h.ic}</span>
              <div className="pop-item-main">
                <div className="pop-item-title">{h.label}</div>
              </div>
              <span className="pop-item-arrow">→</span>
            </div>
          ))}
        </div>
      )}

      {helpItem && (
        <Modal title={helpItem.label} onClose={() => setHelpItem(null)} size="sm">
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.8 }}>{helpItem.text}</p>
        </Modal>
      )}
    </div>
  );
}
