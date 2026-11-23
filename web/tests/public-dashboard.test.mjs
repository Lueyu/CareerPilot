import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import Dashboard from '../src/seed/Dashboard.tsx';
import { createWorkspaceApi, WorkspaceApiContext } from '../src/seed/api.ts';

test('registered dashboard fetches the scoped summary and links to saved practice', async () => {
  const interview = { id: 17, mode: 'interview', title: '系统设计练习', updated_at: '2026-11-24T00:00:00Z' };
  const study = { id: 29, mode: 'study', title: '学习缓存', updated_at: interview.updated_at };
  const summary = {
    selected_role: '平台工程师', role_count: 2, question_count: 57, import_count: 4,
    pending_import_count: 1, practice_count: 8, latest_activity: interview,
    resume_interview: interview, resume_study: study,
    pending_imports: [{ id: 3, filename: '面经.md', created_at: interview.updated_at }],
  };
  const previousFetch = globalThis.fetch;
  const api = createWorkspaceApi(42, () => assert.fail('Unexpected session invalidation'));
  const calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push([path, options.headers.get('X-Expected-Workspace')]);
    return new Response(JSON.stringify(summary));
  };
  let tree;
  try {
    await act(async () => {
      tree = create(createElement(MemoryRouter, { future: { v7_startTransition: true, v7_relativeSplatPath: true } },
        createElement(WorkspaceApiContext.Provider, { value: api }, createElement(Dashboard))));
    });
    assert.deepEqual(calls, [['/api/v1/dashboard', '42']]);
    const text = JSON.stringify(tree.toJSON());
    assert.match(text, /平台工程师/);
    assert.match(text, /面经.md/);
    assert.deepEqual(tree.root.findAllByProps({ className: 'stat-mini-value' }).map(node => node.children.join('')),
      ['2', '57', '4', '8']);
    const destinations = new Set(tree.root.findAllByType('a').map(node => node.props.href));
    assert.deepEqual(destinations, new Set(['/beta/bank', '/beta/exam?thread=17', '/beta/study/chat/29', '/onboarding', '/settings', '/beta/memory']));
  } finally {
    await act(async () => tree?.unmount());
    api.close();
    globalThis.fetch = previousFetch;
  }
});
