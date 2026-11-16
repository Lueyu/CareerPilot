import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAppState } from '../components/AppState';
import { DemoBanner, Modal, PageHead } from '../components/ui';
import { api, LearningTaskView, TaskContentResp, TaskHelpResp } from '../api/client';
import { TODAY_TASKS } from '../data/demo';

const TYPE_NAME: Record<string, string> = { UNDERSTAND: '理解', PRACTICE: '实践', RETEST: '复测' };

// —— v3.1 M4: 统一 5 步教学流(类比→小例子→原理→结合项目→验证) ——
interface FiveStep {
  analogy: string;
  example: string;
  principles: string[];
  practice: string[];
  verify: string;
}

const FALLBACK_CONTENT: Record<string, FiveStep> = {
  t1: {
    analogy:
      '把 Agent 想成一个厨师：对话上下文是厨房的白板，写满了就擦掉。显式状态是你的随身笔记本——菜做到第几步、盐放没放，都记在本子上，白板擦了也不怕。',
    example:
      '用户让你「查三个城市天气再推荐穿搭」。第 2 步时上下文被截断，只记得「推荐穿搭」——没有状态记录的 Agent 会漏查城市；有状态记录的 Agent 读笔记发现城市 3 还没查，补上再推荐。',
    principles: ['显式状态 = 可恢复', 'observation 与 action 交替写入', '循环终止条件必须明确'],
    practice: [
      '阅读资料：ReAct 循环中 observation 的作用（5 分钟）',
      '用你自己的话复述：为什么状态必须显式写入，而不是依赖对话上下文（5 分钟）',
      '对照一个真实失败案例，指出状态丢失发生在哪一步（10 分钟）',
    ],
    verify: '自评：能复述状态丢失导致的失败链路，并在案例中标出关键步骤',
  },
  t2: {
    analogy:
      '工具重试就像给外卖小哥打电话占线：先等 2 秒再打，还占线等 4 秒，再占线等 8 秒——最多打 3 次，不行就换人（转人工）。幂等键好比取餐号：同一单不管问几次，都指向同一份餐，不会重复做。',
    example:
      '支付工具偶发超时：不带幂等键时，重试 3 次 = 用户被扣 3 次钱；带上幂等键 pay_20261116_001 后，第 2、3 次重试服务端识别「同一单」，直接返回首次结果。',
    principles: [
      '指数退避：base × 2^n，加上随机抖动',
      '重试上限：默认 3 次，超过转人工',
      '幂等键：同一请求重放返回同一结果',
      '超时独立于重试：单次调用超时 5s，总预算 30s',
    ],
    practice: [
      '在沙箱中准备一个会随机失败的工具（如 50% 概率返回 503）（10 分钟）',
      '实现带指数退避的重试，加入最大重试次数（10 分钟）',
      '实现幂等键，验证重复调用不会产生副作用（10 分钟）',
      '记录重试日志：次数、退避时长、最终结果（10 分钟）',
    ],
    verify: '沙箱中 20 次调用全通过，日志完整；对验证器描述你的重试参数选择',
  },
  t3: {
    analogy:
      'Recall@k 像考试「划的重点覆盖了多少」——重点没划全，答得再漂亮也丢分；MRR 像「正确的答案排第几」——排第 1 是满分，排第 10 要打折。',
    example:
      '知识库有 100 条资料，问题相关的有 5 条。召回 top-10 里命中 4 条 → Recall@10 = 80%；命中那条排第 3 位 → MRR = 1/3 ≈ 0.33。',
    principles: ['Recall@k 关注覆盖率', 'MRR 关注排序质量', '两者结合评估检索器'],
    practice: [
      '速答 5 道 Recall@k / MRR 概念题（5 分钟）',
      '对照错题复习定义与计算（5 分钟）',
    ],
    verify: '正确率 ≥ 80%，错题已记录到记忆中心',
  },
};

// 5 步标题与副标题
const STEP_META = [
  { key: 'analogy', title: '类比讲清', sub: '先用人话把它讲明白', ic: '💡' },
  { key: 'example', title: '小例子', sub: '一个具体场景看它怎么工作', ic: '🧪' },
  { key: 'principles', title: '原理与术语', sub: '现在换成面试官要听的词', ic: '📖' },
  { key: 'practice', title: '结合你的项目', sub: '动手做一遍才算学会', ic: '🛠️' },
  { key: 'verify', title: '验证理解', sub: '过一遍自测，完成本任务', ic: '✅' },
];

// 每步帮助文案(demo)

export default function LearningTask() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { toast } = useAppState();
  const [task, setTask] = useState<LearningTaskView | null>(null);
  const [notFound, setNotFound] = useState(false);
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
    api.get<LearningTaskView>(`/learning/tasks/${id}`)
      .then((t) => { if (alive) setTask(t); })
      .catch(() => { if (alive) setNotFound(true); });
    return () => { alive = false; };
  }, [id]);

  // 教学内容: 已缓存(任务 detail 带 analogy)直接用; 否则调生成接口(LLM/规则)
  useEffect(() => {
    const c = task?.content;
    if (c?.analogy && (c.source === 'llm' || c.source === 'rule')) {
      setGen(c);
      return;
    }
    if (!task) return;
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
    const cached = task.content?.help_cache?.[mode] || gen?.help_cache?.[mode];

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

  // 后端任务优先; 失败降级 demo
  const fbTask = TODAY_TASKS.find((t) => t.id === id) ?? TODAY_TASKS[0];
  const title = task?.title ?? fbTask.title;
  const kind = task?.kind ?? fbTask.type;
  const minutes = task?.est_minutes ?? fbTask.minutes;
  const domain = task?.skill_name ?? fbTask.domain;
  const why = task?.why ?? fbTask.why;
  const acceptance = task?.acceptance ?? fbTask.criteria;

  const fb: FiveStep = FALLBACK_CONTENT[(fbTask.id as string)] ?? FALLBACK_CONTENT.t1;
  const content: FiveStep = {
    analogy: gen?.analogy || fb.analogy,
    example: gen?.example || fb.example,
    principles: gen?.principles?.length ? gen.principles : (task?.content?.principles?.length ? task.content.principles : fb.principles),
    practice: gen?.steps?.length ? gen.steps : (task?.content?.steps?.length ? task.content.steps : fb.practice),
    verify: gen?.verify || acceptance || fb.verify,
  };
  const genSource = gen?.source;

  const step = STEP_META[stepIdx];
  const allPrevDone = stepDone.slice(0, 4).every(Boolean);

  const markStep = (i: number) => {
    setStepDone((s) => s.map((v, idx) => (idx === i ? true : v)));
  };

  const done = async () => {
    if (task) {
      try {
        await api.post(`/learning/tasks/${task.id}/start`);
        await api.post(`/learning/tasks/${task.id}/complete`);
      } catch { /* 降级: 仍然提示 */ }
    }
    toast('🎉', `「${title}」已完成，证据已写入能力画像`);
    navigate('/learning');
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
            <p className="learn-step-text">{content.verify}</p>
            <div className="hint" style={{ marginTop: 8 }}>
              完成后将生成一条实践证据，并标记「待复测」状态（1/3/7 天）。
            </div>
          </>
        );
      default:
        return null;
    }
  };

  return (
    <div>
      <PageHead
        title={title}
        sub={`${TYPE_NAME[kind] || kind}任务 · ${domain || ''} · 预计 ${minutes} 分钟`}
      >
        <button className="btn btn-outline" onClick={() => navigate('/learning')}>← 返回学习计划</button>
      </PageHead>
      {notFound && <div className="demo-banner demo-banner-err">后端不可用或任务不存在，以下为演示内容</div>}
      <DemoBanner />

      {why && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-head">
            <span className="card-title">为什么学</span>
          </div>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.75 }}>{why}</p>
        </div>
      )}

      <div className="grid grid-2">
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
              <button className="btn btn-primary btn-sm" disabled={!allPrevDone} onClick={done}>
                ✓ 完成验证并结束任务
              </button>
            )}
          </div>
          {!allPrevDone && stepIdx === 4 && (
            <div className="hint" style={{ marginTop: 6 }}>先完成前面 4 步（点「这步懂了」）才能提交完成。</div>
          )}
        </div>
      </div>

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
          {helpSrc === 'rule' && '⚙️ 规则生成（LLM 未配置时的确定性回答）'}
          {helpSrc === 'none' && 'ℹ️ 本次为错误提示，未缓存'}
        </div>
        </Modal>
      )}
    </div>
  );
}
