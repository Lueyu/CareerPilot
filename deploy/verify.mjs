// 端到端验证：TLS 证书 + 页面/数据/缓存头/404/HTTP 强制跳转，结果写入 deploy/verify-https.json
import tls from 'node:tls';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const ROOT_DOMAIN = process.env.ALI_ROOT_DOMAIN || 'agent-interview.icu';
const WWW_DOMAIN = `www.${ROOT_DOMAIN}`;

function probeCert(hostname) {
  return new Promise((resolve) => {
    const socket = tls.connect(
      { host: hostname, port: 443, servername: hostname, rejectUnauthorized: false },
      () => {
        const c = socket.getPeerCertificate();
        resolve({
          hostname,
          authorized: socket.authorized,
          authorizationError: socket.authorizationError?.code || null,
          subject: c.subject?.CN,
          issuer: c.issuer?.CN,
          validFrom: c.valid_from,
          validTo: c.valid_to,
          san: c.subjectaltname,
          protocol: socket.getProtocol(),
        });
        socket.end();
      }
    );
    socket.on('error', (e) => resolve({ hostname, error: e.code || e.message }));
  });
}

async function probeUrl(u) {
  try {
    const r = await fetch(u, { redirect: 'manual' });
    const buf = await r.arrayBuffer();
    return {
      url: u,
      status: r.status,
      location: r.headers.get('location'),
      contentType: r.headers.get('content-type'),
      cacheControl: r.headers.get('cache-control'),
      bytes: buf.byteLength,
    };
  } catch (e) {
    return { url: u, error: e.cause?.code || e.message };
  }
}

// 从 dist/index.html 里取实际使用的带指纹 data.js 地址
async function fingerprintedDataUrl() {
  try {
    const html = await readFile(path.join(__dirname, 'dist', 'index.html'), 'utf8');
    const m = html.match(/src="(data\.js\?v=[0-9a-f]+)"/);
    return m ? `https://${ROOT_DOMAIN}/${m[1]}` : null;
  } catch {
    return null;
  }
}

const result = { certificates: [], urls: [] };
for (const h of [ROOT_DOMAIN, WWW_DOMAIN]) {
  result.certificates.push(await probeCert(h));
}

const urls = [
  `https://${ROOT_DOMAIN}/`,
  `https://${WWW_DOMAIN}/`,
  `https://${ROOT_DOMAIN}/data.js`,
  await fingerprintedDataUrl(),
  `https://${ROOT_DOMAIN}/nonexistent-404-test`,
  `http://${ROOT_DOMAIN}/`,
  `http://${WWW_DOMAIN}/`,
].filter(Boolean);

for (const url of urls) {
  const u = await probeUrl(url);
  result.urls.push(u);
}

await writeFile(path.join(__dirname, 'verify-https.json'), JSON.stringify(result, null, 2));
console.log('written deploy/verify-https.json');

// 就地打印结论，避免只看 JSON 漏掉失败项
let ok = true;
for (const c of result.certificates) {
  if (!c.authorized) ok = false;
  console.log(`证书 ${c.hostname}: authorized=${c.authorized} issuer=${c.issuer} validTo=${c.validTo} san=${c.san}`);
}
for (const u of result.urls) {
  const expectHttp403 = u.url.startsWith('http://');
  const expect404 = u.url.includes('nonexistent-404-test');
  const pass = u.error ? false : expectHttp403 ? u.status === 403 : expect404 ? u.status === 404 : u.status === 200;
  if (!pass) ok = false;
  console.log(`${pass ? 'PASS' : 'FAIL'} ${u.url} -> ${u.status ?? u.error} ${u.contentType || ''} ${u.bytes ?? ''}B cache=${u.cacheControl || ''}`);
}
process.exit(ok ? 0 : 1);
