import { render, screen } from '@testing-library/react';
import type { RunSnapshot } from '../../api/types';
import { DevicesPage } from './DevicesPage';
import { useDeviceWorkspace } from './hooks/useDeviceWorkspace';

vi.mock('./hooks/useDeviceWorkspace', () => ({ useDeviceWorkspace: vi.fn() }));

it('shows completed macOS preflight while Explore case discovery is still pending',()=>{
  const ok={status:'ready' as const,message:'Ready',action:''};
  vi.mocked(useDeviceWorkspace).mockReturnValue({
    bootstrap:{state:'ready',data:{apiVersion:'1.0',platforms:[],busy:false,activeTask:null},error:null},workspaceName:'mac',platform:'macos',mode:'explore',goal:'Verify',casePath:'',targetId:'macos-app',
    readiness:{state:'ready',data:{workspaceName:'mac',platformId:'macos',workspace:ok,platform:ok,provider:ok,target:ok,strict:ok,prerequisites:[],commands:{caseCreate:ok,caseTest:ok},checkedAt:'2026-09-07T00:00:00Z'},error:null},
    targets:{state:'ready',data:{platform:'macos',targetLabel:'Application',targets:[]},error:null},cases:{state:'loading',data:null,error:null},
    selectedTarget:{id:'macos-app',label:'Edge',description:'Ready',status:'ready',selectable:true,isDefault:true,metadata:{}},selectedCase:null,requestId:null,snapshot:null,streamError:null,startError:null,evidenceTab:'screen',selectedStepId:null,
    saveYamlState:{state:'idle',data:null,error:null},controlsLocked:false,canStart:true,starting:false,blockedReason:'',connection:'live',connectionLabel:'Ready',
    setPlatform:vi.fn(),setTargetId:vi.fn(),setMode:vi.fn(),setGoal:vi.fn(),setCasePath:vi.fn(),setEvidenceTab:vi.fn(),setSelectedStepId:vi.fn(),refresh:vi.fn(),start:vi.fn(),cancel:vi.fn(),saveYaml:vi.fn(),newRun:vi.fn(),
  });
  render(<DevicesPage workspaces={[{name:'mac',rootPath:'/mac',status:'available',message:'Ready',platforms:[{platform:'macos',status:'available',message:'Ready',configPath:'/mac/config'}]}]} workspaceRegistryReady selectedWorkspaceName="mac" onWorkspaceChange={vi.fn()} renderShell={(toolbar,content)=><>{toolbar}{content}</>}/>);
  expect(screen.getByText('macOS is ready')).toBeVisible();
  expect(screen.queryByRole('region', { name: 'Live evidence' })).not.toBeInTheDocument();
  expect(screen.queryByText('Start is unavailable until checks complete.')).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Start exploration'})).toBeEnabled();
});

it('announces live run truth and locks target controls without moving focus', () => {
  const snapshot: RunSnapshot = {
    requestId: 'request-1', runId: 'run-1', workspaceName: 'test', platform: 'web', targetId: 'chrome', mode: 'explore', status: 'running',
    source: { goal: 'Verify' }, startedAt: '', completedAt: null, cancelRequested: false, events: [], activeStep: null,
    result: null, summary: 'Executing step 1.', screenshotRevision: 0, uiSnapshotRevision: 0,
    evidenceAvailable: false, reportAvailable: false, terminal: false,
  };
  vi.mocked(useDeviceWorkspace).mockReturnValue({
    bootstrap: { state: 'ready', data: { apiVersion: '1.0', platforms: [{ id: 'web', label: 'Web' }], busy: true, activeTask: snapshot }, error: null },
    workspaceName: 'test', platform: 'web', setPlatform: vi.fn(), targetId: 'chrome', setTargetId: vi.fn(), mode: 'explore', setMode: vi.fn(), goal: 'Verify', setGoal: vi.fn(), casePath: '', setCasePath: vi.fn(),
    readiness: { state: 'ready', data: null, error: null },
    starting:false,blockedReason:'',
    targets: { state: 'ready', data: { platform: 'web', targetLabel: 'Browser', targets: [{ id: 'chrome', label: 'Chrome', description: 'ready', status: 'ready', selectable: true, isDefault: true, metadata: {} }] }, error: null },
    cases: { state: 'ready', data: { platform: 'web', cases: [], truncated: false }, error: null }, selectedTarget: { id: 'chrome', label: 'Chrome', description: 'ready', status: 'ready', selectable: true, isDefault: true, metadata: {} }, selectedCase: null,
    requestId: 'request-1', snapshot, streamError: null, startError: null, evidenceTab: 'screen', setEvidenceTab: vi.fn(),
    selectedStepId: null, setSelectedStepId: vi.fn(), saveYamlState: { state: 'idle', data: null, error: null }, controlsLocked: true, canStart: false, connection: 'live', connectionLabel: 'Live', refresh: vi.fn(), start: vi.fn(), cancel: vi.fn(), saveYaml: vi.fn(), newRun: vi.fn(),
  });

  render(<DevicesPage
    workspaces={[{ name: 'test', rootPath: 'C:\\test', status: 'available', message: 'Available.', platforms: [{ platform: 'web', configPath: 'C:\\test\\.fsq\\config\\config.web.yaml', status: 'available', message: 'Available.' }] }]}
    workspaceRegistryReady
    selectedWorkspaceName="test" onWorkspaceChange={vi.fn()} renderShell={(toolbar, content) => <>{toolbar}{content}</>}
  />);

  expect(screen.queryByRole('combobox', { name: 'Workspace' })).not.toBeInTheDocument();
  expect(screen.getByLabelText('Run context')).toHaveTextContent('test');
  expect(document.querySelector('.operation-body')).toHaveClass('operation-body--run');
  expect(document.querySelector('[aria-live="polite"]')).toHaveTextContent('Run running. Executing step 1. Live.');
  expect(document.body).toHaveFocus();
});

it('shows neutral guidance before platform selection and renders no evidence placeholders', () => {
  const current=vi.mocked(useDeviceWorkspace).getMockImplementation()?.({} as never);
  if (!current) throw new Error("Device workspace fixture is missing");
  vi.mocked(useDeviceWorkspace).mockReturnValue({...current,platform:'',requestId:null,snapshot:null,controlsLocked:false,canStart:false,connectionLabel:'Unavailable',selectedTarget:null});
  render(<DevicesPage workspaces={[]} workspaceRegistryReady selectedWorkspaceName="test" onWorkspaceChange={vi.fn()} renderShell={(_,content)=><>{content}</>}/>);
  expect(screen.queryByText('Readiness is unavailable.')).not.toBeInTheDocument();
  expect(screen.queryByRole('region',{name:'Live evidence'})).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Start exploration'})).toHaveAccessibleDescription('Select a platform to check its environment.');
});

it('keeps reconnecting visible in the execution context without changing run status', () => {
 const current=vi.mocked(useDeviceWorkspace).getMockImplementation()?.({} as never);
 if(!current)throw new Error('Missing fixture');
 const snapshot:RunSnapshot={requestId:'r',runId:'run',workspaceName:'test',platform:'web',targetId:'chrome',mode:'explore',status:'running',source:{goal:'Verify'},startedAt:'',completedAt:null,cancelRequested:false,events:[],activeStep:null,result:null,summary:'Running',screenshotRevision:0,uiSnapshotRevision:0,evidenceAvailable:false,reportAvailable:false,terminal:false};
 vi.mocked(useDeviceWorkspace).mockReturnValue({...current,platform:'web',requestId:'r',snapshot,connection:'reconnecting',connectionLabel:'Reconnecting'});
 render(<DevicesPage workspaces={[]} workspaceRegistryReady selectedWorkspaceName="test" onWorkspaceChange={vi.fn()} renderShell={(_,content)=><>{content}</>}/>);
 expect(screen.getByText('Reconnecting')).toBeVisible();
 expect(screen.getByText('Reconnecting')).toHaveAttribute('role','status');
 expect(screen.getByLabelText('Run context')).toHaveTextContent('running');
});
