import React from 'react';
import { NavLink, useLocation } from 'react-router-dom';

type Tab =
  | { ic: string; label: string; to: string; match: (p: string) => boolean; more?: never }
  | { ic: string; label: string; more: true; to?: never; match?: never };

const TABS: Tab[] = [
  { ic: '🏠', label: '首页', to: '/', match: (p) => p === '/' },
  { ic: '📚', label: '题库', to: '/bank', match: (p) => p === '/bank' || p.startsWith('/bank/') },
  { ic: '💬', label: '学习聊天', to: '/study/chat', match: (p) => p.startsWith('/study/') },
  { ic: '📝', label: '考试与面经', to: '/exam', match: (p) => p.startsWith('/exam') },
  { ic: '☰', label: '更多', more: true },
];

/** 手机端底部标签栏（≤760px 显示）：首页 / 题库 / 学习聊天 / 考试与面经 / 更多(抽屉) */
export default function MobileTabBar({ onMore, moreActive }: { onMore: () => void; moreActive: boolean }) {
  const { pathname } = useLocation();
  return (
    <nav className="mobile-tabbar" aria-label="主导航">
      {TABS.map((tab) =>
        tab.more ? (
          <button
            key="more"
            type="button"
            className={'mobile-tab' + (moreActive ? ' active' : '')}
            onClick={onMore}
            aria-haspopup="dialog"
            aria-expanded={moreActive}
          >
            <span className="mobile-tab-ic" aria-hidden="true">{tab.ic}</span>
            <span className="mobile-tab-label">{tab.label}</span>
          </button>
        ) : (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.to === '/'}
            className={({ isActive }) => 'mobile-tab' + (isActive || tab.match(pathname) ? ' active' : '')}
          >
            <span className="mobile-tab-ic" aria-hidden="true">{tab.ic}</span>
            <span className="mobile-tab-label">{tab.label}</span>
          </NavLink>
        ),
      )}
    </nav>
  );
}
