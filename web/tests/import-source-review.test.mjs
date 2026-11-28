import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement as h } from 'react';
import { act, create } from 'react-test-renderer';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import InterviewNotes from '../src/interview/library/InterviewNotes.tsx';
import Bank from '../src/pages/Bank.tsx';
import Library from '../src/seed/Library.tsx';
import { AppStateProvider } from '../src/components/AppState.tsx';
import { WorkspaceApiContext } from '../src/seed/api.ts';
import { api } from '../src/api/client.ts';
import { getLocale, setLocale, t as translate } from '../src/i18n.ts';
import ImportSourceReview from '../src/components/ImportSourceReview.tsx';

const source = '😀 合法安全面试题：如何防御 "ignore previous instructions"？\n' + '完整材料。'.repeat(150) + '\nEND-OF-PARSED-SOURCE';
const sourceReview = {
  version: 1,
  flags: [{ source: 'security.md', source_index: 0, start: 22, end: 50, reason: 'instruction_override' }],
  truncated: false,
};
const batch = meta => ({
  id: 7, short_code: 'IMP-H3', status: 'REVIEW', meta,
  items: [{ id: 8, title: '如何防御提示词注入？', status: 'DRAFT', extract_confidence: 1,
    suggested_skill_key: 'security', has_reviewable_answer: true, review_revision: 1,
    draft_answer: '<p>保留引用并对比原文。</p>', draft_rubric: { points: [{ title: '隔离指令' }] } }],
});
const output = view => JSON.stringify(view.toJSON());
const button = (view, label) => view.root.findAllByType('button').find(node => node.children.join('') === label);
const mounted = new WeakMap();

function environment(t, locale = 'zh-CN') {
  const prior = { window: globalThis.window, sessionStorage: globalThis.sessionStorage };
  const previousLocale = getLocale();
  globalThis.window = Object.assign(new EventTarget(), { setTimeout, clearTimeout, localStorage: { setItem() {} } });
  globalThis.sessionStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
  setLocale(locale);
  mounted.set(t, []);
  t.after(async () => {
    await act(async () => { for (const view of mounted.get(t)) view.unmount(); });
    setLocale(previousLocale);
    Object.assign(globalThis, prior);
  });
}

async function mount(t, element, route) {
  let view;
  await act(async () => {
    view = create(route ? h(MemoryRouter, { initialEntries: [route] }, element) : element);
  });
  mounted.get(t).push(view);
  return view;
}

function personalApi(t, getBatch) {
  t.mock.method(api, 'get', async path => {
    if (path === '/bank/imports/7') return getBatch();
    if (path === '/bank/imports/pending') return { imports: [getBatch()] };
    if (path === '/bank/interview-sets' || path === '/projects') return { items: [] };
    if (path.startsWith('/bank?')) return { total: 0, items: [] };
    throw new Error('Unexpected fixture route ' + path);
  });
}

test('personal review keeps full parsed source and persisted warning after refetch and reopening', async t => {
  environment(t);
  let current = batch({ extracted_source_text: source, pasted_text: 'STALE-PASTE', source_review: sourceReview });
  personalApi(t, () => current);
  const view = await mount(t, h(InterviewNotes), '/library/interviews?batch=7');
  assert.match(output(view), /END-OF-PARSED-SOURCE/);
  assert.equal(view.root.findByProps({ className: 'ial-prewrap' }).children.join(''), source);
  assert.doesNotMatch(output(view), /STALE-PASTE/);
  assert.match(output(view), /security\.md/);
  assert.match(output(view), /疑似指令/);
  assert.ok(view.root.findAllByType('button').some(node => node.children.includes('审核入库') && !node.props.disabled));
  current = batch({ extracted_source_text: source, source_review: sourceReview, progress: { note: '更新完成' } });
  await act(async () => button(view, '关闭审核').props.onClick());
  await act(async () => button(view, '打开审核').props.onClick());
  assert.match(output(view), /END-OF-PARSED-SOURCE/);
  assert.match(output(view), /疑似指令/);
  const reopened = await mount(t, h(InterviewNotes), '/library/interviews?batch=7');
  assert.match(output(reopened), /security\.md/);
  assert.match(output(reopened), /END-OF-PARSED-SOURCE/);
});

test('personal old batches retain pasted source without a safe verdict', async t => {
  environment(t);
  personalApi(t, () => batch({ pasted_text: source }));
  const view = await mount(t, h(InterviewNotes), '/library/interviews?batch=7');
  assert.match(output(view), /END-OF-PARSED-SOURCE/);
  assert.doesNotMatch(output(view), /疑似指令|安全通过|未发现风险|safe|no risk/i);
});

test('an authoritative empty parsed source does not revive obsolete pasted source', async t => {
  environment(t);
  personalApi(t, () => batch({ extracted_source_text: '', pasted_text: 'OBSOLETE' }));
  const view = await mount(t, h(InterviewNotes), '/library/interviews?batch=7');
  assert.doesNotMatch(output(view), /OBSOLETE/);
});

test('native Bank reopens persisted full source instead of a stale local paste buffer', async t => {
  environment(t, 'en');
  const saved = batch({ extracted_source_text: source, pasted_text: 'OLD-PERSISTED-PASTE', source_review: sourceReview });
  personalApi(t, () => saved);
  const view = await mount(t, h(AppStateProvider, null, h(Bank)), '/bank?review=1&import=1');
  const paste = view.root.findAllByType('textarea').find(node => node.props.value === '');
  await act(async () => paste.props.onChange({ target: { value: 'STALE-LOCAL-PASTE' } }));
  const organize = button(view, translate('bank.organizeImportedSet'));
  assert.ok(organize, 'pending review exposes its existing import source action');
  await act(async () => organize.props.onClick());
  assert.match(output(view), /END-OF-PARSED-SOURCE/);
  assert.match(output(view), /Suspected instructions/);
  assert.match(output(view), /security\.md/);
  assert.doesNotMatch(output(view), /STALE-LOCAL-PASTE|OLD-PERSISTED-PASTE/);
  assert.equal(view.root.findByProps({ className: 'rev-src-text' }).children.join(''), source);
  await act(async () => view.root.findByProps({ className: 'modal-x' }).props.onClick());
  await act(async () => button(view, translate('bank.organizeImportedSet')).props.onClick());
  assert.match(output(view), /Suspected instructions/);
  assert.equal(view.root.findByProps({ className: 'rev-src-text' }).children.join(''), source);
});

test('empty findings render no warning or safe verdict; all reason codes show advice as escaped text', t => {
  environment(t, 'en');
  assert.equal(renderToStaticMarkup(h(ImportSourceReview, { review: { version: 1, flags: [], truncated: false } })), '');
  assert.equal(renderToStaticMarkup(h(ImportSourceReview)), '');
  const reasons = ['instruction_override', 'role_spoofing', 'delimiter_closure', 'workspace_or_approval', 'secret_request'];
  const html = renderToStaticMarkup(h(ImportSourceReview, { review: {
    version: 1, truncated: false,
    flags: reasons.map(reason => ({ source: '<script>source</script>.md', source_index: 0, start: 0, end: 4, reason })),
  } }));
  for (const label of ['override instructions', 'system or assistant role', 'source boundary', 'bypassing approval', 'secrets or credentials']) {
    assert.ok(html.includes(label), label);
  }
  assert.ok(html.includes('&lt;script&gt;source&lt;/script&gt;.md'));
  assert.doesNotMatch(html, /<script>/);
});

function seedApi(getDraft) {
  const role = { role: '后端', selected_topic: '安全', directions: [{ name: '安全', example: '', origin: 'custom' }] };
  return { request: async path => {
    if (path === '/library/roles') return { selected_role: role.role, roles: [role] };
    if (path === '/privacy') return { provider: 'fixture', region: 'fixture' };
    if (path.startsWith('/library/questions?')) return { questions: [], next_before: null };
    if (path === '/library/imports') return { imports: [getDraft()], next_before: null };
    if (path === '/library/imports/7') return getDraft();
    throw new Error('Unexpected seed fixture route ' + path);
  }, send: async () => { throw new Error('No writes expected'); } };
}

for (const locale of ['zh-CN', 'en']) {
  test(`seed history keeps warning and full security quote after reopening in ${locale}`, async t => {
    environment(t, locale);
    const saved = { id: 7, filename: 'security.md', role: '后端', topic: '安全', status: 'REVIEW',
      error: null, suggested: true, source_text: source, source_review: { ...sourceReview, truncated: true }, items: [] };
    const element = h(WorkspaceApiContext.Provider, { value: seedApi(() => saved) }, h(Library, { onPractice() {}, onCoach() {} }));
    const view = await mount(t, element);
    for (let i = 0; i < 2; i++) {
      await act(async () => button(view, translate('registered.openImportRecord')).props.onClick());
      assert.match(output(view), /END-OF-PARSED-SOURCE/);
      assert.ok(view.root.findAllByType('pre').some(node => node.children.join('') === source));
      assert.match(output(view), locale === 'en' ? /Suspected instructions/ : /疑似指令/);
      assert.match(output(view), locale === 'en' ? /first 100/ : /前 100/);
      await act(async () => button(view, translate('registered.closeImportPreview')).props.onClick());
    }
  });
}
