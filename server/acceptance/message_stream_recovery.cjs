// Build web/dist, then run with STUDY_PLAYWRIGHT pointing to the installed Playwright package.
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require(process.env.STUDY_PLAYWRIGHT || 'playwright');

const dist = path.resolve(__dirname, '../../web/dist');
const web = 'http://127.0.0.1:18869';
const session = '/api/v1/study/sessions/1';

function fulfillShell(route, pathname) {
  const { origin } = new URL(route.request().url());
  if (origin === web && pathname === '/study/chat/1') {
    return route.fulfill({ path: path.join(dist, 'index.html') });
  }
  if (origin === web && /^\/assets\/[\w.-]+$/.test(pathname)) {
    return route.fulfill({ path: path.join(dist, pathname) });
  }
  if (!pathname.startsWith('/api/v1/')) return route.abort();
  if (pathname.endsWith('/settings/overview')) {
    return route.fulfill({ json: { workspace: { name: '合成工作区', is_demo: false } } });
  }
  return route.fulfill({ json: pathname.endsWith('/interview-question') ? null : { items: [] } });
}

async function verify(browser, scenario) {
  const page = await browser.newPage();
  const saved = '已保存的合成回答';
  let streams = 0;
  let reads = 0;
  const writes = [];
  try {
    await page.route('**/*', async route => {
      const { pathname } = new URL(route.request().url());
      if (!pathname.startsWith('/api/v1/')) return fulfillShell(route, pathname);
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
      return fulfillShell(route, pathname);
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

async function verifyNotePolling(browser) {
  const page = await browser.newPage();
  let releaseNote;
  const delayedNote = new Promise(resolve => { releaseNote = resolve; });
  let noteReads = 0;
  let sessionReads = 0;
  try {
    await page.route('**/*', async route => {
      const { pathname } = new URL(route.request().url());
      if (pathname === session) {
        sessionReads++;
        const message = { id: 7, role: 'assistant', status: 'completed', sources: [],
          content: '会话快照 ' + sessionReads };
        return route.fulfill({ json: { id: 1, title: '合成会话', status: 'active', context: { sources: [] }, messages: [message] } });
      }
      if (pathname === session + '/note-jobs') {
        const first = ++noteReads === 1;
        if (!first) await delayedNote;
        return route.fulfill({ json: { items: [{ id: 9, status: first ? 'RUNNING' : 'SUCCEEDED',
          note_id: 5, version_id: 1, message_ids: [7], conflict: false }] } });
      }
      return fulfillShell(route, pathname);
    });
    await page.goto(web + '/study/chat/1');
    await page.getByText('整理中…', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '整理中…', exact: true }).isDisabled(), true);
    await page.getByText('会话快照 3', { exact: true }).waitFor({ timeout: 6000 });
    assert.equal(noteReads, 2, 'the next session poll must reuse the pending note request');
    releaseNote();
    await page.getByRole('link', { name: '打开笔记', exact: true }).waitFor({ timeout: 3000 });
    assert.ok(await page.getByRole('button', { name: '整理这条回复', exact: true }).isEnabled());
    console.log(JSON.stringify({ scenario: 'slow-note-poll', sessionReads, noteReads }));
  } finally {
    releaseNote();
    await page.close();
  }
}

async function verifyStop(browser, button, useStreamAttempt) {
  const page = await browser.newPage();
  let stopped = false;
  const attempt = useStreamAttempt ? 1 : 2;
  let releaseNotes;
  const pendingNotes = new Promise(resolve => { releaseNotes = resolve; });
  try {
    await page.route('**/*', async route => {
      const { pathname } = new URL(route.request().url());
      if (pathname === session) {
        const message = { id: 7, role: 'assistant', status: stopped ? 'interrupted' : 'generating',
          sources: [], attempt, content: '' };
        return route.fulfill({ json: { id: 1, title: '合成会话', status: 'active', context: { sources: [] }, messages: [message] } });
      }
      if (pathname === session + '/note-jobs') await pendingNotes;
      if (pathname === session + '/messages/7/stream') {
        const event = { id: 7, attempt: 2, status: 'generating', stage: 'generating',
          model: null, error: null, content: '第二次生成的快照' };
        return route.fulfill({ contentType: 'text/event-stream', body: 'data: ' + JSON.stringify(event) + '\n\n' });
      }
      if (pathname === session + '/messages/7/interrupt') {
        stopped = true;
        return route.fulfill({ json: { id: 7, attempt: 2, status: 'interrupted' } });
      }
      return fulfillShell(route, pathname);
    });
    await page.goto(web + '/study/chat/1');
    if (useStreamAttempt) await page.getByText('第二次生成的快照', { exact: true }).waitFor({ timeout: 3000 });
    const requestPromise = page.waitForRequest(request =>
      new URL(request.url()).pathname === session + '/messages/7/interrupt');
    await page.getByRole('button', { name: button, exact: true }).click();
    const request = await requestPromise;
    assert.equal(request.method(), 'POST');
    assert.equal(new URL(request.url()).search, '?expected_attempt=2');
    assert.deepEqual(request.postDataJSON(), {});
    await page.getByRole('button', { name: '重试回复', exact: true }).waitFor();
    console.log(JSON.stringify({ scenario: 'stop', button, attemptSource: useStreamAttempt ? 'stream' : 'session' }));
  } finally {
    releaseNotes();
    await page.close();
  }
}

async function main() {
  const browser = await chromium.launch({ channel: process.env.STUDY_BROWSER_CHANNEL || 'msedge' });
  try {
    for (const scenario of ['reconnect', 'polling', 'refresh-error']) await verify(browser, scenario);
    await verifyNotePolling(browser);
    await verifyStop(browser, '中断回复', false);
    await verifyStop(browser, '停止生成', true);
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
