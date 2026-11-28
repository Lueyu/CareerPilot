import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { act, create } from "react-test-renderer";
import { api } from "../src/api/client.ts";
import { loadBankInventory, useBankInventory } from "../src/interview/library/useBankInventory.ts";

const inventory = Array.from({ length: 405 }, (_, i) => ({
  id: i + 1,
  short_code: `Q-${i + 1}`,
  title: `Question ${i + 1}`,
  topic: "rag",
  difficulty: i === 404 ? 5 : 1,
  source_type: "USER_PRIVATE",
  tags: [],
}));
const page = (offset) => ({
  total: inventory.length,
  items: inventory.slice(offset, offset + 200),
});
const offsetOf = (path) => Number(new URL(path, "http://fixture").searchParams.get("offset"));

test("complete inventory spans three capped pages and retains the last filtered sentinel", async (t) => {
  const offsets = [];
  t.mock.method(api, "get", async (path, signal) => {
    signal.throwIfAborted();
    const url = new URL(path, "http://fixture");
    assert.equal(url.searchParams.get("limit"), "200");
    assert.equal(url.searchParams.get("scope"), "mine");
    assert.equal(url.searchParams.get("q"), "中文");
    offsets.push(offsetOf(path));
    return page(offsetOf(path));
  });
  const result = await loadBankInventory("/bank?q=中文&scope=mine", new AbortController().signal);
  assert.deepEqual(offsets, [0, 200, 400]);
  assert.deepEqual(result.items, inventory);
  assert.deepEqual(
    result.items.filter((item) => item.difficulty === 5).map((item) => item.id),
    [405],
  );
});

test("empty, repeated and changing pages never return an incomplete inventory", async (t) => {
  for (const bad of [
    { total: 405, items: [] },
    { total: 405, items: inventory.slice(0, 200) },
    { total: 406, items: inventory.slice(200, 400) },
  ]) {
    const mocked = t.mock.method(api, "get", async (path) => (offsetOf(path) ? bad : page(0)));
    await assert.rejects(loadBankInventory("/bank", new AbortController().signal), /重试/);
    mocked.mock.restore();
  }
});

test("a mid-page failure rejects, and retry starts a complete inventory", async (t) => {
  let failed = false;
  t.mock.method(api, "get", async (path) => {
    if (offsetOf(path) === 200 && !failed) {
      failed = true;
      throw new Error("503 fixture");
    }
    return page(offsetOf(path));
  });
  await assert.rejects(loadBankInventory("/bank", new AbortController().signal), /503 fixture/);
  assert.equal((await loadBankInventory("/bank", new AbortController().signal)).items.length, 405);
});

test("cancellation during a page stops all subsequent page requests", async (t) => {
  const controller = new AbortController();
  let calls = 0;
  t.mock.method(api, "get", async () => {
    calls++;
    controller.abort();
    return page(0);
  });
  await assert.rejects(loadBankInventory("/bank", controller.signal), { name: "AbortError" });
  assert.equal(calls, 1);
});

test("hook holds loading until full completion, exposes retry and cancels old queries/unmount", async (t) => {
  const pending = [];
  t.mock.method(
    api,
    "get",
    (path, signal) =>
      new Promise((resolve, reject) => pending.push({ path, signal, resolve, reject })),
  );
  let state;
  function Probe({ path }) {
    state = useBankInventory(path);
    return null;
  }
  let tree;
  t.after(async () => act(async () => tree?.unmount()));
  await act(async () => {
    tree = create(React.createElement(Probe, { path: "/bank?q=old" }));
  });
  assert.equal(state.loading, true);
  assert.equal(state.data, undefined);
  await act(async () => pending[0].resolve(page(0)));
  assert.equal(state.loading, true);
  assert.equal(state.data, undefined);
  await act(async () => pending[1].reject(new Error("fixture unavailable")));
  assert.equal(state.loading, false);
  assert.match(state.error, /fixture unavailable/);
  await act(async () => state.reload());
  assert.equal(state.loading, true);
  await act(async () => {
    tree.update(React.createElement(Probe, { path: "/bank?q=new" }));
  });
  assert.equal(pending[2].signal.aborted, true);
  await act(async () => pending[2].resolve(page(0)));
  assert.equal(pending.length, 4);
  await act(async () => pending[3].resolve({ total: 1, items: [inventory[404]] }));
  assert.equal(state.loading, false);
  assert.deepEqual(
    state.data.items.map((row) => row.id),
    [405],
  );
  await act(async () => state.reload());
  const last = pending.at(-1);
  await act(async () => tree.unmount());
  assert.equal(last.signal.aborted, true);
  await act(async () => last.resolve(page(0)));
  assert.equal(pending.at(-1), last);
});

test("api.get forwards the abort signal to the actual transport", async (t) => {
  const controller = new AbortController();
  t.mock.method(globalThis, "fetch", async (_url, options) => {
    assert.equal(options.signal, controller.signal);
    return Response.json({ total: 0, items: [] });
  });
  assert.equal((await api.get("/bank", controller.signal)).total, 0);
});
