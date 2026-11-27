/**
 * Measure the scroll area's real, space-taking gutter and publish it on the
 * shell (see `styles/scrollGutter.css`). Overlay scrollbars measure 0 and must
 * not shift anything. Measuring the actual container instead of hard-coding a
 * width matters: the gutter varies with browser, OS and zoom, and a wrong
 * constant over-corrects.
 *
 * Kept free of framework imports and effects so the geometry can be unit-tested
 * in a clean Git snapshot with no installed node_modules.
 */
export function applyScrollbarGutter(
  shellEl: HTMLElement | null,
  areaEl: HTMLElement | null,
): number {
  if (!shellEl || !areaEl) return 0;
  const gutter = Math.max(0, areaEl.offsetWidth - areaEl.clientWidth);
  shellEl.style.setProperty("--cp-scrollbar-gutter", `${gutter}px`);
  return gutter;
}
