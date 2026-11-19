import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import ProfileDetail from '../src/pages/ProfileDetail.tsx';
import DiagnosisReport from '../src/pages/DiagnosisReport.tsx';

const h = React.createElement;
const reportUrl = '/exam/diagnosis/report?id=91#exam-question-2';
const profile = {
  skill: { name: 'Agent 工程' },
  state: { status: 'UNKNOWN', level: null, confidence: null, evidence_coverage: 1 },
  evidence: [
    { id: 1, title: '第 2 题观察', source_ref: 'exam:91#q2', observation: { answer_excerpt: '原答摘录' } },
    { id: 2, title: '旧观察', observation: {} },
    { id: 3, title: '旧报告引用', source_ref: 'exam:91', observation: {} },
    { id: 4, title: '无效题号', source_ref: 'exam:91#q0', observation: {} },
  ],
};
const report = {
  id: 91,
  title: '原始答卷',
  status: 'COMPLETED',
  questions: [1, 2].map(order => ({
    order_no: order,
    title: `题目 ${order}`,
    frozen: { rubric: { max_points: 4, criteria: [{ title: `冻结评分项 ${order}` }] } },
    answer: { points: 3, max_points: 4, verdict: 'PARTIAL', text_answer: `完整原答 ${order}` },
  })),
};

test('profile evidence opens the saved answer and rubric at its question on navigation and reload', async t => {
  const storage = { getItem: () => null, removeItem: () => {} };
  t.mock.method(globalThis, 'fetch', async url => {
    if (url.endsWith('/profile/agent')) return Response.json(profile);
    assert.ok(url.endsWith('/exams/91/report'), url);
    return Response.json(report);
  });
  const originalGlobals = Object.fromEntries(['localStorage', 'sessionStorage', 'document'].map(
    name => [name, Object.getOwnPropertyDescriptor(globalThis, name)],
  ));
  const scrolled = [];
  Object.defineProperties(globalThis, {
    localStorage: { configurable: true, value: storage },
    sessionStorage: { configurable: true, value: storage },
    document: { configurable: true, value: {
      getElementById: id => ({ scrollIntoView: () => scrolled.push(id) }),
    } },
  });
  let tree;
  t.after(async () => {
    await act(async () => tree?.unmount());
    for (const [name, descriptor] of Object.entries(originalGlobals)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  });

  function mount(url) {
    return act(async () => {
      tree?.unmount();
      tree = create(h(MemoryRouter, {
        initialEntries: [url], future: { v7_startTransition: true, v7_relativeSplatPath: true },
      }, h(Routes, null,
        h(Route, { path: '/profile/:domain', element: h(ProfileDetail) }),
        h(Route, { path: '/exam/diagnosis/report', element: h(DiagnosisReport) }),
      )));
    });
  }

  function checkQuestion() {
    const question = tree.root.findByProps({ id: 'exam-question-2' });
    assert.equal(question.findByProps({ role: 'button' }).props['aria-expanded'], true);
    const output = JSON.stringify(tree.toJSON());
    assert.match(output, /完整原答 2/);
    assert.match(output, /冻结评分项 2/);
    assert.equal(scrolled.at(-1), 'exam-question-2');
  }

  await mount('/profile/agent');
  const links = tree.root.findAllByType('a');
  assert.deepEqual(links.map(link => link.props.href), [reportUrl]);
  await act(async () => links[0].props.onClick({ button: 0, preventDefault() {} }));
  checkQuestion();

  scrolled.length = 0;
  await mount(reportUrl);
  checkQuestion();
});
