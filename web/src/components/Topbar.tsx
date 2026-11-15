import React from 'react';
import { useLocation } from 'react-router-dom';
import { useAppState } from './AppState';

const PAGE_NAMES: Record<string, string> = {
  '/': '总览',
  '/benchmark': '岗位对标',
  '/profile': '能力画像',
  '/exam': '考试中心',
  '/exam/diagnosis': '独立诊断',
  '/exam/diagnosis/report': '诊断报告',
  '/learning': '学习中心',
  '/bank': '面经库',
  '/assets': '项目与职业资产',
  '/memory': '记忆中心',
  '/observatory': '系统观测台',
  '/evolution': '进化实验室',
  '/settings': '设置',
};

export default function Topbar() {
  const { pathname } = useLocation();
  const { setCmdOpen, setQuickOpen, setNotifOpen } = useAppState();
  const name =
    PAGE_NAMES[pathname] ??
    // 动态路由按前缀匹配
    (Object.entries(PAGE_NAMES).sort((a, b) => b[0].length - a[0].length).find(([p]) => p !== '/' && pathname.startsWith(p))?.[1] ?? '总览');

  return (
    <header className="topbar">
      <div className="crumb">
        <span>个人工作空间</span>
        <span className="crumb-sep">/</span>
        <span className="crumb-cur">{name}</span>
      </div>
      <div className="topbar-right">
        <div className="search-box" onClick={() => setCmdOpen(true)}>
          <span>🔍</span>
          <input readOnly placeholder="搜索技能、证据、任务" />
          <span className="kbd">⌘K</span>
        </div>
        <button className="btn btn-primary" onClick={() => setQuickOpen(true)}>
          + 快捷操作
        </button>
        <div style={{ position: 'relative' }}>
          <button className="icon-btn" onClick={() => setNotifOpen(true)}>
            🔔
            <span className="icon-badge">2</span>
          </button>
        </div>
      </div>
    </header>
  );
}
