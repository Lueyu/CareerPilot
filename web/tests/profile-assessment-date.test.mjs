import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ProfileDetail from '../src/pages/ProfileDetail.tsx';

for (const [assessedAt, visibleDate] of [
  ['2025-09-18T12:30:00+00:00', '2025-09-18'],
  [null, '—'],
]) {
  test(`capability details display last assessment as ${visibleDate}`, async t => {
    const previousFetch = globalThis.fetch;
    t.after(() => { globalThis.fetch = previousFetch; });
    const calls = [];
    globalThis.fetch = async (url, options) => {
      calls.push([options.method, new URL(url).pathname]);
      return Response.json({
        skill: { key: 'tools', name: 'Tool use' },
        state: { level: null, confidence: 0, status: 'UNKNOWN', evidence_coverage: 0, last_assessed_at: assessedAt },
        evidence: [],
      });
    };

    let tree;
    t.after(async () => { await act(async () => tree.unmount()); });
    await act(async () => {
      tree = create(React.createElement(MemoryRouter, {
        initialEntries: ['/profile/tools'],
        future: { v7_startTransition: true, v7_relativeSplatPath: true },
      }, React.createElement(Routes, null,
        React.createElement(Route, { path: '/profile/:domain', element: React.createElement(ProfileDetail) }),
      )));
    });

    const paragraphs = tree.root.findAllByType('p').map(node => node.children.join(''));
    assert.ok(paragraphs.some(text => text.startsWith(`最后评估 ${visibleDate}。`)));
    assert.deepEqual(calls, [['GET', '/api/v1/profile/tools']]);
  });
}
