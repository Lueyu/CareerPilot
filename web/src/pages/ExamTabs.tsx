import React from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import ExamCenter from './ExamCenter';
import Bank from './Bank';

// —— v3.1 M1: 考试与面经(考试中心 + 面经库 合并 Tab 页) ——

export default function ExamTabs() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const tab = pathname.startsWith('/bank') ? 'bank' : 'exam';

  return (
    <div>
      <div className="tabs" style={{ marginBottom: 16 }}>
        <button
          className={`tab ${tab === 'exam' ? 'active' : ''}`}
          onClick={() => navigate('/exam')}
        >
          📝 考试中心
        </button>
        <button
          className={`tab ${tab === 'bank' ? 'active' : ''}`}
          onClick={() => navigate('/bank')}
        >
          📚 面经库
        </button>
      </div>
      {tab === 'exam' ? <ExamCenter /> : <Bank />}
    </div>
  );
}
