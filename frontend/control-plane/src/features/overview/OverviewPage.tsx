import { useState } from 'react';
import { ArrowRight, Bot, Check, ChevronsUpDown, CircleDot, FileCode, FolderOpen, Plus, RefreshCw, Settings } from 'lucide-react';
import type { WorkspaceRegistryEntry } from '../../api/types';
import type { ControlPlanePageId } from '../../app/shell/navigation';
import './overview.css';

export type OverviewProviderState =
  | { status: 'loading' }
  | { status: 'unconfigured' }
  | { status: 'configured'; provider: 'Azure OpenAI' | 'GitHub Copilot'; modelName: string; authenticated?: true }
  | { status: 'error'; error: { message: string; action: string } };
interface OverviewPageProps {
  workspaces: readonly WorkspaceRegistryEntry[];
  selectedWorkspace: WorkspaceRegistryEntry | null;
  registryStatus: 'loading' | 'ready' | 'error';
  registryError?: string;
  provider: OverviewProviderState;
  onNavigate: (page: ControlPlanePageId) => void;
  onCreateWorkspace: () => void;
  onSelectWorkspace: (name: string) => void;
  onClearWorkspace: () => void;
  onOpenWorkspace: (name: string) => void;
  onConfigureWorkspace: (name: string) => void;
  onRecordCase: (name: string) => void;
  onRetryWorkspaces: () => void;
  onRetryProvider: () => void;
}

export function OverviewPage(props: OverviewPageProps) {
  const [choosing, setChoosing] = useState(false);
  const current = props.registryStatus === 'ready' && props.selectedWorkspace?.status !== 'unavailable' ? props.selectedWorkspace : null;
  const choices = props.registryStatus === 'ready' ? props.workspaces.filter(item => item.status !== 'unavailable') : [];
  const canRecord = Boolean(current?.platforms.some(platform => platform.status === 'available'));
  const showChooser = !current || choosing;
  const provider = props.provider;
  return <div className="cp-overview"><div className="cp-overview-content">
    <header className="cp-overview-intro"><p className="cp-kicker">Home</p><h1>{current ? 'Start testing in ' + current.name : 'Choose where to test'}</h1><p>{current ? 'Record a new Case, or open an existing one to replay.' : 'Select a registered Workspace, or create one for your tests.'}</p></header>
    {current && <section className="cp-home-context" aria-label="Current Workspace"><div className="cp-home-identity"><FolderOpen aria-hidden="true"/><div><small>Current Workspace</small><strong>{current.name}</strong><div className="cp-platform-summary" aria-label="Configured platforms">{current.platforms.map(item=><span key={item.platform}><strong>{item.platform}</strong><small>{item.status}</small></span>)}</div></div></div><div className="cp-home-actions-inline"><button id="overview-change-workspace" className="button button--compact" type="button" aria-expanded={showChooser} aria-controls="overview-workspace-chooser" onClick={()=>setChoosing(!choosing)}><ChevronsUpDown aria-hidden="true"/>Change Workspace</button><button className="button button--compact button--quiet" type="button" onClick={()=>props.onConfigureWorkspace(current.name)}><Settings aria-hidden="true"/>Configure Workspace</button></div></section>}
    {showChooser && <section id="overview-workspace-chooser" className="cp-home-chooser" aria-label="Choose a Workspace"><header><h2>Workspaces</h2><button id="overview-create-workspace" className="button button--compact" type="button" onClick={props.onCreateWorkspace}><Plus aria-hidden="true"/>Create workspace</button></header>
      {props.registryStatus==='loading' && <p role="status">Loading registered Workspaces…</p>}
      {props.registryStatus==='error' && <div className="cp-inline-error" role="alert"><div><strong>Workspace registry unavailable</strong><p>{props.registryError}</p></div><button className="button" type="button" onClick={props.onRetryWorkspaces}><RefreshCw aria-hidden="true"/>Retry Workspaces</button></div>}
      {props.registryStatus==='ready' && (choices.length ? <div className="cp-workspace-choices">{choices.map(item=><button type="button" key={item.name} aria-pressed={item.name===current?.name} onClick={()=>{props.onSelectWorkspace(item.name);setChoosing(false);requestAnimationFrame(()=>document.getElementById('overview-change-workspace')?.focus());}}><FolderOpen aria-hidden="true"/><span><strong>{item.name}</strong><small>{item.platforms.map(p=>p.platform+(p.status==='available'?'':' · '+p.status)).join(', ') || 'No platforms configured'}</small></span></button>)}</div> : <p>No available Workspaces. Create a Workspace or repair its configuration.</p>)}
      {current && <button className="button button--quiet button--compact" type="button" onClick={()=>{setChoosing(false);props.onClearWorkspace();}}>Clear selection</button>}
    </section>}
    {current && <div className="cp-home-tasks" aria-label="Start a test"><section className="cp-home-task"><div className="cp-home-task-kind"><CircleDot aria-hidden="true"/><span>From a goal</span></div><h2>Record a new Case</h2><p>Describe what should work. FSQ explores the target and captures evidence as it goes.</p><div className="cp-home-task-input"><small>You provide</small><span>A goal, such as “Find a product and verify its details.”</span></div><footer><button className="button button--primary" type="button" disabled={!canRecord} aria-describedby={!canRecord?'overview-record-blocked':undefined} onClick={()=>props.onRecordCase(current.name)}>Record new case<ArrowRight aria-hidden="true"/></button>{canRecord ? <small>Choose the platform in Test Runner.</small> : <small id="overview-record-blocked">Initialize or repair a platform in Workspace before recording.</small>}</footer></section><section className="cp-home-task"><div className="cp-home-task-kind cp-home-task-kind--neutral"><FileCode aria-hidden="true"/><span>From an existing Case</span></div><h2>Browse and replay</h2><p>Open the Workspace files, inspect a Case, then replay its authored steps.</p><div className="cp-home-task-input"><small>You provide</small><span>An existing .fsq.yaml file from this Workspace.</span></div><footer><button className="button" type="button" onClick={()=>props.onOpenWorkspace(current.name)}>Browse Cases<ArrowRight aria-hidden="true"/></button><small>Selecting a file does not start it.</small></footer></section></div>}
    <section className="cp-provider-strip" aria-label="Global AI configuration"><Bot aria-hidden="true"/><div className="cp-provider-copy"><h2>Global AI Provider</h2><p>Loaded from <code>~/.fsq</code> and shared by every Workspace.</p>{provider.status==='configured' && <p><strong>{provider.provider}</strong> · {provider.modelName}{provider.authenticated && ' · Authenticated'}</p>}{provider.status==='loading' && <p role="status">Loading Provider configuration…</p>}{provider.status==='unconfigured' && <p>Configure a Provider for Explore and AI assertions.</p>}{provider.status==='error' && <div className="cp-inline-error" role="alert"><div><strong>{provider.error.message}</strong><p>{provider.error.action}</p></div><button className="button button--compact" type="button" onClick={props.onRetryProvider}><RefreshCw aria-hidden="true"/>Retry Provider</button></div>}</div>{provider.status==='configured' && <span className="cp-status-pill cp-status-pill--success"><Check aria-hidden="true"/>Configured</span>}<button className="button button--compact button--quiet" type="button" onClick={()=>props.onNavigate('config')}>Manage Provider<ArrowRight aria-hidden="true"/></button></section>
  </div></div>;
}
