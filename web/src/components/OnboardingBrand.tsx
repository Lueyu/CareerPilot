import { useT } from '../i18n';
export default function OnboardingBrand() {
  const t = useT();
  return <div className="ob-brand">
    <div className="brand-logo">CP</div>
    <div>
      <div className="ob-brand-name">CareerPilot</div>
      <div className="ob-brand-sub">{t('shell.brandTagline')}</div>
    </div>
  </div>;
}
