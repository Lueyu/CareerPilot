import assert from 'node:assert/strict';
import test from 'node:test';
import { applyScrollbarGutter } from '../src/lib/scrollbarGutter.ts';

function fakeShell() {
  const values = new Map();
  return {
    values,
    style: {
      setProperty: (key, value) => values.set(key, value),
      removeProperty: key => values.delete(key),
    },
  };
}

test('a space-taking scrollbar gutter is measured and published on the shell', () => {
  const shell = fakeShell();
  const area = { offsetWidth: 572, clientWidth: 557 };
  assert.equal(applyScrollbarGutter(shell, area), 15);
  assert.equal(shell.values.get('--cp-scrollbar-gutter'), '15px');
});

test('overlay scrollbars publish 0px and a rounding inversion cannot go negative', () => {
  const shell = fakeShell();
  assert.equal(applyScrollbarGutter(shell, { offsetWidth: 300, clientWidth: 300 }), 0);
  assert.equal(shell.values.get('--cp-scrollbar-gutter'), '0px');
  assert.equal(applyScrollbarGutter(shell, { offsetWidth: 299, clientWidth: 300 }), 0);
  assert.equal(shell.values.get('--cp-scrollbar-gutter'), '0px');
});

test('missing refs are a no-op', () => {
  const shell = fakeShell();
  assert.equal(applyScrollbarGutter(null, { offsetWidth: 572, clientWidth: 557 }), 0);
  assert.equal(applyScrollbarGutter(shell, null), 0);
  assert.equal(shell.values.size, 0);
});
