import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {act,create} from 'react-test-renderer';
import {MemoryRouter,Routes,Route} from 'react-router-dom';
import {AppStateProvider} from '../src/components/AppState.tsx';
import Assets from '../src/pages/Assets.tsx';
import ProjectReview from '../src/pages/ProjectReview.tsx';
import Memory from '../src/pages/Memory.tsx';
import Workshop from '../src/pages/QuestionWorkshop.tsx';
import {AutopilotSettingsForm,ProjectActions} from '../src/components/ProjectAutopilot.tsx';
import ProjectMemoryCandidates from '../src/components/ProjectMemoryCandidates.tsx';
import ProjectAnalysisPanel from '../src/components/ProjectAnalysisPanel.tsx';

const flush=()=>new Promise(resolve=>setImmediate(resolve));
const text=tree=>JSON.stringify(tree.toJSON());
const button=(tree,label)=>tree.root.findAllByType('button').find(n=>n.children.join('').includes(label));
async function mount(Page,path='/',pattern='*') {let tree;await act(async()=>{tree=create(React.createElement(MemoryRouter,{initialEntries:[path],future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(AppStateProvider,null,React.createElement(Routes,null,React.createElement(Route,{path:pattern,element:React.createElement(Page)})))));await flush();});return tree;}
async function close(tree){await act(async()=>tree.unmount());}
const settings={enabled:true,consent_version:'1',consented_at:'2026-11-17T00:00:00Z',source_notice:'源码片段会发送给已配置的模型服务。',provider_display_name:'Configured model',budget:{max_model_calls:32,max_source_bytes:8388608},check_interval_hours:6};
const queued={id:21,project_id:7,status:'QUEUED',stage:'fetch_repo',coverage_status:'PARTIAL',completed_modules:0,total_modules:3,error:null,allowed_actions:['pause','cancel']};
const project={id:7,name:'Real repository',owner:'example',repo:'repo',repo_url:'https://github.com/example/repo',kind:'github',autopilot:{user_status:'已导入，等待分析',enabled:true,consent_required:false,worker:{online:false,last_seen_at:null},latest_run:queued,latest_valid_report:null,pending_candidate_count:0,allowed_actions:['check_updates','reanalyze','pause','remove']}};
const fullReport={id:11,run_id:20,as_of:'2026-11-16T00:00:00Z',source_revision:'abc123',coverage_status:'COMPLETE',is_latest_valid:true,freshness:'CURRENT',report:{summary:'Earlier valid report',architecture:[],issues:[],unknowns:[]},source_excerpts:[],active_issue_ids:[],issue_memories:[],generations:[]};
const candidate={id:91,revision:2,status:'PENDING_REVIEW',report_id:11,project_id:7,base_memory_id:null,base_memory_revision:null,kind:'architecture',fact_type:'CODE_OBSERVED',content:'Candidate knowledge from code',citations:[{path:'app.py',start_line:1,end_line:3}],source_excerpts:[],change_reason:'新报告',suggested_use:'项目面试背景'};
function fetcher({projects=[project],run=queued,report=null,candidates=[],memories=[],onRequest,config=settings}={}) {return async(url,options={})=>{const p=new URL(url).pathname;const result=await onRequest?.(p,options);if(result)return result;if(p.endsWith('/project-autopilot/settings'))return Response.json(config);if(p.endsWith('/projects'))return Response.json({items:projects});if(p.endsWith('/findings'))return Response.json({items:[],stats:{}});if(p.endsWith('/analyses'))return Response.json({items:[]});if(p.endsWith('/report'))return Response.json({project_id:7,latest_run:run,report,partial_preview:run?.coverage_status==='PARTIAL'?{summary:'Saved module preview'}:null,worker:{online:false,last_seen_at:null},allowed_actions:['check_updates','reanalyze']});if(p.endsWith('/memory-candidates'))return Response.json({items:candidates});if(p.endsWith('/memory'))return Response.json({items:memories});return Response.json({items:[]});};}

test('repository import needs only URL and queues analysis without a browser scan call',async()=>{
 const requests=[];globalThis.fetch=fetcher({projects:[],onRequest:(p,o)=>{if(o.method==='POST'){requests.push([p,JSON.parse(o.body||'{}')]);return Response.json({...project,run_id:21,run_status:'QUEUED'});}}});
 const tree=await mount(Assets);try{
  await act(async()=>{button(tree,'导入仓库').props.onClick();await flush();});
  const url=tree.root.findAllByType('input').find(n=>String(n.props.placeholder).includes('github.com'));
  await act(async()=>url.props.onChange({target:{value:'https://github.com/example/repo'}}));
  await act(async()=>{await (button(tree,'导入项目')||button(tree,'导入并扫描')).props.onClick();await flush();});
  assert.equal(requests.length,1,'import must enqueue once without an extra scan');
  assert.equal(requests[0][0],'/api/v1/projects');assert.equal(requests[0][1].repo_url,'https://github.com/example/repo');assert.ok(!requests[0][1].branch,'remote default branch must not be forced to master');
 }finally{await close(tree);}
});

test('project cards show persistent job and offline worker without legacy scan completion',async()=>{
 globalThis.fetch=fetcher();const tree=await mount(Assets);try{assert.match(text(tree),/已导入，等待分析/);assert.match(text(tree),/尚未启动本地后台服务/);assert.ok(button(tree,'查看项目报告'));assert.doesNotMatch(text(tree),/已扫描|路径 .*内容关键词|分析关注点/);}finally{await close(tree);}
});

test('new partial failed task stays separate from the previous valid report',async()=>{
 globalThis.fetch=fetcher({run:{...queued,status:'BLOCKED',error:{code:'provider_timeout',message:'Provider timed out',recoverable:true},allowed_actions:['resume']},report:fullReport});
 const tree=await mount(ProjectReview,'/assets/project/7','/assets/project/:id');try{assert.match(text(tree),/Provider timed out/);assert.match(text(tree),/旧报告|历史成功报告/);assert.match(text(tree),/Earlier valid report/);assert.match(text(tree),/部分分析|部分结果/);assert.doesNotMatch(text(tree),/项目认知已保存到记忆中心|本次关注的问题/);}finally{await close(tree);}
});

test('candidate review sends selected version and edited content; conflict retains the edit',async()=>{
 let payload;globalThis.fetch=fetcher({candidates:[candidate],onRequest:(p,o)=>{if(p.endsWith('/memory-candidates/review')){payload=JSON.parse(o.body);return Response.json({results:[{id:91,status:'CONFLICT',error:'候选已更新，请刷新后核对',revision:3}]});}}});
 const tree=await mount(Memory);try{
  assert.match(text(tree),/Candidate knowledge from code/);
  await act(async()=>button(tree,'编辑后确认').props.onClick());
  await act(async()=>tree.root.findByProps({'aria-label':'候选记忆内容'}).props.onChange({target:{value:'My explicit correction'}}));
  await act(async()=>{await button(tree,'确认这条记忆').props.onClick();await flush();});
  assert.deepEqual(payload.items,[{candidate_id:91,expected_revision:2,action:'confirm',edited_content:'My explicit correction'}]);
  assert.match(text(tree),/候选已更新/);assert.equal(tree.root.findByProps({'aria-label':'候选记忆内容'}).props.value,'My explicit correction');
 }finally{await close(tree);}
});

test('confirmed but stale project memory is not labelled current or usable for new questions',async()=>{
 globalThis.fetch=fetcher({memories:[{id:51,mtype:'ProjectIssue',content:'Old code observation',source:'USER',status:'ACTIVE',freshness:'NEEDS_REVIEW',eligible_for_personalization:false,ineligible_reason:'引用的代码已变更',created_at:'2026-11-16T00:00:00Z',meta:{project_id:7}}]});
 const tree=await mount(Memory);try{assert.match(text(tree),/需重新确认|需要重新确认/);assert.match(text(tree),/不用于新任务|不参与新任务/);assert.doesNotMatch(text(tree),/当前有效/);}finally{await close(tree);}
});

test('first enable requires source acknowledgement and preserves explicit budget when save fails',async()=>{
 let saved;globalThis.fetch=fetcher({config:{...settings,enabled:false,consented_at:null},onRequest:(p,o)=>{if(o.method==='PUT'){saved=JSON.parse(o.body);return Response.json({detail:'cannot save consent'},{status:503});}}});
 const tree=await mount(AutopilotSettingsForm);try{
  assert.match(text(tree),/源码片段会发送/);assert.match(text(tree),/Configured model/);
  assert.equal(button(tree,'确认并启用自动分析').props.disabled,true);
  await act(async()=>tree.root.findByProps({'aria-label':'确认源码发送与自动分析预算'}).props.onChange({target:{checked:true}}));
  await act(async()=>tree.root.findByProps({'aria-label':'自动分析模型调用预算'}).props.onChange({target:{value:'12'}}));
  await act(async()=>{await button(tree,'确认并启用自动分析').props.onClick();await flush();});
  assert.equal(saved.acknowledge_source_transfer,true);assert.equal(saved.budget.max_model_calls,12);
  assert.match(text(tree),/cannot save consent/);assert.ok(button(tree,'确认并启用自动分析'));assert.equal(tree.root.findByProps({'aria-label':'自动分析模型调用预算'}).props.value,12);
 }finally{await close(tree);}
});

test('candidate bulk confirmation freezes selected IDs and revisions across refreshed arrivals',async()=>{
 let payload,reads=0;globalThis.fetch=fetcher({onRequest:(p,o)=>{
  if(p.endsWith('/memory-candidates/review')){payload=JSON.parse(o.body);return Response.json({results:[{id:91,status:'CONFLICT',error:'候选版本已变化'}]});}
  if(p.endsWith('/memory-candidates'))return Response.json({items:++reads===1?[candidate]:[{...candidate,revision:3},{...candidate,id:92}]});
 }});
 const tree=await mount(Memory);try{
  await act(async()=>button(tree,'选择当前列表').props.onClick());
  await act(async()=>{await button(tree,'刷新候选').props.onClick();await flush();});
  await act(async()=>{await button(tree,'确认所选记忆').props.onClick();await flush();});
  assert.deepEqual(payload.items,[{candidate_id:91,expected_revision:2,action:'confirm'}]);assert.match(text(tree),/候选版本已变化/);
 }finally{await close(tree);}
});

test('new project questions fail closed if source has no confirmed current issue eligibility',async()=>{
 globalThis.fetch=fetcher({onRequest:(p)=>p.endsWith('/analyses/11')?Response.json({id:11,project_id:7,status:'SUCCESS',report:{summary:'Risk',architecture:[],unknowns:[],issues:[{id:'I1',title:'Code risk',skill_key:'reliability',citations:[]}]}}):undefined});
 const tree=await mount(Workshop,'/bank/workshop?project=7&analysis=11&issues=I1');try{assert.equal(button(tree,'创建任务并检查输入').props.disabled,true);assert.match(text(tree),/尚未确认|重新确认/);}finally{await close(tree);}
});

test('full rerun retry reuses idempotency key and does not start a browser model request',async()=>{
 const bodies=[];globalThis.fetch=async(url,o)=>{bodies.push({path:new URL(url).pathname,...JSON.parse(o.body)});return Response.json({detail:'queue response lost'},{status:503});};
 function Actions(){return React.createElement(ProjectActions,{projectId:7,run:null,allowedActions:['reanalyze'],onChanged:()=>{}});}
 const tree=await mount(Actions);try{
  await act(async()=>{await button(tree,'完整重新分析').props.onClick();await flush();});
  await act(async()=>{await button(tree,'完整重新分析').props.onClick();await flush();});
  assert.equal(bodies.length,2);assert.equal(bodies[0].path,'/api/v1/projects/7/reanalyze');assert.equal(bodies[0].mode,'full');assert.ok(bodies[0].request_key);assert.equal(bodies[0].request_key,bodies[1].request_key);
  assert.match(text(tree),/queue response lost/);
 }finally{await close(tree);}
});

test('a newly published report does not discard an in-progress candidate correction',async()=>{
 let publish;function Candidates(){const [revision,setRevision]=React.useState(11);publish=setRevision;return React.createElement(ProjectMemoryCandidates,{projectId:7,refreshKey:revision});}
 globalThis.fetch=fetcher({candidates:[candidate]});const tree=await mount(Candidates);try{
  await act(async()=>button(tree,'编辑后确认').props.onClick());
  await act(async()=>tree.root.findByProps({'aria-label':'候选记忆内容'}).props.onChange({target:{value:'User correction retained while source changes'}}));
  await act(async()=>{publish(12);await flush();});
  assert.equal(tree.root.findAllByProps({'aria-label':'候选记忆内容'}).length,1,'publishing must not silently close the edit');
  assert.equal(tree.root.findByProps({'aria-label':'候选记忆内容'}).props.value,'User correction retained while source changes');
 }finally{await close(tree);}
});

test('project details do not send a new user to a removed manual scan entry point',async()=>{
 globalThis.fetch=fetcher();const tree=await mount(ProjectReview,'/assets/project/7','/assets/project/:id');try{assert.doesNotMatch(text(tree),/先在.*执行一次扫描/);}finally{await close(tree);}
});

test('import can recover when the first settings read failed but opening its form reloads consent',async()=>{
 let settingsReads=0;globalThis.fetch=fetcher({onRequest:p=>p.endsWith('/project-autopilot/settings')?++settingsReads===1?Response.json({detail:'temporary read failure'},{status:503}):Response.json(settings):undefined});
 const tree=await mount(Assets);try{
  await act(async()=>{button(tree,'导入仓库').props.onClick();await flush();});
  await act(async()=>tree.root.findByProps({'aria-label':'仓库地址'}).props.onChange({target:{value:'https://github.com/example/repo'}}));
  assert.equal(button(tree,'导入项目').props.disabled,false,'persisted consent reload should unlock the form without saving consent twice');
 }finally{await close(tree);}
});

test('report chapters expose the actual referenced claim and frozen source excerpt on demand',async()=>{
 const citation={path:'app.py',start_line:1,end_line:3};
 globalThis.fetch=fetcher({run:{...queued,id:11,status:'COMPLETED',coverage_status:'COMPLETE'},report:{...fullReport,source_excerpts:[{...citation,excerpt:'1: chapter_evidence()',excerpt_truncated:false}],report:{...fullReport.report,sections:[{key:'purpose',title:'项目用途',body:'Useful purpose',claim_ids:['C1']}],claims:[{id:'C1',title:'Actual claim',text:'Verified referenced observation',kind:'CODE_OBSERVED',citations:[citation]}]}}});
 const tree=await mount(ProjectReview,'/assets/project/7','/assets/project/:id');try{assert.match(text(tree),/Verified referenced observation/);assert.match(text(tree),/chapter_evidence/);}finally{await close(tree);}
});

test('unknown remote freshness is explained as unknown rather than invented code changes',async()=>{
 globalThis.fetch=fetcher({report:{...fullReport,freshness:'UNKNOWN'}});
 const tree=await mount(ProjectReview,'/assets/project/7','/assets/project/:id');try{assert.match(text(tree),/新鲜度尚未确认/);assert.doesNotMatch(text(tree),/报告对应的代码已有变化/);}finally{await close(tree);}
});

test('removed project links still open immutable reports without project mutations or candidate review',async()=>{
 const original=fetcher({projects:[],report:fullReport});
 globalThis.fetch=async(url,o)=>new URL(url).pathname.endsWith('/report')?Response.json({project_id:7,project:{...project,archived_at:'2026-11-17T00:00:00Z'},latest_run:{...queued,status:'CANCELLED'},report:fullReport,worker:{online:true},allowed_actions:[]}):original(url,o);
 const tree=await mount(ProjectReview,'/assets/project/7','/assets/project/:id');try{
  assert.match(text(tree),/Earlier valid report/);assert.match(text(tree),/已移除.*只读/);
  assert.equal(button(tree,'完整重新分析'),undefined);assert.equal(button(tree,'恢复分析'),undefined);assert.equal(button(tree,'确认所选记忆'),undefined);
  assert.equal(button(tree,'编辑经历与准备面试'),undefined);
 }finally{await close(tree);}
});

test('valid complete project report can supply confirmed issues while candidate job is still running',async()=>{
 globalThis.fetch=fetcher({onRequest:p=>p.endsWith('/analyses/11')?Response.json({id:11,project_id:7,status:'RUNNING',report_status:'VALID',coverage_status:'COMPLETE',is_latest_success:true,active_issue_ids:['I1'],report:{summary:'Risk',architecture:[],unknowns:[],issues:[{id:'I1',title:'Confirmed issue',skill_key:'reliability',citations:[]}]}}):undefined});
 const tree=await mount(Workshop,'/bank/workshop?project=7&analysis=11&issues=I1');try{assert.equal(button(tree,'创建任务并检查输入').props.disabled,false);assert.match(text(tree),/Confirmed issue/);}finally{await close(tree);}
});

test('stale candidates can be ignored but cannot be confirmed individually or in bulk',async()=>{
 globalThis.fetch=fetcher({candidates:[{...candidate,freshness:'NEEDS_REVIEW'}]});
 const tree=await mount(Memory);try{
  assert.match(text(tree),/来源已变化|需要重新核对/);
  assert.equal(button(tree,'确认记忆').props.disabled,true);assert.equal(button(tree,'编辑后确认').props.disabled,true);
  await act(async()=>button(tree,'选择当前列表').props.onClick());
  assert.equal(button(tree,'确认所选记忆').props.disabled,true);assert.equal(button(tree,'忽略所选记忆').props.disabled,false);
 }finally{await close(tree);}
});

test('same report refreshes later candidates when the durable task completes and stops polling',async t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval']});
 let completed=false,reportReads=0,candidateReads=0;
 globalThis.fetch=fetcher({onRequest:p=>{
  if(p.endsWith('/report')){reportReads++;return Response.json({project_id:7,latest_run:{...queued,id:11,status:completed?'COMPLETED':'RUNNING',coverage_status:'COMPLETE'},report:fullReport,worker:{online:true},allowed_actions:[]});}
  if(p.endsWith('/memory-candidates')){candidateReads++;return Response.json({items:completed?[candidate]:[]});}
 }});
 function Panel(){return React.createElement(ProjectAnalysisPanel,{projectId:7});}
 const tree=await mount(Panel);try{
  assert.doesNotMatch(text(tree),/Candidate knowledge from code/);
  completed=true;await act(async()=>{t.mock.timers.tick(5000);await flush();});
  assert.match(text(tree),/Candidate knowledge from code/,'candidate publication after report publication must become visible');
  const settledReads=reportReads;
  await act(async()=>{t.mock.timers.tick(60000);await flush();});
  assert.equal(reportReads,settledReads,'completed tasks must not be polled indefinitely');
  assert.ok(candidateReads<=3,'a report poll must not create repeated candidate GET loops');
 }finally{await close(tree);t.mock.timers.reset();}
});

test('automatic report refresh is bounded and never overlaps an unfinished GET',async t=>{
 t.mock.timers.enable({apis:['setTimeout','setInterval']});
 let reads=0,release;globalThis.fetch=fetcher({onRequest:p=>{
  if(p.endsWith('/report')){reads++;if(reads===2)return new Promise(resolve=>{release=()=>resolve(Response.json({project_id:7,latest_run:{...queued,status:'RUNNING'},report:null,worker:{online:true},allowed_actions:[]}));});
   return Response.json({project_id:7,latest_run:{...queued,status:'RUNNING'},report:null,worker:{online:true},allowed_actions:[]});}
 }});
 function Panel(){return React.createElement(ProjectAnalysisPanel,{projectId:7});}
 const tree=await mount(Panel);try{
  await act(async()=>{t.mock.timers.tick(5000);await flush();});
  await act(async()=>{t.mock.timers.tick(30000);await flush();});
  assert.equal(reads,2,'slow GET must not accumulate concurrent report polls');
  await act(async()=>{release();await flush();});
  for(let i=0;i<160;i++)await act(async()=>{t.mock.timers.tick(10000);await flush();});
  const boundedReads=reads;
  for(let i=0;i<10;i++)await act(async()=>{t.mock.timers.tick(10000);await flush();});
  assert.equal(reads,boundedReads,'polling must stop at its finite budget');
  assert.match(text(tree),/自动刷新已暂停/);
  await act(async()=>{await button(tree,'刷新状态').props.onClick();await flush();});
  assert.equal(reads,boundedReads+1,'manual refresh remains available after the polling budget');
 }finally{if(release)release();await close(tree);t.mock.timers.reset();}
});

test('valid report survives candidate budget pause or failure with an explicit incomplete-memory label',async()=>{
 for(const status of ['PAUSED_BUDGET','FAILED_FINAL']){
  globalThis.fetch=fetcher({run:{...queued,id:11,status,stage:'extract_candidates',coverage_status:'COMPLETE',error:{code:'BUDGET_EXHAUSTED',message:'Candidate stage incomplete',recoverable:status==='PAUSED_BUDGET'}},report:fullReport});
  function Panel(){return React.createElement(ProjectAnalysisPanel,{projectId:7});}
  const tree=await mount(Panel);try{assert.match(text(tree),/报告已完成，候选记忆尚未完成/);assert.match(text(tree),/Earlier valid report/);assert.match(text(tree),/Candidate stage incomplete/);}finally{await close(tree);}
 }
});

test('history opens a valid complete report despite candidate failure and blocks partial completed runs',async()=>{
 let historyOpened=false;const fallback=fetcher({report:fullReport});
 globalThis.fetch=async(url,o)=>{
  const u=new URL(url);
  if(u.pathname.endsWith('/analyses'))return Response.json({items:[{id:10,status:'PAUSED_BUDGET',report_status:'VALID',coverage_status:'COMPLETE'},{id:9,status:'COMPLETED',report_status:'PARTIAL',coverage_status:'PARTIAL'}]});
  if(u.searchParams.get('analysis_id')==='10'){historyOpened=true;return Response.json({project_id:7,latest_run:queued,report:{...fullReport,id:10,is_latest_valid:false,report:{...fullReport.report,summary:'Complete report before candidate budget pause'}},worker:{online:true},allowed_actions:[]});}
  return fallback(url,o);
 };
 function Panel(){return React.createElement(ProjectAnalysisPanel,{projectId:7});}
 const tree=await mount(Panel);try{
  await act(async()=>{await button(tree,'分析历史').props.onClick();await flush();});
  assert.equal(button(tree,'#10').props.disabled,false,'report validity is independent of candidate task completion');
  assert.equal(button(tree,'#9').props.disabled,true,'PARTIAL must not masquerade as complete history');
  await act(async()=>{await button(tree,'#10').props.onClick();await flush();});
  assert.equal(historyOpened,true);assert.match(text(tree),/Complete report before candidate budget pause/);
 }finally{await close(tree);}
});
