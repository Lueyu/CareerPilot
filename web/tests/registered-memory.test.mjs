import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import MemoryTimeline from '../src/components/MemoryTimeline.tsx';
import Memory from '../src/seed/Memory.tsx';
import { createWorkspaceApi, WorkspaceApiContext } from '../src/seed/api.ts';

const h = React.createElement;

test('registered memory uses the shared timeline and writes confirmed statements to the scoped API', async t => {
  const existing = {
    id: 7, text: '已有经历', revision: 1, confirmed_at: '2026-11-24T08:00:00Z', source: 'user_statement',
  };
  let entries = [existing];
  const requests = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(options.headers.get('X-Expected-Workspace'), '42');
    assert.equal(options.credentials, 'same-origin');
    const method = options.method ?? 'GET';
    const body = options.body ? JSON.parse(options.body) : undefined;
    requests.push({ url, method, body });
    if (method === 'GET') {
      assert.equal(url, '/api/v1/memories');
      return Response.json(entries);
    }
    if (method === 'POST') {
      assert.equal(url, '/api/v1/memories');
      const saved = { ...existing, id: 8, text: body.text };
      entries = [saved, ...entries];
      return Response.json(saved, { status: 201 });
    }
    assert.equal(url, '/api/v1/memories/8');
    if (method === 'PUT') {
      const saved = { ...entries[0], text: body.text, revision: 2 };
      entries = [saved, existing];
      return Response.json(saved);
    }
    assert.equal(method, 'DELETE');
    entries = [existing];
    return new Response(null, { status: 204 });
  });

  const api = createWorkspaceApi(42, () => assert.fail('session unexpectedly invalidated'));
  let tree;
  t.after(() => { tree?.unmount(); api.close(); });
  await act(async () => {
    tree = create(h(WorkspaceApiContext.Provider, { value: api }, h(Memory)));
  });

  const button = (label, root = tree.root) => root.findAllByType('button').find(node => node.children.join('') === label);
  const click = (label, root) => act(async () => button(label, root).props.onClick());
  const change = text => act(async () => tree.root.findByType('textarea').props.onChange({ target: { value: text } }));
  const submit = () => act(async () => tree.root.findByType('form').props.onSubmit({ preventDefault() {} }));
  const cards = () => tree.root.findByType(MemoryTimeline).findAllByType('article');
  const content = () => JSON.stringify(tree.toJSON());

  assert.equal(cards().length, 1);
  assert.match(content(), /已有经历/);
  assert.match(content(), /本人陈述/);
  assert.match(content(), /已确认/);
  assert.doesNotMatch(content(), /从对话提取候选记忆|题库审核反馈/);

  await change('  新增经历  ');
  await submit();
  assert.equal(cards().length, 2);
  assert.deepEqual(requests.at(-1).body, { text: '新增经历', source: 'user_statement', confirmed: true });

  await click('修改', cards()[0]);
  assert.equal(tree.root.findByType('textarea').props.value, '新增经历');
  await change('修改后的经历');
  await submit();
  assert.match(content(), /修改后的经历/);
  assert.deepEqual(requests.at(-1).body, { text: '修改后的经历', source: 'user_statement', confirmed: true });

  await click('删除', cards()[0]);
  await click('取消', cards()[0]);
  assert.equal(requests.at(-1).method, 'PUT');
  await click('删除', cards()[0]);
  await click('确认删除这段经历', cards()[0]);
  await click('刷新记忆');
  assert.equal(cards().length, 1);
  assert.doesNotMatch(content(), /修改后的经历/);
  assert.deepEqual(requests.map(request => request.method), ['GET', 'POST', 'PUT', 'DELETE', 'GET']);
});
