import logoLight from '../../assets/logo-light.svg';
import { useId, useState, type ComponentType } from 'react';
import { AlertTriangle, ChevronDown, Check, CircleHelp, Folder, History, House, LoaderCircle, PanelTop, Plus, RefreshCw, Settings } from 'lucide-react';
import type { ControlPlanePageId, NavigationIcon, NavigationItem, WorkspaceNavigationItem } from './navigation';

interface ControlPlaneSidebarProps {
  activePage: ControlPlanePageId;
  navigation: readonly NavigationItem[];
  workspaces?: readonly WorkspaceNavigationItem[];
  selectedWorkspaceId?: string | null;
  workspaceRegistryStatus?: 'loading' | 'ready' | 'error';
  workspaceRegistryError?: string;
  onNavigate?: (page: ControlPlanePageId) => void;
  onRetryWorkspaces?: () => void;
  onCreateWorkspace?: () => void;
  onSelectWorkspace?: (workspaceId: string) => void;
  onDiagnoseWorkspace?: (workspaceId:string) => void;
  interactionLocked?: boolean;
}

function NavigationGlyph({ icon }: { icon: NavigationIcon }) {
  const icons: Record<NavigationIcon, ComponentType<{ 'aria-hidden': true }>> = {
    overview: House,
    workspace: Folder,
    devices: PanelTop,
    runs: History,
    config: Settings,
    settings: CircleHelp,
  };
  const Icon = icons[icon];
  return <Icon aria-hidden={true} />;
}

function workspaceInitials(label: string) {
  const words = label.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return '?';
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return words.slice(0, 2).map((word) => word[0]).join('').toUpperCase();
}

function NavGroup({ items, activePage, onNavigate, interactionLocked }: Pick<ControlPlaneSidebarProps, 'activePage' | 'onNavigate' | 'interactionLocked'> & { items: readonly NavigationItem[] }) {
  return items.map((item) => item.available ? (
    <button
      key={item.id}
      className="cp-nav-item"
      type="button"
      aria-current={item.id === activePage ? 'page' : undefined}
      disabled={interactionLocked}
      onClick={() => onNavigate?.(item.id)}
    >
      <NavigationGlyph icon={item.icon} /><span>{item.label}</span>
    </button>
  ) : (
    <span key={item.id} className="cp-nav-item cp-nav-item--unavailable" aria-disabled="true">
      <NavigationGlyph icon={item.icon} /><span>{item.label}</span><small>{item.id==='runs'?'Coming soon':'Unavailable'}</small>
    </span>
  ));
}

export function ControlPlaneSidebar({ activePage, navigation, workspaces = [], selectedWorkspaceId, workspaceRegistryStatus = 'ready', workspaceRegistryError, onNavigate, onRetryWorkspaces, onCreateWorkspace, onSelectWorkspace, onDiagnoseWorkspace, interactionLocked }: ControlPlaneSidebarProps) {
  const workspaceContextDescriptionId = useId();
  const workspaceChildrenId = useId();
  const [workspacesExpanded, setWorkspacesExpanded] = useState(true);
  const primary = navigation.filter((item) => item.section === 'primary');
  const workspaceIndex = primary.findIndex((item) => item.id === 'workspace');
  const workspaceNavigation = workspaceIndex >= 0 ? primary[workspaceIndex] : null;
  const beforeWorkspace = workspaceIndex >= 0 ? primary.slice(0, workspaceIndex) : primary;
  const afterWorkspace = workspaceIndex >= 0 ? primary.slice(workspaceIndex + 1) : [];
  return (
    <div className="cp-sidebar-inner">
      <div className="cp-brand"><img className="cp-brand-logo" src={logoLight} alt="FSQ" width="400" height="120" /></div>
      <nav className="cp-primary-nav" aria-label="Primary navigation">
        <NavGroup items={beforeWorkspace} activePage={activePage} onNavigate={onNavigate} interactionLocked={interactionLocked} />
        {workspaceNavigation && <div className="cp-workspaces" aria-label="Workspaces">
          {workspaceNavigation.available ? <button
            className="cp-nav-item cp-workspaces-trigger"
            type="button"
            disabled={interactionLocked}
            aria-expanded={workspacesExpanded}
            aria-controls={workspaceChildrenId}
            onClick={() => {
              setWorkspacesExpanded((expanded) => !expanded);
            }}
          >
            <NavigationGlyph icon={workspaceNavigation.icon} /><span>{workspaceNavigation.label}</span><ChevronDown className="cp-workspaces-chevron" aria-hidden="true" />
          </button> : <span className="cp-nav-item cp-nav-item--unavailable" aria-disabled="true"><NavigationGlyph icon={workspaceNavigation.icon} /><span>{workspaceNavigation.label}</span><small>Unavailable</small></span>}
          {workspaceNavigation.available && workspacesExpanded && <div id={workspaceChildrenId} className="cp-workspace-children">
            <button id="workspace-create-sidebar" className="cp-workspace cp-workspace--create" type="button" onClick={onCreateWorkspace} disabled={interactionLocked}><Plus aria-hidden="true" /><span>Create workspace</span></button>
            {workspaces.map((workspace) => workspace.available === false ? (
              <div key={workspace.id}><span className="cp-workspace cp-workspace--unavailable" aria-disabled="true" title={workspace.message}>
                <AlertTriangle aria-hidden="true" /><span><strong>{workspace.label}</strong>{workspace.description && <small>{workspace.description}</small>}</span>
              </span>{workspace.diagnosticAvailable && <button className="cp-workspace-retry" type="button" disabled={interactionLocked} aria-label={'Check macOS environment: '+workspace.label} onClick={()=>onDiagnoseWorkspace?.(workspace.id)}>Check macOS environment</button>}</div>
            ) : (
              <button key={workspace.id} className="cp-workspace" type="button" disabled={interactionLocked} aria-current={activePage === 'workspace' && workspace.id === selectedWorkspaceId ? 'page' : undefined} aria-describedby={workspace.id === selectedWorkspaceId ? workspaceContextDescriptionId : undefined} onClick={() => onSelectWorkspace?.(workspace.id)}>
                <span className="cp-project-glyph" aria-hidden="true">{workspaceInitials(workspace.label)}</span><span><strong>{workspace.label}</strong>{workspace.description && <small>{workspace.description}</small>}</span>{workspace.id === selectedWorkspaceId && <Check className="cp-workspace-selected-mark" aria-hidden="true"/>}
              </button>
            ))}
            {workspaceRegistryStatus === 'loading' && <span className="cp-workspaces-empty"><LoaderCircle aria-hidden="true" />Loading workspaces…</span>}
            {workspaceRegistryStatus === 'error' && <span className="cp-workspaces-empty cp-workspaces-error"><AlertTriangle aria-hidden="true" /><span>{workspaceRegistryError || 'Workspace registry unavailable.'}</span><button className="cp-workspace-retry" type="button" disabled={interactionLocked} aria-label="Retry workspace registry" onClick={onRetryWorkspaces}><RefreshCw aria-hidden="true" /></button></span>}
            {workspaceRegistryStatus === 'ready' && workspaces.length === 0 && <span className="cp-workspaces-empty"><CircleHelp aria-hidden="true" />No registered workspaces</span>}
          </div>}
        </div>}
        <NavGroup items={afterWorkspace} activePage={activePage} onNavigate={onNavigate} interactionLocked={interactionLocked} />
      </nav>
      <span id={workspaceContextDescriptionId} className="visually-hidden">Current Workspace</span>
      <nav className="cp-footer-nav" aria-label="Global settings">
        <NavGroup items={navigation.filter((item) => item.section === 'footer')} activePage={activePage} onNavigate={onNavigate} interactionLocked={interactionLocked} />
      </nav>
    </div>
  );
}
