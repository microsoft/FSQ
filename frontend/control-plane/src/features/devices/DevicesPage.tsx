import { useMemo, useRef, useState } from 'react';
import type { PlatformId, PlatformOption, WorkspaceRegistryEntry } from '../../api/types';
import { LiveEvidencePanel } from './components/LiveEvidencePanel';
import { OperationComposer } from './components/OperationComposer';
import { RunTimeline } from './components/RunTimeline';
import { TargetToolbar } from './components/TargetToolbar';
import { useDeviceWorkspace, type DevicesLaunchIntent } from './hooks/useDeviceWorkspace';

export type { DevicesLaunchIntent } from './hooks/useDeviceWorkspace';

interface DevicesPageProps {
  workspaces: readonly WorkspaceRegistryEntry[];
  workspaceRegistryReady: boolean;
  selectedWorkspaceName: string | null;
  onWorkspaceChange: (workspaceName: string | null) => void;
  launchIntent?: DevicesLaunchIntent | null;
  onLaunchIntentConsumed?: (intentId: number) => void;
  onRepairTarget?: (workspaceName:string, platform?:'macos'|'android') => void;
  onStartPendingChange?: (pending: boolean) => void;
  renderShell: (toolbar: React.ReactNode, content: React.ReactNode) => React.ReactNode;
}

const platformLabels: Record<PlatformId, string> = { android: 'Android', web: 'Web', windows: 'Windows', macos: 'macOS' };

export function DevicesPage({ workspaces, workspaceRegistryReady, selectedWorkspaceName, onWorkspaceChange, launchIntent, onLaunchIntentConsumed, onRepairTarget, onStartPendingChange, renderShell }: DevicesPageProps) {
  const selectedWorkspace = workspaces.find((item) => item.name === selectedWorkspaceName) ?? null;
  const platforms = useMemo<PlatformOption[]>(() => (selectedWorkspace?.platforms ?? [])
    .filter((item) => item.status === 'available' || item.platform==='macos' && item.diagnosticAvailable)
    .map((item) => ({ id: item.platform, label: platformLabels[item.platform] })), [selectedWorkspace]);
  const workspace = useDeviceWorkspace({
    workspaceName: selectedWorkspaceName,
    platforms,
    platformsReady: workspaceRegistryReady,
    onWorkspaceChange,
    launchIntent,
    onLaunchIntentConsumed,
    onStartPendingChange,
  });
  const primaryInputRef = useRef<HTMLTextAreaElement | HTMLSelectElement>(null);
  const [runActions, setRunActions] = useState<HTMLDivElement | null>(null);
  const resultHeadingRef = useRef<HTMLHeadingElement>(null);
  const terminal = workspace.snapshot?.terminal === true;
  const hasRun = Boolean(workspace.requestId);
  const pageError = workspace.bootstrap.error || workspace.readiness.error || workspace.targets.error || workspace.cases.error || workspace.streamError;

  const newRun = () => {
    void workspace.newRun().then(() => window.setTimeout(() => primaryInputRef.current?.focus(), 0));
  };

  const setupHint = !selectedWorkspaceName ? 'Select a Workspace to continue.' : !workspace.platform ? 'Select a platform to check its environment.' : undefined;
  const toolbar = <TargetToolbar
    workspaces={workspaces} workspaceName={selectedWorkspaceName ?? ''} platforms={platforms} platform={workspace.platform} targetId={workspace.targetId} targets={workspace.targets.data}
    locked={workspace.controlsLocked} loading={workspace.targets.state === 'loading'||workspace.readiness.state==='loading'} connectionLabel={setupHint ? 'Not checked' : workspace.connectionLabel}
    onWorkspaceChange={(name) => onWorkspaceChange(name || null)} onPlatformChange={workspace.setPlatform} onTargetChange={workspace.setTargetId} onRefresh={workspace.refresh}
  />;
  const content = <div className="devices-page">
    <div className="visually-hidden" aria-live="polite" aria-atomic="true">
      {workspace.startError ? workspace.startError.message : terminal ? `Run ${workspace.snapshot?.status}: ${workspace.snapshot?.summary}` : hasRun ? `Run ${workspace.snapshot?.status ?? 'preparing'}. ${workspace.snapshot?.summary ?? ''} ${workspace.connectionLabel}.` : (setupHint ?? `${workspace.connectionLabel}.`)}
    </div>
    {!hasRun && <><header className="devices-intro"><h1>What do you want to test?</h1><p>Choose a target and describe the outcome.</p></header>{toolbar}</>}
    {hasRun && <div className="run-context-bar"><div aria-label="Run context"><span className={'status-badge status-badge--' + (workspace.snapshot?.status ?? 'preparing')}>{workspace.snapshot?.status ?? 'preparing'}</span><strong>{workspace.snapshot?.workspaceName ?? selectedWorkspaceName}</strong><span>{workspace.snapshot?.platform ?? workspace.platform} · {workspace.selectedTarget?.label ?? workspace.targetId}</span><small>{workspace.snapshot?.mode === 'strict' ? 'Strict Replay' : 'Explore'}</small>{!terminal && <span className="run-connection" role="status">{workspace.connectionLabel}</span>}</div><div className="run-context-actions" ref={setRunActions}/></div>}
    {pageError && <div className="page-notice" role="alert">
      <strong>{pageError.message}</strong>
      <span>{pageError.action}</span>
    </div>}
    <div className={hasRun ? "devices-workbench" : "devices-preparation"}>
      <section className="operation-card" aria-label={hasRun ? undefined : "Test preparation"} aria-labelledby={hasRun ? "operation-title" : undefined}>
        {hasRun && <header className="card-header"><h2 id="operation-title">{workspace.snapshot?.mode === 'strict' ? 'Case steps' : 'Activity'}</h2></header>}
        <div className={`operation-body${hasRun ? ' operation-body--run' : ''}`}>
          {hasRun ? <RunTimeline actionContainer={runActions} snapshot={workspace.snapshot} connection={workspace.connection} selectedStepId={workspace.selectedStepId} resultHeadingRef={resultHeadingRef} onSelectStep={workspace.setSelectedStepId} onCancel={() => void workspace.cancel()} onSaveYaml={(caseName) => void workspace.saveYaml(caseName)} onNewRun={newRun} saveYamlState={workspace.saveYamlState} /> : <OperationComposer
            setupHint={setupHint}
            mode={workspace.mode} goal={workspace.goal} casePath={workspace.casePath} cases={workspace.cases.data?.cases ?? []} casesState={workspace.cases.state}
            readiness={workspace.readiness.data} discoveryLoading={workspace.readiness.state === 'loading' || workspace.targets.state === 'loading' || (workspace.platform !== 'macos' && workspace.platform !== 'android' && workspace.cases.state === 'loading')}
            canStart={workspace.canStart} errorMessage={workspace.startError?.message} errorAction={workspace.startError?.action} primaryInputRef={primaryInputRef}
            macos={workspace.platform==='macos'} android={workspace.platform==='android'} starting={workspace.starting} blockedReason={workspace.blockedReason} onRecheck={workspace.refresh}
            onRepair={(workspace.platform==='macos'||workspace.platform==='android') && selectedWorkspaceName && selectedWorkspace?.platforms.some(item=>item.platform===workspace.platform&&(item.status==='available'||item.repairAvailable)) && onRepairTarget?()=>onRepairTarget(selectedWorkspaceName,workspace.platform as 'macos'|'android'):undefined}
            onModeChange={workspace.setMode} onGoalChange={workspace.setGoal} onCaseChange={workspace.setCasePath} onStart={() => void workspace.start()}
          />}
        </div>
      </section>
      {hasRun && <LiveEvidencePanel tab={workspace.evidenceTab} snapshot={workspace.snapshot} selectedStepId={workspace.selectedStepId} platform={workspace.platform || null} targetLabel={workspace.selectedTarget?.label ?? workspace.targetId} onTabChange={workspace.setEvidenceTab} onClearStep={() => workspace.setSelectedStepId(null)} />}
    </div>
  </div>;
  return <>{renderShell(null, content)}</>;
}
