import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { WorkspaceSwitcher } from "../components/Overlays";
import { getWorkspace } from "../api/client";
import { useLocale } from "../i18n";
import ChatWorkspace from "./chat/ChatWorkspace";
import ExamWorkspace, { ExamConfiguration } from "./exam/ExamWorkspace";
import InterviewSettings from "./InterviewSettings";
import InterviewShell from "./InterviewShell";
import { InterviewAppearance } from "./appearance";
import { interviewLocation } from "./legacyLocation";
import "./interview.css";

const Library = lazy(() => import("./library/Library"));
const Competency = lazy(() => import("./library/Competency"));

function RedirectToInterview() {
  const location = useLocation();
  const target = interviewLocation(location);
  const current = location.pathname + location.search + location.hash;
  return <Navigate replace to={target === current ? "/chat" : target} />;
}

export default function InterviewWorkspace() {
  const english = useLocale() === "en";
  return (
    <InterviewAppearance key={getWorkspace()}>
      <Suspense
        fallback={<p role="status">{english ? "Loading workspace…" : "正在打开工作区…"}</p>}
      >
        <Routes>
          <Route element={<InterviewShell />}>
            <Route
              path="/chat/:sessionId?"
              element={
                <ChatWorkspace renderExamConfig={(options) => <ExamConfiguration {...options} />} />
              }
            />
            <Route path="/chat/exam" element={<ExamWorkspace />} />
            <Route path="/library/:section?" element={<Library />} />
            <Route path="/competency" element={<Competency />} />
            <Route path="/settings" element={<InterviewSettings />} />
            <Route path="/settings/account" element={<InterviewSettings />} />
          </Route>
          <Route
            path="/chat/exam/:attemptId"
            element={
              <div className="ia-focus">
                <ExamWorkspace />
              </div>
            }
          />
          <Route
            path="/chat/exam/:attemptId/report"
            element={
              <div className="ia-focus">
                <ExamWorkspace />
              </div>
            }
          />
          <Route path="*" element={<RedirectToInterview />} />
        </Routes>
      </Suspense>
      <WorkspaceSwitcher />
    </InterviewAppearance>
  );
}
