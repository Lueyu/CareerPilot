// 生成部署用 dist 目录：拷贝 index.html 与 data.js，并给 data.js 加上内容指纹。
// 题库更新后 data.js 内容会变，URL 随之变化，浏览器不会误用旧缓存。
import { cp, rm, mkdir, readFile, writeFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import crypto from 'node:crypto';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const DIST = path.join(__dirname, 'dist');

const SITE_FILES = ['index.html', 'data.js', 'manifest.webmanifest', 'sw.js'];

await rm(DIST, { recursive: true, force: true });
await mkdir(DIST, { recursive: true });
await mkdir(path.join(DIST, 'icons'), { recursive: true });

for (const file of SITE_FILES) {
  await cp(path.join(ROOT, file), path.join(DIST, file));
}
await cp(path.join(ROOT, 'icons'), path.join(DIST, 'icons'), { recursive: true });

const dataBuf = await readFile(path.join(DIST, 'data.js'));
const hash = crypto.createHash('sha256').update(dataBuf).digest('hex').slice(0, 10);

let html = await readFile(path.join(DIST, 'index.html'), 'utf8');
const before = html;
html = html.replace(/src="data\.js"/g, `src="data.js?v=${hash}"`);
if (html === before) {
  console.error('[prepare] 未在 index.html 中找到 src="data.js"，指纹未生效');
  process.exit(1);
}
await writeFile(path.join(DIST, 'index.html'), html, 'utf8');

const size = (await stat(path.join(DIST, 'data.js'))).size;
console.log(`[prepare] dist 已生成：index.html + data.js（${(size / 1024).toFixed(0)} KB，v=${hash}）`);
