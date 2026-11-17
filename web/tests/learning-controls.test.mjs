import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {act,create} from 'react-test-renderer';
import {MemoryRouter,Route,Routes,useLocation} from 'react-router-dom';
import {AppStateProvider} from '../src/components/AppState.tsx';
import Diagnosis from '../src/pages/Diagnosis.tsx';
import DiagnosisReport from '../src/pages/DiagnosisReport.tsx';
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const text=tree=>JSON.stringify(tree.toJSON());
const button=(tree,label)=>tree.root.findAllByType('button').find(n=>n.children.join('').includes(label));
test.beforeEach(()=>{const values=new Map();globalThis.localStorage=globalThis.sessionStorage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};});
async function close(tree){await act(async()=>tree.unmount());}

test('a stale plan adjustment keeps the reason and sends the observed plan revision',async()=>{
 let Component;try{Component=(await import('../src/components/LearningPlanControls.tsx')).default;}catch(e){assert.fail(`Plan controls are required: ${e.message}`);}const sent=[];let changes=0;
 globalThis.fetch=async(url,o={})=>{sent.push([new URL(url).pathname,JSON.parse(o.body)]);return Response.json({detail:'Plan revision changed; inspect the new feedback.'},{status:409});};
 let tree;await act(async()=>{tree=create(React.createElement(Component,{goalId:701,revision:3,status:'ACTIVE',activityId:711,activityStatus:'READY',onChanged:()=>changes++}));});
 try{await act(async()=>tree.root.findByProps({'aria-label':'学习计划调整原因'}).props.onChange({target:{value:'I have ten minutes today.'}}));await act(async()=>{await button(tree,'保存这次调整').props.onClick();await flush();});assert.equal(sent[0][1].expected_plan_revision,3);assert.equal(sent[0][1].action,'shorten');assert.equal(tree.root.findByProps({'aria-label':'学习计划调整原因'}).props.value,'I have ten minutes today.');assert.equal(changes,0);assert.match(text(tree),/Plan revision changed/);}finally{await close(tree);}
});

test('a finished verification keeps its original learning-path return link',async()=>{
 globalThis.fetch=async(url)=>Response.json(new URL(url).pathname.endsWith('/report')?{id:81,status:'COMPLETED',title:'Verification fixture',score:60,questions:[],summary:{}}:{id:81,status:'COMPLETED',questions:[]});
 let location;function Observer(){location=useLocation();return null;}
 let tree;await act(async()=>{tree=create(React.createElement(MemoryRouter,{initialEntries:['/exam/diagnosis?id=81&return_task_id=77'],future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(AppStateProvider,null,React.createElement(Observer),React.createElement(Routes,null,React.createElement(Route,{path:'/exam/diagnosis',element:React.createElement(Diagnosis)}),React.createElement(Route,{path:'/exam/diagnosis/report',element:React.createElement(DiagnosisReport)}),React.createElement(Route,{path:'/learning/task/:id',element:React.createElement('p',null,'Original learning goal')})))));await flush();await flush();});
 try{assert.equal(location.pathname,'/exam/diagnosis/report');assert.equal(location.search,'?id=81&return_task_id=77');await act(async()=>button(tree,'返回这次学习路径').props.onClick());assert.equal(location.pathname,'/learning/task/77');}finally{await close(tree);}
});
