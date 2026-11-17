import assert from 'node:assert/strict';
import test from 'node:test';

test('model-generated and imported questions stay labelled as personal without inventing a company',async()=>{
  const {bankSourcePresentation}=await import('../src/lib/bankSource.ts');
  assert.deepEqual(bankSourcePresentation({source_type:'GENERATED',company:null}),{company:'个人题库',isPrivate:true,editable:true,label:'模型生成'});
  assert.equal(bankSourcePresentation({source_type:'USER_PRIVATE',company:null}).company,'个人题库');
  assert.equal(bankSourcePresentation({source_type:'USER_PRIVATE',company:'用户记录的来源'}).company,'用户记录的来源');
});

test('shared licensed and verified questions have a read-only presentation',async()=>{
  const {bankSourcePresentation}=await import('../src/lib/bankSource.ts');
  for(const source_type of ['PUBLIC_LICENSED','VERIFIED_PUBLIC']){
    const source=bankSourcePresentation({source_type,company:null});
    assert.equal(source.editable,false);
    assert.equal(source.isPrivate,false);
    assert.equal(source.company,'公开题库');
    assert.equal(source.label,'共享题库 · 只读');
  }
});
