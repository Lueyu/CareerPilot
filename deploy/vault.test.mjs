import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { encryptEnvelope, decryptEnvelope, encryptFile, keygen, selftest, KEY_FILE } from './vault.mjs';

test('加密→WebCrypto 解密 roundtrip（浏览器兼容契约）', async () => {
  const key = 'a'.repeat(64);
  const payload = { version: 1, mastery: { ctx: { score: 47 } }, wounds: [{ d: 'x' }] };
  const back = await decryptEnvelope(key, encryptEnvelope(key, payload));
  assert.deepEqual(back, payload);
});

test('错口令必须被拒', async () => {
  const key = 'a'.repeat(64);
  const env = encryptEnvelope(key, { a: 1 });
  await assert.rejects(() => decryptEnvelope('0'.repeat(64), env));
});

test('被篡改的密文必须被拒', async () => {
  const key = 'a'.repeat(64);
  const env = encryptEnvelope(key, { a: 1 });
  const bad = { ...env, ct: env.ct.slice(0, -12) + 'AAAAAAAAAAAA' };
  await assert.rejects(() => decryptEnvelope(key, bad));
});

test('缺字段 envelope 必须被拒', async () => {
  const key = 'a'.repeat(64);
  await assert.rejects(() => decryptEnvelope(key, { v: 1 }));
});

test('encryptFile 写出 window.VAULT 并给同目录 index.html 加指纹', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'vault-test-'));
  const keyFile = path.join(dir, 'k');
  const payload = path.join(dir, 'payload.json');
  const out = path.join(dir, 'vault.js');
  writeFileSync(payload, JSON.stringify({ hello: '世界' }));
  keygen(keyFile, true);
  encryptFile(payload, out, keyFile);
  const txt = readFileSync(out, 'utf8');
  assert.match(txt, /^window\.VAULT = \{.*\};\n$/);
  writeFileSync(path.join(dir, 'index.html'), '<script src="vault.js"></script>');
  encryptFile(payload, out, keyFile);
  const html = readFileSync(path.join(dir, 'index.html'), 'utf8');
  assert.match(html, /src="vault\.js\?v=[0-9a-f]{10}"/);
});

test('keygen 写 64 位 hex 且默认拒绝覆盖', () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'vault-keygen-'));
  const keyFile = path.join(dir, 'k');
  keygen(keyFile, false);
  assert.match(readFileSync(keyFile, 'utf8').trim(), /^[0-9a-f]{64}$/);
  assert.throws(() => keygen(keyFile, false));
  keygen(keyFile, true);
});

test('selftest 全绿', async () => { assert.equal(await selftest(), true); });
