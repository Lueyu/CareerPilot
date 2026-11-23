import assert from 'node:assert/strict';
import test from 'node:test';
import { readMessageStream } from '../src/api/client.ts';

const frame = (status, content, newline = '\n') =>
  'data: ' + JSON.stringify({ id: 7, status, content, attempt: 1, model: null, error: null, stage: status }) + newline.repeat(2);

async function read(t, chunks, events) {
  const bytes = new TextEncoder();
  t.mock.method(globalThis, 'fetch', async () => new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(bytes.encode(chunk));
      controller.close();
    },
  })));
  await readMessageStream('/fixture/stream', new AbortController().signal, event => events.push(event.status));
}

test('EOF after a heartbeat and a partial snapshot triggers recovery', async t => {
  const events = [];
  await assert.rejects(read(t, [': heartbeat\n\n', frame('generating', '草稿'), 'data: {'], events));
  assert.deepEqual(events, ['generating']);
});

test('a snapshot without a recognized status cannot mark the reply complete', async t => {
  const events = [];
  await assert.rejects(read(t, [frame('unknown', '')], events));
  assert.deepEqual(events, []);
});

for (const status of ['completed', 'failed', 'interrupted']) {
  test('recognizes split CRLF frames ending in ' + status, async t => {
    const events = [];
    const body = frame('generating', '草稿', '\r\n') + frame(status, '定稿', '\r\n');
    await read(t, Array.from(body), events);
    assert.deepEqual(events, ['generating', status]);
  });
}
