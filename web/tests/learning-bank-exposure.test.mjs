import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {readFile} from 'node:fs/promises';
import {act,create} from 'react-test-renderer';
import {MemoryRouter,useLocation} from 'react-router-dom';
import Bank from '../src/pages/Bank.tsx';
import {AppStateProvider} from '../src/components/AppState.tsx';
const bankAsset=new URL('../src/data/bank.json',import.meta.url);
const bankData=JSON.parse(await readFile(bankAsset,'utf8'));
const seed=bankData.questions.find(q=>q.html);
const item={id:999,short_code:seed.id,title:'Reference exposure fixture',qtype:'short_answer',topic:'rag',difficulty:2,source_type:'PUBLIC_LICENSED',status:'APPROVED',tags:[]};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const text=tree=>JSON.stringify(tree.toJSON());
const button=(tree,label)=>tree.root.findAllByType('button').find(n=>n.children.join('').includes(label));
test.beforeEach(()=>{const map=new Map();globalThis.localStorage=globalThis.sessionStorage={getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)};globalThis.window={location:{search:''},addEventListener(){},removeEventListener(){}};});
async function mount(onLocation=()=>{}){const apiFetch=globalThis.fetch;globalThis.fetch=(url,options)=>String(url)===bankAsset.href?Promise.resolve(Response.json({questions:[seed],topics:bankData.topics})):new URL(url).pathname.endsWith('/bank/interview-sets')?Promise.resolve(Response.json({items:[]})):apiFetch(url,options);function Observer(){onLocation(useLocation());return null;}let tree;await act(async()=>{tree=create(React.createElement(MemoryRouter,{future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(AppStateProvider,null,React.createElement(Observer),React.createElement(Bank))));await flush();});return tree;}
async function close(tree){await act(async()=>tree.unmount());}

test('a failed reference exposure write cannot reveal a bundled answer',async()=>{
 globalThis.fetch=async(url)=>{const p=new URL(url).pathname;if(p.endsWith('/pending'))return Response.json({imports:[]});if(p.endsWith('/bank'))return Response.json({items:[item],total:1});return Response.json({detail:'Exposure storage unavailable'},{status:503});};
 const tree=await mount();try{await act(async()=>{await tree.root.findByProps({className:'q-title'}).props.onClick();await flush();});assert.equal(tree.root.findAllByProps({className:'bank-answer'}).length,0);assert.match(text(tree),/Exposure storage unavailable/);assert.ok(button(tree,'重新读取参考'));}finally{await close(tree);}
});

test('starting a question with its server numeric id never fetches reference material',async()=>{
 const calls=[];let location;globalThis.fetch=async(url,o={})=>{const p=new URL(url).pathname;calls.push([p,o.body?JSON.parse(o.body):null]);if(p.endsWith('/pending'))return Response.json({imports:[]});if(p.endsWith('/bank'))return Response.json({items:[item],total:1});if(p.endsWith('/exams'))return Response.json({id:87});return Response.json({id:999,answer_html:'Hidden reference'});};
 const tree=await mount(value=>location=value);try{await act(async()=>{await button(tree,'开始练习').props.onClick();await flush();});assert.equal(calls.some(([p])=>p.endsWith('/bank/'+seed.id)),false);assert.deepEqual(calls.find(([p])=>p.endsWith('/exams'))[1].question_ids,[999]);assert.equal(location.pathname,'/exam/diagnosis');}finally{await close(tree);}
});

test('a successful reference read explains its effect on independent verification',async()=>{
 globalThis.fetch=async(url)=>{const p=new URL(url).pathname;return Response.json(p.endsWith('/pending')?{imports:[]}:p.endsWith('/bank')?{items:[item],total:1}:{id:999,answer_html:'Recorded reference'});};
 const tree=await mount();try{await act(async()=>{await tree.root.findByProps({className:'q-title'}).props.onClick();await flush();});assert.match(text(tree),/Recorded reference/);assert.match(text(tree),/该题族不再用于未辅助独立复测/);}finally{await close(tree);}
});

test('mapping review opens without loading the answer and requires an explicit approval', async () => {
 const calls = [];
 const criterion = {criterion_id:'c1',title:'Retrieval evaluation',behavior_key:null,behavior_version:null,reviewed_at:null};
 globalThis.fetch = async (url, options) => {
   const path = new URL(url).pathname;
   const body = options.body ? JSON.parse(options.body) : null;
   calls.push({ path, method: options.method, body });
   if (path.endsWith('/pending')) return Response.json({ imports: [] });
   if (path.endsWith('/bank')) return Response.json({ items: [item], total: 1 });
   if (options.method === 'PUT') return Response.json({ ...criterion, behavior_key: body.behavior_key, behavior_version: body.behavior_version });
   if (options.method === 'DELETE') return new Response(null, { status: 204 });
   return Response.json({ question_id: 999, rubric_version: 'rubric-v1', reviewable: true, criteria: [criterion] });
 };
 const tree = await mount();
 try {
   await act(async () => { button(tree, '复测映射').props.onClick(); await flush(); });
   assert.match(text(tree), /Retrieval evaluation/);
   assert.ok(button(tree, '确认并批准映射').props.disabled);
   assert.ok(!calls.some(({path}) => path.endsWith(`/bank/${seed.id}`)));
   assert.ok(!calls.some(({method}) => method === 'PUT'));
   const keyInput = tree.root.findByProps({ placeholder: 'rag.retrieval-metrics' });
   await act(async () => { keyInput.props.onChange({ target: { value: 'rag.retrieval-metrics' } }); });
   await act(async () => { await tree.root.findByType('form').props.onSubmit({ preventDefault() {} }); });
   assert.deepEqual(calls.find(({method}) => method === 'PUT').body, {
     rubric_version: 'rubric-v1', behavior_key: 'rag.retrieval-metrics', behavior_version: 1, confirm: true,
   });
   assert.match(text(tree), /已批准映射/);
   await act(async () => { await button(tree, '撤销映射').props.onClick(); });
   assert.match(text(tree), /已撤销映射/);
 } finally { await close(tree); }
});

test('revoke an exact historical mapping when the current rubric cannot be reviewed', async () => {
 const calls = [];
 globalThis.fetch = async (url, { method }) => {
   const target = new URL(url);
   calls.push({ target, method });
   if (target.pathname.endsWith('/pending')) return Response.json({ imports: [] });
   if (target.pathname.endsWith('/bank')) return Response.json({ items: [item], total: 1 });
   if (method === 'DELETE') return new Response(null, { status: 204 });
   return Response.json({
     question_id: 999, rubric_version: 'new', reviewable: false, criteria: [],
     historical_mappings: [
       { criterion_id: 'c1', rubric_version: 'legacy/v1', behavior_key: 'rag.legacy', behavior_version: 1 },
       { criterion_id: 'c1', rubric_version: 'legacy/v2', behavior_key: 'rag.legacy', behavior_version: 2 },
     ],
   });
 };
 const tree = await mount();
 try {
   await act(async () => { button(tree, '复测映射').props.onClick(); await flush(); });
   assert.match(text(tree), /当前评分标准未开放映射审核/);
   assert.match(text(tree), /legacy\/v1/);
   assert.ok(!button(tree, '确认并批准映射'));
   assert.ok(!calls.some(({ method }) => method === 'DELETE'));
   await act(async () => { await button(tree, '撤销历史映射').props.onClick(); });
   const { target } = calls.find(({ method }) => method === 'DELETE');
   assert.ok(target.pathname.endsWith('/retest-mappings/999/c1'));
   assert.equal(target.searchParams.get('rubric_version'), 'legacy/v1');
   assert.match(text(tree), /已撤销历史映射/);
   assert.ok(button(tree, '撤销历史映射'));
 } finally { await close(tree); }
});
