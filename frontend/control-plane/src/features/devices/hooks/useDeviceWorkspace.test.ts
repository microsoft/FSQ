import { act, renderHook, waitFor } from '@testing-library/react';
import { ControlPlaneApiError, type ControlPlaneClient } from '../../../api/controlPlaneClient';
import type { BootstrapResponse, ReadinessResponse, TargetsResponse, CasesResponse } from '../../../api/types';
import { useDeviceWorkspace } from './useDeviceWorkspace';

const bootstrap: BootstrapResponse = {
  apiVersion: '1.0', platforms: [{ id: 'android', label: 'Android' }, { id: 'web', label: 'Web' }],
  busy: false, activeTask: null,
};
const readiness = (platform: 'android' | 'web'): ReadinessResponse => ({
  ...(platform === 'android' ? {targetId:'android-target',prerequisites:[],commands:{caseCreate:{status:'ready' as const,message:'Ready',action:''},caseTest:{status:'ready' as const,message:'Ready',action:''}},checkedAt:'2026-09-08T00:00:00Z'} : {}),
  workspaceName: 'test', platformId: platform,
  platform: { status: 'ready', message: 'ready', action: '' },
  workspace: { status: 'ready', message: 'ready', action: '' }, provider: { status: 'ready', message: 'ready', action: '' },
  target: { status: 'ready', message: 'ready', action: '' }, strict: { status: 'ready', message: 'ready', action: '' },
});
const targets = (platform: 'android' | 'web'): TargetsResponse => ({ platform, targetLabel: platform === 'web' ? 'Browser' : 'Device', targets: [{ id: `${platform}-target`, label: `${platform} target`, description: 'ready', status: 'ready', selectable: true, isDefault: true, metadata: {} }] });
const cases = (platform: 'android' | 'web'): CasesResponse => ({ platform, truncated: false, cases: [{ path: `${platform}.fsq.yaml`, id: platform, name: `${platform} case`, platform, commandCount: 1, requiresAiAssertion: false, validationStatus: 'validated', selectable: true, diagnostics: [] }] });

function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
const runSnapshot = (requestId = 'request-1', platform: 'android' | 'web' = 'android') => ({
  requestId, runId: null, workspaceName: 'test', platform, targetId: `${platform}-target`, mode: 'explore' as const, status: 'preparing' as const,
  source: { goal: 'Verify' }, startedAt: '', completedAt: null, cancelRequested: false, events: [], activeStep: null,
  result: null, summary: 'Preparing', screenshotRevision: 0, uiSnapshotRevision: 0, evidenceAvailable: false, reportAvailable: false, terminal: false,
});
const platforms = bootstrap.platforms;
const deviceContext = { workspaceName: 'test', platforms, onWorkspaceChange: vi.fn() };

it('uses shared Gemini readiness for Explore and AI Strict without submitting provider credentials', async () => {
  const ready = { ...readiness('web'), provider: { status: 'ready' as const, message: 'Google Gemini is ready.', action: '' } };
  const inventory = cases('web');
  inventory.cases[0].requiresAiAssertion = true;
  const client = { bootstrap: vi.fn().mockResolvedValue(bootstrap), readiness: vi.fn().mockResolvedValue(ready), targets: vi.fn().mockResolvedValue(targets('web')), cases: vi.fn().mockResolvedValue(inventory), startRun: vi.fn().mockReturnValue(new Promise(() => {})) } as unknown as ControlPlaneClient;
  const { result } = renderHook(() => useDeviceWorkspace(deviceContext, client));
  await waitFor(() => expect(result.current.bootstrap.state).toBe('ready'));
  act(() => { result.current.setPlatform('web'); result.current.setGoal('Verify Gemini task'); });
  await waitFor(() => expect(result.current.canStart).toBe(true));
  act(() => { result.current.setMode('strict'); result.current.setCasePath('web.fsq.yaml'); });
  await waitFor(() => expect(result.current.canStart).toBe(true));
  vi.mocked(client.readiness).mockResolvedValueOnce({ ...ready, provider: { status: 'unavailable', message: 'Google Gemini unavailable.', action: 'Check Settings.' } });
  act(() => result.current.refresh());
  await waitFor(() => expect(result.current.readiness.data?.provider.status).toBe('unavailable'));
  expect(result.current.canStart).toBe(false);
  act(() => result.current.refresh());
  await waitFor(() => expect(result.current.canStart).toBe(true));
  act(() => { void result.current.start(); });
  expect(result.current.controlsLocked).toBe(true);
  const payload = vi.mocked(client.startRun).mock.calls[0][0];
  expect(payload).toMatchObject({ workspaceName: 'test', platform: 'web', mode: 'strict', casePath: 'web.fsq.yaml' });
  expect(payload).not.toHaveProperty('apiKey');
  expect(payload).not.toHaveProperty('provider');
});

it('binds Android diagnosis to explicit selection and rejects stale device responses',async()=>{
  const one=deferred<ReadinessResponse>();
  const two=deferred<ReadinessResponse>();
  const client={bootstrap:vi.fn().mockResolvedValue(bootstrap),readiness:vi.fn((_w,_p,_s,id)=>id==='one'?one.promise:id==='two'?two.promise:Promise.resolve({...readiness('android'),targetId:null})),targets:vi.fn().mockResolvedValue({platform:'android',targetLabel:'Device',targets:['one','two'].map(id=>({id,label:id,status:'ready',selectable:true,isDefault:false,description:'online',metadata:{}}))}),cases:vi.fn().mockResolvedValue(cases('android'))} as unknown as ControlPlaneClient;
  const {result}=renderHook(()=>useDeviceWorkspace(deviceContext,client));
  await waitFor(()=>expect(result.current.bootstrap.state).toBe('ready'));
  act(()=>{result.current.setPlatform('android');result.current.setGoal('Keep goal');});
  await waitFor(()=>expect(result.current.targets.state).toBe('ready'));
  expect(result.current.targetId).toBe('');
  expect(result.current.canStart).toBe(false);
  act(()=>result.current.setTargetId('one'));
  act(()=>result.current.setTargetId('two'));
  await act(async()=>two.resolve({...readiness('android'),targetId:'two'}));
  await waitFor(()=>expect(result.current.canStart).toBe(true));
  await act(async()=>one.resolve({...readiness('android'),targetId:'one'}));
  expect(result.current.readiness.data?.targetId).toBe('two');
  vi.mocked(client.targets).mockResolvedValueOnce({platform:'android',targetLabel:'Device',targets:[{id:'one',label:'one',status:'ready',selectable:true,isDefault:true,description:'online',metadata:{}}]});
  act(()=>result.current.refresh());
  await waitFor(()=>expect(result.current.targets.state).toBe('ready'));
  expect(result.current.targetId).toBe('two');
  expect(result.current.canStart).toBe(false);
  expect(result.current.goal).toBe('Keep goal');
});

it('allows provider-free Android Strict and keeps input after preflight failure',async()=>{
  const data={...readiness('android'),provider:{status:'unavailable' as const,message:'Configure provider',action:''}};
  const client={bootstrap:vi.fn().mockResolvedValue(bootstrap),readiness:vi.fn().mockResolvedValue(data),targets:vi.fn().mockResolvedValue(targets('android')),cases:vi.fn().mockResolvedValue(cases('android')),startRun:vi.fn().mockRejectedValue(new ControlPlaneApiError(400,{code:'android_preflight_failed',message:'Device disconnected',action:'Reconnect'}))} as unknown as ControlPlaneClient;
  const {result}=renderHook(()=>useDeviceWorkspace(deviceContext,client));
  await waitFor(()=>expect(result.current.bootstrap.state).toBe('ready'));
  act(()=>result.current.setPlatform('android'));
  await waitFor(()=>expect(result.current.readiness.state).toBe('ready'));
  act(()=>{result.current.setMode('strict');result.current.setCasePath('android.fsq.yaml');});
  await waitFor(()=>expect(result.current.canStart).toBe(true));
  await act(async()=>result.current.start());
  expect(result.current.casePath).toBe('android.fsq.yaml');
  expect(result.current.requestId).toBeNull();
  expect(result.current.startError?.code).toBe('android_preflight_failed');
});

it('applies explicit macOS diagnostic intent when switching an existing Devices workspace',async()=>{
  const ok={status:'ready' as const,message:'Ready',action:''};
  const client={bootstrap:vi.fn().mockResolvedValue(bootstrap),readiness:vi.fn((name:string,platform:string)=>Promise.resolve({...readiness('web'),workspaceName:name,platformId:platform,prerequisites:[],commands:{caseCreate:ok,caseTest:ok},checkedAt:'2026-09-07T00:00:00Z'})),targets:vi.fn().mockResolvedValue(targets('web')),cases:vi.fn().mockResolvedValue(cases('web'))} as unknown as ControlPlaneClient;
  const onWorkspaceChange=vi.fn();
  const opts=[{id:'web' as const,label:'Web'},{id:'macos' as const,label:'macOS'}];
  const {result,rerender}=renderHook(({name,intent}:{name:string;intent:import('./useDeviceWorkspace').DevicesLaunchIntent|null})=>useDeviceWorkspace({workspaceName:name,platforms:opts,onWorkspaceChange,launchIntent:intent},client),{initialProps:{name:'test',intent:null as import('./useDeviceWorkspace').DevicesLaunchIntent|null}});
  await waitFor(()=>expect(result.current.bootstrap.state).toBe('ready'));
  act(()=>result.current.setPlatform('web'));
  await waitFor(()=>expect(result.current.readiness.state).toBe('ready'));
  rerender({name:'repair',intent:{id:91,mode:'diagnostic',workspaceName:'repair',platform:'macos'}});
  await waitFor(()=>expect(result.current.platform).toBe('macos'));
  await waitFor(()=>expect(result.current.readiness.data?.workspaceName).toBe('repair'));
  expect(client.readiness).toHaveBeenLastCalledWith('repair','macos',expect.any(AbortSignal));
});

it('gives request-specific disabled reasons during Strict recheck with retained Case',async()=>{
  const client={bootstrap:vi.fn().mockResolvedValue(bootstrap),readiness:vi.fn().mockResolvedValue(readiness('web')),targets:vi.fn().mockResolvedValue(targets('web')),cases:vi.fn().mockResolvedValue(cases('web'))} as unknown as ControlPlaneClient;
  const {result}=renderHook(()=>useDeviceWorkspace(deviceContext,client));
  await waitFor(()=>expect(result.current.bootstrap.state).toBe('ready'));
  act(()=>result.current.setPlatform('web'));
  await waitFor(()=>expect(result.current.cases.state).toBe('ready'));
  act(()=>{result.current.setMode('strict');result.current.setCasePath('web.fsq.yaml');});
  const targetWait=deferred<TargetsResponse>();
  vi.mocked(client.targets).mockReturnValueOnce(targetWait.promise);
  act(()=>result.current.refresh());
  await waitFor(()=>expect(result.current.readiness.state).toBe('ready'));
  expect(result.current.blockedReason).toBe('Wait for target discovery to finish.');
  await act(async()=>targetWait.resolve(targets('web')));
  const caseWait=deferred<CasesResponse>();
  vi.mocked(client.cases).mockReturnValueOnce(caseWait.promise);
  act(()=>result.current.refresh());
  await waitFor(()=>expect(result.current.targets.state).toBe('ready'));
  expect(result.current.casePath).toBe('web.fsq.yaml');
  expect(result.current.blockedReason).toBe('Wait for Case discovery to finish.');
});

it('retains macOS input after start rejection and refreshes diagnosis without waiting for cases',async()=>{
  const ok={status:'ready' as const,message:'Ready',action:''};
  const mac:ReadinessResponse={workspaceName:'test',platformId:'macos',workspace:ok,platform:ok,provider:ok,target:ok,strict:ok,prerequisites:[],commands:{caseCreate:ok,caseTest:ok},checkedAt:'2026-09-07T00:00:00Z'};
  const pendingCases=deferred<CasesResponse>();
  const pendingStart=vi.fn();
  const client={bootstrap:vi.fn().mockResolvedValue(bootstrap),readiness:vi.fn().mockResolvedValue(mac),targets:vi.fn().mockResolvedValue({...targets('web'),platform:'macos'}),cases:vi.fn().mockReturnValue(pendingCases.promise),startRun:vi.fn().mockRejectedValue(new ControlPlaneApiError(400,{code:'macos_preflight_failed',message:'Endpoint offline',action:'Recheck'}))} as unknown as ControlPlaneClient;
  const context={workspaceName:'test',platforms:[{id:'macos' as const,label:'macOS'}],onWorkspaceChange:vi.fn(),onStartPendingChange:pendingStart};
  const {result}=renderHook(()=>useDeviceWorkspace(context,client));
  await waitFor(()=>expect(result.current.bootstrap.state).toBe('ready'));
  act(()=>{result.current.setPlatform('macos');result.current.setGoal('Retain this Goal');});
  await waitFor(()=>expect(result.current.canStart).toBe(true));
  expect(result.current.cases.state).toBe('loading');
  await act(async()=>{await result.current.start();});
  expect(result.current.goal).toBe('Retain this Goal');
  expect(result.current.requestId).toBeNull();
  expect(result.current.startError).not.toBeNull();
  expect(client.readiness).toHaveBeenCalledTimes(2);
  expect(pendingStart.mock.calls).toEqual([[true],[false]]);
});

it('invalidates macOS readiness on refresh and locks duplicate starts while retaining the goal',async()=>{
  const ok={status:'ready' as const,message:'Ready',action:''};
  const mac:ReadinessResponse={workspaceName:'test',platformId:'macos',workspace:ok,platform:ok,provider:ok,target:ok,strict:ok,prerequisites:[],commands:{caseCreate:ok,caseTest:ok},checkedAt:'2026-09-07T00:00:00Z'};
  const pending=deferred<{requestId:string}>();
  const client={bootstrap:vi.fn().mockResolvedValue(bootstrap),readiness:vi.fn().mockResolvedValue(mac),targets:vi.fn().mockResolvedValue({...targets('web'),platform:'macos'}),cases:vi.fn().mockResolvedValue({platform:'macos',cases:[],truncated:false}),startRun:vi.fn().mockReturnValue(pending.promise)} as unknown as ControlPlaneClient;
  const context={workspaceName:'test',platforms:[{id:'macos' as const,label:'macOS'}],onWorkspaceChange:vi.fn()};
  const {result}=renderHook(()=>useDeviceWorkspace(context,client));
  await waitFor(()=>expect(result.current.bootstrap.state).toBe('ready'));
  act(()=>{result.current.setPlatform('macos');result.current.setGoal('Keep my Goal');});
  await waitFor(()=>expect(result.current.canStart).toBe(true));
  const refresh=deferred<ReadinessResponse>();
  vi.mocked(client.readiness).mockReturnValueOnce(refresh.promise);
  act(()=>result.current.refresh());
  expect(result.current.canStart).toBe(false);
  expect(result.current.goal).toBe('Keep my Goal');
  await act(async()=>refresh.resolve(mac));
  await waitFor(()=>expect(result.current.canStart).toBe(true));
  act(()=>{void result.current.start();void result.current.start();});
  expect(client.startRun).toHaveBeenCalledTimes(1);
  expect(result.current.controlsLocked).toBe(true);
  expect(result.current.starting).toBe(true);
});

it('does not discover until a platform is explicitly selected', async () => {
  const client = {
    bootstrap: vi.fn().mockResolvedValue(bootstrap), readiness: vi.fn(), targets: vi.fn(), cases: vi.fn(),
    startRun: vi.fn(), cancelRun: vi.fn(), runSnapshot: vi.fn(), streamUrl: vi.fn(), screenUrl: vi.fn(), uiSnapshot: vi.fn(),
  } as unknown as ControlPlaneClient;
  const { result } = renderHook(() => useDeviceWorkspace(deviceContext, client));
  await waitFor(() => expect(result.current.bootstrap.state).toBe('ready'));

  expect(result.current.platform).toBe('');
  expect(client.readiness).not.toHaveBeenCalled();
  expect(client.targets).not.toHaveBeenCalled();
  expect(client.cases).not.toHaveBeenCalled();
});

it('applies a strict replay intent after validating current case discovery', async () => {
  const onLaunchIntentConsumed = vi.fn();
  const client = {
    bootstrap: vi.fn().mockResolvedValue(bootstrap), readiness: vi.fn().mockResolvedValue(readiness('web')),
    targets: vi.fn().mockResolvedValue(targets('web')), cases: vi.fn().mockResolvedValue(cases('web')),
    startRun: vi.fn(), cancelRun: vi.fn(), runSnapshot: vi.fn(), streamUrl: vi.fn(), screenUrl: vi.fn(), uiSnapshot: vi.fn(),
  } as unknown as ControlPlaneClient;
  const { result } = renderHook(() => useDeviceWorkspace({
    ...deviceContext,
    launchIntent: { id: 1, mode: 'strict', workspaceName: 'test', platform: 'web', casePath: 'web.fsq.yaml' },
    onLaunchIntentConsumed,
  }, client));

  await waitFor(() => expect(result.current.cases.state).toBe('ready'));
  expect(result.current.platform).toBe('web');
  expect(result.current.mode).toBe('strict');
  expect(result.current.casePath).toBe('web.fsq.yaml');
  expect(onLaunchIntentConsumed).toHaveBeenCalledWith(1);
});

it('waits for workspace platform options before consuming a strict replay intent', async () => {
  const onLaunchIntentConsumed = vi.fn();
  const client = {
    bootstrap: vi.fn().mockResolvedValue(bootstrap), readiness: vi.fn().mockResolvedValue(readiness('web')),
    targets: vi.fn().mockResolvedValue(targets('web')), cases: vi.fn().mockResolvedValue(cases('web')),
    startRun: vi.fn(), cancelRun: vi.fn(), runSnapshot: vi.fn(), streamUrl: vi.fn(), screenUrl: vi.fn(), uiSnapshot: vi.fn(),
  } as unknown as ControlPlaneClient;
  const launchIntent = { id: 5, mode: 'strict' as const, workspaceName: 'test', platform: 'web' as const, casePath: 'web.fsq.yaml' };
  const { result, rerender } = renderHook(
    ({ currentPlatforms, platformsReady }) => useDeviceWorkspace({
      ...deviceContext,
      platforms: currentPlatforms,
      platformsReady,
      launchIntent,
      onLaunchIntentConsumed,
    }, client),
    { initialProps: { currentPlatforms: [] as typeof platforms, platformsReady: false } },
  );

  await waitFor(() => expect(result.current.bootstrap.state).toBe('ready'));
  expect(onLaunchIntentConsumed).not.toHaveBeenCalled();
  expect(client.cases).not.toHaveBeenCalled();

  rerender({ currentPlatforms: platforms, platformsReady: true });

  await waitFor(() => expect(result.current.cases.state).toBe('ready'));
  expect(result.current.platform).toBe('web');
  expect(result.current.casePath).toBe('web.fsq.yaml');
  expect(onLaunchIntentConsumed).toHaveBeenCalledWith(5);
});

it('keeps Strict Replay empty when an intended case is no longer selectable', async () => {
  const unavailableCases = { ...cases('web'), cases: [{ ...cases('web').cases[0], selectable: false }] };
  const client = {
    bootstrap: vi.fn().mockResolvedValue(bootstrap), readiness: vi.fn().mockResolvedValue(readiness('web')),
    targets: vi.fn().mockResolvedValue(targets('web')), cases: vi.fn().mockResolvedValue(unavailableCases),
    startRun: vi.fn(), cancelRun: vi.fn(), runSnapshot: vi.fn(), streamUrl: vi.fn(), screenUrl: vi.fn(), uiSnapshot: vi.fn(),
  } as unknown as ControlPlaneClient;
  const { result } = renderHook(() => useDeviceWorkspace({
    ...deviceContext,
    launchIntent: { id: 2, mode: 'strict', workspaceName: 'test', platform: 'web', casePath: 'web.fsq.yaml' },
    onLaunchIntentConsumed: vi.fn(),
  }, client));

  await waitFor(() => expect(result.current.cases.state).toBe('ready'));
  expect(result.current.platform).toBe('web');
  expect(result.current.mode).toBe('strict');
  expect(result.current.casePath).toBe('');
  expect(result.current.canStart).toBe(false);
});

it('keeps Strict Replay empty when an intended case is missing from discovery', async () => {
  const client = {
    bootstrap: vi.fn().mockResolvedValue(bootstrap), readiness: vi.fn().mockResolvedValue(readiness('web')),
    targets: vi.fn().mockResolvedValue(targets('web')), cases: vi.fn().mockResolvedValue({ ...cases('web'), cases: [] }),
    startRun: vi.fn(), cancelRun: vi.fn(), runSnapshot: vi.fn(), streamUrl: vi.fn(), screenUrl: vi.fn(), uiSnapshot: vi.fn(),
  } as unknown as ControlPlaneClient;
  const { result } = renderHook(() => useDeviceWorkspace({
    ...deviceContext,
    launchIntent: { id: 6, mode: 'strict', workspaceName: 'test', platform: 'web', casePath: 'missing.fsq.yaml' },
    onLaunchIntentConsumed: vi.fn(),
  }, client));

  await waitFor(() => expect(result.current.cases.state).toBe('ready'));
  expect(result.current.platform).toBe('web');
  expect(result.current.mode).toBe('strict');
  expect(result.current.casePath).toBe('');
  expect(result.current.canStart).toBe(false);
});

it('does not force an intended platform that is unavailable for the workspace', async () => {
  const client = {
    bootstrap: vi.fn().mockResolvedValue(bootstrap), readiness: vi.fn(), targets: vi.fn(), cases: vi.fn(),
    startRun: vi.fn(), cancelRun: vi.fn(), runSnapshot: vi.fn(), streamUrl: vi.fn(), screenUrl: vi.fn(), uiSnapshot: vi.fn(),
  } as unknown as ControlPlaneClient;
  const { result } = renderHook(() => useDeviceWorkspace({
    ...deviceContext,
    launchIntent: { id: 4, mode: 'strict', workspaceName: 'test', platform: 'macos', casePath: 'desktop.fsq.yaml' },
    onLaunchIntentConsumed: vi.fn(),
  }, client));

  await waitFor(() => expect(result.current.bootstrap.state).toBe('ready'));
  expect(result.current.mode).toBe('strict');
  expect(result.current.platform).toBe('');
  expect(result.current.casePath).toBe('');
  expect(client.cases).not.toHaveBeenCalled();
});

it('restores an active task instead of applying a Workspace launch intent', async () => {
  const activeBootstrap: BootstrapResponse = {
    ...bootstrap,
    busy: true,
    activeTask: { requestId: 'active-request', runId: null, workspaceName: 'test', platform: 'android', targetId: 'android-target', mode: 'explore', status: 'running' },
  };
  const client = {
    bootstrap: vi.fn().mockResolvedValue(activeBootstrap), readiness: vi.fn().mockResolvedValue(readiness('android')),
    targets: vi.fn().mockResolvedValue(targets('android')), cases: vi.fn().mockResolvedValue(cases('android')),
    startRun: vi.fn(), cancelRun: vi.fn(), runSnapshot: vi.fn().mockResolvedValue(runSnapshot('active-request')), streamUrl: vi.fn(), screenUrl: vi.fn(), uiSnapshot: vi.fn(),
  } as unknown as ControlPlaneClient;
  const { result } = renderHook(() => useDeviceWorkspace({
    ...deviceContext,
    launchIntent: { id: 3, mode: 'strict', workspaceName: 'test', platform: 'web', casePath: 'web.fsq.yaml' },
    onLaunchIntentConsumed: vi.fn(),
  }, client));

  await waitFor(() => expect(result.current.requestId).toBe('active-request'));
  expect(result.current.platform).toBe('android');
  expect(result.current.mode).toBe('explore');
});

it('rejects stale platform responses by request generation', async () => {
  const oldReadiness = deferred<ReadinessResponse>();
  const client = {
    bootstrap: vi.fn().mockResolvedValue(bootstrap),
    readiness: vi.fn((_workspace: string, platform: 'android' | 'web') => platform === 'android' ? oldReadiness.promise : Promise.resolve(readiness('web'))),
    targets: vi.fn((_workspace: string, platform: 'android' | 'web') => Promise.resolve(targets(platform))),
    cases: vi.fn((_workspace: string, platform: 'android' | 'web') => Promise.resolve(cases(platform))),
    startRun: vi.fn(), cancelRun: vi.fn(), runSnapshot: vi.fn(), streamUrl: vi.fn(), screenUrl: vi.fn(), uiSnapshot: vi.fn(),
  } as unknown as ControlPlaneClient;
  const { result } = renderHook(() => useDeviceWorkspace(deviceContext, client));
  act(() => result.current.setPlatform('android'));
  await waitFor(() => expect(result.current.targets.data?.platform).toBe('android'));
  act(() => result.current.setPlatform('web'));
  await waitFor(() => expect(result.current.readiness.data?.platformId).toBe('web'));
  act(() => oldReadiness.resolve(readiness('android')));
  await act(async () => Promise.resolve());
  expect(result.current.readiness.data?.platformId).toBe('web');
});

it('clears platform selection on workspace change when multiple platforms remain available', async () => {
  const client = {
    bootstrap: vi.fn().mockResolvedValue(bootstrap), readiness: vi.fn().mockResolvedValue(readiness('android')),
    targets: vi.fn().mockResolvedValue(targets('android')), cases: vi.fn().mockResolvedValue(cases('android')),
    startRun: vi.fn(), cancelRun: vi.fn(), runSnapshot: vi.fn(), streamUrl: vi.fn(), screenUrl: vi.fn(), uiSnapshot: vi.fn(),
  } as unknown as ControlPlaneClient;
  const { result, rerender } = renderHook(
    ({ workspaceName }) => useDeviceWorkspace({ ...deviceContext, workspaceName }, client),
    { initialProps: { workspaceName: 'first' } },
  );
  act(() => result.current.setPlatform('android'));
  await waitFor(() => expect(result.current.targets.state).toBe('ready'));

  rerender({ workspaceName: 'second' });

  await waitFor(() => expect(result.current.platform).toBe(''));
  expect(result.current.targets.state).toBe('idle');
});

it('derives start eligibility and sends mode-specific strict payloads', async () => {
  const startRun = vi.fn().mockResolvedValue({ requestId: 'request-1' });
  const client = {
    bootstrap: vi.fn().mockResolvedValue(bootstrap), readiness: vi.fn().mockResolvedValue(readiness('android')),
    targets: vi.fn().mockResolvedValue(targets('android')), cases: vi.fn().mockResolvedValue(cases('android')),
    startRun, cancelRun: vi.fn(), runSnapshot: vi.fn().mockResolvedValue(runSnapshot()), streamUrl: vi.fn(), screenUrl: vi.fn(), uiSnapshot: vi.fn(),
  } as unknown as ControlPlaneClient;
  globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ requestId: 'request-1', runId: null, workspaceName: 'test', platform: 'android', targetId: 'android-target', mode: 'strict', status: 'preparing', source: { casePath: 'android.fsq.yaml' }, startedAt: '', completedAt: null, cancelRequested: false, events: [], activeStep: null, result: null, summary: 'Preparing', screenshotRevision: 0, uiSnapshotRevision: 0, evidenceAvailable: false, reportAvailable: false, terminal: false }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
  const { result } = renderHook(() => useDeviceWorkspace(deviceContext, client));
  act(() => result.current.setPlatform('android'));
  await waitFor(() => expect(result.current.targets.state).toBe('ready'));
  expect(result.current.canStart).toBe(false);
  act(() => result.current.setMode('strict'));
  expect(result.current.casePath).toBe('');
  expect(result.current.selectedCase).toBeNull();
  expect(result.current.canStart).toBe(false);
  act(() => result.current.setCasePath('android.fsq.yaml'));
  await waitFor(() => expect(result.current.canStart).toBe(true));
  await act(async () => result.current.start());
  expect(startRun).toHaveBeenCalledWith({ mode: 'strict', workspaceName: 'test', platform: 'android', targetId: 'android-target', casePath: 'android.fsq.yaml' });
  expect(result.current.controlsLocked).toBe(true);
});

it('requires provider readiness only for provider-gated strict cases', async () => {
  const unavailableProvider = { ...readiness('android'), provider: { status: 'unavailable' as const, message: 'setup', action: 'configure' } };
  const baseCase = cases('android').cases[0];
  const providerCases = { ...cases('android'), cases: [{ ...baseCase, path: 'gated.fsq.yaml', requiresAiAssertion: true }, { ...baseCase, path: 'free.fsq.yaml' }] };
  const client = {
    bootstrap: vi.fn().mockResolvedValue(bootstrap), readiness: vi.fn().mockResolvedValue(unavailableProvider),
    targets: vi.fn().mockResolvedValue(targets('android')), cases: vi.fn().mockResolvedValue(providerCases),
    startRun: vi.fn(), cancelRun: vi.fn(), runSnapshot: vi.fn(), streamUrl: vi.fn(), screenUrl: vi.fn(), screen: vi.fn(), uiSnapshot: vi.fn(),
  } as unknown as ControlPlaneClient;
  const { result } = renderHook(() => useDeviceWorkspace(deviceContext, client));
  act(() => result.current.setPlatform('android'));
  await waitFor(() => expect(result.current.cases.state).toBe('ready'));
  act(() => result.current.setMode('strict'));
  expect(result.current.selectedCase).toBeNull();
  expect(result.current.canStart).toBe(false);

  act(() => result.current.setCasePath('gated.fsq.yaml'));
  expect(result.current.selectedCase?.requiresAiAssertion).toBe(true);
  expect(result.current.canStart).toBe(false);

  act(() => result.current.setCasePath('free.fsq.yaml'));
  expect(result.current.canStart).toBe(true);
});

it('allows Explore and sends its payload without waiting for case discovery', async () => {
  const pendingCases = deferred<CasesResponse>();
  const startRun = vi.fn().mockResolvedValue({ requestId: 'explore-request' });
  const client = {
    bootstrap: vi.fn().mockResolvedValue(bootstrap), readiness: vi.fn().mockResolvedValue(readiness('android')),
    targets: vi.fn().mockResolvedValue(targets('android')), cases: vi.fn().mockReturnValue(pendingCases.promise),
    startRun, cancelRun: vi.fn(), runSnapshot: vi.fn().mockResolvedValue(runSnapshot('explore-request')), streamUrl: vi.fn(), screenUrl: vi.fn(), uiSnapshot: vi.fn(),
  } as unknown as ControlPlaneClient;
  const { result } = renderHook(() => useDeviceWorkspace(deviceContext, client));
  act(() => result.current.setPlatform('android'));
  await waitFor(() => expect(result.current.targets.state).toBe('ready'));
  act(() => result.current.setGoal('  Verify the welcome page  '));

  await waitFor(() => expect(result.current.canStart).toBe(true));
  expect(result.current.cases.state).toBe('loading');
  await act(async () => result.current.start());
  expect(startRun).toHaveBeenCalledWith({ mode: 'explore', workspaceName: 'test', platform: 'android', targetId: 'android-target', goal: 'Verify the welcome page' });
});

it('allows Explore with an empty case result while Strict has no selectable source', async () => {
  const startRun = vi.fn().mockResolvedValue({ requestId: 'explore-request' });
  const emptyCases: CasesResponse = { platform: 'android', cases: [], truncated: false };
  const client = {
    bootstrap: vi.fn().mockResolvedValue(bootstrap), readiness: vi.fn().mockResolvedValue(readiness('android')),
    targets: vi.fn().mockResolvedValue(targets('android')), cases: vi.fn().mockResolvedValue(emptyCases),
    startRun, cancelRun: vi.fn(), runSnapshot: vi.fn().mockResolvedValue(runSnapshot('explore-request')), streamUrl: vi.fn(), screenUrl: vi.fn(), uiSnapshot: vi.fn(),
  } as unknown as ControlPlaneClient;
  const { result } = renderHook(() => useDeviceWorkspace(deviceContext, client));
  act(() => result.current.setPlatform('android'));
  await waitFor(() => expect(result.current.cases.state).toBe('ready'));
  act(() => result.current.setGoal('Verify without authored cases'));

  expect(result.current.canStart).toBe(true);
  act(() => result.current.setMode('strict'));
  expect(result.current.selectedCase).toBeNull();
  expect(result.current.canStart).toBe(false);

  act(() => result.current.setMode('explore'));
  await act(async () => result.current.start());
  expect(startRun).toHaveBeenCalledWith({ mode: 'explore', workspaceName: 'test', platform: 'android', targetId: 'android-target', goal: 'Verify without authored cases' });
});

it('respects backend bootstrap truth when starting a new run', async () => {
  const activeBootstrap: BootstrapResponse = {
    ...bootstrap, busy: true,
    activeTask: { requestId: 'other-request', runId: null, workspaceName: 'test', platform: 'web', targetId: 'web-target', mode: 'strict', status: 'running' },
  };
  const bootstrapCall = vi.fn()
    .mockResolvedValueOnce(bootstrap)
    .mockResolvedValueOnce(activeBootstrap)
    .mockResolvedValueOnce(bootstrap);
  const client = {
    bootstrap: bootstrapCall, readiness: vi.fn((_workspace: string, platform: 'android' | 'web') => Promise.resolve(readiness(platform))),
    targets: vi.fn((_workspace: string, platform: 'android' | 'web') => Promise.resolve(targets(platform))), cases: vi.fn((_workspace: string, platform: 'android' | 'web') => Promise.resolve(cases(platform))),
    startRun: vi.fn(), cancelRun: vi.fn(), runSnapshot: vi.fn().mockResolvedValue(runSnapshot('other-request', 'web')), streamUrl: vi.fn(), screenUrl: vi.fn(), uiSnapshot: vi.fn(),
  } as unknown as ControlPlaneClient;
  const { result } = renderHook(() => useDeviceWorkspace(deviceContext, client));
  await waitFor(() => expect(result.current.bootstrap.state).toBe('ready'));

  await act(async () => result.current.newRun());
  await waitFor(() => expect(result.current.requestId).toBe('other-request'));
  expect(result.current.bootstrap.data?.busy).toBe(true);
  expect(result.current.platform).toBe('web');

  await act(async () => result.current.newRun());
  await waitFor(() => expect(result.current.requestId).toBeNull());
  expect(result.current.bootstrap.data?.busy).toBe(false);
});
