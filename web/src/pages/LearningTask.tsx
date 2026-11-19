import StudyEntryButton from '../components/StudyEntryButton';
import React, { useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useAppState } from '../components/AppState';
import { DemoBanner, Modal, PageHead } from '../components/ui';
import { api, useApi, LearningTaskView, RetestGoalOutcome, TaskContentResp, TaskHelpResp } from '../api/client';
import LearningPathPanel from '../components/LearningPathPanel';


const TYPE_NAME: Record<string, string> = { UNDERSTAND: '理解', PRACTICE: '实践', RETEST: '复测' };
const GOAL_RESULT: Record<RetestGoalOutcome['status'], string> = {
  UNMAPPED: '待验证（缺少已审核映射）',
  UNVERIFIED: '待验证',
  ASSISTED: '已辅助作答，独立表现待验证',
  PASSED: '通过',
  FAILED: '未通过',
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
  { key: 'analogy', title: '类比讲清', sub: '先用人话把它讲明白', ic: '💡' },
  { key: 'example', title: '小例子', sub: '一个具体场景看它怎么工作', ic: '🧪' },
  { key: 'principles', title: '原理与术语', sub: '现在换成面试官要听的词', ic: '📖' },
  { key: 'practice', title: '结合你的项目', sub: '通过练习记录你的理解', ic: '🛠️' },
  { key: 'verify', title: '记录学习产物', sub: '学习完成后还需新题验证', ic: '✅' },
];

// 每步帮助来自当前任务的后端内容

export default function LearningTask() {
  const {id}=useParams();const [params]=useSearchParams();const navigate=useNavigate();
  const {data:task,error,loading}=useApi<LearningTaskView>(id?`/learning/tasks/${id}`:null);
  if(params.get('legacy')==='1' || task?.kind==='RETEST')return <LegacyLearningTask/>;
  if(!task || String(task.id)!==id)return <div><PageHead title="学习路径"><button className="btn btn-outline" onClick={()=>navigate('/learning')}>返回学习计划</button></PageHead><div className="card" role={error?'alert':undefined}>{error?`无法读取任务：${error}`:loading?'正在读取任务…':'未指定学习任务。'}</div></div>;
  return <div><PageHead title={task.title} sub="围绕一个具体目标完成练习、反馈、修订和验证"><StudyEntryButton kind="task" id={task.id}/><button className="btn btn-outline" onClick={()=>navigate('/learning')}>返回学习计划</button></PageHead><LearningPathPanel key={task.id} task={task}/><details className="card"><summary>原任务来源与完整教学</summary><p>具体失分项：{task.source_criterion || '历史任务未记录'}</p>{task.content?.observation?.answer_excerpt && <blockquote>{task.content.observation.answer_excerpt}</blockquote>}{task.source_exam_id && <button className="btn btn-outline btn-sm" onClick={()=>navigate(`/exam/diagnosis/report?id=${task.source_exam_id}`)}>查看原答与失分证据</button>}<p className="hint">原五步教学和学习产物仍可访问；尚未缓存的教学内容会使用已配置的模型或规则生成。</p><button className="btn btn-outline" onClick={()=>navigate(`/learning/task/${task.id}?legacy=1`)}>打开原教学内容</button></details></div>;
}

function LegacyLearningTask() {
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
        setHelpText(r.text || '（暂时没有生成内容，请稍后再试）');
        setHelpSrc(r.source);
      }
    } catch (e: any) {
      setHelpText(`求助失败: ${e.message || e}`);
      setHelpSrc('none');
    } finally {
      setHelpBusy(false);
    }
  };

  const title = task?.title || '学习任务';
  const kind = task?.kind || '';
  const minutes = task?.est_minutes ?? '—';
  const domain = task?.skill_name || task?.skill;
  const why = task?.why;
  const acceptance = task?.acceptance;
  const content: FiveStep = {
    analogy: gen?.analogy || '暂无教学内容', example: gen?.example || '暂无例子',
    principles: gen?.principles || [], practice: gen?.steps || [], verify: gen?.verify || acceptance || '记录学习产物，再进行新题验证。',
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
      toast('✓', '学习进度已保存；能力尚待新题验证，已从完成日安排 1/3/7 天复测。');
    } catch (e: any) { setActionErr(`保存失败：${e.message || e}。学习产物已保留。`); }
    finally { setBusy(false); }
  };

  const retest = async () => {
    if (!task || busy) return;
    setBusy(true); setActionErr(null);
    try {
      const result = await api.post<{ exam_id: number }>(`/learning/tasks/${task.id}/retest`);
      navigate(`/exam/diagnosis?id=${result.exam_id}&return_task_id=${task.id}`);
    } catch (e: any) { setActionErr(`无法开始验证：${e.message || e}`); }
    finally { setBusy(false); }
  };

  const helpBtns: { key: string; label: string }[] = [
    { key: 'simpler', label: '更简单一点' },
    { key: 'analogy', label: '换个类比' },
    { key: 'example', label: '换个例子' },
    { key: 'project', label: '结合我的项目' },
    { key: 'code', label: '看代码' },
    { key: 'diagram', label: '画个图' },
    { key: 'unclear', label: '还是不理解' },
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
            <p className="learn-step-text">{content.verify}</p><label>学习产物 / 我的理解<textarea className="textarea" value={artifact} onChange={e => setArtifact(e.target.value)} placeholder="用自己的话记录你补齐的失分项" /></label>
            <div className="hint" style={{ marginTop: 8 }}>
              完成只记录学习进度，并从完成日起安排 1/3/7 天复测。即时新题验证不代表长期保持。
            </div>
          </>
        );
      default:
        return null;
    }
  };

  if (!task || String(task.id) !== id) return <div><PageHead title="学习任务"><button className="btn btn-outline" onClick={() => navigate('/learning')}>返回学习计划</button></PageHead><div className="card" role={loadErr ? 'alert' : undefined}>{loadErr ? `无法读取任务：${loadErr}` : '正在读取任务…'}</div></div>;

  const retestResult = task.content?.retest_result;
  const goalOutcomes = retestResult?.goal_outcomes;

  return (
    <div>
      <PageHead
        title={title}
        sub={`${TYPE_NAME[kind] || kind}任务 · ${domain || ''} · 预计 ${minutes} 分钟`}
      >
        <button className="btn btn-outline" onClick={() => navigate('/learning')}>← 返回学习计划</button>
        <StudyEntryButton kind="task" id={task.id}/>
        {task.kind!=='RETEST' && <button className="btn btn-outline" onClick={()=>navigate(`/learning/task/${task.id}`)}>回到学习路径</button>}
      </PageHead>
      {actionErr && <div role="alert" className="demo-banner demo-banner-err">{actionErr}</div>}
      {contentErr && <div role="alert" className="demo-banner demo-banner-err">教学内容读取失败：{contentErr}</div>}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-title">具体失分来源</div>
        <p>评分项：{task.source_criterion || '历史任务未记录评分项'} · 缺口类型：{task.gap_type || '—'}</p>
        <p className="hint">考试 #{task.source_exam_id || '—'} · 题目 #{task.source_exam_question_id || '—'} · 证据 #{task.source_evidence_id || '—'}</p>
        {task.content?.observation && <div>
          <p><b>原题：</b>{task.content.observation.question_title || '—'}</p>
          <p><b>原答摘录：</b>{task.content.observation.answer_excerpt || '（未作答）'}</p>
          <p><b>缺失评分项：</b>{task.content.observation.criterion_detail || task.content.observation.criterion || task.source_criterion}</p>
          <p><b>具体原因：</b>{task.content.observation.reasoning || '请查看原始报告'}</p>
          <p className="hint">{task.content.observation.scope_note || '本任务只针对本次作答中的具体缺口，不代表整个能力域。'}</p>
        </div>}
        {task.source_exam_id && <button className="btn btn-outline btn-sm" onClick={() => navigate(`/exam/diagnosis/report?id=${task.source_exam_id}`)}>查看原答与失分证据</button>}
      </div>
      {((task.kind === 'RETEST' && task.status !== 'DONE') || (task.kind !== 'RETEST' && task.status === 'DONE')) && <div className="card" style={{ marginBottom: 16 }}>
        <p>{task.kind === 'RETEST' ? `计划复测日期：${task.due_date || '—'}。需到期后进行新作答。` : '学习进度已完成，可进行即时新题验证。该结果不代表 1/3/7 天保持。'}</p>
        <button className="btn btn-primary" disabled={busy || (task.kind === 'RETEST' && !!task.due_date && new Date(task.due_date).getTime() > Date.now())} onClick={retest}>{busy ? '准备新题…' : task.kind === 'RETEST' ? '开始到期复测' : '即时新题验证'}</button>
      </div>}
      {retestResult && <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-title">最近新题验证</div>
        <p>整题得分：{retestResult.score ?? '未完成评分'}</p>
        <p>{`本题整体：${retestResult.passed === true ? '通过' : retestResult.passed === false ? '未通过' : '未评定'}`}</p>
        {goalOutcomes?.length ? <>
          <ul aria-label="原评分项结果">
            {goalOutcomes.map((goal, index) => (
              <li key={goal.goal_id ?? `legacy-${index}`}>
                <b>{goal.source_criterion || '原评分项'}</b>
                {' · '}{GOAL_RESULT[goal.status]}
                {goal.score !== null && ` · 评分项得分：${goal.score}`}
              </li>
            ))}
          </ul>
          {retestResult.assessment_scope && <p>验证范围：{retestResult.assessment_scope}</p>}
        </> : <p>原评分项结果：待验证（历史记录缺少审核映射）</p>}
        <p className="hint">{(goalOutcomes?.length ? retestResult.scope_note : null) || '仅反映本次作答表现，不代表长期保持。'}</p>
        <button className="btn btn-outline" onClick={() => navigate(`/exam/diagnosis/report?id=${retestResult.exam_id}&return_task_id=${task.id}`)}>查看本次验证报告</button>
      </div>}
      <DemoBanner />

      {why && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-head">
            <span className="card-title">为什么学</span>
          </div>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.75 }}>{why}</p>
        </div>
      )}

      {task.kind !== 'RETEST' && <div className="grid grid-2">
        {/* 左: 步骤导航 */}
        <div className="card learn-nav">
          <div className="card-head">
            <span className="card-title">五步教学流</span>
            {genSource === 'llm' && <span className="chip chip-purple">✨ AI 生成</span>}
            {genSource === 'rule' && <span className="chip chip-slate">⚙️ 规则生成</span>}
          </div>
          <div className="hint" style={{ padding: '0 14px', marginTop: 8 }}>
            内容依据题库材料生成，服务于「{domain || '当前能力域'}」差距。
          </div>
          {STEP_META.map((s, i) => (
            <div
              key={s.key}
              className={`learn-nav-item ${i === stepIdx ? 'cur' : ''} ${stepDone[i] ? 'done' : ''}`}
              onClick={() => setStepIdx(i)}
            >
              <span className="learn-nav-ic">{stepDone[i] ? '✓' : s.ic}</span>
              <div>
                <div className="learn-nav-title">第 {i + 1} 步 · {s.title}</div>
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
              正在按你的能力画像生成教学内容…
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
              ← 上一步
            </button>
            {stepIdx < 4 ? (
              <button className="btn btn-primary btn-sm" onClick={() => { markStep(stepIdx); setStepIdx((i) => Math.min(4, i + 1)); }}>
                这步懂了，下一步 →
              </button>
            ) : (
              <button className="btn btn-primary btn-sm" disabled={!allPrevDone || busy || task.status === 'DONE' || !gen || !!contentErr} onClick={done}>
                ✓ 保存学习进度
              </button>
            )}
          </div>
          {!allPrevDone && stepIdx === 4 && (
            <div className="hint" style={{ marginTop: 6 }}>先完成前面 4 步（点「这步懂了」）才能提交完成。</div>
          )}
        </div>
      </div>

      }
      {helpKey && (
        <Modal
          title="学习帮助"
          onClose={() => setHelpKey(null)}
          size="sm"
          footer={<button className="btn btn-primary" onClick={() => setHelpKey(null)}>知道了</button>}
        >
        {helpBusy ? (
          <p style={{ fontSize: 13.5, lineHeight: 1.8, color: 'var(--text-2)' }}>
            <span style={{ display: 'inline-block', width: 12, height: 12, border: '2px solid var(--border)', borderTopColor: 'var(--primary)', borderRadius: '50%', animation: 'spin 0.8s linear infinite', marginRight: 8 }} />
            AI 正在按你的任务生成解答…
          </p>
        ) : (
          <p style={{ fontSize: 13.5, lineHeight: 1.8, color: 'var(--text-2)', whiteSpace: 'pre-wrap' }}>
            {helpText || ''}
          </p>
        )}
        <div className="hint" style={{ marginTop: 10 }}>
          {helpSrc === 'llm' && '✨ 由 AI 结合题库材料生成 · 结果已缓存，可反复查看'}
          {helpSrc === 'none' && 'ℹ️ 本次为错误提示，未缓存'}
        </div>
        </Modal>
      )}
    </div>
  );
}
