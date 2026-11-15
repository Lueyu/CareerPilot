import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAppState } from '../components/AppState';
import { DemoBanner, Modal, PageHead } from '../components/ui';
import { api, LearningTaskView } from '../api/client';
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
const HELP_TEXT: Record<string, string> = {
  simpler: '好，用更基础的话再讲一遍：先把这步的结论记成一个「如果…就…」的句子，再回来看类比。仍然抽象时，跳过术语只记场景。',
  example: '换一个场景：想象你在给同事讲这个概念，同事只听懂了「外卖/取餐号/白板」这类生活词——用同样的方式把这一步再描述一次。',
  project: '把这一步映射到你的项目：找到你代码里对应的位置（或计划补上的位置），写下「这里对应这步的什么」。写不出来 = 这步还没懂。',
  code: '看代码（示例）：伪代码层面把这一步落成 5-10 行，参数怎么传、返回什么、错误往哪走。写不出来再回上一步。',
  stuck: '没关系，把它记下来：这个点已加入你的「知识缺口」清单，学习中心会安排复测，现在可以先继续往下走。',
};

export default function LearningTask() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { toast } = useAppState();
  const [task, setTask] = useState<LearningTaskView | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [stepIdx, setStepIdx] = useState(0);
  const [helpKey, setHelpKey] = useState<string | null>(null);
  const [stepDone, setStepDone] = useState<boolean[]>([false, false, false, false, false]);

  useEffect(() => {
    let alive = true;
    api.get<LearningTaskView>(`/learning/tasks/${id}`)
      .then((t) => { if (alive) setTask(t); })
      .catch(() => { if (alive) setNotFound(true); });
    return () => { alive = false; };
  }, [id]);

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
    analogy: fb.analogy,
    example: fb.example,
    principles: task?.content?.principles?.length ? task.content.principles : fb.principles,
    practice: task?.content?.steps?.length ? task.content.steps : fb.practice,
    verify: task?.content?.verify || acceptance || fb.verify,
  };

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

  const helpBtns: { key: keyof typeof HELP_TEXT; label: string }[] = [
    { key: 'simpler', label: '更简单一点' },
    { key: 'example', label: '换个例子' },
    { key: 'project', label: '结合我的项目' },
    { key: 'code', label: '看代码' },
    { key: 'stuck', label: '还是不理解' },
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
            <span className="card-sub">完成第 5 步验证即完成任务</span>
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
          {renderStepBody()}

          {/* 每步常驻帮助 */}
          <div className="step-help">
            {helpBtns.map((b) => (
              <button key={b.key} className="btn btn-ghost btn-sm" onClick={() => setHelpKey(b.key)}>
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

      <Modal
        title="学习帮助"
        onClose={() => setHelpKey(null)}
        size="sm"
        footer={<button className="btn btn-primary" onClick={() => setHelpKey(null)}>知道了</button>}
      >
        <p style={{ fontSize: 13.5, lineHeight: 1.8, color: 'var(--text-2)' }}>
          {helpKey ? HELP_TEXT[helpKey] : ''}
        </p>
        <div className="hint" style={{ marginTop: 10 }}>DEMO · 正式版由大模型按你的画像实时生成。</div>
      </Modal>
    </div>
  );
}
