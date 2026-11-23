import React from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { AppStateProvider } from './AppState';
import Sidebar, { type SidebarWorkspace } from './Sidebar';
import Topbar, { type TopbarActions } from './Topbar';
import './workspaceLayout.css';

export type WorkspaceLayoutProps =
  | {
      workspace: Extract<SidebarWorkspace, { kind: 'personal' }> & { actions: TopbarActions };
      overlays: React.ReactNode;
    }
  | { workspace: Extract<SidebarWorkspace, { kind: 'registered' }>; overlays?: never };

export function WorkspaceLayout({ workspace, overlays }: WorkspaceLayoutProps) {
  const { pathname, key } = useLocation();
  const registered = workspace.kind === 'registered';
  const study = pathname.startsWith('/study/');
  const usesMobileDrawer = registered || pathname === '/learning' || pathname === '/bank';
  const compactNav = !registered && (
    study || pathname === '/memory' || pathname === '/assets' || pathname.startsWith('/assets/project/')
  );
  const drawerId = React.useId();
  const mobileNav = React.useRef<HTMLDialogElement>(null);
  const [mobileNavOpen, setMobileNavOpen] = React.useState(false);

  const closeMenu = React.useCallback(() => {
    if (mobileNav.current?.open) mobileNav.current.close();
    setMobileNavOpen(false);
  }, []);

  React.useEffect(closeMenu, [key, closeMenu]);

  React.useEffect(() => {
    if (!usesMobileDrawer) return;
    const media = window.matchMedia('(max-width: 760px)');
    const onResize = () => { if (!media.matches) closeMenu(); };
    media.addEventListener('change', onResize);
    return () => media.removeEventListener('change', onResize);
  }, [usesMobileDrawer, closeMenu]);

  return (
    <div className={`app${compactNav ? ' app-compact-nav' : ''}${usesMobileDrawer ? ' app-drawer-nav' : ''}`}>
      <Sidebar workspace={workspace} />
      {usesMobileDrawer && (
        <dialog
          ref={mobileNav}
          id={drawerId}
          className="workspace-mobile-drawer"
          aria-label="CareerPilot 导航"
          onClose={() => setMobileNavOpen(false)}
          onClick={event => {
            const target = event.target;
            if (target === event.currentTarget || (target instanceof Element && target.closest('a, button.ws-card'))) closeMenu();
          }}
        >
          <button className="btn btn-outline workspace-mobile-nav-close" type="button" onClick={closeMenu}>关闭菜单</button>
          {mobileNavOpen && <Sidebar workspace={workspace} />}
        </dialog>
      )}
      <div className="app-main">
        <Topbar
          workspaceLabel={registered ? workspace.label : '个人工作空间'}
          actions={workspace.kind === 'personal' ? workspace.actions : undefined}
          beforeCrumb={usesMobileDrawer && (
            <button
              className="btn btn-outline workspace-mobile-nav-button"
              type="button"
              aria-haspopup="dialog"
              aria-controls={drawerId}
              aria-expanded={mobileNavOpen}
              onClick={() => {
                mobileNav.current?.showModal();
                setMobileNavOpen(true);
              }}
            >菜单</button>
          )}
        />
        <div className={study ? 'app-body app-body-study' : 'app-body'}>
          <Outlet />
        </div>
      </div>
      {overlays}
    </div>
  );
}

export interface RegisteredWorkspaceLayoutProps {
  accountName: string;
}

export function RegisteredWorkspaceLayout({ accountName }: RegisteredWorkspaceLayoutProps) {
  return (
    <AppStateProvider>
      <WorkspaceLayout workspace={{ kind: 'registered', label: '我的工作空间', name: accountName }} />
    </AppStateProvider>
  );
}
