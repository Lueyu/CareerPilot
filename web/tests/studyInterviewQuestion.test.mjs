import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import StudyChat from '../src/pages/StudyChat.tsx';

const makeResult = id => ({
  id, short_code: 'Q-CHAT-' + id, title: '从对话整理的题目 ' + id,
  answer_html: '<p>真实对话中的取舍</p><script>unsafe()</script>',
});
const message = (id, role, status, content) => ({ id, role, status, content, sources: [] });
const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

async function mount(t, { messages = {}, results = {}, organize, read } = {}) {
  const requests = [];
  const window = Object.assign(new EventTarget(), {
    matchMedia: () => Object.assign(new EventTarget(), { matches: false }),
    setTimeout, clearTimeout,
  });
  const storage = new Map();
  let tree;
  let navigate;
  t.mock.method(globalThis, 'fetch', async (url, request) => {
    const path = new URL(url).pathname.replace('/api/v1', '');
    const method = request.method;
    requests.push({ path, method, body: request.body && JSON.parse(request.body) });
    if (path === '/projects' || path === '/study/sessions' || path.endsWith('/note-jobs')) {
      return Response.json({ items: [] });
    }
    const match = /^\/study\/sessions\/(\d+)(\/[\w-]+)?$/.exec(path);
    assert.ok(match, 'Unexpected route ' + path);
    const id = Number(match[1]);
    if (match[2] === '/interview-question') {
      return Response.json(method === 'GET' ? (read ? await read(id) : results[id] ?? null) : await organize(id));
    }
    if (match[2] === '/messages') {
      messages[id] ??= [];
      messages[id].push(
        message(id * 100 + messages[id].length, 'user', 'completed', JSON.parse(request.body).content),
        message(id * 100 + messages[id].length + 1, 'assistant', 'completed', '合成学习回复'),
      );
      return Response.json({});
    }
    assert.equal(match[2], undefined);
    return Response.json({ id, title: '原题 ' + id, status: 'active', context: { sources: [] }, messages: messages[id] ?? [] });
  });
  const originalWindow = globalThis.window;
  const originalStorage = globalThis.localStorage;
  globalThis.window = window;
  globalThis.localStorage = {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: key => storage.delete(key),
  };
  t.after(() => {
    if (tree) act(() => tree.unmount());
    globalThis.window = originalWindow;
    if (originalStorage === undefined) delete globalThis.localStorage;
    else globalThis.localStorage = originalStorage;
  });
  const mountAt = async id => {
    function Observe() {
      navigate = useNavigate();
      return null;
    }
    await act(async () => {
      if (tree) tree.unmount();
      tree = create(React.createElement(MemoryRouter, {
        initialEntries: ['/study/chat/' + id],
        future: { v7_startTransition: true, v7_relativeSplatPath: true },
      }, React.createElement(Observe), React.createElement(Routes, null,
        React.createElement(Route, { path: '/study/chat/:sessionId', element: React.createElement(StudyChat) }))));
    });
  };
  await mountAt(1);
  const button = text => tree.root.findAllByType('button').find(node => node.children.join('') === text);
  return {
    button, requests, mountAt,
    text: () => JSON.stringify(tree.toJSON()),
    find: props => tree.root.findByProps(props),
    go: id => act(async () => navigate('/study/chat/' + id)),
    send: async text => {
      act(() => tree.root.findByProps({ 'aria-label': '继续问' }).props.onChange({ target: { value: text } }));
      await act(async () => button('发送').props.onClick());
    },
  };
}

test('question chat organizes only after a completed reply, retries, and restores the saved answer', async t => {
  const first = deferred();
  const results = {};
  let attempts = 0;
  const ui = await mount(t, {
    messages: { 1: [message(1, 'user', 'completed', '原题第一问'), message(2, 'assistant', 'failed', '')] },
    results,
    organize: id => ++attempts === 1 ? first.promise : (results[id] = makeResult(id)),
  });
  assert.equal(ui.button('整理成面试题').props.disabled, true);
  await ui.send('请解释原题的第一种做法');
  const reply = ui.find({ id: 'study-message-103' });
  const followUp = reply.findAllByType('button').find(node => node.children.join('') === '围绕这条追问');
  act(() => followUp.props.onClick());
  await ui.send('再比较第二种做法');
  const sentMessages = ui.requests.filter(req => req.method === 'POST' && req.path.endsWith('/messages'));
  assert.equal(sentMessages[1].body.parent_message_id, 103);
  assert.equal(ui.button('整理成面试题').props.disabled, false);
  assert.ok(ui.button('整理这条回复'));
  assert.ok(ui.button('试着解释'));
  const organize = ui.button('整理成面试题').props.onClick;
  act(() => { void organize(); void organize(); });
  assert.equal(ui.button('正在整理面试题…').props.disabled, true);
  assert.equal(attempts, 1);
  await act(async () => { first.reject(new Error('合成整理失败')); });
  assert.match(ui.text(), /合成整理失败/);
  await act(async () => ui.button('重试整理面试题').props.onClick());
  assert.equal(ui.find({ href: '/bank?scope=mine' }).children.join(''), '在我的题目中查看');
  assert.match(ui.text(), /从对话整理的题目 1/);
  // The renderer has no document, so the shared sanitizer escapes all tags in this environment.
  assert.match(ui.text(), /&lt;script&gt;/);
  assert.doesNotMatch(ui.text(), /<script>/);
  assert.deepEqual(ui.requests.filter(req => req.method === 'POST' && req.path.endsWith('interview-question')),
    [1, 2].map(() => ({ path: '/study/sessions/1/interview-question', method: 'POST', body: {} })));
  await ui.mountAt(1);
  assert.match(ui.text(), /从对话整理的题目 1/);
  assert.equal(attempts, 2);
});

test('late organization and read responses stay in their own sessions', async t => {
  const oldSave = deferred();
  const oldRead = deferred();
  const ui = await mount(t, {
    messages: { 1: [message(2, 'assistant', 'completed', '可以整理')] },
    organize: () => oldSave.promise,
    read: id => id === 1 ? null : id === 2 ? oldRead.promise : makeResult(id),
  });
  act(() => { void ui.button('整理成面试题').props.onClick(); });
  await ui.go(2);
  await ui.go(3);
  await act(async () => {
    oldRead.resolve(makeResult(2));
    oldSave.resolve(makeResult(1));
  });
  assert.match(ui.text(), /从对话整理的题目 3/);
  assert.doesNotMatch(ui.text(), /从对话整理的题目 [12]/);
  assert.equal(ui.button('整理成面试题').props.disabled, true);
});

test('refresh while organizing can recover the finished result by reading instead of posting again', async t => {
  const response = deferred();
  const results = {};
  const ui = await mount(t, {
    messages: { 1: [message(1, 'assistant', 'completed', '已完成学习回复')] },
    results,
    organize: () => response.promise,
  });
  act(() => { void ui.button('整理成面试题').props.onClick(); });
  await ui.mountAt(1);
  assert.match(ui.text(), /上次整理结果尚未确认/);
  await act(async () => { response.resolve(results[1] = makeResult(1)); });
  await act(async () => ui.button('重新读取').props.onClick());
  assert.match(ui.text(), /从对话整理的题目 1/);
  assert.doesNotMatch(ui.text(), /上次整理结果尚未确认/);
  assert.equal(ui.requests.filter(req => req.method === 'POST').length, 1);
});
