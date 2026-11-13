import { test } from 'node:test';
import assert from 'node:assert/strict';
import { findLeaks } from './publish.mjs';

test('personal/ 路径命中泄漏', () => {
  assert.ok(findLeaks(['index.html', 'personal/vault-payload.json'], ''));
});
test('notes.json 命中', () => {
  assert.ok(findLeaks(['notes.json'], ''));
});
test('.vault-key 命中', () => {
  assert.ok(findLeaks(['personal/.vault-key'], ''));
});
test('私钥块命中', () => {
  assert.ok(findLeaks(['deploy/x.mjs'], '-----BEGIN RSA PRIVATE KEY-----'));
});
test('阿里 AK 命中', () => {
  assert.ok(findLeaks(['docs/a.md'], 'LTAI5tDeadBeef12345678'));
});
test('deploy/vault.mjs 不误伤', () => {
  assert.equal(findLeaks(['deploy/vault.mjs', 'content/questions/rag/001.html'], '正常内容'), null);
});
