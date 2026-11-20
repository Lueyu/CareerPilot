import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom';
import ExperiencedBank from '../src/pages/ExperiencedBank.tsx';

const domains = Array.from({ length: 11 }, (_, index) => ({
  key: `domain-${index}`,
  name: `能力域 ${index + 1}`,
  order: index,
  focus: '',
  questions: Array.from({ length: index === 0 ? 27 : 20 }, (_, row) => ({
    id: index * 100 + row + 1,
    short_code: `Q-${index}-${row}`,
    title: `工具题 ${index}-${row}`,
    tags: row === 25 ? ['backoff'] : row === 1 ? ['tool-validation'] : [],
    level: row % 2 ? 'senior' : 'mid',
    source: row % 2 ? 'authored' : 'legacy',
    company: ['字节(真题)', '字节 / 阿里（高频）', '所有公司（通用）', '', null][row % 5],
  })),
}));

async function mount(t, url, responses = [Response.json({ domains })]) {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async url => {
    calls.push(new URL(url).pathname);
    return responses.shift();
  };
  let tree;
  let location;
  let navigate;
  const actions = [];
  function Observe() {
    location = useLocation();
    navigate = useNavigate();
    return null;
  }
  t.after(() => {
    if (tree) act(() => tree.unmount());
    globalThis.fetch = originalFetch;
  });
  await act(async () => {
    tree = create(React.createElement(MemoryRouter, {
      initialEntries: [{ ...url, state: { experiencedPracticeId: 87 } }],
      future: { v7_startTransition: true, v7_relativeSplatPath: true },
    }, React.createElement(Observe), React.createElement(ExperiencedBank, {
      practiceBusy: null,
      practiceError: null,
      onPractice: question => actions.push(['practice', question.id]),
      onReference: (question, domain) => actions.push(['reference', question.id, domain]),
    })));
  });
  return { tree, actions, calls, location: () => location, back: () => act(() => navigate(-1)) };
}

const button = (tree, label) => tree.root.findAllByType('button').find(node =>
  node.children.join('') === label || node.props['aria-label'] === label);
const click = (tree, label) => act(() => button(tree, label).props.onClick());
const change = (node, value) => act(() => node.props.onChange({ currentTarget: { value } }));
const count = tree => tree.root.findAllByProps({ role: 'status' })[0].children.join('');

test('the catalog starts with 227 questions and supports local search, paging, and Back', async t => {
  const { tree, calls, location, back } = await mount(t, { pathname: '/bank' });
  assert.equal(count(tree), '共 227 题');
  assert.equal(tree.root.findAllByType('li').length, 10);
  assert.equal(tree.root.findAllByProps({ className: 'experienced-domain' }).length, 12);
  assert.ok(tree.root.findAllByType('a').some(node => node.props.href?.startsWith('/exam/diagnosis?id=87')));
  click(tree, '下一页');
  assert.equal(location().search, '?page=2');
  const input = tree.root.findByType('input');
  change(input, 'BACK');
  change(input, 'BACKOFF Q-0');
  act(() => tree.root.findByType('form').props.onSubmit({ preventDefault() {} }));
  assert.equal(count(tree), '共 1 题');
  assert.equal(tree.root.findByType('h3').children.join(''), '工具题 0-25');
  assert.equal(new URLSearchParams(location().search).get('page'), null);
  back();
  assert.equal(location().search, '?page=2');
  assert.equal(input.props.value, '');
  assert.deepEqual(calls, ['/api/v1/bank/experienced']);
});

test('the narrow domain picker and desktop sidebar share the same filters', async t => {
  const { tree, calls, location } = await mount(t, { pathname: '/bank', search: '?q=工具题&page=2' });
  const nav = tree.root.findByProps({ 'aria-label': '能力域', className: 'card bank-section experienced-domains' });
  const picker = nav.findByType('select');
  const allDomains = button(tree, '全部分类');
  assert.equal(allDomains.findByType('small').children.join(''), '227');
  change(picker, 'domain-1');
  const params = new URLSearchParams(location().search);
  assert.equal(params.get('q'), '工具题');
  assert.equal(params.get('domain'), 'domain-1');
  assert.equal(params.has('page'), false);
  assert.equal(button(tree, '能力域 2').props['aria-pressed'], true);
  assert.equal(count(tree), '共 20 题');
  assert.equal(allDomains.findByType('small').children.join(''), '227');
  click(tree, '全部分类');
  assert.equal(picker.props.value, '');
  assert.equal(new URLSearchParams(location().search).has('domain'), false);
  assert.equal(count(tree), '共 227 题');
  assert.deepEqual(calls, ['/api/v1/bank/experienced']);
});

test('company aliases, old links, action identity, and clearing combined filters', async t => {
  const { tree, calls, actions, location } = await mount(t, {
    pathname: '/bank', search: '?track=experienced&domain=domain-0&level=senior&source=authored&company=阿里&q=tool-validation&page=999',
  });
  assert.equal(count(tree), '共 1 题');
  click(tree, '筛选');
  const select = tree.root.findByProps({ 'aria-label': '公司' });
  assert.deepEqual(select.findAllByType('option').map(node => node.props.value).sort(), ['', '字节', '阿里'].sort());
  click(tree, '查看参考');
  click(tree, '开始练习');
  assert.deepEqual(actions, [['reference', 2, '能力域 1'], ['practice', 2]]);
  change(select, '字节');
  assert.equal(count(tree), '共 1 题');
  assert.equal(new URLSearchParams(location().search).has('track'), false);
  change(tree.root.findByType('input'), 'no matches');
  assert.equal(count(tree), '共 0 题');
  click(tree, '清除筛选');
  assert.equal(location().search, '');
  assert.equal(count(tree), '共 227 题');
  assert.deepEqual(calls, ['/api/v1/bank/experienced']);
});

test('a failed catalog request can be retried', async t => {
  const { tree, calls } = await mount(t, { pathname: '/bank' }, [
    Response.json({ detail: '服务暂不可用' }, { status: 503 }),
    Response.json({ domains }),
  ]);
  assert.match(tree.root.findByProps({ role: 'alert' }).children.join(''), /服务暂不可用/);
  await act(async () => button(tree, '重试加载题库').props.onClick());
  assert.equal(count(tree), '共 227 题');
  assert.equal(calls.length, 2);
});
