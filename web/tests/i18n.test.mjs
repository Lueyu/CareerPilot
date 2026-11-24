import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { test } from 'node:test';
import { getLocale, setLocale, t } from '../src/i18n.ts';

const catalogRoot = new URL('../src/locales/', import.meta.url);
const placeholders = text => [...new Set(Array.from(text.matchAll(/\{(\w+)\}/g), match => match[1]))].sort();

test('both locales cover every catalog key and interpolation argument', async () => {
  const files = locale => readdir(new URL(locale + '/', catalogRoot));
  const enFiles = (await files('en')).sort();
  assert.deepEqual((await files('zh-CN')).sort(), enFiles);
  for (const file of enFiles) {
    const read = async locale => JSON.parse(await readFile(new URL(locale + '/' + file, catalogRoot), 'utf8'));
    const [en, zh] = await Promise.all([read('en'), read('zh-CN')]);
    assert.deepEqual(Object.keys(en).sort(), Object.keys(zh).sort(), file);
    for (const [key, value] of Object.entries(en)) {
      const label = file + '.' + key;
      assert.ok(value.trim() && zh[key].trim(), label);
      assert.deepEqual(placeholders(value), placeholders(zh[key]), label);
    }
  }
});

test('switching languages updates the selected copy immediately', () => {
  const previous = getLocale();
  try {
    setLocale('zh-CN');
    const chinese = t('core.description');
    setLocale('en');
    assert.match(t('core.description'), /workspace|question bank/i);
    assert.notEqual(t('core.description'), chinese);
    setLocale('zh-CN');
    assert.equal(t('core.description'), chinese);
  } finally {
    setLocale(previous);
  }
});

