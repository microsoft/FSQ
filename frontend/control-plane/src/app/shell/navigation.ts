export type ControlPlanePageId = 'overview' | 'workspace' | 'devices' | 'runs' | 'config' | 'settings';
export type NavigationIcon = 'overview' | 'workspace' | 'devices' | 'runs' | 'config' | 'settings';

export interface NavigationItem {
  id: ControlPlanePageId;
  label: string;
  icon: NavigationIcon;
  available: boolean;
  section: 'primary' | 'footer';
}

export const CONTROL_PLANE_NAVIGATION: readonly NavigationItem[] = [
  { id: 'overview', label: 'Home', icon: 'overview', available: true, section: 'primary' },
  { id: 'devices', label: 'Test Runner', icon: 'devices', available: true, section: 'primary' },
  { id: 'runs', label: 'Runs', icon: 'runs', available: false, section: 'primary' },
  { id: 'workspace', label: 'Workspaces', icon: 'workspace', available: true, section: 'primary' },
  { id: 'config', label: 'Settings', icon: 'config', available: true, section: 'footer' },
] as const;

export interface WorkspaceNavigationItem {
  id: string;
  label: string;
  description?: string;
  available?: boolean;
  message?: string;
  diagnosticAvailable?: boolean;
}
