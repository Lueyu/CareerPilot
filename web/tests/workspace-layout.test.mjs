import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement as h } from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { RegisteredWorkspaceLayout, WorkspaceLayout } from '../src/components/WorkspaceLayout.tsx';

const registeredRoutes = ['/', '/bank', '/study/chat', '/podcasts', '/exam', '/learning', '/memory', '/settings'];

function renderShell(t, path, layout) {
  const original = { window: globalThis.window, document: globalThis.document, localStorage: globalThis.localStorage, fetch };
  const media = Object.assign(new EventTarget(), { matches: true });
  const stored = new Map([['cp-theme', 'light']]);
  globalThis.window = { matchMedia: () => media };
  globalThis.document = { documentElement: { dataset: {} } };
  globalThis.localStorage = { getItem: key => stored.get(key), setItem: (key, value) => stored.set(key, value) };
  globalThis.fetch = () => assert.fail('The shell should not fetch workspace data.');
  const dialog = {
    open: false,
    showModal() { this.open = true; },
    close() { this.open = false; },
  };
  let view;
  t.after(() => {
    act(() => view?.unmount());
    Object.assign(globalThis, original);
  });
  act(() => {
    view = create(h(MemoryRouter, {
      initialEntries: [path],
      future: { v7_startTransition: true, v7_relativeSplatPath: true },
    }, h(Routes, null, h(Route, { element: layout },
      registeredRoutes.map(route => h(Route, { key: route, path: route, element: h('main', null, route) })),
    ))), { createNodeMock: node => node.type === 'dialog' ? dialog : null });
  });
  return { root: view.root, dialog, media, stored };
}

function navDestinations(sidebar) {
  return sidebar.findAllByType('a').filter(link => link.props.className !== 'brand').map(link => link.props.href);
}

for (const route of registeredRoutes) {
  test(`registered shell renders the allowed navigation and a drawer at ${route}`, t => {
    const { root, dialog, media, stored } = renderShell(t, route, h(RegisteredWorkspaceLayout, { accountName: 'learner' }));
    const sidebar = root.findByType('aside');
    assert.deepEqual(navDestinations(sidebar), registeredRoutes);
    assert.deepEqual(
      sidebar.findAllByType('a').filter(link => link.props['aria-current'] === 'page').map(link => link.props.href),
      [route],
    );
    assert.equal(sidebar.findByProps({ title: 'learner' }).children[0], 'learner');
    assert.equal(sidebar.findAllByType('button').length, 0);
    assert.equal(root.findByType('main').children[0], route);
    assert.equal(root.findAllByProps({ className: 'search-box' }).length, 0);

    const menu = root.findByProps({ className: 'btn btn-outline workspace-mobile-nav-button' });
    assert.equal(root.findByProps({ className: 'btn feedback-entry' }).children[0], '提意见');
    act(() => menu.props.onClick());
    assert.equal(dialog.open, true);
    assert.equal(menu.props['aria-expanded'], true);
    const drawer = root.findByProps({ className: 'workspace-mobile-drawer' });
    assert.deepEqual(navDestinations(drawer.findByType('aside')), registeredRoutes);
    act(() => drawer.findByType('button').props.onClick());
    assert.equal(dialog.open, false);

    act(() => menu.props.onClick());
    media.matches = false;
    act(() => media.dispatchEvent(new Event('change')));
    assert.equal(dialog.open, false);
    assert.equal(menu.props['aria-expanded'], false);

    assert.equal(document.documentElement.dataset.theme, 'light');
    act(() => root.findByProps({ 'aria-label': '切换主题' }).props.onClick());
    assert.equal(document.documentElement.dataset.theme, 'dark');
    assert.equal(stored.get('cp-theme'), 'dark');
  });
}

test('personal shell keeps its routes, workspace switcher and injected controls', t => {
  const calls = [];
  const { root } = renderShell(t, '/bank', h(WorkspaceLayout, {
    workspace: {
      kind: 'personal', label: '演示工作空间', name: 'demo',
      openSwitcher: () => calls.push('workspace'),
      actions: {
        search: () => calls.push('search'),
        quick: () => calls.push('quick'),
        notifications: () => calls.push('notifications'),
      },
    },
    overlays: h('div', { id: 'overlays' }),
  }));
  const sidebar = root.findByType('aside');
  assert.deepEqual(navDestinations(sidebar),
    ['/', '/benchmark', '/exam', '/study/chat', '/podcasts', '/learning', '/assets', '/memory', '/observatory', '/evolution', '/settings']);
  assert.equal(sidebar.findByProps({ className: 'ws-label' }).children[0], '演示工作空间');
  act(() => sidebar.findByType('button').props.onClick());
  act(() => root.findByProps({ className: 'search-box' }).props.onClick());
  act(() => root.findByProps({ className: 'topbar-right' }).children
    .find(node => node.props?.className === 'btn btn-primary').props.onClick());
  act(() => root.findAllByProps({ className: 'icon-btn' })[1].props.onClick());
  assert.deepEqual(calls, ['workspace', 'search', 'quick', 'notifications']);
  assert.ok(root.findByProps({ id: 'overlays' }));
});

test('navigating from the drawer closes it and updates the breadcrumb', t => {
  const { root, dialog } = renderShell(t, '/', h(RegisteredWorkspaceLayout, { accountName: 'learner' }));
  const menu = root.findByProps({ className: 'btn btn-outline workspace-mobile-nav-button' });
  act(() => menu.props.onClick());
  const bank = root.findByProps({ className: 'workspace-mobile-drawer' }).findByProps({ href: '/bank' });
  act(() => bank.props.onClick({ button: 0, defaultPrevented: false, preventDefault() {} }));
  assert.equal(dialog.open, false);
  assert.equal(menu.props['aria-expanded'], false);
  assert.equal(root.findByProps({ className: 'crumb-cur' }).children[0], '面试题库');
});

for (const route of registeredRoutes) {
  test(`personal mobile navigation remains available at ${route}`, t => {
    const { root, dialog } = renderShell(t, route, h(WorkspaceLayout, {
      workspace: {
        kind: 'personal', label: '个人工作空间', name: 'fixture',
        openSwitcher() {},
        actions: { search() {}, quick() {}, notifications() {} },
      },
    }));
    const compact = route.startsWith('/study/') || route === '/memory' || route.startsWith('/assets');
    const menu = root.findAllByProps({ className: 'btn btn-outline workspace-mobile-nav-button' });
    assert.equal(menu.length, 1);
    assert.ok(root.findByProps({ className: 'btn feedback-entry' }));
    if (compact) {
      assert.ok(root.findByProps({ className: 'app app-compact-nav' }));
      assert.ok(navDestinations(root.findByType('aside')).includes('/study/chat'));
    } else {
      assert.ok(root.findByProps({ className: 'app app-drawer-nav' }));
    }
    act(() => menu[0].props.onClick());
    assert.equal(dialog.open, true);
    const drawer = root.findByProps({ className: 'workspace-mobile-drawer' });
    assert.deepEqual(navDestinations(drawer.findByType('aside')), navDestinations(root.findAllByType('aside')[0]));
    act(() => drawer.findByProps({ className: 'btn btn-outline workspace-mobile-nav-close' }).props.onClick());
    assert.equal(dialog.open, false);
  });
}
