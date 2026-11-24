import { getWorkspace, sendLocalFeedback, workspaceStorageKey } from '../api/client';
import React, { useContext, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { WorkspaceApiContext } from '../seed/api';
import FeedbackDialog from './FeedbackDialog';

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
  '/settings/account': '账户与资料',
  '/beta': 'Beta 记录',
  '/beta/bank': 'Beta 题库',
  '/beta/study/chat': 'Beta 教练',
  '/beta/study/notes': 'Beta 笔记',
  '/beta/exam': 'Beta 答题',
  '/beta/learning': 'Beta 学习日历',
  '/beta/memory': 'Beta 记忆',
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
  const { pathname } = useLocation();
  const workspaceApi = useContext(WorkspaceApiContext);
  const [theme, setTheme] = useState<Theme>(currentTheme);
  React.useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  const name =
    PAGE_NAMES[pathname] ??
    // 动态路由按前缀匹配
    (Object.entries(PAGE_NAMES).sort((a, b) => b[0].length - a[0].length).find(([p]) => p !== '/' && pathname.startsWith(p))?.[1] ?? '总览');

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
        <span className="crumb-cur">{name}</span>
      </div>
      <div className="topbar-right">
        {actions && (
          <>
            <button className="search-box" type="button" onClick={actions.search}
              aria-label="全局搜索" aria-haspopup="dialog" title="全局搜索">
              <span aria-hidden="true">🔍</span>
              <span className="topbar-search-label">搜索技能、证据、任务</span>
              <span className="kbd" aria-hidden="true">⌘K</span>
            </button>
            <button className="btn btn-primary" type="button" onClick={actions.quick}
              aria-label="快捷操作" aria-haspopup="dialog" title="快捷操作">
              <span aria-hidden="true">+</span>
              <span className="topbar-quick-label">快捷操作</span>
            </button>
          </>
        )}
        <FeedbackDialog key={getWorkspace()} send={workspaceApi
          ? (submission, signal) => workspaceApi.send('/product-feedback', 'POST', submission, {
            signal, mode: 'same-origin', redirect: 'error', expectedStatus: 200,
          })
          : sendLocalFeedback} />
        <button
          className="icon-btn"
          onClick={toggleTheme}
          title={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式（护眼）'}
          aria-label="切换主题"
        >
          {theme === 'dark' ? '☀️' : '🌙'}
        </button>
        {actions && <div style={{ position: 'relative' }}>
          <button className="icon-btn" onClick={actions.notifications}>
            🔔
          </button>
        </div>}
      </div>
    </header>
  );
}
