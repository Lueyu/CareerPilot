import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setTimeout as delay } from 'node:timers/promises';
import { createElement as h } from 'react';
import { act, create } from 'react-test-renderer';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import RegisteredApp from '../src/seed/RegisteredApp.tsx';
import StudyNotes from '../src/seed/StudyNotes.tsx';
import { SeedApiError, WorkspaceApiContext } from '../src/seed/api.ts';

function button(root, label) {
  return root.findAllByType('button').find(node => node.children.join('') === label);
}

test('a conflicting note shows the remote revision and saves the merged draft against it', async t => {
  let remote = { id: 7, version_id: 1, title: 'Initial', body: 'First draft', versions: [] };
  const patches = [];
  async function request(path, options) {
    assert.equal(path, '/coach/notes/7');
    if (options.method !== 'PATCH') return remote;
    const patch = JSON.parse(options.body);
    patches.push(patch);
    if (patch.expected_version_id !== remote.version_id) throw new SeedApiError(409, 'Version conflict');
    remote = { ...remote, title: patch.title, body: patch.body, version_id: remote.version_id + 1 };
    return remote;
  }
  let view;
  t.after(() => act(() => view?.unmount()));
  await act(async () => {
    view = create(h(WorkspaceApiContext.Provider, { value: { request } }, h(StudyNotes, { noteId: 7 })));
  });
  const { root } = view;
  const input = () => root.findByType('textarea');
  const edit = value => act(async () => input().props.onChange({ target: { value } }));
  const save = () => act(async () => root.findByType('form').props.onSubmit({ preventDefault() {} }));
  await edit('My draft');
  remote = { ...remote, title: 'Other tab', body: 'Other edit', version_id: 2 };
  await save();
  assert.equal(input().props.value, 'My draft');
  assert.ok(root.findAllByType('p').some(node => node.children.includes('Other edit')));
  assert.equal(patches.length, 1);
  await edit('Other edit + my draft');
  await save();
  assert.deepEqual(patches.map(patch => patch.expected_version_id), [1, 2]);
  assert.equal(remote.body, 'Other edit + my draft');
  assert.equal(button(root, '放弃修改'), undefined);
});

test('Back restores the initially displayed study thread and then the preceding page', async t => {
  const prior = { window: globalThis.window, document: globalThis.document, fetch: globalThis.fetch };
  globalThis.window = Object.assign(new EventTarget(), { matchMedia: () => new EventTarget() });
  globalThis.document = Object.assign(new EventTarget(), { documentElement: { dataset: {} } });
  const thread = id => ({
    id, title: 'Study ' + id, mode: 'study', role: 'Engineering', direction: 'Systems',
    actions: ['chat'], turns: [], candidates: [],
  });
  const history = [thread(1)];
  globalThis.fetch = async (url, options) => {
    const path = url.slice('/api/v1'.length);
    if (path === '/auth/me') return Response.json({ workspace_id: 11, username: 'tester' });
    if (path === '/native/access') return Response.json({ personal_owner: false });
    if (path.startsWith('/native/')) return Response.json({}, { status: 404 });
    if (path === '/library/roles') return Response.json({
      selected_role: 'Engineering', roles: [{ role: 'Engineering', selected_topic: 'Systems' }],
    });
    if (path === '/coach/threads?mode=study') return Response.json({ threads: history, next_offset: null });
    if (path === '/coach/threads' && options.method === 'POST') {
      history.unshift(thread(2));
      return Response.json(history[0]);
    }
    const detail = history.find(row => path === '/coach/threads/' + row.id);
    if (detail) return Response.json(detail);
    throw new Error('Unexpected route ' + path);
  };
  const router = createMemoryRouter([
    { path: '/previous', element: h('p', null, 'Previous page') },
    { path: '*', element: h(RegisteredApp) },
  ], { initialEntries: ['/previous', '/beta/study/chat'], initialIndex: 1 });
  let view;
  t.after(() => {
    act(() => view?.unmount());
    router.dispose();
    Object.assign(globalThis, prior);
  });
  await act(async () => { view = create(h(RouterProvider, { router })); });
  // Wait for the registered app's lazy-loaded coach and its first history request.
  const until = Date.now() + 5000;
  while (router.state.location.pathname === '/beta/study/chat' && Date.now() < until) {
    await act(async () => { await delay(10); });
  }
  const { root } = view;
  assert.equal(router.state.location.pathname, '/beta/study/chat/1');
  assert.equal(button(root, 'Study 1').props['aria-current'], 'true');
  await act(async () => button(root, '新建学习对话').props.onClick());
  const startForm = root.findAllByType('form').find(form => button(form, '开始学习对话'));
  await act(async () => startForm.props.onSubmit({ preventDefault() {} }));
  assert.equal(router.state.location.pathname, '/beta/study/chat/2');
  assert.equal(button(root, 'Study 2').props['aria-current'], 'true');
  await act(async () => { await router.navigate(-1); });
  assert.equal(router.state.location.pathname, '/beta/study/chat/1');
  assert.equal(button(root, 'Study 1').props['aria-current'], 'true');
  await act(async () => { await router.navigate(-1); });
  assert.equal(router.state.location.pathname, '/previous');
});
