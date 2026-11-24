import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import Workshop from '../src/pages/QuestionWorkshop.tsx';
import { getLocale, setLocale } from '../src/i18n.ts';

test('workshop refreshes suggestions in the active language and keeps an unsaved goal', async () => {
  const previous = getLocale();
  const requests = [];
  const resourceCalls = [];
  const savedFetch = globalThis.fetch;
  globalThis.fetch = (url, options) => {
    const path = new URL(url).pathname;
    if (!path.endsWith('/suggestions')) {
      resourceCalls.push(path);
      return Promise.resolve(Response.json({items: []}));
    }
    return new Promise(resolve => requests.push({
      language: options.headers['Accept-Language'],
      reply: (title, reason, purpose) => resolve(Response.json({items: [{
        id: 'coverage-rag', title, reason, purpose, kind: 'supplement', count: 5,
        skill_keys: ['rag'], project_ids: [],
      }]})),
    }));
  };
  let tree;
  const rendered = () => JSON.stringify(tree.toJSON());
  const goal = label => tree.root.findByProps({'aria-label': label});
  try {
    setLocale('zh-CN');
    await act(async () => {
      tree = create(React.createElement(MemoryRouter, {
        initialEntries: ['/bank/workshop'],
        future: { v7_startTransition: true, v7_relativeSplatPath: true },
      }, React.createElement(Workshop)));
    });
    assert.equal(requests[0].language, 'zh-CN');
    await act(async () => requests[0].reply('旧的补题建议', '覆盖不足', '旧补题目标'));
    assert.match(rendered(), /旧的补题建议/);
    const unsaved = 'Unsaved goal from the fixture';
    await act(async () => goal('补题目标').props.onChange({target: {value: unsaved}}));

    await act(async () => setLocale('en'));
    assert.doesNotMatch(rendered(), /旧的补题建议/);
    assert.equal(goal('Goal for new questions').props.value, unsaved);
    assert.equal(requests[1].language, 'en');

    await act(async () => setLocale('zh-CN'));
    assert.equal(requests[2].language, 'zh-CN');
    await act(async () => requests[2].reply('当前补题建议', '当前建议理由', '当前补题目标'));
    await act(async () => requests[1].reply('Stale English suggestion', 'Stale reason', 'Stale goal'));
    assert.match(rendered(), /当前补题建议/);
    assert.doesNotMatch(rendered(), /Stale English suggestion/);
    assert.equal(goal('补题目标').props.value, unsaved);

    await act(async () => setLocale('en'));
    assert.equal(requests[3].language, 'en');
    await act(async () => requests[3].reply('Current English suggestion', 'Current reason', 'Current goal'));
    assert.match(rendered(), /Current English suggestion.*Current reason/);
    assert.doesNotMatch(rendered(), /当前补题建议/);
    assert.equal(goal('Goal for new questions').props.value, unsaved);
    const apply = tree.root.findAllByType('button').find(button => button.children.includes('Use this suggestion'));
    await act(async () => apply.props.onClick());
    assert.equal(goal('Goal for new questions').props.value, 'Current goal');

    await act(async () => setLocale('zh-CN'));
    await act(async () => requests[4].reply('已切换语言的补题建议', '建议理由', '中文目标'));
    assert.match(tree.root.findByProps({role: 'status'}).children.join(''), /已选择「已切换语言的补题建议」/);
    assert.equal(goal('补题目标').props.value, 'Current goal');
    assert.deepEqual(resourceCalls.sort(), ['/api/v1/projects', '/api/v1/question-generation']);
  } finally {
    if (tree) await act(async () => tree.unmount());
    globalThis.fetch = savedFetch;
    setLocale(previous);
  }
});
