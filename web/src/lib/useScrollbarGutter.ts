import { useLayoutEffect, type RefObject } from "react";
import { applyScrollbarGutter } from "./scrollbarGutter.ts";

export { applyScrollbarGutter };

/** React binding: remeasure on resize and when the content grows a scrollbar. */
export function useScrollbarGutter(shell: RefObject<HTMLElement>, area: RefObject<HTMLElement>) {
  useLayoutEffect(() => {
    const shellEl = shell.current;
    const areaEl = area.current;
    if (!shellEl || !areaEl) return;
    let frame = 0;
    const measure = () => {
      applyScrollbarGutter(shellEl, areaEl);
    };
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    };
    measure();
    const observer = new ResizeObserver(schedule);
    observer.observe(areaEl);
    // Content growth can make a scrollbar appear/disappear without resizing the
    // scroll container itself, so watch the content box too.
    if (areaEl.firstElementChild) observer.observe(areaEl.firstElementChild);
    window.addEventListener("resize", schedule);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", schedule);
      shellEl.style.removeProperty("--cp-scrollbar-gutter");
    };
  }, [shell, area]);
}
