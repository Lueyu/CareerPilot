import { useSyncExternalStore } from "react";
import { DEFAULT_MEMORY, type AgentMemory } from "./model";

// 面试 AI 助手 · 会话内临时上下文（mock）
// 清除上下文仅重置工作记忆展示，长期记忆 / 笔记 / 题库不受影响。

export const CONTEXT_CLEARED_EVENT = "interview-agent-context-cleared";

let memory: AgentMemory = DEFAULT_MEMORY;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

export function subscribeMemory(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getMemorySnapshot(): AgentMemory {
  return memory;
}

export function clearAgentContext() {
  memory = { targetRole: null, weakPoints: [], currentSkill: null, examCount: 0 };
  emit();
  try {
    window.dispatchEvent(new Event(CONTEXT_CLEARED_EVENT));
  } catch {
    /* 非浏览器环境忽略 */
  }
}

export function useAgentMemory(): AgentMemory {
  return useSyncExternalStore(subscribeMemory, getMemorySnapshot);
}
