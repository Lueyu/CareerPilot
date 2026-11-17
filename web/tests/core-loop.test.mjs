import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { StaticRouter } from 'react-router-dom/server.js';
import { AppStateProvider } from '../src/components/AppState.tsx';
import * as client from '../src/api/client.ts';
import Dashboard from '../src/pages/Dashboard.tsx';
import Profile from '../src/pages/Profile.tsx';
import ProfileDetail from '../src/pages/ProfileDetail.tsx';
import ExamCenter from '../src/pages/ExamCenter.tsx';
import DiagnosisReport from '../src/pages/DiagnosisReport.tsx';
import Learning from '../src/pages/Learning.tsx';
import LearningTask from '../src/pages/LearningTask.tsx';

const render = (Page, path = '/') => renderToStaticMarkup(React.createElement(StaticRouter, { location: path },
  React.createElement(AppStateProvider, null, React.createElement(Page))));

for (const [name, Page] of Object.entries({ Dashboard, Profile, ProfileDetail, ExamCenter, DiagnosisReport, Learning, LearningTask })) {
  test(`${name} never substitutes demo evidence while real data is unavailable`, () => {
    const html = render(Page);
    assert.doesNotMatch(html, /DEMO|EV-041|2\.7\/5|所有已评估能力都达到|无重大缺口|让工具安全地重试|显式状态/);
  });
}

test('API requests default to the personal workspace', async () => {
  const saved = globalThis.fetch;
  let workspace;
  globalThis.fetch = async (_url, options) => { workspace = options.headers['X-Workspace']; return new Response('{}'); };
  try { await client.api.get('/dashboard'); assert.equal(workspace, 'personal'); }
  finally { globalThis.fetch = saved; }
});

test('workspace export shares the selected workspace with JSON requests', async () => {
  const savedFetch = globalThis.fetch;
  const savedStorage = globalThis.localStorage;
  const values = new Map();
  globalThis.localStorage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) };
  const workspaces = [];
  globalThis.fetch = async (_url, options) => { workspaces.push(options.headers['X-Workspace']); return new Response('{}'); };
  try {
    client.selectWorkspace('actual-selection');
    await client.api.get('/dashboard'); await client.downloadWorkspaceExport();
    assert.deepEqual(workspaces, ['actual-selection', 'actual-selection']);
  } finally { globalThis.fetch = savedFetch; globalThis.localStorage = savedStorage; }
});

test('unknown values remain unknown instead of a numeric zero', async () => {
  const { SegBars } = await import('../src/components/ui.tsx');
  const html = renderToStaticMarkup(React.createElement(SegBars, { items: [{ name: 'RAG', current: null, weight: 20 }] }));
  assert.match(html, /未知/);
  assert.doesNotMatch(html, />0\.0</);
});

test('heuristic confidence describes observation coverage rather than scoring reliability', async () => {
  const { plainConfidence } = await import('../src/components/ui.tsx');
  for (const value of [0, 0.15, 0.3, 0.45]) {
    assert.match(plainConfidence(value), /观察覆盖较少/);
  }
  for (const value of [0.5, 0.7, 0.75, 0.99, 1]) {
    assert.match(plainConfidence(value), /已有更多独立观察/);
    assert.doesNotMatch(plainConfidence(value), /可靠|把握|准确率|置信|概率/);
  }
  for (const value of [null, undefined, Number.NaN]) assert.match(plainConfidence(value), /覆盖未知/);
});

async function core() {
  try { return await import('../src/lib/coreLoop.ts'); }
  catch (error) { assert.fail(`Core loop behavior is missing: ${error.message}`); }
}

const exam = { id: 42, questions: [{ order_no: 7, qtype: 'mcq' }, { order_no: 9, qtype: 'open' }] };

test('failed atomic submission retains the callers final answers', async () => {
  const { submitExam } = await core();
  const answers = [1, 'my actual answer'];
  const calls = [];
  const transport = { post: async (path) => { calls.push(path); throw new Error('save unavailable'); } };
  await assert.rejects(submitExam(exam, answers, transport), /save unavailable/);
  assert.deepEqual(answers, [1, 'my actual answer']);
  assert.deepEqual(calls, ['/exams/42/submit']);
});

test('all final answers and revisions are submitted atomically using their own question type', async () => {
  const { submitExam } = await core();
  const calls = [];
  const transport = { post: async (path, body) => { calls.push([path, body]); return {}; } };
  await submitExam(exam, [2, 'new text'], transport, [2,4]);
  assert.deepEqual(calls, [
    ['/exams/42/submit', {answers:[{order_no:7,choice_index:2,expected_revision:2},{order_no:9,text_answer:'new text',expected_revision:4}]}],
  ]);
});

test('loading an explicit exam never creates or substitutes another exam', async () => {
  const { loadOrCreateExam } = await core();
  const calls = [];
  const transport = { get: async (path) => { calls.push(path); throw new Error('exam unavailable'); }, post: async () => assert.fail('must not create') };
  await assert.rejects(loadOrCreateExam('77', transport, new Map(), 'personal'), /exam unavailable/);
  assert.deepEqual(calls, ['/exams/77']);
});

test('concurrent creation uses the same persisted idempotency key', async () => {
  const { loadOrCreateExam } = await core();
  const stored = new Map();
  const storage = { getItem: key => stored.get(key), setItem: (key, value) => stored.set(key, value), removeItem: key => stored.delete(key) };
  const bodies = [];
  const transport = { get: async () => ({ history: [] }), post: async (_path, body) => { bodies.push(body); return { id: 45 }; } };
  await Promise.all([loadOrCreateExam(null, transport, storage, 'personal'), loadOrCreateExam(null, transport, storage, 'personal')]);
  assert.equal(bodies.length, 2);
  assert.ok(bodies[0].idempotency_key);
  assert.equal(bodies[0].idempotency_key, bodies[1].idempotency_key);
});
