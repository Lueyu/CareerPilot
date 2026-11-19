import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ProjectReview from '../src/pages/ProjectReview.tsx';

const h = React.createElement;
const project = { id: 7, name: 'Saved repository', owner: 'example', repo: 'saved', kind: 'github', archived_at: null };
const secondProject = { ...project, id: 8, name: 'Another visible repository' };
const report = {
  project_id: 7, project, latest_run: null, worker: { online: true }, allowed_actions: [],
  report: {
    id: 10, run_id: 10, is_latest_valid: true, freshness: 'CURRENT', coverage_status: 'COMPLETE',
    report: {
      summary: 'Saved report summary',
      architecture: [{ title: 'Saved architecture detail', detail: 'Frozen technical evidence', citations: [] }],
      issues: [], unknowns: [],
    },
    active_issue_ids: [],
  },
};
const content = tree => JSON.stringify(tree.toJSON());
const click = node => act(async () => node.props.onClick({ button: 0, preventDefault() {} }));

async function mount(t, url) {
  let tree;
  t.after(() => act(async () => tree?.unmount()));
  await act(async () => {
    tree = create(h(MemoryRouter, {
      initialEntries: [url], future: { v7_startTransition: true, v7_relativeSplatPath: true },
    }, h(Routes, null, h(Route, { path: '/assets/project/:id', element: h(ProjectReview) }))));
  });
  return tree;
}

test('a saved report and its tabs stay available while the project list fails and retries', async t => {
  let listAvailable = false;
  const requests = [];
  t.mock.method(globalThis, 'fetch', async url => {
    const path = new URL(url).pathname;
    requests.push(path);
    if (path.endsWith('/projects')) {
      return listAvailable ? Response.json({ items: [project, secondProject] })
        : Response.json({ detail: 'Picker unavailable' }, { status: 503 });
    }
    if (path.endsWith('/7/report')) return Response.json(report);
    if (path.endsWith('/8/report')) return Response.json({ ...report, project_id: 8, project: secondProject });
    if (path.endsWith('/analyses')) return Response.json({ items: [] });
    assert.fail(`Unexpected request ${path}`);
  });

  const tree = await mount(t, '/assets/project/7?tab=process');
  assert.match(content(tree), /Saved repository/);
  assert.match(content(tree), /Saved report summary/);
  assert.match(content(tree), /Picker unavailable/);
  const picker = () => tree.root.findByType('select');
  assert.equal(picker().props.value, 7);

  const evidence = tree.root.findAllByType('a').find(link => link.props.href.endsWith('?tab=evidence'));
  await click(evidence);
  assert.match(content(tree), /Saved architecture detail/);
  assert.ok(evidence.props['aria-current'] === 'page');

  listAvailable = true;
  const reportRequests = requests.filter(path => path.endsWith('/7/report')).length;
  const retry = tree.root.findAllByType('button').find(button => button.children.includes('重试加载项目列表'));
  await click(retry);
  assert.equal(requests.filter(path => path.endsWith('/7/report')).length, reportRequests);
  assert.doesNotMatch(content(tree), /Picker unavailable/);
  assert.match(content(tree), /Saved architecture detail/);
  assert.ok(picker().findAllByType('option').some(option => option.props.value === 8));
  await act(async () => picker().props.onChange({ target: { value: '8' } }));
  assert.equal(picker().props.value, 8);
  assert.match(content(tree), /Another visible repository/);
  assert.match(content(tree), /Saved architecture detail/);
});

test('a denied report never opens from a direct project link', async t => {
  const requests = [];
  t.mock.method(globalThis, 'fetch', async url => {
    const path = new URL(url).pathname;
    requests.push(path);
    if (path.endsWith('/projects')) return Response.json({ items: [project] });
    if (path.endsWith('/99/report')) return Response.json({ detail: '项目不存在或已移除' }, { status: 404 });
    assert.fail(`Unexpected request ${path}`);
  });

  const tree = await mount(t, '/assets/project/99?tab=evidence');
  assert.match(content(tree), /项目不存在或已移除/);
  assert.doesNotMatch(content(tree), /Saved repository|Saved architecture detail|项目报告视图/);
  assert.deepEqual(requests.sort(), ['/api/v1/projects', '/api/v1/projects/99/report']);
});
