import { useMemo, useSyncExternalStore } from "react";
import { useLocale } from "../../../i18n";
import { demoSample, emptyDemoMemory, type AgentMemory } from "./model";

// 演示工作区的示例记忆开关。这里只保存一个布尔值，不接触真实会话、笔记、
// 题库或能力画像；重置语义是「清空本页演示内容」，不是「清除真实上下文」。

export const DEMO_CONTEXT_RESET_EVENT = "interview-demo-context-reset";

let reset = false;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getResetSnapshot(): boolean {
  return reset;
}

function dispatchResetEvent() {
  try {
    window.dispatchEvent(new Event(DEMO_CONTEXT_RESET_EVENT));
  } catch {
    /* 非浏览器环境忽略 */
  }
}

export function resetDemoContext() {
  reset = true;
  emit();
  dispatchResetEvent();
}

export function restoreDemoMemory() {
  reset = false;
  emit();
}

export function isDemoContextReset(): boolean {
  return reset;
}

export function useDemoMemory(): AgentMemory {
  const locale = useLocale();
  const cleared = useSyncExternalStore(subscribe, getResetSnapshot, getResetSnapshot);
  return useMemo(
    () =>
      cleared
        ? emptyDemoMemory()
        : { ...demoSample(locale).memory, weakPoints: [...demoSample(locale).memory.weakPoints] },
    [cleared, locale],
  );
}
