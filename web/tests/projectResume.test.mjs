import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../src/api/client.ts';
import ProjectResume from '../src/components/ProjectResume.tsx';

test('project facts stay out of contribution, and only an explicit save persists the preview', async context => {
  const saved = new Map();
  const saves = [];
  const projectIdFor = path => Number(path.split('/')[2]);
  context.mock.method(api, 'get', async path => !path.endsWith('/resume') ? { id: projectIdFor(path), name: `项目 ${projectIdFor(path)}` } : ({
    project_id: projectIdFor(path),
    evidence: [{ id: 'claim-1', title: '仓库检测到重试模块', detail: '', basis:'code', source: { kind: 'analysis' }, citations: [{ path: 'worker.ts' }] }],
    suggestions: [{ id: 'todo-1', title: '计划做灾备', detail: '尚未完成' }],
    profile: projectIdFor(path) === 12 ? { responsibilities: '我设计了幂等键', outcomes: '通过重投测试验证' } : projectIdFor(path) === 13 ? { outcomes: null } : undefined,
    draft: saved.get(path) ?? null,
  }));
  context.mock.method(api, 'put', async (path, payload) => {
    saves.push({ path, ...payload });
    const draft = { contribution: payload.contribution, outcome: payload.outcome, content: `项目 ${projectIdFor(path)}\n本人职责：${payload.contribution}\n依据与结果：${payload.outcome}`, revision: payload.revision + 1, updated_at: '2026-11-18T00:00:00Z' };
    saved.set(path, draft);
    return { project_id: projectIdFor(path), draft };
  });
  const element = projectId => React.createElement(MemoryRouter, {
    future: { v7_startTransition: true, v7_relativeSplatPath: true },
  }, React.createElement(ProjectResume, { projectId }));
  let panel;
  const button = label => panel.root.findAllByType('button').find(node => node.children.includes(label));
  const click = async label => { await act(async () => button(label).props.onClick()); };
  const submit = async () => { await act(async () => panel.root.findByType('form').props.onSubmit({ preventDefault() {} })); };
  const type = async value => {
    await act(async () => panel.root.findAllByType('textarea')[0].props.onChange({ currentTarget: { value } }));
  };
  const confirm = async () => { await act(async () => panel.root.findByType('input').props.onChange({ currentTarget: { checked: true } })); };
  try {
    await act(async () => { panel = create(element(12)); });
    assert.match(JSON.stringify(panel.toJSON()), /代码观察/);
    await click('补充本人职责');
    assert.deepEqual(panel.root.findAllByType('textarea').map(node => node.props.value), ['', '']);
    await click('选入已登记职责');
    await click('选入已登记依据与结果');
    assert.equal(button('预览本地草稿').props.disabled, true);
    await confirm();
    await submit();
    assert.match(JSON.stringify(panel.toJSON()), /项目 12.*本人职责：我设计了幂等键.*依据与结果：通过重投测试验证/);
    assert.doesNotMatch(JSON.stringify(panel.toJSON()), /仓库检测到重试模块|计划做灾备/);
    assert.deepEqual(saves, []);
    await click('保存本地草稿');
    assert.deepEqual(saves, [{ path: '/projects/12/resume', project_id: 12, revision: 0, confirmed: true, contribution: '我设计了幂等键', outcome: '通过重投测试验证' }]);
    await act(async () => panel.update(element(13)));
    assert.equal(button('打开已保存草稿'), undefined);
    assert.doesNotMatch(JSON.stringify(panel.toJSON()), /我设计了幂等键/);
    await click('补充本人职责');
    assert.equal(button('选入已登记职责'), undefined);
    assert.equal(button('选入已登记依据与结果'), undefined);
    await act(async () => panel.update(element(14)));
    await click('补充本人职责');
    assert.deepEqual(panel.root.findAllByType('textarea').map(node => node.props.value), ['', '']);
    await act(async () => panel.update(element(12)));
    await click('打开已保存草稿');
    await click('返回编辑');
    await confirm();
    await type('我负责测试幂等键');
    assert.equal(button('预览本地草稿').props.disabled, true);
    await confirm();
    await submit();
    assert.match(JSON.stringify(panel.toJSON()), /我负责测试幂等键/);
    assert.equal(saves.length, 1);
    await click('保存本地草稿');
    assert.equal(saves[1].revision, 1);
  } finally {
    if (panel) await act(async () => panel.unmount());
  }
});
