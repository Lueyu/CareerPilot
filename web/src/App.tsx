import React from 'react';
import { Route, Routes, Outlet, Navigate } from 'react-router-dom';
import { AppStateProvider } from './components/AppState';
import Sidebar from './components/Sidebar';
import Topbar from './components/Topbar';
import { CommandPalette, NotificationsPanel, QuickActionsPanel, WorkspaceSwitcher } from './components/Overlays';

import Onboarding from './pages/Onboarding';
import Dashboard from './pages/Dashboard';
import CompetencyTabs from './pages/CompetencyTabs';
import ProfileDetail from './pages/ProfileDetail';
import ExamTabs from './pages/ExamTabs';
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

function Layout() {
  return (
    <AppStateProvider>
      <div className="app">
        <Sidebar />
        <div className="app-main">
          <Topbar />
          <div className="app-body">
            <Outlet />
          </div>
        </div>
        <CommandPalette />
        <NotificationsPanel />
        <QuickActionsPanel />
        <WorkspaceSwitcher />
      </div>
    </AppStateProvider>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/onboarding" element={<Onboarding />} />
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/benchmark" element={<CompetencyTabs />} />
        <Route path="/profile" element={<CompetencyTabs />} />
        <Route path="/profile/:domain" element={<ProfileDetail />} />
        <Route path="/exam" element={<ExamTabs />} />
        <Route path="/bank" element={<ExamTabs />} />
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
    </Routes>
  );
}
