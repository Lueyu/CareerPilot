/* AI Agent 面试题库 · PWA Service Worker
   策略:导航请求 network-first(在线必拿最新指纹的 index.html,离线回退缓存);
   data.js(带指纹,immutable)/manifest/图标 cache-first。内容更新不需要改本文件。 */
var CACHE = 'ib-shell-v1';
var SHELL = [
  '/index.html',
  '/manifest.webmanifest',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/icon-180.png',
  '/icons/icon-maskable-512.png'
];

self.addEventListener('install', function(e){
  e.waitUntil(caches.open(CACHE).then(function(c){
    /* 单项失败不阻塞安装:图标缺失时站点仍可离线 */
    return Promise.all(SHELL.map(function(u){
      return c.add(u).catch(function(err){
        if(self.console) console.warn('sw precache miss:', u, err && err.message);
      });
    }));
  }).then(function(){ return self.skipWaiting(); }));
});

self.addEventListener('activate', function(e){
  e.waitUntil(caches.keys().then(function(keys){
    return Promise.all(keys.filter(function(k){ return k !== CACHE; })
      .map(function(k){ return caches.delete(k); }));
  }).then(function(){ return self.clients.claim(); }));
});

self.addEventListener('fetch', function(e){
  if(e.request.method !== 'GET') return;
  var url = new URL(e.request.url);
  if(url.origin !== location.origin) return;
  /* 页面导航:网络优先,离线回退缓存的 App Shell */
  if(e.request.mode === 'navigate'){
    e.respondWith(fetch(e.request).then(function(r){
      var cp = r.clone();
      caches.open(CACHE).then(function(c){ c.put('/index.html', cp); });
      return r;
    }).catch(function(){
      return caches.match('/index.html').then(function(m){
        return m || Response.error();
      });
    }));
    return;
  }
  /* 其余资源:缓存优先;data.js(任意指纹)与 PWA 资产命中网络后写入缓存 */
  e.respondWith(caches.match(e.request).then(function(hit){
    if(hit) return hit;
    return fetch(e.request).then(function(r){
      var keep = url.pathname === '/data.js'
        || SHELL.indexOf(url.pathname) !== -1
        || url.pathname.indexOf('/icons/') === 0;
      if(r.ok && keep){
        var cp = r.clone();
        caches.open(CACHE).then(function(c){ c.put(e.request, cp); });
      }
      return r;
    });
  }));
});
