import OnboardingBrand from '../components/OnboardingBrand';
import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { getWorkspace, useApi, workspaceLogin } from '../api/client';

export function DiagnosisModelReadiness() {
  const applicable = !workspaceLogin && getWorkspace() !== 'demo';
  const { data, loading, error } = useApi<{ configured: boolean } | null>(
    applicable ? '/settings/llm/readiness' : null,
  );
  if (!applicable || loading || (!data && !error)) return null;

  const message = error
    ? '暂时无法读取模型配置。你可以在设置中查看模型状态。'
    : data?.configured
      ? '模型已配置。可在设置中点击「测试连接」，确认开放题评分所用的模型可以访问。'
      : '模型未配置。你可以先开始答题，开放题评分需要配置模型。';

  return (
    <div className="card" style={{ margin: '12px 0 16px' }}>
      <div className="card-head">
        <span className="card-title">诊断前的模型准备</span>
        <Link className="link" to="/settings?tab=models">模型设置</Link>
      </div>
      <p className="hint" role="status">{message}</p>
    </div>
  );
}

export default function Onboarding() {
  const navigate = useNavigate();
  return (
    <div className="ob-page">
      <div className="ob-glow" />
      <div className="ob-glow2" />
      <OnboardingBrand />
      <div className="ob-hero">
        <h1 className="ob-title">
          从一次诊断，开始你的<span className="grad">职业进阶</span>
        </h1>
        <p className="ob-desc">
          CareerPilot 用证据而非感觉评估你的能力：诊断、对标、学习、复测，
          每一步都有据可循。专为 AI Agent 应用开发工程师打造。
        </p>
        <DiagnosisModelReadiness />
        <div className="ob-cta">
          <button className="btn btn-primary btn-lg" style={{ fontSize: 15, padding: '12px 30px' }} onClick={() => navigate('/')}>
            开始体验 CareerPilot →
          </button>
        </div>
      </div>
      <div className="ob-feats">
        <div className="ob-feat">
          <div className="ob-feat-ic">🧾</div>
          <b>证据驱动</b>
          <p>诊断、面经、项目实践都会沉淀为证据，能力估计可追溯、可解释。</p>
        </div>
        <div className="ob-feat">
          <div className="ob-feat-ic">🎯</div>
          <b>自适应学习</b>
          <p>只学你真正缺少的：按能力差距生成任务，1/3/7 天自动安排复测。</p>
        </div>
        <div className="ob-feat">
          <div className="ob-feat-ic">📦</div>
          <b>职业资产自动化</b>
          <p>项目证据自动映射为职业展示，草稿、风险分级、发布全流程可审计。</p>
        </div>
      </div>
      <div className="ob-foot">Evidence-driven career growth platform · Demo 数据仅供演示</div>
    </div>
  );
}
