import React from 'react';
import { Route, Routes, Navigate, useSearchParams } from 'react-router-dom';
import { AppStateProvider, useAppState } from './components/AppState';
import ImportMonitor from './components/ImportMonitor';
import { WorkspaceLayout } from './components/WorkspaceLayout';
import { getWorkspace, useApi, type SettingsOverview } from './api/client';
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
  const { setCmdOpen, setQuickOpen, setNotifOpen, setWsOpen } = useAppState();
  const { data } = useApi<SettingsOverview>('/settings/overview');
  return (
    <WorkspaceLayout
      workspace={{
        kind: 'personal',
        label: data?.workspace.is_demo ? '演示工作空间' : '个人工作空间',
        name: data?.workspace.name || getWorkspace(),
        openSwitcher: () => setWsOpen(true),
        actions: {
          search: () => setCmdOpen(true),
          quick: () => setQuickOpen(true),
          notifications: () => setNotifOpen(true),
        },
      }}
      overlays={<><CommandPalette /><NotificationsPanel /><QuickActionsPanel /><WorkspaceSwitcher /></>}
    />
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
