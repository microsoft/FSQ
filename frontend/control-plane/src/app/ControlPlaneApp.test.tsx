import { useEffect } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { controlPlaneClient } from '../api/controlPlaneClient';
import { ControlPlaneApp } from './ControlPlaneApp';

vi.mock('../api/controlPlaneClient', () => ({
  controlPlaneClient: { workspaces: vi.fn().mockResolvedValue({ workspaces: [] }), config: vi.fn().mockResolvedValue({ configured: false, provider: null }) },
  toApiError: vi.fn((error) => error),
}));

vi.mock('../features/devices/DevicesPage', () => ({
  DevicesPage: ({ workspaceRegistryReady, selectedWorkspaceName, launchIntent, onLaunchIntentConsumed, onStartPendingChange, onRepairTarget, renderShell }: { workspaceRegistryReady: boolean; selectedWorkspaceName: string | null; launchIntent?: { id: number; mode: string; workspaceName: string; platform?: string; casePath?: string } | null; onLaunchIntentConsumed?: (id: number) => void; onStartPendingChange?: (pending:boolean)=>void; onRepairTarget?: (name:string)=>void; renderShell: (toolbar: React.ReactNode, content: React.ReactNode) => React.ReactNode }) =>
    renderShell(null, <div>Devices content<button onClick={()=>onStartPendingChange?.(true)}>Simulate pending start</button><button onClick={()=>onStartPendingChange?.(false)}>Finish pending start</button><button onClick={()=>selectedWorkspaceName&&onRepairTarget?.(selectedWorkspaceName)}>Repair selected target</button><span>Registry {workspaceRegistryReady ? 'ready' : 'pending'}</span><span>Devices Workspace {selectedWorkspaceName ?? 'unselected'}</span><span>{launchIntent ? `${launchIntent.mode}:${launchIntent.workspaceName}:${launchIntent.platform ?? ''}:${launchIntent.casePath ?? ''}` : 'No launch intent'}</span>{launchIntent && <button type="button" onClick={() => onLaunchIntentConsumed?.(launchIntent.id)}>Consume launch intent</button>}</div>),
}));
vi.mock('../features/config/ConfigPage', () => ({
  ConfigPage: ({ onDirtyChange, onSavePendingChange, onSaveUncertainChange }: { onDirtyChange?: (dirty: boolean) => void; onSavePendingChange?: (pending: boolean) => void; onSaveUncertainChange?: (uncertain: boolean) => void }) => <div>Config content<button type="button" onClick={() => onDirtyChange?.(true)}>Make draft dirty</button><button onClick={() => onSavePendingChange?.(true)}>Start provider save</button><button onClick={() => { onSavePendingChange?.(false); onSaveUncertainChange?.(true); }}>Lose provider save response</button></div>,
}));
vi.mock('../features/overview/OverviewPage', () => ({
  OverviewPage: ({ workspaces, selectedWorkspace, provider, onNavigate, onSelectWorkspace, onClearWorkspace, onOpenWorkspace, onConfigureWorkspace, onRetryWorkspaces, onRetryProvider, onCreateWorkspace }: { workspaces: { name: string; status: string }[]; selectedWorkspace: { name: string; status: string } | null; provider: { status: string; provider?: string; modelName?: string }; onNavigate: (page: 'devices') => void; onSelectWorkspace: (name: string) => void; onClearWorkspace: () => void; onOpenWorkspace: (name: string) => void; onConfigureWorkspace: (name: string) => void; onRetryWorkspaces: () => void; onRetryProvider: () => void; onCreateWorkspace: () => void }) => <div>Overview content<button id="overview-create-workspace" onClick={onCreateWorkspace}>Overview create</button><button id="overview-step-create-workspace" onClick={onCreateWorkspace}>Overview step create</button><span>{selectedWorkspace ? `Overview ${selectedWorkspace.name}` : 'Overview unselected'}</span><span data-testid="overview-provider-projection">{JSON.stringify(provider)}</span><button type="button" onClick={() => onNavigate('devices')}>Start dynamic</button><button type="button" onClick={onRetryWorkspaces}>Overview retry</button><button type="button" onClick={onRetryProvider}>Provider retry</button>{selectedWorkspace && <><button type="button" onClick={onClearWorkspace}>Overview clear</button><button type="button" onClick={() => onConfigureWorkspace(selectedWorkspace.name)}>Overview configure</button></>}{workspaces[0]?.status !== 'unavailable' && <><button type="button" onClick={() => onSelectWorkspace(workspaces[0].name)}>Overview select</button><button type="button" onClick={() => onOpenWorkspace(workspaces[0].name)}>Overview open</button></>}</div>,
}));
vi.mock('../features/workspace/WorkspacePage', () => ({
  WorkspaceTitlebar: ({ workspace, onRecordCase }: { workspace: { name: string }; onRecordCase: () => void }) => <div><h1 id="workspace-heading" tabIndex={-1}>{workspace.name}</h1><button type="button" onClick={onRecordCase}>Record case from browser</button></div>,
  WorkspacePage: ({ createRequested, configurationOpen, selectedName, onDirtyChange, onCancelCreate, onRequestCreate, onCreated, onRegistryChanged, onPresentationChange, onRecordCase, onReplayCase, onConfigurationOpenChange }: { createRequested: boolean; configurationOpen: boolean; selectedName: string | null; onDirtyChange?: (dirty: boolean) => void; onCancelCreate: () => void; onRequestCreate: () => void; onCreated: (detail: object) => void; onRegistryChanged: () => void; onPresentationChange?: (presentation: 'default' | 'full-bleed') => void; onConfigurationOpenChange: (open: boolean) => void; onRecordCase?: () => void; onReplayCase?: (platform: 'web', casePath: string) => void }) => {
    useEffect(() => {
      onPresentationChange?.(selectedName && !createRequested ? 'full-bleed' : 'default');
    }, [configurationOpen, createRequested, onPresentationChange, selectedName]);
    return <div>
      {!createRequested && !selectedName && <button id="workspace-create-empty" onClick={onRequestCreate}>Empty create</button>}
      {createRequested ? 'Create workspace content' : selectedName ? `Workspace ${selectedName}` : 'Workspace content'}
      {selectedName && !createRequested && <button onClick={()=>onConfigurationOpenChange(true)}>Configure workspace</button>}
      {configurationOpen && <span>Workspace configuration open</span>}
      <button type="button" onClick={() => onDirtyChange?.(true)}>Make workspace draft dirty</button>
      {createRequested && <button type="button" onClick={() => onCreated({ name: 'created', rootPath: 'C:\\projects\\created', status: 'available', message: 'Available.', platforms: [] })}>Complete creation</button>}
      {selectedName && !createRequested && <><button type="button" onClick={() => onReplayCase?.('web', 'flows/login.fsq.yaml')}>Replay case from browser</button></>}
      <button type="button" onClick={createRequested ? onCancelCreate : onRegistryChanged}>Cancel registry fixture</button>
    </div>;
  },
}));

beforeEach(() => {
  vi.mocked(controlPlaneClient.workspaces).mockResolvedValue({ workspaces: [] });
  vi.mocked(controlPlaneClient.config).mockResolvedValue({ configured: false, provider: null });
});
afterEach(() => vi.restoreAllMocks());

it('locks sidebar navigation and diagnostic context changes during pending Start',async()=>{
  vi.mocked(controlPlaneClient.workspaces).mockResolvedValue({workspaces:[{name:'repair',rootPath:'/local',status:'unavailable',message:'Missing',action:'Repair',platforms:[{platform:'macos',configPath:'/local/config',status:'unavailable',message:'Missing',action:'Repair',diagnosticAvailable:true,repairAvailable:true}]}]});
  render(<ControlPlaneApp/>);
  await userEvent.click(await screen.findByRole('button',{name:'Check macOS environment: repair'}));
  await userEvent.click(screen.getByRole('button',{name:'Simulate pending start'}));
  for(const name of ['Home','Workspaces','Settings','Create workspace','Check macOS environment: repair'])expect(screen.getByRole('button',{name})).toBeDisabled();
  await userEvent.click(screen.getByRole('button',{name:'Home'}));
  expect(screen.getByText('Devices content')).toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Finish pending start'}));
  expect(screen.getByRole('button',{name:'Home'})).toBeEnabled();
});

it('opens a repairable unavailable macOS workspace for diagnosis without a ready selection',async()=>{
  vi.mocked(controlPlaneClient.workspaces).mockResolvedValue({workspaces:[{name:'broken-mac',rootPath:'/local',status:'unavailable',message:'Missing app',action:'Repair',platforms:[{platform:'macos',status:'unavailable',configPath:'/local/config',message:'Missing app',action:'Repair',diagnosticAvailable:true,repairAvailable:true}]}]});
  render(<ControlPlaneApp/>);
  await userEvent.click(await screen.findByRole('button',{name:'Check macOS environment: broken-mac'}));
  expect(screen.getByText('Devices Workspace broken-mac')).toBeVisible();
  expect(screen.getByText('diagnostic:broken-mac:macos:')).toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Home'}));
  expect(screen.getByText('Overview unselected')).toBeVisible();
});

it('defaults to Overview and selects available pages through centralized navigation', async () => {
  const user = userEvent.setup();
  render(<ControlPlaneApp />);
  expect(screen.getByText('Overview content')).toBeVisible();

  await user.click(screen.getByRole('button', { name: 'Start dynamic' }));
  expect(screen.getByText('Devices content')).toBeVisible();

  await user.click(screen.getByRole('button', { name: 'Settings' }));
  expect(screen.getByText('Config content')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Settings' })).toHaveAttribute('aria-current', 'page');

  await user.click(screen.getByRole('button', { name: 'Test Runner' }));
  expect(screen.getByText('Devices content')).toBeVisible();

  await user.click(screen.getByRole('button', { name: 'Create workspace' }));
  expect(screen.getByText('Create workspace content')).toBeVisible();
});

it('keeps Overview active for context selection and opens Workspace only on request', async () => {
  vi.mocked(controlPlaneClient.workspaces).mockResolvedValue({ workspaces: [
    { name: 'web-app', rootPath: 'C:\\projects\\web-app', status: 'available', message: 'Available.', platforms: [] },
  ] });
  const user = userEvent.setup();
  render(<ControlPlaneApp />);
  await user.click(await screen.findByRole('button', { name: 'Overview select' }));
  expect(screen.getByText('Overview web-app')).toBeVisible();
  expect(screen.queryByText('Workspace web-app')).not.toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: 'Overview open' }));
  expect(screen.getByText('Workspace web-app')).toBeVisible();
});

it('opens the selected Workspace configuration directly from Overview', async () => {
  vi.mocked(controlPlaneClient.workspaces).mockResolvedValue({ workspaces: [
    { name: 'web-app', rootPath: 'C:\\projects\\web-app', status: 'available', message: 'Available.', platforms: [] },
  ] });
  const user = userEvent.setup();
  render(<ControlPlaneApp />);

  await user.click(await screen.findByRole('button', { name: 'Overview select' }));
  await user.click(screen.getByRole('button', { name: 'Overview configure' }));

  expect(screen.getByText('Workspace web-app')).toBeVisible();
  expect(screen.getByText('Workspace configuration open')).toBeVisible();
});

it('clears only the current Overview Workspace selection', async () => {
  vi.mocked(controlPlaneClient.workspaces).mockResolvedValue({ workspaces: [
    { name: 'web-app', rootPath: 'C:\\projects\\web-app', status: 'available', message: 'Available.', platforms: [] },
  ] });
  const user = userEvent.setup();
  render(<ControlPlaneApp />);

  await user.click(await screen.findByRole('button', { name: 'Overview select' }));
  await user.click(screen.getByRole('button', { name: 'Overview clear' }));

  expect(screen.getByText('Overview unselected')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Overview select' })).toBeVisible();
  expect(screen.queryByText('Workspace web-app')).not.toBeInTheDocument();
});

it('projects OpenAI into Home without passing its key', async () => {
  vi.mocked(controlPlaneClient.config).mockResolvedValue({ configured: true, provider: { type: 'openai', modelName: 'gpt-5', apiKey: 'must-not-enter-home' } });
  render(<ControlPlaneApp />);
  const projection = await screen.findByTestId('overview-provider-projection');
  expect(JSON.parse(projection.textContent ?? '{}')).toEqual({ status: 'configured', provider: 'OpenAI', modelName: 'gpt-5' });
});

it('blocks navigation while saving a Provider and warns when its result is unknown', async () => {
  const user = userEvent.setup();
  const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
  render(<ControlPlaneApp />);
  await user.click(screen.getByRole('button', { name: 'Settings' }));
  await user.click(screen.getByRole('button', { name: 'Start provider save' }));
  for (const name of ['Home', 'Test Runner', 'Create workspace']) expect(screen.getByRole('button', { name })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Home' }));
  expect(screen.getByText('Config content')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Lose provider save response' }));
  await user.click(screen.getByRole('button', { name: 'Home' }));
  expect(screen.getByText('Config content')).toBeVisible();
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('does not cancel'));
  await user.click(screen.getByRole('button', { name: 'Home' }));
  expect(screen.getByText('Overview content')).toBeVisible();
});

it('projects Azure configuration into a secret-free Overview summary', async () => {
  vi.mocked(controlPlaneClient.config).mockResolvedValue({
    configured: true,
    provider: {
      type: 'azure_openai',
      modelName: 'gpt-5.6',
      baseUrl: 'https://private-resource.example.invalid',
      apiKey: 'overview-must-never-receive-this-secret',
    },
  });

  render(<ControlPlaneApp />);

  const projection = await screen.findByTestId('overview-provider-projection');
  expect(projection).toHaveTextContent('Azure OpenAI');
  expect(projection).toHaveTextContent('gpt-5.6');
  expect(projection).not.toHaveTextContent('private-resource');
  expect(projection).not.toHaveTextContent('overview-must-never-receive-this-secret');
});

it('drops Provider error details before projecting the failure into Overview', async () => {
  vi.mocked(controlPlaneClient.config).mockRejectedValue({
    code: 'config_unavailable',
    message: 'Provider configuration unavailable.',
    action: 'Open Config and retry.',
    details: { apiKey: 'must-not-enter-overview', endpoint: 'https://private.example.invalid' },
  });

  render(<ControlPlaneApp />);

  const projection = await screen.findByTestId('overview-provider-projection');
  expect(projection).toHaveTextContent('Provider configuration unavailable.');
  expect(projection).toHaveTextContent('Open Config and retry.');
  expect(projection).not.toHaveTextContent('must-not-enter-overview');
  expect(projection).not.toHaveTextContent('private.example.invalid');
  expect(projection).not.toHaveTextContent('details');
});

it('revokes selected Workspace truth when registry refresh marks the same entry unavailable', async () => {
  const available = { name: 'web-app', rootPath: 'C:\\projects\\web-app', status: 'available' as const, message: 'Available.', platforms: [] };
  const unavailable = { ...available, status: 'unavailable' as const, message: 'Configuration is unavailable.', action: 'Repair config.yaml.' };
  vi.mocked(controlPlaneClient.workspaces)
    .mockResolvedValueOnce({ workspaces: [available] })
    .mockResolvedValueOnce({ workspaces: [unavailable] });
  const user = userEvent.setup();
  render(<ControlPlaneApp />);

  await user.click(await screen.findByRole('button', { name: 'Overview select' }));
  expect(screen.getByText('Overview web-app')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Overview retry' }));

  await waitFor(() => expect(screen.getByText('Overview unselected')).toBeVisible());
  expect(controlPlaneClient.workspaces).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole('button', { name: 'Overview open' })).not.toBeInTheDocument();
  expect(screen.queryByText('Workspace web-app')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Test Runner' }));
  expect(screen.getByText('Devices Workspace unselected')).toBeVisible();
  expect(screen.getByText('web-app').closest('[aria-disabled="true"]')).toBeInTheDocument();
});

it('revokes selected Workspace truth when the entry disappears during refresh', async () => {
  const available = { name: 'web-app', rootPath: 'C:\\projects\\web-app', status: 'available' as const, message: 'Available.', platforms: [] };
  vi.mocked(controlPlaneClient.workspaces)
    .mockResolvedValueOnce({ workspaces: [available] })
    .mockResolvedValueOnce({ workspaces: [] });
  const user = userEvent.setup();
  render(<ControlPlaneApp />);

  await user.click(await screen.findByRole('button', { name: 'Overview select' }));
  expect(screen.getByText('Overview web-app')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Overview retry' }));

  await waitFor(() => expect(screen.getByText('Overview unselected')).toBeVisible());
  expect(screen.queryByText('Workspace web-app')).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Test Runner' }));
  expect(screen.getByText('Devices Workspace unselected')).toBeVisible();
});

it('hands Workspace case actions to Devices and clears them for ordinary navigation', async () => {
  vi.mocked(controlPlaneClient.workspaces).mockResolvedValue({ workspaces: [
    { name: 'web-app', rootPath: 'C:\\projects\\web-app', status: 'available', message: 'Available.', platforms: [{ platform: 'web', configPath: 'web.yaml', status: 'available', message: 'Available.' }] },
  ] });
  const user = userEvent.setup();
  render(<ControlPlaneApp />);

  await user.click(await screen.findByRole('button', { name: /web-app/i }));
  await user.click(screen.getByRole('button', { name: 'Record case from browser' }));
  expect(screen.getByText('explore:web-app::')).toBeVisible();

  await user.click(screen.getByRole('button', { name: /web-app/i }));
  await user.click(screen.getByRole('button', { name: 'Replay case from browser' }));
  expect(screen.getByText('strict:web-app:web:flows/login.fsq.yaml')).toBeVisible();

  await user.click(screen.getByRole('button', { name: 'Consume launch intent' }));
  await user.click(screen.getByRole('button', { name: /web-app/i }));
  await user.click(screen.getByRole('button', { name: 'Test Runner' }));
  expect(screen.getByText('No launch intent')).toBeVisible();
});

it('revokes Workspace, Record, and Replay consumers after a registry refresh fails', async () => {
  vi.mocked(controlPlaneClient.workspaces)
    .mockResolvedValueOnce({ workspaces: [
      { name: 'web-app', rootPath: 'C:\\projects\\web-app', status: 'available', message: 'Available.', platforms: [{ platform: 'web', configPath: 'web.yaml', status: 'available', message: 'Available.' }] },
    ] })
    .mockRejectedValueOnce({ code: 'network_error', message: 'Registry unavailable.', action: 'Retry locally.' });
  const user = userEvent.setup();
  render(<ControlPlaneApp />);

  await user.click(await screen.findByRole('button', { name: /web-app/i }));
  await user.click(screen.getByRole('button', { name: 'Cancel registry fixture' }));
  await waitFor(() => expect(controlPlaneClient.workspaces).toHaveBeenCalledTimes(2));
  expect(screen.getByText('Workspace content')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Record case from browser' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Replay case from browser' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Test Runner' }));
  expect(screen.getByText('Registry pending')).toBeVisible();
  expect(screen.getByText('Devices Workspace unselected')).toBeVisible();
  expect(screen.getByText('No launch intent')).toBeVisible();
});

it('keeps Config active when the user rejects dirty-draft navigation', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
  const user = userEvent.setup();
  render(<ControlPlaneApp />);
  await user.click(screen.getByRole('button', { name: 'Test Runner' }));
  await user.click(screen.getByRole('button', { name: 'Settings' }));
  await user.click(screen.getByRole('button', { name: 'Make draft dirty' }));

  await user.click(screen.getByRole('button', { name: 'Test Runner' }));
  expect(screen.getByText('Config content')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Test Runner' }));

  expect(confirm).toHaveBeenCalledTimes(2);
  expect(screen.getByText('Devices content')).toBeVisible();
});

it('keeps a dirty Workspace draft until page navigation is confirmed', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
  const user = userEvent.setup();
  render(<ControlPlaneApp />);
  await user.click(screen.getByRole('button', { name: 'Create workspace' }));
  await user.click(screen.getByRole('button', { name: 'Make workspace draft dirty' }));

  await user.click(screen.getByRole('button', { name: 'Test Runner' }));
  expect(screen.getByText('Create workspace content')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Test Runner' }));

  expect(confirm).toHaveBeenCalledTimes(2);
  expect(screen.getByText('Devices content')).toBeVisible();
});

it('restores focus to the initiating control when workspace creation is cancelled', async () => {
  const user = userEvent.setup();
  render(<ControlPlaneApp />);
  const createWorkspace = screen.getByRole('button', { name: 'Create workspace' });

  await user.click(createWorkspace);
  await user.click(screen.getByRole('button', { name: 'Cancel registry fixture' }));

  await waitFor(() => expect(createWorkspace).toHaveFocus());
});

it('focuses the workspace heading after successful creation', async () => {
  vi.mocked(controlPlaneClient.workspaces)
    .mockResolvedValueOnce({ workspaces: [] })
    .mockResolvedValue({ workspaces: [
      { name: 'created', rootPath: 'C:\\projects\\created', status: 'available', message: 'Available.', platforms: [] },
    ] });
  const user = userEvent.setup();
  render(<ControlPlaneApp />);
  await user.click(screen.getByRole('button', { name: 'Create workspace' }));
  await user.click(screen.getByRole('button', { name: 'Complete creation' }));

  await waitFor(() => expect(screen.getByRole('heading', { name: 'created' })).toHaveFocus());
});

it('reopens narrow navigation before restoring create-cancel focus', async () => {
  const matchMedia = vi.spyOn(window, 'matchMedia').mockReturnValue({
    matches: true,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  } as unknown as MediaQueryList);
  const user = userEvent.setup();
  render(<ControlPlaneApp />);
  const openNavigation = screen.getByRole('button', { name: 'Open navigation' });
  await user.click(openNavigation);
  const createWorkspace = screen.getByRole('button', { name: 'Create workspace' });
  await user.click(createWorkspace);

  await user.click(screen.getByRole('button', { name: 'Cancel registry fixture' }));

  await waitFor(() => {
    expect(openNavigation).toHaveAttribute('aria-expanded', 'true');
    expect(createWorkspace).toHaveFocus();
  });
  matchMedia.mockRestore();
});

it('restores the previous workspace selection when creation is cancelled', async () => {
  vi.mocked(controlPlaneClient.workspaces).mockResolvedValue({ workspaces: [
    { name: 'mobile', rootPath: 'C:\\projects\\mobile', status: 'available', message: 'Workspace is available.', platforms: [{ platform: 'android', configPath: 'C:\\projects\\mobile\\.fsq\\config\\config.android.yaml', status: 'available', message: 'Platform is available.' }] },
  ] });
  const user = userEvent.setup();
  render(<ControlPlaneApp />);
  await user.click(await screen.findByRole('button', { name: /mobile/i }));
  await user.click(screen.getByRole('button', { name: 'Create workspace' }));

  await user.click(screen.getByRole('button', { name: 'Cancel registry fixture' }));

  expect(screen.getByText('Workspace mobile')).toBeVisible();
});

it('selects available registry entries and exposes unavailable entries only as repair guidance', async () => {
  vi.mocked(controlPlaneClient.workspaces).mockResolvedValue({ workspaces: [
    { name: 'mobile', rootPath: 'C:\\projects\\mobile', status: 'available', message: 'Workspace is available.', platforms: [{ platform: 'android', configPath: 'C:\\projects\\mobile\\.fsq\\config\\config.android.yaml', status: 'available', message: 'Platform is available.' }] },
    { name: 'broken', rootPath: 'C:\\projects\\broken', status: 'unavailable', message: 'Configuration is unavailable.', action: 'Repair config.yaml.', platforms: [{ platform: 'windows', configPath: 'C:\\projects\\broken\\.fsq\\config\\config.windows.yaml', status: 'unavailable', message: 'Platform is unavailable.', action: 'Repair config.windows.yaml.' }] },
  ] });
  const user = userEvent.setup();
  render(<ControlPlaneApp />);

  const broken = await screen.findByText('broken');
  const brokenEntry = broken.closest('[aria-disabled="true"]');
  expect(brokenEntry).toHaveAttribute('title', 'Configuration is unavailable. Repair config.yaml.');
  expect(brokenEntry).toHaveTextContent('windows unavailable');
  expect(brokenEntry).not.toHaveTextContent('C:\\projects');
  expect(screen.queryByRole('button', { name: /broken/i })).not.toBeInTheDocument();

  const mobile = screen.getByRole('button', { name: /mobile/i });
  expect(mobile).toHaveTextContent('android');
  expect(mobile).not.toHaveTextContent('available');
  expect(mobile).not.toHaveTextContent('C:\\projects');
  await user.click(mobile);
  expect(screen.getByText('Workspace mobile')).toBeVisible();
});

it('shows every configured platform and status for a partial workspace without its path', async () => {
  vi.mocked(controlPlaneClient.workspaces).mockResolvedValue({ workspaces: [
    { name: 'partial', rootPath: 'C:\\projects\\partial', status: 'partial', message: 'One platform needs repair.', platforms: [
      { platform: 'android', configPath: 'android.yaml', status: 'available', message: 'Platform is available.' },
      { platform: 'web', configPath: 'web.yaml', status: 'unavailable', message: 'Platform is unavailable.', action: 'Repair web config.' },
    ] },
  ] });
  render(<ControlPlaneApp />);

  const partial = await screen.findByRole('button', { name: /partial/i });
  expect(partial).toHaveTextContent('android, web unavailable');
  expect(partial).not.toHaveTextContent('C:\\projects');
});

it('keeps selected Workspace Files and Configuration in the full-bleed outlet', async () => {
  vi.mocked(controlPlaneClient.workspaces).mockResolvedValue({ workspaces: [
    { name: 'mobile', rootPath: 'C:\\projects\\mobile', status: 'available', message: 'Workspace is available.', platforms: [] },
  ] });
  const user = userEvent.setup();
  render(<ControlPlaneApp />);
  const outlet = screen.getByRole('main');

  expect(outlet).not.toHaveClass('cp-page-outlet--full-bleed');
  await user.click(await screen.findByRole('button', { name: /mobile/i }));
  await waitFor(() => expect(outlet).toHaveClass('cp-page-outlet--full-bleed'));

  await user.click(screen.getByRole('button', { name: 'Configure workspace' }));
  await waitFor(() => expect(outlet).toHaveClass('cp-page-outlet--full-bleed'));
});

it('distinguishes workspace registry loading from empty and retries a failed request', async () => {
  let resolveInitial!: (value: { workspaces: [] }) => void;
  vi.mocked(controlPlaneClient.workspaces)
    .mockImplementationOnce(() => new Promise((resolve) => { resolveInitial = resolve; }))
    .mockRejectedValueOnce({ code: 'network_error', message: 'Registry unavailable.', action: 'Retry locally.' })
    .mockResolvedValueOnce({ workspaces: [] });
  const user = userEvent.setup();
  render(<ControlPlaneApp />);

  expect(screen.getByText('Loading workspaces…')).toBeVisible();
  expect(screen.queryByText('No registered workspaces')).not.toBeInTheDocument();
  resolveInitial({ workspaces: [] });
  expect(await screen.findByText('No registered workspaces')).toBeVisible();

  await user.click(screen.getByRole('button', { name: 'Create workspace' }));
  await user.click(screen.getByRole('button', { name: 'Cancel registry fixture' }));
  expect(await screen.findByText('Registry unavailable.')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Retry workspace registry' }));
  await waitFor(() => expect(controlPlaneClient.workspaces).toHaveBeenCalledTimes(3));
  expect(await screen.findByText('No registered workspaces')).toBeVisible();
});

it('retries Provider and registry requests without forwarding click events as AbortSignals', async () => {
  const error = { code: 'unavailable', message: 'Temporarily unavailable.', action: 'Retry.' };
  vi.mocked(controlPlaneClient.config).mockRejectedValueOnce(error);
  vi.mocked(controlPlaneClient.workspaces).mockRejectedValueOnce(error);
  render(<ControlPlaneApp />);
  await waitFor(() => expect(screen.getByTestId('overview-provider-projection')).toHaveTextContent('error'));
  const configCalls = vi.mocked(controlPlaneClient.config).mock.calls.length;
  const registryCalls = vi.mocked(controlPlaneClient.workspaces).mock.calls.length;
  await userEvent.click(screen.getByRole('button', { name: 'Provider retry' }));
  await userEvent.click(screen.getByRole('button', { name: 'Overview retry' }));
  await waitFor(() => expect(screen.getByTestId('overview-provider-projection')).toHaveTextContent('unconfigured'));
  expect(controlPlaneClient.config).toHaveBeenCalledTimes(configCalls + 1);
  expect(controlPlaneClient.workspaces).toHaveBeenCalledTimes(registryCalls + 1);
  expect(controlPlaneClient.config).toHaveBeenLastCalledWith(undefined);
  expect(controlPlaneClient.workspaces).toHaveBeenLastCalledWith(undefined);
});

it.each(['Overview create', 'Overview step create', 'Empty create'])('restores the connected %s control after cancelled creation', async (origin) => {
  render(<ControlPlaneApp />);
  if (origin === 'Empty create') {
    await userEvent.click(screen.getByRole('button', { name: 'Create workspace' }));
    await userEvent.click(screen.getByRole('button', { name: 'Cancel registry fixture' }));
  }
  await userEvent.click(screen.getByRole('button', { name: origin }));
  expect(screen.getByText('Create workspace content')).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Cancel registry fixture' }));
  await waitFor(() => expect(screen.getByRole('button', { name: origin })).toHaveFocus());
  expect(screen.queryByText('Create workspace content')).not.toBeInTheDocument();
});

it('collapses Workspaces without navigating or discarding a Settings draft',async()=>{
 const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
 render(<ControlPlaneApp/>);
 await userEvent.click(screen.getByRole('button',{name:'Settings'}));
 await userEvent.click(screen.getByRole('button',{name:'Make draft dirty'}));
 await userEvent.click(screen.getByRole('button',{name:'Workspaces'}));
 expect(screen.getByText('Config content')).toBeVisible();
 expect(confirm).not.toHaveBeenCalled();
 await userEvent.click(screen.getByRole('button',{name:'Workspaces'}));
 expect(confirm).not.toHaveBeenCalled();
 expect(screen.getByRole('button',{name:'Settings'})).toHaveAttribute('aria-current','page');
});

it('focuses the Workspace heading on selection including a same-context return from Home',async()=>{
 vi.mocked(controlPlaneClient.workspaces).mockResolvedValue({workspaces:[{name:'focus-demo',rootPath:'/focus',status:'available',message:'Available',platforms:[]}]});
 render(<ControlPlaneApp/>);
 const entry=await screen.findByRole('button',{name:/focus-demo/});
 await userEvent.click(entry);
 await waitFor(()=>expect(screen.getByRole('heading',{name:'focus-demo'})).toHaveFocus());
 await userEvent.click(screen.getByRole('button',{name:'Home'}));
 await userEvent.click(screen.getByRole('button',{name:/focus-demo/}));
 await waitFor(()=>expect(screen.getByRole('heading',{name:'focus-demo'})).toHaveFocus());
});

it('focuses the Workspace heading when Home opens configuration',async()=>{
 vi.mocked(controlPlaneClient.workspaces).mockResolvedValue({workspaces:[{name:'configure-focus',rootPath:'/focus',status:'available',message:'Available',platforms:[]}]});
 render(<ControlPlaneApp/>);
 await userEvent.click(await screen.findByRole('button',{name:'Overview select'}));
 await userEvent.click(screen.getByRole('button',{name:'Overview configure'}));
 await waitFor(()=>expect(screen.getByRole('heading',{name:'configure-focus'})).toHaveFocus());
});
