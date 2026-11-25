import { useEffect, useRef } from "react";
import { Icon, useResource } from "./ui";
import type { ChatSource } from "./model";

interface MemoryItem {
  id: number;
  mtype: string;
  content: string;
  source: string;
  source_ref?: string;
  status: string;
  eligible_for_personalization?: boolean;
  ineligible_reason?: string;
}

export function MemoryPanel({ sources, onClose }: { sources: ChatSource[]; onClose: () => void }) {
  const modal = useRef<HTMLDialogElement>(null);
  const { state, refresh } = useResource<{ items: MemoryItem[] }>("/memory");
  useEffect(() => {
    modal.current?.showModal();
  }, []);

  return (
    <dialog
      ref={modal}
      className="ia-chat-memory"
      aria-label="对话记忆"
      onClose={onClose}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="ia-chat-memory-body">
        <header className="ia-chat-card-head">
          <h2>对话记忆</h2>
          <button onClick={onClose} aria-label="关闭记忆">
            ×
          </button>
        </header>
        <section>
          <h3>当前回复使用的来源</h3>
          <p>这些条目来自回复记录，可以核对建议引用了哪些材料。</p>
          {sources.length ? (
            <ul>
              {sources.map((source, index) => (
                <li key={source.kind + ":" + source.id + ":" + index}>
                  <strong>{source.label}</strong>
                  {source.text && <p>{source.text}</p>}
                </li>
              ))}
            </ul>
          ) : (
            <p>当前回复暂无可显示的来源。</p>
          )}
        </section>
        <section>
          <h3>工作区记忆</h3>
          <p>已确认的条目与待核对的记忆会显示在这里。回复是否读取某个条目，以本轮的来源为准。</p>
          {state.kind === "loading" && <p role="status">正在读取记忆…</p>}
          {state.kind === "error" && (
            <div role="alert">
              <p>{state.error}</p>
              <button onClick={refresh}>重试</button>
            </div>
          )}
          {state.kind === "ready" &&
            (state.data.items.length ? (
              <ul>
                {state.data.items.map((item) => (
                  <li key={item.id}>
                    <small>
                      {item.eligible_for_personalization
                        ? "可用于个性化"
                        : item.ineligible_reason ||
                          (item.status === "ACTIVE" ? "已记录" : "待核对")}
                    </small>
                    <p>{item.content}</p>
                    <small>
                      {item.source}
                      {item.source_ref ? " · " + item.source_ref : ""}
                    </small>
                  </li>
                ))}
              </ul>
            ) : (
              <p>此工作区还没有记忆条目。</p>
            ))}
        </section>
        <section>
          <h3 className="ia-chat-card-head">
            <Icon name="moon" />
            Dreaming
          </h3>
          <p>工作区尚未提供晨间整理报告。</p>
        </section>
      </div>
    </dialog>
  );
}
