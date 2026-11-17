import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {act,create} from 'react-test-renderer';
import {MemoryRouter} from 'react-router-dom';
import DiagnosisReport from '../src/pages/DiagnosisReport.tsx';
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const text=tree=>JSON.stringify(tree.toJSON());
const frozen={rubric:{rubric_version:'rubric-fixed-v2',calibration_status:'AI_DRAFT',max_points:5,criteria:[{criterion_id:'c1',title:'Frozen retry boundary'}]}};
const staleFeedback={judge_reasoning:'Stale reasoning must stay hidden',hit_points:[],missed_points:[],next:'Stale next action',followups:[{kind:'Boundary',question:'Stale followup question',why:'Stale followup reason'}]};
const question={order_no:1,short_code:'PENDING-1',title:'Explain the boundary',qtype:'open',frozen,answer:{text_answer:'The user original answer.',points:null,max_points:5,verdict:null,feedback:staleFeedback}};
const report={id:93,title:'Durable grading fixture',kind:'bank_exam',status:'SCORING',score:null,gained_points:null,total_points:0,questions:[question],summary:{},diagnosis_deep:{per_question:{'1':{why:'Stale deep explanation',how:'Stale deep advice'}}}};
test.beforeEach(()=>{const values=new Map();globalThis.localStorage=globalThis.sessionStorage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};});
async function mount(value){globalThis.fetch=async()=>Response.json(value);let tree;await act(async()=>{tree=create(React.createElement(MemoryRouter,{initialEntries:['/exam/diagnosis/report?id=93'],future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(DiagnosisReport)));await flush();});return tree;}
async function close(tree){await act(async()=>tree.unmount());}

for(const status of ['QUEUED','RUNNING','RETRY_WAIT','FAILED','NEEDS_REVIEW'])test(`${status} is a current scoring state, not missing historical evidence or an evaluation`,async()=>{
 const task={id:5,status,completed_count:0,total_count:1,can_retry:status==='FAILED',questions:[{order_no:1,status,can_retry:status==='FAILED',error:status==='FAILED'?{message:'Safe provider timeout'}:null}]};
 const tree=await mount({...report,status:status==='FAILED'?'SCORING_FAILED':status==='NEEDS_REVIEW'?'NEEDS_REVIEW':'SCORING',scoring_task:task,questions:[status==='FAILED'?{...question,answer:{...question.answer,points:3,verdict:'PARTIAL'}}:question]});try{const output=text(tree);assert.match(output,/本题尚未有效评分/);assert.match(output,/The user original answer/);assert.match(output,/Frozen retry boundary/);assert.match(output,/rubric-fixed-v2/);assert.doesNotMatch(output,/历史记录未保存|没有逐项引用|命中要点|缺失要点|Stale/);const hero=tree.root.findByProps({className:'hero-chip'}).children.join('');assert.match(hero,/—\/5 分/);}finally{await close(tree);}
});

test('a successful zero-score question keeps its real feedback while another question waits',async()=>{
 const success={...question,answer:{...question.answer,points:0,verdict:'WRONG',judge_version:'judge-v2',feedback:{judge_reasoning:'Real completed zero-score reasoning',hit_points:[],missed_points:['Actual missed mechanism']}}};
 const tree=await mount({...report,questions:[success,{...question,order_no:2}],diagnosis_deep:null,scoring_task:{id:5,status:'RUNNING',completed_count:1,total_count:2,can_retry:false,questions:[{order_no:1,status:'SUCCEEDED',can_retry:false},{order_no:2,status:'RUNNING',can_retry:false}]}});try{assert.match(text(tree),/Real completed zero-score reasoning/);assert.match(text(tree),/Actual missed mechanism/);const header=tree.root.findByProps({id:'exam-question-2'}).findByProps({className:'report-q-head'});await act(async()=>header.props.onClick());assert.match(text(tree),/本题尚未有效评分/);assert.doesNotMatch(text(tree),/Stale reasoning/);}finally{await close(tree);}
});

test('a succeeded status without a valid numeric result cannot display evaluation content',async()=>{
 const tree=await mount({...report,scoring_task:{id:5,status:'RUNNING',completed_count:1,total_count:1,can_retry:false,questions:[{order_no:1,status:'SUCCEEDED',can_retry:false}]}});try{assert.match(text(tree),/本题尚未有效评分/);assert.doesNotMatch(text(tree),/Stale|命中要点|缺失要点/);}finally{await close(tree);}
});

test('a genuinely completed historical result retains its historical evidence limitation',async()=>{
 const tree=await mount({...report,status:'COMPLETED',score:60,total_points:5,gained_points:3,diagnosis_deep:null,questions:[{...question,frozen:null,answer:{text_answer:'Historical original answer',points:3,max_points:5,verdict:'PARTIAL',feedback:{hit_points:['Legacy satisfied point'],missed_points:['Legacy missing point']}}}]});try{assert.match(text(tree),/Legacy satisfied point/);assert.match(text(tree),/Legacy missing point/);assert.match(text(tree),/历史记录未保存|没有逐项引用/);assert.doesNotMatch(text(tree),/本题尚未有效评分/);}finally{await close(tree);}
});

test('partial criterion credit shows saved positive evidence without calling it a passing anchor or rounding the total',async()=>{
 const feedback={hit_points:[],missed_points:['Frozen retry boundary'],strengths:['The answer notices interruption.'],criterion_scores:[{criterion_id:'c1',title:'Frozen retry boundary',score:1,max_score:4,answer_quotes:['The user original answer.'],satisfied_anchors:['c1:1'],missing_points:['Define what was already delivered.'],reasoning_summary:'A useful observation is present but the contract is incomplete.',why_not_higher:'No explicit terminal event.'}]};
 const tree=await mount({...report,status:'COMPLETED',score:13.8,total_points:5,gained_points:0.6875,diagnosis_deep:null,questions:[{...question,answer:{...question.answer,points:0.6875,verdict:'WRONG',judge_version:'judge-v2',feedback}}]});try{const output=text(tree);assert.match(output,/13.8 分/);assert.doesNotMatch(output,/14 分|没有记录明确的正确要点/);assert.match(output,/已有部分依据/);assert.match(output,/The answer notices interruption/);assert.match(output,/The user original answer/);assert.equal(tree.root.findAllByType('b').some(node=>node.children.join('')==='命中要点：'),false);assert.match(output,/0.6875/);}finally{await close(tree);}
});
