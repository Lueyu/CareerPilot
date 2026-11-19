import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { AppStateProvider } from '../src/components/AppState.tsx';
import Dashboard from '../src/pages/Dashboard.tsx';
import Benchmark from '../src/pages/Benchmark.tsx';
import Diagnosis from '../src/pages/Diagnosis.tsx';
import DiagnosisReport from '../src/pages/DiagnosisReport.tsx';
import Learning from '../src/pages/Learning.tsx';
import LearningTask from '../src/pages/LearningTask.tsx';
import Profile from '../src/pages/Profile.tsx';
import ProfileDetail from '../src/pages/ProfileDetail.tsx';

const memory = () => {
  const entries = new Map();
  return { getItem: key => entries.get(key) || null, setItem: (key, value) => entries.set(key, value), removeItem: key => entries.delete(key) };
};
globalThis.localStorage = memory();
globalThis.sessionStorage = memory();
const flush = () => new Promise(resolve => setImmediate(resolve));
const json = (value, status = 200) => new Response(JSON.stringify(value), { status });
const text = tree => JSON.stringify(tree.toJSON());
const button = (tree, label) => tree.root.findAllByType('button').find(n => n.children.join('').includes(label));
let location;
function RouteObserver() { location = useLocation(); return null; }
async function mount(Page, path = '/', route = '*') {
  let tree;
  await act(async () => {
    tree = create(React.createElement(MemoryRouter, { initialEntries: [path], future: { v7_startTransition: true, v7_relativeSplatPath: true } },
      React.createElement(AppStateProvider, null, React.createElement(RouteObserver), React.createElement(Routes, null, React.createElement(Route, { path: route, element: React.createElement(Page) }), route !== '*' && React.createElement(Route, { path: '*', element: null })))));
    await flush();
  });
  return tree;
}
async function cleanup(tree) { await act(async () => tree.unmount()); }

test('real empty dashboard cannot claim mastery or show example tasks', async () => {
  globalThis.fetch = async () => json({ workspace: { name: 'test', target_role: '', target_level: '' }, stats: { evidence_total: 0, pending_skills: 0, today_tasks: 0, pending_imports: 0 }, domains: [], gap: null, today_tasks: [], pending_imports: [], recent_activity: [] });
  const tree = await mount(Dashboard);
  try { assert.match(text(tree), /未知/); assert.doesNotMatch(text(tree), /所有已评估能力都达到|无重大缺口|让工具安全地重试|DEMO/); }
  finally { await cleanup(tree); }
});

test('unknown benchmark domain must not become a numeric gap', async () => {
  globalThis.fetch = async () => json({ standard: { role: 'test', level: 'P6', jd_sample_count: 0 }, rows: [{ skill_key: 'rag', skill: 'RAG', weight_pct: 100, target_level: 4, current_level: null, status: 'UNKNOWN', evidence_coverage: 0, confidence: null }] });
  const tree = await mount(Benchmark);
  try { const gap = tree.root.findAll(n => n.props.className === 'bench-gap')[0]; assert.ok(gap.findAll(n => n.children.includes('未知')).length > 0, 'unknown gap must be labelled 未知'); }
  finally { await cleanup(tree); }
});

test('completed learning tasks remain available for immediate verification', async () => {
  globalThis.fetch = async () => json({ today: [], retest_plan: [], completed: [{ id: 99, title: 'Actual completed task', kind: 'PRACTICE', status: 'DONE' }] });
  const tree = await mount(Learning, '/learning?plan=history', '/learning');
  try { assert.match(text(tree), /Actual completed task/); assert.match(text(tree), /即时/); }
  finally { await cleanup(tree); }
});

test('report request failure keeps the requested id and does not load old history', async () => {
  const calls = [];
  globalThis.fetch = async url => { calls.push(url); return json({ detail: 'report unavailable' }, 503); };
  const tree = await mount(DiagnosisReport, '/exam/diagnosis/report?id=77');
  try { assert.match(text(tree), /77/); assert.match(text(tree), /report unavailable/); assert.doesNotMatch(text(tree), /演示报告/); assert.equal(calls.length, 1); assert.ok(calls[0].endsWith('/exams/77/report')); }
  finally { await cleanup(tree); }
});

test('failed atomic save keeps local edits and only unlocks after the server confirms ACTIVE', async () => {
  const exam = { id: 42, title: 'test', status: 'ACTIVE', duration_minutes: 30, questions: [{ order_no: 1, qtype: 'open', title: 'actual question', short_code: 'Q1', answer: null }] };
  const posts = [];let reads=0;
  globalThis.fetch = async (url, options) => {
    if (options.method === 'GET') {reads++;return json(exam);}
    posts.push({url,body:JSON.parse(options.body)}); return json({ detail: 'cannot save' }, 503);
  };
  const tree = await mount(Diagnosis, '/exam/diagnosis?id=42');
  try {
    await act(async () => tree.root.findByType('textarea').props.onChange({ target: { value: 'my retained answer' } }));
    await act(async () => button(tree, '提交答卷').props.onClick());
    await act(async () => { await button(tree, '确认提交').props.onClick(); await flush(); });
    assert.equal(tree.root.findByType('textarea').props.value, 'my retained answer');
    assert.match(text(tree), /cannot save/);
    assert.deepEqual(posts.map(x => new URL(x.url).pathname), ['/api/v1/exams/42/submit']);
    assert.deepEqual(posts[0].body.answers,[{order_no:1,text_answer:'my retained answer',expected_revision:0}]);
    assert.ok(reads>=2,'submission failure must reconcile the authoritative exam state before editing');
    assert.equal(location.search, '?id=42');
    const voice = button(tree, '语音输入不可用'); assert.equal(voice.props.disabled, true);
  } finally { await cleanup(tree); }
});

test('retest task opens a new answer session and cannot be completed as learning', async () => {
  globalThis.fetch = async (url, options) => options.method === 'POST' ? json({ exam_id: 88 }) : json({ id: 99, title: 'scheduled task', kind: 'RETEST', status: 'PENDING', due_date: '2020-01-01', content: {} });
  const tree = await mount(LearningTask, '/learning/task/99?legacy=1', '/learning/task/:id');
  try {
    assert.equal(button(tree, '保存学习进度'), undefined);
    await act(async () => { await button(tree, '开始到期复测').props.onClick(); await flush(); });
    assert.equal(location.pathname + location.search, '/exam/diagnosis?id=88&return_task_id=99');
  } finally { await cleanup(tree); }
});

test('task displays the actual answer excerpt and missed criterion', async () => {
  globalThis.fetch = async () => json({ id: 99, title: 'specific task', kind: 'PRACTICE', status: 'DONE', source_criterion: 'criterion A', source_exam_id: 42, content: { source: 'rule', analogy: 'actual teaching', observation: { question_title: 'actual source question', answer_excerpt: 'actual source answer', criterion_detail: 'specific expected point', reasoning: 'missing mechanism' } } });
  const tree = await mount(LearningTask, '/learning/task/99?legacy=1', '/learning/task/:id');
  try { assert.match(text(tree), /actual source answer/); assert.match(text(tree), /specific expected point/); }
  finally { await cleanup(tree); }
});

test('linked learning task refreshes legacy teaching context once', async () => {
  const paths = [];
  globalThis.fetch = async (url) => {
    paths.push(String(url));
    return String(url).endsWith('/content')
      ? json({id:99, content:{source:'llm', source_context_version:2, analogy:'frozen source teaching'}})
      : json({id:99, title:'specific task', kind:'PRACTICE', status:'DONE', source_exam_question_id:42,
              content:{source:'llm', analogy:'legacy unrelated teaching'}});
  };
  const tree = await mount(LearningTask, '/learning/task/99?legacy=1', '/learning/task/:id');
  try {
    assert.equal(paths.filter(path => path.endsWith('/content')).length, 1);
    assert.match(text(tree), /frozen source teaching/);
    assert.doesNotMatch(text(tree), /legacy unrelated teaching/);
  } finally { await cleanup(tree); }
});

test('learning completion failure retains artifact and cannot display a success claim', async () => {
  globalThis.fetch = async (url, options) => options.method === 'POST' ? json({ detail: 'storage unavailable' }, 503) : json({ id: 99, title: 'specific task', kind: 'PRACTICE', status: 'PENDING', content: { source: 'rule', analogy: 'actual teaching', example: 'actual example' } });
  const tree = await mount(LearningTask, '/learning/task/99?legacy=1', '/learning/task/:id');
  try {
    for (let i = 0; i < 4; i++) await act(async () => button(tree, '下一步 →').props.onClick());
    await act(async () => tree.root.findByType('textarea').props.onChange({ target: { value: 'my own practice artifact' } }));
    await act(async () => { await button(tree, '保存学习进度').props.onClick(); await flush(); });
    assert.equal(tree.root.findByType('textarea').props.value, 'my own practice artifact');
    assert.match(text(tree), /storage unavailable/);
    assert.doesNotMatch(text(tree), /学习进度已保存|证据已写入能力画像/);
    assert.equal(location.pathname, '/learning/task/99');
  } finally { await cleanup(tree); }
});

test('zero observation score leaves the overall profile unknown', async () => {
  globalThis.fetch = async () => json({ overall: { level: null, confidence: 0, evidence_total: 1 }, domains: [{ key: 'rag', name: 'RAG', level: null, status: 'UNKNOWN', evidence_coverage: 1, confidence: 0 }], evidence: [{ short_code: 'EV-REAL', kind: 'EXAM_ANSWER', title: 'This answer scored zero', grade: 'HIGH', detail: 'missing actual criterion', skill: 'RAG' }] });
  const tree = await mount(Profile);
  try { assert.match(text(tree), /整体估计 /); assert.match(text(tree), /未知/); assert.match(text(tree), /This answer scored zero/); assert.doesNotMatch(text(tree), /2\.7|精通/); }
  finally { await cleanup(tree); }
});

for (const view of [
  {name:'Profile',Page:Profile,path:'/profile',route:'*',payload:{overall:{level:2,confidence:0.99,evidence_total:8},domains:[{key:'rag',name:'RAG',level:2,status:'NEEDS_VALIDATION',evidence_coverage:8,confidence:0.99}],evidence:[]}},
  {name:'ProfileDetail',Page:ProfileDetail,path:'/profile/rag',route:'/profile/:domain',payload:{skill:{key:'rag',name:'RAG'},state:{level:2,status:'NEEDS_VALIDATION',confidence:0.99,evidence_coverage:8,dimension_scores:{}},evidence:[]}},
  {name:'Benchmark',Page:Benchmark,path:'/benchmark',route:'*',payload:{standard:{role:'fixture',level:'P6',jd_sample_count:0},rows:[{skill_key:'rag',skill:'RAG',weight_pct:100,target_level:4,current_level:2,status:'NEEDS_VALIDATION',evidence_coverage:8,confidence:0.99}]}}
]) test(`${view.name} labels abundant observations without presenting accuracy or reliability odds`, async()=>{
  globalThis.fetch=async()=>json(view.payload);
  const tree=await mount(view.Page,view.path,view.route);
  try{
    assert.match(text(tree),/观察覆盖/);assert.match(text(tree),/已有更多独立观察/);
    assert.match(text(tree),/不代表评分准确率/);
    assert.doesNotMatch(text(tree),/评估比较可靠|评估有一定把握|置信|0\.99|99%/);
  }finally{await cleanup(tree);}
});

test('report opens the exact task linked to its actual observation', async () => {
  globalThis.fetch = async () => json({ id: 42, status: 'COMPLETED', title: 'actual report', score: 0, total_points: 5, gained_points: 0, questions: [], observations: [{ answer_excerpt: 'my answer' }], learning_tasks: [{ id: 99, title: 'fix actual missed criterion', source_criterion: 'missing point' }] });
  const tree = await mount(DiagnosisReport, '/exam/diagnosis/report?id=42');
  try {
    assert.match(text(tree), /fix actual missed criterion/);
    await act(async () => button(tree, '学习这一项').props.onClick());
    assert.equal(location.pathname, '/learning/task/99');
  } finally { await cleanup(tree); }
});

test('profile detail identifies only missed criteria as missing', async () => {
  globalThis.fetch = async () => json({ skill: { key: 'rag', name: 'RAG' }, state: { level: null, status: 'UNKNOWN', confidence: 0, evidence_coverage: 1, dimension_scores: {} }, evidence: [{ short_code: 'EV-REAL', title: 'actual observation', observation: { score: 0, max_points: 5, answer_excerpt: 'actual answer', criteria: [{ title: 'already hit point', status: 'hit' }, { title: 'missing point', status: 'missing' }] } }] });
  const tree = await mount(ProfileDetail, '/profile/rag', '/profile/:domain');
  try { assert.match(text(tree), /missing point/); assert.doesNotMatch(text(tree), /already hit point/); }
  finally { await cleanup(tree); }
});

test('completed report clears creation retry identity after a successful rescore', async () => {
  sessionStorage.setItem('cp-exam-create:personal:diagnosis', 'old-completed-request');
  globalThis.fetch = async () => json({ id: 42, status: 'COMPLETED', title: 'actual report', score: 0, questions: [] });
  const tree = await mount(DiagnosisReport, '/exam/diagnosis/report?id=42');
  try { assert.equal(sessionStorage.getItem('cp-exam-create:personal:diagnosis'), null); }
  finally { await cleanup(tree); }
});

test('report summary reflects actual AI judges and shows the local completion time', async () => {
  const previousTZ = process.env.TZ;
  process.env.TZ = 'Asia/Shanghai';
  globalThis.fetch = async () => json({ id: 42, status: 'COMPLETED', title: 'actual report', score: 85, completed_at: '2026-11-16T08:12:00+00:00', questions: [], summary: { judges: { judge_v1: 1, rule_v1: 0 } }, overall: { conclusion: 'actual conclusion', strong: [], mid: [], weak: [] } });
  const tree = await mount(DiagnosisReport, '/exam/diagnosis/report?id=42');
  try {
    assert.ok(text(tree).includes('AI 评分 1 题 · 规则评分 0 题'));
    assert.doesNotMatch(text(tree), /确定性判定 · 评分 rule-v1/);
    assert.match(text(tree), /16:12/);
  } finally {
    await cleanup(tree);
    if (previousTZ === undefined) delete process.env.TZ; else process.env.TZ = previousTZ;
  }
});

test('report summary counts both v1 and v2 graders without inventing a zero for new results', async()=>{
  for(const scenario of [
    {judges:{judge_v2:2,rule_v2:1},label:'AI 评分 2 题 · 规则评分 1 题'},
    {judges:{judge_v1:1,rule_v1:2,judge_v2:3,rule_v2:4},label:'AI 评分 4 题 · 规则评分 6 题'},
  ]){
    globalThis.fetch=async()=>json({id:42,status:'COMPLETED',title:'Grader version fixture',score:60,questions:[],summary:{judges:scenario.judges},overall:{conclusion:'Fixture completed grading',strong:[],mid:[],weak:[]}});
    const tree=await mount(DiagnosisReport,'/exam/diagnosis/report?id=42');
    try{assert.ok(text(tree).includes(scenario.label));}finally{await cleanup(tree);}
  }
});

for(const view of [{name:'Profile',Page:Profile,path:'/profile',route:'*'}, {name:'ProfileDetail',Page:ProfileDetail,path:'/profile/rag',route:'/profile/:domain'}])
test(`${view.name} explains an uncalibrated observation without promoting it to a reliable judgement`,async()=>{
  const evidence=[{id:81,short_code:'EV-UNCALIBRATED',kind:'EXAM_ANSWER',title:'Actual answer observation',grade:'OBSERVED_UNCALIBRATED'}];
  globalThis.fetch=async()=>json({overall:{level:null,confidence:0.15,evidence_total:1},domains:[],skill:{key:'rag',name:'RAG'},state:{level:null,confidence:0.15,evidence_coverage:1,status:'UNKNOWN',dimension_scores:{}},evidence});
  const tree=await mount(view.Page,view.path,view.route);
  try{assert.match(text(tree),/未校准观察/);assert.match(text(tree),/不等于可靠能力判断/);assert.doesNotMatch(text(tree),/OBSERVED_UNCALIBRATED/);}finally{await cleanup(tree);}
});

for (const scenario of [
  { name: 'legacy result with an old criterion pass', result: { passed: true, source_criterion_covered: true, source_criterion_passed: true }, overall: '通过' },
  { name: 'legacy result without source criterion assessment', result: { passed: false }, overall: '未通过' },
]) {
  test(`retest presentation distinguishes ${scenario.name}`, async () => {
    globalThis.fetch = async () => json({ id: 99, title: 'specific task', kind: 'PRACTICE', status: 'DONE', source_criterion: 'original missing point', content: { source: 'rule', analogy: 'actual teaching', retest_result: { exam_id: 42, score: 85, outcome: '历史原项已通过', assessment_scope: '历史映射范围', scope_note: '历史验证说明', ...scenario.result } } });
    const tree = await mount(LearningTask, '/learning/task/99?legacy=1', '/learning/task/:id');
    try {
      const rendered = text(tree);
      assert.ok(rendered.includes(`本题整体：${scenario.overall}`));
      assert.match(rendered, /整题得分：.*85/);
      assert.match(rendered, /原评分项结果：待验证（历史记录缺少审核映射）/);
      assert.doesNotMatch(rendered, /历史原项已通过|历史映射范围|历史验证说明/);
    } finally { await cleanup(tree); }
  });
}

test('retest keeps reviewed goal outcomes separate from the overall exam result', async () => {
  const goal_outcomes = [
    { goal_id: 1, source_criterion: 'Unmapped criterion', status: 'UNMAPPED', score: null, independent: null, covered: false },
    { goal_id: 2, source_criterion: 'Assisted criterion', status: 'ASSISTED', score: 95, independent: false, covered: true },
    { goal_id: 3, source_criterion: 'Independent criterion', status: 'FAILED', score: 40, independent: true, covered: true },
  ];
  globalThis.fetch = async () => json({ id: 99, title: 'specific task', kind: 'RETEST', status: 'DONE', content: { retest_result: { exam_id: 42, passed: true, goal_outcomes } } });
  const tree = await mount(LearningTask, '/learning/task/99?legacy=1', '/learning/task/:id');
  try {
    assert.ok(text(tree).includes('本题整体：通过'));
    const rows = tree.root.findByProps({ 'aria-label': '原评分项结果' }).findAllByType('li');
    const renderedRows = rows.map(row => JSON.stringify(row.findByType('b').children) + row.children.filter(child => typeof child === 'string').join(''));
    assert.match(renderedRows[0], /Unmapped criterion.*待验证/);
    assert.match(renderedRows[1], /Assisted criterion.*独立表现待验证.*95/);
    assert.match(renderedRows[2], /Independent criterion.*未通过.*40/);
  } finally { await cleanup(tree); }
});
