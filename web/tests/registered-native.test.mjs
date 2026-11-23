import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement as h } from 'react';
import { act, create } from 'react-test-renderer';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import RegisteredApp from '../src/seed/RegisteredApp.tsx';
import { api, bindRegisteredNativeApi, downloadWorkspaceExport, getWorkspace, readMessageStream } from '../src/api/client.ts';
import { loadLegacyBank } from '../src/seed/legacyBank.ts';
import { createWorkspaceApi } from '../src/seed/api.ts';

test('bound owner enters a native project deep link with cookie-only transport', async t => {
  const original = { window: globalThis.window, document: globalThis.document, fetch: globalThis.fetch };
  globalThis.window = Object.assign(new EventTarget(), { matchMedia: () => new EventTarget(), setTimeout, clearTimeout });
  globalThis.document = Object.assign(new EventTarget(), { documentElement: { dataset: {} } });
  const calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push(path);
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.cache, 'no-store');
    assert.equal(options.headers.has('X-Api-Token'), false);
    assert.equal(options.headers.has('X-Workspace'), false);
    if (path === '/api/v1/auth/me') {
      assert.equal(options.headers.has('X-Expected-Workspace'), false);
      return Response.json({ workspace_id: 31, username: 'fixture_owner' });
    }
    assert.ok(path.startsWith('/api/v1/native/'), path);
    assert.equal(options.headers.get('X-Expected-Workspace'), '31');
    if (path.endsWith('/access')) return Response.json({ personal_owner: true });
    if (path.endsWith('/settings/export')) return new Response('fixture export');
    if (path.endsWith('/bank/legacy-data')) return Response.json({ questions: [{ id: 'fixture' }] });
    if (path.endsWith('/fixture/stream')) return new Response('data: {"id":1}\n\n');
    if (path.endsWith('/fixture/upload')) {
      assert.ok(options.body instanceof FormData);
      assert.equal(options.headers.has('Content-Type'), false);
      return Response.json({ accepted: true });
    }
    if (path.endsWith('/projects')) return Response.json({ items: [] });
    return Response.json({ detail: 'Fixture not found' }, { status: 404 });
  };
  const router = createMemoryRouter([{ path: '*', element: h(RegisteredApp) }], {
    initialEntries: ['/assets/project/42'],
  });
  let view;
  t.after(async () => {
    await act(async () => view?.unmount());
    router.dispose();
    Object.assign(globalThis, original);
  });
  await act(async () => { view = create(h(RouterProvider, { router })); });
  assert.equal(router.state.location.pathname, '/assets/project/42');
  assert.ok(calls.includes('/api/v1/native/projects/42/report'));
  assert.ok(view.root.findAllByType('input').some(input => input.props.placeholder === '搜索技能、证据、任务'));
  assert.equal(getWorkspace(), 'registered:31');
  assert.ok(!calls.some(path => path.endsWith('/bank/legacy-data')));
  assert.deepEqual(await loadLegacyBank(), { questions: [{ id: 'fixture' }] });
  assert.equal(await (await downloadWorkspaceExport()).text(), 'fixture export');
  const messages = [];
  await readMessageStream('/fixture/stream', new AbortController().signal, event => messages.push(event));
  assert.deepEqual(messages, [{ id: 1 }]);
  assert.deepEqual(await api.upload('/fixture/upload', new FormData()), { accepted: true });
});

test('logout invalidates a pending native response and the next account binds in the same document', async t => {
  const originalFetch = globalThis.fetch;
  const owner = createWorkspaceApi(31, () => assert.fail('unexpected invalidation'));
  const peer = createWorkspaceApi(32, () => assert.fail('unexpected invalidation'));
  const body = Promise.withResolvers();
  const staleStarted = Promise.withResolvers();
  const calls = [];
  t.after(() => {
    owner.close();
    peer.close();
    globalThis.fetch = originalFetch;
  });
  globalThis.fetch = async (path, options) => {
    const expected = options.headers.get('X-Expected-Workspace');
    calls.push([path, expected]);
    if (expected === '32') return Response.json({ workspace: 32 });
    const response = Response.json({});
    response.json = () => {
      staleStarted.resolve();
      return body.promise;
    };
    return response;
  };

  const closeOwner = bindRegisteredNativeApi({ workspaceId: 31, personalOwner: true, ...owner });
  const stale = api.get('/dashboard');
  const rejected = assert.rejects(stale, { name: 'AbortError' });
  await staleStarted.promise;
  owner.close();
  assert.equal(getWorkspace(), 'registered:unbound');
  await assert.rejects(api.get('/dashboard'), { status: 401 });

  bindRegisteredNativeApi({ workspaceId: 32, personalOwner: false, ...peer });
  closeOwner();
  assert.equal(getWorkspace(), 'registered:32');
  assert.deepEqual(await api.get('/dashboard'), { workspace: 32 });
  body.resolve({ workspace: 31 });
  await rejected;
  assert.deepEqual(calls, [['/api/v1/native/dashboard', '31'], ['/api/v1/native/dashboard', '32']]);
});

test('non-owner can reach conversational question generation from native bank and study', async t => {
  const original = { window: globalThis.window, document: globalThis.document, fetch: globalThis.fetch };
  globalThis.window = Object.assign(new EventTarget(), { matchMedia: () => new EventTarget(), setTimeout, clearTimeout });
  globalThis.document = Object.assign(new EventTarget(), { documentElement: { dataset: {} } });
  const calls = [];
  globalThis.fetch = async url => {
    calls.push(url);
    if (url.endsWith('/auth/me')) return Response.json({ workspace_id: 32, username: 'peer_fixture' });
    if (url.endsWith('/native/access')) return Response.json({ personal_owner: false });
    if (url.endsWith('/library/roles')) return Response.json({
      selected_role: '产品运营',
      roles: [{ role: '产品运营', selected_topic: '用户增长' }],
    });
    if (url.includes('/native/bank?')) return Response.json({ total: 0, items: [] });
    if (url.includes('/native/study/sessions')) return Response.json({ items: [] });
    return Response.json({}, { status: 404 });
  };
  const router = createMemoryRouter([{ path: '*', element: h(RegisteredApp) }], { initialEntries: ['/bank'] });
  let view;
  t.after(async () => {
    await act(async () => view?.unmount());
    router.dispose();
    Object.assign(globalThis, original);
  });
  await act(async () => { view = create(h(RouterProvider, { router })); });
  const links = () => view.root.findAllByType('a');
  const builder = () => links().find(link => link.children.join('') === '和教练聊着出题');
  assert.equal(builder()?.props.href, '/beta/study/chat');
  assert.ok(links().some(link => link.props.href === '/beta/bank'));
  assert.ok(calls.some(url => url.includes('/native/bank?scope=mine')));
  assert.ok(!calls.some(url => url.endsWith('/bank/experienced') || url.endsWith('/bank/legacy-data')));

  await act(async () => { await router.navigate('/study/chat'); });
  assert.equal(builder()?.props.href, '/beta/study/chat');
});
