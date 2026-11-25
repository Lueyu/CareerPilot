import { useT } from '../i18n';
import React from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import Benchmark from './Benchmark';
import Profile from './Profile';

// —— v3.1 M1: 能力与岗位(岗位对标 + 能力画像 合并 Tab 页) ——

export default function CompetencyTabs() {
  const t = useT();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const tab = pathname.startsWith('/profile') ? 'profile' : 'benchmark';

  return (
    <div>
      <div className="tabs" style={{ marginBottom: 16 }}>
        <button
          className={`tab ${tab === 'benchmark' ? 'active' : ''}`}
          onClick={() => navigate('/benchmark')}
        >
          {t('progress.competencyTabsBenchmark')}
        </button>
        <button
          className={`tab ${tab === 'profile' ? 'active' : ''}`}
          onClick={() => navigate('/profile')}
        >
          {t('progress.competencyTabsProfile')}
        </button>
      </div>
      {tab === 'benchmark' ? <Benchmark /> : <Profile />}
    </div>
  );
}
