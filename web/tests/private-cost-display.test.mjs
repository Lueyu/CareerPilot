import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import Ops from '../src/pages/Ops.tsx';

for (const cost of [null, 0, 0.014]) {
  test(`private costs distinguish ${cost === null ? 'unknown' : cost} from zero and obsolete estimates`, async t => {
    const storage = { getItem: () => null, removeItem: () => {} };
    for (const name of ['localStorage', 'sessionStorage']) {
      const descriptor = Object.getOwnPropertyDescriptor(globalThis, name);
      Object.defineProperty(globalThis, name, { configurable: true, value: storage });
      t.after(() => descriptor ? Object.defineProperty(globalThis, name, descriptor) : delete globalThis[name]);
    }
    const summary = {
      business: { users: { total: 0, active_1d: 0, active_7d: 0, active_30d: 0 }, events_total: 0,
        top_events: [], top_pages: [], devices: [], regions: [], locales: [] },
      agent: { calls: 1, ok_rate: 100, errors: {}, latency_ms: { p50: 1, p95: 1, p99: 1, avg: 1 },
        ttft_ms: { p50: 1, p95: 1 }, tokens: { prompt: 5, completion: 3, total: 8 }, cost_estimate_cny: cost,
        by_model: [{ model: 'synthetic', calls: 1, errors: 0, avg_ms: 1, tokens: 8, cost_cny: cost }],
        finish_reasons: [], stream_share: 100 },
      quality: { sessions: 0, messages: 0, assistant: { completed: 0, failed: 0, interrupted: 0, generating: 0 },
        failure_rate: 0, retry_rate: 0 },
      engine: { traces: 0, success_rate: 0, spans: 0, span_error_rate: 0 }, trend: [],
    };
    t.mock.method(globalThis, 'fetch', async url => Response.json(url.endsWith('/ops/metrics/summary') ? summary : {
      items: [{ id: 1, kind: 'synthetic', model: 'synthetic', duration_ms: 1, ttft_ms: 1, total_tokens: 8,
        cost_cny: cost, ok: true, error_kind: null, finish_reason: 'stop', stream: true, created_at: null }],
    }));
    let tree;
    t.after(() => act(async () => tree?.unmount()));
    await act(async () => {
      tree = create(React.createElement(MemoryRouter, { future: { v7_startTransition: true, v7_relativeSplatPath: true } },
        React.createElement(Ops)));
    });
    const visible = JSON.stringify(tree.toJSON());
    assert.match(visible, /配置计费/);
    assert.doesNotMatch(visible, /¥null|估算成本/);
    if (cost === null) assert.match(visible, /费用未知/);
    else assert.ok(visible.includes(`¥${cost}`));
  });
}
