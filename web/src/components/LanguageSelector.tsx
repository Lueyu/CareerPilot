import { setLocale, useLocale, useT } from '../i18n';
import './languageSelector.css';

export default function LanguageSelector({ compact = false }: { compact?: boolean }) {
  const locale = useLocale();
  const t = useT();
  return (
    <select className="language-selector" aria-label={t('core.language')} title={t('core.language')}
      value={locale} onChange={event => setLocale(event.target.value === 'en' ? 'en' : 'zh-CN')}>
      <option value="zh-CN" lang="zh-CN">{compact ? '中文' : t('core.chinese')}</option>
      <option value="en" lang="en">{compact ? 'EN' : t('core.english')}</option>
    </select>
  );
}
