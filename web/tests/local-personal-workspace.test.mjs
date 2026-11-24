import assert from 'node:assert/strict';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { AppStateProvider } from '../src/components/AppState.tsx';
import { bindRegisteredNativeApi, selectWorkspace } from '../src/api/client.ts';
import { subscribeImports, trackImport } from '../src/lib/importTracker.ts';
import Bank from '../src/pages/Bank.tsx';
import Dashboard from '../src/pages/Dashboard.tsx';
import Onboarding from '../src/pages/Onboarding.tsx';

const storage = () => {
  const items = new Map();
  return { getItem: key => items.get(key) ?? null, setItem: (key, value) => items.set(key, value) };
};

const mounted = new WeakMap();
test.beforeEach(t => {
  const trees = [];
  mounted.set(t, trees);
  const saved = Object.fromEntries(['localStorage', 'sessionStorage', 'window', 'fetch']
    .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.defineProperties(globalThis, {
    localStorage: { configurable: true, value: storage() },
    sessionStorage: { configurable: true, value: storage() },
    window: { configurable: true, value: new EventTarget() },
  });
  t.after(() => {
    act(() => trees.forEach(tree => tree.unmount()));
    for (const [key, descriptor] of Object.entries(saved)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
});

async function mount(t, Page, path) {
  let tree;
  await act(async () => {
    tree = create(React.createElement(MemoryRouter, {
      initialEntries: [path],
      future: { v7_startTransition: true, v7_relativeSplatPath: true },
    }, React.createElement(AppStateProvider, null, React.createElement(Page))));
  });
  mounted.get(t).push(tree);
  return tree;
}

const text = tree => JSON.stringify(tree.toJSON());
const tab = (tree, label) => tree.root.findAllByType('button').find(node => node.children.join('') === label);

test('Linzhou opens its authored private questions on the ordinary bank route', async t => {
  selectWorkspace('linzhou');
  const calls = [];
  globalThis.fetch = async (url, options) => {
    const path = new URL(url);
    calls.push([path.pathname, options.headers['X-Workspace']]);
    if (path.pathname.endsWith('/bank')) {
      assert.equal(path.searchParams.get('scope'), 'mine');
      return Response.json({ total: 1, items: [{
        id: 77, short_code: 'LZ-77', title: '林舟整理的面试题', difficulty: 2,
        topic: 'rag', source_type: 'USER_PRIVATE', status: 'APPROVED',
      }] });
    }
    assert.ok(path.pathname.endsWith('/bank/interview-sets'));
    return Response.json({ items: [] });
  };
  const tree = await mount(t, Bank, '/bank');
  assert.equal(tab(tree, '我的题目')?.props['aria-pressed'], true);
  assert.equal(tab(tree, '全部题目'), undefined);
  assert.match(text(tree), /林舟整理的面试题/);
  assert.ok(calls.some(([path]) => path.endsWith('/bank')));
  assert.ok(calls.every(([path, workspace]) => !path.endsWith('/experienced') && workspace === 'linzhou'));
});

for (const existing of ['personal', 'my-previous-workspace']) {
  test(`existing local workspace ${existing} still opens the trained catalog`, async t => {
    selectWorkspace(existing);
    const calls = [];
    globalThis.fetch = async url => {
      const path = new URL(url).pathname;
      calls.push(path);
      return Response.json({ domains: [], items: [] });
    };
    const tree = await mount(t, Bank, '/bank');
    assert.equal(tab(tree, '全部题目')?.props['aria-pressed'], true);
    assert.ok(calls.some(path => path.endsWith('/bank/experienced')));
  });
}

test('Linzhou dashboard and onboarding use personal workspace copy', async t => {
  selectWorkspace('linzhou');
  globalThis.fetch = async () => Response.json({
    workspace: { name: '林舟的求职笔记', target_role: '应用开发', target_level: '中级', is_demo: false },
  });
  const dashboard = await mount(t, Dashboard, '/');
  const onboarding = await mount(t, Onboarding, '/onboarding');
  assert.match(text(dashboard), /林舟的求职笔记/);
  assert.match(text(dashboard), /应用开发/);
  assert.doesNotMatch(text(dashboard), /Demo|DEMO|演示工作空间/);
  assert.doesNotMatch(text(onboarding), /Demo 数据/);
});

test('workspace switches preserve the old import tracker and restore each workspace separately', () => {
  const snapshot = () => {
    let shortCodes;
    const unsubscribe = subscribeImports(items => { shortCodes = items.map(item => item.shortCode); });
    unsubscribe();
    return shortCodes;
  };
  sessionStorage.setItem('cp-import-tracking-v1', JSON.stringify([
    { id: 5, shortCode: 'original-import', startedAt: Date.now() },
  ]));
  assert.deepEqual(snapshot(), ['original-import']);
  selectWorkspace('linzhou');
  assert.deepEqual(snapshot(), []);
  trackImport(5, 'linzhou-import');
  assert.deepEqual(snapshot(), ['linzhou-import']);
  selectWorkspace('personal');
  assert.deepEqual(snapshot(), ['original-import']);
  selectWorkspace('linzhou');
  assert.deepEqual(snapshot(), ['linzhou-import']);
});

test('an open document keeps its request workspace when another tab changes the default', () => {
  const register = new URL('./register.mjs', import.meta.url).href;
  const transport = new URL('../src/api/localTransport.ts', import.meta.url).href;
  const result = spawnSync(process.execPath, ['--import', register, '--input-type=module', '--eval', `
    import assert from 'node:assert/strict';
    let selection = 'personal';
    globalThis.window = {};
    Object.defineProperty(globalThis, 'localStorage', { value: { getItem: () => selection, setItem: (_key, value) => { selection = value; } } });
    const { localFetch, selectLocalWorkspace } = await import(${JSON.stringify(transport)});
    selection = 'linzhou';
    globalThis.fetch = async (_url, options) => {
      assert.equal(options.headers['X-Workspace'], 'personal');
      return Response.json({});
    };
    await localFetch('/dashboard', {});
    selectLocalWorkspace('next-workspace');
    assert.equal(selection, 'next-workspace');
    await localFetch('/dashboard', {});
  `], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
});

test('registered accounts retain the catalog entitlement of the bound account', async t => {
  const controller = new AbortController();
  t.after(() => controller.abort());
  bindRegisteredNativeApi({
    workspaceId: 42, personalOwner: false, signal: controller.signal,
    response: async path => Response.json(path.startsWith('/native/bank?') ? { total: 0, items: [] } : { items: [] }),
  });
  const tree = await mount(t, Bank, '/bank');
  assert.equal(tab(tree, '我的题目')?.props['aria-pressed'], true);
  assert.equal(tab(tree, '全部题目'), undefined);
});
