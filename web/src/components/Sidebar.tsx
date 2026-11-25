import { useT } from '../i18n';
import React from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';

// —— v3.1 信息架构: 一级导航 5 项(合并页), 高级功能收进次级区 ——

type NavItem = { ic: string; label: string; to: string; match: (p: string) => boolean };

const HOME: NavItem = { ic: '🏠', label: 'shell.home', to: '/', match: (p) => p === '/' };
const EXAM: NavItem = { ic: '📝', label: 'shell.examsInterviews', to: '/exam', match: (p) => p === '/exam' || p.startsWith('/exam/') };
const STUDY: NavItem = { ic: '💬', label: 'shell.studyChat', to: '/study/chat', match: (p) => p.startsWith('/study/') };
const LEARNING: NavItem = { ic: '📚', label: 'shell.learningPlan', to: '/learning', match: (p) => p.startsWith('/learning') };
const MEMORY: NavItem = { ic: '🧠', label: 'shell.memoryCenter', to: '/memory', match: (p) => p.startsWith('/memory') };

const NAV: NavItem[] = [
  HOME,
  { ic: '🎯', label: 'shell.skillsRoles', to: '/benchmark', match: (p) => p === '/benchmark' || p.startsWith('/profile') },
  { ...EXAM, match: (p) => EXAM.match(p) || p === '/bank' },
  { ...STUDY, label: 'shell.studyChatNotes' },
  LEARNING,
  { ic: '🚀', label: 'shell.projectsResume', to: '/assets', match: (p) => p.startsWith('/assets') },
];

const ADVANCED: NavItem[] = [
  MEMORY,
  { ic: '📡', label: 'shell.observatory', to: '/observatory', match: (p) => p.startsWith('/observatory') },
  { ic: '🧬', label: 'shell.evolutionLab', to: '/evolution', match: (p) => p.startsWith('/evolution') },
];

const REGISTERED_NAV: NavItem[] = [
  HOME,
  { ic: '📚', label: 'shell.questionBank', to: '/bank', match: (p) => p === '/bank' },
  STUDY,
  EXAM,
  LEARNING,
  MEMORY,
];

export type SidebarWorkspace = {
  label: string;
  name: string;
} & ({ kind: 'personal'; openSwitcher: () => void } | { kind: 'registered' });

export default function Sidebar({ workspace }: { workspace: SidebarWorkspace }) {
  const t = useT();
  const personal = workspace.kind === 'personal';
  const { pathname } = useLocation();

  const itemCls = (active: boolean) => `nav-item ${active ? 'active' : ''}`;

  const workspaceInfo = <>
    <span className="ws-avatar" aria-hidden="true">🧭</span>
    <span className="ws-info">
      <span className="ws-label">{workspace.label}</span>
      <span className="ws-name" title={workspace.name}>{workspace.name}</span>
    </span>
  </>;

  return (
    <aside className="sidebar">
      <Link className="brand" to="/">
        <div className="brand-logo">CP</div>
        <div>
          <div className="brand-name">CareerPilot</div>
          <div className="brand-sub">{t('shell.brandTagline')}</div>
        </div>
      </Link>

      {personal ? (
        <button type="button" className="ws-card" onClick={workspace.openSwitcher}>
          {workspaceInfo}
          <span className="ws-caret" aria-hidden="true">▾</span>
        </button>
      ) : <div className="ws-card ws-card-static">{workspaceInfo}</div>}

      <nav className="nav">
        {(personal ? NAV : REGISTERED_NAV).map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            title={t(n.label)}
            className={itemCls(n.match(pathname))}
            end={n.to === '/'}
          >
            <span className="nav-num">{n.ic}</span>
            <span>{t(n.label)}</span>
          </NavLink>
        ))}
      </nav>

      {personal && (
        <>
          <div className="nav-adv-label">{t('shell.advanced')}</div>
          <nav className="nav nav-adv">
            {ADVANCED.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                title={t(n.label)}
                className={`nav-item nav-item-sub ${n.match(pathname) ? 'active' : ''}`}
              >
                <span className="nav-num">{n.ic}</span>
                <span>{t(n.label)}</span>
              </NavLink>
            ))}
          </nav>
        </>
      )}

      <div className="side-tip">
        <span className="side-tip-ic">🎯</span>
        <div>
          <b>{t('shell.sidebarTipTitle')}</b>
          <p>{t('shell.sidebarTip')}</p>
        </div>
      </div>

      <div className="nav-settings">
        <NavLink to="/settings" title={t('shell.settingsWorkspace')} className={itemCls(pathname.startsWith('/settings'))}>
          <span className="nav-num">⚙</span>
          <span>{t('shell.settingsWorkspace')}</span>
        </NavLink>
      </div>
    </aside>
  );
}
