import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom';
import Dashboard from '../src/pages/Dashboard.tsx';

const due = {
  id: 42,
  title: 'Explain retry boundaries again',
  due_date: '2026-11-20',
  parent_id: 17,
  parent_title: 'Study retry boundaries',
};

function TaskRoute() {
  const { id } = useParams();
  return React.createElement('p', null, `Task ${id}`);
}

test('the homepage action opens the due retest and the card links back to its source', async t => {
  const previousFetch = globalThis.fetch;
  t.after(() => { globalThis.fetch = previousFetch; });
  globalThis.fetch = async () => Response.json({
    workspace: { name: 'Fixture workspace' },
    stats: { evidence_total: 0, pending_skills: 0, today_tasks: 0, pending_imports: 0 },
    domains: [],
    today_tasks: [],
    due_retests: [due],
  });

  let tree;
  t.after(async () => { await act(async () => tree.unmount()); });
  await act(async () => {
    tree = create(React.createElement(MemoryRouter, {
      future: { v7_startTransition: true, v7_relativeSplatPath: true },
    }, React.createElement(Routes, null,
      React.createElement(Route, { path: '/', element: React.createElement(Dashboard) }),
      React.createElement(Route, { path: '/learning/task/:id', element: React.createElement(TaskRoute) }),
    )));
  });

  const primary = tree.root.findByProps({ className: 'btn btn-primary' });
  assert.match(primary.children.join(''), /到期.*复测/);
  const links = tree.root.findAllByType('a');
  assert.ok(links.some(link => link.props.href === '/learning/task/42' && link.children.includes(due.title)));
  assert.ok(links.some(link => link.props.href === '/learning/task/17' && link.children.includes(due.parent_title)));
  await act(async () => primary.props.onClick());
  assert.equal(tree.toJSON().children.join(''), 'Task 42');
});
