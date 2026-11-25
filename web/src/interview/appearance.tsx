import { createContext, useContext, useLayoutEffect, useState, type ReactNode } from "react";
import { workspaceStorageKey } from "../api/client";

export type ThemePreference = "light" | "dark" | "system";
type Appearance = {
  preference: ThemePreference;
  dark: boolean;
  setPreference: (value: ThemePreference) => void;
};

const AppearanceContext = createContext<Appearance | null>(null);
const darkQuery = "(prefers-color-scheme: dark)";

export function InterviewAppearance({ children }: { children: ReactNode }) {
  const key = workspaceStorageKey("cp-theme");
  const [preference, update] = useState<ThemePreference>(() => {
    const stored = localStorage.getItem(key);
    return stored === "light" || stored === "dark" ? stored : "system";
  });
  const [systemDark, setSystemDark] = useState(() => window.matchMedia(darkQuery).matches);
  const dark = preference === "system" ? systemDark : preference === "dark";

  useLayoutEffect(() => {
    const query = window.matchMedia(darkQuery);
    const changed = () => setSystemDark(query.matches);
    query.addEventListener("change", changed);
    changed();
    return () => query.removeEventListener("change", changed);
  }, []);

  useLayoutEffect(() => {
    const root = document.documentElement;
    const previous = root.dataset.theme;
    root.dataset.theme = dark ? "dark" : "light";
    return () => {
      if (previous) root.dataset.theme = previous;
      else delete root.dataset.theme;
    };
  }, [dark]);

  function setPreference(value: ThemePreference) {
    localStorage.setItem(key, value);
    update(value);
  }

  return (
    <AppearanceContext.Provider value={{ preference, dark, setPreference }}>
      <div className="interview-app" data-interview-theme={dark ? "dark" : "light"}>
        {children}
      </div>
    </AppearanceContext.Provider>
  );
}

export function useInterviewAppearance() {
  const appearance = useContext(AppearanceContext);
  if (!appearance) throw new Error("Interview appearance requires a workspace");
  return appearance;
}
