import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppState } from '../components/AppState';
import { DemoBanner, Modal, PageHead } from '../components/ui';
import { DRAFT_CONTENT, PIPELINE_STEPS, PRS } from '../data/demo';

export default function Pipeline() {
  const navigate = useNavigate();
  const { toast } = useAppState();
  const [step, setStep] = useState(4); // 当前推进到的步骤下标
  const [rollbackOpen, setRollbackOpen] = useState(false);

  const next = () => {
    if (step < PIPELINE_STEPS.length - 1) setStep(step + 1);
    else toast('🎉', '发布完成：职业展示已更新');
  };

  return (
    <div>
      <PageHead title="职业资产更新流水线" sub="Squabble · 最近一次变更 · 检测于 2026-11-15 09:58">
        <button className="btn btn-outline" onClick={() => navigate('/assets')}>← 返回项目资产</button>
      </PageHead>
      <DemoBanner />

      <div className="card" style={{ marginBottom: 18 }}>
        <div className="pipeline">
          {PIPELINE_STEPS.map((s, i) => (
            <React.Fragment key={s}>
              {i > 0 && <span className="pl-arrow">→</span>}
              <span className={`pl-step ${i < step ? 'done' : i === step ? 'now' : ''}`}>
                {i < step ? '✓' : i === step ? '⟳' : `${i + 1}`} {s}
              </span>
            </React.Fragment>
          ))}
        </div>
      </div>

      {step === 4 && (
        <div className="card" style={{ marginBottom: 18 }}>
          <div className="card-head">
            <span className="card-title">检查内容草稿</span>
            <span className={`chip ${DRAFT_CONTENT.risk === '低' ? 'chip-green' : 'chip-red'}`}>风险分级：{DRAFT_CONTENT.risk}</span>
          </div>
          <h3 style={{ fontSize: 16, fontWeight: 650 }}>{DRAFT_CONTENT.title}</h3>
          <div className="hint" style={{ margin: '4px 0 10px' }}>{DRAFT_CONTENT.riskReason}</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {DRAFT_CONTENT.changed.map((c) => (
              <div className="list-row" key={c} style={{ padding: '7px 2px' }}>
                <span className="chip chip-green">变更</span>
                <span style={{ fontSize: 13 }}>{c}</span>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 12, display: 'flex', gap: 10 }}>
            <button className="btn btn-primary" onClick={next}>草稿无误，继续 →</button>
            <button className="btn btn-outline" onClick={() => navigate('/assets/project/squabble')}>重新审查证据</button>
          </div>
        </div>
      )}

      {step === 5 && (
        <div className="card" style={{ marginBottom: 18 }}>
          <div className="card-head">
            <span className="card-title">发布前检查</span>
            <span className="chip chip-green">✓ 通过</span>
          </div>
          <div className="list-row">
            <div className="list-ic">🏗️</div>
            <div className="list-main">
              <div className="list-title">构建检查</div>
              <div className="list-sub">{DRAFT_CONTENT.build}</div>
            </div>
            <span className="chip chip-green">通过</span>
          </div>
          <div className="list-row">
            <div className="list-ic">🔗</div>
            <div className="list-main">
              <div className="list-title">风险声明核验</div>
              <div className="list-sub">未验证声明（沙箱隔离、高可用）已从展示中移除或降级</div>
            </div>
            <span className="chip chip-green">通过</span>
          </div>
          <div style={{ marginTop: 12, display: 'flex', gap: 10 }}>
            <button className="btn btn-primary" onClick={next}>创建 PR →</button>
          </div>
        </div>
      )}

      {step === 6 && (
        <div className="card" style={{ marginBottom: 18 }}>
          <div className="card-head">
            <span className="card-title">PR 已创建，等待合并</span>
            <span className="chip chip-amber">等待合并</span>
          </div>
          {PRS.map((pr) => (
            <div className="list-row" key={pr.id}>
              <div className="list-ic">🔀</div>
              <div className="list-main">
                <div className="list-title">{pr.id} · {pr.title}</div>
                <div className="list-sub">{pr.repo} · {pr.time}</div>
              </div>
              <span className={`chip ${pr.state === '已合并' ? 'chip-green' : 'chip-amber'}`}>{pr.state}</span>
            </div>
          ))}
          <div style={{ marginTop: 12, display: 'flex', gap: 10 }}>
            <button className="btn btn-primary" onClick={next}>PR 已合并，发布 →</button>
            <button className="btn btn-outline" onClick={() => toast('⏸️', '已暂停，等待人工合并')}>稍后处理</button>
          </div>
        </div>
      )}

      {step === 7 && (
        <div className="card" style={{ marginBottom: 18 }}>
          <div className="card-head">
            <span className="card-title">发布完成</span>
            <span className="chip chip-green">已发布</span>
          </div>
          <div className="score-box" style={{ padding: '30px 20px' }}>
            <div style={{ fontSize: 32 }}>🚀</div>
            <h3 style={{ fontSize: 16, fontWeight: 650, marginTop: 8 }}>职业展示已更新至 lueyu.github.io · v1.9</h3>
            <p className="hint" style={{ textAlign: 'center', marginTop: 6 }}>能力画像已重新评估：Squabble 项目证据覆盖 +1，Agent Evaluation 置信度提升至 0.71</p>
          </div>
          <div style={{ marginTop: 14, display: 'flex', gap: 10 }}>
            <button className="btn btn-outline" onClick={() => navigate('/profile')}>查看能力画像</button>
            <button className="btn btn-danger" onClick={() => setRollbackOpen(true)}>↩ 回滚发布</button>
          </div>
        </div>
      )}

      <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <div className="card-title" style={{ margin: 0 }}>自动化规则</div>
          <p className="hint" style={{ margin: '4px 0 0' }}>低风险内容自动发布 · 高风险转人工审批（可在设置中调整）</p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          {step < PIPELINE_STEPS.length - 1 && (
            <button className="btn btn-primary" onClick={next}>执行下一步</button>
          )}
          <button className="btn btn-outline" onClick={() => navigate('/assets')}>返回项目资产</button>
        </div>
      </div>

      {rollbackOpen && (
        <Modal title="回滚发布？" size="sm" onClose={() => setRollbackOpen(false)}
          footer={
            <>
              <button className="btn btn-outline" onClick={() => setRollbackOpen(false)}>取消</button>
              <button className="btn btn-danger" onClick={() => { setRollbackOpen(false); setStep(7); toast('↩️', '已回滚至 lueyu.github.io · v1.8'); }}>确认回滚</button>
            </>
          }>
          <p style={{ fontSize: 13.5, color: 'var(--text-2)', lineHeight: 1.7 }}>
            将职业展示回滚至 <b>v1.8</b>，本次发布产生的证据仍保留，不会删除能力画像数据。
          </p>
        </Modal>
      )}
    </div>
  );
}
