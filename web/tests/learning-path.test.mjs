import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {act,create} from 'react-test-renderer';
import {MemoryRouter,Route,Routes} from 'react-router-dom';
import LearningTask from '../src/pages/LearningTask.tsx';
import {AppStateProvider} from '../src/components/AppState.tsx';

const flush=()=>new Promise(resolve=>setImmediate(resolve));
const text=tree=>JSON.stringify(tree.toJSON());
const button=(tree,label)=>tree.root.findAllByType('button').find(n=>n.children.join('').includes(label));
const noInventedMastery=tree=>assert.equal(tree.root.findAll(n=>String(n.props.className || '').includes('chip') && n.children.some(c=>c==='已掌握' || c==='本次通过')).length,0);
const task={id:77,title:'Explain the exact failure boundary',kind:'PRACTICE',status:'IN_PROGRESS',est_minutes:15,source_exam_id:25,source_exam_question_id:251,source_criterion:'Failure boundary',content:{observation:{answer_excerpt:'I would retry.',criterion_detail:'Explain the boundary before retrying.'}}};
const assistance={hint_used:false,reference_viewed:false,ai_declared:false,feedback_seen:false,events:[]};
const goal={id:701,target_behavior:'Independently explain which operation can be retried.',status:'ACTIVE',evidence_status:'VALID',plan_revision:1,success_criteria_version:'criteria-v1',origin_refs:{exam_id:25,exam_question_id:251,answer_revision_id:8,criterion_id:'c1',rubric_version:'rubric-v2'},verification:{understanding:'UNVERIFIED',practice:'NOT_SUBMITTED',transfer:'UNVERIFIED',retention:'NOT_DUE'},allowed_actions:['prepare','complete']};
const activity={id:711,kind:'CONCEPT',title:'Explain the boundary in a different example',status:'READY',position:1,revision:1,content_version:'activity-v1',prompt:'Explain why repeating a committed write can be unsafe.',required_output:'A short explanation and one failure example.',success_criteria:['Distinguish the write from its acknowledgement.'],allowed_actions:['submit','hint','reference'],assistance,latest_submission:null};
const path={task_id:77,task_status:'IN_PROGRESS',goal,activities:[activity],submissions:[],learning_content:{analogy:'A receipt can be lost after a purchase.',example:'A response times out after the database commit.',principles:['Separate side effects from acknowledgements.']},candidates:[]};
const submission={id:721,activity_id:711,revision:1,parent_submission_id:null,status:'COMPLETED',scoring_run_id:88,assistance,has_feedback:true,can_retry:false,error:null};
const feedback={correct:[{criterion_id:'c1',quotes:['commit succeeded'],why:'The answer distinguishes acknowledgement from committed state.'}],gaps:[{criterion_id:'c1',kind:'MISSING_OR_UNCLEAR',quotes:[],why:'The response does not define duplicate handling.',minimal_hint:'Identify which effect must happen only once.'}],next_action:'REVISE',scope_note:'This is practice feedback, not independent verification.',versions:{rubric_version:'rubric-v2',judge_version:'judge-v2'}};
let oldStorage;
test.beforeEach(()=>{oldStorage=globalThis.localStorage;const values=new Map();globalThis.localStorage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};});
test.afterEach(()=>{globalThis.localStorage=oldStorage;});
async function mount(){let Component;try{Component=(await import('../src/components/LearningPathPanel.tsx')).default;}catch(e){assert.fail(`Learning path component is required: ${e.message}`);}let tree;await act(async()=>{tree=create(React.createElement(MemoryRouter,{future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(Component,{task})));await flush();});return tree;}
async function close(tree){await act(async()=>tree.unmount());}

test('learning path keeps reading free of model calls and prepares activities only on explicit action',async()=>{
 const calls=[];globalThis.fetch=async(url,o={})=>{const p=new URL(url).pathname;calls.push([p,o.method||'GET']);return Response.json(p.endsWith('/prepare')?path:{task_id:77,goal:null,activities:[],submissions:[],learning_content:null});};
 const tree=await mount();try{assert.deepEqual(calls,[['/api/v1/learning/tasks/77/path','GET']]);await act(async()=>{await button(tree,'准备练习').props.onClick();await flush();});assert.ok(calls.some(([p,m])=>p.endsWith('/prepare')&&m==='POST'));assert.match(text(tree),/Independently explain/);assert.match(text(tree),/Explain why repeating/);assert.match(text(tree),/不代表独立复测|不是独立复测/);}finally{await close(tree);}
});

test('the existing task route opens the learning path and never silently generates another lesson',async()=>{
 const calls=[];globalThis.fetch=async(url)=>{const p=new URL(url).pathname;calls.push(p);if(p.endsWith('/path'))return Response.json(path);return Response.json(task);};
 let tree;await act(async()=>{tree=create(React.createElement(MemoryRouter,{initialEntries:['/learning/task/77'],future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(AppStateProvider,null,React.createElement(Routes,null,React.createElement(Route,{path:'/learning/task/:id',element:React.createElement(LearningTask)})))));await flush();await flush();});
 try{assert.match(text(tree),/当前学习目标/);assert.match(text(tree),/Explain why repeating/);assert.ok(calls.every(p=>!p.endsWith('/content')));assert.ok(button(tree,'打开原教学内容'));}finally{await close(tree);}
});

test('a practice submission freezes the local revision and stays pending until its own feedback exists',async()=>{
 const calls=[];let submitted=false;
 globalThis.fetch=async(url,o={})=>{const p=new URL(url).pathname;calls.push([p,o.body?JSON.parse(o.body):null]);if(p.endsWith('/submissions')){submitted=true;return Response.json({submission_id:721,exam_id:81,scoring_run_id:88,status:'SCORING',feedback_url:'/learning/submissions/721'},{status:202});}return Response.json(submitted?{...path,submissions:[{...submission,status:'SCORING',has_feedback:false}],activities:[{...activity,latest_submission:{...submission,status:'SCORING',has_feedback:false}}]}:path);};
 const tree=await mount();try{
  await act(async()=>tree.root.findByProps({'aria-label':'练习答案'}).props.onChange({target:{value:'The commit succeeded but the reply was lost.'}}));
  await act(async()=>tree.root.findByProps({'aria-label':'本次使用了 AI 辅助'}).props.onChange({target:{checked:true}}));
  await act(async()=>{await button(tree,'提交练习').props.onClick();await flush();});
  const sent=calls.find(([p])=>p.endsWith('/submissions'))[1];assert.equal(sent.payload.text_answer,'The commit succeeded but the reply was lost.');assert.equal(sent.payload.ai_declared,true);assert.equal(sent.expected_activity_revision,1);assert.ok(sent.request_key);
  assert.match(text(tree),/后台检查|等待反馈/);noInventedMastery(tree);
 }finally{await close(tree);}
});

test('viewing feedback explicitly records exposure and revisions retain their parent submission',async()=>{
 const calls=[];
 globalThis.fetch=async(url,o={})=>{const p=new URL(url).pathname;calls.push([p,o.body?JSON.parse(o.body):null]);if(p.endsWith('/submissions/721'))return Response.json({...submission,feedback,assistance:{...assistance,feedback_seen:true}});if(p.endsWith('/submissions'))return Response.json({submission_id:722,exam_id:82,scoring_run_id:89,status:'SCORING'},{status:202});return Response.json({...path,submissions:[submission],activities:[{...activity,latest_submission:submission}]});};
 const tree=await mount();try{
  assert.equal(calls.some(([p])=>p.endsWith('/submissions/721')),false);assert.doesNotMatch(text(tree),/duplicate handling/);
  await act(async()=>{await button(tree,'查看本次反馈').props.onClick();await flush();});assert.match(text(tree),/duplicate handling/);assert.match(text(tree),/commit succeeded/);assert.match(text(tree),/修改后再提交|修订/);
  await act(async()=>tree.root.findByProps({'aria-label':'练习答案'}).props.onChange({target:{value:'I now bound retries to the idempotent operation.'}}));
  await act(async()=>{await button(tree,'提交修订').props.onClick();await flush();});assert.equal(calls.find(([p])=>p.endsWith('/submissions'))[1].parent_submission_id,721);
 }finally{await close(tree);}
});

test('failed assistance recording cannot reveal the reference answer or mark a success',async()=>{
 globalThis.fetch=async(url)=>new URL(url).pathname.endsWith('/assistance')?Response.json({detail:'Exposure storage unavailable'},{status:503}):Response.json(path);
 const tree=await mount();try{await act(async()=>{await button(tree,'查看参考').props.onClick();await flush();});assert.match(text(tree),/Exposure storage unavailable/);assert.doesNotMatch(text(tree),/辅助已记录|Reference secret content/);}finally{await close(tree);}
});

test('an old success remains history while a newer failed submission exposes retry without judging the learner',async()=>{
 const failed={...submission,id:722,revision:2,parent_submission_id:721,status:'SCORING_FAILED',has_feedback:false,can_retry:true,error:{code:'TIMEOUT',message:'Model request timed out.'}};
 globalThis.fetch=async()=>Response.json({...path,submissions:[submission,failed],activities:[{...activity,latest_submission:failed}]});
 const tree=await mount();try{assert.match(text(tree),/Model request timed out/);assert.ok(button(tree,'重试本次检查'));assert.doesNotMatch(text(tree),/你没掌握|本次通过|duplicate handling/);assert.match(text(tree),/历史提交|提交历史/);}finally{await close(tree);}
});

test('frozen structured criteria render as standards and completing reading does not imply verification',async()=>{
 const calls=[];globalThis.fetch=async(url,o={})=>{const p=new URL(url).pathname;calls.push(p);if(p.endsWith('/complete'))return Response.json({status:'DONE',mastery_updated:false});return Response.json({...path,goal:{...goal,allowed_actions:['complete_reading']},activities:[{...activity,success_criteria:[{criterion_id:'c1',title:'Bound the side effect',required_evidence:['Name the once-only operation'],score_anchors:[{anchor_id:'c1:3',score:3,description:'Explains a concrete duplicate boundary'}]}]}]});};
 const tree=await mount();try{assert.match(text(tree),/Bound the side effect/);assert.match(text(tree),/Name the once-only operation/);await act(async()=>{await button(tree,'记录学习完成').props.onClick();await flush();});assert.ok(calls.some(p=>p.endsWith('/complete')));noInventedMastery(tree);}finally{await close(tree);}
});


test('project feedback separates code review, supplied logs and personal understanding',async()=>{
 const projectFeedback={...feedback,project_feedback:{test_verification:'NOT_EXECUTED',understanding:'UNVERIFIED',scope_note:'Source inspection only.',defense_questions:['Why does this bound retries?'],acceptance_version:'contract-v1'}};
 globalThis.fetch=async(url)=>Response.json(new URL(url).pathname.endsWith('/submissions/721')?{...submission,feedback:projectFeedback,payload:{answer_text:'[src/worker.py:L2] bounded retry',test_evidence:{execution:'NOT_EXECUTED',user_log_supplied:true,ci_verified:false}}}:{...path,submissions:[submission],activities:[{...activity,latest_submission:submission}]});
 const tree=await mount();try{await act(async()=>{await button(tree,'查看本次反馈').props.onClick();await flush();});assert.match(text(tree),/Why does this bound retries/);assert.match(text(tree),/运行效果未验证|未执行项目测试/);assert.match(text(tree),/个人理解仍待验证/);assert.match(text(tree),/src\/worker.py:L2/);assert.match(text(tree),/contract-v1/);}finally{await close(tree);}
});

test('a deferred activity cannot be submitted until the plan explicitly resumes it',async()=>{
 globalThis.fetch=async()=>Response.json({...path,activities:[{...activity,status:'DEFERRED'}]});const tree=await mount();try{await act(async()=>tree.root.findByProps({'aria-label':'练习答案'}).props.onChange({target:{value:'An old browser draft'}}));assert.equal(button(tree,'提交练习').props.disabled,true);assert.match(text(tree),/已延后|暂不继续/);assert.match(text(tree),/调整当前计划/);}finally{await close(tree);}
});


test('an exam-created goal with no activities still offers explicit practice preparation',async()=>{
 const calls=[];globalThis.fetch=async(url,o={})=>{const p=new URL(url).pathname;calls.push([p,o.method||'GET']);return Response.json(p.endsWith('/prepare')?path:{...path,activities:[]});};
 const tree=await mount();try{assert.ok(button(tree,'准备练习'));assert.equal(calls.some(([p])=>p.endsWith('/prepare')),false);await act(async()=>{await button(tree,'准备练习').props.onClick();await flush();});assert.match(text(tree),/Explain why repeating/);}finally{await close(tree);}
});

test('a persisted submission acknowledgement freezes editing while the first status refresh is slow',async()=>{
 let reads=0,resolveRefresh;globalThis.fetch=async(url)=>{if(new URL(url).pathname.endsWith('/submissions'))return Response.json({submission_id:721,status:'QUEUED'},{status:202});if(++reads===1)return Response.json(path);return new Promise(resolve=>resolveRefresh=resolve);};
 const tree=await mount();try{await act(async()=>tree.root.findByProps({'aria-label':'练习答案'}).props.onChange({target:{value:'My final first revision'}}));await act(async()=>{await button(tree,'提交练习').props.onClick();await flush();});assert.equal(tree.root.findByProps({'aria-label':'练习答案'}).props.disabled,true);assert.equal(button(tree,'提交练习').props.disabled,true);}finally{await close(tree);resolveRefresh?.(Response.json(path));}
});

test('unsubmitted practice text and its AI declaration survive reopening the task',async()=>{
 globalThis.fetch=async()=>Response.json(path);let tree=await mount();await act(async()=>tree.root.findByProps({'aria-label':'练习答案'}).props.onChange({target:{value:'An unfinished explanation'}}));await act(async()=>tree.root.findByProps({'aria-label':'本次使用了 AI 辅助'}).props.onChange({target:{checked:true}}));await close(tree);tree=await mount();try{assert.equal(tree.root.findByProps({'aria-label':'练习答案'}).props.value,'An unfinished explanation');assert.equal(tree.root.findByProps({'aria-label':'本次使用了 AI 辅助'}).props.checked,true);}finally{await close(tree);}
});
