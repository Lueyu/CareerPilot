import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { DemoBanner, PageHead, Modal } from '../components/ui';
import { useApi, LearningData, LearningTaskView } from '../api/client';
import { TODAY_TASKS, RETEST_PLAN } from '../data/demo';

const TYPE_CLS: Record<string, string> = { UNDERSTAND: 'chip-blue', PRACTICE: 'chip-purple', RETEST: 'chip-amber' };
const TYPE_IC: Record<string, string> = { UNDERSTAND: '📖', PRACTICE: '🛠️', RETEST: '🔁' };
const TYPE_NAME: Record<string, string> = { UNDERSTAND: '理解', PRACTICE: '实践', RETEST: '复测' };

const HELP_ITEMS = [
  { key: 'simple', ic: '💡', label: '解释得更简单', text: '把「证据驱动评估」讲得简单一点：就像体检报告——不是靠感觉说你健康，而是量血压、测血糖，用数据说话。你的每次诊断、面经、实践就是一次"体检项目"。' },
  { key: 'analogy', ic: '🔁', label: '换个类比', text: 'CareerPilot 像一个懂行的健身教练：先测你的力量基线（独立诊断），再按短板安排训练（学习任务），隔几天复测确认进步（1/3/7 天复测），最后把你的训练记录整理成简历素材（职业资产）。' },
  { key: 'stuck', ic: '🙋', label: '我还是没理解', text: '没关系，可以把具体困惑发给我，我会换一个角度重新讲解，或直接生成一道例题带你过一遍。（Demo：此处将接入学习助手）' },
  { key: 'flow', ic: '📊', label: '画流程图', text: '证据流：诊断/面经/项目 → 证据库 → 能力估计（含置信度）→ 差距分析 → 学习任务 → 复测 → 新证据。闭环循环，每一步都可追溯。' },
];

export default function Learning() {
  const navigate = useNavigate();
  const [helpOpen, setHelpOpen] = useState(false);
  const [helpItem, setHelpItem] = useState<(typeof HELP_ITEMS)[number] | null>(null);
  const { data, loading, error } = useApi<LearningData>('/learning');

  const today: (LearningTaskView & { domain?: string })[] = data?.today?.length
    ? data.today.map((t) => ({ ...t, domain: t.skill_name || '' }))
    : TODAY_TASKS.map((t) => ({
        id: t.id as any, title: t.title, kind: t.type, why: t.why, est_minutes: t.minutes,
        acceptance: t.criteria, status: 'PENDING', domain: t.domain,
      }));

  const retest = data?.retest_plan?.length
    ? data.retest_plan.map((r) => ({
        day: `${r.due_in_days <= 0 ? '今天' : `${r.due_in_days} 天后`}`, date: r.due_date.slice(0, 10),
        tasks: [r.title], id: r.id,
      }))
    : RETEST_PLAN.map((r, i) => ({ ...r, id: `t3-${i}` }));

  return (
    <div>
      <PageHead
        title="学习中心"
        sub="只学你真正缺少的：任务由证据缺口生成，完成后自动沉淀新证据"
      >
        <button className="btn btn-outline" onClick={() => navigate('/profile')}>查看能力画像</button>
        <button className="btn btn-primary" onClick={() => navigate('/exam/diagnosis')}>开始一次诊断</button>
      </PageHead>
      <DemoBanner />

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>正在从后端加载实时数据…</div>}
      {error && <div className="demo-banner demo-banner-err" style={{ marginBottom: 12 }}>后端不可用（{error}），以下为静态演示数据</div>}

      <div className="card-head" style={{ margin: '0 0 10px' }}>
        <span className="card-title">今日任务 <span className="chip chip-purple" style={{ marginLeft: 6 }}>{today.length}</span></span>
      </div>
      <div className="grid grid-3" style={{ marginBottom: 22 }}>
        {today.map((t) => (
          <div className="task-card" key={t.id}>
            <div className="task-head">
              <div className="exam-ic" style={{ width: 36, height: 36, fontSize: 16 }}>{TYPE_IC[t.kind] || '📖'}</div>
              <span className={`chip ${TYPE_CLS[t.kind] || 'chip-slate'}`}>{TYPE_NAME[t.kind] || t.kind}</span>
            </div>
            <div className="task-title" style={{ marginTop: 10 }}>{t.title}</div>
            {t.why && <div className="task-why">为什么学：<b>{t.why}</b></div>}
            <div className="task-meta">
              <span>⏱ 预计 {t.est_minutes} 分钟</span>
              {t.domain && <><span className="sep" /><span>域：{t.domain}</span></>}
            </div>
            {t.acceptance && (
              <div className="hint" style={{ margin: '8px 0 12px' }}>
                <b>完成标准：</b>{t.acceptance}
              </div>
            )}
            <button className="btn btn-primary" style={{ width: '100%' }} onClick={() => navigate(`/learning/task/${t.id}`)}>
              {t.kind === 'RETEST' ? '开始复测' : '开始任务'}
            </button>
          </div>
        ))}
      </div>

      <div className="grid grid-1-2">
        <div className="card">
          <div className="card-head">
            <span className="card-title">复测安排</span>
            <span className="card-sub">记忆衰减曲线 · 1/3/7 天</span>
          </div>
          {retest.map((r: any) => (
            <div className="list-row" key={r.id || r.day}>
              <div className="list-ic">📅</div>
              <div className="list-main">
                <div className="list-title">{r.day} · {r.date}</div>
                <div className="list-sub">{r.tasks.join('、')}</div>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/learning/task/${r.id}`)}>去复测</button>
            </div>
          ))}
        </div>

        <div className="card">
          <div className="card-head">
            <span className="card-title">学习提示</span>
            <span className="card-sub">示例</span>
          </div>
          <div className="list-row">
            <div className="list-ic">💡</div>
            <div className="list-main">
              <div className="list-title">如何高效利用证据报告</div>
              <div className="list-sub">优先处理「低于目标 + 高权重」的能力域，一次只补一个短板</div>
            </div>
          </div>
          <div className="list-row">
            <div className="list-ic">🎯</div>
            <div className="list-main">
              <div className="list-title">复测为什么重要</div>
              <div className="list-sub">复测通过才会把能力状态从「待验证」提升为「达到目标」</div>
            </div>
          </div>
        </div>
      </div>

      {/* 学习帮助浮动按钮 */}
      <div className="help-fab" onClick={() => setHelpOpen(!helpOpen)} title="学习帮助">
        ❓
      </div>
      {helpOpen && (
        <div className="help-menu">
          <div className="help-menu-head">
            <b>需要帮助？</b>
            <button className="modal-x" onClick={() => setHelpOpen(false)}>✕</button>
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
