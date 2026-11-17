import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {act,create} from 'react-test-renderer';
import {MemoryRouter} from 'react-router-dom';
import Observatory from '../src/pages/Observatory.tsx';
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const text=t=>JSON.stringify(t.toJSON());
async function mount(){let t;await act(async()=>{t=create(React.createElement(MemoryRouter,{future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(Observatory)));await flush();});return t;}
function responses(cost){return async url=>new URL(url).pathname.endsWith('/observatory')?Response.json({stats:{total_runs:1,success_rate:100,avg_latency_ms:40,cost_cny:cost,failures:0},engines:[],recent_failures:[]}):Response.json({items:[]});}
test('unknown provider cost remains unknown in the summary and advanced view',async()=>{
 globalThis.fetch=responses(null);const t=await mount();try{assert.match(text(t),/成本未知|费用未知/);await act(async()=>t.root.findByProps({'aria-label':'切换高级数据'}).props.onClick());assert.match(text(t),/未知/);assert.doesNotMatch(text(t),/¥0\.00|42\.50/);}finally{await act(async()=>t.unmount());}
});
test('recorded numeric costs remain visible without invented traces for empty lists',async()=>{
 globalThis.fetch=responses(12.5);const t=await mount();try{assert.match(text(t),/12\.50/);assert.doesNotMatch(text(t),/TR-1042|EV-041|5 个引擎全部健康/);}finally{await act(async()=>t.unmount());}
});
test('unavailable observatory never substitutes sample costs or events',async()=>{
 globalThis.fetch=async()=>Response.json({detail:'observation store unavailable'},{status:503});const t=await mount();try{assert.match(text(t),/observation store unavailable/);assert.doesNotMatch(text(t),/42\.50|1,284|TR-1042|EV-041|静态演示数据/);}finally{await act(async()=>t.unmount());}
});
