import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, downloadWorkspaceExport, type SettingsOverview } from "../api/client";
import { useAppState } from "../components/AppState";
import LanguageSelector from "../components/LanguageSelector";
import { useLocale } from "../i18n";
import { InterviewIcon } from "./InterviewIcon";
import { useInterviewAppearance, type ThemePreference } from "./appearance";

type Label = (zh: string, en: string) => string;
type ModelSettings = {
  active: boolean;
  api_key_set: boolean;
  provider: string | null;
  base_url: string | null;
  model: string | null;
};
type Field = Pick<ModelSettings, "provider" | "base_url" | "model"> & { api_key?: string };

function SettingsCard({
  title,
  detail,
  icon,
  children,
}: {
  title: string;
  detail: string;
  icon: "settings" | "model" | "user" | "memory";
  children: ReactNode;
}) {
  return (
    <section className="ia-settings-card">
      <header>
        <span className="ia-settings-icon">
          <InterviewIcon name={icon} />
        </span>
        <div>
          <h2>{title}</h2>
          <p>{detail}</p>
        </div>
      </header>
      {children}
    </section>
  );
}

function ModelForm({ label }: { label: Label }) {
  const [config, setConfig] = useState<ModelSettings>();
  const [provider, setProvider] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let active = true;
    void api
      .get<ModelSettings>("/settings/llm")
      .then((value) => {
        if (!active) return;
        setConfig(value);
        setProvider(value.provider ?? "");
        setBaseUrl(value.base_url ?? "");
        setModel(value.model ?? "");
      })
      .catch((reason) => {
        if (active) setError(String(reason.message ?? reason));
      });
    return () => {
      active = false;
    };
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields: Field = {
      provider: provider.trim(),
      base_url: baseUrl.trim(),
      model: model.trim(),
    };
    if (key.trim()) fields.api_key = key.trim();
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      const value = await api.put<ModelSettings>("/settings/llm", fields);
      setConfig(value);
      setKey("");
      setSaved(true);
      window.dispatchEvent(new Event("interview-context-changed"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsCard
      title={label("模型", "Model")}
      detail={label(
        "为教练对话和评分配置模型连接。",
        "Configure the model used for coaching and scoring.",
      )}
      icon="model"
    >
      {error && (
        <p role="alert" className="ia-form-error">
          {error}
        </p>
      )}
      {!config ? (
        !error && <p role="status">{label("正在读取模型配置…", "Loading model configuration…")}</p>
      ) : (
        <form className="ia-settings-form" onSubmit={save}>
          <p className="ia-form-status">
            {config.active
              ? label("模型已配置", "Model configured")
              : label("模型待配置", "Model setup required")}
          </p>
          <label>
            {label("服务商", "Provider")}
            <input
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
              required
            />
          </label>
          <label>
            {label("接口地址", "API base URL")}
            <input
              type="url"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              required
            />
          </label>
          <label>
            {label("模型名称", "Model name")}
            <input value={model} onChange={(event) => setModel(event.target.value)} required />
          </label>
          <label>
            {label("API 密钥", "API key")}
            <input
              type="password"
              autoComplete="off"
              value={key}
              onChange={(event) => setKey(event.target.value)}
              required={!config.api_key_set}
              placeholder={
                config.api_key_set
                  ? label("已配置，留空保留当前密钥", "Configured. Leave blank to keep it.")
                  : ""
              }
            />
          </label>
          <div className="ia-settings-actions">
            <button type="submit" disabled={busy}>
              {label("保存模型", "Save model")}
            </button>
            {saved && <span role="status">{label("已保存", "Saved")}</span>}
          </div>
        </form>
      )}
    </SettingsCard>
  );
}

function AccountForm({ label }: { label: Label }) {
  const { setWsOpen } = useAppState();
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void api
      .get<SettingsOverview>("/settings/overview")
      .then((value) => {
        if (!active) return;
        setDisplayName(value.account?.display_name ?? "");
        setEmail(value.account?.email ?? "");
        setLoaded(true);
      })
      .catch((reason) => {
        if (active) setError(String(reason.message ?? reason));
      });
    return () => {
      active = false;
    };
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await api.patch("/settings/account", {
        display_name: displayName.trim(),
        email: email.trim(),
      });
      setMessage(label("账户已保存", "Account saved"));
      window.dispatchEvent(new Event("interview-context-changed"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function exportData() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const archive = await downloadWorkspaceExport();
      const url = URL.createObjectURL(archive);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = "careerpilot-workspace.json";
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }
  return (
    <SettingsCard
      title={label("账号与工作区", "Account and workspace")}
      icon="user"
      detail={label(
        "管理个人资料、工作区和本地备份。",
        "Manage your profile, workspace and local export.",
      )}
    >
      {error && (
        <p role="alert" className="ia-form-error">
          {error}
        </p>
      )}
      {loaded && (
        <form className="ia-settings-form" onSubmit={save}>
          <label>
            {label("显示名称", "Display name")}
            <input
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              required
            />
          </label>
          <label>
            {label("邮箱", "Email")}
            <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} />
          </label>
          <div className="ia-settings-actions">
            <button type="submit" disabled={busy}>
              {label("保存账号", "Save account")}
            </button>
            <button type="button" className="ia-secondary" onClick={() => setWsOpen(true)}>
              {label("管理工作区", "Manage workspaces")}
            </button>
            <button
              type="button"
              className="ia-secondary"
              onClick={() => void exportData()}
              disabled={busy}
            >
              {label("导出工作区数据", "Export workspace data")}
            </button>
          </div>
          {message && <p role="status">{message}</p>}
        </form>
      )}
    </SettingsCard>
  );
}

export default function InterviewSettings() {
  const english = useLocale() === "en";
  const label: Label = (zh, en) => (english ? en : zh);
  const { preference, setPreference } = useInterviewAppearance();
  const modes = [
    ["light", "浅色", "Light"],
    ["dark", "深色", "Dark"],
    ["system", "跟随系统", "System"],
  ] as const satisfies readonly [ThemePreference, string, string][];
  return (
    <div className="ia-settings">
      <header className="ia-settings-heading">
        <h1>{label("设置", "Settings")}</h1>
        <p>
          {label(
            "管理账号、显示偏好、记忆和模型。",
            "Manage your account, appearance, memory and model.",
          )}
        </p>
      </header>
      <SettingsCard
        title={label("界面偏好", "Appearance")}
        detail={label("应用于工作台和正式考试。", "Applies to the workspace and exams.")}
        icon="settings"
      >
        <div
          className="ia-settings-actions"
          role="group"
          aria-label={label("显示模式", "Appearance mode")}
        >
          {modes.map(([id, zh, en]) => (
            <button
              type="button"
              key={id}
              aria-pressed={preference === id}
              className={preference === id ? "" : "ia-secondary"}
              onClick={() => setPreference(id)}
            >
              {label(zh, en)}
            </button>
          ))}
          <LanguageSelector />
        </div>
      </SettingsCard>
      <ModelForm label={label} />
      <AccountForm label={label} />
      <SettingsCard
        title={label("记忆和提醒", "Memories and reminders")}
        detail={label(
          "查看教练在当前会话中使用的资料和作答记录。",
          "Inspect the sources and answers available to your coach.",
        )}
        icon="memory"
      >
        <Link className="ia-settings-link" to="/chat?memory=1">
          {label("查看记忆", "View memory")}
        </Link>
      </SettingsCard>
    </div>
  );
}
