import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, getWorkspace, type SettingsOverview } from "../api/client";
import { useAppState } from "../components/AppState";
import { useLocale } from "../i18n";
import { InterviewIcon } from "./InterviewIcon";
import { useInterviewAppearance } from "./appearance";

const destinations = [
  { to: "/chat", icon: "chat", zh: "聊天工作台", en: "Chat workspace" },
  { to: "/library", icon: "folder", zh: "资料库", en: "Library" },
  { to: "/competency", icon: "target", zh: "能力对标", en: "Competency" },
  { to: "/settings", icon: "settings", zh: "设置", en: "Settings" },
] as const;

type Recent = { id: number; title: string };
export default function InterviewShell() {
  const { dark, setPreference } = useInterviewAppearance();
  const { setWsOpen } = useAppState();
  const [overview, setOverview] = useState<SettingsOverview>();
  const [recent, setRecent] = useState<Recent[]>([]);
  const location = useLocation();
  const navigate = useNavigate();
  const english = useLocale() === "en";
  const label = (zh: string, en: string) => (english ? en : zh);
  const workspace = getWorkspace();
  const page = destinations.find(
    (item) => location.pathname === item.to || location.pathname.startsWith(item.to + "/"),
  );
  const currentTitle = page ? label(page.zh, page.en) : label("聊天工作台", "Chat workspace");
  const isChat = page?.to === "/chat";
  const displayName = overview?.account?.display_name || overview?.workspace?.name || "CareerPilot";
  const avatar = Array.from(displayName)[0];
  const memoryCount = overview?.counts?.memory ?? 0;

  useEffect(() => {
    let current = true;
    function refresh() {
      void api
        .get<SettingsOverview>("/settings/overview")
        .then((value) => {
          if (current) setOverview(value);
        })
        .catch(() => {});
    }
    refresh();
    window.addEventListener("interview-context-changed", refresh);
    return () => {
      current = false;
      window.removeEventListener("interview-context-changed", refresh);
    };
  }, [workspace]);

  useEffect(() => {
    let current = true;
    const refresh = () => {
      void api
        .get<{ items: Recent[] }>("/study/sessions?limit=5")
        .then((value) => {
          if (current) setRecent(value.items ?? []);
        })
        .catch(() => {});
    };
    refresh();
    window.addEventListener("study-history-changed", refresh);
    return () => {
      current = false;
      window.removeEventListener("study-history-changed", refresh);
    };
  }, [workspace, location.pathname]);

  function openMemory() {
    if (isChat && location.pathname !== "/chat/exam") {
      window.dispatchEvent(new CustomEvent("interview-memory-open"));
    } else {
      navigate("/chat?memory=1");
    }
  }

  function nav(className: string, mobile = false): ReactNode {
    return (
      <nav className={className} aria-label={label("工作区导航", "Workspace navigation")}>
        {destinations.map((item) => (
          <NavLink key={item.to} to={item.to}>
            <InterviewIcon name={item.icon} />
            <span>
              {mobile && item.to === "/chat" ? label("对话", "Chat") : label(item.zh, item.en)}
            </span>
          </NavLink>
        ))}
      </nav>
    );
  }

  return (
    <div className="ia-shell">
      <aside className="ia-rail">
        <Link className="ia-brand" to="/chat">
          <span>
            <InterviewIcon name="brand" size={22} />
          </span>
          CareerPilot
        </Link>
        <Link className="ia-new" to="/chat?new=1">
          <InterviewIcon name="plus" size={17} />
          {label("新对话", "New conversation")}
        </Link>
        {nav("ia-nav")}
        <section className="ia-recent" aria-label={label("最近对话", "Recent conversations")}>
          <h2>{label("最近对话", "Recent conversations")}</h2>
          {recent.length === 0 && (
            <p>{label("保存的对话会显示在这里", "Saved conversations appear here")}</p>
          )}
          {recent.map((item) => (
            <NavLink end key={item.id} to={"/chat/" + item.id} title={item.title}>
              <InterviewIcon name="chat" size={14} />
              <span>{item.title}</span>
            </NavLink>
          ))}
        </section>
        <div className="ia-account">
          <button type="button" onClick={() => setWsOpen(true)} className="ia-account-switch">
            <span className="ia-avatar">{avatar}</span>
            <span>
              <strong>{displayName}</strong>
              <small>
                {overview?.workspace?.is_demo
                  ? label("演示工作区", "Demo workspace")
                  : label("个人工作区", "Personal workspace")}
              </small>
            </span>
            <InterviewIcon name="chevron" size={14} />
          </button>
        </div>
      </aside>
      <div className="ia-main">
        <header className="ia-topbar">
          <div className="ia-location">
            <InterviewIcon name="panel" size={17} />
            <strong>{currentTitle}</strong>
            {overview?.workspace?.is_demo && (
              <span className="ia-demo">{label("演示数据", "Demo")}</span>
            )}
          </div>
          <div className="ia-top-tools">
            <button type="button" className="ia-memory-indicator" onClick={openMemory}>
              <span className="ia-dot" data-ready={memoryCount > 0} />
              {memoryCount > 0 ? label("查看会话记忆", "View memories") : label("记忆", "Memory")}
              <InterviewIcon name="chevron" size={13} />
            </button>
            <button
              type="button"
              className="ia-theme"
              onClick={() => setPreference(dark ? "light" : "dark")}
              aria-label={label(
                dark ? "切换到浅色" : "切换到深色",
                dark ? "Switch to light" : "Switch to dark",
              )}
            >
              <InterviewIcon name={dark ? "sun" : "moon"} size={17} />
              {dark ? label("浅色", "Light") : label("深色", "Dark")}
            </button>
            <button
              type="button"
              className="ia-avatar ia-top-avatar"
              onClick={() => setWsOpen(true)}
              aria-label={label("切换工作区", "Switch workspace")}
            >
              {avatar}
            </button>
          </div>
        </header>
        <main className="ia-stage" data-page={page?.icon}>
          <Outlet />
        </main>
        {nav("ia-mobile-nav", true)}
      </div>
    </div>
  );
}
