import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../src/api/client.ts';
import PracticePanel from '../src/components/StudyPracticePanel.tsx';

const olderPractice={id:1,card_id:7,kind:'quiz',question:'旧问题',submissions:[]};
const linkedPractice={id:2,card_id:42,kind:'quiz',question:'卡片问题',submissions:[
  {submission_id:21,revision:1,status:'COMPLETED',payload:{text_answer:'先测量延迟再调整'},feedback:{
    criterion_scores:[{criterion_id:'internal_id',score:2,answer_quotes:['测量延迟','测量延迟','不存在的引用'],reasoning_summary:'考虑了指标',why_not_higher:'没有比较基线',missing_points:['没有比较基线','需要评估误差']}],
    correct:[{why:'考虑了指标'}],reasoning:'重复评语',next_action:'REVISE',versions:{rubric_version:'opaque-version'},
  }},
  {submission_id:22,revision:2,status:'FAILED',can_retry:true,payload:{text_answer:'再次作答'},error:{message:'任务暂时失败'},feedback:{scope_note:'不得显示旧反馈'}},
]};

test('card link creates once, resumes the matching quiz after reload, and renders only grounded feedback',async context=>{
  let items=[olderPractice];
  let panel;
  const posts=[];
  context.mock.method(api,'get',async path=>path==='/profile'?{domains:[]}:{items});
  context.mock.method(api,'post',async (path,body)=>{
    posts.push({path,body});
    if(path.endsWith('/retry'))return {};
    items=[linkedPractice,...items];
    return linkedPractice;
  });
  const mount=async()=>{
    await act(async()=>{panel=create(React.createElement(MemoryRouter,{initialEntries:['/study/chat/9?card=42&practice=quiz'],future:{v7_startTransition:true,v7_relativeSplatPath:true}},React.createElement(PracticePanel,{sessionId:9,cardId:42,ended:false})));});
  };
  try {
    await mount();
    assert.deepEqual(posts,[{path:'/study/sessions/9/practices',body:{kind:'quiz',card_id:42,skill_key:undefined,request_key:'study-card-quiz-42'}}]);
    await act(async()=>panel.unmount());
    items=[olderPractice,linkedPractice];
    await mount();
    assert.equal(posts.length,1);
    const article=panel.root.findByType('article');
    assert.ok(article.findAllByType('p').some(node=>node.children.includes('卡片问题')));
    assert.deepEqual(article.findAllByType('q').map(node=>node.children.join('')),['测量延迟']);
    const rendered=JSON.stringify(panel.toJSON());
    assert.doesNotMatch(rendered,/internal_id|不存在的引用|重复评语|REVISE|opaque-version|不得显示旧反馈/);
    assert.equal(rendered.match(/考虑了指标/g).length,1);
    assert.equal(rendered.match(/没有比较基线/g).length,1);
    assert.match(rendered,/评价失败/);
    assert.match(rendered,/任务暂时失败/);
    await act(async()=>panel.root.findByProps({id:'study-quiz'}).props.onClick());
    assert.equal(posts.length,1);
    await act(async()=>panel.root.findAllByType('button').find(node=>node.children.includes('重试评价')).props.onClick());
    assert.equal(posts[1].path,'/learning/submissions/22/retry');
  } finally { if(panel)await act(async()=>panel.unmount()); }
});
