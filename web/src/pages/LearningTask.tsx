import { t, useT } from '../i18n';
import StudyEntryButton from '../components/StudyEntryButton';
import { formatProgressDate } from '../lib/coreLoop';
import React, { useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useAppState } from '../components/AppState';
import { DemoBanner, Modal, PageHead } from '../components/ui';
import { api, useApi, LearningTaskView, RetestGoalOutcome, TaskContentResp, TaskHelpResp } from '../api/client';
import LearningPathPanel from '../components/LearningPathPanel';


const TYPE_NAME: Record<string, string> = {
  get UNDERSTAND() { return t('progress.understanding'); },
  get PRACTICE() { return t('progress.practice'); },
  get RETEST() { return t('progress.retest'); },
};
const GOAL_RESULT: Record<RetestGoalOutcome['status'], string> = {
  get UNMAPPED() { return t('progress.learningTaskNoReviewedMapping'); },
  get UNVERIFIED() { return t('progress.learningTaskPendingVerification'); },
  get ASSISTED() { return t('progress.learningTaskAssistedVerification'); },
  get PASSED() { return t('progress.pass'); },
  get FAILED() { return t('progress.fail'); },
};

// —— v3.1 M4: 统一 5 步教学流(类比→小例子→原理→结合项目→验证) ——
interface FiveStep {
  analogy: string;
  example: string;
  principles: string[];
  practice: string[];
  verify: string;
}

// 5 步标题与副标题
const STEP_META = [
  { key: 'analogy', get title() { return t('progress.learningTaskStepAnalogy'); }, get sub() { return t('progress.learningTaskStepAnalogyNote'); }, ic: '💡' },
  { key: 'example', get title() { return t('progress.learningTaskStepExample'); }, get sub() { return t('progress.learningTaskStepExampleNote'); }, ic: '🧪' },
  { key: 'principles', get title() { return t('progress.learningTaskStepPrinciples'); }, get sub() { return t('progress.learningTaskStepPrinciplesNote'); }, ic: '📖' },
  { key: 'practice', get title() { return t('progress.learningTaskStepProject'); }, get sub() { return t('progress.learningTaskStepProjectNote'); }, ic: '🛠️' },
  { key: 'verify', get title() { return t('progress.learningTaskStepRecord'); }, get sub() { return t('progress.learningTaskStepRecordNote'); }, ic: '✅' },
];

// 每步帮助来自当前任务的后端内容

export default function LearningTask() {
  const t = useT();
  const {id}=useParams();const [params]=useSearchParams();const navigate=useNavigate();
  const {data:task,error,loading}=useApi<LearningTaskView>(id?`/learning/tasks/${id}`:null);
  if(params.get('legacy')==='1' || task?.kind==='RETEST')return <LegacyLearningTask/>;
  if(!task || String(task.id)!==id)return <div><PageHead title={t('progress.learningPath')}><button className="btn btn-outline" onClick={()=>navigate('/learning')}>{t('progress.backToPlan')}</button></PageHead><div className="card" role={error?'alert':undefined}>{error?t('progress.learningTaskLoadError', { error: error }):loading?t('progress.learningTaskLoading'):t('progress.learningTaskNoTask')}</div></div>;
  return <div><PageHead title={task.title} sub={t('progress.learningTaskPathDescription')}><StudyEntryButton kind="task" id={task.id}/><button className="btn btn-outline" onClick={()=>navigate('/learning')}>{t('progress.backToPlan')}</button></PageHead><LearningPathPanel key={task.id} task={task}/><details className="card"><summary>{t('progress.learningTaskOriginalTask')}</summary><p>{t('progress.learningTaskSpecificGap')}{task.source_criterion || t('progress.learningTaskLegacyGap')}</p>{task.content?.observation?.answer_excerpt && <blockquote>{task.content.observation.answer_excerpt}</blockquote>}{task.source_exam_id && <button className="btn btn-outline btn-sm" onClick={()=>navigate(`/exam/diagnosis/report?id=${task.source_exam_id}`)}>{t('progress.learningTaskOriginalEvidence')}</button>}<p className="hint">{t('progress.learningTaskOriginalLessonNote')}</p><button className="btn btn-outline" onClick={()=>navigate(`/learning/task/${task.id}?legacy=1`)}>{t('progress.learningTaskOriginalLesson')}</button></details></div>;
}

function LegacyLearningTask() {
  const t = useT();
  const { id } = useParams();
  const navigate = useNavigate();
  const { toast } = useAppState();
  const [task, setTask] = useState<LearningTaskView | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [artifact, setArtifact] = useState('');
  const [stepIdx, setStepIdx] = useState(0);
  const [helpKey, setHelpKey] = useState<string | null>(null);
  const [stepDone, setStepDone] = useState<boolean[]>([false, false, false, false, false]);
  // M-D: 真实教学内容(LLM/规则生成) + 求助接口
  const [gen, setGen] = useState<LearningTaskView['content'] | null>(null);
  const [contentBusy, setContentBusy] = useState(false);
  const [contentErr, setContentErr] = useState<string | null>(null);
  const [helpText, setHelpText] = useState<string | null>(null);
  const [helpSrc, setHelpSrc] = useState<string>('');
  const [helpBusy, setHelpBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    setTask(null); setGen(null); setLoadErr(null); setStepDone([false, false, false, false, false]); setStepIdx(0); setArtifact('');
    api.get<LearningTaskView>(`/learning/tasks/${id}`)
      .then((t) => { if (alive) { setTask(t); setArtifact(t.content?.artifact_text || ''); } })
      .catch((e) => { if (alive) setLoadErr(String(e.message || e)); });
    return () => { alive = false; };
  }, [id]);

  // 教学内容: 已缓存(任务 detail 带 analogy)直接用; 否则调生成接口(LLM/规则)
  useEffect(() => {
    const c = task?.content;
    if (c?.analogy && (c.source === 'llm' || c.source === 'rule') &&
        (!task?.source_exam_question_id || c.source_context_version === 2)) {
      setGen(c);
      return;
    }
    if (!task || task.kind === 'RETEST') return;
    let alive = true;
    setContentBusy(true);
    setContentErr(null);
    api.get<TaskContentResp>(`/learning/tasks/${task.id}/content`)
      .then((r) => { if (alive) setGen(r.content); })
      .catch((e: any) => { if (alive) setContentErr(String(e.message || e)); })
      .finally(() => { if (alive) setContentBusy(false); });
    return () => { alive = false; };
  }, [task]);

  const doHelp = async (mode: string) => {
    if (!task) return;
    // 缓存命中直接展示
    const validTaskCache = !task.source_exam_question_id || task.content?.source_context_version === 2;
    const cached = [gen?.help_cache?.[mode], validTaskCache ? task.content?.help_cache?.[mode] : undefined]
      .find((entry) => entry?.source === 'llm' && entry.text);

    if (cached?.text) {
      setHelpText(cached.text);
      setHelpSrc(cached.source);
      setHelpKey(mode);
      return;
    }
    setHelpKey(mode);
    setHelpBusy(true);
    setHelpText(null);
    setHelpSrc('');
    try {
      const r = await api.post<TaskHelpResp>(`/learning/tasks/${task.id}/help`, { mode });
      if (r.error) {
        setHelpText(r.error);
        setHelpSrc('none');
      } else {
        setHelpText(r.text || t('progress.learningTaskHelpEmpty'));
        setHelpSrc(r.source);
      }
    } catch (e: any) {
      setHelpText(t('progress.learningTaskHelpError', { error: e.message || e }));
      setHelpSrc('none');
    } finally {
      setHelpBusy(false);
    }
  };

  const title = task?.title || t('progress.learningTask');
  const kind = task?.kind || '';
  const minutes = task?.est_minutes ?? '—';
  const domain = task?.skill_name || task?.skill;
  const why = task?.why;
  const acceptance = task?.acceptance;
  const content: FiveStep = {
    analogy: gen?.analogy || t('progress.learningTaskNoLesson'), example: gen?.example || t('progress.learningTaskNoExample'),
    principles: gen?.principles || [], practice: gen?.steps || [], verify: gen?.verify || acceptance || t('progress.learningTaskVerificationAfterWork'),
  };
  const genSource = gen?.source;

  const step = STEP_META[stepIdx];
  const allPrevDone = stepDone.slice(0, 4).every(Boolean);

  const markStep = (i: number) => {
    setStepDone((s) => s.map((v, idx) => (idx === i ? true : v)));
  };

  const done = async () => {
    if (!task || busy) return;
    setBusy(true); setActionErr(null);
    try {
      const result = await api.post<{ status: string }>(`/learning/tasks/${task.id}/complete`, { artifact_text: artifact });
      setTask({ ...task, status: result.status });
      toast('✓', t('progress.learningTaskProgressSaved'));
    } catch (e: any) { setActionErr(t('progress.learningTaskSaveError', { error: e.message || e })); }
    finally { setBusy(false); }
  };

  const retest = async () => {
    if (!task || busy) return;
    setBusy(true); setActionErr(null);
    try {
      const result = await api.post<{ exam_id: number }>(`/learning/tasks/${task.id}/retest`);
      navigate(`/exam/diagnosis?id=${result.exam_id}&return_task_id=${task.id}`);
    } catch (e: any) { setActionErr(t('progress.learningTaskVerificationError', { error: e.message || e })); }
    finally { setBusy(false); }
  };

  const helpBtns: { key: string; label: string }[] = [
    { key: 'simpler', label: t('progress.learningTaskHelpSimpler') },
    { key: 'analogy', label: t('progress.learningTaskHelpAnalogy') },
    { key: 'example', label: t('progress.learningTaskHelpExample') },
    { key: 'project', label: t('progress.learningTaskHelpProject') },
    { key: 'code', label: t('progress.learningTaskHelpCode') },
    { key: 'diagram', label: t('progress.learningTaskHelpDiagram') },
    { key: 'unclear', label: t('progress.learningTaskHelpConfused') },
  ];

  const renderStepBody = () => {
    switch (step.key) {
      case 'analogy':
        return <p className="learn-step-text">{content.analogy}</p>;
      case 'example':
        return <p className="learn-step-text">{content.example}</p>;
      case 'principles':
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {content.principles.map((p, i) => (
              <div className="list-row" key={i} style={{ padding: '9px 2px' }}>
                <span className="chip chip-purple">{i + 1}</span>
                <span style={{ fontSize: 13.5 }}>{p}</span>
              </div>
            ))}
          </div>
        );
      case 'practice':
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {content.practice.map((s, i) => (
              <div className="tl-item" key={i} style={{ paddingBottom: 0 }}>
                <div className="tl-text"><b>{i + 1}.</b> {s}</div>
              </div>
            ))}
          </div>
        );
      case 'verify':
        return (
          <>
            <p className="learn-step-text">{content.verify}</p><label>{t('progress.learningTaskMyWork')}<textarea className="textarea" value={artifact} onChange={e => setArtifact(e.target.value)} placeholder={t('progress.learningTaskMyWorkPlaceholder')} /></label>
            <div className="hint" style={{ marginTop: 8 }}>
              {t('progress.learningTaskCompletionScope')}
            </div>
          </>
        );
      default:
        return null;
    }
  };

  if (!task || String(task.id) !== id) return <div><PageHead title={t('progress.learningTask')}><button className="btn btn-outline" onClick={() => navigate('/learning')}>{t('progress.backToPlan')}</button></PageHead><div className="card" role={loadErr ? 'alert' : undefined}>{loadErr ? t('progress.learningTaskLoadError', { error: loadErr }) : t('progress.learningTaskLoading')}</div></div>;

  const retestResult = task.content?.retest_result;
  const goalOutcomes = retestResult?.goal_outcomes;

  return (
    <div>
      <PageHead
        title={title}
        sub={t('progress.learningTaskTaskSubtitle', { type: TYPE_NAME[kind] || kind, domain: domain || '', minutes: minutes })}
      >
        <button className="btn btn-outline" onClick={() => navigate('/learning')}>{t('progress.learningTaskBackArrow')}</button>
        <StudyEntryButton kind="task" id={task.id}/>
        {task.kind!=='RETEST' && <button className="btn btn-outline" onClick={()=>navigate(`/learning/task/${task.id}`)}>{t('progress.learningTaskBackToPath')}</button>}
      </PageHead>
      {actionErr && <div role="alert" className="demo-banner demo-banner-err">{actionErr}</div>}
      {contentErr && <div role="alert" className="demo-banner demo-banner-err">{t('progress.learningTaskLessonLoadError')}{contentErr}</div>}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-title">{t('progress.learningTaskGapSource')}</div>
        <p>{t('progress.learningTaskScoringCriterion')}{task.source_criterion || t('progress.learningTaskMissingLegacyCriterion')}{' ' + t('progress.learningTaskGapType')}{task.gap_type || '—'}</p>
        <p className="hint">{t('progress.learningTaskSourceIds', { exam: task.source_exam_id || '—', question: task.source_exam_question_id || '—', evidence: task.source_evidence_id || '—' })}</p>
        {task.content?.observation && <div>
          <p><b>{t('progress.learningTaskOriginalQuestion')}</b>{task.content.observation.question_title || '—'}</p>
          <p><b>{t('progress.learningTaskAnswerExcerpt')}</b>{task.content.observation.answer_excerpt || t('progress.unanswered')}</p>
          <p><b>{t('progress.learningTaskMissingCriteria')}</b>{task.content.observation.criterion_detail || task.content.observation.criterion || task.source_criterion}</p>
          <p><b>{t('progress.learningTaskReason')}</b>{task.content.observation.reasoning || t('progress.learningTaskSeeReport')}</p>
          <p className="hint">{task.content.observation.scope_note || t('progress.learningTaskTaskScope')}</p>
        </div>}
        {task.source_exam_id && <button className="btn btn-outline btn-sm" onClick={() => navigate(`/exam/diagnosis/report?id=${task.source_exam_id}`)}>{t('progress.learningTaskOriginalEvidence')}</button>}
      </div>
      {((task.kind === 'RETEST' && task.status !== 'DONE') || (task.kind !== 'RETEST' && task.status === 'DONE')) && <div className="card" style={{ marginBottom: 16 }}>
        <p>{task.kind === 'RETEST' ? t('progress.learningTaskRetestDate', { date: formatProgressDate(task.due_date) || '—' }) : t('progress.learningTaskImmediateVerificationScope')}</p>
        <button className="btn btn-primary" disabled={busy || (task.kind === 'RETEST' && !!task.due_date && new Date(task.due_date).getTime() > Date.now())} onClick={retest}>{busy ? t('progress.learningTaskPreparingQuestion') : task.kind === 'RETEST' ? t('progress.learningTaskStartDueRetest') : t('progress.learningTaskVerifyNow')}</button>
      </div>}
      {retestResult && <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-title">{t('progress.learningTaskLatestVerification')}</div>
        <p>{t('progress.learningTaskOverallScore')}{retestResult.score ?? t('progress.learningTaskUnscored')}</p>
        <p>{t('progress.learningTaskOverallResult', { result: retestResult.passed === true ? t('progress.pass') : retestResult.passed === false ? t('progress.fail') : t('progress.learningTaskNotAssessed') })}</p>
        {goalOutcomes?.length ? <>
          <ul aria-label={t('progress.learningTaskCriterionResult')}>
            {goalOutcomes.map((goal, index) => (
              <li key={goal.goal_id ?? `legacy-${index}`}>
                <b>{goal.source_criterion || t('progress.learningTaskOriginalCriterion')}</b>
                {' · '}{GOAL_RESULT[goal.status]}
                {goal.score !== null && t('progress.learningTaskCriterionScore', { score: goal.score })}
              </li>
            ))}
          </ul>
          {retestResult.assessment_scope && <p>{t('progress.learningTaskVerificationScope')}{retestResult.assessment_scope}</p>}
        </> : <p>{t('progress.learningTaskMissingLegacyMapping')}</p>}
        <p className="hint">{(goalOutcomes?.length ? retestResult.scope_note : null) || t('progress.learningTaskVerificationResultScope')}</p>
        <button className="btn btn-outline" onClick={() => navigate(`/exam/diagnosis/report?id=${retestResult.exam_id}&return_task_id=${task.id}`)}>{t('progress.learningTaskVerificationReport')}</button>
      </div>}
      <DemoBanner />

      {why && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-head">
            <span className="card-title">{t('progress.learningTaskWhy')}</span>
          </div>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.75 }}>{why}</p>
        </div>
      )}

      {task.kind !== 'RETEST' && <div className="grid grid-2">
        {/* 左: 步骤导航 */}
        <div className="card learn-nav">
          <div className="card-head">
            <span className="card-title">{t('progress.learningTaskLesson')}</span>
            {genSource === 'llm' && <span className="chip chip-purple">{t('progress.learningTaskGeneratedByAI')}</span>}
            {genSource === 'rule' && <span className="chip chip-slate">{t('progress.learningTaskGeneratedByRules')}</span>}
          </div>
          <div className="hint" style={{ padding: '0 14px', marginTop: 8 }}>
            {t('progress.learningTaskBankLesson', { domain: domain || t('progress.learningTaskCurrentDomain') })}
          </div>
          {STEP_META.map((s, i) => (
            <div
              key={s.key}
              className={`learn-nav-item ${i === stepIdx ? 'cur' : ''} ${stepDone[i] ? 'done' : ''}`}
              onClick={() => setStepIdx(i)}
            >
              <span className="learn-nav-ic">{stepDone[i] ? '✓' : s.ic}</span>
              <div>
                <div className="learn-nav-title">{t('progress.learningTaskStep', { step: i + 1, title: s.title })}</div>
                <div className="learn-nav-sub">{s.sub}</div>
              </div>
            </div>
          ))}
        </div>

        {/* 右: 当前步骤内容 */}
        <div className="card">
          <div className="card-head">
            <span className="card-title">{step.ic} {step.title}</span>
            <span className="chip chip-slate">{stepIdx + 1}/5</span>
          </div>
          {contentBusy && !gen ? (
            <div style={{ padding: '28px 8px', textAlign: 'center', color: 'var(--text-2)', fontSize: 13.5 }}>
              <span style={{ display: 'inline-block', width: 14, height: 14, border: '2px solid var(--border)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 0.8s linear infinite', marginRight: 8 }} />
              {t('progress.learningTaskGeneratingLesson')}
            </div>
          ) : (
            renderStepBody()
          )}

          {/* 每步常驻帮助 */}
          <div className="step-help">
            {helpBtns.map((b) => (
              <button key={b.key} className="btn btn-ghost btn-sm" onClick={() => doHelp(b.key)}>
                {b.label}
              </button>
            ))}
          </div>

          <div className="learn-step-foot">
            <button className="btn btn-outline btn-sm" disabled={stepIdx === 0} onClick={() => setStepIdx((i) => Math.max(0, i - 1))}>
              {t('progress.learningTaskPrevious')}
            </button>
            {stepIdx < 4 ? (
              <button className="btn btn-primary btn-sm" onClick={() => { markStep(stepIdx); setStepIdx((i) => Math.min(4, i + 1)); }}>
                {t('progress.learningTaskNext')}
              </button>
            ) : (
              <button className="btn btn-primary btn-sm" disabled={!allPrevDone || busy || task.status === 'DONE' || !gen || !!contentErr} onClick={done}>
                {t('progress.learningTaskSave')}
              </button>
            )}
          </div>
          {!allPrevDone && stepIdx === 4 && (
            <div className="hint" style={{ marginTop: 6 }}>{t('progress.learningTaskCompleteFirstFour')}</div>
          )}
        </div>
      </div>

      }
      {helpKey && (
        <Modal
          title={t('progress.learningHelp')}
          onClose={() => setHelpKey(null)}
          size="sm"
          footer={<button className="btn btn-primary" onClick={() => setHelpKey(null)}>{t('progress.learningTaskDismiss')}</button>}
        >
        {helpBusy ? (
          <p style={{ fontSize: 13.5, lineHeight: 1.8, color: 'var(--text-2)' }}>
            <span style={{ display: 'inline-block', width: 12, height: 12, border: '2px solid var(--border)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 0.8s linear infinite', marginRight: 8 }} />
            {t('progress.learningTaskGeneratingHelp')}
          </p>
        ) : (
          <p style={{ fontSize: 13.5, lineHeight: 1.8, color: 'var(--text-2)', whiteSpace: 'pre-wrap' }}>
            {helpText || ''}
          </p>
        )}
        <div className="hint" style={{ marginTop: 10 }}>
          {helpSrc === 'llm' && t('progress.learningTaskCachedHelp')}
          {helpSrc === 'none' && t('progress.learningTaskUncachedError')}
        </div>
        </Modal>
      )}
    </div>
  );
}
