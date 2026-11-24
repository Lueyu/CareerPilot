import { useT } from '../i18n';
import OnboardingBrand from '../components/OnboardingBrand';
import LanguageSelector from '../components/LanguageSelector';
import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { getWorkspace, useApi, workspaceLogin } from '../api/client';

export function DiagnosisModelReadiness() {
  const t = useT();
  const applicable = !workspaceLogin && getWorkspace() !== 'demo';
  const { data, loading, error } = useApi<{ configured: boolean } | null>(
    applicable ? '/settings/llm/readiness' : null,
  );
  if (!applicable || loading || (!data && !error)) return null;

  const message = error
    ? t('shell.readinessUnavailable')
    : data?.configured
      ? t('shell.readinessConfigured')
      : t('shell.readinessUnconfigured');

  return (
    <div className="card" style={{ margin: '12px 0 16px' }}>
      <div className="card-head">
        <span className="card-title">{t('shell.readinessTitle')}</span>
        <Link className="link" to="/settings?tab=models">{t('shell.modelSettings')}</Link>
      </div>
      <p className="hint" role="status">{message}</p>
    </div>
  );
}

export default function Onboarding() {
  const t = useT();
  const navigate = useNavigate();
  const [titleBefore, titleAfter] = t('shell.onboardingTitle', { focus: '{focus}' }).split('{focus}');
  return (
    <div className="ob-page">
      <div className="ob-glow" />
      <div className="ob-glow2" />
      <div className="workspace-onboarding-heading"><OnboardingBrand /><LanguageSelector /></div>
      <div className="ob-hero">
        <h1 className="ob-title">
          {titleBefore}<span className="grad">{t('shell.onboardingFocus')}</span>{titleAfter}
        </h1>
        <p className="ob-desc">
          {t('shell.onboardingDescription')}
        </p>
        <DiagnosisModelReadiness />
        <div className="ob-cta">
          <button className="btn btn-primary btn-lg" style={{ fontSize: 15, padding: '12px 30px' }} onClick={() => navigate('/')}>
            {t('shell.onboardingStart')}
          </button>
        </div>
      </div>
      <div className="ob-feats">
        <div className="ob-feat">
          <div className="ob-feat-ic">🧾</div>
          <b>{t('shell.onboardingEvidence')}</b>
          <p>{t('shell.onboardingEvidenceHint')}</p>
        </div>
        <div className="ob-feat">
          <div className="ob-feat-ic">🎯</div>
          <b>{t('shell.onboardingAdaptive')}</b>
          <p>{t('shell.onboardingAdaptiveHint')}</p>
        </div>
        <div className="ob-feat">
          <div className="ob-feat-ic">📦</div>
          <b>{t('shell.onboardingPortfolio')}</b>
          <p>{t('shell.onboardingPortfolioHint')}</p>
        </div>
      </div>
      <div className="ob-foot">{t('shell.onboardingTagline')}{getWorkspace() === 'demo' && t('shell.onboardingDemoNote')}</div>
    </div>
  );
}
