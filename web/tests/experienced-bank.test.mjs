import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import Bank from '../src/pages/Bank.tsx';
import ExamCenter from '../src/pages/ExamCenter.tsx';
import Diagnosis from '../src/pages/Diagnosis.tsx';
import DiagnosisReport from '../src/pages/DiagnosisReport.tsx';
import { AppStateProvider } from '../src/components/AppState.tsx';

const asset = new URL('../src/data/bank.json', import.meta.url);
const generalBank = JSON.parse(await readFile(asset, 'utf8'));
const mid = { id: 3101, short_code: 'EX-TOOLS-M', title: '如何设计工具失败重试？', level: 'mid', tags: ['tools'] };
const senior = { id: 3102, short_code: 'EX-TOOLS-S', title: '如何避免跨服务重试造成重复执行？', level: 'senior', tags: ['tools'], company: 'Example AI' };
const original = generalBank.questions.find(question => question.html?.includes('class="sdiagram"') && question.html.includes('<details'));
const legacy = { id: 161, short_code: original.id, title: original.title, level: 'mid', tags: [], source: 'legacy' };
const toolsDomain = { key: 'tools', name: '工具调用', order: 1, focus: '考察失败分类、超时和幂等。', questions: [mid, senior, legacy] };
const trainingDomain = { key: 'training', name: '算法与模型训练（选修）', order: 11, focus: '考察训练数据与评价指标。', questions: [] };
const otherDomains = Array.from({ length: 9 }, (_, index) => ({
  key: `domain-${index}`, name: `能力域 ${index + 2}`, order: index + 2, focus: '考察工程判断。', questions: [],
}));
const catalog = { domains: [trainingDomain, ...otherDomains, toolsDomain] };
const activeExam = {
  id: 87, title: '社招专项', kind: 'practice', status: 'ACTIVE', duration_minutes: 20, experienced_bank_practice: true,
  questions: [{ order_no: 1, short_code: senior.short_code, title: senior.title, qtype: 'short_answer', options: null, skill: '工具调用' }],
};
const completedReport = {
  id: 87, title: '历史标题', kind: 'practice', status: 'COMPLETED', experienced_bank_practice: true,
  score: 70, total_points: 10, gained_points: 7, questions: [],
  overall: { conclusion: '此能力结论不应出现在练习报告', strong: [], mid: [], weak: [] },
};
const interviewSet = { id: 'set-001', import_id: 22, title: '隔离验收面经集', project_id: 1, project_name: '测试项目', questions: [] };
const review = {
  id: 22, short_code: 'IMP-22', status: 'REVIEW',
  items: [{
    id: 19, title: '被深链选中的待审核题', status: 'DRAFT', qtype: 'short_answer', topic: 'rag',
    draft_answer: '<p>测试私有答案</p>',
    draft_rubric: { points: [{ title: '测试私有评分项' }] },
  }],
};

const rendered = tree => JSON.stringify(tree.toJSON());
const buttons = tree => tree.root.findAllByType('button');
const labelOf = node => node.children.filter(child => typeof child === 'string').join('');
const button = (tree, label) => {
  const match = buttons(tree).find(node => (node.props['aria-label'] ?? labelOf(node)) === label);
  assert.ok(match, `missing button: ${label}`);
  return match;
};
const click = async (tree, label) => {
  await act(async () => { await button(tree, label).props.onClick(); });
};
const flush = () => new Promise(resolve => setImmediate(resolve));

async function mount(t, path, respond) {
  const originalFetch = globalThis.fetch;
  const originalWindow = globalThis.window;
  const originalStorage = globalThis.localStorage;
  const originalSessionStorage = globalThis.sessionStorage;
  const storage = new Map();
  globalThis.localStorage = globalThis.sessionStorage = {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: key => storage.delete(key),
  };
  globalThis.window = { location: { search: '' }, addEventListener() {}, removeEventListener() {} };
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const address = new URL(url);
    const endpoint = address.pathname.replace(/^\/api\/v1/, '');
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ endpoint, search: address.searchParams, body, method: options.method ?? 'GET' });
    if (address.href === asset.href) return Response.json(generalBank);
    const response = respond?.(endpoint, body);
    if (response) return response;
    if (endpoint === '/bank') return Response.json({ items: [], total: 0 });
    if (endpoint === '/bank/imports/pending') return Response.json({ imports: [review] });
    if (endpoint === '/bank/interview-sets') return Response.json({ items: [interviewSet] });
    if (endpoint === '/bank/experienced') return Response.json(catalog);
    if (endpoint === '/exams/87') return Response.json(activeExam);
    if (endpoint === '/exams/87/report') return Response.json(completedReport);
    return Response.json({ detail: 'Unexpected test endpoint' }, { status: 404 });
  };

  let tree;
  let location;
  let navigate;
  function RouterObserver() {
    location = useLocation();
    navigate = useNavigate();
    return null;
  }
  t.after(async () => {
    if (tree) await act(async () => tree.unmount());
    globalThis.fetch = originalFetch;
    globalThis.window = originalWindow;
    globalThis.localStorage = originalStorage;
    globalThis.sessionStorage = originalSessionStorage;
  });
  await act(async () => {
    tree = create(React.createElement(MemoryRouter, {
      initialEntries: [path], future: { v7_startTransition: true, v7_relativeSplatPath: true },
    }, React.createElement(AppStateProvider, null, React.createElement(RouterObserver),
      React.createElement(Routes, null,
        React.createElement(Route, { path: '/exam', element: React.createElement(ExamCenter) }),
        React.createElement(Route, { path: '/bank', element: React.createElement(Bank) }),
        React.createElement(Route, { path: '/exam/diagnosis', element: React.createElement(Diagnosis) }),
        React.createElement(Route, { path: '/exam/diagnosis/report', element: React.createElement(DiagnosisReport) }),
        React.createElement(Route, { path: '/study/chat/:id', element: React.createElement('div') }),
      ),
    )));
    await flush();
  });
  return { tree, calls, location: () => location, navigate: path => navigate(path) };
}

test('the exam center opens the experienced question catalog directly', async t => {
  const { tree, location } = await mount(t, '/exam', endpoint =>
    endpoint === '/exams' ? Response.json({ kinds: [], history: [] }) : undefined);
  const entry = tree.root.findAllByType('a').find(node => labelOf(node) === '查看题库');
  assert.equal(entry?.props.href, '/bank');
  assert.equal(tree.root.findAllByType('a').filter(node => node.props.href?.startsWith('/bank')).length, 1);
  await act(async () => { entry.props.onClick({ button: 0, preventDefault() {} }); await flush(); });
  assert.equal(location().pathname + location().search, '/bank');
  assert.ok(rendered(tree).includes(mid.title));
});

test('the legacy route canonicalizes and opens all levels, sources and domains by default', async t => {
  const { tree, calls, location } = await mount(t, '/bank?track=experienced');
  assert.equal(location().search, '');
  const domains = tree.root.findAllByProps({ className: 'experienced-domain' });
  assert.equal(domains.length, 12);
  const allDomains = button(tree, '全部分类');
  assert.ok(allDomains.props['aria-pressed']);
  assert.equal(allDomains.findByType('small').children.join(''), '3');
  assert.equal(domains[1].props['aria-label'], toolsDomain.name);
  assert.equal(domains.at(-1).props['aria-label'], trainingDomain.name);
  assert.match(rendered(tree), /题目列表/);
  assert.ok(rendered(tree).includes(legacy.title));
  assert.ok(rendered(tree).includes(mid.title));
  assert.ok(rendered(tree).includes(senior.title));
  assert.equal(button(tree, '筛选').props['aria-expanded'], false);
  await click(tree, '筛选');
  async function select(label, value) {
    const control = tree.root.findAllByType('select').find(node => node.props['aria-label'] === label);
    await act(async () => control.props.onChange({ currentTarget: { value } }));
  }
  await select('题目来源', 'legacy');
  assert.ok(rendered(tree).includes(legacy.title));
  assert.ok(!rendered(tree).includes(mid.title));
  await select('题目来源', 'authored');
  await select('建议年限', 'senior');
  assert.ok(!rendered(tree).includes(legacy.title));
  assert.ok(!rendered(tree).includes(mid.title));
  assert.ok(rendered(tree).includes(senior.title));
  await act(async () => domains.at(-1).props.onClick());
  assert.ok(!rendered(tree).includes(senior.title));
  await click(tree, '清除筛选');
  assert.ok(rendered(tree).includes(senior.title));
  assert.equal(location().search, '');
  assert.deepEqual(calls.map(({endpoint}) => endpoint), ['/bank/experienced']);
});

test('a failed catalog read can be retried and does not fall back to bundled content', async t => {
  let attempt = 0;
  const { tree } = await mount(t, '/bank?track=experienced', endpoint => {
    if (endpoint !== '/bank/experienced') return;
    if (++attempt === 1) return Response.json({ detail: 'catalog unavailable' }, { status: 503 });
    if (attempt === 2) return Response.json({ id: 42, answer_html: 'answer should stay hidden' });
    return Response.json(catalog);
  });
  assert.match(rendered(tree), /catalog unavailable/);
  await click(tree, '重试加载题库');
  assert.match(rendered(tree), /题单格式异常/);
  assert.ok(!rendered(tree).includes('answer should stay hidden'));
  await click(tree, '重试加载题库');
  assert.ok(rendered(tree).includes(mid.title));
});

test('practice starts with the DB ID, retries with the same request key and never reads a reference', async t => {
  let attempt = 0;
  const { tree, calls, location } = await mount(t, '/bank?track=experienced', endpoint => {
    if (endpoint !== '/exams') return;
    if (++attempt === 1) return Response.json({ detail: 'question unavailable' }, { status: 404 });
    return Response.json({ id: 87 });
  });
  assert.deepEqual(calls.map(({endpoint}) => endpoint), ['/bank/experienced']);
  await click(tree, '开始练习');
  assert.match(rendered(tree), /question unavailable/);
  await click(tree, '重试练习');
  assert.deepEqual(calls.map(({endpoint}) => endpoint), ['/bank/experienced', '/exams', '/exams', '/exams/87']);
  assert.deepEqual(calls[1].body.question_ids, [mid.id]);
  assert.ok(calls[1].body.idempotency_key);
  assert.equal(calls[1].body.experienced_bank, true);
  assert.deepEqual(calls[1].body, calls[2].body);
  assert.equal(location().pathname + location().search, '/exam/diagnosis?id=87&from=bank');
});

test('reference opens on explicit request and only shows the server answer after a successful read', async t => {
  let attempt = 0;
  const { tree, calls } = await mount(t, '/bank?track=experienced', endpoint => {
    if (endpoint !== `/bank/${mid.short_code}`) return;
    if (++attempt === 1) return Response.json({ detail: 'exposure unavailable' }, { status: 503 });
    return Response.json({ id: mid.id, answer_html: '<p>本次参考来自服务端。</p>' });
  });
  await click(tree, '查看参考');
  assert.match(rendered(tree), /辅助记录未确认/);
  assert.equal(tree.root.findAllByProps({ className: 'bank-answer' }).length, 0);
  await click(tree, '重新读取参考');
  assert.match(rendered(tree), /本次参考来自服务端/);
  assert.match(rendered(tree), /读取参考会记录这道题的辅助条件/);
  assert.deepEqual(calls.map(({endpoint}) => endpoint), ['/bank/experienced', `/bank/${mid.short_code}`, `/bank/${mid.short_code}`]);
});

test('returning to the filtered list saves the draft and resumes the same practice without creating another exam', async t => {
  let saved = null;
  const { tree, calls, location } = await mount(t, '/bank?level=senior&domain=tools&source=authored&q=重复&company=Example+AI', (endpoint, body) => {
    if (endpoint === '/exams') return Response.json({ id: activeExam.id });
    if (endpoint === '/exams/87/answers') {
      saved = body.text_answer;
      return Response.json({ ok: true, revision: 1, saved_at: '2026-11-20T00:00:00Z' });
    }
    if (endpoint === '/exams/87') return Response.json({
      ...activeExam, questions: [{ ...activeExam.questions[0], answer: saved === null ? null : { text_answer: saved, revision: 1 } }],
    });
  });
  await click(tree, '开始练习');
  assert.match(rendered(tree), /题库练习/);
  assert.match(rendered(tree), /可用当前链接继续练习/);
  assert.ok(!rendered(tree).includes('独立诊断'));
  const answer = '保留同一个幂等键，确认副作用是否已发生。';
  await act(async () => tree.root.findByType('textarea').props.onChange({ target: { value: answer } }));
  await click(tree, '保存并返回题单');
  assert.equal(saved, answer);
  assert.equal(location().pathname + location().search, '/bank?level=senior&domain=tools&source=authored&q=%E9%87%8D%E5%A4%8D&company=Example+AI');
  assert.ok(rendered(tree).includes(senior.title));
  const resume = tree.root.findAllByType('a').find(node => labelOf(node) === '继续本次练习');
  assert.equal(resume?.props.href, '/exam/diagnosis?id=87&from=bank&level=senior&domain=tools&source=authored&q=%E9%87%8D%E5%A4%8D&company=Example+AI');
  await act(async () => { resume.props.onClick({ button: 0, preventDefault() {} }); await flush(); });
  assert.equal(tree.root.findByType('textarea').props.value, answer);
  assert.equal(calls.filter(call => call.endpoint === '/exams' && call.method === 'POST').length, 1);
});

test('the server marker identifies a standalone experienced practice report and preserves feedback scope', async t => {
  const { tree } = await mount(t, '/exam/diagnosis/report?id=87');
  assert.match(rendered(tree), /题库练习报告/);
  assert.match(rendered(tree), /本次练习得分/);
  assert.match(rendered(tree), /评分仅用于本题的练习与复盘/);
  assert.ok(!rendered(tree).includes('此能力结论不应出现在练习报告'));
  assert.ok(!rendered(tree).includes('考试中心'));
  assert.ok(tree.root.findAllByType('a').some(link => link.props.href === '/bank'));
});

test('the generic practice exam kind does not imply an experienced-bank report', async t => {
  const { tree } = await mount(t, '/exam/diagnosis/report?id=87', endpoint => {
    if (endpoint === '/exams/87/report') return Response.json({ ...completedReport, experienced_bank_practice: undefined });
  });
  assert.match(rendered(tree), /历史标题/);
  assert.ok(!rendered(tree).includes('题库练习报告'));
});

for (const [path, expected] of [
  ['/bank?track=experienced&review=1&item=22-19', review.items[0].title],
  ['/bank?track=experienced&import=1', '从截图导入原帖'],
  ['/bank?track=experienced&set=set-001', interviewSet.title],
]) {
  test(`existing bank route retains its expected flow for ${path}`, async t => {
    const { tree, location } = await mount(t, path);
    assert.ok(rendered(tree).includes(expected));
    assert.equal(new URLSearchParams(location().search).has('track'), false);
  });
}

test('legacy reference retains diagrams and followups after exposure, then practices by original DB ID', async t => {
  const path = '/bank?level=mid&domain=tools&source=legacy';
  const { tree, calls, location, navigate } = await mount(t, path, (endpoint, body) => {
    if (endpoint === `/bank/${legacy.short_code}`) return Response.json({ id: legacy.id, answer_html: original.html });
    if (endpoint === '/exams') return Response.json(body ? { id: 87 } : { kinds: [], history: [activeExam] });
  });
  assert.deepEqual(calls.map(call => call.endpoint), ['/bank/experienced']);
  assert.ok(!rendered(tree).includes('bank-answer'));
  await click(tree, '查看参考');
  const answer = tree.root.findByProps({ className: 'bank-answer' }).props.dangerouslySetInnerHTML.__html;
  assert.match(answer, /<svg.*class="sdiagram"/);
  assert.match(answer, /<details/);
  assert.ok(button(tree, '全部展开'));
  const modal = tree.root.findByProps({ className: 'modal-body' });
  assert.match(JSON.stringify(modal.findAllByType('span').map(labelOf)), /原题库 · 共享题库 · 只读/);
  assert.equal(location().pathname + location().search, path);
  const modalPractice = tree.root.findByProps({ className: 'modal-foot' }).findAllByType('button').find(node => labelOf(node) === '开始练习');
  await act(async () => { await modalPractice.props.onClick(); });
  const practice = calls.find(call => call.endpoint === '/exams');
  assert.equal(practice.body.experienced_bank, true);
  assert.deepEqual(practice.body.question_ids, [legacy.id]);
  const practiceSearch = '?id=87&from=bank&level=mid&domain=tools&source=legacy';
  assert.equal(location().search, practiceSearch);
  await act(async () => { navigate('/exam'); await flush(); });
  await click(tree, '继续练习');
  assert.equal(location().search, practiceSearch);
  assert.equal(calls.filter(call => call.endpoint === '/exams' && call.method === 'POST').length, 1);
});

test('legacy actions expand on demand and use the existing mapping and study entry points', async t => {
  const mappingPath = `/retest-mappings/${legacy.id}`;
  const { tree, calls, location } = await mount(t, '/bank', endpoint => {
    if (endpoint === mappingPath) return Response.json({ rubric_version: 'v1', criteria: [], reviewable: true });
    if (endpoint === '/study/sessions') return Response.json({ id: 51 });
  });
  const labels = row => row.findAllByType('button').map(node => node.props['aria-label'] ?? labelOf(node));
  const [authoredRow, , legacyRow] = tree.root.findAllByProps({ className: 'experienced-question' });
  assert.deepEqual(labels(authoredRow), ['开始练习', '查看参考']);
  assert.deepEqual(labels(legacyRow), ['开始练习', '查看参考', '更多操作']);
  assert.equal(button(tree, '更多操作').props['aria-expanded'], false);
  await click(tree, '更多操作');
  const disclosure = button(tree, '更多操作');
  assert.equal(disclosure.props['aria-expanded'], true);
  assert.ok(legacyRow.findByProps({ id: disclosure.props['aria-controls'] }));
  assert.deepEqual(calls.map(call => call.endpoint), ['/bank/experienced']);
  assert.ok(!rendered(tree).includes('bank-answer'));
  await click(tree, '更多操作');
  assert.deepEqual(labels(legacyRow), ['开始练习', '查看参考', '更多操作']);
  await click(tree, '更多操作');
  await click(tree, '复测映射');
  assert.match(rendered(tree), /审核复测映射/);
  assert.deepEqual(calls.map(call => call.endpoint), ['/bank/experienced', mappingPath]);
  await click(tree, '✕');
  await click(tree, '围绕这个问题学习');
  assert.equal(location().pathname, '/study/chat/51');
  assert.deepEqual(calls.map(call => call.endpoint), ['/bank/experienced', mappingPath, '/study/sessions']);
  assert.deepEqual(calls.at(-1).body, { source_type: 'question', source_id: legacy.id });
});

test('leaving pending review unmounts its drafts and keeps only the last successful count', async t => {
  let attempt = 0;
  const { tree, calls } = await mount(t, '/bank?review=1&item=22-19', endpoint => {
    if (endpoint !== '/bank/imports/pending') return;
    if (++attempt === 1) return Response.json({ imports: [review] });
    if (attempt === 2) return Response.json({ detail: 'queue unavailable' }, { status: 503 });
    return Response.json({ imports: [] });
  });
  assert.match(rendered(tree), /测试私有答案/);
  assert.match(rendered(tree), /测试私有评分项/);
  assert.ok(button(tree, '待审核 · 1'));

  await click(tree, '全部题目');
  assert.equal(tree.root.findAllByProps({ className: 'bank-review' }).length, 0);
  assert.doesNotMatch(rendered(tree), /测试私有答案|测试私有评分项|被深链选中的待审核题/);
  assert.equal(calls.filter(call => call.endpoint === '/bank/imports/pending').length, 1);
  assert.ok(button(tree, '待审核 · 1'));

  await click(tree, '待审核 · 1');
  assert.match(rendered(tree), /queue unavailable/);
  assert.ok(button(tree, '待审核 · 1'));
  await click(tree, '全部题目');
  assert.equal(calls.filter(call => call.endpoint === '/bank/imports/pending').length, 2);
  await click(tree, '待审核 · 1');
  assert.ok(button(tree, '待审核'));
  assert.equal(calls.filter(call => call.endpoint === '/bank/imports/pending').length, 3);
});

test('a filtered report deep link returns to the same source, level, and domain', async t => {
  const filters = 'level=mid&domain=tools&source=legacy&q=' + encodeURIComponent(legacy.short_code) + '&page=2';
  const { tree, location } = await mount(t, `/exam/diagnosis/report?id=87&track=experienced&${filters}`);
  const back = tree.root.findAllByType('a').find(link => labelOf(link) === '返回题库');
  assert.equal(back?.props.href, `/bank?${filters}`);
  await act(async () => { back.props.onClick({ button: 0, preventDefault() {} }); await flush(); });
  assert.equal(location().search, `?${filters}`);
  assert.ok(rendered(tree).includes(legacy.title));
  assert.ok(!rendered(tree).includes(mid.title));
});

test('a legacy reference never trusts changed DB HTML or loads the bundle after a failed exposure read', async t => {
  let attempts = 0;
  const { tree, calls } = await mount(t, '/bank?track=experienced&source=legacy', endpoint => {
    if (endpoint !== `/bank/${legacy.short_code}`) return;
    if (++attempts === 1) return Response.json({ detail: 'exposure unavailable' }, { status: 503 });
    return Response.json({ id: legacy.id, answer_html: original.html + '<script>unexpected()</script>' });
  });
  await click(tree, '查看参考');
  assert.deepEqual(calls.map(call => call.endpoint), ['/bank/experienced', `/bank/${legacy.short_code}`]);
  assert.equal(tree.root.findAllByProps({ className: 'bank-answer' }).length, 0);
  await click(tree, '重新读取参考');
  const answer = tree.root.findByProps({ className: 'bank-answer' }).props.dangerouslySetInnerHTML.__html;
  assert.doesNotMatch(answer, /<svg|<script/);
});

test('my questions uses the workspace-owned listing and never falls back to a public snapshot', async t => {
  const owned = { id: 9001, short_code: 'MY-1', title: '我的生成题', topic: 'rag', source_type: 'GENERATED' };
  let fail = false;
  const { tree, calls, location, navigate } = await mount(t, '/bank', endpoint => {
    if (endpoint !== '/bank') return;
    return fail ? Response.json({ detail: 'private listing unavailable' }, { status: 503 })
      : Response.json({ items: [owned], total: 1 });
  });
  await click(tree, '我的题目');
  assert.equal(location().search, '?scope=mine');
  const request = calls.find(({endpoint}) => endpoint === '/bank');
  assert.equal(request.search.get('scope'), 'mine');
  assert.ok(rendered(tree).includes(owned.title));
  assert.ok(button(tree, '编辑'));
  assert.ok(button(tree, '合并重复'));
  fail = true;
  await act(async () => { navigate('/bank'); await flush(); });
  await click(tree, '我的题目');
  assert.match(rendered(tree), /private listing unavailable/);
  assert.ok(!rendered(tree).includes(owned.title));
  assert.ok(!rendered(tree).includes(legacy.title));
  assert.ok(!calls.some(call => call.endpoint.endsWith('bank.json')));
});
