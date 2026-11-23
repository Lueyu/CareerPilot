// Build web/dist, then run with STUDY_PLAYWRIGHT pointing to the installed Playwright package.
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require(process.env.STUDY_PLAYWRIGHT || 'playwright');

const dist = path.resolve(__dirname, '../../web/dist');
const web = 'http://127.0.0.1:18869';
const session = '/api/v1/study/sessions/1';

async function verify(browser, scenario) {
  const page = await browser.newPage();
  const saved = '已保存的合成回答';
  let streams = 0;
  let reads = 0;
  const writes = [];
  try {
    await page.route('**/*', async route => {
      const { origin, pathname } = new URL(route.request().url());
      if (origin === web && pathname === '/study/chat/1') {
        return route.fulfill({ path: path.join(dist, 'index.html') });
      }
      if (origin === web && /^\/assets\/[\w.-]+$/.test(pathname)) {
        return route.fulfill({ path: path.join(dist, pathname) });
      }
      if (!pathname.startsWith('/api/v1/')) return route.abort();
      const method = route.request().method();
      if (method !== 'GET') {
        writes.push(method);
        return route.abort();
      }
      if (pathname === session + '/messages/7/stream') {
        streams++;
        const completed = scenario === 'refresh-error' || (scenario === 'reconnect' && streams === 2);
        const event = { id: 7, attempt: 1, status: completed ? 'completed' : 'generating',
          stage: 'generating', model: null, error: null, content: '合成的流式快照' };
        return route.fulfill({ contentType: 'text/event-stream', body: 'data: ' + JSON.stringify(event) + '\n\n' });
      }
      if (pathname === session) {
        reads++;
        if (scenario === 'refresh-error' && reads === 2) return route.fulfill({ status: 503, json: { detail: '稍后重试' } });
        const message = { id: 7, role: 'assistant', sources: [], status: reads === 1 ? 'generating' : 'completed',
          content: reads === 1 ? '' : saved };
        return route.fulfill({ json: { id: 1, title: '合成会话', status: 'active', context: { sources: [] }, messages: [message] } });
      }
      if (pathname.endsWith('/settings/overview')) {
        return route.fulfill({ json: { workspace: { name: '合成工作区', is_demo: false } } });
      }
      return route.fulfill({ json: pathname.endsWith('/interview-question') ? null : { items: [] } });
    });
    await page.goto(web + '/study/chat/1');
    await page.getByText(saved, { exact: true }).waitFor({ timeout: 12000 });
    await page.getByRole('textbox', { name: '继续问' }).fill('合成追问');
    assert.ok(await page.getByRole('button', { name: '发送', exact: true }).isEnabled());
    assert.deepEqual(writes, []);
    assert.equal(streams, { reconnect: 2, polling: 3, 'refresh-error': 1 }[scenario]);
    assert.equal(reads, scenario === 'refresh-error' ? 3 : 2);
    console.log(JSON.stringify({ scenario, streams, reads, writes: writes.length }));
  } finally {
    await page.close();
  }
}

async function main() {
  const browser = await chromium.launch({ channel: process.env.STUDY_BROWSER_CHANNEL || 'msedge' });
  try {
    for (const scenario of ['reconnect', 'polling', 'refresh-error']) await verify(browser, scenario);
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
