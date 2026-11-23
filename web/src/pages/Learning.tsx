import React, { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { DemoBanner, PageHead, Modal } from '../components/ui';
import { useApi, LearningData } from '../api/client';
import DailyLearningPlan from './DailyLearningPlan';


const TYPE_CLS: Record<string, string> = { UNDERSTAND: 'chip-blue', PRACTICE: 'chip-purple', RETEST: 'chip-amber' };
const TYPE_IC: Record<string, string> = { UNDERSTAND: '📖', PRACTICE: '🛠️', RETEST: '🔁' };
const TYPE_NAME: Record<string, string> = { UNDERSTAND: '理解', PRACTICE: '实践', RETEST: '复测' };

const HELP_ITEMS = [
  { key: 'simple', ic: '💡', label: '解释得更简单', text: '把「证据驱动评估」讲得简单一点：就像体检报告——不是靠感觉说你健康，而是量血压、测血糖，用数据说话。你的每次诊断、面经、实践就是一次"体检项目"。' },
  { key: 'analogy', ic: '🔁', label: '换个类比', text: 'CareerPilot 像一个懂行的健身教练：先测你的力量基线（独立诊断），再按短板安排训练（学习任务），隔几天复测确认进步（1/3/7 天复测），最后把你的训练记录整理成简历素材（职业资产）。' },
  { key: 'stuck', ic: '🙋', label: '我还是没理解', text: '没关系，可以把具体困惑发给我，我会换一个角度重新讲解，或直接生成一道例题带你过一遍。请在具体任务中使用学习帮助。' },
  { key: 'flow', ic: '📊', label: '画流程图', text: '证据流：诊断/面经/项目 → 证据库 → 能力估计（含观察覆盖说明）→ 差距分析 → 学习任务 → 复测 → 新证据。观察覆盖不代表评分准确率。闭环循环，每一步都可追溯。' },
];

export default function Learning() {
  const [params] = useSearchParams();
  return params.get('plan') === 'history' ? <LearningHistory /> : <DailyLearningPlan />;
}

function LearningHistory() {
  const navigate = useNavigate();
  const [helpOpen, setHelpOpen] = useState(false);
  const [helpItem, setHelpItem] = useState<(typeof HELP_ITEMS)[number] | null>(null);
  const { data, loading, error } = useApi<LearningData>('/learning');

  const today = (data?.today ?? []).map(t => ({ ...t, domain: t.skill_name || t.skill || '' }));
  const retest = (data?.retest_plan ?? []).map(r => ({ day: r.due_in_days == null ? '未安排' : r.due_in_days <= 0 ? '已到期' : `${r.due_in_days} 天后`, date: r.due_date?.slice(0, 10) ?? '待定', tasks: [r.title], id: r.id, due: r.due_in_days != null && r.due_in_days <= 0 }));

  return (
    <div>
      <PageHead
        title="学习中心"
        sub="任务关联本次作答的具体失分项；完成学习记录进度，新题验证再生成表现证据"
      >
        <Link className="btn btn-outline" to="/learning">返回学习计划</Link>
        <button className="btn btn-outline" onClick={() => navigate('/profile')}>查看能力画像</button>
        <button className="btn btn-primary" onClick={() => navigate('/exam/diagnosis')}>开始一次诊断</button>
      </PageHead>
      <DemoBanner />

      {loading && <div className="demo-banner" style={{ marginBottom: 12 }}>正在从后端加载实时数据…</div>}
      {error && <div className="demo-banner demo-banner-err" style={{ marginBottom: 12 }}>后端不可用（{error}），无法读取当前空间任务</div>}

      <div className="card-head" style={{ margin: '0 0 10px' }}>
        <span className="card-title">今日任务 <span className="chip chip-purple" style={{ marginLeft: 6 }}>{today.length}</span></span>
      </div>
      <div className="grid grid-3" style={{ marginBottom: 22 }}>
        {!loading && !error && today.length === 0 && <div className="card">当前没有学习任务。完成诊断后，具体失分项会生成任务。</div>}
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

      {!!data?.completed?.length && <div className="card" style={{ marginBottom: 18 }}>
        <div className="card-head"><span className="card-title">学习与复测记录</span><span className="card-sub">学习进度不等于能力证明</span></div>
        {data.completed.map(t => <div className="list-row" key={t.id}>
          <div className="list-main"><div className="list-title">{t.title}</div><div className="list-sub">已记录学习进度，可打开任务进行新题验证</div></div>
          <button className="btn btn-outline" onClick={() => navigate(`/learning/task/${t.id}`)}>{t.kind === 'RETEST' ? '查看复测结果' : '查看任务与即时验证'}</button>
        </div>)}
      </div>}

      <div className="grid grid-1-2">
        <div className="card">
          <div className="card-head">
            <span className="card-title">复测安排</span>
            <span className="card-sub">从完成学习起 · 1/3/7 天</span>
          </div>
          {!loading && !error && retest.length === 0 && <p className="hint">还没有复测安排。完成学习后从当日安排 1/3/7 天复测。</p>}
          {retest.map((r: any) => (
            <div className="list-row" key={r.id || r.day}>
              <div className="list-ic">📅</div>
              <div className="list-main">
                <div className="list-title">{r.day} · {r.date}</div>
                <div className="list-sub">{r.tasks.join('、')}</div>
              </div>
              <button className="btn btn-ghost btn-sm" onClick={() => navigate(`/learning/task/${r.id}`)}>{r.due ? '去复测' : '查看安排'}</button>
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
              <div className="list-sub">完成学习不等于能力已提升。新题验证记录本次表现；即时验证不代表长期保持。</div>
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
