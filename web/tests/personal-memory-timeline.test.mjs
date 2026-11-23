import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../src/api/client.ts';
import { AppStateProvider } from '../src/components/AppState.tsx';
import MemoryTimeline from '../src/components/MemoryTimeline.tsx';
import Memory from '../src/pages/Memory.tsx';

test('personal memory keeps its review filter and history in the shared timeline', async t => {
  const base = { source: 'USER', created_at: '2026-11-24T08:00:00Z' };
  const items = [
    { ...base, id: 1, mtype: 'BankReviewFeedback', status: 'ACTIVE', eligible_for_bank_generation: true, content: '审核反馈' },
    { ...base, id: 2, mtype: 'Preference', status: 'SUPERSEDED', superseded_by: 3, content: '旧偏好' },
  ];
  t.mock.method(api, 'get', async path => {
    if (path === '/memory') return { items };
    assert.equal(path, '/memory-candidates');
    return { items: [] };
  });

  const h = React.createElement;
  let tree;
  t.after(() => tree?.unmount());
  await act(async () => {
    tree = create(h(MemoryRouter, {
      initialEntries: ['/memory?type=bank-review'],
      future: { v7_startTransition: true, v7_relativeSplatPath: true },
    }, h(AppStateProvider, null, h(Memory))));
  });
  const cards = () => tree.root.findByType(MemoryTimeline).findAllByType('article');

  assert.equal(cards().length, 1);
  assert.match(JSON.stringify(tree.toJSON()), /仅题库补题使用/);
  assert.match(JSON.stringify(tree.toJSON()), /从对话提取候选记忆/);
  await act(async () => tree.root.findByType('select').props.onChange({ target: { value: '' } }));
  assert.equal(cards().length, 2);
  assert.match(JSON.stringify(tree.toJSON()), /历史版本/);
  assert.equal(cards()[1].findByProps({ className: 'hint' }).children.join(''), '已被记忆 #3 替代。');
});
