import { RefreshCw } from 'lucide-react';
import type { PlatformId, PlatformOption, TargetsResponse, WorkspaceRegistryEntry } from '../../../api/types';

interface TargetToolbarProps {
  workspaces: readonly WorkspaceRegistryEntry[];
  workspaceName: string;
  platforms: readonly PlatformOption[];
  platform: PlatformId | '';
  targetId: string;
  targets: TargetsResponse | null;
  locked: boolean;
  loading: boolean;
  connectionLabel: string;
  onWorkspaceChange: (workspaceName: string) => void;
  onPlatformChange: (platform: PlatformId | '') => void;
  onTargetChange: (targetId: string) => void;
  onRefresh: () => void;
}

export function TargetToolbar({ workspaces, workspaceName, platforms, platform, targetId, targets, locked, loading, connectionLabel, onWorkspaceChange, onPlatformChange, onTargetChange, onRefresh }: TargetToolbarProps) {
  return <div className="target-toolbar">
    <label className="toolbar-select"><span>Workspace</span>
      <select aria-label="Workspace" value={workspaceName} disabled={locked} onChange={(event) => onWorkspaceChange(event.target.value)}>
        <option value="">Select a workspace</option>
        {workspaces.map((item) => <option key={item.name} value={item.name} disabled={item.status === 'unavailable' && !item.platforms.some(p=>p.platform==='macos'&&p.diagnosticAvailable)}>{item.name}{item.status==='unavailable'?' — diagnosis only':''}</option>)}
      </select>
    </label>
    <label className="toolbar-select"><span>Platform</span>
      <select aria-label="Platform" value={platform} disabled={locked || !workspaceName} onChange={(event) => onPlatformChange(event.target.value as PlatformId | '')}>
        <option value="">Select a platform</option>
        {platforms.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select>
    </label>
    <label className="toolbar-select toolbar-select--target"><span>{targets?.targetLabel ?? (platform === 'android' ? 'Device' : platform === 'web' ? 'Browser' : 'Application')}</span>
      <select aria-label={targets?.targetLabel ?? 'Target'} value={targetId} disabled={locked || loading || !workspaceName || !platform} onChange={(event) => onTargetChange(event.target.value)}>
        <option value="">{!platform ? 'Select a platform first' : loading ? 'Discovering…' : 'Select a target'}</option>
        {platform==='android' && targetId && !targets?.targets.some(target=>target.id===targetId) && <option value={targetId} disabled>Selected device unavailable — choose another</option>}
        {targets?.targets.map((target) => <option key={target.id} value={target.id} disabled={!target.selectable}>{target.label}{target.selectable ? '' : ` — ${target.status}`}</option>)}
      </select>
    </label>
    <span className={`connection-status connection-status--${connectionLabel.toLowerCase().replaceAll(' ', '-')}`} role="status"><span aria-hidden="true">●</span>{connectionLabel}</span>
    <button className="button button--secondary" type="button" disabled={locked || loading || !workspaceName || !platform} onClick={onRefresh} aria-label="Refresh readiness, targets, and cases"><RefreshCw aria-hidden="true"/>Refresh</button>
  </div>;
}
