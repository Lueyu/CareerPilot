const shellPath = '/podcast-offline.html';
const shellCache = 'careerpilot-podcast-shell-v1';
const assetPath = /^\/(?:assets|static-assets)\/[A-Za-z0-9_.-]+\.(?:js|css)$/;

self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

let preparation;
async function prepareShell() {
  const shell = await fetch(shellPath, { cache: 'reload', credentials: 'omit', redirect: 'error' });
  const html = await shell.clone().text();
  if (!shell.ok || !shell.headers.get('content-type')?.includes('text/html') ||
      !html.includes('data-podcast-offline-entry')) throw new Error('离线播放器入口不可用');
  const assets = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map(match => match[1]).filter(path => assetPath.test(path));
  if (!assets.some(path => path.endsWith('.js'))) throw new Error('需要构建版才能保存离线播放器');
  const cache = await caches.open(shellCache);
  await Promise.all([...new Set(assets)].map(async path => {
    const asset = await fetch(path, { credentials: 'omit', redirect: 'error', cache: 'reload' });
    const type = asset.headers.get('content-type') ?? '';
    if (!asset.ok || (path.endsWith('.js') ? !type.includes('javascript') : !type.includes('text/css'))) {
      throw new Error('离线播放器文件缺失，请重试');
    }
    await cache.put(path, asset);
  }));
  await cache.put(shellPath, shell);
}

self.addEventListener('message', event => {
  const port = event.ports[0];
  if (!port || event.data?.type !== 'PREPARE_PODCAST_SHELL' ||
      !event.source?.url || new URL(event.source.url).origin !== self.location.origin) return;
  preparation ??= prepareShell().finally(() => { preparation = undefined; });
  event.waitUntil(preparation.then(
    () => port.postMessage({ ready: true }),
    error => port.postMessage({ ready: false, error: error.message }),
  ));
});

self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (request.mode === 'navigate' && url.pathname === shellPath) {
    event.respondWith(caches.open(shellCache).then(cache =>
      cache.match(shellPath).then(response => response ?? fetch(request)),
    ));
  } else if (assetPath.test(url.pathname)) {
    event.respondWith(caches.open(shellCache).then(cache =>
      cache.match(url.pathname).then(response => response ?? fetch(request)),
    ));
  }
});
