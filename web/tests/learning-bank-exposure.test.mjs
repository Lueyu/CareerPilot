import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {readFile} from 'node:fs/promises';
import {act,create} from 'react-test-renderer';
import {MemoryRouter,useLocation} from 'react-router-dom';
import Bank from '../src/pages/Bank.tsx';
import {AppStateProvider} from '../src/components/AppState.tsx';
const seed=JSON.parse(await readFile(new URL('../src/data/bank.json',import.meta.url),'utf8')).questions.find(q=>q.html);
const item={id:999,short_code:seed.id,title:'Reference exposure fixture',qtype:'short_answer',topic:'rag',difficulty:2,source_type:'PUBLIC_LICENSED',status:'APPROVED',tags:[]};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const text=tree=>JSON.stringify(tree.toJSON());
const button=(tree,label)=>tree.root.findAllByType('button').find(n=>n.children.join('').includes(label));
test.beforeEach(()=>{const map=new Map();globalThis.localStorage=globalThis.sessionStorage={getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)};globalThis.window={location:{search:''},addEventListener(){},removeEventListener(){}};});
async function mount(onLocation=()=>{}){function Observer(){onLocation(useLocation());return null;}let tree;await act(async()=>{tree=create(React.createElement(MemoryRouter,{future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(AppStateProvider,null,React.createElement(Observer),React.createElement(Bank))));await flush();});return tree;}
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
