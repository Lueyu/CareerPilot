import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import DiagnosisReport from '../src/pages/DiagnosisReport.tsx';

const h = React.createElement;
const report = {
  id: 54, title: 'Coverage fixture', status: 'COMPLETED', score: 50,
  total_points: 10, gained_points: 5,
  questions: [1, 2].map(order => ({
    order_no: order, title: `Fixture question ${order}`, skill: 'Fixture skill',
    answer: { points: order === 1 ? 5 : 0, max_points: 5, verdict: order === 1 ? 'CORRECT' : 'WRONG',
      feedback: {}, evidence_refs: [`EV-fixture-${order}`] },
  })),
};

for (const missing of [[2], []]) {
  test(`deep report shows ${missing.length ? 'partial' : 'complete'} coverage after generation and reload`, async t => {
    const deep = {
      conclusion: 'Fixture conclusion', weaknesses: [], strengths: [], actions: [],
      per_question: { 1: { why: 'Reason 1', how: 'Advice 1' } },
      coverage: { total: 2, explained: 2 - missing.length, missing_order_nos: missing },
    };
    if (!missing.length) deep.per_question[2] = { why: 'Reason 2', how: 'Advice 2' };
    const storage = { getItem: () => null, removeItem: () => {} };
    const descriptors = Object.fromEntries(['localStorage', 'sessionStorage'].map(
      name => [name, Object.getOwnPropertyDescriptor(globalThis, name)],
    ));
    for (const name of Object.keys(descriptors)) {
      Object.defineProperty(globalThis, name, { configurable: true, value: storage });
    }
    t.after(() => {
      for (const [name, descriptor] of Object.entries(descriptors)) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor);
        else delete globalThis[name];
      }
    });
    let stored;
    let posts = 0;
    t.mock.method(globalThis, 'fetch', async (url, options) => {
      if (url.endsWith('/exams/54/report/deep') && options.method === 'POST') {
        posts++;
        stored = deep;
        return Response.json(stored);
      }
      assert.ok(url.endsWith('/exams/54/report'), url);
      return Response.json({ ...report, diagnosis_deep: stored });
    });
    let tree;
    t.after(() => act(async () => tree?.unmount()));
    const mount = () => act(async () => {
      tree?.unmount();
      tree = create(h(MemoryRouter, {
        initialEntries: ['/exam/diagnosis/report?id=54'],
        future: { v7_startTransition: true, v7_relativeSplatPath: true },
      }, h(DiagnosisReport)));
    });
    function checkCoverage() {
      const output = JSON.stringify(tree.toJSON());
      assert.ok(output.includes(`已解释 ${2 - missing.length}/2 题`));
      if (missing.length) {
        assert.match(output, /未生成讲解/, 'Show which questions lack explanations');
        const status = tree.root.findByProps({ role: 'status' });
        assert.match(JSON.stringify(status.children), /Q2/);
      } else {
        assert.match(output, /逐题讲解完整/);
        assert.doesNotMatch(output, /未生成讲解/);
      }
      assert.match(output, /EV-fixture-2/);
    }
    await mount();
    const generate = tree.root.findAllByType('button').find(
      button => button.children.join('').includes('生成深度诊断'),
    );
    await act(async () => generate.props.onClick());
    checkCoverage();
    await mount();
    checkCoverage();
    assert.equal(posts, 1);
  });
}
