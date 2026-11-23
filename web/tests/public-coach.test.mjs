import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';
import Coach from '../src/seed/Coach.tsx';
import { createWorkspaceApi, WorkspaceApiContext } from '../src/seed/api.ts';

const summary = { id: 1, title: '增长练习', role: '产品运营', direction: '用户增长', question_title: null };
const study = { ...summary, actions: ['chat', 'generate'], turns: [], candidates: [], ability_status: null };

function makeFixture(t, request) {
  const sources = [];
  const calls = [];
  const fetchBefore = globalThis.fetch;
  const windowBefore = globalThis.window;
  const workspaceId = 11;
  const api = createWorkspaceApi(workspaceId, () => assert.fail('unexpected session invalidation'));
  globalThis.fetch = async (url, options) => {
    const path = url.slice('/api/v1'.length);
    const method = options?.method ?? 'GET';
    const body = options?.body ? JSON.parse(options.body) : undefined;
    assert.equal(options.headers.get('X-Expected-Workspace'), String(workspaceId));
    calls.push({ method, path, body });
    if (method === 'GET' && path.startsWith('/coach/threads?')) {
      return Response.json({ threads: [summary], next_offset: null });
    }
    if (path.endsWith('/events')) {
      const body = new ReadableStream({
        start(controller) {
          sources.push({
            path,
            signal: options.signal,
            emit(value) { controller.enqueue(new TextEncoder().encode('data: ' + JSON.stringify(value) + '\n\n')); },
          });
        },
      });
      return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
    }
    return Response.json(await request({ path, method, body }));
  };
  globalThis.window = { setTimeout, clearTimeout, location: { origin: 'https://seed.test' } };
  let view;
  t.after(async () => {
    await act(async () => view?.unmount());
    globalThis.fetch = fetchBefore;
    globalThis.window = windowBefore;
  });
  return {
    calls, sources,
    async render(props = {}) {
      await act(async () => {
        const element = createElement(WorkspaceApiContext.Provider, { value: api },
          createElement(Coach, { mode: 'study', onLibrary() {}, ...props }),
        );
        if (view) view.update(element);
        else view = create(element);
      });
      return view.root;
    },
  };
}

function button(root, label) {
  return root.findAllByType('button').find(node => node.children.join('') === label);
}

async function submit(root, text) {
  await act(async () => root.findByType('textarea').props.onChange({ target: { value: text } }));
  await act(async () => root.findByType('form').props.onSubmit({ preventDefault() {} }));
}

function turn(id, action, userText, status, reply = '') {
  return { id, action, user_text: userText, assisted: null, reply, status, error: null, attempt: 1, result: null };
}

test('shows streamed text before completion and confirms a learner message before saving memory', async t => {
  let thread = study;
  const response = turn(7, 'chat', '我负责社群拉新', 'generating');
  const fixture = makeFixture(t, ({ path, method }) => {
    if (path === '/coach/threads/1' && method === 'GET') return thread;
    if (path === '/coach/threads/1/turns') return response;
    if (path === '/memories') return { id: 20 };
    assert.fail(path);
  });

  const root = await fixture.render();
  await submit(root, response.user_text);
  const source = fixture.sources[0];
  assert.equal(source.path, '/coach/threads/1/turns/7/events');
  await act(async () => source.emit({ ...response, reply: '先梳理社群目标' }));
  assert.ok(root.findAll(node => node.type === 'div' && node.children.includes('先梳理社群目标')).length);
  assert.ok(root.findAllByProps({ role: 'status' }).some(node => node.children.includes('正在生成回复…')));

  const completed = { ...response, status: 'completed', reply: '先梳理社群目标，再说明你负责的部分。' };
  thread = { ...study, turns: [completed] };
  await act(async () => source.emit(completed));
  assert.equal(source.signal.aborted, true);
  assert.ok(root.findAllByType('p').some(node => node.children.includes(completed.reply)));
  const memoryButtons = root.findAllByType('button').filter(node => node.children.includes('选作项目经历'));
  assert.equal(memoryButtons.length, 1);
  await act(async () => memoryButtons[0].props.onClick());
  assert.equal(fixture.calls.some(call => call.path === '/memories'), false);
  assert.equal(root.findByProps({ 'aria-label': '确认项目经历' }).findByType('textarea').props.value, response.user_text);
  await act(async () => root.findByProps({ 'aria-label': '确认项目经历' }).props.onSubmit({ preventDefault() {} }));
  assert.deepEqual(fixture.calls.find(call => call.path === '/memories').body, {
    text: response.user_text, source: 'user_statement', confirmed: true,
  });
});

test('sends conversational requests to candidate generation and collects one revised candidate', async t => {
  const titles = ['复盘活动目标', '解释自然增长', '衡量转化效果'];
  let thread = study;
  const fixture = makeFixture(t, ({ path, method, body }) => {
    if (path === '/coach/threads/1' && method === 'GET') return thread;
    if (path === '/coach/threads/1/turns') {
      const response = turn(thread.turns.length + 1, body.action, body.text, 'completed', '题目已更新');
      const candidates = titles.map((title, index) => ({
        id: index + 1, number: index + 1, title, reference: null, superseded: false, question_id: null,
      }));
      if (thread.turns.length) {
        candidates[1].superseded = true;
        candidates.push({ ...candidates[1], id: 9, title: '如何复盘实习中的拉新活动', superseded: false });
      }
      thread = { ...study, turns: [...thread.turns, response], candidates };
      return response;
    }
    if (path === '/coach/threads/1/candidates/9/collect') return { question_id: 83 };
    assert.fail(path);
  });

  const root = await fixture.render();
  await submit(root, '我想练三道活动复盘题');
  assert.ok(button(root, '收藏第 1 题'));
  assert.ok(button(root, '收藏第 3 题'));
  await submit(root, '第二题换成实习场景');
  const actions = fixture.calls.filter(call => call.path.endsWith('/turns')).map(call => call.body.action);
  assert.deepEqual(actions, ['generate', 'generate']);
  await act(async () => button(root, '收藏第 2 题').props.onClick());
  assert.deepEqual(fixture.calls.filter(call => call.path.endsWith('/collect')).map(call => call.path),
    ['/coach/threads/1/candidates/9/collect']);
  assert.equal(button(root, '收藏第 2 题'), undefined);
  assert.ok(root.findAllByProps({ role: 'status' }).some(node => node.children.includes('已收藏到我的题库')));
});

test('uses the route thread even when outside the first page and reports a history selection', async t => {
  const older = { ...study, id: 51, title: '更早的练习', mode: 'study', turns: [
    turn(9, 'chat', '较早的问题', 'completed', '较早的回复'),
  ] };
  const changed = [];
  const fixture = makeFixture(t, ({ path }) => {
    if (path === '/coach/threads/51') return older;
    if (path === '/coach/threads/1') return study;
    assert.fail(path);
  });
  const onThreadChange = id => changed.push(id);
  const root = await fixture.render({ threadId: 51, onThreadChange });
  assert.ok(root.findAllByType('p').some(node => node.children.includes('较早的回复')));
  await act(async () => button(root, summary.title).props.onClick());
  assert.deepEqual(changed, [1]);
  await fixture.render({ threadId: 1, onThreadChange });
  assert.equal(root.findAllByType('p').some(node => node.children.includes('较早的回复')), false);
  await fixture.render({ threadId: 51, onThreadChange });
  assert.ok(root.findAllByType('p').some(node => node.children.includes('较早的回复')));
});

test('never renders a deep-linked interview in study mode', async t => {
  const fixture = makeFixture(t, ({ path }) => {
    assert.equal(path, '/coach/threads/51');
    return { ...study, id: 51, mode: 'interview', turns: [
      turn(9, 'answer', '面试作答', 'completed', '面试追问'),
    ] };
  });
  const root = await fixture.render({ threadId: 51 });
  assert.ok(root.findAllByProps({ role: 'alert' }).some(node => node.children.includes('这段记录不属于当前练习。')));
  assert.equal(root.findAllByType('p').some(node => node.children.includes('面试追问')), false);
});

test('saves an assistant reply as a note only on explicit action and opens the returned note', async t => {
  const opened = [];
  const fixture = makeFixture(t, ({ path, method }) => {
    if (path === '/coach/threads/1') return {
      ...study, turns: [turn(9, 'chat', '我的问题', 'completed', '这条回复可以保存。')],
    };
    assert.equal(path, '/coach/threads/1/turns/9/note');
    assert.equal(method, 'POST');
    return { id: 18 };
  });
  const root = await fixture.render({ onNote: id => opened.push(id) });
  assert.equal(fixture.calls.some(call => call.method === 'POST'), false);
  await act(async () => button(root, '保存为笔记').props.onClick());
  assert.deepEqual(opened, [18]);
});
