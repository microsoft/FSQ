import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { controlPlaneClient, toApiError, type ControlPlaneClient } from '../../../api/controlPlaneClient';
import type {
  ApiErrorBody,
  BootstrapResponse,
  CaseRecord,
  CasesResponse,
  EvidenceTab,
  PlatformId,
  ReadinessResponse,
  RequestResource,
  RunMode,
  StartRunPayload,
  TargetsResponse,
  PlatformOption,
} from '../../../api/types';
import { useRunStream } from './useRunStream';

const emptyResource = <T,>(): RequestResource<T> => ({ state: 'idle', data: null, error: null });
const loadingResource = <T,>(previous: T | null = null): RequestResource<T> => ({ state: 'loading', data: previous, error: null });

interface DeviceWorkspaceContext {
  workspaceName: string | null;
  platforms: readonly PlatformOption[];
  platformsReady?: boolean;
  onWorkspaceChange: (workspaceName: string) => void;
  launchIntent?: DevicesLaunchIntent | null;
  onLaunchIntentConsumed?: (intentId: number) => void;
  onStartPendingChange?: (pending: boolean) => void;
}

export type DevicesLaunchIntent =
  | { id: number; mode: 'explore'; workspaceName: string }
  | { id: number; mode: 'strict'; workspaceName: string; platform: PlatformId; casePath: string }
  | { id: number; mode: 'diagnostic'; workspaceName: string; platform:'macos' };

export function useDeviceWorkspace(context: DeviceWorkspaceContext, client: ControlPlaneClient = controlPlaneClient) {
  const { workspaceName, platforms, platformsReady = true, onWorkspaceChange, launchIntent, onLaunchIntentConsumed, onStartPendingChange } = context;
  const [bootstrap, setBootstrap] = useState<RequestResource<BootstrapResponse>>(emptyResource);
  const [platform, setPlatformState] = useState<PlatformId | ''>('');
  const [targetId, setTargetId] = useState('');
  const targetRef = useRef('');
  useEffect(() => {targetRef.current=targetId;}, [targetId]);
  const diagnosisGeneration = useRef(0);
  const diagnosisController = useRef<AbortController | null>(null);
  const [mode, setMode] = useState<RunMode>('explore');
  const [goal, setGoal] = useState('');
  const [casePath, setCasePath] = useState('');
  const [readiness, setReadiness] = useState<RequestResource<ReadinessResponse>>(emptyResource);
  const [targets, setTargets] = useState<RequestResource<TargetsResponse>>(emptyResource);
  const [cases, setCases] = useState<RequestResource<CasesResponse>>(emptyResource);
  const [requestId, setRequestId] = useState<string | null>(null);
  const [startError, setStartError] = useState<ApiErrorBody | null>(null);
  const [starting,setStarting] = useState(false);
  const startPendingRef = useRef(false);
  const [saveYamlState, setSaveYamlState] = useState<RequestResource<{ savedPath: string; message: string }>>(emptyResource);
  const [evidenceTab, setEvidenceTab] = useState<EvidenceTab>('screen');
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);
  const generationRef = useRef(0);
  const previousWorkspaceRef = useRef<string | null>(workspaceName);
  const discoveryControllerRef = useRef<AbortController | null>(null);
  const pendingCasePathRef = useRef<string | null>(null);
  const pendingDiagnosticWorkspaceRef = useRef<string | null>(null);
  const consumedLaunchIntentRef = useRef<number | null>(null);
  const { snapshot, connection, error: streamError } = useRunStream(requestId, client);

  const loadAndroidDiagnosis = useCallback((selectedWorkspace: string, selectedTarget: string) => {
    diagnosisController.current?.abort();
    const controller = new AbortController();
    diagnosisController.current = controller;
    const generation = ++diagnosisGeneration.current;
    setReadiness(loadingResource());
    void client.readiness(selectedWorkspace, 'android', controller.signal, selectedTarget || null).then(data => {
      if (controller.signal.aborted || generation !== diagnosisGeneration.current) return;
      if (selectedTarget && data.targetId !== selectedTarget) {
        setReadiness({state:'error',data:null,error:{code:'device_binding_mismatch',message:'Diagnosis does not match the selected device.',action:'Recheck environment.'}});
        return;
      }
      setReadiness({state:'ready',data,error:null});
    }).catch(error => {if (!controller.signal.aborted && generation===diagnosisGeneration.current) setReadiness({state:'error',data:null,error:toApiError(error)});});
  }, [client]);

  useEffect(() => () => { diagnosisController.current?.abort(); ++diagnosisGeneration.current; }, [workspaceName, platform]);

  const loadDiscovery = useCallback((selectedWorkspace: string, selectedPlatform: PlatformId, clearSelection: boolean) => {
    diagnosisController.current?.abort();
    ++diagnosisGeneration.current;
    discoveryControllerRef.current?.abort();
    const controller = new AbortController();
    discoveryControllerRef.current = controller;
    const generation = ++generationRef.current;
    if (clearSelection) { targetRef.current=''; setTargetId(''); setCasePath(''); }
    setReadiness((value) => loadingResource(!clearSelection && value.data?.workspaceName === selectedWorkspace && value.data.platformId === selectedPlatform ? value.data : null));
    setTargets((value) => loadingResource(!clearSelection && value.data?.platform === selectedPlatform ? value.data : null));
    setCases((value) => loadingResource(!clearSelection && value.data?.platform === selectedPlatform ? value.data : null));

    const applies = () => generationRef.current === generation && !controller.signal.aborted;
    if (selectedPlatform !== 'android') void client.readiness(selectedWorkspace, selectedPlatform, controller.signal).then((data) => { if (applies()) setReadiness({ state: 'ready', data, error: null }); }).catch((error) => { if (applies()) setReadiness({ state: 'error', data: null, error: toApiError(error) }); });
    void client.targets(selectedWorkspace, selectedPlatform, controller.signal).then((data) => {
      if (!applies()) return;
      setTargets({ state: 'ready', data, error: null });
      if (selectedPlatform === 'android') {
        const next = targetRef.current || data.targets.find(target=>target.selectable && target.isDefault)?.id || '';
        targetRef.current=next;
        setTargetId(next);
        loadAndroidDiagnosis(selectedWorkspace,next);
        return;
      }
      setTargetId((current) => {
        if (data.targets.some((target) => target.id === current && target.selectable)) return current;
        return data.targets.find((target) => target.selectable && target.isDefault)?.id ?? data.targets.find((target) => target.selectable)?.id ?? '';
      });
    }).catch((error) => { if (applies()) {setTargets({ state: 'error', data: null, error: toApiError(error) }); if(selectedPlatform==='android') loadAndroidDiagnosis(selectedWorkspace,targetRef.current);} });
    void client.cases(selectedWorkspace, selectedPlatform, controller.signal).then((data) => {
      if (!applies()) return;
      setCases({ state: 'ready', data, error: null });
      setCasePath((current) => {
        const candidate = pendingCasePathRef.current ?? current;
        if (pendingCasePathRef.current !== null) pendingCasePathRef.current = null;
        return data.cases.some((item) => item.path === candidate && item.selectable) ? candidate : '';
      });
    }).catch((error) => { if (applies()) setCases({ state: 'error', data: null, error: toApiError(error) }); });
  }, [client, loadAndroidDiagnosis]);

  useEffect(() => {
    const controller = new AbortController();
    setBootstrap(loadingResource());
    void client.bootstrap(controller.signal).then((data) => {
      setBootstrap({ state: 'ready', data, error: null });
      if (data.activeTask) {
        onWorkspaceChange(data.activeTask.workspaceName);
        setPlatformState(data.activeTask.platform);
        setTargetId(data.activeTask.targetId);
        setMode(data.activeTask.mode);
        setRequestId(data.activeTask.requestId);
      }
    }).catch((error) => { if (!controller.signal.aborted) setBootstrap({ state: 'error', data: null, error: toApiError(error) }); });
    return () => controller.abort();
  }, [client, onWorkspaceChange]);

  useEffect(() => {
    if (!launchIntent || bootstrap.state !== 'ready' || consumedLaunchIntentRef.current === launchIntent.id) return;
    if ((launchIntent.mode === 'strict' || launchIntent.mode==='diagnostic') && launchIntent.workspaceName === workspaceName && !platformsReady) {
      setMode(launchIntent.mode==='strict'?'strict':'explore');
      return;
    }
    consumedLaunchIntentRef.current = launchIntent.id;
    onLaunchIntentConsumed?.(launchIntent.id);
    if (bootstrap.data?.activeTask) {
      pendingCasePathRef.current = null;
      return;
    }
    setMode(launchIntent.mode==='diagnostic'?'explore':launchIntent.mode);
    if (launchIntent.mode==='diagnostic' && launchIntent.workspaceName===workspaceName && platforms.some(item=>item.id==='macos')) {
      pendingDiagnosticWorkspaceRef.current=workspaceName;
      setPlatformState('macos');
      return;
    }
    if (launchIntent.mode === 'strict' && launchIntent.workspaceName === workspaceName && platforms.some((item) => item.id === launchIntent.platform)) {
      pendingCasePathRef.current = launchIntent.casePath;
      setPlatformState(launchIntent.platform);
      return;
    }
    pendingCasePathRef.current = null;
    setPlatformState('');
  }, [bootstrap.data?.activeTask, bootstrap.state, launchIntent, onLaunchIntentConsumed, platforms, platformsReady, workspaceName]);

  useEffect(() => {
    const workspaceChanged = previousWorkspaceRef.current !== workspaceName;
    previousWorkspaceRef.current = workspaceName;
    discoveryControllerRef.current?.abort();
    generationRef.current += 1;
    setTargetId('');
    setCasePath('');
    setReadiness(emptyResource());
    setTargets(emptyResource());
    setCases(emptyResource());
    setStartError(null);
    if (workspaceChanged) pendingCasePathRef.current = null;
    const activeTask = bootstrap.data?.activeTask;
    const restoringActiveTask = activeTask?.workspaceName === workspaceName && activeTask.platform === platform;
    const platformAvailable = Boolean(platform && platforms.some((item) => item.id === platform));
    const explicitDiagnosis = pendingDiagnosticWorkspaceRef.current === workspaceName && platforms.some(item=>item.id==='macos');
    if (!workspaceName || (workspaceChanged && !restoringActiveTask && !explicitDiagnosis) || (platform && !platformAvailable && !explicitDiagnosis)) {
      pendingDiagnosticWorkspaceRef.current=null;
      setPlatformState('');
      return;
    }
    if (explicitDiagnosis && platform !== 'macos') {setPlatformState('macos');return;}
    pendingDiagnosticWorkspaceRef.current=null;
    if (!platform) return;
    loadDiscovery(workspaceName, platform, true);
    return () => discoveryControllerRef.current?.abort();
  }, [bootstrap.data?.activeTask, loadDiscovery, platform, platforms, workspaceName]);

  useEffect(() => {
    if (!snapshot) return;
    pendingCasePathRef.current = null;
    onWorkspaceChange(snapshot.workspaceName);
    setPlatformState(snapshot.platform);
    setTargetId(snapshot.targetId);
    setMode(snapshot.mode);
    if (!snapshot.terminal) setSelectedStepId(null);
    if (!snapshot.terminal || snapshot.mode !== 'explore') setSaveYamlState(emptyResource());
  }, [onWorkspaceChange, snapshot]);

  useEffect(() => {
    if (streamError?.code !== 'run_ended') return;
    setRequestId(null);
    setStartError(streamError);
    void client.bootstrap().then((data) => {
      setBootstrap({ state: 'ready', data, error: null });
      if (data.activeTask) {
        onWorkspaceChange(data.activeTask.workspaceName);
        setPlatformState(data.activeTask.platform);
        setTargetId(data.activeTask.targetId);
        setMode(data.activeTask.mode);
        setRequestId(data.activeTask.requestId);
      }
    }).catch((error) => setBootstrap({ state: 'error', data: null, error: toApiError(error) }));
  }, [client, onWorkspaceChange, streamError]);

  const active = Boolean(snapshot && !snapshot.terminal) || Boolean(requestId && !snapshot);
  const controlsLocked = active || starting;
  const selectedTarget = targets.data?.targets.find((target) => target.id === targetId) ?? null;
  const selectedCase: CaseRecord | null = cases.data?.cases.find((item) => item.path === casePath) ?? null;
  const commonReady = readiness.state === 'ready' && targets.state === 'ready'
    && Boolean(workspaceName && platform)
    && readiness.data?.workspace.status === 'ready'
    && readiness.data?.platform.status === 'ready'
    && readiness.data.target.status === 'ready'
    && (platform !== 'android' || readiness.data.targetId === targetId)
    && selectedTarget?.selectable === true;
  const sourceReady = mode === 'explore'
    ? goal.trim().length > 0 && readiness.data?.provider.status === 'ready'
    : cases.state === 'ready'
      && selectedCase?.selectable === true
      && readiness.data?.strict.status === 'ready'
      && (!selectedCase.requiresAiAssertion || readiness.data.provider.status === 'ready');
  const macosVerdict = mode==='explore'?readiness.data?.commands?.caseCreate:readiness.data?.commands?.caseTest;
  const canStart = Boolean(commonReady && sourceReady && !active && !starting && bootstrap.state === 'ready' && !bootstrap.data?.busy && (!['macos','android'].includes(platform)||macosVerdict?.status==='ready'));
  const blockedReason = (() => {
    if (starting) return 'Checking the environment before starting.';
    if (!workspaceName || !platform) return 'Select a Workspace and platform.';
    if (readiness.state === 'loading') return 'Wait for the environment check to finish.';
    if (readiness.state !== 'ready') return readiness.error?.message || 'Recheck the environment to obtain current results.';
    if (targets.state === 'loading') return 'Wait for target discovery to finish.';
    if (targets.state !== 'ready') return targets.error?.message || 'Refresh target discovery.';
    if (['macos','android'].includes(platform) && macosVerdict?.status !== 'ready') return macosVerdict?.message || 'Environment readiness has not been confirmed.';
    if (!selectedTarget?.selectable) return 'Select an available target.';
    if (mode === 'strict') {
      if (cases.state === 'loading') return 'Wait for Case discovery to finish.';
      if (cases.state !== 'ready') return cases.error?.message || 'Refresh Case discovery.';
      if (!selectedCase?.selectable) return 'Select a validated Case.';
    } else if (!goal.trim()) return 'Enter a Goal.';
    if ((mode === 'explore' || selectedCase?.requiresAiAssertion) && readiness.data?.provider.status !== 'ready') return readiness.data?.provider.message || 'Configure the required Provider.';
    if (bootstrap.state !== 'ready') return 'Wait for Control Plane to become ready.';
    if (active || bootstrap.data?.busy) return 'Another test is active.';
    return '';
  })();

  const setPlatform = (next: PlatformId | '') => {
    if (!controlsLocked && (!next || platforms.some((item) => item.id === next))) {
      pendingCasePathRef.current = null;
      setPlatformState(next);
    }
  };
  const selectTarget = (next: string) => {
    if (controlsLocked) return;
    targetRef.current=next;
    setTargetId(next);
    if (platform==='android' && workspaceName) loadAndroidDiagnosis(workspaceName,next);
  };
  const refresh = () => { if (!controlsLocked && readiness.state!=='loading' && workspaceName && platform) {setStartError(null);loadDiscovery(workspaceName, platform, false);} };
  const start = async () => {
    if (!canStart || startPendingRef.current || !selectedTarget || !workspaceName || !platform) return;
    startPendingRef.current=true;
    setStarting(true);
    onStartPendingChange?.(true);
    setStartError(null);
    const payload: StartRunPayload = mode === 'explore'
      ? { mode: 'explore', workspaceName, platform, targetId: selectedTarget.id, goal: goal.trim() }
      : { mode: 'strict', workspaceName, platform, targetId: selectedTarget.id, casePath };
    try {
      const response = await client.startRun(payload);
      setSaveYamlState(emptyResource());
      setRequestId(response.requestId);
    } catch (error) {
      setStartError(toApiError(error));
      loadDiscovery(workspaceName, platform, false);
    } finally { startPendingRef.current=false; setStarting(false); onStartPendingChange?.(false); }
  };
  const cancel = async () => {
    if (!requestId) return;
    try { await client.cancelRun(requestId); }
    catch (error) { setStartError(toApiError(error)); }
  };
  const newRun = () => {
    setStartError(null);
    setSaveYamlState(emptyResource());
    setEvidenceTab('screen');
    setSelectedStepId(null);
    return client.bootstrap().then((data) => {
      setBootstrap({ state: 'ready', data, error: null });
      if (data.activeTask) {
        onWorkspaceChange(data.activeTask.workspaceName);
        setPlatformState(data.activeTask.platform);
        setTargetId(data.activeTask.targetId);
        setMode(data.activeTask.mode);
        setRequestId(data.activeTask.requestId);
      } else {
        setRequestId(null);
        if (workspaceName && platform) loadDiscovery(workspaceName, platform, false);
      }
    }).catch((error) => setStartError(toApiError(error)));
  };
  const saveYaml = async (caseName: string) => {
    if (!requestId || snapshot?.terminal !== true || snapshot.mode !== 'explore') return;
    setSaveYamlState(loadingResource(saveYamlState.data));
    try {
      const response = await client.saveYaml(requestId, { caseName });
      setSaveYamlState({ state: 'ready', data: response, error: null });
    } catch (error) {
      setSaveYamlState({ state: 'error', data: saveYamlState.data, error: toApiError(error) });
    }
  };

  const connectionLabel = useMemo(() => {
    if (active) return connection === 'polling' ? 'Polling' : connection === 'reconnecting' ? 'Reconnecting' : 'Live';
    if (targets.state === 'loading') return 'Discovering';
    if (['macos','android'].includes(platform) && (readiness.state!=='ready'||readiness.data?.target.status!=='ready')) return readiness.state==='loading'?'Checking':'Unavailable';
    if (selectedTarget?.selectable) return 'Ready';
    return 'Unavailable';
  }, [active, connection, selectedTarget, targets.state, platform, readiness]);

  return {
    bootstrap, workspaceName, platform, setPlatform, targetId, setTargetId: selectTarget, mode, setMode, goal, setGoal, casePath, setCasePath,
    readiness, targets, cases, selectedTarget, selectedCase, requestId, snapshot, streamError, startError,
    evidenceTab, setEvidenceTab, selectedStepId, setSelectedStepId, saveYamlState, controlsLocked, canStart, starting, blockedReason, connection, connectionLabel, refresh, start, cancel, saveYaml, newRun,
  };
}

export type DeviceWorkspace = ReturnType<typeof useDeviceWorkspace>;
