import React from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { useT } from '../i18n';

type Tab =
  | { ic: string; labelKey: string; to: string; match: (p: string) => boolean; more?: never }
  | { ic: string; labelKey: string; more: true; to?: never; match?: never };

const TABS: Tab[] = [
  { ic: '🏠', labelKey: 'shell.home', to: '/', match: (p) => p === '/' },
  { ic: '📚', labelKey: 'shell.questionBank', to: '/bank', match: (p) => p === '/bank' || p.startsWith('/bank/') },
  { ic: '💬', labelKey: 'shell.studyChat', to: '/study/chat', match: (p) => p.startsWith('/study/') },
  { ic: '📝', labelKey: 'shell.examsInterviews', to: '/exam', match: (p) => p.startsWith('/exam') },
  { ic: '☰', labelKey: 'shell.menu', more: true },
];

/** 手机端底部标签栏（≤760px 显示）：首页 / 题库 / 学习聊天 / 考试与面经 / 更多(抽屉) */
export default function MobileTabBar({ onMore, moreActive }: { onMore: () => void; moreActive: boolean }) {
  const { pathname } = useLocation();
  const t = useT();
  return (
    <nav className="mobile-tabbar" aria-label={t('shell.careerPilotNav')}>
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
            <span className="mobile-tab-label">{t(tab.labelKey)}</span>
          </button>
        ) : (
          <NavLink
            key={tab.to}
            to={tab.to}
            end={tab.to === '/'}
            className={({ isActive }) => 'mobile-tab' + (isActive || tab.match(pathname) ? ' active' : '')}
          >
            <span className="mobile-tab-ic" aria-hidden="true">{tab.ic}</span>
            <span className="mobile-tab-label">{t(tab.labelKey)}</span>
          </NavLink>
        ),
      )}
    </nav>
  );
}
