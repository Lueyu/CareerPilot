import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Dashboard from '../src/pages/Dashboard.tsx';
import Onboarding from '../src/pages/Onboarding.tsx';

const h = React.createElement;

async function renderEntry(t, { path, configured, demo = false }) {
  const fetchBefore = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, { method }) => {
    const endpoint = new URL(url).pathname.replace('/api/v1', '');
    requests.push([method, endpoint]);
    if (endpoint === '/settings/llm/readiness') return Response.json({ configured });
    if (endpoint === '/dashboard') return Response.json({
      workspace: { name: 'Fixture workspace', is_demo: demo },
      recent_activity: [],
    });
    throw new Error('Unexpected request: ' + endpoint);
  };
  t.after(() => { globalThis.fetch = fetchBefore; });
  let tree;
  await act(async () => {
    tree = create(h(MemoryRouter, {
      initialEntries: [path],
      future: { v7_startTransition: true, v7_relativeSplatPath: true },
    }, h(Routes, null,
      h(Route, { path: '/onboarding', element: h(Onboarding) }),
      h(Route, { path: '/', element: h(Dashboard) }),
      h(Route, { path: '/exam/diagnosis', element: h('h1', null, 'Diagnosis route') }),
    )));
  });
  t.after(async () => { await act(async () => tree.unmount()); });
  return { tree, requests };
}

test('onboarding offers model setup and still allows entering the homepage', async t => {
  const { tree, requests } = await renderEntry(t, { path: '/onboarding', configured: false });
  assert.match(JSON.stringify(tree.toJSON()), /未配置.*开放题评分/);
  assert.ok(tree.root.findAllByType('a').some(link => link.props.href === '/settings?tab=models'));
  assert.deepEqual(requests, [['GET', '/settings/llm/readiness']]);
  const start = tree.root.findAllByType('button').find(button => button.children.join('').includes('开始体验 CareerPilot'));
  await act(async () => start.props.onClick());
  assert.equal(tree.root.findByType('h1').children.join(''), '把下一步，变得更清晰');
});

test('a configured homepage distinguishes setup from testing and allows direct diagnosis', async t => {
  const { tree, requests } = await renderEntry(t, { path: '/', configured: true });
  const content = JSON.stringify(tree.toJSON());
  assert.match(content, /已配置.*测试连接/);
  assert.doesNotMatch(content, /连接成功|已连接/);
  assert.deepEqual(requests, [['GET', '/dashboard'], ['GET', '/settings/llm/readiness']]);
  const start = tree.root.findAllByType('button').find(button => button.children.join('').includes('开始能力诊断'));
  await act(async () => start.props.onClick());
  assert.equal(tree.root.findByType('h1').children.join(''), 'Diagnosis route');
});

test('Demo homepage keeps diagnosis and skips model readiness', async t => {
  const { tree, requests } = await renderEntry(t, { path: '/', demo: true });
  assert.deepEqual(requests, [['GET', '/dashboard']]);
  const start = tree.root.findAllByType('button').find(button => button.children.join('').includes('开始能力诊断'));
  await act(async () => start.props.onClick());
  assert.equal(tree.root.findByType('h1').children.join(''), 'Diagnosis route');
});
