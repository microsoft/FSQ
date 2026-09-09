import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { controlPlaneClient } from '../../api/controlPlaneClient';
import type { WorkspaceDetail, WorkspacePlatformDetail } from '../../api/types';
import { WorkspacePage, WorkspaceTitlebar } from './WorkspacePage';

const summary = (name: string): WorkspaceDetail => ({
  name,
  rootPath: `C:\\projects\\${name}`,
  status: 'available',
  message: 'Available.',
  platforms: [
    { platform: 'android', configPath: 'android.yaml', status: 'available', message: 'Available.', target: { appId: `com.example.${name}` }, env: [], revision: 'sha256:android' },
    { platform: 'web', configPath: 'web.yaml', status: 'available', message: 'Available.', target: { browserChannel: 'chrome', browserExecutablePath: 'C:\\chrome.exe' }, env: [], revision: 'sha256:web' },
  ],
});

const props = {
  createRequested: false,
  configurationOpen: true,
  onRetryRegistry: vi.fn(),
  onRequestCreate: vi.fn(),
  onCancelCreate: vi.fn(),
  onConfigurationOpenChange: vi.fn(),
  onCreated: vi.fn(),
  onRegistryChanged: vi.fn(),
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

afterEach(() => vi.restoreAllMocks());

it('loads private repair details only on Edit and clears them during registry revalidation',async()=>{
  const response:WorkspaceDetail={...summary('repair'),status:'unavailable',platforms:[{platform:'macos',configPath:'mac.yaml',status:'unavailable',message:'Missing app',action:'Repair',diagnosticAvailable:true,repairAvailable:true}]};
  vi.spyOn(controlPlaneClient,'workspace').mockResolvedValue(response);
  const detail=vi.spyOn(controlPlaneClient,'workspacePlatform').mockResolvedValue({name:'repair',rootPath:'/local',configPath:'mac.yaml',platform:'macos',target:{bundleId:'com.example.app',appPath:'/Missing.app'},env:{PRIVATE:'sensitive-local-value'},revision:'sha256:old'});
  const {rerender}=render(<WorkspacePage {...props} selectedName="repair" repairContext/>);
  await screen.findByRole('button',{name:'Edit target configuration'});
  expect(detail).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button',{name:'Edit target configuration'}));
  await waitFor(()=>expect(detail).toHaveBeenCalledWith('repair','macos',expect.any(AbortSignal)));
  expect(await screen.findByDisplayValue('/Missing.app')).toBeInTheDocument();
  rerender(<WorkspacePage {...props} selectedName={null} repairContext/>);
  expect(screen.queryByDisplayValue('/Missing.app')).not.toBeInTheDocument();
  expect(screen.queryByDisplayValue('sensitive-local-value')).not.toBeInTheDocument();
});

it('keeps repair configuration isolated across registry loading and reload',async()=>{
  const response:WorkspaceDetail={...summary('repair'),status:'unavailable',platforms:[{platform:'macos',configPath:'mac.yaml',status:'unavailable',message:'Missing app',action:'Repair',diagnosticAvailable:true,repairAvailable:true}]};
  vi.spyOn(controlPlaneClient,'workspace').mockResolvedValue(response);
  const onConfigurationOpenChange=vi.fn();
  const {rerender}=render(<WorkspacePage {...props} selectedName="repair" repairContext onConfigurationOpenChange={onConfigurationOpenChange}/>);
  await screen.findByText('macOS configuration unavailable');
  rerender(<WorkspacePage {...props} selectedName={null} repairContext onConfigurationOpenChange={onConfigurationOpenChange}/>);
  expect(onConfigurationOpenChange).not.toHaveBeenCalledWith(false);
  rerender(<WorkspacePage {...props} selectedName="repair" repairContext configurationOpen={false} onConfigurationOpenChange={onConfigurationOpenChange}/>);
  expect(await screen.findByRole('button',{name:'Back to diagnostics'})).toBeVisible();
  expect(screen.queryByRole('button',{name:'Add platform'})).not.toBeInTheDocument();
  expect(screen.queryByRole('region',{name:/Workspace files/})).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Edit target configuration'})).toBeVisible();
});

it('groups the workspace name and full path separately from platform metadata', () => {
  const onRecordCase = vi.fn();
  render(<WorkspaceTitlebar workspace={{
    name: 'edge',
    rootPath: 'D:\\fsq\\edge',
    status: 'available',
    message: 'Available.',
    platforms: [
      { platform: 'android', configPath: 'android.yaml', status: 'available', message: 'Available.' },
      { platform: 'web', configPath: 'web.yaml', status: 'unavailable', message: 'Unavailable.', action: 'Repair web config.' },
    ],
  }} onRecordCase={onRecordCase} />);

  const heading = screen.getByRole('heading', { name: 'edge' });
  const path = screen.getByText('D:\\fsq\\edge');
  const platforms = screen.getByLabelText('Workspace platforms');
  expect(heading.parentElement).toContainElement(path);
  expect(heading.parentElement).not.toContainElement(platforms);
  expect(heading.parentElement?.parentElement).toContainElement(platforms);
  expect(screen.getByLabelText('Android available').querySelector('[title="Available"] svg')).toBeVisible();
  expect(screen.getByLabelText('Web unavailable')).toHaveTextContent('Unavailable');
  expect(screen.getByRole('button', { name: 'Record new case' })).toHaveAttribute('title', 'Record new case');
});

it('discards an aborted platform detail response after the workspace changes', async () => {
  const oldDetail = deferred<WorkspacePlatformDetail>();
  vi.spyOn(controlPlaneClient, 'workspace').mockImplementation((name) => Promise.resolve(summary(name)));
  const platformRequest = vi.spyOn(controlPlaneClient, 'workspacePlatform').mockReturnValue(oldDetail.promise);
  const user = userEvent.setup();
  const { rerender } = render(<WorkspacePage {...props} selectedName="alpha" />);
  await user.click(await screen.findByRole('button', { name: 'Edit' }));
  const signal = platformRequest.mock.calls[0]?.[2];

  rerender(<WorkspacePage {...props} selectedName="beta" />);
  await screen.findByRole('heading', { name: 'Platform configuration' });
  oldDetail.resolve({ ...summary('alpha').platforms[0], name: 'alpha', rootPath: 'C:\\projects\\alpha', env: { SECRET: 'old-value' } } as WorkspacePlatformDetail);
  await waitFor(() => expect(signal?.aborted).toBe(true));

  expect(screen.queryByRole('group', { name: 'Edit Android' })).not.toBeInTheDocument();
  expect(screen.queryByDisplayValue('old-value')).not.toBeInTheDocument();
});

it('implements selected tab-panel relationships and keyboard navigation', async () => {
  vi.spyOn(controlPlaneClient, 'workspace').mockResolvedValue(summary('alpha'));
  const user = userEvent.setup();
  render(<WorkspacePage {...props} selectedName="alpha" />);
  const android = await screen.findByRole('tab', { name: /Android/ });
  const web = screen.getByRole('tab', { name: /Web/ });
  android.focus();
  await user.keyboard('{End}');

  expect(web).toHaveFocus();
  expect(web).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tabpanel', { name: /Web/ })).toHaveAttribute('aria-labelledby', web.id);
});
it('keeps selected Workspace presentations full-bleed under the shared identity', async () => {
  const detail = deferred<WorkspaceDetail>();
  vi.spyOn(controlPlaneClient, 'workspace').mockReturnValue(detail.promise);
  const onPresentationChange = vi.fn();
  const { rerender } = render(<WorkspacePage {...props} configurationOpen={false} selectedName="alpha" onPresentationChange={onPresentationChange} />);

  expect(onPresentationChange).toHaveBeenLastCalledWith('default');
  await act(async () => detail.resolve(summary('alpha')));
  await waitFor(() => expect(onPresentationChange).toHaveBeenLastCalledWith('full-bleed'));

  rerender(<WorkspacePage {...props} configurationOpen selectedName="alpha" onPresentationChange={onPresentationChange} />);
  await waitFor(() => expect(onPresentationChange).toHaveBeenLastCalledWith('full-bleed'));
});

it('guards Files navigation while an edited private draft is dirty and clears it on discard', async () => {
  vi.spyOn(controlPlaneClient, 'workspace').mockResolvedValue(summary('alpha'));
  vi.spyOn(controlPlaneClient, 'workspacePlatform').mockResolvedValue({name:'alpha',rootPath:'/alpha',configPath:'/alpha/config',platform:'android',target:{appId:'com.alpha'},env:{},revision:'r1'});
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  const onChange = vi.fn();
  render(<WorkspacePage {...props} selectedName="alpha" onConfigurationOpenChange={onChange}/>);
  await userEvent.click(await screen.findByRole('button',{name:'Edit'}));
  await userEvent.type(await screen.findByRole('textbox',{name:'App ID'}),'.draft');
  await userEvent.click(screen.getByRole('tab',{name:'Files'}));
  expect(confirm).toHaveBeenCalledWith('Discard unsaved workspace changes?');
  expect(screen.getByRole('textbox',{name:'App ID'})).toHaveValue('com.alpha.draft');
  expect(onChange).not.toHaveBeenCalledWith(false);
  confirm.mockReturnValue(true);
  await userEvent.click(screen.getByRole('tab',{name:'Files'}));
  expect(onChange).toHaveBeenCalledWith(false);
  expect(screen.queryByRole('textbox',{name:'App ID'})).not.toBeInTheDocument();
});

it('locks presentation tabs during save and retains the form until persistence resolves', async () => {
  vi.spyOn(controlPlaneClient,'workspace').mockResolvedValue(summary('alpha'));
  vi.spyOn(controlPlaneClient,'workspacePlatform').mockResolvedValue({name:'alpha',rootPath:'/alpha',configPath:'/alpha/config',platform:'android',target:{appId:'com.alpha'},env:{},revision:'r1'});
  const pending=deferred<WorkspaceDetail>();
  vi.spyOn(controlPlaneClient,'updateWorkspacePlatform').mockReturnValue(pending.promise as never);
  render(<WorkspacePage {...props} selectedName="alpha"/>);
  await userEvent.click(await screen.findByRole('button',{name:'Edit'}));
  await userEvent.click(screen.getByRole('button',{name:'Save changes'}));
  expect(screen.getByRole('tab',{name:'Files'})).toBeDisabled();
  expect(screen.getByRole('group',{name:'Edit Android'})).toBeVisible();
});

it('keeps a pending folder selection valid across parent rerenders',async()=>{
  let resolve!: (value: {status:'selected';selectedPath:string;isEmpty:boolean})=>void;
  vi.spyOn(controlPlaneClient,'pickWorkspaceParentDirectory').mockReturnValue(new Promise(done=>{resolve=done}));
  const {rerender}=render(<WorkspacePage {...props} selectedName={null} createRequested/>);
  await userEvent.click(screen.getByRole('button',{name:'Choose folder'}));
  rerender(<WorkspacePage {...props} selectedName={null} createRequested onDirtyChange={()=>{}}/>);
  await act(async()=>resolve({status:'selected',selectedPath:'/temporary',isEmpty:true}));
  expect(screen.getByRole('textbox',{name:'Selected folder'})).toHaveValue('/temporary');
  expect(screen.getByRole('button',{name:'Choose folder'})).toBeEnabled();
});

it('explains a pending configuration save separately from missing platform eligibility',()=>{
 render(<WorkspaceTitlebar workspace={{name:'ready',rootPath:'/ready',status:'available',message:'Ready',platforms:[{platform:'web',status:'available',message:'Ready',configPath:'/ready/web'}]}} onRecordCase={vi.fn()} recordDisabled recordDisabledReason="Wait for the Workspace configuration to finish saving."/>);
 expect(screen.getByRole('button',{name:'Record new case'})).toHaveAccessibleDescription('Wait for the Workspace configuration to finish saving.');
});

it('keeps the revision in an accessible collapsed details disclosure', async () => {
  vi.spyOn(controlPlaneClient, 'workspace').mockResolvedValue(summary('demo'));
  render(<WorkspacePage {...props} selectedName="demo" />);
  const revision = await screen.findByText('sha256:android');
  const disclosure = revision.closest('details');
  expect(disclosure).not.toHaveAttribute('open');
  await userEvent.click(screen.getByText('Configuration details'));
  expect(disclosure).toHaveAttribute('open');
  expect(screen.getByRole('button', {name:'Edit'})).toBeEnabled();
});
