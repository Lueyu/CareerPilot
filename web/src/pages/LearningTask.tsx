import React, { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAppState } from '../components/AppState';
import { DemoBanner, PageHead } from '../components/ui';
import { api, LearningTaskView } from '../api/client';
import { TODAY_TASKS } from '../data/demo';

const TYPE_NAME: Record<string, string> = { UNDERSTAND: '理解', PRACTICE: '实践', RETEST: '复测' };

const FALLBACK_CONTENT: Record<string, { steps: string[]; principles: string[]; verify: string }> = {
  t1: {
    steps: [
      '阅读资料：ReAct 循环中 observation 的作用（5 分钟）',
      '用你自己的话复述：为什么状态必须显式写入，而不是依赖对话上下文（5 分钟）',
      '对照一个真实失败案例，指出状态丢失发生在哪一步（10 分钟）',
    ],
    principles: ['显式状态 = 可恢复', 'observation 与 action 交替写入', '循环终止条件必须明确'],
    verify: '自评：能复述状态丢失导致的失败链路，并在案例中标出关键步骤',
  },
  t2: {
    steps: [
      '在沙箱中准备一个会随机失败的工具（如 50% 概率返回 503）（10 分钟）',
      '实现带指数退避的重试，加入最大重试次数（10 分钟）',
      '实现幂等键，验证重复调用不会产生副作用（10 分钟）',
      '记录重试日志：次数、退避时长、最终结果（10 分钟）',
    ],
    principles: [
      '指数退避：base × 2^n，加上随机抖动',
      '重试上限：默认 3 次，超过转人工',
      '幂等键：同一请求重放返回同一结果',
      '超时独立于重试：单次调用超时 5s，总预算 30s',
    ],
    verify: '沙箱中 20 次调用全通过，日志完整；对验证器描述你的重试参数选择',
  },
  t3: {
    steps: [
      '速答 5 道 Recall@k / MRR 概念题（5 分钟）',
      '对照错题复习定义与计算（5 分钟）',
    ],
    principles: ['Recall@k 关注覆盖率', 'MRR 关注排序质量', '两者结合评估检索器'],
    verify: '正确率 ≥ 80%，错题已记录到记忆中心',
  },
};

export default function LearningTask() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { toast } = useAppState();
  const [task, setTask] = useState<LearningTaskView | null>(null);
  const [notFound, setNotFound] = useState(false);

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

  const content = task?.content && (task.content.steps?.length || task.content.principles?.length || task.content.verify)
    ? {
        steps: task.content.steps?.length ? task.content.steps : FALLBACK_CONTENT.t1.steps,
        principles: task.content.principles?.length ? task.content.principles : ['由差距驱动生成', '完成后沉淀为证据', '1/3/7 天自动安排复测'],
        verify: task.content.verify || acceptance || '完成后将生成一条实践证据',
      }
    : (FALLBACK_CONTENT[(fbTask.id as string)] ?? FALLBACK_CONTENT.t1);

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

  return (
    <div>
      <PageHead
        title={title}
        sub={`${TYPE_NAME[kind] || kind}任务 · ${domain || ''} · 预计 ${minutes} 分钟`}
      >
        <button className="btn btn-outline" onClick={() => navigate('/learning')}>← 返回学习中心</button>
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
        <div className="card">
          <div className="card-head">
            <span className="card-title">任务步骤</span>
            <span className="chip chip-purple">{TYPE_NAME[kind] || kind}</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            {content.steps.map((s, i) => (
              <div className="tl-item" key={i} style={{ paddingBottom: 0 }}>
                <div className="tl-text"><b>Step {i + 1}.</b> {s}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <span className="card-title">要点速览</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {content.principles.map((p, i) => (
              <div className="list-row" key={i} style={{ padding: '9px 2px' }}>
                <span className="chip chip-purple">{i + 1}</span>
                <span style={{ fontSize: 13.5 }}>{p}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card mt-18">
        <div className="card-head">
          <span className="card-title">验证方式</span>
        </div>
        <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.75 }}>{content.verify}</p>
        <div className="hint" style={{ marginTop: 8 }}>
          完成后将生成一条实践证据，并标记「待复测」状态（1/3/7 天）。
        </div>
        <div style={{ marginTop: 16, display: 'flex', gap: 10 }}>
          <button className="btn btn-primary" onClick={done}>✓ 标记完成</button>
          <button className="btn btn-outline" onClick={() => navigate('/learning')}>稍后继续</button>
        </div>
      </div>
    </div>
  );
}
