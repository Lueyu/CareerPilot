/**
 * Browser layout assertion for chat alignment.
 *
 * The unit test covers the measurement maths; this covers the actual layout:
 * a space-taking scrollbar shrinks the scroll area's content box, so a message
 * column sized against the scroll area drifts away from the header/composer
 * that are sized against the full shell. The fix publishes the measured gutter
 * on the shell and lets the scroll area overflow back into it.
 *
 * Runs against a static fixture at several viewport widths, with compensation
 * on (must align), off (must reproduce the drift, proving the assertion bites)
 * and with overlay scrollbars (must not shift at all).
 *
 * Usage: node tests/chat-alignment.browser.mjs
 *   CHAT_ALIGNMENT_CHANNEL      browser channel, defaults to "chrome"
 *   CHAT_ALIGNMENT_PLAYWRIGHT   path to a playwright package directory when it
 *                               is not resolvable from this directory
 */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { mkdir, mkdtemp, readFile } from 'node:fs/promises';
import { extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build, loadConfigFromFile } from 'vite';

const require = createRequire(import.meta.url);
function loadPlaywright() {
  try {
    return require('playwright');
  } catch {
    const override = process.env.CHAT_ALIGNMENT_PLAYWRIGHT;
    assert.ok(
      override,
      'Install playwright, or set CHAT_ALIGNMENT_PLAYWRIGHT to a playwright package directory.',
    );
    return require(override);
  }
}
const { chromium } = loadPlaywright();

const web = fileURLToPath(new URL('../', import.meta.url));
const artifacts = fileURLToPath(new URL('../../.agent-runs/chat-alignment-browser/', import.meta.url));
await mkdir(artifacts, { recursive: true });
const outDir = await mkdtemp(join(artifacts, 'built-'));

const { config } = await loadConfigFromFile(
  { command: 'build', mode: 'production' },
  join(web, 'vite.config.ts'),
  web,
);
await build({
  ...config,
  configFile: false,
  root: web,
  logLevel: 'error',
  build: {
    ...config.build,
    outDir,
    emptyOutDir: true,
    rollupOptions: {
      ...config.build.rollupOptions,
      input: join(web, 'tests/chat-alignment.fixture.html'),
    },
  },
});

const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css' };
const server = createServer(async (request, response) => {
  const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
  if (!/^\/(?:assets\/[\w.-]+|tests\/chat-alignment\.fixture\.html)$/.test(pathname)) {
    response.writeHead(404);
    return response.end();
  }
  try {
    const content = await readFile(join(outDir, pathname.slice(1)));
    response.writeHead(200, { 'Content-Type': mime[extname(pathname)] });
    response.end(content);
  } catch {
    response.writeHead(404);
    response.end();
  }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = 'http://127.0.0.1:' + server.address().port;
const url = origin + '/tests/chat-alignment.fixture.html';

const browser = await chromium.launch({ channel: process.env.CHAT_ALIGNMENT_CHANNEL || 'chrome' });

const round2 = value => Math.round(value * 100) / 100;

/** Read the inline edges and the real gutter of one fixture case. */
function measure(frame) {
  return frame.evaluate(node => {
    const round = value => Math.round(value * 100) / 100;
    const edges = name => {
      const element = node.querySelector(`[data-measure="${name}"]`);
      if (!element) throw new Error(`fixture case "${node.dataset.case}" has no [data-measure="${name}"]`);
      const rect = element.getBoundingClientRect();
      return { left: round(rect.left), right: round(rect.right), width: round(rect.width) };
    };
    const area = node.querySelector('.cp-scroll-area');
    return {
      column: edges('column'),
      fixed: edges('fixed'),
      head: node.querySelector('[data-measure="head"]') ? edges('head') : null,
      compose: node.querySelector('[data-measure="compose"]') ? edges('compose') : null,
      gutter: area.offsetWidth - area.clientWidth,
    };
  });
}

function near(actual, expected, tolerance, label) {
  assert.ok(
    Math.abs(actual - expected) <= tolerance,
    `${label}: expected ${expected} ± ${tolerance}, got ${actual}`,
  );
}

/** Both inline edges of the scroll column must match the fixed chrome. */
function assertAligned(box, reference, label) {
  near(box.left, reference.left, 1, `${label} left edge`);
  near(box.right, reference.right, 1, `${label} right edge`);
}

const driftOf = ({ column, fixed }) =>
  Math.max(Math.abs(column.right - fixed.right), Math.abs(column.left - fixed.left));

const settle = tab =>
  tab.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));

const widths = [800, 1024, 1280, 1440];
const report = [];
const cases = ['chat', 'study'];

let context;
try {
  context = await browser.newContext();
  const page = await context.newPage();

  for (const width of widths) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(url);

    for (const name of cases) {
      const frame = page.locator(`[data-case="${name}"]`);
      await frame.scrollIntoViewIfNeeded();
      await settle(page);

      const compensated = await measure(frame);
      assert.ok(compensated.gutter > 0, `${name}@${width}: fixture must reproduce a space-taking scrollbar`);
      assertAligned(compensated.column, compensated.fixed, `${name}@${width} scroll column vs composer`);
      if (compensated.head) assertAligned(compensated.column, compensated.head, `${name}@${width} scroll column vs header`);
      if (compensated.compose) assertAligned(compensated.column, compensated.compose, `${name}@${width} scroll column vs footer`);
      await frame.screenshot({ path: join(artifacts, `${name}-${width}-compensated.png`) });

      await frame.evaluate(node => { node.dataset.compensated = 'false'; });
      await settle(page);
      const broken = await measure(frame);
      // A capped, centred column loses half the gutter on each side; a fluid
      // column keeps its left edge and loses the whole gutter on the right.
      // Either way the drift must be visible, so the assertion cannot pass on
      // the pre-fix layout.
      const drift = driftOf(broken);
      assert.ok(
        drift >= broken.gutter / 2 - 1,
        `${name}@${width}: without compensation the columns must drift by the gutter, got ${round2(drift)}px for a ${broken.gutter}px gutter`,
      );
      assert.ok(drift > 1, `${name}@${width}: the pre-fix layout must be detectably misaligned`);
      await frame.screenshot({ path: join(artifacts, `${name}-${width}-uncompensated.png`) });

      await frame.evaluate(node => { node.dataset.compensated = 'true'; });
      await settle(page);
      const restored = await measure(frame);
      assertAligned(restored.column, restored.fixed, `${name}@${width} after restoring compensation`);

      report.push(`${name}@${width}: gutter ${compensated.gutter}px, drift without fix ${round2(drift)}px`);
    }
  }

  // Overlay scrollbars take no space: nothing may shift, so the shell must not
  // reserve a gutter even though the measurement hook is mounted.
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(url);
  await page.addStyleTag({ content: '.cp-scroll-area { border-inline-end: 0 !important; }' });
  await settle(page);
  for (const name of cases) {
    const frame = page.locator(`[data-case="${name}"]`);
    await frame.scrollIntoViewIfNeeded();
    const overlay = await measure(frame);
    assert.equal(overlay.gutter, 0, `${name}: overlay scrollbars must measure a 0px gutter`);
    assertAligned(overlay.column, overlay.fixed, `${name} overlay scrollbars`);
    if (overlay.head) assertAligned(overlay.column, overlay.head, `${name} overlay header`);
    if (overlay.compose) assertAligned(overlay.column, overlay.compose, `${name} overlay footer`);
  }

  // A resize after mount must re-measure instead of keeping a stale width.
  await page.setViewportSize({ width: 700, height: 900 });
  await settle(page);
  for (const name of cases) {
    const frame = page.locator(`[data-case="${name}"]`);
    await frame.scrollIntoViewIfNeeded();
    const resized = await measure(frame);
    assertAligned(resized.column, resized.fixed, `${name} after resize`);
  }

  console.log('Chat alignment layout assertions passed.');
  for (const line of report) console.log(`  ${line}`);
} finally {
  await context?.close();
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
