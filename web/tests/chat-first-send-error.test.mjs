import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { act, create } from 'react-test-renderer';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import ChatWorkspace from '../src/interview/chat/ChatWorkspace.tsx';
import { getLocale, setLocale } from '../src/i18n.ts';

test('failed first send keeps its visible error, draft, session and retry ticket', async () => {
  const previous = { window: globalThis.window, localStorage: globalThis.localStorage, fetch: globalThis.fetch };
  const locale = getLocale();
  const values = new Map();
  const storage = { getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  globalThis.localStorage = storage;
  globalThis.window = Object.assign(new EventTarget(), { localStorage: storage });
  let created = 0;
  const sent = [];
  const prompt = '题库里有哪些关于F7BOUNDARY的题';
  globalThis.fetch = async (url, options) => {
    const path = new URL(url, 'http://localhost').pathname;
    if (path.endsWith('/study/sessions') && options.method === 'POST') {
      created++;
      return Response.json({ id: 7, title: prompt, status: 'active', context: {}, messages: [] });
    }
    if (path.endsWith('/study/sessions/7/messages')) {
      sent.push(JSON.parse(options.body));
      return sent.length === 1
        ? Response.json({ detail: { code: 'INTERNAL_ERROR', message: '服务暂时出错，请稍后重试。' } }, { status: 500 })
        : Response.json({ status: 'completed', assistant_message_id: 2 });
    }
    if (path.endsWith('/study/sessions/7')) {
      return Response.json({ id: 7, title: prompt, status: 'active', context: {}, messages: [] });
    }
    return Response.json({ items: [] });
  };
  const router = createMemoryRouter([{ path: '/chat', element: h(ChatWorkspace) },
    { path: '/chat/:sessionId', element: h(ChatWorkspace) }], { initialEntries: ['/chat?new=1'] });
  let view;
  try {
    setLocale('zh-CN');
    await act(async () => { view = create(h(RouterProvider, { router })); });
    await act(async () => view.root.findByProps({ 'aria-label': '聊天输入' }).props.onChange({ target: { value: prompt } }));
    const send = () => view.root.findByProps({ className: 'ia-chat-composer-box' }).props.onSubmit({ preventDefault() {} });
    await act(async () => { await send(); });
    assert.equal(router.state.location.pathname, '/chat');
    assert.match(JSON.stringify(view.root.findByProps({ role: 'alert' }).children), /服务暂时出错/);
    assert.equal(view.root.findByProps({ 'aria-label': '聊天输入' }).props.value, prompt);
    await act(async () => { await send(); });
    assert.equal(router.state.location.pathname, '/chat/7');
    assert.equal(created, 1);
    assert.equal(sent.length, 2);
    assert.equal(sent[0].request_id, sent[1].request_id);
    assert.equal(sent[0].content, sent[1].content);
    assert.equal(view.root.findAllByProps({ role: 'alert' }).length, 0);
  } finally {
    if (view) await act(async () => view.unmount());
    router.dispose();
    setLocale(locale);
    Object.assign(globalThis, previous);
  }
});
