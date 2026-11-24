import type { Locale, t } from "../i18n";
import { useEffect, useState, type FormEvent } from "react";
import { messageFor, useWorkspaceApi, type MessageKey, type UiMessage } from "./api";

type Direction = { name: string; example: string; examples?: Partial<Record<Locale, string>>; origin: "preset" | "model" };
export type LibraryRole = {
  role: string;
  jd: string | null;
  selected_topic: string | null;
  directions: Direction[];
};
export type LibraryRoles = { selected_role: string | null; roles: LibraryRole[] };
export type Selection = { role: string; topic: string };

const presetKeys: ReadonlyMap<string, string> = new Map([
  ["用户增长", "userGrowth"],
  ["活动复盘", "campaignReview"],
  ["用户分层", "userSegmentation"],
  ["接口与数据一致性", "apiConsistency"],
  ["故障排查", "troubleshooting"],
  ["系统设计", "systemDesign"],
  ["交互状态", "interactionState"],
  ["页面性能", "pagePerformance"],
  ["可访问性", "accessibility"],
  ["需求优先级", "prioritization"],
  ["指标设计", "metricDesign"],
  ["方案验证", "solutionValidation"],
  ["指标诊断", "metricDiagnosis"],
  ["实验设计", "experimentDesign"],
  ["数据质量", "dataQuality"],
  ["需求挖掘", "customerDiscovery"],
  ["机会管理", "salesOpportunity"],
  ["异议处理", "salesObjections"],
  ["设计取舍", "designTradeoffs"],
  ["设计评审", "designReview"],
  ["可用性测试", "usability"],
  ["教学设计", "lessonPlanning"],
  ["学习反馈", "learningFeedback"],
  ["沟通协作", "collaboration"],
  ["岗位任务", "roleResponsibilities"],
  ["现场判断", "judgmentUnderPressure"],
  ["成果复盘", "resultsReview"],
]);

export function directionLabel(direction: Direction, translate: typeof t) {
  const preset = direction.origin === "preset" ? presetKeys.get(direction.name) : undefined;
  return preset ? translate(`registered.${preset}Topic`) : direction.name;
}

export function directionExample(direction: Direction, locale: Locale) {
  return direction.examples?.[locale] ?? direction.example;
}

export function useLibraryRole() {
  const { request, send } = useWorkspaceApi();
  const [roles, setRoles] = useState<LibraryRoles>();
  const [roleInput, setRoleInput] = useState("");
  const [jd, setJd] = useState("");
  const [busy, setBusy] = useState<MessageKey | null>(null);
  const [error, setError] = useState<UiMessage | null>(null);
  const [status, setStatus] = useState<MessageKey | null>(null);
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
        setError(null);
      },
      (failure: unknown) => {
        if (mounted) setError(messageFor(failure, 'registered.actionFailed'));
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

  async function operate(label: MessageKey, action: () => Promise<void>) {
    setBusy(label);
    setError(null);
    setStatus(null);
    try {
      await action();
    } catch (failure) {
      setError(messageFor(failure, 'registered.actionFailed'));
    } finally {
      setBusy(null);
    }
  }

  function saveRole(event: FormEvent) {
    event.preventDefault();
    const role = roleInput.trim();
    if (!role || busy) return;
    return operate('registered.roleSaving', async () => {
      applyRole(await send<LibraryRole>("/library/role", "PUT", { role, jd: jd.trim() }));
      setStatus('registered.roleSaved');
    });
  }

  function selectDirection(topic: string, onSaved?: () => void) {
    if (!active || busy) return;
    return operate('registered.topicSelecting', async () => {
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
