import { useT } from '../i18n';
import { getWorkspace, sendLocalFeedback, workspaceStorageKey } from '../api/client';
import React, { useContext, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { WorkspaceApiContext } from '../seed/api';
import FeedbackDialog from './FeedbackDialog';
import LanguageSelector from './LanguageSelector';

const PAGE_NAMES: Record<string, string> = {
  '/': 'shell.home',
  '/benchmark': 'shell.benchmarkBreadcrumb',
  '/profile': 'shell.profileBreadcrumb',
  '/exam': 'shell.examBreadcrumb',
  '/exam/diagnosis': 'shell.diagnosis',
  '/exam/diagnosis/report': 'shell.diagnosisReport',
  '/study/chat': 'shell.studyChat',
  '/study/notes': 'shell.studyNotes',
  '/learning': 'shell.learningPlan',
  '/bank': 'shell.questionBank',
  '/podcasts': 'shell.podcasts',
  '/assets': 'shell.projectsResume',
  '/memory': 'shell.memoryCenter',
  '/observatory': 'shell.observatory',
  '/evolution': 'shell.evolutionLab',
  '/settings': 'shell.settings',
  '/settings/account': 'shell.accountProfile',
  '/beta': 'shell.betaRecords',
  '/beta/bank': 'shell.betaBank',
  '/beta/study/chat': 'shell.betaCoach',
  '/beta/study/notes': 'shell.betaNotes',
  '/beta/exam': 'shell.betaExam',
  '/beta/learning': 'shell.betaCalendar',
  '/beta/memory': 'shell.betaMemory',
};

type Theme = 'light' | 'dark';

export function currentTheme(): Theme {
  try {
    const saved = localStorage.getItem(workspaceStorageKey('cp-theme'));
    if (saved === 'light' || saved === 'dark') return saved;
    if (workspaceStorageKey('cp-theme') !== 'cp-theme') return 'dark';
  } catch {
    // Theme remains usable when storage is unavailable.
  }
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

export interface TopbarActions {
  search: () => void;
  quick: () => void;
  notifications: () => void;
}

export default function Topbar({ beforeCrumb, workspaceLabel, actions }: {
  beforeCrumb?: React.ReactNode;
  workspaceLabel: string;
  actions?: TopbarActions;
}) {
  const t = useT();
  const { pathname } = useLocation();
  const workspaceApi = useContext(WorkspaceApiContext);
  const [theme, setTheme] = useState<Theme>(currentTheme);
  React.useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  const nameKey =
    PAGE_NAMES[pathname] ??
    // 动态路由按前缀匹配
    (Object.entries(PAGE_NAMES).sort((a, b) => b[0].length - a[0].length).find(([p]) => p !== '/' && pathname.startsWith(p))?.[1] ?? 'shell.overview');

  const toggleTheme = () => {
    const next: Theme = theme === 'dark' ? 'light' : 'dark';
    try {
      localStorage.setItem(workspaceStorageKey('cp-theme'), next);
    } catch {
      // The preference still applies to the current view.
    }
    setTheme(next);
  };

  return (
    <header className="topbar">
      <div className="crumb">
        {beforeCrumb}
        <span>{workspaceLabel}</span>
        <span className="crumb-sep">/</span>
        <span className="crumb-cur">{t(nameKey)}</span>
      </div>
      <div className="topbar-right">
        {actions && (
          <>
            <button className="search-box" type="button" onClick={actions.search}
              aria-label={t('shell.search')} aria-haspopup="dialog" title={t('shell.search')}>
              <span aria-hidden="true">🔍</span>
              <span className="topbar-search-label">{t('shell.searchPrompt')}</span>
              <span className="kbd" aria-hidden="true">⌘K</span>
            </button>
            <button className="btn btn-primary" type="button" onClick={actions.quick}
              aria-label={t('shell.quickActions')} aria-haspopup="dialog" title={t('shell.quickActions')}>
              <span aria-hidden="true">+</span>
              <span className="topbar-quick-label">{t('shell.quickActions')}</span>
            </button>
          </>
        )}
        <LanguageSelector />
        <FeedbackDialog key={getWorkspace()} send={workspaceApi
          ? (submission, signal) => workspaceApi.send('/product-feedback', 'POST', submission, {
            signal, mode: 'same-origin', redirect: 'error', expectedStatus: 200,
          })
          : sendLocalFeedback} />
        <button
          className="icon-btn"
          onClick={toggleTheme}
          title={theme === 'dark' ? t('shell.lightTheme') : t('shell.darkTheme')}
          aria-label={t('shell.toggleTheme')}
        >
          {theme === 'dark' ? '☀️' : '🌙'}
        </button>
        {actions && <div style={{ position: 'relative' }}>
          <button className="icon-btn" onClick={actions.notifications} aria-label={t('shell.notifications')}>
            🔔
          </button>
        </div>}
      </div>
    </header>
  );
}
