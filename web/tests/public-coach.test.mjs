import { strict as assert } from 'node:assert';
import { register } from 'node:module';
import { test } from 'node:test';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

const apiKey = Symbol.for('careerpilot.public-coach.test-api');
const virtualApi = 'data:text/javascript,' + encodeURIComponent(
  "export function request(...args) { return globalThis[Symbol.for('careerpilot.public-coach.test-api')](...args); }",
);
const loader = 'data:text/javascript,' + encodeURIComponent(
  'export async function resolve(specifier, context, next) {' +
    'if (specifier === "./api" && context.parentURL?.endsWith("/src/seed/Coach.tsx")) {' +
      'return { url: ' + JSON.stringify(virtualApi) + ', shortCircuit: true };' +
    '} return next(specifier, context);' +
  '}',
);
register(loader, import.meta.url);
const { default: Coach } = await import('../src/seed/Coach.tsx');

const summary = { id: 1, title: '增长练习', role: '产品运营', direction: '用户增长', question_title: null };
const study = { ...summary, actions: ['chat', 'generate'], turns: [], candidates: [], ability_status: null };

function makeFixture(t, request) {
  const sources = [];
  const calls = [];
  const apiBefore = globalThis[apiKey];
  const eventSourceBefore = globalThis.EventSource;
  const windowBefore = globalThis.window;
  globalThis[apiKey] = async (path, options) => {
    const method = options?.method ?? 'GET';
    const body = options?.body ? JSON.parse(options.body) : undefined;
    calls.push({ method, path, body });
    if (method === 'GET' && path.startsWith('/coach/threads?')) {
      return { threads: [summary], next_offset: null };
    }
    return request({ path, method, body });
  };
  globalThis.window = { setTimeout, clearTimeout };
  globalThis.EventSource = class {
    constructor(url) {
      this.url = url;
      this.closed = false;
      sources.push(this);
    }
    addEventListener() {}
    close() { this.closed = true; }
    emit(value) { this.onmessage({ data: JSON.stringify(value) }); }
  };
  let view;
  t.after(async () => {
    await act(async () => view?.unmount());
    globalThis[apiKey] = apiBefore;
    globalThis.EventSource = eventSourceBefore;
    globalThis.window = windowBefore;
  });
  return {
    calls, sources,
    async render() {
      await act(async () => {
        view = create(createElement(Coach, { mode: 'study', onLibrary() {} }));
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
  assert.equal(source.url, '/api/v1/coach/threads/1/turns/7/events');
  await act(async () => source.emit({ ...response, reply: '先梳理社群目标' }));
  assert.ok(root.findAll(node => node.type === 'div' && node.children.includes('先梳理社群目标')).length);
  assert.ok(root.findAllByProps({ role: 'status' }).some(node => node.children.includes('正在生成回复…')));

  const completed = { ...response, status: 'completed', reply: '先梳理社群目标，再说明你负责的部分。' };
  thread = { ...study, turns: [completed] };
  await act(async () => source.emit(completed));
  assert.equal(source.closed, true);
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
