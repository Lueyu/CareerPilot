// Run with Playwright installed (or set NODE_PATH to its module directory).
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const { chromium } = createRequire(import.meta.url)('playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
process.env.VITE_API_BASE = 'http://127.0.0.1:18371/api/v1';
process.env.VITE_AUTH_MODE = 'local';
process.env.VITE_WORKSPACE = 'personal';
const browser = await chromium.launch(process.env.FEEDBACK_BROWSER_CHANNEL
  ? { channel: process.env.FEEDBACK_BROWSER_CHANNEL } : {});
const feedbackText = '合成反馈：希望更容易找到题库';
const deliveries = [
  [503, { detail: 'unavailable' }, '反馈服务暂不可用'],
  [422, { detail: 'rejected' }, '反馈未通过审核'],
  [429, { detail: 'quota' }, '已达到发送限额'],
  [409, { detail: 'unknown' }, '暂时无法确认发送结果'],
  [403, { detail: 'denied' }, '当前会话无权发送反馈'],
  [201, { status: 'sent' }, '无法确认反馈是否已发送'],
  [200, { status: 'queued' }, '无法确认反馈是否已发送'],
  [200, { status: 'sent' }, '反馈已交给邮件服务器'],
];

async function check({ base, registered, width }) {
  const context = await browser.newContext({ viewport: { width, height: width < 500 ? 568 : 900 } });
  try {
    let account = { username: 'fixture-user', workspace_id: 42 };
    const payloads = [];
    let releaseFirst;
    const firstResponse = new Promise(resolve => { releaseFirst = resolve; });
    const outside = [];
    await context.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (!url.pathname.startsWith('/api/v1/')) {
        if (url.origin === base) return route.continue();
        outside.push(url.origin);
        return route.abort();
      }
      const path = url.pathname.slice('/api/v1'.length);
      const cors = {
        'access-control-allow-origin': base,
        'access-control-allow-headers': 'content-type,x-api-token,x-workspace',
        'access-control-allow-methods': 'GET,POST,OPTIONS',
      };
      if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: cors });
      assert.notEqual(path, '/feedback');
      assert.notEqual(path, '/native/product-feedback');
      if (path === '/product-feedback') {
        assert.equal(request.method(), 'POST');
        assert.match(request.headers()['content-type'], /^application\/json/);
        const body = request.postDataJSON();
        assert.deepEqual(Object.keys(body).sort(), ['message', 'submission_id']);
        assert.equal(body.message, feedbackText);
        assert.match(body.submission_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
        if (registered) {
          assert.equal(url.origin, base);
          assert.equal(request.headers()['x-expected-workspace'], '42');
          assert.equal(request.headers()['x-api-token'], undefined);
        } else {
          assert.equal(request.headers()['x-workspace'], 'personal');
          assert.equal(request.headers()['x-api-token'], 'synthetic-feedback-test-token');
          assert.equal(request.headers()['x-expected-workspace'], undefined);
        }
        const index = payloads.push(body) - 1;
        if (index === 0) await firstResponse;
        if (registered && index === deliveries.length) {
          account = { username: 'second-fixture', workspace_id: 43 };
          return route.fulfill({ status: 409, json: { detail: { code: 'workspace_changed' } } });
        }
        const [status, json] = deliveries[index];
        return route.fulfill({ status, json, headers: cors });
      }
      if (path === '/auth/me') return route.fulfill({ json: account });
      if (path === '/native/access') return route.fulfill({ json: { personal_owner: true } });
      if (path === '/settings/workspaces') return route.fulfill({ headers: cors, json: {
        workspaces: [{ id: 1, name: 'Synthetic demo', slug: 'demo', is_demo: true }],
      } });
      return route.fulfill({ status: 503, json: { detail: 'Fixture only' }, headers: cors });
    });

    const page = await context.newPage();
    if (!registered) await page.addInitScript(() => {
      sessionStorage.setItem('cp-login-workspace', 'personal');
      sessionStorage.setItem('cp-login-token', 'synthetic-feedback-test-token');
    });
    await page.goto(base + (registered ? '/settings/account' : '/'), {
      waitUntil: 'domcontentloaded', timeout: 60_000,
    });
    const entry = page.locator('.topbar').getByRole('button', { name: '提意见' });
    await entry.waitFor();
    const search = page.getByRole('button', { name: '全局搜索' });
    const quick = page.getByRole('button', { name: '快捷操作' });
    for (const control of [search, quick]) {
      const controlBounds = await control.boundingBox();
      assert.ok(controlBounds && controlBounds.x >= 0 && controlBounds.x + controlBounds.width <= width);
    }
    await search.focus();
    await page.keyboard.press('Enter');
    const searchDialog = page.getByRole('dialog', { name: '全局搜索' });
    await searchDialog.getByRole('combobox').waitFor();
    await page.keyboard.press('Escape');
    await searchDialog.waitFor({ state: 'hidden' });
    assert.equal(await search.evaluate(el => el === document.activeElement), true);
    await quick.focus();
    await page.keyboard.press('Enter');
    const quickDialog = page.getByRole('dialog', { name: '快捷操作' });
    await quickDialog.getByRole('button', { name: /导入面经/ }).waitFor();
    const quickBounds = await quickDialog.boundingBox();
    assert.ok(quickBounds && quickBounds.x >= 0 && quickBounds.x + quickBounds.width <= width);
    await page.keyboard.press('Tab');
    assert.equal(await quickDialog.getByRole('button', { name: /导入面经/ })
      .evaluate(el => el === document.activeElement), true);
    await page.keyboard.press('Escape');
    await quickDialog.waitFor({ state: 'hidden' });
    assert.equal(await quick.evaluate(el => el === document.activeElement), true);
    if (width <= 760) {
      const menu = page.getByRole('button', { name: '菜单', exact: true });
      const drawer = page.getByRole('dialog', { name: 'CareerPilot 导航' });
      await menu.click();
      await drawer.getByRole('link', { name: '设置与工作空间' }).click();
      await drawer.waitFor({ state: 'hidden' });
      await page.waitForURL('**/settings');
      await page.goBack({ waitUntil: 'domcontentloaded' });
      await entry.waitFor();
    }
    const entryBounds = await entry.boundingBox();
    assert.ok(entryBounds.x >= 0 && entryBounds.x + entryBounds.width <= width);
    await entry.click();
    const dialog = page.getByRole('dialog', { name: '意见与贡献' });
    await dialog.waitFor();
    const bounds = await dialog.boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width);
    const darkSurface = await dialog.evaluate(el => getComputedStyle(el).backgroundColor);
    const tab = dialog.getByRole('tab', { name: '提意见' });
    assert.equal(await tab.evaluate(el => el === document.activeElement), true);
    const draft = dialog.getByLabel('你的意见');
    await dialog.getByText('反馈文本会发送至内容审核服务，通过后以邮件发给维护者。', { exact: false }).waitFor();
    const send = dialog.getByRole('button', { name: '发送反馈' });
    assert.equal(await send.isDisabled(), true);
    await draft.fill('字'.repeat(2001));
    assert.equal(await send.isDisabled(), true);
    await dialog.getByText('反馈最多 2000 字，请缩短后发送。').waitFor();
    await draft.fill('  ' + feedbackText + '  ');
    await send.click();
    const busy = dialog.getByRole('button', { name: '正在发送…' });
    assert.equal(await busy.isDisabled(), true);
    await busy.evaluate(el => el.click());
    await busy.evaluate(el => el.form.requestSubmit());
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(await entry.evaluate(el => el === document.activeElement), true);
    await entry.click();
    assert.equal(await busy.isDisabled(), true);
    releaseFirst();

    for (const [index, [, , notice]] of deliveries.entries()) {
      if (index > 0) await send.click();
      await dialog.getByText(notice, { exact: false }).waitFor();
      assert.equal(await draft.inputValue(), index === deliveries.length - 1 ? '' : '  ' + feedbackText + '  ');
    }
    assert.equal(payloads.length, deliveries.length);
    assert.equal(new Set(payloads.map(body => body.submission_id)).size, 1);
    await tab.focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await dialog.getByRole('tab', { name: '发起 PR' }).getAttribute('aria-selected'), 'true');
    assert.equal(await dialog.getByRole('link', { name: 'Fork 仓库' }).getAttribute('href'),
      'https://github.com/Lueyu/CareerPilot/fork');
    await dialog.getByLabel('GitHub 用户名').fill('synthetic-user');
    const branch = dialog.getByLabel('包含改动的分支');
    await branch.fill('refs//bad');
    assert.equal(await dialog.getByRole('button', { name: '前往 GitHub' }).isDisabled(), true);
    await branch.fill('feature/中文');
    const github = dialog.getByRole('link', { name: '前往 GitHub' });
    assert.equal(await github.getAttribute('href'),
      'https://github.com/Lueyu/CareerPilot/compare/dev...synthetic-user:feature%2F%E4%B8%AD%E6%96%87?expand=1');
    await dialog.getByLabel('GitHub 用户名').fill('lueyu');
    assert.equal(await github.getAttribute('href'),
      'https://github.com/Lueyu/CareerPilot/compare/dev...feature%2F%E4%B8%AD%E6%96%87?expand=1');
    assert.equal(await github.getAttribute('rel'), 'noreferrer');
    await dialog.getByRole('button', { name: '关闭' }).click();
    await dialog.waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: '切换主题' }).click();
    const theme = await page.locator('html').getAttribute('data-theme');
    assert.equal(theme, 'light');
    await entry.click();
    assert.notEqual(await dialog.evaluate(el => getComputedStyle(el).backgroundColor), darkSurface);
    assert.equal(await tab.getAttribute('aria-selected'), 'true');
    await draft.fill(registered ? feedbackText : 'synthetic private draft');
    assert.equal(await page.evaluate(() => [localStorage, sessionStorage].some(storage =>
      Object.values(storage).some(value => value.includes('synthetic private draft') || value.includes('合成反馈')))), false);

    if (registered) {
      await send.click();
      await page.getByText('second-fixture', { exact: true }).last().waitFor();
    } else {
      await page.keyboard.press('Escape');
      if (width <= 760) await page.getByRole('button', { name: '菜单', exact: true }).click();
      await page.locator('aside:visible .ws-card').click();
      await Promise.all([
        page.waitForEvent('load'),
        page.getByText('Synthetic demo', { exact: true }).click(),
      ]);
    }
    await dialog.waitFor({ state: 'hidden' });
    await entry.click();
    assert.equal(await draft.inputValue(), '');
    assert.deepEqual(outside, []);
    console.log(`${registered ? 'registered' : 'native'}, ${width}px: passed`);
  } finally {
    await context.close();
  }
}

try {
  for (const registered of [false, true]) {
    const server = await createServer({
      root,
      cacheDir: 'node_modules/.vite-feedback-' + (registered ? 'registered' : 'native'),
      configFile: root + (registered ? 'vite.seed.config.ts' : 'vite.config.ts'),
      server: { host: '127.0.0.1', port: 18370, hmr: false },
    });
    try {
      await server.listen();
      const base = server.resolvedUrls.local[0].replace(/\/$/, '');
      for (const width of [320, 390, 800, 1280]) await check({ base, registered, width });
    } finally {
      await server.close();
    }
  }
} finally {
  await browser.close();
}
