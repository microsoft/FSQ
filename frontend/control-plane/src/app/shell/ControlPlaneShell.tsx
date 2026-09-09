import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Menu, X } from 'lucide-react';
import { ControlPlaneSidebar } from './ControlPlaneSidebar';
import { CONTROL_PLANE_NAVIGATION, type ControlPlanePageId, type WorkspaceNavigationItem } from './navigation';
import './shell.css';

interface ControlPlaneShellProps {
  activePage: ControlPlanePageId;
  title: string;
  description: string;
  outletPresentation?: 'default' | 'full-bleed';
  titleContent?: ReactNode;
  titleActions?: ReactNode;
  children: ReactNode;
  workspaces?: readonly WorkspaceNavigationItem[];
  selectedWorkspaceId?: string | null;
  workspaceRegistryStatus?: 'loading' | 'ready' | 'error';
  workspaceRegistryError?: string;
  onNavigate?: (page: ControlPlanePageId) => void;
  onRetryWorkspaces?: () => void;
  onCreateWorkspace?: (restoreFocus?: () => void) => void;
  onSelectWorkspace?: (workspaceId: string) => void;
  onDiagnoseWorkspace?: (workspaceId:string) => void;
  interactionLocked?: boolean;
}

export function ControlPlaneShell({ activePage, title, description, outletPresentation = 'default', titleContent, titleActions, children, workspaces, selectedWorkspaceId, workspaceRegistryStatus, workspaceRegistryError, onNavigate, onRetryWorkspaces, onCreateWorkspace, onSelectWorkspace, onDiagnoseWorkspace, interactionLocked }: ControlPlaneShellProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const drawerFocusTarget = useRef<string | null>(null);

  useEffect(() => {
    const query = window.matchMedia('(max-width: 980px)');
    const update = () => setNarrow(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);

  useEffect(() => {
    if (!drawerOpen) return;
    const drawer = drawerRef.current;
    const getFocusable = () => drawer?.querySelectorAll<HTMLElement>('button:not([disabled]), [tabindex]:not([tabindex="-1"])');
    const focusable = getFocusable();
    const requestedFocus = drawerFocusTarget.current ? document.getElementById(drawerFocusTarget.current) : null;
    (requestedFocus ?? focusable?.[0])?.focus();
    drawerFocusTarget.current = null;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        toggleRef.current?.focus();
        setDrawerOpen(false);
        return;
      }
      const controls = getFocusable();
      if (event.key !== 'Tab' || !controls?.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [drawerOpen]);

  const closeDrawer = () => {
    if (drawerOpen) toggleRef.current?.focus();
    setDrawerOpen(false);
  };

  const restoreWorkspaceCreateFocus = () => {
    const focusCreate = () => document.getElementById('workspace-create-sidebar')?.focus();
    if (narrow) {
      drawerFocusTarget.current = 'workspace-create-sidebar';
      setDrawerOpen(true);
      return;
    }
    focusCreate();
  };

  return (
    <div className="cp-shell">
      <button
        ref={toggleRef}
        className="cp-menu-button"
        type="button"
        aria-label="Open navigation"
        aria-expanded={drawerOpen}
        aria-controls="control-plane-sidebar"
        onClick={() => setDrawerOpen(true)}
      ><Menu aria-hidden="true"/></button>
      {drawerOpen && <button className="cp-drawer-scrim" type="button" aria-label="Dismiss navigation overlay" onClick={closeDrawer} />}
      <aside ref={drawerRef} id="control-plane-sidebar" className={`cp-sidebar${drawerOpen ? ' cp-sidebar--open' : ''}`} aria-label="Control Plane sidebar" aria-hidden={narrow && !drawerOpen ? true : undefined} inert={narrow && !drawerOpen ? true : undefined}>
        <button className="cp-drawer-close" type="button" aria-label="Close navigation" onClick={closeDrawer}><X aria-hidden="true"/></button>
        <ControlPlaneSidebar
          interactionLocked={interactionLocked}
          activePage={activePage}
          navigation={CONTROL_PLANE_NAVIGATION}
          workspaces={workspaces}
          selectedWorkspaceId={selectedWorkspaceId}
          workspaceRegistryStatus={workspaceRegistryStatus}
          workspaceRegistryError={workspaceRegistryError}
          onNavigate={(page) => { onNavigate?.(page); closeDrawer(); }}
          onRetryWorkspaces={onRetryWorkspaces}
          onCreateWorkspace={() => { onCreateWorkspace?.(restoreWorkspaceCreateFocus); closeDrawer(); }}
          onSelectWorkspace={(workspaceId) => { onSelectWorkspace?.(workspaceId); closeDrawer(); }}
          onDiagnoseWorkspace={(workspaceId)=>{onDiagnoseWorkspace?.(workspaceId);closeDrawer();}}
        />
      </aside>
      <div className={`cp-main-column${outletPresentation === 'full-bleed' ? ' cp-main-column--full-bleed' : ''}`}>
        <header className="cp-titlebar">
          {titleContent ?? <div className="cp-title-context"><strong>{title}</strong><small>{description}</small></div>}
          {titleActions && <div className="cp-title-actions">{titleActions}</div>}
        </header>
        <main className={`cp-page-outlet${outletPresentation === 'full-bleed' ? ' cp-page-outlet--full-bleed' : ''}`} id="main-content">{children}</main>
      </div>
    </div>
  );
}
