import React from 'react';
import { Route, Link, Navigate, useSearchParams } from 'react-router-dom';
import { AppStateProvider, useAppState } from './components/AppState';
import { Modal } from './components/ui';
import ImportMonitor from './components/ImportMonitor';
import { WorkspaceLayout } from './components/WorkspaceLayout';
import { getWorkspace, useApi, type SettingsOverview } from './api/client';
import { CommandPalette, NotificationsPanel, QuickActionsPanel, WorkspaceSwitcher } from './components/Overlays';

import { WorkspaceRoutes } from './WorkspaceRoutes';
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

function Layout({ accountName }: { accountName?: string }) {
  const { setCmdOpen, setQuickOpen, setNotifOpen, setWsOpen } = useAppState();
  const { data } = useApi<SettingsOverview>('/settings/overview');
  return (
    <WorkspaceLayout
      workspace={{
        kind: 'personal',
        label: data?.workspace.is_demo ? '演示工作空间' : '个人工作空间',
        name: data?.workspace.name || accountName || getWorkspace(),
        openSwitcher: () => setWsOpen(true),
        actions: {
          search: () => setCmdOpen(true),
          quick: () => setQuickOpen(true),
          notifications: () => setNotifOpen(true),
        },
      }}
      overlays={<><CommandPalette /><NotificationsPanel /><QuickActionsPanel />{accountName === undefined ? <WorkspaceSwitcher /> :
        <RegisteredWorkspaceSwitcher name={accountName} />}</>}
    />
  );
}

function RegisteredWorkspaceSwitcher({ name }: { name: string }) {
  const { wsOpen, setWsOpen } = useAppState();
  if (!wsOpen) return null;
  return <Modal title="当前工作空间" onClose={() => setWsOpen(false)} size="sm">
    <p>{name}</p>
    <Link to="/settings/account" onClick={() => setWsOpen(false)}>账户与资料</Link>
  </Modal>;
}

function StudyRetest({ report = false }: { report?: boolean }) {
  const [params] = useSearchParams();
  if (!/^[1-9]\d*$/.test(params.get('id') ?? '')) return <Navigate to="/study/notes?tab=review" replace />;
  return <StudyShell section="review"><div className="study-retest-page">
    {report ? <DiagnosisReport studyMode /> : <Diagnosis studyMode />}
  </div></StudyShell>;
}

type RegisteredPages = {
  accountName: string;
  onboarding: React.ReactElement;
  chat: React.ReactElement;
  notes: React.ReactElement;
  exam: React.ReactElement;
  routes: React.ReactNode;
};

export function NativeWorkspace({ registered }: { registered?: RegisteredPages }) {
  return (
    <AppStateProvider>
      <WorkspaceRoutes layout={<Layout accountName={registered?.accountName} />} onboarding={registered?.onboarding ?? <Onboarding />} home={<Dashboard />}
        bank={<Bank registered={!!registered} />} chat={registered?.chat ?? <StudyChat />} notes={registered?.notes ?? <StudyNotebook />} exam={registered?.exam ?? <ExamCenter />}
        learning={<Learning />} memory={<Memory />} settings={<Settings registered={!!registered} />}>
        <Route path="/study/retest" element={<StudyRetest />} />
        <Route path="/study/retest/report" element={<StudyRetest report />} />
        <Route path="/benchmark" element={<CompetencyTabs />} />
        <Route path="/profile" element={<CompetencyTabs />} />
        <Route path="/profile/:domain" element={<ProfileDetail />} />
        <Route path="/bank/workshop" element={<QuestionWorkshop />} />
        <Route path="/exam/diagnosis" element={<Diagnosis />} />
        <Route path="/exam/diagnosis/report" element={<DiagnosisReport />} />
        <Route path="/learning/task/:id" element={<LearningTask />} />
        <Route path="/assets" element={<Assets />} />
        <Route path="/assets/project/:id" element={<ProjectReview />} />
        <Route path="/assets/pipeline" element={<Pipeline />} />
        <Route path="/observatory" element={<Observatory />} />
        <Route path="/evolution" element={<Evolution />} />
        {registered?.routes}
      </WorkspaceRoutes>
      <ImportMonitor /></AppStateProvider>
  );
}

export default function App() {
  return <WorkspaceLogin><NativeWorkspace /></WorkspaceLogin>;
}
