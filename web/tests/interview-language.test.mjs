import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { act, create } from 'react-test-renderer';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { AppStateProvider } from '../src/components/AppState.tsx';
import { InterviewAppearance } from '../src/interview/appearance.tsx';
import InterviewShell from '../src/interview/InterviewShell.tsx';
import InterviewSettings from '../src/interview/InterviewSettings.tsx';
import ChatWorkspace from '../src/interview/chat/ChatWorkspace.tsx';
import { api } from '../src/api/client.ts';
import { createWorkspaceApi } from '../src/seed/api.ts';
import { getLocale, setLocale } from '../src/i18n.ts';

test('header and settings switch chat controls immediately and preserve drafts and saved messages', async () => {
  const previousLocale = getLocale();
  const original = { window: globalThis.window, document: globalThis.document,
    localStorage: globalThis.localStorage, fetch: globalThis.fetch };
  const values = new Map();
  const storage = { getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
  globalThis.localStorage = storage;
  globalThis.window = Object.assign(new EventTarget(), { localStorage: storage,
    matchMedia: () => Object.assign(new EventTarget(), { matches: false }) });
  globalThis.document = { documentElement: { dataset: {} } };
  const calls = [];
  const savedReply = '历史中文回复保持原样。';
  const savedQuestion = '原始 RAG 面试题';
  const messages = [
    { id: 11, request_id: 'fixture', role: 'user', content: '列出资料库题目', status: 'completed' },
    { id: 12, request_id: 'fixture', role: 'assistant', agent_role: 'collect', content: savedReply,
      status: 'completed', sources: [], blocks: [], library_inventory: { total: 1, language: 'zh-CN',
        items: [{ id: 3, title: savedQuestion, difficulty: 3, topic: 'rag', tags: [] }] } },
  ];
  globalThis.fetch = async (url, options) => {
    calls.push({ path: new URL(url, 'http://localhost').pathname,
      language: new Headers(options.headers).get('Accept-Language'), method: options.method });
    const path = calls.at(-1).path;
    if (path.endsWith('/settings/overview')) return Response.json({ counts: { memory: 1 } });
    if (path.endsWith('/settings/llm')) return Response.json({ active: false, api_key_set: false });
    if (path.endsWith('/study/sessions/7')) return Response.json({ id: 7, title: '保存的对话标题',
      status: 'ended', context: {}, messages });
    return Response.json({ items: [] });
  };
  const router = createMemoryRouter([{ element: h(AppStateProvider, null,
    h(InterviewAppearance, null, h(InterviewShell))), children: [
    { path: '/chat/:sessionId', element: h(ChatWorkspace) },
    { path: '/settings', element: h(InterviewSettings) },
  ] }], { initialEntries: ['/chat/7'] });
  let view;
  const output = () => JSON.stringify(view.toJSON());
  const select = () => view.root.findByProps({ className: 'ia-top-tools' }).findByType('select');
  try {
    setLocale('zh-CN');
    await act(async () => { view = create(h(RouterProvider, { router })); });
    assert.match(output(), /继续对话/);
    assert.match(output(), /在聊天内作答/);
    await act(async () => {
      view.root.findByProps({ 'aria-label': '聊天输入' }).props.onChange({ target: { value: '未发送的草稿' } });
      view.root.findAllByType('button').find(button => button.children.includes('打开角色与技能')).props.onClick();
      select().props.onChange({ target: { value: 'en' } });
    });
    assert.equal(values.get('careerpilot.locale'), 'en');
    assert.equal(view.root.findByProps({ 'aria-label': 'Chat input' }).props.value, '未发送的草稿');
    for (const label of ['Resume conversation', 'Roles and skills', 'Interviewer', 'Resume advisor',
      'Full mock interview', 'Answer in chat', 'Save as study note', savedReply, savedQuestion, '保存的对话标题']) {
      assert.ok(output().includes(label), label);
    }
    await api.get('/language-fixture');
    assert.equal(calls.at(-1).language, 'en');
    await act(async () => select().props.onChange({ target: { value: 'zh-CN' } }));
    assert.match(output(), /继续对话/);
    assert.match(output(), /在聊天内作答/);
    assert.equal(view.root.findByProps({ 'aria-label': '聊天输入' }).props.value, '未发送的草稿');
    await api.get('/language-fixture');
    assert.equal(calls.at(-1).language, 'zh-CN');
    await act(async () => router.navigate('/settings'));
    const settings = view.root.findByProps({ className: 'ia-settings' });
    await act(async () => settings.findByType('select').props.onChange({ target: { value: 'en' } }));
    assert.equal(select().props.value, 'en');
    assert.equal(values.get('careerpilot.locale'), 'en');
    assert.match(output(), /Save model/);
    await act(async () => router.navigate('/chat/7'));
    assert.equal(select().props.value, 'en');
    assert.equal(view.root.findByProps({ 'aria-label': 'Chat input' }).props.value, '未发送的草稿');
    assert.ok(output().includes(savedReply));
    assert.deepEqual(messages[1].content, savedReply);
  } finally {
    if (view) await act(async () => view.unmount());
    router.dispose();
    setLocale(previousLocale);
    Object.assign(globalThis, original);
  }
});

test('registered native transport keeps the selected language on fresh requests', async () => {
  const previousLocale = getLocale();
  const originalFetch = globalThis.fetch;
  const calls = [];
  const owner = createWorkspaceApi(31, () => assert.fail('unexpected session invalidation'));
  globalThis.fetch = async (_url, options) => {
    calls.push(options);
    return Response.json({});
  };
  try {
    for (const language of ['en', 'zh-CN', 'en']) {
      setLocale(language);
      await owner.request('/native/language-fixture');
      assert.equal(calls.at(-1).headers.get('Accept-Language'), language);
      assert.equal(calls.at(-1).headers.get('X-Expected-Workspace'), '31');
      assert.equal(calls.at(-1).credentials, 'same-origin');
    }
  } finally {
    owner.close();
    globalThis.fetch = originalFetch;
    setLocale(previousLocale);
  }
});
