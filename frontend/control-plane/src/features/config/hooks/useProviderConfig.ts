import { useCallback, useEffect, useRef, useState } from 'react';
import { ControlPlaneApiError, controlPlaneClient, toApiError, type ControlPlaneClient } from '../../../api/controlPlaneClient';
import type {
  ApiErrorBody,
  AzureConfigPayload,
  DeepSeekConfigPayload,
  OpenAIConfigPayload,
  OpenAIModelsResponse,
  GoogleGeminiConfigPayload,
  KimiConfigPayload,
  KimiRegion,
  ConfigResponse,
  ConnectionTestResponse,
  GitHubDeviceFlowResponse,
  RequestResource,
} from '../../../api/types';

export type DeviceFlowPending = 'starting' | 'waiting' | 'loading_models' | 'retrying_models' | 'saving' | 'cancelling' | null;
export type OpenAIModelsState = { state: 'idle' | 'loading' | 'ready' | 'error'; data: OpenAIModelsResponse | null; error: ApiErrorBody | null };
export type SaveRecovery = 'none' | 'loading' | 'unavailable' | 'reconciled';
const rejectedApiKeySaves: Record<string, number> = {
  invalid_request: 400, invalid_provider_config: 400, provider_model_not_offered: 400, provider_no_eligible_models: 400,
  provider_authorization_failed: 401, provider_access_denied: 403, config_unavailable: 403, cross_origin_forbidden: 403,
  provider_rate_limited: 429, provider_timeout: 504, provider_unavailable: 503, provider_response_invalid: 502, provider_storage_unavailable: 503,
};

function providerRequestError(error: unknown, providerName: string): ApiErrorBody {
  return error instanceof ControlPlaneApiError ? error.body : { code: 'network_error', message: `The ${providerName} configuration request could not be completed.`, action: 'Check the local server and retry.' };
}
export type ConnectionResult =
  | { success: true; data: ConnectionTestResponse }
  | { success: false; error: ApiErrorBody };

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}

function isPolling(flow: GitHubDeviceFlowResponse): flow is Extract<GitHubDeviceFlowResponse, { status: 'waiting' | 'loading_models' }> {
  return flow.status === 'waiting' || flow.status === 'loading_models';
}

function isUnsaved(flow: GitHubDeviceFlowResponse): boolean {
  return ['waiting', 'loading_models', 'ready', 'model_error'].includes(flow.status);
}

export function useProviderConfig(client: ControlPlaneClient = controlPlaneClient) {
  const [config, setConfig] = useState<RequestResource<ConfigResponse>>({ state: 'loading', data: null, error: null });
  const [savePending, setSavePending] = useState(false);
  const [saveError, setSaveError] = useState<ApiErrorBody | null>(null);
  const clearSaveError = useCallback(() => setSaveError(null), []);
  const [openaiModels, setOpenAIModels] = useState<OpenAIModelsState>({ state: 'idle', data: null, error: null });
  const [deepseekModels, setDeepSeekModels] = useState<OpenAIModelsState>({ state: 'idle', data: null, error: null });
  const [geminiModels, setGeminiModels] = useState<OpenAIModelsState>({ state: 'idle', data: null, error: null });
  const deepseekModelsControllerRef = useRef<AbortController | null>(null);
  const geminiModelsControllerRef = useRef<AbortController | null>(null);
  const [kimiModels, setKimiModels] = useState<OpenAIModelsState>({ state: 'idle', data: null, error: null });
  const kimiModelsControllerRef = useRef<AbortController | null>(null);
  const kimiModelsIdentityRef = useRef('');
  const [saveRecovery, setSaveRecovery] = useState<SaveRecovery>('none');
  const recoveryBlocked = saveRecovery === 'loading' || saveRecovery === 'unavailable';
  const modelsControllerRef = useRef<AbortController | null>(null);
  const recoveryControllerRef = useRef<AbortController | null>(null);
  const [deviceFlow, setDeviceFlowState] = useState<GitHubDeviceFlowResponse | null>(null);
  const [deviceFlowPending, setDeviceFlowPending] = useState<DeviceFlowPending>(null);
  const [deviceFlowError, setDeviceFlowError] = useState<ApiErrorBody | null>(null);
  const [testPending, setTestPending] = useState(false);
  const [connectionResult, setConnectionResult] = useState<ConnectionResult | null>(null);
  const mountedRef = useRef(false);
  const configControllerRef = useRef<AbortController | null>(null);
  const saveControllerRef = useRef<AbortController | null>(null);
  const deviceControllerRef = useRef<AbortController | null>(null);
  const testControllerRef = useRef<AbortController | null>(null);
  const pollTimerRef = useRef<number | null>(null);
  const deviceGenerationRef = useRef(0);
  const deviceFlowRef = useRef<GitHubDeviceFlowResponse | null>(null);

  const setDeviceFlow = useCallback((next: GitHubDeviceFlowResponse | null) => {
    deviceFlowRef.current = next;
    setDeviceFlowState(next);
  }, []);

  const clearPoll = useCallback(() => {
    if (pollTimerRef.current !== null) window.clearTimeout(pollTimerRef.current);
    pollTimerRef.current = null;
    deviceControllerRef.current?.abort();
    deviceControllerRef.current = null;
  }, []);

  const loadConfig = useCallback(async (quiet = false) => {
    configControllerRef.current?.abort();
    const controller = new AbortController();
    configControllerRef.current = controller;
    if (!quiet) setConfig((current) => ({ state: 'loading', data: current.data, error: null }));
    try {
      const data = await client.config(controller.signal);
      if (mountedRef.current && configControllerRef.current === controller) setConfig({ state: 'ready', data, error: null });
      return data;
    } catch (error) {
      if (!isAbort(error) && mountedRef.current && configControllerRef.current === controller) {
        setConfig((current) => ({ state: 'error', data: quiet ? current.data : null, error: toApiError(error) }));
      }
      return null;
    }
  }, [client]);

  const clearOpenAIModels = useCallback(() => {
    modelsControllerRef.current?.abort();
    modelsControllerRef.current = null;
    setOpenAIModels({ state: 'idle', data: null, error: null });
  }, []);

  const loadOpenAIModels = useCallback(async (apiKey: string) => {
    modelsControllerRef.current?.abort();
    const controller = new AbortController();
    modelsControllerRef.current = controller;
    setOpenAIModels({ state: 'loading', data: null, error: null });
    try {
      const data = await client.openaiModels(apiKey.trim(), controller.signal);
      if (!mountedRef.current || modelsControllerRef.current !== controller || controller.signal.aborted) return null;
      setOpenAIModels({ state: 'ready', data, error: null });
      return data;
    } catch (error) {
      if (mountedRef.current && modelsControllerRef.current === controller && !controller.signal.aborted) {
        setOpenAIModels({ state: 'error', data: null, error: providerRequestError(error, 'OpenAI') });
      }
      return null;
    }
  }, [client]);

  const clearDeepSeekModels = useCallback(() => {
    deepseekModelsControllerRef.current?.abort();
    deepseekModelsControllerRef.current = null;
    setDeepSeekModels({ state: 'idle', data: null, error: null });
  }, []);

  const loadDeepSeekModels = useCallback(async (apiKey: string) => {
    deepseekModelsControllerRef.current?.abort();
    const controller = new AbortController();
    deepseekModelsControllerRef.current = controller;
    setDeepSeekModels({ state: 'loading', data: null, error: null });
    try {
      const data = await client.deepseekModels(apiKey.trim(), controller.signal);
      if (!mountedRef.current || deepseekModelsControllerRef.current !== controller || controller.signal.aborted) return null;
      setDeepSeekModels({ state: 'ready', data, error: null });
      return data;
    } catch (error) {
      if (mountedRef.current && deepseekModelsControllerRef.current === controller && !controller.signal.aborted) {
        setDeepSeekModels({ state: 'error', data: null, error: providerRequestError(error, 'DeepSeek') });
      }
      return null;
    }
  }, [client]);

  const clearGeminiModels = useCallback(() => {
    geminiModelsControllerRef.current?.abort();
    geminiModelsControllerRef.current = null;
    setGeminiModels({ state: 'idle', data: null, error: null });
  }, []);

  const loadGeminiModels = useCallback(async (apiKey: string) => {
    geminiModelsControllerRef.current?.abort();
    const controller = new AbortController();
    geminiModelsControllerRef.current = controller;
    setGeminiModels({ state: 'loading', data: null, error: null });
    try {
      const data = await client.googleGeminiModels(apiKey.trim(), controller.signal);
      if (!mountedRef.current || geminiModelsControllerRef.current !== controller || controller.signal.aborted) return null;
      setGeminiModels({ state: 'ready', data, error: null });
      return data;
    } catch (error) {
      if (mountedRef.current && geminiModelsControllerRef.current === controller && !controller.signal.aborted) {
        setGeminiModels({ state: 'error', data: null, error: providerRequestError(error, 'Google Gemini') });
      }
      return null;
    }
  }, [client]);

  const clearKimiModels = useCallback(() => {
    kimiModelsControllerRef.current?.abort();
    kimiModelsControllerRef.current = null;
    kimiModelsIdentityRef.current = '';
    setKimiModels({ state: 'idle', data: null, error: null });
  }, []);

  const loadKimiModels = useCallback(async (region: KimiRegion, apiKey: string) => {
    kimiModelsControllerRef.current?.abort();
    const controller = new AbortController();
    const normalizedKey = apiKey.trim();
    const identity = `${region}\0${normalizedKey}`;
    kimiModelsControllerRef.current = controller;
    kimiModelsIdentityRef.current = identity;
    setKimiModels({ state: 'loading', data: null, error: null });
    try {
      const data = await client.kimiModels({ region, apiKey: normalizedKey }, controller.signal);
      if (!mountedRef.current || kimiModelsControllerRef.current !== controller || kimiModelsIdentityRef.current !== identity || controller.signal.aborted) return null;
      setKimiModels({ state: 'ready', data, error: null });
      return data;
    } catch (error) {
      if (mountedRef.current && kimiModelsControllerRef.current === controller && kimiModelsIdentityRef.current === identity && !controller.signal.aborted) {
        setKimiModels({ state: 'error', data: null, error: providerRequestError(error, 'Kimi') });
      }
      return null;
    }
  }, [client]);

  const reconcileConfig = useCallback(async () => {
    recoveryControllerRef.current?.abort();
    configControllerRef.current?.abort();
    const controller = new AbortController();
    recoveryControllerRef.current = controller;
    setSaveRecovery('loading');
    setConfig({ state: 'loading', data: null, error: null });
    try {
      const data = await client.config(controller.signal);
      if (!mountedRef.current || recoveryControllerRef.current !== controller || controller.signal.aborted) return;
      setConfig({ state: 'ready', data, error: null });
      setSaveRecovery('reconciled');
    } catch {
      if (!mountedRef.current || recoveryControllerRef.current !== controller || controller.signal.aborted) return;
      setConfig({ state: 'error', data: null, error: { code: 'save_outcome_unknown', message: 'The current configuration could not be read.', action: 'Reload configuration before making another change.' } });
      setSaveRecovery('unavailable');
    }
  }, [client]);

  const saveApiKeyProvider = useCallback(async (
    payload: OpenAIConfigPayload | DeepSeekConfigPayload | GoogleGeminiConfigPayload | KimiConfigPayload,
    kind: 'openai' | 'deepseek' | 'google_gemini' | 'kimi',
  ) => {
    if (saveControllerRef.current || saveRecovery === 'loading' || saveRecovery === 'unavailable') return null;
    const controller = new AbortController();
    saveControllerRef.current = controller;
    setSavePending(true);
    setSaveError(null);
    setSaveRecovery('none');
    try {
      const common = { modelName: payload.modelName.trim(), apiKey: payload.apiKey.trim() };
      const data = kind === 'kimi'
        ? await client.saveKimiConfig({ ...common, region: (payload as KimiConfigPayload).region }, controller.signal)
        : kind === 'deepseek'
          ? await client.saveDeepSeekConfig(common, controller.signal)
        : kind === 'google_gemini'
          ? await client.saveGoogleGeminiConfig(common, controller.signal)
          : await client.saveOpenAIConfig(common, controller.signal);
      if (!mountedRef.current || saveControllerRef.current !== controller || controller.signal.aborted) return null;
      setConfig({ state: 'ready', data, error: null });
      return data;
    } catch (error) {
      if (!mountedRef.current || saveControllerRef.current !== controller) return null;
      if (error instanceof ControlPlaneApiError && rejectedApiKeySaves[error.body.code] === error.status) {
        setSaveError(error.body);
      } else {
        await reconcileConfig();
      }
      return null;
    } finally {
      if (saveControllerRef.current === controller) {
        saveControllerRef.current = null;
        if (mountedRef.current) setSavePending(false);
      }
    }
  }, [client, reconcileConfig, saveRecovery]);

  const saveOpenAI = useCallback((payload: OpenAIConfigPayload) => saveApiKeyProvider(payload, 'openai'), [saveApiKeyProvider]);
  const saveDeepSeek = useCallback((payload: DeepSeekConfigPayload) => saveApiKeyProvider(payload, 'deepseek'), [saveApiKeyProvider]);
  const saveGemini = useCallback((payload: GoogleGeminiConfigPayload) => saveApiKeyProvider(payload, 'google_gemini'), [saveApiKeyProvider]);
  const saveKimi = useCallback((payload: KimiConfigPayload) => saveApiKeyProvider(payload, 'kimi'), [saveApiKeyProvider]);

  const schedulePoll = useCallback((flow: GitHubDeviceFlowResponse, generation: number) => {
    if (!isPolling(flow)) return;
    const delay = Math.min(10, Math.max(1, flow.pollIntervalSeconds)) * 1000;
    pollTimerRef.current = window.setTimeout(async () => {
      const controller = new AbortController();
      deviceControllerRef.current = controller;
      try {
        const next = await client.githubDeviceFlow(flow.authRequestId, controller.signal);
        if (!mountedRef.current || generation !== deviceGenerationRef.current) return;
        setDeviceFlow(next);
        setDeviceFlowError(null);
        if (isPolling(next)) {
          setDeviceFlowPending(next.status);
          schedulePoll(next, generation);
        } else {
          setDeviceFlowPending(null);
          pollTimerRef.current = null;
        }
      } catch (error) {
        if (!isAbort(error) && mountedRef.current && generation === deviceGenerationRef.current) {
          setDeviceFlowPending(null);
          setDeviceFlowError(toApiError(error));
        }
      } finally {
        if (deviceControllerRef.current === controller) deviceControllerRef.current = null;
      }
    }, delay);
  }, [client, setDeviceFlow]);

  const saveAzure = useCallback(async (payload: AzureConfigPayload) => {
    if (saveControllerRef.current || recoveryBlocked) return null;
    const controller = new AbortController();
    saveControllerRef.current = controller;
    setSavePending(true);
    setSaveError(null);
    const normalized = {
      baseUrl: payload.baseUrl.trim(),
      modelName: payload.modelName.trim(),
      apiKey: payload.apiKey.trim(),
    };
    try {
      const data = await client.saveAzureConfig(normalized, controller.signal);
      if (mountedRef.current && saveControllerRef.current === controller) {
        setSaveRecovery('none');
        setConfig({ state: 'ready', data, error: null });
      }
      return data;
    } catch (error) {
      if (!isAbort(error) && mountedRef.current && saveControllerRef.current === controller) setSaveError(toApiError(error));
      return null;
    } finally {
      if (saveControllerRef.current === controller) {
        saveControllerRef.current = null;
        if (mountedRef.current) setSavePending(false);
      }
    }
  }, [client, recoveryBlocked]);

  const startGithub = useCallback(async () => {
    if (saveControllerRef.current || recoveryBlocked) return null;
    clearPoll();
    const generation = ++deviceGenerationRef.current;
    const controller = new AbortController();
    deviceControllerRef.current = controller;
    setDeviceFlow(null);
    setDeviceFlowError(null);
    setDeviceFlowPending('starting');
    try {
      const flow = await client.startGithubDeviceFlow(controller.signal);
      if (!mountedRef.current || generation !== deviceGenerationRef.current) return null;
      setDeviceFlow(flow);
      if (isPolling(flow)) {
        setDeviceFlowPending(flow.status);
        schedulePoll(flow, generation);
      } else {
        setDeviceFlowPending(null);
      }
      return flow;
    } catch (error) {
      if (!isAbort(error) && mountedRef.current && generation === deviceGenerationRef.current) {
        setDeviceFlowPending(null);
        setDeviceFlowError(toApiError(error));
      }
      return null;
    } finally {
      if (deviceControllerRef.current === controller) deviceControllerRef.current = null;
    }
  }, [clearPoll, client, schedulePoll, setDeviceFlow, recoveryBlocked]);

  const retryGithubModels = useCallback(async () => {
    const active = deviceFlowRef.current;
    if (!active || (active.status !== 'model_error' && !(active.status === 'ready' && active.models.length === 0))) return null;
    clearPoll();
    const generation = ++deviceGenerationRef.current;
    const controller = new AbortController();
    deviceControllerRef.current = controller;
    setDeviceFlowError(null);
    setDeviceFlowPending('retrying_models');
    try {
      const flow = await client.retryGithubModels(active.authRequestId, controller.signal);
      if (!mountedRef.current || generation !== deviceGenerationRef.current) return null;
      setDeviceFlow(flow);
      if (isPolling(flow)) {
        setDeviceFlowPending(flow.status);
        schedulePoll(flow, generation);
      } else {
        setDeviceFlowPending(null);
      }
      return flow;
    } catch (error) {
      if (!isAbort(error) && mountedRef.current && generation === deviceGenerationRef.current) {
        setDeviceFlowPending(null);
        setDeviceFlowError(toApiError(error));
      }
      return null;
    } finally {
      if (deviceControllerRef.current === controller) deviceControllerRef.current = null;
    }
  }, [clearPoll, client, schedulePoll, setDeviceFlow]);

  const saveGithubModel = useCallback(async (modelName: string) => {
    if (saveControllerRef.current || recoveryBlocked) return null;
    const active = deviceFlowRef.current;
    const selectedModel = modelName.trim();
    if (!active || active.status !== 'ready' || !selectedModel) return null;
    clearPoll();
    const generation = ++deviceGenerationRef.current;
    const controller = new AbortController();
    deviceControllerRef.current = controller;
    setDeviceFlowError(null);
    setDeviceFlowPending('saving');
    try {
      const data = await client.saveGithubModel(active.authRequestId, selectedModel, controller.signal);
      if (!mountedRef.current || generation !== deviceGenerationRef.current) return null;
      setSaveRecovery('none');
      setConfig({ state: 'ready', data, error: null });
      setDeviceFlow({ authRequestId: active.authRequestId, status: 'success', message: 'GitHub Copilot Provider saved.' });
      return data;
    } catch (error) {
      if (!isAbort(error) && mountedRef.current && generation === deviceGenerationRef.current) setDeviceFlowError(toApiError(error));
      return null;
    } finally {
      if (mountedRef.current && generation === deviceGenerationRef.current) setDeviceFlowPending(null);
      if (deviceControllerRef.current === controller) deviceControllerRef.current = null;
    }
  }, [clearPoll, client, setDeviceFlow, recoveryBlocked]);

  const cancelGithub = useCallback(async () => {
    const active = deviceFlowRef.current;
    const generation = ++deviceGenerationRef.current;
    clearPoll();
    if (!active || !isUnsaved(active)) {
      setDeviceFlow(null);
      setDeviceFlowError(null);
      setDeviceFlowPending(null);
      return;
    }
    const controller = new AbortController();
    deviceControllerRef.current = controller;
    setDeviceFlowPending('cancelling');
    try {
      const cancelled = await client.cancelGithubDeviceFlow(active.authRequestId, controller.signal);
      if (mountedRef.current && generation === deviceGenerationRef.current) {
        setDeviceFlow(cancelled);
        setDeviceFlowError(null);
      }
    } catch (error) {
      if (!isAbort(error) && mountedRef.current && generation === deviceGenerationRef.current) setDeviceFlowError(toApiError(error));
    } finally {
      if (mountedRef.current && generation === deviceGenerationRef.current) setDeviceFlowPending(null);
      if (deviceControllerRef.current === controller) deviceControllerRef.current = null;
    }
  }, [clearPoll, client, setDeviceFlow]);

  const clearDeviceFlow = useCallback(async () => {
    if (deviceFlowRef.current && isUnsaved(deviceFlowRef.current)) {
      const cancellation = cancelGithub();
      const cancellationGeneration = deviceGenerationRef.current;
      await cancellation;
      if (cancellationGeneration !== deviceGenerationRef.current) return;
    }
    ++deviceGenerationRef.current;
    clearPoll();
    setDeviceFlow(null);
    setDeviceFlowError(null);
    setDeviceFlowPending(null);
  }, [cancelGithub, clearPoll, setDeviceFlow]);

  const testSavedConnection = useCallback(async () => {
    testControllerRef.current?.abort();
    const controller = new AbortController();
    testControllerRef.current = controller;
    setTestPending(true);
    setConnectionResult(null);
    try {
      const data = await client.testConnection(controller.signal);
      if (mountedRef.current && testControllerRef.current === controller) setConnectionResult({ success: true, data });
    } catch (error) {
      if (!isAbort(error) && mountedRef.current && testControllerRef.current === controller) {
        setConnectionResult({ success: false, error: toApiError(error) });
      }
    } finally {
      if (mountedRef.current && testControllerRef.current === controller) setTestPending(false);
    }
  }, [client]);

  useEffect(() => {
    mountedRef.current = true;
    void loadConfig();
    return () => {
      mountedRef.current = false;
      ++deviceGenerationRef.current;
      configControllerRef.current?.abort();
      saveControllerRef.current?.abort();
      modelsControllerRef.current?.abort();
      deepseekModelsControllerRef.current?.abort();
      geminiModelsControllerRef.current?.abort();
      kimiModelsControllerRef.current?.abort();
      recoveryControllerRef.current?.abort();
      testControllerRef.current?.abort();
      clearPoll();
      const active = deviceFlowRef.current;
      if (active && isUnsaved(active)) void client.cancelGithubDeviceFlow(active.authRequestId);
    };
  }, [clearPoll, client, loadConfig]);

  return {
    config,
    reload: loadConfig,
    openaiModels,
    deepseekModels,
    geminiModels,
    kimiModels,
    loadDeepSeekModels,
    clearDeepSeekModels,
    saveDeepSeek,
    loadGeminiModels,
    clearGeminiModels,
    loadKimiModels,
    clearKimiModels,
    saveGemini,
    saveKimi,
    loadOpenAIModels,
    clearOpenAIModels,
    saveOpenAI,
    saveRecovery,
    reconcileConfig,
    saveAzure,
    savePending,
    saveError,
    clearSaveError,
    deviceFlow,
    deviceFlowPending,
    deviceFlowError,
    startGithub,
    retryGithubModels,
    saveGithubModel,
    cancelGithub,
    clearDeviceFlow,
    testSavedConnection,
    testPending,
    connectionResult,
    dismissConnectionResult: () => setConnectionResult(null),
  };
}