import { useT } from './i18n';
import React, { lazy, Suspense } from 'react';
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
import Ops from './pages/Ops';
import Evolution from './pages/Evolution';
import Settings from './pages/Settings';
import QuestionWorkshop from './pages/QuestionWorkshop';
import Podcasts from './pages/Podcasts';
import { WorkspacePodcastPlayer } from './podcasts/PodcastPlayer';

const InterviewWorkspace = lazy(() => import('./interview/InterviewWorkspace'));

function Layout({ accountName }: { accountName?: string }) {
  const t = useT();
  const { setCmdOpen, setQuickOpen, setNotifOpen, setWsOpen } = useAppState();
  const { data } = useApi<SettingsOverview>('/settings/overview');
  return (
    <WorkspaceLayout
      workspace={{
        kind: 'personal',
        label: data?.workspace.is_demo ? t('shell.demoWorkspace') : t('shell.personalWorkspace'),
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
  const t = useT();
  const { wsOpen, setWsOpen } = useAppState();
  if (!wsOpen) return null;
  return <Modal title={t('shell.currentWorkspace')} onClose={() => setWsOpen(false)} size="sm">
    <p>{name}</p>
    <Link to="/settings/account" onClick={() => setWsOpen(false)}>{t('shell.accountProfile')}</Link>
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
      <WorkspacePodcastPlayer key={getWorkspace()}>
        <WorkspaceRoutes layout={<Layout accountName={registered?.accountName} />} onboarding={registered?.onboarding ?? <Onboarding />} home={<Dashboard />}
          bank={<Bank registered={!!registered} />} chat={registered?.chat ?? <StudyChat />} notes={registered?.notes ?? <StudyNotebook />} exam={registered?.exam ?? <ExamCenter />}
          learning={<Learning />} memory={<Memory />} settings={<Settings registered={!!registered} />}>
          <Route path="/study/retest" element={<StudyRetest />} />
          <Route path="/study/retest/report" element={<StudyRetest report />} />
          <Route path="/benchmark" element={<CompetencyTabs />} />
          <Route path="/profile" element={<CompetencyTabs />} />
          <Route path="/profile/:domain" element={<ProfileDetail />} />
          <Route path="/bank/workshop" element={<QuestionWorkshop />} />
          <Route path="/podcasts" element={<Podcasts />} />
          <Route path="/exam/diagnosis" element={<Diagnosis />} />
          <Route path="/exam/diagnosis/report" element={<DiagnosisReport />} />
          <Route path="/learning/task/:id" element={<LearningTask />} />
          <Route path="/assets" element={<Assets />} />
          <Route path="/assets/project/:id" element={<ProjectReview />} />
          <Route path="/assets/pipeline" element={<Pipeline />} />
          <Route path="/observatory" element={<Observatory />} />
          <Route path="/ops" element={<Ops />} />
          <Route path="/evolution" element={<Evolution />} />
          {registered?.routes}
        </WorkspaceRoutes>
        <ImportMonitor />
      </WorkspacePodcastPlayer>
    </AppStateProvider>
  );
}

export default function App() {
  return <WorkspaceLogin>
    <AppStateProvider>
      <WorkspacePodcastPlayer key={getWorkspace()}>
        <Suspense fallback={<p role="status">正在打开工作区…</p>}>
          <InterviewWorkspace />
        </Suspense>
        <ImportMonitor reviewPath="/library/notes?review=1" />
      </WorkspacePodcastPlayer>
    </AppStateProvider>
  </WorkspaceLogin>;
}
