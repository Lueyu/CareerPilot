import { useEffect, useState, type ReactNode } from "react";
import { api } from "../../api/client";
import { errorText } from "./model";
import arrow from "./assets/arrow.svg";
import check from "./assets/check.svg";
import chevron from "./assets/chevron.svg";
import moon from "./assets/moon.svg";
import paperclip from "./assets/paperclip.svg";
import slash from "./assets/slash.svg";
import target from "./assets/target.svg";

const icons = { arrow, check, chevron, moon, paperclip, slash, target };

export function Icon({ name }: { name: keyof typeof icons }) {
  return <img className="ia-chat-icon" src={icons[name]} alt="" />;
}

export function Card({
  title,
  icon = "target",
  detail,
  children,
}: {
  title: string;
  icon?: keyof typeof icons;
  detail?: string;
  children: ReactNode;
}) {
  return (
    <section className="ia-chat-card">
      <header className="ia-chat-card-head">
        <span className="ia-chat-card-icon">
          <Icon name={icon} />
        </span>
        <h3>{title}</h3>
        {detail && <small>{detail}</small>}
      </header>
      {children}
    </section>
  );
}

type Resource<T> =
  | { kind: "loading"; path: string }
  | { kind: "error"; path: string; error: string }
  | { kind: "ready"; path: string; data: T };

export function useResource<T>(path: string) {
  const [revision, setRevision] = useState(0);
  const [value, setValue] = useState<Resource<T>>({ kind: "loading", path });
  useEffect(() => {
    let active = true;
    api
      .get<T>(path)
      .then((data) => {
        if (active) setValue({ kind: "ready", data, path });
      })
      .catch((error) => {
        if (active) setValue({ kind: "error", error: errorText(error), path });
      });
    return () => {
      active = false;
    };
  }, [path, revision]);
  const state: Resource<T> = value.path === path ? value : { kind: "loading", path };
  return {
    state,
    refresh: () => {
      setValue({ kind: "loading", path });
      setRevision((current) => current + 1);
    },
  };
}

export function ReadCard<T>({
  path,
  title,
  render,
}: {
  path: string;
  title: string;
  render: (data: T) => ReactNode;
}) {
  const { state, refresh } = useResource<T>(path);
  return (
    <Card title={title}>
      {state.kind === "loading" && <p role="status">正在读取…</p>}
      {state.kind === "error" && (
        <div role="alert">
          <p>{state.error}</p>
          <button onClick={refresh}>重新读取</button>
        </div>
      )}
      {state.kind === "ready" && render(state.data)}
    </Card>
  );
}
