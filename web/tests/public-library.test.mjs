import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';
import Library, { RegisteredOnboarding } from '../src/seed/Library.tsx';
import { BankQuestionRow, BankSearch, BankUploadPicker } from '../src/components/BankPresentation.tsx';
import { createWorkspaceApi, WorkspaceApiContext } from '../src/seed/api.ts';

const topic = '接口与数据一致性';
const role = {
  role: '后端开发', jd: null, selected_topic: null,
  directions: [{ name: topic, example: '怎么设计幂等接口？', origin: 'preset' }],
};

function fixture(t, respond) {
  const previousFetch = globalThis.fetch;
  const api = createWorkspaceApi(42, () => assert.fail('unexpected session invalidation'));
  globalThis.fetch = async (url, options) => {
    assert.equal(options.headers.get('X-Expected-Workspace'), '42');
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : options.body;
    const result = respond(url.slice('/api/v1'.length), options.method ?? 'GET', body);
    return result instanceof Response ? result : Response.json(result);
  };
  let view;
  t.after(async () => {
    await act(async () => view?.unmount());
    api.close();
    globalThis.fetch = previousFetch;
  });
  return async (element) => {
    await act(async () => {
      view = create(createElement(WorkspaceApiContext.Provider, { value: api }, element));
    });
    return view.root;
  };
}

function button(root, label) {
  return root.findAllByType('button').find(node => node.children.join('') === label);
}

test('onboarding persists role and direction before explicit completion', async (t) => {
  const calls = [];
  let complete = 0;
  const render = fixture(t, (path, method, body) => {
    calls.push({ path, method, body });
    if (path === '/library/roles') return { selected_role: null, roles: [] };
    if (path === '/library/role') return role;
    assert.equal(path, '/library/direction');
    return { ...role, selected_topic: topic };
  });
  const root = await render(createElement(RegisteredOnboarding, { onComplete: () => complete++ }));
  assert.equal(button(root, '进入工作空间').props.disabled, true);
  await act(async () => root.findByType('input').props.onChange({ target: { value: role.role } }));
  await act(async () => root.findByType('form').props.onSubmit({ preventDefault() {} }));
  assert.equal(button(root, '进入工作空间').props.disabled, true);
  await act(async () => button(root, topic).props.onClick());
  assert.equal(button(root, topic).props['aria-pressed'], true);
  assert.ok(root.findAllByType('p').some(node => node.children.includes(role.directions[0].example)));
  assert.equal(complete, 0);
  await act(async () => button(root, '进入工作空间').props.onClick());
  assert.equal(complete, 1);
  assert.deepEqual(calls, [
    { path: '/library/roles', method: 'GET', body: undefined },
    { path: '/library/role', method: 'PUT', body: { role: role.role, jd: '' } },
    { path: '/library/direction', method: 'PUT', body: { role: role.role, topic } },
  ]);
});

test('registered Bank uses native rows, picker, and scoped search and approval', async (t) => {
  const question = { id: 8, role: role.role, title: '如何验证幂等？', topic, answer: '用重放验证。', filename: 'test.md' };
  const batch = {
    id: 5, filename: 'test.md', role: role.role, topic, status: 'REVIEW',
    items: [{ id: 6, title: '草稿题目', answer: '', selected: true, status: 'DRAFT' }],
  };
  let approved = false;
  let searches = 0;
  const render = fixture(t, (path, method, body) => {
    if (path === '/library/roles') return { selected_role: role.role, roles: [{ ...role, selected_topic: topic }] };
    if (path === '/privacy') return { provider: 'fixture', region: 'fixture' };
    if (path.startsWith('/library/questions?')) {
      const params = new URLSearchParams(path.split('?')[1]);
      assert.equal(params.get('role'), role.role);
      assert.equal(params.get('topic'), topic);
      if (params.has('q')) {
        assert.equal(params.get('q'), '幂等');
        searches++;
      }
      return { questions: approved ? [question] : [], next_before: null };
    }
    if (path === '/library/imports' && method === 'GET') return { imports: [], next_before: null };
    if (path === '/library/imports' && method === 'POST') {
      assert.equal(body.get('file').name, 'test.md');
      assert.equal(body.get('role'), role.role);
      assert.equal(body.get('topic'), topic);
      return batch;
    }
    assert.equal(path, '/library/imports/5/approve');
    assert.deepEqual(body, { items: [{ id: 6, title: '如何验证幂等？', answer: '', selected: true }] });
    approved = true;
    return { ...batch, status: 'APPROVED', items: [] };
  });
  let practice;
  const root = await render(createElement(Library, { onPractice: (...args) => { practice = args; }, onCoach() {} }));
  assert.ok(root.findByType(BankUploadPicker));
  const file = root.findAllByType('input').find(node => node.props.type === 'file');
  await act(async () => file.props.onChange({ target: { files: [new File(['面经'], 'test.md')] } }));
  await act(async () => root.findAllByType('form')[1].props.onSubmit({ preventDefault() {} }));
  const draftTitle = root.findAllByType('input').find(node => node.props.value === '草稿题目');
  await act(async () => draftTitle.props.onChange({ target: { value: question.title } }));
  await act(async () => root.findAllByType('form')[2].props.onSubmit({ preventDefault() {} }));
  assert.equal(root.findByType(BankQuestionRow).props.title, question.title);
  const search = root.findByType(BankSearch);
  await act(async () => search.findByType('input').props.onChange({ target: { value: '幂等' } }));
  assert.equal(searches, 1);
  await act(async () => button(root, '学习').props.onClick());
  assert.deepEqual(practice, [question, 'study']);
});

test('Library comments keep their existing feedback endpoint, payload and 201 response', async t => {
  const question = { id: 8, role: role.role, title: '如何验证幂等？', topic };
  const render = fixture(t, (path, method, body) => {
    if (path === '/library/roles') return { selected_role: role.role, roles: [{ ...role, selected_topic: topic }] };
    if (path === '/privacy') return { provider: 'fixture', region: 'fixture' };
    if (path.startsWith('/library/questions?')) return { questions: [question], next_before: null };
    if (path === '/library/imports') return { imports: [], next_before: null };
    assert.equal(path, '/feedback');
    assert.equal(method, 'POST');
    assert.deepEqual(body, { question_id: 8, text: '合成评论' });
    return Response.json({ id: 10 }, { status: 201 });
  });
  const root = await render(createElement(Library, { onPractice() {}, onCoach() {} }));
  const feedback = root.findByType(BankQuestionRow).findByType('form');
  await act(async () => feedback.findByType('textarea').props.onChange({ target: { value: ' 合成评论 ' } }));
  await act(async () => feedback.props.onSubmit({ preventDefault() {} }));
  assert.equal(feedback.findByProps({ role: 'status' }).children[0], '反馈已发送。');
});
