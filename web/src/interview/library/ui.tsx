import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { sanitizeReviewAnswer } from "../../pages/BankReviewQueue";
import arrow from "./assets/arrow.svg";
import bookmark from "./assets/bookmark.svg";
import chart from "./assets/chart.svg";
import chevron from "./assets/chevron.svg";
import filter from "./assets/filter.svg";
import history from "./assets/history.svg";
import materials from "./assets/materials.svg";
import notes from "./assets/notes.svg";
import paperclip from "./assets/paperclip.svg";
import podcasts from "./assets/podcasts.svg";
import projects from "./assets/projects.svg";
import questions from "./assets/questions.svg";
import sparkles from "./assets/sparkles.svg";
import "./library.css";

const icons = {
  arrow,
  bookmark,
  chart,
  chevron,
  filter,
  history,
  materials,
  notes,
  paperclip,
  podcasts,
  projects,
  questions,
  sparkles,
};
export type IconName = keyof typeof icons;
export type Items<T> = { items: T[] };

export function Icon({ name, tile = false }: { name: IconName; tile?: boolean }) {
  const icon = <img src={icons[name]} alt="" className="ial-icon" />;
  return tile ? <span className={`ial-icon-tile ial-icon-${name}`}>{icon}</span> : icon;
}

export function ButtonLink({
  to,
  children,
  primary = false,
}: {
  to: string;
  children: ReactNode;
  primary?: boolean;
}) {
  return (
    <Link className={primary ? "ial-button ial-primary" : "ial-button"} to={to}>
      {children}
    </Link>
  );
}

export function PageTitle({
  title,
  intro,
  children,
}: {
  title: string;
  intro?: string;
  children?: ReactNode;
}) {
  return (
    <header className="ial-head">
      <div>
        <h1>{title}</h1>
        {intro && <p>{intro}</p>}
      </div>
      {children}
    </header>
  );
}

export function PanelTitle({
  icon,
  title,
  detail,
}: {
  icon: IconName;
  title: string;
  detail?: string;
}) {
  return (
    <header className="ial-panel-head">
      <h2>
        <Icon name={icon} tile />
        {title}
      </h2>
      {detail && <small>{detail}</small>}
    </header>
  );
}

export function Row({
  icon,
  title,
  subtitle,
  status,
  children,
}: {
  icon: IconName;
  title: ReactNode;
  subtitle?: string;
  status?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <li className="ial-row">
      <Icon name={icon} tile />
      <div className="ial-row-text">
        <strong>{title}</strong>
        {subtitle && <small>{subtitle}</small>}
      </div>
      {status && <small className="ial-row-status">{status}</small>}
      {children && <div className="ial-actions">{children}</div>}
    </li>
  );
}

export function Search({
  value,
  onChange,
  placeholder = "搜索",
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="ial-search">
      <Icon name="filter" />
      <input
        type="search"
        aria-label={placeholder}
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}

export function QueryState({
  loading,
  error,
  retry,
  empty,
}: {
  loading?: boolean;
  error?: string;
  retry?: () => void;
  empty?: string;
}) {
  if (error)
    return (
      <div className="ial-state" role="alert">
        <p>{error}</p>
        {retry && (
          <button className="ial-button" onClick={retry}>
            重试
          </button>
        )}
      </div>
    );
  if (loading)
    return (
      <p className="ial-state" role="status">
        正在读取…
      </p>
    );
  return empty ? <p className="ial-state">{empty}</p> : null;
}

export function Reference({ html }: { html: string }) {
  const markup = sanitizeReviewAnswer(html).replace(/\s+href="[^"]*"/gi, "");
  return <div className="ial-prose" dangerouslySetInnerHTML={{ __html: markup }} />;
}

export function useRead<T>(path: string | null, interval = 0) {
  const [state, setState] = useState<{
    path: string | null;
    data?: T;
    error?: string;
    loading: boolean;
  }>();
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    if (!path) return;
    let active = true;
    let timer: number;
    async function fetchData(url: string) {
      try {
        const data = await api.get<T>(url);
        if (active) setState({ path: url, data, loading: false });
      } catch (error) {
        if (active)
          setState((previous) => ({
            path: url,
            data: previous?.path === url ? previous?.data : undefined,
            error: errorText(error),
            loading: false,
          }));
      } finally {
        if (active && interval) timer = window.setTimeout(() => void fetchData(url), interval);
      }
    }
    void fetchData(path);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [path, revision, interval]);

  return {
    ...(state?.path === path ? state : { data: undefined, error: undefined, loading: !!path }),
    reload,
  };
}

export function useAction() {
  const lock = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(action: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (error) {
      setError(errorText(error));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return { busy, error, run };
}

export function errorText(error: unknown) {
  return error instanceof Error ? error.message : "请求失败，请重试。";
}

export function dateText(value?: string | null) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("zh-CN");
}

export function matches(query: string, ...values: (string | undefined | null)[]) {
  const term = query.trim().toLocaleLowerCase();
  return values.some((value) => value?.toLocaleLowerCase().includes(term));
}

export function chatLink(skill: string, topic?: string) {
  const params = new URLSearchParams({ skill });
  if (topic) params.set("topic", topic);
  return "/chat?" + params;
}

export function examLink(topic?: string) {
  return topic ? "/chat/exam?" + new URLSearchParams({ topic }) : "/chat/exam";
}

export function reveal(element: HTMLElement | null) {
  if (!element) return;
  const bounds = element.getBoundingClientRect();
  if (bounds.top < 0 || bounds.top > window.innerHeight - 100) {
    element.scrollIntoView({ block: "start" });
  }
}
