import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter, useLocation } from 'react-router-dom';

const sample = () => ({ title: '怎样排查检索失败？', original_question: '原面经问题', skill_key: 'rag', question_mode: 'general', project_id: null, fact_refs: [], answer: { tldr: '先区分召回与生成失败', elevator: '原有概述', analogy: '', examiner: ['排查能力'], steps: [{title:'对照检索结果',detail:'使用标注查询定位错误'}], how_to_answer: '', pitfalls: [], followups: [{q:'如何验证？',a:'使用固定评估集'}], key_points: [{title:'分层排查',detail:'先看召回结果，再看生成'}], terms: [] }, project_answer: '', hypothetical_answer: '', missing_context: [], source_question_id: 42 });

test('approval is blocked for missing project context and unfinished or empty drafts', async () => {
  const { approvalBlock } = await import('../src/lib/questionWorkshop.ts');
  const row = {status:'DRAFT',draft_answer:'<p>answer</p>',generation_meta:{item:sample()}};
  assert.equal(approvalBlock(row), null);
  assert.match(approvalBlock({...row,generation_meta:{item:{...sample(),question_mode:'needs_context',missing_context:['我的职责']}}}), /资料/);
  assert.match(approvalBlock({...row,status:'APPROVED'}), /审核/);
  assert.match(approvalBlock({...row,draft_answer:''}), /答案/);
});

test('structured draft edit persists updated answer without losing followups, criteria or source mapping', async () => {
  const { default: DraftEditor } = await import('../src/components/QuestionDraftEditor.tsx');
  let saved;
  let renderer;
  await act(async () => { renderer = TestRenderer.create(React.createElement(DraftEditor,{item:sample(),skills:[{key:'rag',name:'RAG'}],projects:[],busy:false,onCancel(){},onSave:async value=>{saved=value;}})); });
  const field = renderer.root.findByProps({'aria-label':'一句话回答'});
  await act(async () => field.props.onChange({target:{value:'新的真实回答'}}));
  const save = renderer.root.findByProps({'data-action':'save-draft'});
  await act(async () => save.props.onClick());
  assert.equal(saved.answer.tldr,'新的真实回答');
  assert.deepEqual(saved.answer.followups,sample().answer.followups);
  assert.deepEqual(saved.answer.key_points,sample().answer.key_points);
  assert.equal(saved.source_question_id,42);
  renderer.unmount();
});

const task = () => ({id:17,kind:'supplement',mode:'external',status:'READY',prompt_text:'完整项目 Prompt',input_snapshot:{count:1,skills:[{key:'rag',name:'RAG'}],skill_keys:['rag'],project_context:[],purpose:'补充 RAG',source_questions:[]}});
const flush = () => new Promise(resolve => setImmediate(resolve));
const button = (tree,label) => tree.root.findAllByType('button').find(n=>n.children.join('').includes(label));

test('creating an API task previews the frozen input and requires a separate generate action', async()=>{
  const {default:Workshop}=await import('../src/pages/QuestionWorkshop.tsx');
  const savedFetch=globalThis.fetch;
  const calls=[];let tree;
  globalThis.fetch=async(url,options)=>{
    const path=new URL(url).pathname;calls.push([path,options.method,options.body]);
    if(options.method==='POST' || path.endsWith('/17')) return Response.json({...task(),mode:'api'});
    return Response.json({items:[]});
  };
  try{
    await act(async()=>{tree=TestRenderer.create(React.createElement(MemoryRouter,{initialEntries:['/bank/workshop'],future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(Workshop)));await flush();});
    await act(async()=>{await button(tree,'创建任务并检查输入').props.onClick();await flush();});
    assert.ok(button(tree,'启动 API 生成'));
    assert.equal(calls.filter(([,m])=>m==='POST').length,1);
    const post=calls.find(([,m])=>m==='POST');
    assert.equal(post[0],'/api/v1/question-generation');
    assert.equal(JSON.parse(post[2]).mode,'api');
    assert.ok(JSON.parse(post[2]).request_key);
    assert.ok(!calls.some(([p])=>p.endsWith('/run')));
  }finally{if(tree)await act(async()=>tree.unmount());globalThis.fetch=savedFetch;}
});

test('external invalid response remains editable with a real error and never starts API generation', async () => {
  const {default:Workshop}=await import('../src/pages/QuestionWorkshop.tsx');
  const savedFetch=globalThis.fetch;
  const calls=[];
  let tree;
  globalThis.fetch=async(url,options)=>{
    const path=new URL(url).pathname;calls.push([path,options.method]);
    if(path.endsWith('/result')) return Response.json({...task(),status:'FAILED',raw_response:'bad format',error:'缺少评分要点'});
    if(path.endsWith('/17'))return Response.json(task());
    return Response.json({items:[]});
  };
  try{
    await act(async()=>{tree=TestRenderer.create(React.createElement(MemoryRouter,{initialEntries:['/bank/workshop?task=17'],future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(Workshop)));await flush();});
    await act(async()=>tree.root.findByProps({'aria-label':'LLM 完整回复'}).props.onChange({target:{value:'bad format'}}));
    await act(async()=>button(tree,'校验并生成审核草稿').props.onClick());
    assert.match(JSON.stringify(tree.toJSON()),/缺少评分要点/);
    assert.equal(tree.root.findByProps({'aria-label':'LLM 完整回复'}).props.value,'bad format');
    assert.equal(calls.filter(([p,m])=>m==='POST').length,1);
    assert.ok(calls.some(([p])=>p.endsWith('/17/result')));
    assert.ok(!calls.some(([p])=>p.endsWith('/run')));
  }finally{if(tree)await act(async()=>tree.unmount());globalThis.fetch=savedFetch;}
});

test('failed draft save keeps edited content and cannot claim it was persisted', async()=>{
  const {default:Workshop}=await import('../src/pages/QuestionWorkshop.tsx');
  const savedFetch=globalThis.fetch;
  let tree;let submitted;
  globalThis.fetch=async(url,options)=>{
    if(options.method==='PATCH'){submitted=JSON.parse(options.body);return Response.json({detail:'保存暂时失败'},{status:503});}
    if(new URL(url).pathname.endsWith('/17'))return Response.json({...task(),status:'REVIEW',import_session_id:23,review:{id:23,short_code:'IM-G17',status:'REVIEW',items:[{id:51,title:sample().title,status:'DRAFT',draft_answer:'<p>答案</p>',generation_meta:{item:sample(),source:'external'}}]}});
    return Response.json({items:[]});
  };
  try{
    await act(async()=>{tree=TestRenderer.create(React.createElement(MemoryRouter,{initialEntries:['/bank/workshop?task=17'],future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(Workshop)));await flush();});
    await act(async()=>button(tree,'编辑答案与评分要点').props.onClick());
    await act(async()=>tree.root.findByProps({'aria-label':'一句话回答'}).props.onChange({target:{value:'等待保存的答案'}}));
    await act(async()=>tree.root.findByProps({'data-action':'save-draft'}).props.onClick());
    assert.equal(submitted.item.answer.tldr,'等待保存的答案');
    assert.equal(tree.root.findByProps({'aria-label':'一句话回答'}).props.value,'等待保存的答案');
    assert.match(JSON.stringify(tree.toJSON()),/保存暂时失败/);
    assert.doesNotMatch(JSON.stringify(tree.toJSON()),/草稿已保存并重新排版/);
  }finally{if(tree)await act(async()=>tree.unmount());globalThis.fetch=savedFetch;}
});

test('approved draft starts a real exam for that exact question and opens its answer session', async()=>{
  const {default:Workshop}=await import('../src/pages/QuestionWorkshop.tsx');
  const savedFetch=globalThis.fetch;
  let tree;let submitted;let route;
  function Observe(){route=useLocation();return null;}
  globalThis.fetch=async(url,options)=>{
    if(new URL(url).pathname.endsWith('/exams')){submitted=JSON.parse(options.body);return Response.json({id:88});}
    if(new URL(url).pathname.endsWith('/17'))return Response.json({...task(),status:'COMPLETE',import_session_id:23,review:{id:23,short_code:'IM-G17',status:'APPROVED',items:[{id:51,question_id:37,title:sample().title,status:'APPROVED',draft_answer:'<p>答案</p>',generation_meta:{item:sample(),source:'external'}}]}});
    return Response.json({items:[]});
  };
  try{
    await act(async()=>{tree=TestRenderer.create(React.createElement(MemoryRouter,{initialEntries:['/bank/workshop?task=17'],future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(React.Fragment,null,React.createElement(Observe),React.createElement(Workshop))));await flush();});
    await act(async()=>button(tree,'开始练这题').props.onClick());
    assert.deepEqual(submitted.question_ids,[37]);
    assert.equal(submitted.kind,'bank_exam');
    assert.ok(submitted.idempotency_key);
    assert.equal(route.pathname,'/exam/diagnosis');assert.equal(route.search,'?id=88');
  }finally{if(tree)await act(async()=>tree.unmount());globalThis.fetch=savedFetch;}
});

test('manual project cards offer personal context instead of an impossible repository scan', async()=>{
  const {default:Assets}=await import('../src/pages/Assets.tsx');
  const {AppStateProvider}=await import('../src/components/AppState.tsx');
  const savedFetch=globalThis.fetch;let tree;
  globalThis.fetch=async()=>Response.json({items:[{id:9,name:'工作项目',kind:'manual',owner:'',repo:'',repo_url:'',status:'NEVER',findings:{matrix:[]}}]});
  try{
    await act(async()=>{tree=TestRenderer.create(React.createElement(MemoryRouter,{future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(AppStateProvider,null,React.createElement(Assets))));await flush();});
    assert.match(JSON.stringify(tree.toJSON()),/手工项目/);
    assert.ok(button(tree,'编辑经历与准备面试'));
    assert.equal(button(tree,'重新分析'),undefined);
  }finally{if(tree)await act(async()=>tree.unmount());globalThis.fetch=savedFetch;}
});

test('switching task history clears the previous task approval notice',async()=>{
  const {default:Workshop}=await import('../src/pages/QuestionWorkshop.tsx');
  const savedFetch=globalThis.fetch;let tree;
  globalThis.fetch=async(url,options)=>{
    const path=new URL(url).pathname;
    if(path.endsWith('/approve'))return Response.json({created:['Q-G17-51']});
    if(path.endsWith('/17'))return Response.json({...task(),status:'REVIEW',import_session_id:23,review:{id:23,short_code:'IM-G17',status:'REVIEW',items:[{id:51,title:sample().title,status:'DRAFT',draft_answer:'<p>答案</p>',generation_meta:{item:sample(),source:'external'}}]}});
    if(path.endsWith('/18'))return Response.json({...task(),id:18});
    if(path.endsWith('/question-generation'))return Response.json({items:[task(),{...task(),id:18}]});
    return Response.json({items:[]});
  };
  try{
    await act(async()=>{tree=TestRenderer.create(React.createElement(MemoryRouter,{initialEntries:['/bank/workshop?task=17'],future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(Workshop)));await flush();});
    await act(async()=>button(tree,'通过入私有库').props.onClick());
    assert.match(JSON.stringify(tree.toJSON()),/Q-G17-51/);
    const next=tree.root.findAllByType('a').find(node=>node.props.href==='/bank/workshop?task=18');
    await act(async()=>{next.props.onClick({button:0,preventDefault(){}});await flush();});
    assert.doesNotMatch(JSON.stringify(tree.toJSON()),/Q-G17-51/);
    assert.equal(tree.root.findByProps({'aria-label':'完整 Prompt'}).props.value,task().prompt_text);
  }finally{if(tree)await act(async()=>tree.unmount());globalThis.fetch=savedFetch;}
});
