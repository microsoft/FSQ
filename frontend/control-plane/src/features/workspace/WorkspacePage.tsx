import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, ArrowLeft, Edit3, FolderKanban, CircleDot, Plus, RefreshCw, ShieldCheck } from 'lucide-react';
import { controlPlaneClient, toApiError } from '../../api/controlPlaneClient';
import type {
  ApiErrorBody,
  PlatformId,
  WorkspaceDetail,
  WorkspacePlatformDetail,
  WorkspacePlatformSummary,
  WorkspaceRegistryEntry,
  WorkspaceTarget,
} from '../../api/types';
import { WorkspaceBrowser } from './WorkspaceBrowser';
import { WorkspaceForm } from './WorkspaceForm';
import './workspace.css';
import { ContentTabs } from '../../shared/ContentTabs';

interface WorkspacePageProps {
  repairContext?: boolean;
  selectedName: string | null;
  createRequested: boolean;
  configurationOpen: boolean;
  registryError?: ApiErrorBody | null;
  onRetryRegistry: () => void;
  onRequestCreate: () => void;
  onCancelCreate: () => void;
  onConfigurationOpenChange: (open: boolean) => void;
  onPresentationChange?: (presentation: 'default' | 'full-bleed') => void;
  onReplayCase?: (platform: PlatformId, casePath: string) => void;
  onCreated: (detail: WorkspaceDetail) => void;
  onRegistryChanged: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  onPendingChange?: (pending: boolean) => void;
}

const allPlatforms: PlatformId[] = ['android', 'web', 'windows', 'macos'];
const platformLabels: Record<PlatformId, string> = { android: 'Android', web: 'Web', windows: 'Windows', macos: 'macOS' };

export function WorkspaceTitlebar({ workspace, onRecordCase, recordDisabled = false, recordDisabledReason = "Select an available platform configuration before recording." }: { workspace: WorkspaceRegistryEntry; onRecordCase?: () => void; recordDisabled?: boolean; recordDisabledReason?: string }) {
  return <div className="cp-workspace-titlebar">
    <div className="cp-workspace-identity"><h1 id="workspace-heading" tabIndex={-1}>{workspace.name}</h1><p className="mono">{workspace.rootPath}</p></div>
    <div className="cp-workspace-platforms" aria-label="Workspace platforms">{workspace.platforms.length ? workspace.platforms.map((platform) => <span key={platform.platform} className={`cp-workspace-platform cp-workspace-platform--${platform.status}`} aria-label={`${platformLabels[platform.platform]} ${platform.status}`}><strong>{platformLabels[platform.platform]}</strong><span className="cp-workspace-platform-status" title={platform.status === 'available' ? 'Available' : 'Unavailable'} aria-hidden="true">{platform.status === 'available' ? <ShieldCheck /> : <><AlertCircle /><span>Unavailable</span></>}</span></span>) : <span>No configured platforms</span>}</div>
    <button className="button button--compact cp-workspace-record" type="button" disabled={recordDisabled} aria-describedby={recordDisabled?"workspace-record-blocked":undefined} title="Record new case" onClick={onRecordCase}><CircleDot aria-hidden="true"/><span>Record new case</span></button>{recordDisabled && <small id="workspace-record-blocked">{recordDisabledReason}</small>}
  </div>;
}

function targetRows(target?: WorkspaceTarget): [string, string][] {
  if (!target) return [];
  if ('appId' in target) return [['App ID', target.appId]];
  if ('browserChannel' in target) return [['Browser channel', target.browserChannel], ['Web path', target.browserExecutablePath || 'Discovered automatically']];
  if ('windowTitleRe' in target || 'launchArgs' in target) return [
    ['App path', target.appPath],
    ['Window title regex', target.windowTitleRe || 'Not configured'],
    ['Launch args', target.launchArgs || 'None'],
  ];
  return [['Bundle ID', target.bundleId || 'Not configured'], ['App path', target.appPath || 'Not configured']];
}

function platformRevision(detail: WorkspaceDetail): string {
  return detail.platforms.map((platform) => `${platform.platform}:${platform.revision ?? platform.status}`).join('|');
}

export function WorkspacePage({ selectedName, createRequested, configurationOpen, repairContext, registryError, onRetryRegistry, onRequestCreate, onCancelCreate, onConfigurationOpenChange, onPresentationChange, onReplayCase, onCreated, onRegistryChanged, onDirtyChange, onPendingChange }: WorkspacePageProps) {
  const dirty = useRef(false);
  const [formPending,setFormPending] = useState(false);
  const callbacks = useRef({onDirtyChange,onPendingChange});
  callbacks.current = {onDirtyChange,onPendingChange};
  const reportDirty = useCallback((value:boolean)=>{dirty.current=value;callbacks.current.onDirtyChange?.(value)},[]);
  const reportPending = useCallback((value:boolean)=>{setFormPending(value);callbacks.current.onPendingChange?.(value)},[]);
  const previousSelectedName = useRef(selectedName);
  const [detail, setDetail] = useState<WorkspaceDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiErrorBody | null>(null);
  const [selectedPlatform, setSelectedPlatform] = useState<PlatformId | null>(null);
  const [formMode, setFormMode] = useState<'add' | 'edit' | null>(null);
  const [platformDetail, setPlatformDetail] = useState<WorkspacePlatformDetail | null>(null);
  const [platformLoading, setPlatformLoading] = useState(false);
  const [platformError, setPlatformError] = useState<ApiErrorBody | null>(null);
  const platformDetailController = useRef<AbortController | null>(null);
  const loadDetail = () => {
    if (!selectedName) return undefined;
    setLoading(true);
    setError(null);
    const controller = new AbortController();
    void controlPlaneClient.workspace(selectedName, controller.signal).then((response) => {
      if (controller.signal.aborted) return;
      setDetail(response);
      setSelectedPlatform((current) => repairContext?'macos':response.platforms.some((item) => item.platform === current) ? current : (response.platforms[0]?.platform ?? null));
    }).catch((reason) => {
      if (!controller.signal.aborted) setError(toApiError(reason));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return controller;
  };

  const loadPlatformDetail = (platform: PlatformId) => {
    if (!selectedName) return;
    platformDetailController.current?.abort();
    const controller = new AbortController();
    platformDetailController.current = controller;
    setPlatformLoading(true);
    setPlatformError(null);
    void controlPlaneClient.workspacePlatform(selectedName, platform, controller.signal).then((response) => {
      if (controller.signal.aborted || platformDetailController.current !== controller) return;
      setPlatformDetail(response);
      setFormMode('edit');
    }).catch((reason) => {
      if (!controller.signal.aborted && platformDetailController.current === controller) setPlatformError(toApiError(reason));
    }).finally(() => {
      if (!controller.signal.aborted && platformDetailController.current === controller) setPlatformLoading(false);
    });
  };

  useEffect(() => {
    const selectionChanged = previousSelectedName.current !== selectedName;
    previousSelectedName.current = selectedName;
    platformDetailController.current?.abort();
    platformDetailController.current = null;
    setDetail(null);
    if (selectionChanged && !repairContext) onConfigurationOpenChange(false);
    setSelectedPlatform(null);
    setFormMode(null);
    setPlatformDetail(null);
    setError(null);
    setPlatformError(null);
    reportDirty(false);
    const controller = createRequested ? undefined : loadDetail();
    return () => {
      controller?.abort();
      platformDetailController.current?.abort();
    };
  }, [selectedName, createRequested]);

  useEffect(() => () => platformDetailController.current?.abort(), []);

  const workspaceVisible = Boolean(detail && !loading && !error && !createRequested && !repairContext);
  useEffect(() => {
    onPresentationChange?.(workspaceVisible ? 'full-bleed' : 'default');
  }, [workspaceVisible, onPresentationChange]);

  const closeForm = () => {
    platformDetailController.current?.abort();
    platformDetailController.current = null;
    setFormMode(null);
    setPlatformDetail(null);
    setPlatformError(null);
    reportDirty(false);
  };
  const acceptPlatformSave = (workspace: WorkspaceDetail, platform?: WorkspacePlatformDetail) => {
    setDetail(workspace);
    setSelectedPlatform(platform?.platform ?? selectedPlatform);
    closeForm();
    onRegistryChanged();
  };

  if (createRequested) return <div className="cp-workspace-page cp-workspace-page--form"><WorkspaceForm mode="create" onCancel={onCancelCreate} onSaved={(created) => { onRegistryChanged(); onCreated(created); }} onDirtyChange={reportDirty} onPendingChange={reportPending} /></div>;

  if (!selectedName) return <div className="cp-workspace-page"><section className="cp-workspace-zero"><span><FolderKanban aria-hidden="true" /></span><h1>Choose a workspace</h1><p>Select a registered workspace from the navigation or create a local workspace with independent platform targets, cases, knowledge, and runs.</p>{registryError && <div className="cp-inline-error"><AlertCircle aria-hidden="true" /><span><strong>{registryError.message}</strong><small>{registryError.action}</small></span><button className="cp-icon-button" type="button" aria-label="Retry workspace registry" onClick={onRetryRegistry}><RefreshCw aria-hidden="true" /></button></div>}<button id="workspace-create-empty" className="button button--primary" type="button" onClick={onRequestCreate}><Plus aria-hidden="true" />Create workspace</button></section></div>;

  if (loading) return <div className="cp-workspace-page"><p className="cp-workspace-loading">Loading workspace summary…</p></div>;
  if (error) return <div className="cp-workspace-page"><section className="cp-workspace-zero"><span className="cp-workspace-zero--error"><AlertCircle aria-hidden="true" /></span><h1>Workspace unavailable</h1><p>{error.message}</p><small>{error.action}</small><button className="button" type="button" onClick={loadDetail}><RefreshCw aria-hidden="true" />Retry</button></section></div>;
  if (!detail) return null;

  const selectedSummary: WorkspacePlatformSummary | null = detail.platforms.find((item) => item.platform === selectedPlatform) ?? null;
  const visiblePlatforms = repairContext ? detail.platforms.filter(item=>item.platform==='macos') : detail.platforms;
  const absentPlatforms = allPlatforms.filter((platform) => !detail.platforms.some((item) => item.platform === platform));

  const changePresentation = (value: 'files' | 'configuration') => {
    if (formPending) return;
    if (dirty.current && !window.confirm('Discard unsaved workspace changes?')) return;
    closeForm();
    setPlatformLoading(false);
    onConfigurationOpenChange(value === 'configuration');
  };
  const presentation = configurationOpen || repairContext ? 'configuration' : 'files';
  const present = (content: React.ReactNode) => <div className="cp-workspace-view">
    {!repairContext && <ContentTabs label="Workspace views" className="cp-workspace-view-tabs" value={presentation} onChange={changePresentation} items={[
      {id:'files',label:'Files',disabled:formPending,tabId:'workspace-files-tab',panelId:'workspace-presentation-panel'},
      {id:'configuration',label:'Configuration',disabled:formPending,tabId:'workspace-configuration-tab',panelId:'workspace-presentation-panel'},
    ]}/>}
    <div id="workspace-presentation-panel" className="cp-workspace-presentation" role={!repairContext?'tabpanel':undefined} aria-labelledby={!repairContext?'workspace-'+presentation+'-tab':undefined}>{content}</div>
  </div>;

  if (formMode === 'edit' && platformDetail) return present(<div className="cp-workspace-page cp-workspace-page--form"><WorkspaceForm
    key={platformDetail.revision} mode="edit" detail={platformDetail} onCancel={closeForm} onSaved={acceptPlatformSave}
    onReloadLatest={() => selectedPlatform && loadPlatformDetail(selectedPlatform)} onDirtyChange={reportDirty} onPendingChange={reportPending}
  /></div>);
  if (formMode === 'add') return present(<div className="cp-workspace-page cp-workspace-page--form"><WorkspaceForm
    mode="add" workspace={detail} allowedPlatforms={absentPlatforms} onCancel={closeForm} onSaved={acceptPlatformSave} onDirtyChange={reportDirty} onPendingChange={reportPending}
  /></div>);

  if (configurationOpen || repairContext) {
    const selectPlatformTab = (platform: PlatformId) => {
      platformDetailController.current?.abort();
      platformDetailController.current = null;
      setPlatformLoading(false);
      setPlatformDetail(null);
      setFormMode(null);
      setSelectedPlatform(platform);
      setPlatformError(null);
    };
    return present(<div className="cp-workspace-page"><section className="cp-workspace-configuration" aria-label="Workspace configuration">
    {repairContext && <button className="button cp-back-button" type="button" onClick={() => { platformDetailController.current?.abort(); onConfigurationOpenChange(false); }}><ArrowLeft aria-hidden="true"/>Back to diagnostics</button>}
    <header><h2>Platform configuration</h2>{!repairContext && absentPlatforms.length > 0 && <button className="button button--compact" type="button" onClick={() => setFormMode('add')}><Plus aria-hidden="true"/>Add platform</button>}</header>
    <ContentTabs label="Configured platforms" className="cp-platform-tabs" value={selectedPlatform ?? ''} onChange={value=>selectPlatformTab(value as PlatformId)} items={visiblePlatforms.map(platform=>({id:platform.platform,tabId:'workspace-platform-tab-'+platform.platform,panelId:'workspace-platform-panel',label:<>{platformLabels[platform.platform]}<span className={'cp-platform-status cp-platform-status--'+platform.status}>{platform.status}</span></>}))}/>
    <div id="workspace-platform-panel" role="tabpanel" aria-labelledby={selectedPlatform ? `workspace-platform-tab-${selectedPlatform}` : undefined}>{!selectedSummary ? <div className="cp-pane-state">No platform configuration is available.</div> : selectedSummary.status === 'unavailable' ? <div className="cp-platform-unavailable" role="status"><AlertCircle aria-hidden="true" /><div><strong>{platformLabels[selectedSummary.platform]} configuration unavailable</strong><p>{selectedSummary.message}</p><small>{selectedSummary.action}</small>{selectedSummary.platform==='macos' && selectedSummary.repairAvailable && <button className="button" type="button" disabled={platformLoading} onClick={()=>loadPlatformDetail('macos')}>Edit target configuration</button>}{platformError && <p role="alert">{platformError.message}</p>}</div></div> : <div className="cp-platform-summary">
      <div className="cp-platform-summary-heading"><div><h3>{platformLabels[selectedSummary.platform]} target</h3></div><button className="button" type="button" disabled={platformLoading} onClick={() => loadPlatformDetail(selectedSummary.platform)}><Edit3 aria-hidden="true" />{platformLoading ? 'Loading…' : 'Edit'}</button></div>
      <details className="cp-configuration-details"><summary>Configuration details</summary><p>Revision <code>{selectedSummary.revision}</code></p></details>
      {platformError && <div className="cp-inline-error" role="alert"><AlertCircle aria-hidden="true" /><span><strong>{platformError.message}</strong><small>{platformError.action}</small></span></div>}
      <div className="cp-workspace-facts"><dl>{targetRows(selectedSummary.target).map(([label, value]) => <div key={label}><dt>{label}</dt><dd className={label.toLowerCase().includes('path') ? 'mono' : undefined}>{value}</dd></div>)}</dl><div className="cp-secret-summary"><ShieldCheck aria-hidden="true" /><div><strong>Runtime environment</strong>{selectedSummary.env?.length ? <ul>{selectedSummary.env.map((item) => <li key={item.name}><code>{item.name}</code><span>Configured</span></li>)}</ul> : <p>No private environment values configured.</p>}</div></div></div>
    </div>}</div>
  </section></div>);
  }

  return present(<div className="cp-workspace-page cp-workspace-page--browser"><WorkspaceBrowser key={platformRevision(detail)} workspaceName={detail.name} onReplayCase={onReplayCase}/></div>);
}
