import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import ExamCenter from '../src/pages/ExamCenter.tsx';

const h = React.createElement;

function Destination() {
  const location = useLocation();
  return h('div', null, location.pathname + location.search);
}

test('older history can be retried without losing loaded exams and opens the oldest report', async t => {
  const history = Array.from({ length: 25 }, (_, index) => ({
    id: 25 - index, title: `History ${25 - index}`, kind: 'diagnosis',
    status: index === 0 ? 'ACTIVE' : 'COMPLETED', score: index === 0 ? null : 80,
  }));
  const kinds = [{ kind: 'diagnosis', title: '独立诊断', default_minutes: 30, history_count: 25 }];
  let attempts = 0;
  t.mock.method(globalThis, 'fetch', async url => {
    const path = new URL(url);
    assert.equal(path.pathname, '/api/v1/exams/history');
    if (!path.search) {
      return Response.json({ history: history.slice(0, 20), kinds, total_count: 25, next_cursor: 6 });
    }
    assert.equal(path.search, '?before_id=6');
    if (++attempts === 1) return Response.json({ detail: '暂时不可用' }, { status: 503 });
    return Response.json({ history: history.slice(20), kinds, total_count: 25, next_cursor: null });
  });

  let tree;
  t.after(() => act(async () => tree?.unmount()));
  await act(async () => {
    tree = create(h(MemoryRouter, { initialEntries: ['/exam'], future: { v7_startTransition: true, v7_relativeSplatPath: true } },
      h(Routes, null,
        h(Route, { path: '/exam', element: h(ExamCenter) }),
        h(Route, { path: '/exam/diagnosis/report', element: h(Destination) }),
      )));
  });
  const rows = () => tree.root.findByType('tbody').findAllByType('tr');
  const loadMore = () => tree.root.findAllByType('button').find(button => button.children.includes('加载更早记录'));
  const content = () => JSON.stringify(tree.toJSON());

  assert.equal(rows().length, 20);
  assert.match(content(), /20 \/ 25/);
  assert.match(content(), /25 次记录/);
  await act(async () => loadMore().props.onClick());
  assert.equal(rows().length, 20);
  assert.match(content(), /暂时不可用/);

  await act(async () => loadMore().props.onClick());
  assert.equal(attempts, 2);
  assert.doesNotMatch(content(), /暂时不可用/);
  assert.match(content(), /25 \/ 25/);
  assert.equal(loadMore(), undefined);
  assert.deepEqual(rows().map(row => row.findAllByType('td')[0].children[0]), history.map(exam => exam.title));

  await act(async () => rows().at(-1).findByType('button').props.onClick());
  assert.match(content(), /\/exam\/diagnosis\/report\?id=1/);
});
