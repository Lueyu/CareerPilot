import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {act,create} from 'react-test-renderer';
import {MemoryRouter,Routes,Route} from 'react-router-dom';
import {AppStateProvider} from '../src/components/AppState.tsx';
import Diagnosis from '../src/pages/Diagnosis.tsx';
import DiagnosisReport from '../src/pages/DiagnosisReport.tsx';
import {useSpeechRecognition} from '../src/lib/speech.ts';
import {useExamDraft} from '../src/lib/useExamDraft.ts';
import {api} from '../src/api/client.ts';

const flush=()=>new Promise(resolve=>setImmediate(resolve));
const text=tree=>JSON.stringify(tree.toJSON());
const button=(tree,label)=>tree.root.findAllByType('button').find(n=>n.children.join('').includes(label));
const storage=()=>{const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};};
let previousStorage;
test.beforeEach(()=>{previousStorage=globalThis.sessionStorage;globalThis.sessionStorage=storage();});
test.afterEach(()=>{globalThis.sessionStorage=previousStorage;});
async function mount(Page,path='/exam/diagnosis/report?id=25'){let tree;await act(async()=>{tree=create(React.createElement(MemoryRouter,{initialEntries:[path],future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(AppStateProvider,null,React.createElement(Routes,null,React.createElement(Route,{path:'*',element:React.createElement(Page)})))));await flush();});return tree;}
async function close(tree){await act(async()=>tree.unmount());}
const question={order_no:1,short_code:'Q-TEST',title:'Explain a transaction boundary',qtype:'open',options:null,skill:'Reliability',answer:{text_answer:'Original exact answer',revision:0}};
const exam={id:25,title:'Isolated fixture exam',kind:'diagnosis',status:'ACTIVE',duration_minutes:30,questions:[question]};
const scored={...question,reference_answer:'<p>A reviewed explanation</p>',frozen:{rubric:{rubric_version:'rubric-fixture-v2',calibration_status:'UNCALIBRATED'}},answer:{text_answer:'Original exact answer',points:3,max_points:5,verdict:'PARTIAL',judge_version:'judge-fixture-v2',feedback:{judge_reasoning:'The mechanism is present but the failure boundary is missing.',hit_points:['Mechanism'],missed_points:['Failure boundary']}}};
const report={id:25,title:'Isolated fixture exam',status:'COMPLETED',score:60,total_points:5,gained_points:3,questions:[scored],learning_tasks:[],summary:{},overall:null};

test('question ambiguity feedback saves to review without changing the current grade',async()=>{
 const sent=[];let saved=false;
 globalThis.fetch=async(url,o={})=>{const p=new URL(url).pathname;
  if(p.endsWith('/feedback')){sent.push(JSON.parse(o.body));saved=true;return Response.json({id:81,status:'PENDING_REVIEW',reason:sent[0].reason,category:sent[0].category});}
  if(p.endsWith('/assessment-feedback'))return Response.json({items:saved?[{id:81,status:'PENDING_REVIEW',reason:'The boundary in the question is ambiguous.',category:'QUESTION_AMBIGUITY'}]:[]});
  return Response.json({...report,questions:[{...scored,exam_question_id:251}]});};
 let tree=await mount(DiagnosisReport);try{
  const field=tree.root.findAllByType('textarea').find(n=>n.props['aria-label']==='题目反馈原因');assert.ok(field,'each expanded question exposes an ambiguity feedback field');
  await act(async()=>field.props.onChange({target:{value:'The boundary in the question is ambiguous.'}}));
  await act(async()=>{await button(tree,'提交题目反馈').props.onClick();await flush();});
  assert.equal(sent.length,1);assert.equal(sent[0].category,'QUESTION_AMBIGUITY');assert.ok(sent[0].request_key);
  assert.match(text(tree),/已保存.*待人工审核/);assert.match(text(tree),/60 分/);
  await close(tree);tree=await mount(DiagnosisReport);assert.match(text(tree),/已保存.*待人工审核/,'saved feedback survives reopening the report');
 }finally{await close(tree);}
});

test('failed ambiguity feedback preserves the text and never claims it entered review',async()=>{
 globalThis.fetch=async(url)=>{const p=new URL(url).pathname;if(p.endsWith('/feedback'))return Response.json({detail:'Feedback storage temporarily unavailable'},{status:503});if(p.endsWith('/assessment-feedback'))return Response.json({items:[]});return Response.json({...report,questions:[{...scored,exam_question_id:251}]});};
 const tree=await mount(DiagnosisReport);try{
  const field=tree.root.findAllByType('textarea').find(n=>n.props['aria-label']==='题目反馈原因');assert.ok(field);
  await act(async()=>field.props.onChange({target:{value:'Please clarify what successful isolation means.'}}));
  await act(async()=>{await button(tree,'提交题目反馈').props.onClick();await flush();});
  assert.equal(field.props.value,'Please clarify what successful isolation means.');assert.match(text(tree),/Feedback storage temporarily unavailable/);assert.doesNotMatch(text(tree),/已保存.*待人工审核/);
 }finally{await close(tree);}
});

test('typing the last answer autosaves its latest edit without leaving the question',async t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval']});const previous=globalThis.sessionStorage;globalThis.sessionStorage=storage();
 const saved=[];globalThis.fetch=async(url,o={})=>{const p=new URL(url).pathname;if(p.endsWith('/answers')){saved.push(JSON.parse(o.body));return Response.json({ok:true,revision:saved.length,saved_at:'2026-11-17T00:00:00Z'});}return Response.json(exam);};
 const tree=await mount(Diagnosis,'/exam/diagnosis?id=25');try{
  await act(async()=>tree.root.findByType('textarea').props.onChange({target:{value:'First edit'}}));
  await act(async()=>tree.root.findByType('textarea').props.onChange({target:{value:'Latest final edit'}}));
  await act(async()=>{t.mock.timers.tick(1500);await flush();});
  assert.equal(saved.length,1);assert.equal(saved[0].text_answer,'Latest final edit');assert.equal(saved[0].expected_revision,0);
  assert.match(text(tree),/已保存到服务器/);
 }finally{await close(tree);globalThis.sessionStorage=previous;t.mock.timers.reset();}
});

test('unknown scoring results never appear as a formal total or a zero ability bar',async()=>{
 globalThis.fetch=async()=>Response.json({...report,status:'SCORING_FAILED',score:99,scoring_failed:[1],questions:[{...scored,answer:{...scored.answer,points:null,verdict:'SCORING_FAILED'}}, {...scored,order_no:2}],skill_breakdown:[{skill_key:'reliability',skill_name:'Reliability',earned:null,max:10,ratio:null,q_count:2,order_nos:[1,2],verdicts:{CORRECT:0,PARTIAL:1,WRONG:0}}]});
 const tree=await mount(DiagnosisReport);try{
  assert.doesNotMatch(text(tree),/99 分/);assert.equal(tree.root.findAll(n=>n.props.style?.width==='0%').length,0);
  assert.match(text(tree),/尚无有效成绩|没有有效总分/);assert.match(text(tree),/评分未完成|尚未评分/);
  assert.match(text(tree),/Original exact answer/,'successful and failed answers remain inspectable');
 }finally{await close(tree);}
});

test('existing scoring reasoning and uncalibrated versions are visible without another model call',async()=>{
 globalThis.fetch=async()=>Response.json(report);const tree=await mount(DiagnosisReport);try{
  assert.match(text(tree),/The mechanism is present but the failure boundary is missing/);
  assert.match(text(tree),/未校准/);assert.match(text(tree),/rubric-fixture-v2/);assert.match(text(tree),/judge-fixture-v2/);
 }finally{await close(tree);}
});

test('reference answers cannot inject event handlers or script URLs into the report DOM',async()=>{
 globalThis.fetch=async()=>Response.json({...report,questions:[{...scored,reference_answer:'<p>Useful explanation</p><img src=x onerror="alert(1)"><a href="javascript:alert(1)">unsafe</a><script>alert(1)</script>'}]});
 const tree=await mount(DiagnosisReport);try{
  assert.equal(tree.root.findAll(n=>n.props.dangerouslySetInnerHTML && /onerror|javascript:|<script/i.test(n.props.dangerouslySetInnerHTML.__html)).length,0);
  assert.match(text(tree),/Useful explanation/);
 }finally{await close(tree);}
});

test('retry accepts a task acknowledgement, preserves partial answers, and polls only the same exam',async t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval']});let started=false;const calls=[];
 globalThis.fetch=async(url,o={})=>{const p=new URL(url).pathname;calls.push(p);if(p.endsWith('/rescore')){started=true;return Response.json({exam_id:25,task_id:71,status:'SCORING',report_url:'/exams/25/report'},{status:202});}
  return Response.json({...report,status:started?'SCORING':'SCORING_FAILED',score:null,scoring_failed:[1],scoring_task:{id:71,status:started?'RUNNING':'FAILED',completed_count:1,total_count:2,can_retry:!started,questions:[{order_no:1,status:started?'RUNNING':'FAILED',can_retry:!started,error:{code:'TIMEOUT',message:'The model request timed out.'}}]}});};
 const tree=await mount(DiagnosisReport);try{
  await act(async()=>{await button(tree,'重新评分').props.onClick();await flush();});
  assert.match(text(tree),/Original exact answer/,'202 acknowledgement must not replace a report with task metadata');
  await act(async()=>{t.mock.timers.tick(5000);await flush();});
  assert.ok(calls.filter(p=>p.endsWith('/25/report')).length>=2);assert.ok(calls.every(p=>p.includes('/25/')));
  assert.doesNotMatch(text(tree),/60 分/);
 }finally{await close(tree);t.mock.timers.reset();}
});

test('autosave serializes late edits with the acknowledged server revision',async t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval']});const sent=[],releases=[];
 globalThis.fetch=async(url,o={})=>{if(new URL(url).pathname.endsWith('/answers')){sent.push(JSON.parse(o.body));return new Promise(resolve=>releases.push(()=>resolve(Response.json({ok:true,revision:sent.length,saved_at:'now'}))));}return Response.json(exam);};
 const tree=await mount(Diagnosis,'/exam/diagnosis?id=25');try{
  await act(async()=>tree.root.findByType('textarea').props.onChange({target:{value:'First in flight'}}));
  await act(async()=>{t.mock.timers.tick(1000);await flush();});
  await act(async()=>tree.root.findByType('textarea').props.onChange({target:{value:'Latest edit while saving'}}));
  await act(async()=>{t.mock.timers.tick(2000);await flush();});assert.equal(sent.length,1);
  await act(async()=>{releases[0]();await flush();});
  assert.equal(sent.length,2);assert.equal(sent[1].expected_revision,1);assert.equal(sent[1].text_answer,'Latest edit while saving');
  assert.doesNotMatch(text(tree),/已保存到服务器。/);
  await act(async()=>{releases[1]();await flush();});assert.match(text(tree),/已保存到服务器。/);
 }finally{releases.forEach(r=>r());await close(tree);t.mock.timers.reset();}
});

test('a local dirty revision cannot overwrite a newer server answer after refresh',async t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval']});
 globalThis.sessionStorage.setItem('cp-exam-draft:personal:25',JSON.stringify({version:2,answers:[{order_no:1,value:'My unsaved correction',revision:1,dirty:true}]}));
 const saved=[];globalThis.fetch=async(url,o={})=>{if(new URL(url).pathname.endsWith('/answers')){saved.push(JSON.parse(o.body));return Response.json({ok:true,revision:3,saved_at:'now'});}return Response.json({...exam,questions:[{...question,answer:{text_answer:'Newer server answer',revision:2}}]});};
 const tree=await mount(Diagnosis,'/exam/diagnosis?id=25');try{
  assert.equal(tree.root.findByType('textarea').props.value,'My unsaved correction');assert.match(text(tree),/服务器已有不同版本/);
  await act(async()=>{t.mock.timers.tick(1500);await flush();});assert.equal(saved.length,0);
  await act(async()=>button(tree,'按最新版本保存我的编辑').props.onClick());
  await act(async()=>{t.mock.timers.tick(1500);await flush();});assert.equal(saved[0].expected_revision,2);assert.equal(saved[0].text_answer,'My unsaved correction');
 }finally{await close(tree);t.mock.timers.reset();}
});

test('the final edit is atomically submitted and a frozen failed response never reopens the answer',async()=>{
 let frozen=false,payload,detailReads=0;
 globalThis.fetch=async(url,o={})=>{const p=new URL(url).pathname;
  if(p.endsWith('/submit')){payload=JSON.parse(o.body);frozen=true;return Response.json({detail:'The reply was interrupted after freezing'},{status:502});}
  if(p.endsWith('/answers'))assert.fail('final submission must not make separate non-atomic answer saves');
  detailReads++;return Response.json({...exam,status:frozen?'SCORING_FAILED':'ACTIVE'});
 };
 function Flow(){return React.createElement(Routes,null,React.createElement(Route,{path:'/exam/diagnosis',element:React.createElement(Diagnosis)}),React.createElement(Route,{path:'/exam/diagnosis/report',element:React.createElement('p',null,'Only this frozen report')}));}
 const tree=await mount(Flow,'/exam/diagnosis?id=25');try{
  await act(async()=>tree.root.findByType('textarea').props.onChange({target:{value:'Final unsaved keystroke'}}));
  await act(async()=>button(tree,'提交答卷').props.onClick());
  await act(async()=>{await button(tree,'确认提交').props.onClick();await flush();});
  assert.deepEqual(payload.answers,[{order_no:1,text_answer:'Final unsaved keystroke',expected_revision:0}]);
  assert.ok(detailReads>=2);assert.equal(tree.root.findAllByType('textarea').length,0);assert.match(text(tree),/Only this frozen report/);
 }finally{await close(tree);}
});

test('strict criterion quotes and anchor descriptions connect to a clearly labelled AI reference draft',async()=>{
 const q={...scored,frozen:{rubric:{rubric_version:'r2',calibration_status:'UNCALIBRATED',criteria:[{criterion_id:'c1',title:'Explain rollback',score_anchors:[{anchor_id:'c1:2',score:2,description:'Names rollback but misses the boundary'}]}],reference_standard:{alignment_status:'AI_DRAFT_NOT_HUMAN_REVIEWED',sections:[{topic:'mechanism',title:'Rollback mechanism',text:'Follow the transaction lifecycle',criterion_ids:['c1']}],levels:{minimum:'Minimum example',good:'Good example',excellent:'Excellent example'},oral_60_90_seconds:'Start with the failure boundary',core_points:['Rollback'],bonus_points:['Isolation tradeoff'],followups:[{question:'What about nested transactions?',answer:'Define the boundary first',criterion_ids:['c1']}]}}},answer:{...scored.answer,feedback:{criterion_scores:[{criterion_id:'c1',score:2,max_score:4,answer_quotes:['Original exact answer'],satisfied_anchors:['c1:2'],missing_points:['Missing isolation guarantee'],reasoning_summary:'Partial explanation',why_not_higher:'No boundary example'}]}}};
 globalThis.fetch=async()=>Response.json({...report,questions:[q]});const tree=await mount(DiagnosisReport);try{
  assert.match(text(tree),/Explain rollback/);assert.match(text(tree),/Names rollback but misses the boundary/);assert.match(text(tree),/No boundary example/);
  assert.match(text(tree),/未经人工审核|尚未人工审核/);assert.match(text(tree),/Start with the failure boundary/);assert.match(text(tree),/What about nested transactions/);
  assert.ok(tree.root.findAllByType('blockquote').some(n=>n.children.join('')==='Original exact answer'));
 }finally{await close(tree);}
});

test('speech stop waits for its final transcript and ignores callbacks from a replaced recorder',async()=>{
 const previousWindow=globalThis.window;const instances=[],received=[];
 class Recognition{constructor(){instances.push(this);}start(){}stop(){}abort(){}}
 globalThis.window={SpeechRecognition:Recognition};let speech;
 function Speech(){speech=useSpeechRecognition(text=>received.push(text));return React.createElement('p',null,String(speech.listening));}
 const tree=await mount(Speech);try{
  await act(async()=>speech.start());const first=instances[0];await act(async()=>speech.stop());
  assert.equal(speech.listening,true,'stop request must not unlock submission before final/onend');
  await act(async()=>first.onresult({resultIndex:0,results:[{isFinal:true,0:{transcript:'Final transcript'}}]}));
  await act(async()=>first.onend());assert.equal(speech.listening,false);assert.deepEqual(received,['Final transcript']);
  await act(async()=>speech.start());const second=instances[1];await act(async()=>speech.start());const third=instances[2];
  await act(async()=>{second.onresult({resultIndex:0,results:[{isFinal:true,0:{transcript:'Old recorder text'}}]});second.onend();});
  assert.equal(speech.listening,true);assert.deepEqual(received,['Final transcript']);
  await act(async()=>third.onend());
 }finally{await close(tree);globalThis.window=previousWindow;}
});

test('a server-side freeze discovered by autosave locks the current page without losing its local draft',async t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval']});let frozen=false;
 globalThis.fetch=async(url,o={})=>{if(new URL(url).pathname.endsWith('/answers')){frozen=true;return Response.json({detail:'exam status is SCORING, cannot save answers'},{status:409});}return Response.json({...exam,status:frozen?'SCORING':'ACTIVE'});};
 function Flow(){return React.createElement(Routes,null,React.createElement(Route,{path:'/exam/diagnosis',element:React.createElement(Diagnosis)}),React.createElement(Route,{path:'/exam/diagnosis/report',element:React.createElement('p',null,'Frozen answer status')}));}
 const tree=await mount(Flow,'/exam/diagnosis?id=25');try{
  await act(async()=>tree.root.findByType('textarea').props.onChange({target:{value:'Edit racing another tab submit'}}));
  await act(async()=>{t.mock.timers.tick(1200);await flush();});
  assert.equal(tree.root.findAllByType('textarea').length,0);assert.match(text(tree),/Frozen answer status/);
  assert.match(globalThis.sessionStorage.getItem('cp-exam-draft:personal:25'),/Edit racing another tab submit/);
 }finally{await close(tree);t.mock.timers.reset();}
});

test('answer conflicts display the safe server message instead of object coercion',async()=>{
 globalThis.fetch=async()=>Response.json({detail:{code:'ANSWER_REVISION_CONFLICT',message:'Keep your local correction and choose a version',server_revision:3}},{status:409});
 await assert.rejects(api.post('/exams/25/answers',{}),/Keep your local correction and choose a version/);
});

test('a slow save from the previous exam cannot block autosave in a different exam',async t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval']});let changeExam,draft,finishOld;const paths=[];
 globalThis.fetch=async(url)=>{const p=new URL(url).pathname;paths.push(p);if(p.includes('/25/'))return new Promise(resolve=>{finishOld=()=>resolve(Response.json({ok:true,revision:1,saved_at:'now'}));});return Response.json({ok:true,revision:1,saved_at:'now'});};
 function Probe(){const [value,setValue]=React.useState(exam);changeExam=setValue;draft=useExamDraft(value,true);return React.createElement('p',null,draft.entries[0]?.value);}
 const tree=await mount(Probe);try{
  await act(async()=>draft.edit(0,'Old exam edit'));await act(async()=>{t.mock.timers.tick(1000);await flush();});
  await act(async()=>changeExam({...exam,id:26}));await act(async()=>draft.edit(0,'New exam edit'));
  await act(async()=>{t.mock.timers.tick(1000);await flush();});
  assert.ok(paths.includes('/api/v1/exams/26/answers'),'the current exam must save independently of an old request');
  await act(async()=>{finishOld();await flush();});assert.equal(draft.entries[0].value,'New exam edit');assert.equal(draft.entries[0].dirty,false);
 }finally{finishOld?.();await close(tree);t.mock.timers.reset();}
});

test('an uncertain submission stays read-only when the follow-up state read also fails',async()=>{
 let attempting=false;globalThis.fetch=async(url)=>{if(new URL(url).pathname.endsWith('/submit'))attempting=true;return attempting?Response.json({detail:'Network confirmation unavailable'},{status:503}):Response.json(exam);};
 const tree=await mount(Diagnosis,'/exam/diagnosis?id=25');try{
  await act(async()=>tree.root.findByType('textarea').props.onChange({target:{value:'Preserve this final draft'}}));
  await act(async()=>button(tree,'提交答卷').props.onClick());await act(async()=>{await button(tree,'确认提交').props.onClick();await flush();});
  assert.equal(tree.root.findAllByType('textarea').length,0);assert.match(text(tree),/答卷保持只读|答卷只读/);assert.ok(button(tree,'重新核对本次状态'));
  assert.match(globalThis.sessionStorage.getItem('cp-exam-draft:personal:25'),/Preserve this final draft/);
 }finally{await close(tree);}
});
