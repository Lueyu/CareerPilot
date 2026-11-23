import { useEffect, useState, type FormEvent } from "react";
import { useWorkspaceApi } from "./api";

type Direction = { name: string; example: string; origin: "preset" | "model" };
export type LibraryRole = {
  role: string;
  jd: string | null;
  selected_topic: string | null;
  directions: Direction[];
};
export type LibraryRoles = { selected_role: string | null; roles: LibraryRole[] };
export type Selection = { role: string; topic: string };

export function useLibraryRole() {
  const { request, send } = useWorkspaceApi();
  const [roles, setRoles] = useState<LibraryRoles>();
  const [roleInput, setRoleInput] = useState("");
  const [jd, setJd] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [retry, setRetry] = useState(0);
  const active = roles?.roles.find((role) => role.role === roles.selected_role);
  const direction = active?.directions.find((item) => item.name === active.selected_topic);
  const selection = active && direction ? { role: active.role, topic: direction.name } : null;

  useEffect(() => {
    let mounted = true;
    request<LibraryRoles>("/library/roles").then(
      (result) => {
        if (!mounted) return;
        const selected = result.roles.find((role) => role.role === result.selected_role);
        setRoles(result);
        setRoleInput(selected?.role ?? "");
        setJd(selected?.jd ?? "");
        setError("");
      },
      (failure: unknown) => {
        if (mounted) setError(failure instanceof Error && failure.message ? failure.message : "操作失败，请重试。");
      },
    );
    return () => {
      mounted = false;
    };
  }, [request, retry]);

  function applyRole(role: LibraryRole, resetInputs = true) {
    setRoles((previous) => {
      const entries = previous?.roles ?? [];
      return {
        selected_role: role.role,
        roles: entries.some((entry) => entry.role === role.role)
          ? entries.map((entry) => (entry.role === role.role ? role : entry))
          : [...entries, role],
      };
    });
    if (resetInputs) {
      setRoleInput(role.role);
      setJd(role.jd ?? "");
    }
  }

  async function operate(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError("");
    setStatus("");
    try {
      await action();
    } catch (failure) {
      setError(failure instanceof Error && failure.message ? failure.message : "操作失败，请重试。");
    } finally {
      setBusy("");
    }
  }

  function saveRole(event: FormEvent) {
    event.preventDefault();
    const role = roleInput.trim();
    if (!role || busy) return;
    return operate("正在保存岗位…", async () => {
      applyRole(await send<LibraryRole>("/library/role", "PUT", { role, jd: jd.trim() }));
      setStatus("岗位已保存。");
    });
  }

  function selectDirection(topic: string, onSaved?: () => void) {
    if (!active || busy) return;
    return operate("正在选择方向…", async () => {
      applyRole(await send<LibraryRole>("/library/direction", "PUT", {
        role: active.role,
        topic,
      }), false);
      onSaved?.();
    });
  }

  return {
    roles, roleInput, setRoleInput, jd, setJd, busy, error, status, setStatus, setRetry,
    active, direction, selection, applyRole, operate, saveRole, selectDirection,
  };
}
