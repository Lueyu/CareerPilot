import type { ReactElement, ReactNode } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

/** Both installations follow the same navigation. Pages are supplied at the build boundary. */
export function WorkspaceRoutes({ layout, onboarding, home, bank, chat, notes, exam, learning, memory, settings, children }: {
  layout: ReactElement;
  onboarding: ReactElement;
  home: ReactElement;
  bank: ReactElement;
  chat: ReactElement;
  notes?: ReactElement;
  exam: ReactElement;
  learning: ReactElement;
  memory: ReactElement;
  settings: ReactElement;
  children?: ReactNode;
}) {
  return <Routes>
    <Route path="/onboarding" element={onboarding} />
    <Route element={layout}>
      <Route index element={home} />
      <Route path="/bank" element={bank} />
      <Route path="/study/chat/:sessionId?" element={chat} />
      {notes && <Route path="/study/notes/:id?" element={notes} />}
      <Route path="/exam" element={exam} />
      <Route path="/learning" element={learning} />
      <Route path="/memory" element={memory} />
      <Route path="/settings" element={settings} />
      {children}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Route>
  </Routes>;
}
