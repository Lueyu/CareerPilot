import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {act,create} from 'react-test-renderer';
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const text=tree=>JSON.stringify(tree.toJSON());
const button=(tree,label)=>tree.root.findAllByType('button').find(n=>n.children.join('').includes(label));
const activity={id:55,kind:'PROJECT',title:'Add a bounded retry regression',revision:2,status:'READY',allowed_actions:['submit'],assistance:{hint_used:false,reference_viewed:false,ai_declared:false,feedback_seen:false},project_contract:{project_id:7,analysis_id:11,baseline_revision:'a'.repeat(40),allowed_paths:['src/worker.py'],acceptance:['Keep retries bounded.'],kind:'REGRESSION_TEST',version:'pc1'}};
const preview={snapshot_id:901,content_hash:'hash-one',baseline_revision:'a'.repeat(40),submission_revision:'user-snapshot',files:[{path:'src/worker.py',diff:'- pass\n+ retry()',before_hash:'before',after_hash:'after'}],excluded_paths:['.env'],test_evidence:'NOT_EXECUTED',limits:{}};
async function mount(props={}){let Component;try{Component=(await import('../src/components/ProjectPracticePanel.tsx')).default;}catch(e){assert.fail(`Project submission UI is required: ${e.message}`);}let tree;await act(async()=>{tree=create(React.createElement(Component,{activity,latest:{id:801,revision:1,status:'COMPLETED'},onSubmitted:()=>{},...props}));await flush();});return tree;}
async function close(tree){await act(async()=>tree.unmount());}

test('project code is previewed explicitly before submission and editing invalidates that preview',async()=>{
 const calls=[];globalThis.fetch=async(url,o={})=>{calls.push([new URL(url).pathname,JSON.parse(o.body)]);return Response.json(preview);};
 const tree=await mount();try{
  await act(async()=>tree.root.findByProps({'aria-label':'项目提交方式'}).props.onChange({target:{value:'patch'}}));
  await act(async()=>tree.root.findByProps({'aria-label':'项目 patch'}).props.onChange({target:{value:'--- a/src/worker.py\n+++ b/src/worker.py\n@@ -1 +1 @@\n-pass\n+retry()'}}));
  assert.equal(calls.length,0);await act(async()=>{await button(tree,'预览提交范围').props.onClick();await flush();});
  assert.equal(calls.length,1);assert.equal(calls[0][1].base_revision,'a'.repeat(40));assert.equal(calls[0][1].source_kind,'patch');
  assert.match(text(tree),/src\/worker.py/);assert.match(text(tree),/未执行|运行结果未验证/);assert.ok(button(tree,'确认快照并提交检查'));
  await act(async()=>tree.root.findByProps({'aria-label':'项目 patch'}).props.onChange({target:{value:'A changed patch'}}));
  assert.equal(button(tree,'确认快照并提交检查'),undefined);assert.equal(calls.filter(([p])=>p.endsWith('/project-submit')).length,0);
 }finally{await close(tree);}
});

test('explicitly selected local files retain editable repository paths and submit only the confirmed snapshot',async()=>{
 const calls=[],acks=[];globalThis.fetch=async(url,o={})=>{const p=new URL(url).pathname;calls.push([p,JSON.parse(o.body)]);return Response.json(p.endsWith('/project-preview')?preview:{submission_id:802,scoring_run_id:902,status:'QUEUED'},{status:p.endsWith('/project-preview')?200:202});};
 const tree=await mount({onSubmitted:ack=>acks.push(ack)});try{
  await act(async()=>{await tree.root.findByProps({'aria-label':'选择本地提交文件'}).props.onChange({target:{files:[{name:'worker.py',size:20,text:async()=> 'retry()'}]}});await flush();});
  assert.equal(calls.length,0);await act(async()=>tree.root.findByProps({'aria-label':'文件 1 的仓库路径'}).props.onChange({target:{value:'src/worker.py'}}));
  await act(async()=>{await button(tree,'预览提交范围').props.onClick();await flush();});assert.deepEqual(calls[0][1].files,[{path:'src/worker.py',content:'retry()'}]);
  await act(async()=>{await button(tree,'确认快照并提交检查').props.onClick();await flush();});
  const submitted=calls.find(([p])=>p.endsWith('/project-submit'))[1];assert.equal(submitted.snapshot_id,901);assert.equal(submitted.expected_activity_revision,2);assert.equal(submitted.parent_submission_id,801);assert.ok(submitted.request_key);assert.equal(acks[0].submission_id,802);
 }finally{await close(tree);}
});

test('a rejected project baseline keeps the user patch and cannot claim reviewed code or passed tests',async()=>{
 globalThis.fetch=async()=>Response.json({detail:'The baseline changed; inspect the new source version.'},{status:409});
 const tree=await mount();try{
  await act(async()=>tree.root.findByProps({'aria-label':'项目提交方式'}).props.onChange({target:{value:'patch'}}));
  await act(async()=>tree.root.findByProps({'aria-label':'项目 patch'}).props.onChange({target:{value:'My unsubmitted patch'}}));
  await act(async()=>{await button(tree,'预览提交范围').props.onClick();await flush();});assert.equal(tree.root.findByProps({'aria-label':'项目 patch'}).props.value,'My unsubmitted patch');assert.match(text(tree),/baseline changed/);assert.equal(button(tree,'确认快照并提交检查'),undefined);assert.doesNotMatch(text(tree),/检查已完成|测试通过/);
 }finally{await close(tree);}
});
