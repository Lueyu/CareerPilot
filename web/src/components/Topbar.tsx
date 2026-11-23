import React, { useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useAppState } from './AppState';

const PAGE_NAMES: Record<string, string> = {
  '/': '首页',
  '/benchmark': '能力与岗位 · 岗位对标',
  '/profile': '能力与岗位 · 能力画像',
  '/exam': '考试与面经 · 考试中心',
  '/exam/diagnosis': '独立诊断',
  '/exam/diagnosis/report': '诊断报告',
  '/study/chat': '学习聊天',
  '/study/notes': '我的笔记 / 复习',
  '/learning': '学习计划',
  '/bank': '面试题库',
  '/assets': '项目与简历',
  '/memory': '记忆中心',
  '/observatory': '系统观测台',
  '/evolution': '进化实验室',
  '/settings': '设置',
};

type Theme = 'light' | 'dark';

function currentTheme(): Theme {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}

export default function Topbar({ beforeCrumb }: { beforeCrumb?: React.ReactNode }) {
  const { pathname } = useLocation();
  const { setCmdOpen, setQuickOpen, setNotifOpen } = useAppState();
  const [theme, setTheme] = useState<Theme>(currentTheme);
  const name =
    PAGE_NAMES[pathname] ??
    // 动态路由按前缀匹配
    (Object.entries(PAGE_NAMES).sort((a, b) => b[0].length - a[0].length).find(([p]) => p !== '/' && pathname.startsWith(p))?.[1] ?? '总览');

  const toggleTheme = () => {
    const next: Theme = theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('cp-theme', next);
    setTheme(next);
  };

  return (
    <header className="topbar">
      <div className="crumb">
        {beforeCrumb}
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
        <button
          className="icon-btn"
          onClick={toggleTheme}
          title={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式（护眼）'}
          aria-label="切换主题"
        >
          {theme === 'dark' ? '☀️' : '🌙'}
        </button>
        <div style={{ position: 'relative' }}>
          <button className="icon-btn" onClick={() => setNotifOpen(true)}>
            🔔

          </button>
        </div>
      </div>
    </header>
  );
}
