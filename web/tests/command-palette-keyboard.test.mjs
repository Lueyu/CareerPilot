import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { AppStateProvider } from '../src/components/AppState.tsx';
import { CommandPalette } from '../src/components/Overlays.tsx';

async function mountPalette(t) {
  let path;
  let tree;
  function RouteObserver() {
    path = useLocation().pathname;
    return null;
  }

  class FocusableElement {
    isConnected = true;
    focus() { document.activeElement = this; }
  }
  const trigger = new FocusableElement();
  const input = new FocusableElement();
  const viewport = new EventTarget();
  globalThis.window = viewport;
  globalThis.document = { activeElement: trigger };
  globalThis.HTMLElement = FocusableElement;
  t.mock.method(globalThis, 'fetch', async url => {
    switch (new URL(url).pathname) {
      case '/api/v1/profile':
        return Response.json({ domains: [
          { key: 'rag', name: 'RAG 检索', level: null },
          { key: 'tools', name: '工具调用', level: 2 },
        ] });
      case '/api/v1/learning':
        return Response.json({ today: [{ id: 42, title: 'RAG 练习', status: 'PENDING' }] });
      default:
        assert.fail(`Unexpected search request: ${url}`);
    }
  });
  t.after(() => {
    act(() => tree?.unmount());
    delete globalThis.window;
    delete globalThis.document;
    delete globalThis.HTMLElement;
  });

  const h = React.createElement;
  await act(async () => {
    tree = create(
      h(MemoryRouter, { future: { v7_startTransition: true, v7_relativeSplatPath: true } },
        h(AppStateProvider, null, h(RouteObserver), h(CommandPalette))),
      { createNodeMock: node => node.type === 'input' ? input : null },
    );
  });

  const searchBox = () => tree.root.findByType('input');
  const pressGlobal = async (key, modifiers = {}) => {
    const event = Object.assign(new Event('keydown', { cancelable: true }), { key, ...modifiers });
    await act(async () => viewport.dispatchEvent(event));
    return event;
  };
  const pressInput = key => act(() => {
    searchBox().props.onKeyDown({ key, nativeEvent: {}, preventDefault() {} });
  });
  const search = value => act(() => searchBox().props.onChange({ target: { value } }));
  const options = () => tree.root.findAllByProps({ role: 'option' });
  return { searchBox, pressGlobal, pressInput, search, options, input, trigger, pathname: () => path, tree };
}

test('keyboard search selects the second result across groups and opens its route', async t => {
  const palette = await mountPalette(t);
  const shortcut = await palette.pressGlobal('k', { ctrlKey: true });
  assert.equal(shortcut.defaultPrevented, true);
  assert.equal(document.activeElement, palette.input);

  palette.search('RAG');
  const [first, second] = palette.options();
  assert.equal(palette.options().length, 2);
  assert.equal(first.props['aria-selected'], true);
  palette.pressInput('ArrowDown');
  assert.equal(document.activeElement, palette.input);
  assert.equal(palette.searchBox().props['aria-activedescendant'], second.props.id);
  assert.equal(palette.options()[1].props['aria-selected'], true);
  palette.pressInput('Enter');
  assert.equal(palette.pathname(), '/learning/task/42');
  assert.equal(palette.tree.root.findAllByType('input').length, 0);
});

test('empty search ignores Enter and Escape restores the previous focus', async t => {
  const palette = await mountPalette(t);
  await palette.pressGlobal('k', { metaKey: true });
  palette.search('no-match');
  assert.match(JSON.stringify(palette.tree.toJSON()), /无匹配结果/);
  assert.equal(palette.searchBox().props['aria-activedescendant'], undefined);
  palette.pressInput('Enter');
  assert.equal(palette.pathname(), '/');
  assert.equal(document.activeElement, palette.input);

  await palette.pressGlobal('Escape');
  assert.equal(palette.tree.root.findAllByType('input').length, 0);
  assert.equal(document.activeElement, palette.trigger);
});
