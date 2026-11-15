import React from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { useAppState } from './AppState';

// —— v3.1 信息架构: 一级导航 5 项(合并页), 高级功能收进次级区 ——

const NAV: { ic: string; label: string; to: string; match: (p: string) => boolean }[] = [
  { ic: '🏠', label: '首页', to: '/', match: (p) => p === '/' },
  { ic: '🎯', label: '能力与岗位', to: '/benchmark', match: (p) => p === '/benchmark' || p.startsWith('/profile') },
  { ic: '📝', label: '考试与面经', to: '/exam', match: (p) => p === '/exam' || p.startsWith('/exam/') || p === '/bank' },
  { ic: '📚', label: '学习计划', to: '/learning', match: (p) => p.startsWith('/learning') },
  { ic: '🚀', label: '项目与简历', to: '/assets', match: (p) => p.startsWith('/assets') },
];

const ADVANCED: { ic: string; label: string; to: string; match: (p: string) => boolean }[] = [
  { ic: '🧠', label: '记忆中心', to: '/memory', match: (p) => p.startsWith('/memory') },
  { ic: '📡', label: '系统观测台', to: '/observatory', match: (p) => p.startsWith('/observatory') },
  { ic: '🧬', label: '进化实验室', to: '/evolution', match: (p) => p.startsWith('/evolution') },
];

export default function Sidebar() {
  const { setWsOpen } = useAppState();
  const navigate = useNavigate();
  const { pathname } = useLocation();

  const itemCls = (active: boolean) => `nav-item ${active ? 'active' : ''}`;

  return (
    <aside className="sidebar">
      <div className="brand" onClick={() => navigate('/')} style={{ cursor: 'pointer' }}>
        <div className="brand-logo">CP</div>
        <div>
          <div className="brand-name">CareerPilot</div>
          <div className="brand-sub">CAREER INTELLIGENCE</div>
        </div>
      </div>

      <div className="ws-card" onClick={() => setWsOpen(true)}>
        <div className="ws-avatar">🧭</div>
        <div className="ws-info">
          <div className="ws-label">个人工作空间</div>
          <div className="ws-name">求职作战室</div>
        </div>
        <span className="ws-caret">▾</span>
      </div>

      <nav className="nav">
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            className={itemCls(n.match(pathname))}
            end={n.to === '/'}
          >
            <span className="nav-num">{n.ic}</span>
            <span>{n.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="nav-adv-label">高级区</div>
      <nav className="nav nav-adv">
        {ADVANCED.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            className={`nav-item nav-item-sub ${n.match(pathname) ? 'active' : ''}`}
          >
            <span className="nav-num">{n.ic}</span>
            <span>{n.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="side-tip">
        <span className="side-tip-ic">🎯</span>
        <div>
          <b>只学你真正缺少的</b>
          <p>证据驱动，不刷题、不堆时长。</p>
        </div>
      </div>

      <div className="nav-settings">
        <NavLink to="/settings" className={itemCls(pathname.startsWith('/settings'))}>
          <span className="nav-num">⚙</span>
          <span>设置与工作空间</span>
        </NavLink>
      </div>
    </aside>
  );
}
