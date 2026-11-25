import bankZh from './locales/zh-CN/bank.json' with { type: 'json' };
import bankEn from './locales/en/bank.json' with { type: 'json' };
import studyZh from './locales/zh-CN/study.json' with { type: 'json' };
import studyEn from './locales/en/study.json' with { type: 'json' };
import projectsZh from './locales/zh-CN/projects.json' with { type: 'json' };
import projectsEn from './locales/en/projects.json' with { type: 'json' };
import progressZh from './locales/zh-CN/progress.json' with { type: 'json' };
import progressEn from './locales/en/progress.json' with { type: 'json' };
import registeredZh from './locales/zh-CN/registered.json' with { type: 'json' };
import registeredEn from './locales/en/registered.json' with { type: 'json' };
import shellZh from './locales/zh-CN/shell.json' with { type: 'json' };
import shellEn from './locales/en/shell.json' with { type: 'json' };
import coreZh from './locales/zh-CN/core.json' with { type: 'json' };
import coreEn from './locales/en/core.json' with { type: 'json' };
import { useSyncExternalStore } from 'react';

export type Locale = 'zh-CN' | 'en';
type Catalog = Record<string, Record<string, string>>;
type Values = Readonly<Record<string, string | number>>;
const catalogs: Record<Locale, Catalog> = {
  'zh-CN': { bank: bankZh, study: studyZh, projects: projectsZh, progress: progressZh, registered: registeredZh, shell: shellZh, core: coreZh },
  en: { bank: bankEn, study: studyEn, projects: projectsEn, progress: progressEn, registered: registeredEn, shell: shellEn, core: coreEn },
};
const preferenceKey = 'careerpilot.locale';
const listeners = new Set<() => void>();

function preferredLocale(): Locale {
  if (typeof window === 'undefined') return 'zh-CN';
  try {
    const saved = window.localStorage?.getItem(preferenceKey);
    if (saved === 'en' || saved === 'zh-CN') return saved;
  } catch { /* Storage is optional in private browsing. */ }
  const languages = window.navigator?.languages ?? [window.navigator?.language];
  for (const language of languages) {
    if (/^en(?:-|$)/i.test(language ?? '')) return 'en';
    if (/^zh(?:-|$)/i.test(language ?? '')) return 'zh-CN';
  }
  return 'zh-CN';
}

let locale = preferredLocale();

export function getLocale(): Locale {
  return locale;
}

function translator(language: Locale) {
  return (key: string, values: Values = {}): string => {
    const split = key.indexOf('.');
    const namespace = key.slice(0, split);
    const id = key.slice(split + 1);
    const text = catalogs[language][namespace]?.[id] ?? catalogs['zh-CN'][namespace]?.[id];
    if (typeof text !== 'string') throw new Error(`Unknown locale key: ${key}`);
    return text.replace(/\{(\w+)\}/g, (placeholder, name: string) =>
      Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : placeholder);
  };
}
const translators = { 'zh-CN': translator('zh-CN'), en: translator('en') };

export function t(key: string, values?: Values): string {
  return translators[locale](key, values);
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function useLocale(): Locale {
  return useSyncExternalStore(subscribe, getLocale, getLocale);
}

export function useT() {
  return translators[useLocale()];
}

function select(localeToUse: Locale) {
  if (locale === localeToUse) return;
  locale = localeToUse;
  for (const listener of listeners) listener();
}

export function setLocale(language: Locale) {
  if (language !== 'zh-CN' && language !== 'en') return;
  try { window.localStorage.setItem(preferenceKey, language); } catch { /* Use the in-memory preference. */ }
  select(language);
}

export function initializeDocumentLocale() {
  function updateDocument() {
    document.documentElement.lang = locale;
    document.title = t('core.title');
    document.querySelector('meta[name="description"]')?.setAttribute('content', t('core.description'));
  }
  function onStorage(event: StorageEvent) {
    if (event.key === preferenceKey || event.key === null) select(preferredLocale());
  }
  updateDocument();
  const unsubscribe = subscribe(updateDocument);
  window.addEventListener('storage', onStorage);
  return () => {
    unsubscribe();
    window.removeEventListener('storage', onStorage);
  };
}
