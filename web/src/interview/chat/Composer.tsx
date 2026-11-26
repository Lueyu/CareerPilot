import { useRef, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { roles, workflows, type Intent, type Role, type Workflow } from "./model";
import { Icon } from "./ui";

const shortcuts: { intent: Intent; label: string }[] = [
  { intent: "exam", label: "正式考试" },
  { intent: "plan", label: "每日学习计划" },
  { intent: "quiz", label: "随堂测验" },
  { intent: "benchmark", label: "岗位能力快照" },
  { intent: "questions", label: "检索题库" },
  { intent: "jd", label: "导入 JD" },
];

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
        <section className="ia-chat-palette" aria-label="角色与技能">
          <header className="ia-chat-card-head">
            <h3>角色与技能</h3>
            <button
              type="button"
              onClick={() => {
                if (slash) put("");
                togglePalette(false);
              }}
              aria-label="关闭角色与技能"
            >
              ×
            </button>
          </header>
          <div className="ia-chat-palette-grid">
            <div>
              <h4>角色</h4>
              {matching.map((item) => (
                <button
                  type="button"
                  className="ia-chat-palette-entry"
                  key={item.id}
                  disabled={disabled || running}
                  onClick={() => pick(item.id)}
                >
                  <strong>{item.name}</strong>
                  <code>/{item.id}</code>
                  <small>{item.detail}</small>
                </button>
              ))}
              {!matching.length && <p>没有匹配的角色。</p>}
            </div>
            {!query && (
              <div>
                <h4>技能</h4>
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
                    <strong>{item.name}</strong>
                    <Icon name="chevron" />
                  </button>
                ))}
              </div>
            )}
          </div>
          {!query && (
            <div className="ia-chat-actions ia-chat-shortcuts">
              {shortcuts.map((item) => (
                <button
                  type="button"
                  key={item.intent}
                  onClick={() => {
                    if (slash) put("");
                    togglePalette(false);
                    open(item.intent);
                  }}
                >
                  {item.label}
                </button>
              ))}
              <Link to="/library/questions">资料库题库</Link>
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
            <span>{selected.name}</span>
            <button type="button" onClick={() => selectRole(null)} aria-label="恢复自动选择角色">
              ×
            </button>
          </div>
        )}
        <textarea
          ref={textarea}
          rows={2}
          aria-label="聊天输入"
          disabled={disabled}
          value={input}
          maxLength={8000}
          onKeyDown={inputKey}
          onChange={(event) => put(event.target.value)}
          placeholder="说说今天想准备什么，或输入 / 调用角色和技能…"
        />
        <div className="ia-chat-actions ia-chat-composer-actions">
          <button type="button" onClick={() => open("import")}>
            <Icon name="paperclip" />
            附件
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
            角色与技能
          </button>
          {status && <span role="status">{status}</span>}
          {running ? (
            <button
              type="button"
              className="ia-chat-primary ia-chat-send"
              onClick={interrupt}
              disabled={disabled}
            >
              停止
            </button>
          ) : (
            <button
              className="ia-chat-primary ia-chat-send"
              type="submit"
              aria-label="发送"
              disabled={!input.trim() || disabled}
            >
              <Icon name="arrow" />
            </button>
          )}
        </div>
      </form>
      <small>Enter 发送 · Shift + Enter 换行</small>
    </footer>
  );
}
