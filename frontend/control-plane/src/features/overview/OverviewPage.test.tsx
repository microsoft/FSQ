import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { WorkspaceRegistryEntry } from '../../api/types';
import { OverviewPage } from './OverviewPage';

const workspace: WorkspaceRegistryEntry = {
  name: 'TodoMVC Release', rootPath: '/private/path', status: 'partial', message: 'One platform needs attention.',
  platforms: [
    { platform: 'web', configPath: '/private/web.yaml', status: 'available', message: 'Ready.' },
    { platform: 'android', configPath: '/private/android.yaml', status: 'unavailable', message: 'Unavailable.' },
  ],
};
function props(overrides: Partial<React.ComponentProps<typeof OverviewPage>> = {}): React.ComponentProps<typeof OverviewPage> {
  return {workspaces:[],selectedWorkspace:null,registryStatus:'ready',provider:{status:'unconfigured'},onNavigate:vi.fn(),onCreateWorkspace:vi.fn(),onSelectWorkspace:vi.fn(),onClearWorkspace:vi.fn(),onOpenWorkspace:vi.fn(),onConfigureWorkspace:vi.fn(),onRecordCase:vi.fn(),onRetryWorkspaces:vi.fn(),onRetryProvider:vi.fn(),...overrides};
}
it('presents one create entry and no duplicate onboarding steps before selection', () => {
  render(<OverviewPage {...props()} />);
  expect(screen.getAllByRole('button', { name: 'Create workspace' })).toHaveLength(1);
  expect(screen.queryByRole('list', { name: 'Test this Workspace' })).not.toBeInTheDocument();
  expect(screen.queryByText('Coming soon')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Record new case' })).not.toBeInTheDocument();
  expect(screen.getByRole('region', { name: 'Global AI configuration' })).toHaveTextContent('~/.fsq');
});
it('shows safe identity and direct actions, with a chooser only on request', async () => {
  const p=props({workspaces:[workspace],selectedWorkspace:workspace});
  render(<OverviewPage {...p} />);
  expect(screen.getByRole('heading',{name:'Start testing in TodoMVC Release'})).toBeVisible();
  expect(screen.queryByText('/private/path')).not.toBeInTheDocument();
  expect(screen.queryByText('/private/web.yaml')).not.toBeInTheDocument();
  expect(screen.queryByRole('region',{name:'Choose a Workspace'})).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button',{name:'Record new case'}));
  expect(p.onRecordCase).toHaveBeenCalledWith(workspace.name);
  await userEvent.click(screen.getByRole('button',{name:'Browse Cases'}));
  expect(p.onOpenWorkspace).toHaveBeenCalledWith(workspace.name);
  await userEvent.click(screen.getByRole('button',{name:'Configure Workspace'}));
  expect(p.onConfigureWorkspace).toHaveBeenCalledWith(workspace.name);
  await userEvent.click(screen.getByRole('button',{name:'Change Workspace'}));
  expect(screen.getByRole('region',{name:'Choose a Workspace'})).toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Clear selection'}));
  expect(p.onClearWorkspace).toHaveBeenCalledOnce();
});
it('shows safe Provider summary independently and retries error without changing Workspace', async()=>{
 const p=props({workspaces:[workspace],selectedWorkspace:workspace,provider:{status:'configured',provider:'Azure OpenAI',modelName:'gpt-5.5'}});
 const {rerender}=render(<OverviewPage {...p}/>);
 expect(screen.getByText(/gpt-5.5/)).toBeVisible();
 expect(screen.queryByText(/api key|endpoint|token/i)).not.toBeInTheDocument();
 await userEvent.click(screen.getByRole('button',{name:'Manage Provider'}));
 expect(p.onNavigate).toHaveBeenCalledWith('config');
 rerender(<OverviewPage {...p} provider={{status:'error',error:{message:'Provider unavailable.',action:'Retry the local server.'}}}/>);
 expect(screen.getByRole('alert')).toHaveTextContent('Provider unavailable.');
 expect(screen.getByRole('heading',{name:'Start testing in TodoMVC Release'})).toBeVisible();
 await userEvent.click(screen.getByRole('button',{name:'Retry Provider'}));
 expect(p.onRetryProvider).toHaveBeenCalledOnce();
});
it('blocks recording with explanation when no platform is available but keeps repair and browsing',()=>{
 const unavailable={...workspace,platforms:workspace.platforms.filter(p=>p.platform==='android')};
 render(<OverviewPage {...props({selectedWorkspace:unavailable,workspaces:[unavailable]})}/>);
 expect(screen.getByRole('button',{name:'Record new case'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'Record new case'})).toHaveAccessibleDescription('Initialize or repair a platform in Workspace before recording.');
 expect(screen.getByRole('button',{name:'Browse Cases'})).toBeEnabled();
 const platforms=screen.getByLabelText('Configured platforms');
 expect(within(platforms).getByText('android').parentElement).toHaveTextContent('unavailable');
});
it('revokes stale identity while registry loads or fails and offers retry',async()=>{
 const p=props({selectedWorkspace:workspace,workspaces:[workspace],registryStatus:'loading'});
 const {rerender}=render(<OverviewPage {...p}/>);
 expect(screen.queryByText('TodoMVC Release')).not.toBeInTheDocument();
 expect(screen.getByRole('status')).toHaveTextContent('Loading registered Workspaces');
 rerender(<OverviewPage {...p} registryStatus="error" registryError="Registry unavailable."/>);
 expect(screen.queryByText('TodoMVC Release')).not.toBeInTheDocument();
 await userEvent.click(screen.getByRole('button',{name:'Retry Workspaces'}));
 expect(p.onRetryWorkspaces).toHaveBeenCalledOnce();
});
