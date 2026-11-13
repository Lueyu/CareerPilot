#!/usr/bin/env node
// deploy/vault.mjs —— 作战室加密仪表盘(vault.js)生成器
// 用法:
//   node vault.mjs keygen [--force]                      # 生成 32 字节口令 → personal/.vault-key
//   node vault.mjs encrypt <payload.json> [--out <path>]  # 加密 → 默认 deploy/dist/vault.js(同目录 index.html 自动加指纹)
//   node vault.mjs --selftest                             # 内置回归(纯内存,不动真实文件)
// 格式契约(浏览器 #/vault 与本文件必须一致):
//   PBKDF2-SHA256, 100000 iter, 32B key; AES-256-GCM, 12B iv; ct = 密文||16B tag; 全部 base64
import { randomBytes, pbkdf2Sync, createCipheriv, createHash } from 'node:crypto';
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
export const KEY_FILE = path.join(ROOT, 'personal', '.vault-key');
const DEFAULT_OUT = path.join(__dirname, 'dist', 'vault.js');
const ITER = 100000;

export function encryptEnvelope(key, payloadObj) {
  const salt = randomBytes(16);
  const iv = randomBytes(12);
  const dk = pbkdf2Sync(key, salt, ITER, 32, 'sha256');
  const c = createCipheriv('aes-256-gcm', dk, iv);
  const ct = Buffer.concat([c.update(JSON.stringify(payloadObj), 'utf8'), c.final(), c.getAuthTag()]);
  return { v: 1, kdf: 'pbkdf2-sha256', iter: ITER, salt: salt.toString('base64'), iv: iv.toString('base64'), ct: ct.toString('base64'), builtAt: new Date().toISOString() };
}

export async function decryptEnvelope(key, env) {
  const km = await crypto.subtle.importKey('raw', new TextEncoder().encode(key), { name: 'PBKDF2' }, false, ['deriveKey']);
  const dk = await crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt: b64u(env.salt), iterations: env.iter, hash: 'SHA-256' },
    km, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
  const pt = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: b64u(env.iv) }, dk, b64u(env.ct));
  return JSON.parse(new TextDecoder().decode(pt));
}
function b64u(b64) { return new Uint8Array(Buffer.from(b64, 'base64')); }

export function keygen(keyFile = KEY_FILE, force = false) {
  if (existsSync(keyFile) && !force) throw new Error('口令已存在:' + keyFile + '(重生成加 --force;旧密文将全部不可解,必须重新加密发布)');
  mkdirSync(path.dirname(keyFile), { recursive: true });
  writeFileSync(keyFile, randomBytes(32).toString('hex') + '\n', { encoding: 'utf8', mode: 0o600 });
  return keyFile;
}

export function encryptFile(payloadPath, outPath, keyFile = KEY_FILE) {
  const key = readFileSync(keyFile, 'utf8').trim();
  const payload = JSON.parse(readFileSync(payloadPath, 'utf8'));
  const env = encryptEnvelope(key, payload);
  mkdirSync(path.dirname(outPath), { recursive: true });
  writeFileSync(outPath, 'window.VAULT = ' + JSON.stringify(env) + ';\n');
  const idx = path.join(path.dirname(outPath), 'index.html');
  if (existsSync(idx)) {
    const hash = createHash('sha256').update(readFileSync(outPath)).digest('hex').slice(0, 10);
    const html = readFileSync(idx, 'utf8');
    const patched = html.replace(/src="vault\.js(\?v=[0-9a-f]{10})?"/g, `src="vault.js?v=${hash}"`);
    if (patched !== html) writeFileSync(idx, patched);
  }
  return env;
}

export async function selftest() {
  const key = randomBytes(32).toString('hex');
  const payload = { version: 1, mastery: { rag: { score: 42 } } };
  const env = encryptEnvelope(key, payload);
  const back = await decryptEnvelope(key, env);
  if (JSON.stringify(back) !== JSON.stringify(payload)) throw new Error('roundtrip 失败');
  let wrong = false; try { await decryptEnvelope('0'.repeat(64), env); } catch { wrong = true; }
  if (!wrong) throw new Error('错口令未被拒');
  let tam = false; try { await decryptEnvelope(key, { ...env, ct: env.ct.slice(0, -12) + 'AAAAAAAAAAAA' }); } catch { tam = true; }
  if (!tam) throw new Error('篡改密文未被拒');
  let bad = false; try { await decryptEnvelope(key, { v: 1 }); } catch { bad = true; }
  if (!bad) throw new Error('缺字段未被拒');
  return true;
}

function main() {
  const args = process.argv.slice(2);
  if (args[0] === '--selftest') {
    selftest().then(() => { console.log('[vault] selftest OK'); process.exit(0); },
      (e) => { console.error('[vault] selftest FAIL:', e.message); process.exit(1); });
    return;
  }
  if (args[0] === 'keygen') {
    const f = keygen(KEY_FILE, args.includes('--force'));
    console.log('[vault] 口令已生成:', f, '(chmod 600;丢失=历史密文不可解)');
    return;
  }
  if (args[0] === 'encrypt') {
    const payloadPath = args[1];
    if (!payloadPath || !existsSync(payloadPath)) {
      console.error('[vault] 用法: node vault.mjs encrypt <payload.json> [--out <path>]');
      process.exit(1);
    }
    const outIdx = args.indexOf('--out');
    const out = outIdx > -1 ? path.resolve(args[outIdx + 1]) : DEFAULT_OUT;
    const env = encryptFile(payloadPath, out);
    console.log('[vault] 已加密 →', out, '(ct ' + env.ct.length + 'B, builtAt ' + env.builtAt + ')');
    return;
  }
  console.error('用法: node vault.mjs keygen[--force] | encrypt <payload.json> [--out <path>] | --selftest');
  process.exit(1);
}
if (process.argv[1] === fileURLToPath(import.meta.url)) main();
