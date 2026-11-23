import React from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';

// —— v3.1 信息架构: 一级导航 5 项(合并页), 高级功能收进次级区 ——

type NavItem = { ic: string; label: string; to: string; match: (p: string) => boolean };

const HOME: NavItem = { ic: '🏠', label: '首页', to: '/', match: (p) => p === '/' };
const EXAM: NavItem = { ic: '📝', label: '考试与面经', to: '/exam', match: (p) => p === '/exam' || p.startsWith('/exam/') };
const STUDY: NavItem = { ic: '💬', label: '学习聊天', to: '/study/chat', match: (p) => p.startsWith('/study/') };
const LEARNING: NavItem = { ic: '📚', label: '学习计划', to: '/learning', match: (p) => p.startsWith('/learning') };
const MEMORY: NavItem = { ic: '🧠', label: '记忆中心', to: '/memory', match: (p) => p.startsWith('/memory') };

const NAV: NavItem[] = [
  HOME,
  { ic: '🎯', label: '能力与岗位', to: '/benchmark', match: (p) => p === '/benchmark' || p.startsWith('/profile') },
  { ...EXAM, match: (p) => EXAM.match(p) || p === '/bank' },
  { ...STUDY, label: '学习聊天与笔记' },
  LEARNING,
  { ic: '🚀', label: '项目与简历', to: '/assets', match: (p) => p.startsWith('/assets') },
];

const ADVANCED: NavItem[] = [
  MEMORY,
  { ic: '📡', label: '系统观测台', to: '/observatory', match: (p) => p.startsWith('/observatory') },
  { ic: '🧬', label: '进化实验室', to: '/evolution', match: (p) => p.startsWith('/evolution') },
];

const REGISTERED_NAV: NavItem[] = [
  HOME,
  { ic: '📚', label: '面试题库', to: '/bank', match: (p) => p === '/bank' },
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
          <div className="brand-sub">CAREER INTELLIGENCE</div>
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
            title={n.label}
            className={itemCls(n.match(pathname))}
            end={n.to === '/'}
          >
            <span className="nav-num">{n.ic}</span>
            <span>{n.label}</span>
          </NavLink>
        ))}
      </nav>

      {personal && (
        <>
          <div className="nav-adv-label">高级区</div>
          <nav className="nav nav-adv">
            {ADVANCED.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                title={n.label}
                className={`nav-item nav-item-sub ${n.match(pathname) ? 'active' : ''}`}
              >
                <span className="nav-num">{n.ic}</span>
                <span>{n.label}</span>
              </NavLink>
            ))}
          </nav>
        </>
      )}

      <div className="side-tip">
        <span className="side-tip-ic">🎯</span>
        <div>
          <b>只学你真正缺少的</b>
          <p>证据驱动，不刷题、不堆时长。</p>
        </div>
      </div>

      <div className="nav-settings">
        <NavLink to="/settings" title="设置与工作空间" className={itemCls(pathname.startsWith('/settings'))}>
          <span className="nav-num">⚙</span>
          <span>设置与工作空间</span>
        </NavLink>
      </div>
    </aside>
  );
}
