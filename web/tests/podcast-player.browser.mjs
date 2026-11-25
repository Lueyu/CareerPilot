import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { mkdir, mkdtemp, readFile } from 'node:fs/promises';
import { extname, join } from 'node:path';
import { setTimeout as pause } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { build, loadConfigFromFile } from 'vite';

const { chromium } = createRequire(import.meta.url)('playwright');
assert.ok(process.env.PODCAST_MP3_FIXTURE, 'Set PODCAST_MP3_FIXTURE to a local MP3 at least 10 seconds long');
const mp3 = await readFile(process.env.PODCAST_MP3_FIXTURE);
const web = fileURLToPath(new URL('../', import.meta.url));
const artifacts = fileURLToPath(new URL('../../.agent-runs/podcast-player-browser/', import.meta.url));
await mkdir(artifacts, { recursive: true });
const outDir = await mkdtemp(join(artifacts, 'built-'));
process.env.VITE_API_BASE = '/api/v1';
process.env.VITE_WORKSPACE = 'personal';
process.env.VITE_AUTH_MODE = 'local';
const { config } = await loadConfigFromFile({ command: 'build', mode: 'production' }, join(web, 'vite.config.ts'), web);
await build({
  ...config, configFile: false, root: web, logLevel: 'error',
  build: {
    ...config.build, outDir, emptyOutDir: true,
    rollupOptions: {
      ...config.build.rollupOptions,
      input: [join(web, 'tests/podcast-player.fixture.html'), join(web, 'podcast-offline.html')],
    },
  },
});

const episode = (id, topic, familiarity = 'unmarked') => ({
  id, question_id: id, question_code: 'CP-' + id, title: '测试第 ' + id + ' 集', topic, status: 'ready',
  part: 1, duration_seconds: 120, position_seconds: 0, familiarity, error: null,
});
const library = {
  episodes: [episode(1, 'Agent', 'unfamiliar'), episode(2, 'Agent'), episode(3, 'RAG', 'unfamiliar')],
  playlists: [
    { id: 1, name: '通勤歌单', episode_ids: [1, 2, 3] },
    { id: 2, name: 'Agent', episode_ids: [1, 3] },
    { id: 3, name: '通勤歌单', episode_ids: [1, 3] },
    { id: 4, name: '不熟悉的随机听', episode_ids: [2] },
  ],
};
const data = new Map();
const updates = [];
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css' };
let registeredId = 42;
let unavailableAudio;
const server = createServer(async (request, response) => {
  const pathname = new URL(request.url, 'http://127.0.0.1').pathname;
  const send = (code, value) => {
    response.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    response.end(JSON.stringify(value));
  };
  if (pathname === '/api/v1/auth/me') return send(200, { username: 'account-' + registeredId, workspace_id: registeredId });
  const api = /^\/api\/v1(\/native)?\/podcasts(?:\/episodes\/(\d+)(?:\/(audio|script))?)?$/.exec(pathname);
  if (api) {
    const [, registered, id, resource] = api;
    const scope = registered ? 'registered:' + request.headers['x-expected-workspace'] : request.headers['x-workspace'];
    if (registered && scope !== 'registered:' + registeredId) {
      return send(409, { detail: { code: 'workspace_changed' } });
    }
    if (!data.has(scope)) data.set(scope, structuredClone(library));
    const store = data.get(scope);
    if (!id) return send(200, store);
    const item = store.episodes.find(candidate => candidate.id === Number(id));
    if (!item) return send(404, { detail: '节目已删除' });
    if (request.method === 'DELETE') {
      store.episodes = store.episodes.filter(candidate => candidate.id !== item.id);
      for (const list of store.playlists) list.episode_ids = list.episode_ids.filter(id => id !== item.id);
      return send(200, {});
    }
    if (resource === 'script') return send(200, { title: '讲解', script: '查看第 ' + id + ' 集的已保存讲稿。' });
    if (resource === 'audio') {
      if (Number(id) === unavailableAudio) return send(503, { detail: '音频暂不可用' });
      response.writeHead(200, { 'Content-Type': 'audio/mpeg', 'Cache-Control': 'no-store' });
      return response.end(mp3);
    }
    if (request.method === 'PATCH') {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const patch = JSON.parse(Buffer.concat(chunks).toString());
      updates.push({ scope, id: Number(id), patch });
      Object.assign(item, patch);
      return send(200, item);
    }
  }
  if (!/^\/(?:assets\/[\w.-]+|tests\/podcast-player\.fixture\.html|podcast-offline\.html|podcast-sw\.js)$/.test(pathname)) {
    return send(404, {});
  }
  try {
    const content = await readFile(join(outDir, pathname.slice(1)));
    response.writeHead(200, { 'Content-Type': mime[extname(pathname)] });
    response.end(content);
  } catch { send(404, {}); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = 'http://127.0.0.1:' + server.address().port;
const path = origin + '/tests/podcast-player.fixture.html';
const offlinePath = origin + '/podcast-offline.html';

const browser = await chromium.launch(process.env.PODCAST_BROWSER_CHANNEL ? { channel: process.env.PODCAST_BROWSER_CHANNEL } : {});
const context = await browser.newContext({ viewport: { width: 390, height: 820 }, hasTouch: true, acceptDownloads: true });
let page = await context.newPage();

async function startFirst(tab) {
  await tab.getByRole('button', { name: /^测试第 1 集/ }).first().click();
  const dialog = tab.getByRole('dialog', { name: '播客播放器' });
  await dialog.getByRole('button', { name: '暂停', exact: true }).waitFor();
  return dialog;
}

async function savePack(tab, dialog) {
  await dialog.getByRole('button', { name: '离线和导出' }).click();
  await dialog.getByRole('button', { name: '保存当前范围到此设备' }).click();
  await dialog.getByText('已存到这台设备', { exact: true }).waitFor();
  assert.equal(await dialog.getByRole('link', { name: '打开离线播放器' }).getAttribute('href'), '/podcast-offline.html');
  const cached = await tab.evaluate(async () => {
    const cache = await caches.open('careerpilot-podcast-shell-v1');
    return (await cache.keys()).map(request => new URL(request.url).pathname);
  });
  assert.ok(cached.includes('/podcast-offline.html'));
  assert.ok(cached.some(url => /^\/assets\/podcast-offline.+\.js$/.test(url)));
  assert.ok(cached.every(url => url === '/podcast-offline.html' || url.startsWith('/assets/')));
}

async function checkDeletedPlayback(tab, dialog) {
  // Start from a paused restore so switching tracks doesn't sync a playback checkpoint.
  await tab.reload();
  await tab.getByRole('button', { name: '打开播放器，测试第 1 集' }).click();
  const scope = dialog.getByRole('combobox', { name: '播放范围' });
  await scope.selectOption('playlist:number:1');

  // An older library snapshot may not include a track the listener just selected.
  const release = Promise.withResolvers();
  await tab.route('**/api/v1/podcasts', async route => {
    await release.promise;
    await route.fulfill({ json: {
      episodes: library.episodes.filter(item => item.id !== 2),
      playlists: library.playlists,
    } });
  }, { times: 1 });
  try {
    await Promise.all([
      tab.waitForRequest('**/api/v1/podcasts'),
      tab.evaluate(() => window.dispatchEvent(new Event('careerpilot-podcast-library-changed'))),
    ]);
    await dialog.getByRole('button', { name: '下一集', exact: true }).click();
    await dialog.getByRole('heading', { name: '测试第 2 集' }).waitFor();
    await dialog.getByRole('button', { name: '播放', exact: true }).click();
    await dialog.getByRole('button', { name: '暂停', exact: true }).waitFor();
    data.get('personal').playlists.push({ id: 99, name: '最新的歌单', episode_ids: [] });
  } finally {
    release.resolve();
  }
  await scope.locator('option[value="playlist:number:99"]').waitFor({ state: 'attached' });
  await dialog.getByRole('button', { name: '暂停', exact: true }).waitFor();
  await dialog.getByRole('button', { name: '逐字稿', exact: true }).click();
  await dialog.getByText('查看第 2 集的已保存讲稿。').waitFor();
  await tab.waitForFunction(() => navigator.mediaSession.metadata?.title === '测试第 2 集');

  const status = await tab.evaluate(async () => {
    const response = await fetch('/api/v1/podcasts/episodes/2', {
      method: 'DELETE', headers: { 'X-Workspace': 'personal' },
    });
    window.dispatchEvent(new CustomEvent('careerpilot-podcast-library-changed', { detail: { source: 'library' } }));
    return response.status;
  });
  assert.equal(status, 200);
  await tab.waitForFunction(() => {
    const audio = document.querySelector('audio');
    return audio.paused && !audio.hasAttribute('src') && !navigator.mediaSession.metadata &&
      navigator.mediaSession.playbackState === 'none' && !document.querySelector('.podcast-mini');
  }, undefined, { timeout: 5000 });
  assert.equal(await tab.getByText('查看第 2 集的已保存讲稿。').count(), 0);
  const resume = await tab.evaluate(async () => {
    const journal = Object.keys(localStorage).filter(key => key.startsWith('careerpilot-podcast-resume:'))
      .map(key => JSON.parse(localStorage.getItem(key)).id);
    const db = await new Promise((resolve, reject) => {
      const request = indexedDB.open('careerpilot-podcasts');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    try {
      const records = await new Promise((resolve, reject) => {
        const request = db.transaction('workspaces').objectStore('workspaces').getAll();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      return [...journal, ...records.map(record => record.resume?.id)];
    } finally { db.close(); }
  });
  assert.ok(!resume.includes(2), 'deleted episode still has a resume checkpoint');
}

try {
  await page.goto(path);
  await page.getByRole('button', { name: '从主题播放' }).click();
  let dialog = page.getByRole('dialog', { name: '播客播放器' });
  const scope = dialog.getByRole('combobox', { name: '播放范围' });
  assert.equal(await scope.inputValue(), 'topic:Agent');
  await dialog.getByRole('button', { name: '下一集', exact: true }).click();
  await dialog.getByRole('heading', { name: '测试第 2 集' }).waitFor();
  assert.equal(await dialog.getByRole('button', { name: '下一集', exact: true }).isDisabled(), true);
  await scope.selectOption('playlist:number:2');
  await dialog.getByRole('heading', { name: '测试第 1 集' }).waitFor();
  await dialog.getByRole('button', { name: '下一集', exact: true }).click();
  await dialog.getByRole('heading', { name: '测试第 3 集' }).waitFor();
  await dialog.getByRole('button', { name: '收起播放器' }).click();

  await page.evaluate(() => { Math.random = () => 0; });
  await page.getByRole('button', { name: '不熟悉的随机听', exact: true }).click();
  assert.equal(await scope.inputValue(), 'unfamiliar');
  assert.equal(await dialog.getByRole('button', { name: '随机', exact: true }).getAttribute('aria-pressed'), 'true');
  await dialog.getByRole('heading', { name: '测试第 1 集' }).waitFor();
  await dialog.getByRole('button', { name: '暂停', exact: true }).waitFor();
  await page.locator('audio').evaluate(media => { media.currentTime = media.duration - 0.15; });
  await dialog.getByRole('heading', { name: '测试第 3 集' }).waitFor();
  assert.equal(await dialog.getByRole('button', { name: '下一集', exact: true }).isDisabled(), true);
  await dialog.getByRole('button', { name: '收起播放器' }).click();

  await page.getByRole('button', { name: '从制作批次播放' }).click();
  assert.equal(await scope.inputValue(), 'selection:production');
  await dialog.getByRole('heading', { name: '测试第 3 集' }).waitFor();
  await dialog.getByRole('button', { name: '下一集', exact: true }).click();
  await dialog.getByRole('heading', { name: '测试第 1 集' }).waitFor();
  assert.equal(await dialog.getByRole('button', { name: '下一集', exact: true }).isDisabled(), true);
  await dialog.getByRole('button', { name: '收起播放器' }).click();
  const search = page.getByRole('searchbox', { name: '搜索节目' });
  await search.fill('RAG');
  await page.locator('.podlib-episode-main').filter({ hasText: '测试第 3 集' }).click();
  assert.equal(await scope.inputValue(), 'selection:search');
  assert.equal(await dialog.getByRole('button', { name: '下一集', exact: true }).isDisabled(), true);
  await dialog.getByRole('button', { name: '收起播放器' }).click();

  dialog = await startFirst(page);
  assert.equal(await scope.inputValue(), 'playlist:number:1');
  assert.notEqual(await scope.locator('option[value="playlist:number:1"]').textContent(),
    await scope.locator('option[value="playlist:number:3"]').textContent());
  const player = await page.locator('audio').elementHandle();
  await page.waitForFunction(() => navigator.mediaSession.metadata?.title === '测试第 1 集');
  await dialog.getByRole('button', { name: '逐字稿', exact: true }).click();
  await dialog.getByText('查看第 1 集的已保存讲稿。').waitFor();
  await dialog.getByRole('button', { name: '收起播放器' }).click();
  assert.equal(await page.locator('audio').evaluate(element => element.paused), false);
  await page.getByRole('button', { name: '打开播放器，测试第 1 集' }).click();
  const disc = await dialog.locator('.podcast-cover').boundingBox();
  const cdp = await context.newCDPSession(page);
  const y = Math.round(disc.y + disc.height * .6);
  const xFrom = Math.round(disc.x + disc.width * .8);
  const xTo = Math.round(disc.x + disc.width * .2);
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: xFrom, y }] });
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: xTo, y }] });
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await cdp.detach();
  await dialog.getByRole('heading', { name: '测试第 2 集' }).waitFor();
  assert.equal(await page.locator('audio').evaluate((element, same) => element === same, player), true);
  await dialog.getByRole('button', { name: '上一集，测试第 1 集' }).click();
  await dialog.getByRole('heading', { name: '测试第 1 集' }).waitFor();
  await dialog.getByRole('combobox', { name: '播放范围' }).selectOption('unfamiliar');
  assert.equal(await dialog.getByRole('button', { name: '随机', exact: true }).getAttribute('aria-pressed'), 'true');
  await dialog.getByRole('button', { name: '单集循环' }).click();
  await dialog.getByRole('button', { name: '暂停', exact: true }).waitFor();
  await page.locator('audio').evaluate(media => { media.currentTime = media.duration - 0.15; });
  await page.waitForFunction(() => {
    const audio = document.querySelector('audio');
    return audio.currentTime < 2 && !audio.paused && navigator.mediaSession.metadata?.title === '测试第 1 集';
  });
  await dialog.getByRole('button', { name: '顺序', exact: true }).click();
  await dialog.getByRole('combobox', { name: '播放范围' }).selectOption('playlist:number:1');

  unavailableAudio = 2;
  await dialog.getByRole('button', { name: '离线和导出' }).click();
  await dialog.getByRole('button', { name: '保存当前范围到此设备' }).click();
  await dialog.getByText('音频暂不可用', { exact: false }).waitFor();
  assert.equal(await dialog.getByRole('link', { name: '打开离线播放器' }).count(), 0);
  unavailableAudio = undefined;
  await dialog.getByRole('button', { name: '离线和导出' }).click();
  await savePack(page, dialog);
  await scope.selectOption('playlist:number:3');
  await dialog.getByRole('button', { name: '离线和导出' }).click();
  await savePack(page, dialog);
  const downloadPromise = page.waitForEvent('download');
  await dialog.getByRole('button', { name: '导出这集 MP3' }).click();
  const download = await downloadPromise;
  assert.equal(download.suggestedFilename(), '测试第 1 集.mp3');
  assert.deepEqual(await readFile(await download.path()), mp3);

  await checkDeletedPlayback(page, dialog);

  await page.close();
  await context.setOffline(true);
  page = await context.newPage();
  await page.goto(offlinePath);
  const savedPacks = page.locator('main section').filter({ has: page.getByRole('heading', { name: '通勤歌单' }) });
  await savedPacks.nth(1).waitFor();
  await savedPacks.first().getByRole('button', { name: /^测试第 2 集/ }).click();
  dialog = page.getByRole('dialog', { name: '播客播放器' });
  await dialog.getByRole('button', { name: '暂停', exact: true }).waitFor();
  await dialog.getByRole('button', { name: '逐字稿', exact: true }).click();
  await dialog.getByText('查看第 2 集的已保存讲稿。').waitFor();
  await dialog.getByRole('button', { name: '收起播放器' }).click();
  await savedPacks.nth(1).getByRole('button', { name: /^测试第 1 集/ }).click();
  dialog = page.getByRole('dialog', { name: '播客播放器' });
  await dialog.getByRole('button', { name: '下一集，测试第 3 集' }).waitFor();
  await dialog.getByRole('button', { name: '收起播放器' }).click();
  dialog = await startFirst(page);
  await dialog.getByRole('button', { name: '下一集，测试第 2 集' }).waitFor();
  await dialog.getByRole('button', { name: '熟悉', exact: true }).click();
  await dialog.getByRole('button', { name: '逐字稿', exact: true }).click();
  await dialog.getByText('查看第 1 集的已保存讲稿。').waitFor();
  await dialog.getByRole('slider', { name: '播放进度' }).press('ArrowRight');
  const scrubber = await dialog.getByRole('slider', { name: '播放进度' }).boundingBox();
  await page.mouse.click(scrubber.x + scrubber.width / 3, scrubber.y + scrubber.height / 2);
  await page.waitForFunction(() => document.querySelector('audio').currentTime > 5);
  await dialog.getByRole('button', { name: '暂停', exact: true }).click();
  await page.close();
  page = await context.newPage();
  await page.goto(offlinePath);
  await page.getByRole('button', { name: '打开播放器，测试第 1 集' }).click();
  dialog = page.getByRole('dialog', { name: '播客播放器' });
  assert.equal(await dialog.getByRole('button', { name: '熟悉', exact: true }).getAttribute('aria-pressed'), 'true');
  assert.ok(Number(await dialog.getByRole('slider', { name: '播放进度' }).inputValue()) > 5);
  assert.equal(await page.locator('audio').evaluate(audio => audio.paused), true);
  await page.screenshot({ path: join(artifacts, 'offline-resume.png') });
  assert.equal(updates.some(item => item.patch.familiarity === 'familiar'), false);

  await context.setOffline(false);
  const reopened = await context.newPage();
  await reopened.goto(path);
  for (let attempt = 0; attempt < 100; attempt++) {
    const [first] = data.get('personal').episodes;
    if (first.familiarity === 'familiar' && first.position_seconds > 5) break;
    await pause(100);
  }
  assert.ok(updates.some(item => item.scope === 'personal' && item.id === 1 && item.patch.familiarity === 'familiar'));
  assert.ok(data.get('personal').episodes[0].position_seconds > 5);
  await reopened.evaluate(() => localStorage.setItem('cp-workspace', 'other'));
  await page.getByText('当前工作区在这台设备上没有可打开的离线歌单。', { exact: false }).waitFor();

  await reopened.goto(path + '?registered=42');
  dialog = await startFirst(reopened);
  await savePack(reopened, dialog);
  await page.goto(offlinePath);
  await page.getByRole('heading', { name: '通勤歌单' }).waitFor();
  await context.setOffline(true);
  dialog = await startFirst(page);
  await dialog.getByRole('button', { name: '熟悉', exact: true }).click();
  const otherTab = await context.newPage();
  await context.setOffline(false);
  registeredId = 43;
  await otherTab.goto(path + '?registered=43');
  await otherTab.evaluate(() => localStorage.setItem('careerpilot-public-session-change', 'next-account'));
  await page.getByText('当前工作区在这台设备上没有可打开的离线歌单。', { exact: false }).waitFor();
  await context.setOffline(true);
  await page.reload();
  await page.getByText('当前工作区在这台设备上没有可打开的离线歌单。', { exact: false }).waitFor();
  assert.ok(!updates.some(item => item.scope === 'registered:43' && item.patch.familiarity === 'familiar'));
  console.log('Passed: scoped playback, swipe, loop, transcript, stale refresh, active deletion, saved copies offline, atomic save, MP3, resume, sync, and workspace/account isolation.');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
