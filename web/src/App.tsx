import React from 'react';
import { Route, Routes, Outlet, Navigate, useLocation, useSearchParams } from 'react-router-dom';
import { AppStateProvider } from './components/AppState';
import ImportMonitor from './components/ImportMonitor';
import Sidebar from './components/Sidebar';
import Topbar from './components/Topbar';
import { CommandPalette, NotificationsPanel, QuickActionsPanel, WorkspaceSwitcher } from './components/Overlays';

import StudyShell from './components/StudyShell';
import StudyNotebook from './pages/StudyNotebook';
import StudyChat from './pages/StudyChat';
import WorkspaceLogin from './components/WorkspaceLogin';
import Onboarding from './pages/Onboarding';
import Dashboard from './pages/Dashboard';
import CompetencyTabs from './pages/CompetencyTabs';
import ProfileDetail from './pages/ProfileDetail';
import ExamCenter from './pages/ExamCenter';
import Bank from './pages/Bank';
import Diagnosis from './pages/Diagnosis';
import DiagnosisReport from './pages/DiagnosisReport';
import Learning from './pages/Learning';
import LearningTask from './pages/LearningTask';
import Assets from './pages/Assets';
import ProjectReview from './pages/ProjectReview';
import Pipeline from './pages/Pipeline';
import Memory from './pages/Memory';
import Observatory from './pages/Observatory';
import Evolution from './pages/Evolution';
import Settings from './pages/Settings';
import QuestionWorkshop from './pages/QuestionWorkshop';

function Layout() {
  const { pathname } = useLocation();
  const study = pathname.startsWith('/study/');
  const learning = pathname === '/learning';
  const mobileNav = React.useRef<HTMLDialogElement>(null);
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false);
  React.useEffect(() => { if (!learning) setMobileNavOpen(false); }, [learning]);
  const compactNav = study || pathname === '/bank' || pathname === '/memory' || pathname === '/assets' || pathname.startsWith('/assets/project/');
  return (
    <div className={`app${compactNav ? ' app-compact-nav' : ''}${learning ? ' app-learning-calendar' : ''}`}>
      <Sidebar />
      {learning && <dialog ref={mobileNav} className="learning-mobile-drawer" aria-label="CareerPilot 导航" onClose={() => setMobileNavOpen(false)}
        onClick={event => {
          const target = event.target;
          if (target === event.currentTarget || (target instanceof Element && target.closest('a, .ws-card'))) mobileNav.current?.close();
        }}>
        <button className="btn btn-outline learning-mobile-nav-close" type="button" onClick={() => mobileNav.current?.close()}>关闭菜单</button>
        {mobileNavOpen && <Sidebar />}
      </dialog>}
      <div className="app-main">
        <Topbar beforeCrumb={learning && <button className="btn btn-outline learning-mobile-nav-button" type="button" aria-haspopup="dialog"
          onClick={() => { mobileNav.current?.showModal(); setMobileNavOpen(true); }}>菜单</button>} />
        <div className={study ? 'app-body app-body-study' : 'app-body'}>
          <Outlet />
        </div>
      </div>
      <CommandPalette />
      <NotificationsPanel />
      <QuickActionsPanel />
      <WorkspaceSwitcher />
    </div>
  );
}

function StudyRetest({ report = false }: { report?: boolean }) {
  const [params] = useSearchParams();
  if (!/^[1-9]\d*$/.test(params.get('id') ?? '')) return <Navigate to="/study/notes?tab=review" replace />;
  return <StudyShell section="review"><div className="study-retest-page">
    {report ? <DiagnosisReport studyMode /> : <Diagnosis studyMode />}
  </div></StudyShell>;
}

export default function App() {
  return (
    <WorkspaceLogin><AppStateProvider><Routes>
      <Route path="/onboarding" element={<Onboarding />} />
      <Route element={<Layout />}>
        <Route path="/study/retest" element={<StudyRetest />} />
        <Route path="/study/retest/report" element={<StudyRetest report />} />
        <Route path="/study/chat" element={<StudyChat />} />
        <Route path="/study/chat/:sessionId" element={<StudyChat />} />
        <Route path="/study/notes" element={<StudyNotebook />} />
        <Route path="/study/notes/:id" element={<StudyNotebook />} />
        <Route path="/" element={<Dashboard />} />
        <Route path="/benchmark" element={<CompetencyTabs />} />
        <Route path="/profile" element={<CompetencyTabs />} />
        <Route path="/profile/:domain" element={<ProfileDetail />} />
        <Route path="/exam" element={<ExamCenter />} />
        <Route path="/bank" element={<Bank />} />
        <Route path="/bank/workshop" element={<QuestionWorkshop />} />
        <Route path="/exam/diagnosis" element={<Diagnosis />} />
        <Route path="/exam/diagnosis/report" element={<DiagnosisReport />} />
        <Route path="/learning" element={<Learning />} />
        <Route path="/learning/task/:id" element={<LearningTask />} />
        <Route path="/assets" element={<Assets />} />
        <Route path="/assets/project/:id" element={<ProjectReview />} />
        <Route path="/assets/pipeline" element={<Pipeline />} />
        <Route path="/memory" element={<Memory />} />
        <Route path="/observatory" element={<Observatory />} />
        <Route path="/evolution" element={<Evolution />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes><ImportMonitor /></AppStateProvider></WorkspaceLogin>
  );
}
