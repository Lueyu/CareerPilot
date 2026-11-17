import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {act,create} from 'react-test-renderer';
import {MemoryRouter} from 'react-router-dom';
import {AppStateProvider} from '../src/components/AppState.tsx';
import Memory from '../src/pages/Memory.tsx';
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const text=tree=>JSON.stringify(tree.toJSON());
const button=(tree,label)=>tree.root.findAllByType('button').find(n=>n.children.join('').includes(label));
const candidate={id:91,revision:3,status:'CANDIDATE',freshness:'CURRENT',content:'This answer omitted the retry boundary; scope is one practice.',eligible_for_personalization:false,meta:{goal_id:701,submission_id:721,submission_revision:1,scoring_run_id:88,origin_refs:{exam_id:25,criterion_id:'c1'}}};
async function mount(props={}){let Component;try{Component=(await import('../src/components/LearningMemoryReview.tsx')).default;}catch(e){assert.fail(`Learning memory review is required: ${e.message}`);}let tree;await act(async()=>{tree=create(React.createElement(Component,{goalId:701,items:[candidate],onReviewed:()=>{},...props}));await flush();});return tree;}
async function close(tree){await act(async()=>tree.unmount());}

test('learning candidate review sends its exact revision and never activates a stale inference',async()=>{
 const sent=[];globalThis.fetch=async(url,o={})=>{sent.push([new URL(url).pathname,JSON.parse(o.body)]);return Response.json({results:[{id:91,status:'CONFIRMED',revision:4}]});};
 const tree=await mount({items:[candidate,{...candidate,id:92,freshness:'NEEDS_REVIEW'}]});try{assert.equal(sent.length,0);const confirm=tree.root.findAllByType('button').filter(n=>n.children.join('')==='确认记忆');assert.equal(confirm.length,2);assert.equal(confirm[1].props.disabled,true);await act(async()=>{await confirm[0].props.onClick();await flush();});assert.deepEqual(sent[0],['/api/v1/learning/goals/701/memory-review',{items:[{candidate_id:91,expected_revision:3,action:'confirm'}]}]);assert.match(text(tree),/不代表.*掌握|不提高能力/);}finally{await close(tree);}
});

test('a conflicting learning memory edit preserves the edited text and reports no false success',async()=>{
 globalThis.fetch=async()=>Response.json({results:[{id:91,status:'CONFLICT',revision:4,error:'The source changed; review the latest candidate.'}]});
 const tree=await mount();try{await act(async()=>button(tree,'编辑后确认').props.onClick());await act(async()=>tree.root.findByProps({'aria-label':'学习候选记忆内容'}).props.onChange({target:{value:'A carefully bounded observation.'}}));await act(async()=>{await button(tree,'保存并确认').props.onClick();await flush();});assert.equal(tree.root.findByProps({'aria-label':'学习候选记忆内容'}).props.value,'A carefully bounded observation.');assert.match(text(tree),/source changed/);assert.doesNotMatch(text(tree),/已确认 1 条/);}finally{await close(tree);}
});

test('memory center never calls a stale learning inference currently effective or confirms it via the legacy route',async()=>{
 globalThis.fetch=async(url)=>Response.json({items:new URL(url).pathname.endsWith('/memory')?[{...candidate,status:'ACTIVE',freshness:'NEEDS_REVIEW',mtype:'LearningObservation',source:'SYSTEM',created_at:'2026-11-17T00:00:00Z'}]:[]});
 let tree;await act(async()=>{tree=create(React.createElement(MemoryRouter,{future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(AppStateProvider,null,React.createElement(Memory))));await flush();});
 try{const row=tree.root.findAll(n=>n.props.className==='mem-item')[0];assert.doesNotMatch(JSON.stringify(row.toJSON?.() || row.findAllByType('span').map(n=>n.children)),/当前有效/);assert.match(text(tree),/需要重新确认|需要复核/);assert.match(text(tree),/学习路径|学习目标/);}finally{await close(tree);}
});
