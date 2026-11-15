import React from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { useAppState } from './AppState';

const NAV = [
  { num: '01', label: '总览', to: '/' },
  { num: '02', label: '岗位对标', to: '/benchmark' },
  { num: '03', label: '能力画像', to: '/profile' },
  { num: '04', label: '考试中心', to: '/exam' },
  { num: '05', label: '学习中心', to: '/learning' },
  { num: '06', label: '面经库', to: '/bank' },
  { num: '07', label: '项目与职业资产', to: '/assets' },
  { num: '08', label: '记忆中心', to: '/memory' },
  { num: '09', label: '系统观测台', to: '/observatory' },
  { num: '10', label: '进化实验室', to: '/evolution' },
];

export default function Sidebar() {
  const { setWsOpen } = useAppState();
  const navigate = useNavigate();

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
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
            end={n.to === '/'}
          >
            <span className="nav-num">{n.num}</span>
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
        <NavLink to="/settings" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
          <span className="nav-num">⚙</span>
          <span>设置与工作空间</span>
        </NavLink>
      </div>
    </aside>
  );
}
