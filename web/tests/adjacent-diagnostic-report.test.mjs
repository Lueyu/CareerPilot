import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {act,create} from 'react-test-renderer';
import {MemoryRouter,useLocation} from 'react-router-dom';
import DiagnosisReport from '../src/pages/DiagnosisReport.tsx';
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const text=tree=>JSON.stringify(tree.toJSON());
const report={id:91,title:'Streaming boundary answer',kind:'bank_exam',status:'COMPLETED',score:60,questions:[],summary:{}};
test.beforeEach(()=>{const values=new Map();globalThis.localStorage=globalThis.sessionStorage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};});
async function mount(value,onLocation=()=>{}){globalThis.fetch=async()=>Response.json(value);function Observer(){onLocation(useLocation());return null;}let tree;await act(async()=>{tree=create(React.createElement(MemoryRouter,{initialEntries:['/exam/diagnosis/report?id=91&return_task_id=1'],future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(Observer),React.createElement(DiagnosisReport)));await flush();});return tree;}
async function close(tree){await act(async()=>tree.unmount());}

test('report uses the actual exam title without calling every bank exam an independent diagnosis',async()=>{
 const tree=await mount(report);try{assert.equal(tree.root.findByType('h1').children.join(''),'Streaming boundary answer');assert.doesNotMatch(text(tree),/相邻场景诊断|NOT_COLLECTED|独立诊断报告/);}finally{await close(tree);}
});

test('a titleless report uses its actual exam kind as a neutral fallback',async()=>{
 const tree=await mount({...report,title:''});try{assert.equal(tree.root.findByType('h1').children.join(''),'面经考试报告');assert.doesNotMatch(text(tree),/相邻场景诊断|NOT_COLLECTED/);}finally{await close(tree);}
});

test('only the frozen adjacent diagnostic scope explains missing assistance declarations and retains the learning return link',async()=>{
 const question={order_no:1,exam_question_id:901,short_code:'adjacent-one',title:'A fresh adjacent question',qtype:'open',answer:null,frozen:{diagnostic_origin:{scope:'adjacent_scenario_diagnostic_not_course_completion_or_delayed_retest',external_assistance_declaration:'NOT_COLLECTED',source_task_id:1}}};let location;
 const tree=await mount({...report,questions:[question]},value=>location=value);try{assert.match(text(tree),/相邻场景诊断/);assert.match(text(tree),/未宣称课程完成/);assert.match(text(tree),/原失分项已通过/);assert.match(text(tree),/延迟保持/);assert.match(text(tree),/NOT_COLLECTED/);assert.match(text(tree),/尚未收集外部辅助声明/);const back=tree.root.findAllByType('button').find(node=>node.children.join('')==='返回这次学习路径');await act(async()=>back.props.onClick());assert.equal(location.pathname,'/learning/task/1');}finally{await close(tree);}
});
