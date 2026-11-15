import React, { createContext, useCallback, useContext, useRef, useState } from 'react';

interface Toast {
  id: number;
  ic: string;
  text: string;
}

interface AppState {
  cmdOpen: boolean;
  setCmdOpen: (v: boolean) => void;
  notifOpen: boolean;
  setNotifOpen: (v: boolean) => void;
  quickOpen: boolean;
  setQuickOpen: (v: boolean) => void;
  wsOpen: boolean;
  setWsOpen: (v: boolean) => void;
  toast: (ic: string, text: string) => void;
}

const Ctx = createContext<AppState | null>(null);

export function AppStateProvider({ children }: { children: React.ReactNode }) {
  const [cmdOpen, setCmdOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [quickOpen, setQuickOpen] = useState(false);
  const [wsOpen, setWsOpen] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const idRef = useRef(1);

  const toast = useCallback((ic: string, text: string) => {
    const id = idRef.current++;
    setToasts((t) => [...t, { id, ic, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 2600);
  }, []);

  return (
    <Ctx.Provider
      value={{ cmdOpen, setCmdOpen, notifOpen, setNotifOpen, quickOpen, setQuickOpen, wsOpen, setWsOpen, toast }}
    >
      {children}
      <div className="toast-wrap">
        {toasts.map((t) => (
          <div className="toast" key={t.id}>
            <span className="toast-ic">{t.ic}</span>
            {t.text}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useAppState() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useAppState must be used within AppStateProvider');
  return ctx;
}
