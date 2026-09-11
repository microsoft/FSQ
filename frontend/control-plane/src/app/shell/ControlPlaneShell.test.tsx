import logoLight from '../../assets/logo-light.svg';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ControlPlaneShell } from './ControlPlaneShell';

it('renders the specified navigation with one Settings entry and available Runs', () => {
  render(<ControlPlaneShell activePage="devices" title="Test Runner" description="Independent outlet"><div>Arbitrary page outlet</div></ControlPlaneShell>);
  const sidebar=screen.getByLabelText('Control Plane sidebar');
  expect(Array.from(sidebar.querySelectorAll('.cp-nav-item > span'), item=>item.textContent)).toEqual(['Home','Test Runner','Runs','Workspaces','Settings']);
  expect(screen.getByRole('button',{name:'Test Runner'})).toHaveAttribute('aria-current','page');
  expect(screen.getAllByRole('button',{name:'Settings'})).toHaveLength(1);
  expect(screen.queryByRole('button',{name:'Config'})).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Runs'})).toBeEnabled();
  expect(screen.queryByText('Coming soon')).not.toBeInTheDocument();
});

it.each(['overview','devices','config','workspace'] as const)('keeps Workspace context distinct from the current %s page',async activePage=>{
  const onNavigate=vi.fn();
  const onSelectWorkspace=vi.fn();
  render(<ControlPlaneShell activePage={activePage} title="Test" description="Test" workspaces={[{id:'demo',label:'Demo',description:'web'}]} selectedWorkspaceId="demo" onNavigate={onNavigate} onSelectWorkspace={onSelectWorkspace}><input aria-label="Draft" defaultValue="Preserve this"/></ControlPlaneShell>);
  const sidebar=screen.getByLabelText('Control Plane sidebar');
  const child=screen.getByRole('button',{name:/Demo\s*web/});
  expect(child).toHaveAccessibleDescription('Current Workspace');
  expect(child.querySelector('.cp-workspace-selected-mark')).toBeInTheDocument();
  expect(sidebar.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
  if(activePage==='workspace')expect(child).toHaveAttribute('aria-current','page');
  else expect(child).not.toHaveAttribute('aria-current');
  const group=screen.getByRole('button',{name:'Workspaces'});
  expect(group).not.toHaveAttribute('aria-current');
  await userEvent.click(group);
  expect(group).toHaveAttribute('aria-expanded','false');
  expect(onNavigate).not.toHaveBeenCalled();
  expect(onSelectWorkspace).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Draft')).toHaveValue('Preserve this');
  await userEvent.click(group);
  expect(screen.getByRole('button',{name:/Demo\s*web/})).toHaveAccessibleDescription('Current Workspace');
  await userEvent.click(screen.getByRole('button',{name:/Demo\s*web/}));
  expect(onSelectWorkspace).toHaveBeenCalledWith('demo');
});

it('opens and closes the accessible drawer with focus restoration', async () => {
  const user = userEvent.setup();
  render(<ControlPlaneShell activePage="devices" title="Devices" description="Test"><div>Outlet</div></ControlPlaneShell>);
  const open = screen.getByRole('button', { name: 'Open navigation' });
  await user.click(open);
  expect(open).toHaveAttribute('aria-expanded', 'true');
  const close = screen.getByRole('button', { name: 'Close navigation' });
  expect(close).toHaveFocus();
  await user.keyboard('{Escape}');
  await waitFor(() => expect(open).toHaveFocus());
  expect(open).toHaveAttribute('aria-expanded', 'false');
});

it('traps forward and reverse keyboard focus inside the open drawer', async () => {
  const user = userEvent.setup();
  render(<ControlPlaneShell activePage="devices" title="Devices" description="Test"><button>Outlet action</button></ControlPlaneShell>);
  const open = screen.getByRole('button', { name: 'Open navigation' });
  await user.click(open);
  const close = screen.getByRole('button', { name: 'Close navigation' });
  const config = screen.getByRole('button', { name: 'Settings' });

  close.focus();
  await user.keyboard('{Shift>}{Tab}{/Shift}');
  expect(config).toHaveFocus();
  await user.keyboard('{Tab}');
  expect(close).toHaveFocus();
  await user.click(screen.getByRole('button', { name: 'Dismiss navigation overlay' }));
  await waitFor(() => expect(open).toHaveFocus());
});

it('keeps drawer keyboard containment correct after Workspaces is collapsed',async()=>{
 render(<ControlPlaneShell activePage="overview" title="Home" description="Test" workspaces={[{id:'demo',label:'Demo'}]}><button>Outside</button></ControlPlaneShell>);
 await userEvent.click(screen.getByRole('button',{name:'Open navigation'}));
 await userEvent.click(screen.getByRole('button',{name:'Workspaces'}));
 expect(screen.getByRole('button',{name:'Open navigation'})).toHaveAttribute('aria-expanded','true');
 screen.getByRole('button',{name:'Settings'}).focus();
 await userEvent.keyboard('{Tab}');
 expect(screen.getByRole('button',{name:'Close navigation'})).toHaveFocus();
 await userEvent.keyboard('{Shift>}{Tab}{/Shift}');
 expect(screen.getByRole('button',{name:'Settings'})).toHaveFocus();
});

it('bundles the supplied accessible FSQ logo', () => {
  render(<ControlPlaneShell activePage="overview" title="Home" description="Test"><div>Content</div></ControlPlaneShell>);
  expect(screen.getByRole('img', { name: 'FSQ' })).toHaveAttribute('src', logoLight);
});
