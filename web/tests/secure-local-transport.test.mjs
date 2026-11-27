import assert from 'node:assert/strict';
import test from 'node:test';
import { spawnSync } from 'node:child_process';

test('runtime login, selection before reload, and explicit relogin preserve the document session', () => {
  const register = new URL('./register.mjs', import.meta.url).href;
  const transport = new URL('../src/api/localTransport.ts', import.meta.url).href;
  const result = spawnSync(process.execPath, ['--import', register, '--input-type=module', '--eval', `
    import assert from 'node:assert/strict';
    const storage = () => { const m = new Map(); return {
      getItem: k => m.get(k) ?? null, setItem: (k,v) => m.set(k,v), removeItem: k => m.delete(k),
    }; };
    globalThis.window = {};
    Object.defineProperty(globalThis, 'localStorage', { value: storage() });
    Object.defineProperty(globalThis, 'sessionStorage', { value: storage() });
    const t = await import(${JSON.stringify(transport)});
    assert.equal(t.requiresLocalLogin, true);
    assert.equal(t.workspaceLogin, false);
    const calls=[];
    globalThis.fetch = async (_url, options) => { calls.push(options.headers); return Response.json({}); };
    await t.localFetch('/dashboard', {});
    assert.equal(calls.at(-1)['X-Api-Token'], '');
    // First login after module evaluation changes the explicit document session.
    t.beginLocalSession('first-workspace', 'runtime-secret-one');
    await t.localFetch('/dashboard', {});
    assert.equal(calls.at(-1)['X-Api-Token'], 'runtime-secret-one');
    assert.equal(calls.at(-1)['X-Workspace'], 'first-workspace');
    // Menu selection persists the next document without retargeting late requests.
    t.selectLocalWorkspace('next-workspace');
    await t.localFetch('/exams/old-rendered-id', {});
    assert.equal(localStorage.getItem('cp-workspace'), 'next-workspace');
    assert.equal(calls.at(-1)['X-Workspace'], 'first-workspace');
    // A fresh document binds the selected workspace and still reads runtime token.
    const next = await import(${JSON.stringify(transport)} + '?reload');
    assert.equal(next.localWorkspace(), 'next-workspace');
    t.clearLocalSession();
    // Same token may be used again; explicit login is the session boundary.
    t.beginLocalSession('third-workspace', 'runtime-secret-one');
    await t.localFetch('/dashboard', {});
    assert.equal(calls.at(-1)['X-Workspace'], 'third-workspace');
    t.clearLocalSession();
    await t.localFetch('/dashboard', {});
    assert.equal(calls.at(-1)['X-Api-Token'], '');
  `], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
});
