import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {act, create} from 'react-test-renderer';
import {MemoryRouter, Route, Routes, useLocation, useNavigate} from 'react-router-dom';
import {AppStateProvider} from '../src/components/AppState.tsx';
import ProjectReview from '../src/pages/ProjectReview.tsx';
import Assets from '../src/pages/Assets.tsx';
import Memory from '../src/pages/Memory.tsx';
import Workshop from '../src/pages/QuestionWorkshop.tsx';
import {beginProjectAssessment} from '../src/lib/questionWorkshop.ts';
import {SourceCitations} from '../src/components/ProjectAnalysisPanel.tsx';

const flush=()=>new Promise(resolve=>setImmediate(resolve));
const text=tree=>JSON.stringify(tree.toJSON());
const button=(tree,label)=>tree.root.findAllByType('button').find(n=>n.children.join('').includes(label));
const project={id:7,name:'Source project',owner:'example',repo:'source',kind:'github',status:'SUCCESS',profile:{role:'开发',responsibilities:'负责检索'},findings:{matrix:[{key:'backend',level:5,findings:9}],stats:{files:9}}};
const report={schema_version:1,summary:'Repository understanding',architecture:[{title:'Request flow',detail:'Calls the query layer',citations:[{path:'app/query.py',start_line:10,end_line:15}]}],issues:[{id:'I1',title:'Timeout boundary',skill_key:'reliability',severity:'high',detail:'An external wait has no deadline',impact:'Requests can wait indefinitely',recommendation:'Set and test a deadline',status:'observed',citations:[{path:'app/query.py',start_line:12,end_line:14}],interview_questions:['How would you bound the wait?']}],unknowns:['Production traffic not observed']};
const successful={id:11,project_id:7,status:'SUCCESS',source_revision:'abc1234567890',report,coverage:{files_read:3,eligible_files:20,unread_files:['app/other.py'],truncated_files:[],tree_truncated:false},memory_ids:[51],active_issue_ids:['I1'],created_at:'2026-11-16T00:00:00Z'};
let route;
let navigateRoute;
function Observe(){route=useLocation();navigateRoute=useNavigate();return null;}
async function mount(Page,path='/',pattern='*'){
 let tree;
 await act(async()=>{tree=create(React.createElement(MemoryRouter,{initialEntries:[path],future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(AppStateProvider,null,React.createElement(Observe),React.createElement(Routes,null,React.createElement(Route,{path:pattern,element:React.createElement(Page)}),pattern!=='*'&&React.createElement(Route,{path:'*',element:null})))));await flush();});
 return tree;
}
async function cleanup(tree){await act(async()=>tree.unmount());}
function reportEnvelope(success,latest=success) {
 const run=latest?{id:latest.id,project_id:7,status:({SUCCESS:'COMPLETED',FAILED:'FAILED_FINAL',READY:'QUEUED'})[latest.status]||latest.status,stage:latest.result_meta?.stage,coverage_status:latest.status==='SUCCESS'?'COMPLETE':'PARTIAL',completed_modules:0,total_modules:0,error:latest.error?{code:'fixture',message:latest.error,recoverable:true}:null,allowed_actions:[]}:null;
 return {project_id:7,latest_run:run,report:success?{...success,run_id:success.id,as_of:success.created_at,coverage_status:'COMPLETE',is_latest_valid:true,freshness:'CURRENT'}:null,partial_preview:null,worker:{online:true},allowed_actions:['check_updates','reanalyze']};
}
function dossier(projectId, summary) {
 return {project_id:projectId,report:summary?{id:11,summary,source_revision:successful.source_revision,freshness:'CURRENT'}:null,technologies:[],bank_questions:[],suggested_questions:[],recommendations:[],limitations:[],source_excerpts:[]};
}
function repositoryFetch(runs){return async url=>{
 const u=new URL(url),p=u.pathname;
 if(p.endsWith('/report'))return Response.json(reportEnvelope(runs.find(r=>u.searchParams.has('analysis_id')?r.id===Number(u.searchParams.get('analysis_id')):r.status==='SUCCESS'),runs[0]));
 if(p.endsWith('/analyses'))return Response.json({items:runs});
 if(p.includes('/analyses/'))return Response.json(runs.find(r=>p.endsWith('/'+r.id)));
 if(p.endsWith('/dossier'))return Response.json(dossier(7,runs.find(r=>r.status==='SUCCESS')?.report.summary));
 if(p.endsWith('/memory-candidates'))return Response.json({items:[]});
 if(p.endsWith('/project-autopilot/settings'))return Response.json({enabled:true,budget:{max_model_calls:32,max_source_bytes:8388608},check_interval_hours:6});
 return Response.json({items:[project]});
};}

test('project review separates latest analysis failure from explicitly labelled historical success',async()=>{
 globalThis.fetch=repositoryFetch([{id:12,project_id:7,status:'FAILED',error:'model unavailable'},successful]);
 const tree=await mount(ProjectReview,'/assets/project/7?tab=process','/assets/project/:id');
 try{
  assert.match(text(tree),/model unavailable/);
  assert.match(text(tree),/历史成功报告/);
  assert.match(text(tree),/Repository understanding/);
  assert.doesNotMatch(text(tree),/能力矩阵|确定性计分/);
  assert.equal(tree.root.findAll(n=>n.props.className==='dim-fill').length,0);
 }finally{await cleanup(tree);}
});

test('project issue selection carries exact analysis and issue to question workshop',async()=>{
 globalThis.fetch=repositoryFetch([successful]);
 const tree=await mount(ProjectReview,'/assets/project/7?tab=improvements','/assets/project/:id');
 try{
  assert.match(text(tree),/app\/query.py/);
  assert.match(text(tree),/项目问题不代表个人能力短板/);
  const select=tree.root.findByProps({'aria-label':'选择项目问题 I1'});
  await act(async()=>select.props.onChange({target:{checked:true}}));
  await act(async()=>button(tree,'根据所选问题准备考核').props.onClick());
  assert.equal(route.pathname,'/bank/workshop');
  const query=new URLSearchParams(route.search);
  assert.equal(query.get('project'),'7');assert.equal(query.get('analysis'),'11');assert.equal(query.get('issues'),'I1');
 }finally{await cleanup(tree);}
});

test('workshop freezes selected project issue provenance into generation request',async()=>{
 let submitted;
 globalThis.fetch=async(url,options)=>{
  const p=new URL(url).pathname;
  if(p.endsWith('/analyses/11'))return Response.json(successful);
  if(p.endsWith('/projects'))return Response.json({items:[project]});
  if(options.method==='POST'){submitted=JSON.parse(options.body);return Response.json({id:22,status:'READY',kind:'supplement',mode:'api',input_snapshot:{project_context:[],count:3},prompt_text:''});}
  if(p.endsWith('/22'))return Response.json({id:22,status:'READY',kind:'supplement',mode:'api',input_snapshot:{project_context:[],count:3},prompt_text:''});
  return Response.json({items:[]});
 };
 const tree=await mount(Workshop,'/bank/workshop?project=7&analysis=11&issues=I1');
 try{
  assert.match(text(tree),/Timeout boundary/);
  await act(async()=>{await button(tree,'创建任务并检查输入').props.onClick();await flush();});
  assert.equal(submitted.source_analysis_id,11);
  assert.deepEqual(submitted.source_issue_ids,['I1']);
  assert.deepEqual(submitted.project_ids,[7]);
  assert.ok(submitted.skill_keys.includes('reliability'));
 }finally{await cleanup(tree);}
});

test('unavailable source analysis prevents silently creating unrelated workshop questions',async()=>{
 globalThis.fetch=async url=>new URL(url).pathname.includes('/analyses/')?Response.json({detail:'analysis not found'},{status:404}):Response.json({items:[project]});
 const tree=await mount(Workshop,'/bank/workshop?project=7&analysis=11&issues=I1');
 try{assert.match(text(tree),/analysis not found/);assert.equal(button(tree,'创建任务并检查输入').props.disabled,true);}
 finally{await cleanup(tree);}
});

test('memory load failure shows no demo memories or invented candidates',async()=>{
 globalThis.fetch=async()=>Response.json({detail:'storage unavailable'},{status:503});
 const tree=await mount(Memory);
 try{assert.match(text(tree),/storage unavailable/);assert.doesNotMatch(text(tree),/静态演示|Squabble|DEMO|三个月内拿到/);}
 finally{await cleanup(tree);}
});

test('memory correction failure retains edited text and never reports local success',async()=>{
 globalThis.fetch=async(url,options)=>options.method==='POST'?Response.json({detail:'cannot save correction'},{status:503}):Response.json({items:[{id:51,mtype:'ProjectIssue',content:'Original project issue',source:'SYSTEM',status:'ACTIVE',created_at:'2026-11-16T00:00:00Z',meta:{project_id:7,analysis_id:11,source_revision:'abc123',citations:report.issues[0].citations}}]});
 const tree=await mount(Memory);
 try{
  await act(async()=>button(tree,'纠正').props.onClick());
  await act(async()=>tree.root.findByProps({'aria-label':'纠正记忆内容'}).props.onChange({target:{value:'My retained correction'}}));
  await act(async()=>{await button(tree,'保存纠正').props.onClick();await flush();});
  assert.match(text(tree),/cannot save correction/);
  assert.equal(tree.root.findByProps({'aria-label':'纠正记忆内容'}).props.value,'My retained correction');
  assert.doesNotMatch(text(tree),/纠正已记录|已纠正为新的记忆/);
 }finally{await cleanup(tree);}
});

test('assets keep repository cards focused on project reports without ability scores',async()=>{
 globalThis.fetch=repositoryFetch([]);
 const tree=await mount(Assets);
 try{assert.doesNotMatch(text(tree),/能力矩阵/);assert.equal(tree.root.findAll(n=>n.props.className==='dim-fill').length,0);assert.ok(button(tree,'查看项目档案'));assert.equal(tree.root.findByProps({href:'/assets/project/7'}).children.join(''),project.name);}
 finally{await cleanup(tree);}
});

test('project batch assessment includes only reviewed IDs and deduplicates merged questions',async()=>{
 let submitted;
 const task={id:22,status:'COMPLETE',kind:'supplement',mode:'api',input_snapshot:{project_context:[],count:5,source_analysis_id:11,source_issue_ids:['I1']},prompt_text:'',review:{id:31,short_code:'IM-G22',status:'APPROVED',items:[
  {id:1,title:'Reviewed',status:'APPROVED',question_id:37},
  {id:2,title:'Merged',status:'MERGED',merged_into:39},
  {id:3,title:'Repeated',status:'MERGED',merged_into:37},
  {id:4,title:'Draft',status:'DRAFT',question_id:45},
  {id:5,title:'Rejected',status:'REJECTED',question_id:46},
 ]}};
 globalThis.fetch=async(url,options)=>{
  const p=new URL(url).pathname;
  if(p.endsWith('/exams')){submitted=JSON.parse(options.body);return Response.json({id:72});}
  if(p.endsWith('/22'))return Response.json(task);
  return Response.json({items:[]});
 };
 const tree=await mount(Workshop,'/bank/workshop?task=22');
 try{
  await act(async()=>{await button(tree,'开始本批次考核').props.onClick();await flush();});
  assert.deepEqual(submitted.question_ids,[37,39]);assert.equal(submitted.question_count,2);
  assert.equal(submitted.kind,'bank_exam');assert.equal(submitted.project_generation_id,22);assert.ok(submitted.idempotency_key);
  assert.equal(route.pathname+route.search,'/exam/diagnosis?id=72');
 }finally{await cleanup(tree);}
});

test('unreviewed project batch cannot create an assessment',async()=>{
 let called=false;
 await assert.rejects(()=>beginProjectAssessment([{id:1,status:'DRAFT',question_id:37}],'key',{post:async()=>{called=true;}}),/审核入库/);
 assert.equal(called,false);
});

test('project analysis observes the already queued task without a manual start request',async()=>{
 const get=repositoryFetch([{id:14,project_id:7,status:'READY'}]);let posts=0;
 globalThis.fetch=async(url,options)=>{if(options?.method==='POST')posts++;return get(url,options);};
 const tree=await mount(ProjectReview,'/assets/project/7?tab=process','/assets/project/:id');
 try{assert.match(text(tree),/已导入，等待分析/);assert.equal(posts,0);assert.equal(tree.root.findAllByProps({'aria-label':'项目分析关注点'}).length,0);}
 finally{await cleanup(tree);}
});

test('project report loads linked generation and scored assessment from detail even when list has report',async()=>{
 const get=repositoryFetch([successful]);
 globalThis.fetch=async(url,options)=>new URL(url).pathname.endsWith('/report')?Response.json(reportEnvelope({...successful,generations:[{id:22,status:'COMPLETE',issue_ids:['I1'],question_ids:[37],assessments:[{id:72,status:'COMPLETED',score:60}]}]})):get(url,options);
 const tree=await mount(ProjectReview,'/assets/project/7?tab=improvements','/assets/project/:id');
 try{assert.ok(tree.root.findAllByType('a').some(a=>a.props.href==='/exam/diagnosis/report?id=72'));}
 finally{await cleanup(tree);}
});

test('dismissed issue is labelled and cannot be selected from an immutable historical report',async()=>{
 const get=repositoryFetch([successful]);
 globalThis.fetch=async(url,options)=>new URL(url).pathname.endsWith('/report')?Response.json(reportEnvelope({...successful,active_issue_ids:[]})):get(url,options);
 const tree=await mount(ProjectReview,'/assets/project/7?tab=improvements','/assets/project/:id');
 try{assert.equal(tree.root.findByProps({'aria-label':'选择项目问题 I1'}).props.disabled,true);assert.match(text(tree),/对应记忆尚未确认/);}
 finally{await cleanup(tree);}
});

test('corrected active issue memory is visible beside its immutable analysis observation',async()=>{
 const get=repositoryFetch([successful]);
 globalThis.fetch=async(url,options)=>new URL(url).pathname.endsWith('/report')?Response.json(reportEnvelope({...successful,active_issue_ids:['I1'],issue_memories:[{issue_id:'I1',memory_id:61,content:'Deadline is implemented by gateway; verify only local cancellation',user_corrected:true,source_revision:'abc123'}]})):get(url,options);
 const tree=await mount(ProjectReview,'/assets/project/7?tab=improvements','/assets/project/:id');
 try{assert.match(text(tree),/当前记忆已纠正/);assert.match(text(tree),/Deadline is implemented by gateway/);assert.match(text(tree),/An external wait has no deadline/);}
 finally{await cleanup(tree);}
});

test('source citations expose frozen excerpts as escaped text and label partial excerpts',()=>{
 const citation={path:'app/render.ts',start_line:10,end_line:90};
 const html=renderToStaticMarkup(React.createElement(SourceCitations,{citations:[citation],sourceExcerpts:[{...citation,excerpt:'10: <script>globalThis.repositoryExecuted = true</script>',excerpt_truncated:true}]}));
 assert.match(html,/查看本次读取的代码片段/);
 assert.match(html,/&lt;script&gt;/);
 assert.doesNotMatch(html,/<script>/);
 assert.match(html,/片段已截断/);
 assert.equal(globalThis.repositoryExecuted,undefined);
});

test('source citations match the frozen source identity rather than a same-path older excerpt',()=>{
 const citation={path:'app.py',start_line:1,end_line:2,source_id:'new-source',source_version:'revision-b'};
 const html=renderToStaticMarkup(React.createElement(SourceCitations,{citations:[citation],sourceExcerpts:[{...citation,source_id:'old-source',source_version:'revision-a',excerpt:'Old code must not replace cited evidence'},{...citation,excerpt:'Correct frozen evidence'}]}));
 assert.match(html,/Correct frozen evidence/);assert.doesNotMatch(html,/Old code must not replace/);
});

test('project report passes frozen excerpts to architecture and issue citations',async()=>{
 const get=repositoryFetch([successful]);
 globalThis.fetch=async(url,options)=>new URL(url).pathname.endsWith('/report')?Response.json(reportEnvelope({...successful,source_excerpts:[{path:'app/query.py',start_line:10,end_line:15,excerpt:'10: architecture_source_fixture',excerpt_truncated:false},{path:'app/query.py',start_line:12,end_line:14,excerpt:'12: issue_source_fixture',excerpt_truncated:false}]})):get(url,options);
 const tree=await mount(ProjectReview,'/assets/project/7?tab=evidence','/assets/project/:id');
 try{
  assert.match(text(tree),/architecture_source_fixture/);
  assert.doesNotMatch(text(tree),/issue_source_fixture/);
  await act(async()=>{navigateRoute('/assets/project/7?tab=improvements');await flush();});
  assert.match(text(tree),/issue_source_fixture/);
 }
 finally{await cleanup(tree);}
});

test('project issue labels use the exact linked claim kind instead of observed status',async()=>{
 const kinds=[['DOC_CLAIM','文档声明'],['INFERRED','待验证推断'],['CODE_OBSERVED','代码观察'],['UNASSESSED','尚未判断']];
 const issues=kinds.map(([kind],i)=>({...report.issues[0],id:`I${i}`,title:`Issue ${kind}`,claim_id:`claim-${i}`,status:'observed'}));
 const claims=kinds.map(([kind],i)=>({id:`claim-${i}`,title:`Claim ${i}`,text:'Frozen observation',kind,citations:[]}));
 globalThis.fetch=repositoryFetch([{...successful,report:{...report,issues,claims}}]);
 const tree=await mount(ProjectReview,'/assets/project/7?tab=improvements','/assets/project/:id');
 try{for(const [i,[,expected]] of kinds.entries()){
  const card=tree.root.findAllByType('article').find(n=>n.findAllByProps({'aria-label':`选择项目问题 I${i}`}).length);
  assert.equal(card.findAllByType('span').find(n=>n.props.className==='chip chip-slate').children.join(''),expected);
 }}finally{await cleanup(tree);}
});

test('unlinked and unknown project issue kinds do not become code observations',async()=>{
 const issues=[{...report.issues[0],id:'unlinked'}, {...report.issues[0],id:'missing',claim_id:'not-in-report'}, {...report.issues[0],id:'unknown',claim_id:'unknown-kind'}];
 globalThis.fetch=repositoryFetch([{...successful,report:{...report,issues,claims:[{id:'unknown-kind',kind:'FUTURE_KIND',title:'Unrecognized',text:'Observation',citations:[]}]}}]);
 const tree=await mount(ProjectReview,'/assets/project/7?tab=improvements','/assets/project/:id');
 try{for(const issue of issues){
  const card=tree.root.findAllByType('article').find(n=>n.findAllByProps({'aria-label':`选择项目问题 ${issue.id}`}).length);
  assert.equal(card.findAllByType('span').find(n=>n.props.className==='chip chip-slate').children.join(''),'来源类型未确认');
 }}finally{await cleanup(tree);}
});

test('architecture cards expose their saved fact type including uncertain legacy records',async()=>{
 const kinds=[['DOC_CLAIM','文档声明'],['INFERRED','待验证推断'],['CODE_OBSERVED','代码观察'],[undefined,'来源类型未确认']];
 const architecture=kinds.map(([fact_type],i)=>({...report.architecture[0],title:`Architecture ${i}`,fact_type}));
 globalThis.fetch=repositoryFetch([{...successful,report:{...report,architecture}}]);
 const tree=await mount(ProjectReview,'/assets/project/7?tab=evidence','/assets/project/:id');
 try{for(const [i,[,expected]] of kinds.entries()){
  const card=tree.root.findAllByType('article').find(n=>n.findAllByType('h4').some(h=>h.children.join('')===`Architecture ${i}`));
  assert.equal(card.findAllByType('span').find(n=>n.props.className==='chip chip-slate')?.children.join(''),expected);
 }}finally{await cleanup(tree);}
});

test('project dossier distinguishes evidenced technologies, bank questions and suggested followups',async()=>{
 const requests=[];
 const get=repositoryFetch([successful]);
 const citation=report.architecture[0].citations[0];
 globalThis.fetch=async url=>{
  const p=new URL(url).pathname;
  requests.push(p);
  if(p.endsWith('/dossier'))return Response.json({...dossier(7,report.summary),
   technologies:[{name:'SQLite',use:'Stores query results',citations:[citation]}],
   bank_questions:[{id:37,short_code:'Q-37',title:'Explain the query plan'}],
   suggested_questions:[{id:'I1:0',title:'How would you bound the wait?',citations:[citation]}],
   recommendations:[{id:'I1',title:'Set a request deadline',detail:'Exercise the slow dependency',citations:[citation]}],
  });
  return get(url);
 };
 const tree=await mount(ProjectReview,'/assets/project/7','/assets/project/:id');
 try{
  assert.equal(requests.filter(p=>p==='/api/v1/projects/7/dossier').length,1);
  assert.match(text(tree),/Repository understanding/);
  assert.match(text(tree),/SQLite/);
  assert.match(text(tree),/app\/query.py/);
  assert.match(text(tree),/已入题库.*Q-37.*Explain the query plan/);
  const suggestions=tree.root.findAllByType('details').find(n=>n.findAllByType('summary').some(x=>x.children.join('').includes('AI 建议追问')));
  assert.ok(suggestions);
  assert.match(JSON.stringify(suggestions.findByType('h3').children),/How would you bound the wait/);
  assert.match(text(tree),/Set a request deadline/);
  for(const href of ['?tab=evidence','?tab=improvements','?tab=resume']) {
   assert.ok(tree.root.findAllByProps({href:'/assets/project/7'+href}).length);
  }
 }finally{await cleanup(tree);}
});

test('legacy AI reports without explicit technology evidence leave the technology list empty',async()=>{
 globalThis.fetch=repositoryFetch([successful]);
 const tree=await mount(ProjectReview,'/assets/project/7','/assets/project/:id');
 try{
  assert.match(text(tree),/这份报告未单列技术实践/);
  assert.doesNotMatch(text(tree),/Request flow|Backend directory/);
 }finally{await cleanup(tree);}
});

test('running analysis explains current model reading stage in Chinese',async()=>{
 globalThis.fetch=repositoryFetch([{id:12,project_id:7,status:'RUNNING',result_meta:{stage:'read_followup'}}]);
 const tree=await mount(ProjectReview,'/assets/project/7?tab=process','/assets/project/:id');
 try{assert.match(text(tree),/补读关联代码/);}
 finally{await cleanup(tree);}
});

test('failed analysis identifies its last stage without presenting historical success as current',async()=>{
 globalThis.fetch=repositoryFetch([{id:12,project_id:7,status:'FAILED',error:'model unavailable',result_meta:{stage:'final_report'}},successful]);
 const tree=await mount(ProjectReview,'/assets/project/7?tab=process','/assets/project/:id');
 try{assert.match(text(tree),/停留阶段/);assert.match(text(tree),/形成架构与问题报告/);assert.match(text(tree),/历史成功报告/);}
 finally{await cleanup(tree);}
});

function issueTask(){return {id:22,status:'REVIEW',kind:'supplement',mode:'api',input_snapshot:{project_context:[{...project,facts:[],has_personal_context:true}],count:3,source_analysis_id:11,source_issue_ids:['I1'],project_issue_targets:[{project_id:7,analysis_id:11,issue_id:'I1'}]},prompt_text:''};}

test('recreating an issue batch after completing profile keeps the exact project problem provenance',async()=>{
 let submitted;
 globalThis.fetch=async(url,options)=>{
  const p=new URL(url).pathname;
  if(p.endsWith('/analyses/11'))return Response.json({...successful,is_latest_success:true,active_issue_ids:['I1']});
  if(p.endsWith('/projects'))return Response.json({items:[project]});
  if(p.endsWith('/22'))return Response.json(issueTask());
  if(options.method==='POST'){submitted=JSON.parse(options.body);return Response.json({...issueTask(),id:23,status:'READY'});}
  if(p.endsWith('/23'))return Response.json({...issueTask(),id:23,status:'READY'});
  return Response.json({items:[]});
 };
 const tree=await mount(Workshop,'/bank/workshop?task=22');
 try{
  await act(async()=>{await button(tree,'创建新任务').props.onClick();await flush();});
  const query=new URLSearchParams(route.search);
  assert.equal(query.get('project'),'7');assert.equal(query.get('analysis'),'11');assert.equal(query.get('issues'),'I1');
  await act(async()=>{await button(tree,'创建任务并检查输入').props.onClick();await flush();});
  assert.equal(submitted.source_analysis_id,11);assert.deepEqual(submitted.source_issue_ids,['I1']);assert.deepEqual(submitted.project_ids,[7]);
 }finally{await cleanup(tree);}
});

test('recreating a superseded issue batch requires selecting a current report instead of generic questions',async()=>{
 globalThis.fetch=async url=>{
  const p=new URL(url).pathname;
  if(p.endsWith('/analyses/11'))return Response.json({...successful,is_latest_success:false,active_issue_ids:['I1']});
  if(p.endsWith('/projects'))return Response.json({items:[project]});
  if(p.endsWith('/22'))return Response.json(issueTask());
  return Response.json({items:[]});
 };
 const tree=await mount(Workshop,'/bank/workshop?task=22');
 try{
  await act(async()=>{await button(tree,'创建新任务').props.onClick();await flush();});
  assert.equal(new URLSearchParams(route.search).get('analysis'),'11');
  assert.match(text(tree),/已被.*替代/);
  assert.equal(button(tree,'创建任务并检查输入').props.disabled,true);
  assert.ok(tree.root.findAllByType('a').some(a=>a.props.href==='/assets/project/7'));
 }finally{await cleanup(tree);}
});

test('reading the dossier and AI report tabs never requests structural scan findings',async()=>{
 const requests=[];
 const get=repositoryFetch([successful]);
 globalThis.fetch=async url=>{requests.push(new URL(url).pathname);return get(url);};
 const tree=await mount(ProjectReview,'/assets/project/7','/assets/project/:id');
 try{
  for(const tab of ['process','evidence','improvements']) {
   await act(async()=>{navigateRoute('/assets/project/7?tab='+tab);await flush();});
  }
  assert.ok(requests.includes('/api/v1/projects/7/report'));
  assert.ok(requests.includes('/api/v1/projects/7/dossier'));
  assert.ok(!requests.some(p=>/\/(findings|scan)$/.test(p)));
 }finally{await cleanup(tree);}
});

test('switching project and failing its load clears the previous project report and actions',async()=>{
 let projectLoads=0;
 const previous=repositoryFetch([successful]);
 globalThis.fetch=async(url,options)=>{
  const p=new URL(url).pathname;
  if(p.endsWith('/projects') && ++projectLoads>1)return Response.json({detail:'cannot load next project'},{status:503});
  return previous(url,options);
 };
 const tree=await mount(ProjectReview,'/assets/project/7','/assets/project/:id');
 try{
  assert.match(text(tree),/Repository understanding/);
  await act(async()=>{navigateRoute('/assets/project/8');await flush();});
  assert.match(text(tree),/cannot load next project/);
  assert.doesNotMatch(text(tree),/Source project|Repository understanding|Timeout boundary/);
  assert.equal(button(tree,'AI 扫描'),undefined);
 }finally{await cleanup(tree);}
});

test('late dossier responses cannot replace the current project dossier',async()=>{
 let resolvePrevious;
 const other={...project,id:8,name:'Current second project'};
 globalThis.fetch=async url=>{
  const p=new URL(url).pathname;
  if(p.endsWith('/projects'))return Response.json({items:[project,other]});
  if(p.endsWith('/7/dossier'))return new Promise(resolve=>{resolvePrevious=resolve;});
  if(p.endsWith('/8/dossier'))return Response.json(dossier(8,'Second project report'));
  return Response.json({items:[]});
 };
 const tree=await mount(ProjectReview,'/assets/project/7','/assets/project/:id');
 try{
  assert.match(text(tree),/正在读取项目档案/);
  await act(async()=>{navigateRoute('/assets/project/8');await flush();});
  assert.match(text(tree),/Current second project.*Second project report/);
  await act(async()=>{resolvePrevious(Response.json(dossier(7,'Old project report')));await flush();});
  assert.equal(tree.root.findByType('select').props.value,8);
  assert.match(text(tree),/Second project report/);
  assert.doesNotMatch(text(tree),/Old project report/);
 }finally{await cleanup(tree);}
});

test('late project list responses cannot reset the selected project',async()=>{
 let resolvePrevious;
 let requests=0;
 const other={...project,id:8,name:'Current second project'};
 globalThis.fetch=async url=>{
  const p=new URL(url).pathname;
  if(p.endsWith('/projects')){
   if(++requests===1)return new Promise(resolve=>{resolvePrevious=resolve;});
   return Response.json({items:[other]});
  }
  if(p.endsWith('/8/dossier'))return Response.json(dossier(8,'Second project report'));
  return Response.json({items:[]});
 };
 const tree=await mount(ProjectReview,'/assets/project/7','/assets/project/:id');
 try{
  await act(async()=>{navigateRoute('/assets/project/8');await flush();});
  await act(async()=>{resolvePrevious(Response.json({items:[project]}));await flush();});
  assert.equal(tree.root.findByType('select').props.value,8);
  assert.match(text(tree),/Current second project.*Second project report/);
  assert.doesNotMatch(text(tree),/Source project/);
 }finally{await cleanup(tree);}
});
