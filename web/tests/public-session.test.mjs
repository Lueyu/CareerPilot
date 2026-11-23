import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';
import App from '../src/seed/App.tsx';
import { sessionChangeKey } from '../src/seed/api.ts';

const first = { username: 'first_tab', workspace_id: 11 };
const second = { username: 'second_tab', workspace_id: 22 };
const firstMemory = { id: 100, text: 'First account memory', revision: 1 };
const secondMemory = { id: 200, text: 'Second account memory', revision: 1 };

function invalidSession(status, code) {
  return Response.json({ detail: { code } }, { status });
}

function browser(t, initialUser, intercept = () => undefined) {
  const original = { window: globalThis.window, document: globalThis.document, fetch, FormData };
  const window = new EventTarget();
  window.location = { hash: '#memories' };
  const announcements = [];
  window.localStorage = { setItem: (...args) => announcements.push(args) };
  globalThis.window = window;
  globalThis.document = new EventTarget();
  globalThis.FormData = class {
    constructor(form) { this.form = form; }
    get(key) { return this.form[key]; }
  };

  let user = initialUser;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    const path = url.slice('/api/v1'.length);
    const method = options.method ?? 'GET';
    const expected = options.headers.get('X-Expected-Workspace');
    calls.push({ path, method, expected });
    if (path === '/auth/me') {
      assert.equal(expected, null);
      return user ? Response.json(user) : invalidSession(401, 'session_required');
    }
    if (path === '/auth/recover') {
      assert.equal(expected, null);
      return intercept({ path, method, options });
    }
    assert.ok(expected, 'workspace requests carry the tab’s expected account');
    if (!user) return invalidSession(401, 'session_required');
    if (expected !== String(user.workspace_id)) return invalidSession(409, 'workspace_changed');
    const response = intercept({ path, method, options });
    if (response) return response;
    assert.equal(path, '/memories');
    assert.equal(method, 'GET');
    return Response.json([user === first ? firstMemory : secondMemory]);
  };

  let view;
  t.after(async () => {
    await act(async () => view?.unmount());
    Object.assign(globalThis, original);
  });
  return {
    calls,
    announcements,
    setUser(next) { user = next; },
    focus() { window.dispatchEvent(new Event('focus')); },
    notify() { window.dispatchEvent(Object.assign(new Event('storage'), { key: sessionChangeKey })); },
    async render() {
      await act(async () => { view = create(createElement(App)); });
      return view.root;
    },
  };
}

function button(root, label) {
  return root.findAllByType('button').find(node => node.children.join('') === label);
}

function paragraphs(root) {
  return root.findAllByType('p').map(node => node.children.join(''));
}

test('a rejected stale draft clears the composer and reloads the new account', async t => {
  const local = browser(t, first);
  const root = await local.render();
  assert.ok(paragraphs(root).includes(firstMemory.text));
  await act(async () => root.findByType('textarea').props.onChange({ target: { value: 'Draft belonging to A' } }));
  local.setUser(second);
  await act(async () => root.findByType('form').props.onSubmit({ preventDefault() {} }));
  assert.deepEqual(local.calls.filter(call => call.method === 'POST'), [
    { method: 'POST', path: '/memories', expected: '11' },
  ]);
  assert.equal(root.findByType('textarea').props.value, '');
  assert.ok(paragraphs(root).includes(secondMemory.text));
  assert.ok(!paragraphs(root).includes(firstMemory.text));
});

test('focus and notification reconcile account changes and abort an outstanding write', async t => {
  const held = Promise.withResolvers();
  let inFlight;
  const local = browser(t, first, ({ method, options }) => {
    if (method !== 'POST') return;
    inFlight = options.signal;
    return held.promise;
  });
  const root = await local.render();
  await act(async () => root.findByType('textarea').props.onChange({ target: { value: 'Draft belonging to A' } }));
  await act(async () => local.focus());
  assert.equal(root.findByType('textarea').props.value, 'Draft belonging to A');
  await act(async () => root.findByType('form').props.onSubmit({ preventDefault() {} }));
  assert.equal(inFlight.aborted, false);
  local.setUser(second);
  await act(async () => local.notify());
  assert.equal(inFlight.aborted, true);
  await act(async () => held.resolve(Response.json(firstMemory)));
  assert.equal(root.findByType('textarea').props.value, '');
  assert.ok(paragraphs(root).includes(secondMemory.text));
  assert.ok(!paragraphs(root).includes(firstMemory.text));

  local.setUser(null);
  await act(async () => local.focus());
  assert.ok(button(root, '登录'));
  local.setUser(second);
  await act(async () => local.focus());
  assert.ok(paragraphs(root).includes(secondMemory.text));
});

test('cross-tab changes cannot hide a recovery code before acknowledgement', async t => {
  const recovered = Promise.withResolvers();
  const local = browser(t, null, ({ path }) => {
    if (path === '/auth/recover') return recovered.promise;
  });
  const root = await local.render();
  await act(async () => button(root, '找回账号').props.onClick());
  await act(async () => {
    void root.findByType('form').props.onSubmit({
      preventDefault() {}, currentTarget: { ...first, password: 'new fixture password', recovery_code: 'older-code' },
    });
  });
  local.setUser(first);
  await act(async () => local.focus());
  await act(async () => recovered.resolve(Response.json({ ...first, recovery_code: 'fresh-fixture-code' })));
  local.setUser(second);
  await act(async () => { local.notify(); local.focus(); });
  assert.equal(root.findByType('code').children.join(''), 'fresh-fixture-code');
  assert.deepEqual(local.announcements.map(([key]) => key), [sessionChangeKey]);
  assert.ok(!local.calls.some(call => call.path === '/memories'));
  await act(async () => button(root, '我已保存，继续').props.onClick());
  assert.ok(paragraphs(root).includes(secondMemory.text));
  assert.equal(root.findAllByType('code').length, 0);
});
