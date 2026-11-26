import { useRef, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { roles, workflows, type Intent, type Role, type Workflow } from "./model";
import { useT } from "../../i18n";
import { Icon } from "./ui";

const shortcuts: Intent[] = ["exam", "plan", "quiz", "benchmark", "questions", "jd"];

export default function Composer({
  input,
  put,
  role,
  selectRole,
  palette,
  togglePalette,
  start,
  open,
  status,
  send,
  disabled,
  running,
  interrupt,
}: {
  input: string;
  put: (text: string) => void;
  role: Role | null;
  selectRole: (role: Role | null) => void;
  palette: boolean;
  togglePalette: (open: boolean) => void;
  start: (id: Workflow) => void;
  open: (intent: Intent) => void;
  status?: string;
  send: () => void;
  disabled: boolean;
  running: boolean;
  interrupt: () => void;
}) {
  const t = useT();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const slash = /^\/([a-z]*)$/i.exec(input.trim());
  const query = slash?.[1].toLowerCase() ?? "";
  const shown = palette || !!slash;
  const matching = roles.filter((item) => item.id.startsWith(query));
  const selected = roles.find((item) => item.id === role);

  function pick(id: Role) {
    selectRole(id);
    if (slash) put("");
    togglePalette(false);
    if (id === "exam") open("exam");
    textarea.current?.focus();
  }

  function inputKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    if (event.key === "Escape" && shown) {
      if (slash) put("");
      togglePalette(false);
      return;
    }
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    if (disabled || running) return;
    if (slash && matching.length === 1) pick(matching[0].id);
    else if (!slash || !matching.length) send();
  }

  return (
    <footer className="ia-chat-composer">
      {shown && (
        <section className="ia-chat-palette" aria-label={t("interview.palette")}>
          <header className="ia-chat-card-head">
            <h3>{t("interview.palette")}</h3>
            <button
              type="button"
              onClick={() => {
                if (slash) put("");
                togglePalette(false);
              }}
              aria-label={t("interview.palette.close")}
            >
              ×
            </button>
          </header>
          <div className="ia-chat-palette-grid">
            <div>
              <h4>{t("interview.roles")}</h4>
              {matching.map((item) => (
                <button
                  type="button"
                  className="ia-chat-palette-entry"
                  key={item.id}
                  disabled={disabled || running}
                  onClick={() => pick(item.id)}
                >
                  <strong>{t("interview.role." + item.id)}</strong>
                  <code>/{item.id}</code>
                  <small>{t("interview.role." + item.id + ".detail")}</small>
                </button>
              ))}
              {!matching.length && <p>{t("interview.roles.empty")}</p>}
            </div>
            {!query && (
              <div>
                <h4>{t("interview.skills")}</h4>
                {workflows.map((item) => (
                  <button
                    type="button"
                    className="ia-chat-palette-entry"
                    key={item.id}
                    disabled={disabled || running}
                    onClick={() => {
                      if (slash) put("");
                      togglePalette(false);
                      start(item.id);
                    }}
                  >
                    <strong>{t("interview.workflow." + item.id)}</strong>
                    <Icon name="chevron" />
                  </button>
                ))}
              </div>
            )}
          </div>
          {!query && (
            <div className="ia-chat-actions ia-chat-shortcuts">
              {shortcuts.map((intent) => (
                <button
                  type="button"
                  key={intent}
                  onClick={() => {
                    if (slash) put("");
                    togglePalette(false);
                    open(intent);
                  }}
                >
                  {t("interview.shortcut." + intent)}
                </button>
              ))}
              <Link to="/library/questions">{t("interview.library")}</Link>
            </div>
          )}
        </section>
      )}
      <form
        className="ia-chat-composer-box"
        onSubmit={(event) => {
          event.preventDefault();
          send();
        }}
      >
        {selected && (
          <div className="ia-chat-selected">
            <span>{t("interview.role." + selected.id)}</span>
            <button
              type="button"
              onClick={() => selectRole(null)}
              aria-label={t("interview.role.reset")}
            >
              ×
            </button>
          </div>
        )}
        <textarea
          ref={textarea}
          rows={2}
          aria-label={t("interview.composer.input")}
          disabled={disabled}
          value={input}
          maxLength={8000}
          onKeyDown={inputKey}
          onChange={(event) => put(event.target.value)}
          placeholder={t("interview.composer.placeholder")}
        />
        <div className="ia-chat-actions ia-chat-composer-actions">
          <button type="button" onClick={() => open("import")}>
            <Icon name="paperclip" />
            {t("interview.attachment")}
          </button>
          <button
            type="button"
            aria-expanded={shown}
            onClick={() => {
              if (shown && slash) put("");
              togglePalette(!shown);
            }}
          >
            <Icon name="slash" />
            {t("interview.palette")}
          </button>
          {status && <span role="status">{status}</span>}
          {running ? (
            <button
              type="button"
              className="ia-chat-primary ia-chat-send"
              onClick={interrupt}
              disabled={disabled}
            >
              {t("interview.stop")}
            </button>
          ) : (
            <button
              className="ia-chat-primary ia-chat-send"
              type="submit"
              aria-label={t("interview.send")}
              disabled={!input.trim() || disabled}
            >
              <Icon name="arrow" />
            </button>
          )}
        </div>
      </form>
      <small>{t("interview.composer.help")}</small>
    </footer>
  );
}
