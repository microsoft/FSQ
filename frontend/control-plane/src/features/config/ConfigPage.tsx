import { useEffect, useMemo, useRef, useState } from 'react';
import type { ControlPlaneClient } from '../../api/controlPlaneClient';
import type { AzureConfigPayload, ConfigResponse, GoogleGeminiConfigPayload, KimiConfigPayload, OpenAIConfigPayload } from '../../api/types';
import { AzureConfigForm } from './components/AzureConfigForm';
import { OpenAIConfigForm } from './components/OpenAIConfigForm';
import { GoogleGeminiConfigForm } from './components/GoogleGeminiConfigForm';
import { KimiConfigForm, type KimiDraftPayload } from './components/KimiConfigForm';
import { ConnectionResultDialog } from './components/ConnectionResultDialog';
import { ProviderDialog } from './components/ProviderDialog';
import { useProviderConfig } from './hooks/useProviderConfig';

interface ConfigPageProps {
  client?: ControlPlaneClient;
  onDirtyChange?: (dirty: boolean) => void;
  onSavePendingChange?: (pending: boolean) => void;
  onSaveUncertainChange?: (uncertain: boolean) => void;
}

type ConfigDraft =
  | { type: 'openai'; payload: OpenAIConfigPayload }
  | { type: 'azure_openai'; payload: AzureConfigPayload }
  | { type: 'google_gemini'; payload: GoogleGeminiConfigPayload }
  | { type: 'kimi'; payload: KimiDraftPayload };

const emptyAzure: AzureConfigPayload = { baseUrl: '', modelName: '', apiKey: '' };

function persistedDraft(config: ConfigResponse | null): ConfigDraft | null {
  if (!config?.configured) return null;
  const provider = config.provider;
  if (provider.type === 'openai') return { type: 'openai', payload: { modelName: provider.modelName, apiKey: provider.apiKey } };
  if (provider.type === 'azure_openai') return { type: 'azure_openai', payload: { baseUrl: provider.baseUrl, modelName: provider.modelName, apiKey: provider.apiKey } };
  if (provider.type === 'google_gemini') return { type: 'google_gemini', payload: { modelName: provider.modelName, apiKey: provider.apiKey } };
  if (provider.type === 'kimi') return { type: 'kimi', payload: { region: provider.region, modelName: provider.modelName, apiKey: provider.apiKey } };
  return null;
}

function normalizedDraft(draft: ConfigDraft): ConfigDraft {
  if (draft.type === 'azure_openai') {
    return { type: draft.type, payload: { baseUrl: draft.payload.baseUrl.trim(), modelName: draft.payload.modelName.trim(), apiKey: draft.payload.apiKey.trim() } };
  }
  if (draft.type === 'kimi') {
    return { type: draft.type, payload: { region: draft.payload.region, modelName: draft.payload.modelName.trim(), apiKey: draft.payload.apiKey.trim() } };
  }
  return { type: draft.type, payload: { modelName: draft.payload.modelName.trim(), apiKey: draft.payload.apiKey.trim() } };
}

function sameDraft(left: ConfigDraft, right: ConfigDraft): boolean {
  return left.type === right.type && JSON.stringify(normalizedDraft(left)) === JSON.stringify(normalizedDraft(right));
}

function draftHasValue(draft: ConfigDraft): boolean {
  return Object.values(normalizedDraft(draft).payload).some(Boolean);
}

function copyDraft(draft: ConfigDraft | null): ConfigDraft | null {
  return draft ? { ...draft, payload: { ...draft.payload } } as ConfigDraft : null;
}

export function ConfigPage({ client, onDirtyChange, onSavePendingChange, onSaveUncertainChange }: ConfigPageProps) {
  const provider = useProviderConfig(client);
  const connectionOpener = useRef<HTMLElement | null>(null);
  const [draft, setDraft] = useState<ConfigDraft | null>(null);
  const appliedConfig = useRef<ConfigResponse | null>(null);
  const [providerDialogOpen, setProviderDialogOpen] = useState(false);
  const loadedProvider = provider.config.data?.configured ? provider.config.data.provider : null;
  const loadedDraft = useMemo(() => persistedDraft(provider.config.data), [provider.config.data]);
  const dirty = draft !== null && (loadedDraft?.type === draft.type ? !sameDraft(draft, loadedDraft) : draftHasValue(draft));
  const replacementDraft = draft !== null && loadedProvider !== null && loadedProvider.type !== draft.type;
  const recoveryBlocked = provider.saveRecovery === 'loading' || provider.saveRecovery === 'unavailable';
  const saveUncertain = provider.saveRecovery !== 'none';
  const deviceBusy = provider.deviceFlowPending !== null;

  useEffect(() => {
    if (!provider.config.data || appliedConfig.current === provider.config.data) return;
    appliedConfig.current = provider.config.data;
    if (provider.saveRecovery !== 'none') return;
    setDraft(copyDraft(loadedDraft));
  }, [loadedDraft, provider.config.data, provider.saveRecovery]);
  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange]);
  useEffect(() => { onSavePendingChange?.(provider.savePending); return () => onSavePendingChange?.(false); }, [provider.savePending, onSavePendingChange]);
  useEffect(() => { onSaveUncertainChange?.(saveUncertain); return () => onSaveUncertainChange?.(false); }, [saveUncertain, onSaveUncertainChange]);
  useEffect(() => {
    if (provider.deviceFlow?.status !== 'success' || !provider.config.data?.configured || provider.config.data.provider.type !== 'github_copilot') return;
    setDraft(null);
    setProviderDialogOpen(false);
    void provider.clearDeviceFlow();
  }, [provider.config.data, provider.deviceFlow?.status, provider.clearDeviceFlow]);

  const clearDiscoveries = () => {
    provider.clearOpenAIModels();
    provider.clearGeminiModels();
    provider.clearKimiModels();
  };
  const discardDraft = () => {
    setDraft(copyDraft(loadedDraft));
    clearDiscoveries();
    provider.clearSaveError();
  };
  const confirmDiscard = () => !provider.savePending
    && (!saveUncertain || window.confirm('The save result is unknown. Leaving does not cancel it. Continue?'))
    && (!dirty || window.confirm('Discard unsaved Provider changes?'));
  const openProviderDialog = () => {
    if (recoveryBlocked || !confirmDiscard()) return;
    discardDraft();
    void provider.clearDeviceFlow();
    setProviderDialogOpen(true);
  };
  const cancelDraft = () => {
    if (confirmDiscard()) discardDraft();
  };
  const selectProvider = (next: ConfigDraft) => {
    clearDiscoveries();
    setDraft(next);
    setProviderDialogOpen(false);
    void provider.clearDeviceFlow();
  };

  const loadOpenAIModels = async () => {
    if (draft?.type !== 'openai') return;
    const key = draft.payload.apiKey;
    const data = await provider.loadOpenAIModels(key);
    if (!data) return;
    setDraft(current => current?.type === 'openai' && current.payload.apiKey === key ? {
      type: 'openai',
      payload: {
        ...current.payload,
        modelName: data.models.some(model => model.id === current.payload.modelName) ? current.payload.modelName
          : loadedProvider?.type === 'openai' && loadedProvider.apiKey === key.trim() && data.models.some(model => model.id === loadedProvider.modelName) ? loadedProvider.modelName : '',
      },
    } : current);
  };
  const loadGeminiModels = async () => {
    if (draft?.type !== 'google_gemini') return;
    const key = draft.payload.apiKey;
    const data = await provider.loadGeminiModels(key);
    if (!data) return;
    setDraft(current => current?.type === 'google_gemini' && current.payload.apiKey === key ? {
      type: 'google_gemini',
      payload: {
        ...current.payload,
        modelName: data.models.some(model => model.id === current.payload.modelName) ? current.payload.modelName
          : loadedProvider?.type === 'google_gemini' && loadedProvider.apiKey === key.trim() && data.models.some(model => model.id === loadedProvider.modelName) ? loadedProvider.modelName : '',
      },
    } : current);
  };
  const loadKimiModels = async () => {
    if (draft?.type !== 'kimi' || !draft.payload.region) return;
    const region = draft.payload.region;
    const key = draft.payload.apiKey;
    const identity = `${region}\0${key.trim()}`;
    const data = await provider.loadKimiModels(region, key);
    if (!data) return;
    setDraft(current => current?.type === 'kimi' && `${current.payload.region}\0${current.payload.apiKey.trim()}` === identity ? {
      type: 'kimi',
      payload: {
        ...current.payload,
        modelName: data.models.some(model => model.id === current.payload.modelName) ? current.payload.modelName
          : loadedProvider?.type === 'kimi' && loadedProvider.region === region && loadedProvider.apiKey === key.trim()
            && data.models.some(model => model.id === loadedProvider.modelName) ? loadedProvider.modelName : '',
      },
    } : current);
  };

  const testConnection = () => {
    connectionOpener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    void provider.testSavedConnection();
  };
  const closeProviderDialog = async () => {
    await provider.clearDeviceFlow();
    setProviderDialogOpen(false);
  };
  const cancelAuthentication = async () => {
    await provider.cancelGithub();
    await provider.clearDeviceFlow();
    setProviderDialogOpen(false);
  };

  if (provider.config.state === 'loading' && !provider.config.data && !saveUncertain) return <div className="config-page"><p className="config-loading" role="status">Loading Provider configuration...</p></div>;
  if (provider.config.error && !provider.config.data && !saveUncertain) {
    const unavailable = provider.config.error.code === 'config_unavailable';
    return <div className="config-page"><section className="config-state" aria-labelledby="config-error-title">
      <p className="config-eyebrow">{unavailable ? 'Local access required' : 'Configuration unavailable'}</p>
      <h1 id="config-error-title">{provider.config.error.message}</h1>
      <p>{provider.config.error.action}</p>
      {!unavailable && <button className="button" type="button" onClick={() => void provider.reload()}>Retry</button>}
    </section></div>;
  }

  const configured = provider.config.data?.configured === true;
  const discoveryLoading = provider.openaiModels.state === 'loading' || provider.geminiModels.state === 'loading' || provider.kimiModels.state === 'loading';
  const canTest = configured && !dirty && !replacementDraft && !provider.savePending && !deviceBusy && !provider.testPending && !recoveryBlocked && !discoveryLoading;
  return <div className="config-page">
    {saveUncertain && <div className="config-recovery" role="status">
      <strong>The save result could not be confirmed.</strong>
      <p>{provider.saveRecovery === 'loading' ? 'Reading current configuration...' : provider.saveRecovery === 'unavailable' ? 'Current configuration is unavailable. Changes remain blocked.' : 'Current configuration reloaded. The earlier request may still complete. Remaining changes are unsaved.'}</p>
      <button className="button" type="button" disabled={provider.savePending || provider.saveRecovery === 'loading'} onClick={() => void provider.reconcileConfig()}>Reload configuration</button>
    </div>}
    {provider.config.error && <div className="config-error" role="alert"><strong>{provider.config.error.message}</strong><span>{provider.config.error.action}</span></div>}
    {draft?.type === 'kimi' ? <KimiConfigForm draft={draft.payload} configured={loadedProvider?.type === 'kimi'} dirty={dirty}
      models={provider.kimiModels} blocked={recoveryBlocked} savePending={provider.savePending} saveError={provider.saveError}
      testPending={provider.testPending} canTest={canTest}
      onChange={payload => {
        if (payload.region !== draft.payload.region || payload.apiKey !== draft.payload.apiKey) provider.clearKimiModels();
        setDraft({ type: 'kimi', payload });
      }}
      onLoadModels={() => void loadKimiModels()}
      onSave={() => {
        if (!draft.payload.region) return;
        void provider.saveKimi({ ...draft.payload, region: draft.payload.region } satisfies KimiConfigPayload);
      }}
      onCancel={cancelDraft} onChangeProvider={openProviderDialog} onTest={testConnection}
    /> : draft?.type === 'google_gemini' ? <GoogleGeminiConfigForm draft={draft.payload} configured={loadedProvider?.type === 'google_gemini'} dirty={dirty}
      models={provider.geminiModels} blocked={recoveryBlocked} savePending={provider.savePending} saveError={provider.saveError}
      testPending={provider.testPending} canTest={canTest}
      onChange={payload => {
        if (payload.apiKey !== draft.payload.apiKey) provider.clearGeminiModels();
        setDraft({ type: 'google_gemini', payload });
      }}
      onLoadModels={() => void loadGeminiModels()} onSave={() => void provider.saveGemini(draft.payload)}
      onCancel={cancelDraft} onChangeProvider={openProviderDialog} onTest={testConnection}
    /> : draft?.type === 'openai' ? <OpenAIConfigForm draft={draft.payload} configured={loadedProvider?.type === 'openai'} dirty={dirty}
      models={provider.openaiModels} blocked={recoveryBlocked} savePending={provider.savePending} saveError={provider.saveError}
      testPending={provider.testPending} canTest={canTest}
      onChange={payload => {
        if (payload.apiKey !== draft.payload.apiKey) provider.clearOpenAIModels();
        setDraft({ type: 'openai', payload });
      }}
      onLoadModels={() => void loadOpenAIModels()} onSave={() => void provider.saveOpenAI(draft.payload)}
      onCancel={cancelDraft} onChangeProvider={openProviderDialog} onTest={testConnection}
    /> : draft?.type === 'azure_openai' ? <AzureConfigForm
      draft={draft.payload} configured={loadedProvider?.type === 'azure_openai'} dirty={dirty} savePending={provider.savePending} blocked={recoveryBlocked}
      saveError={provider.saveError} testPending={provider.testPending} canTest={canTest} onChange={payload => setDraft({ type: 'azure_openai', payload })}
      onSave={() => void provider.saveAzure(draft.payload)} onCancel={cancelDraft} onChangeProvider={openProviderDialog}
      onTest={testConnection}
    /> : loadedProvider?.type === 'github_copilot' ? <section className="config-provider" aria-labelledby="github-provider-title">
      <div className="config-section-heading">
        <div><p className="config-eyebrow">Active provider</p><h1 id="github-provider-title">GitHub Copilot GPT authenticated</h1></div>
        <button className="button" type="button" disabled={recoveryBlocked || provider.savePending} onClick={openProviderDialog}>Change provider</button>
      </div>
      <dl className="provider-details"><div><dt>Provider</dt><dd>GitHub Copilot GPT</dd></div><div><dt>Model</dt><dd className="mono">{loadedProvider.modelName}</dd></div><div><dt>Status</dt><dd><span className="config-status-dot" />Authenticated</dd></div></dl>
      <div className="config-test-actions"><div><strong>Connection check</strong><span>Send a fixed minimal request using only the saved configuration.</span></div><button className="button" type="button" disabled={!canTest} onClick={testConnection}>{provider.testPending ? 'Testing...' : 'Test connection'}</button></div>
    </section> : <section className="config-state" aria-labelledby="empty-config-title">
      <p className="config-eyebrow">Model provider</p><h1 id="empty-config-title">No Provider configured</h1>
      <p>Add the one model provider FSQ will use for the next complete task.</p>
      <button className="button button--primary" type="button" disabled={recoveryBlocked || provider.savePending} onClick={openProviderDialog}>Add configuration</button>
    </section>}
    {providerDialogOpen && <ProviderDialog
      deviceFlow={provider.deviceFlow} deviceFlowPending={provider.deviceFlowPending} deviceFlowError={provider.deviceFlowError}
      onSelectOpenAI={() => selectProvider(loadedProvider?.type === 'openai' && loadedDraft?.type === 'openai' ? copyDraft(loadedDraft)! : { type: 'openai', payload: { modelName: '', apiKey: '' } })}
      onSelectAzure={() => selectProvider(loadedProvider?.type === 'azure_openai' && loadedDraft?.type === 'azure_openai' ? copyDraft(loadedDraft)! : { type: 'azure_openai', payload: { ...emptyAzure } })}
      onSelectGemini={() => selectProvider(loadedProvider?.type === 'google_gemini' && loadedDraft?.type === 'google_gemini' ? copyDraft(loadedDraft)! : { type: 'google_gemini', payload: { modelName: '', apiKey: '' } })}
      onSelectKimi={() => selectProvider(loadedProvider?.type === 'kimi' && loadedDraft?.type === 'kimi' ? copyDraft(loadedDraft)! : { type: 'kimi', payload: { region: '', modelName: '', apiKey: '' } })}
      onStartGithub={provider.startGithub} onRetryModels={provider.retryGithubModels}
      onSaveModel={provider.saveGithubModel} onCancelAuthentication={cancelAuthentication}
      onClose={() => void closeProviderDialog()}
    />}
    {provider.connectionResult && <ConnectionResultDialog result={provider.connectionResult} onClose={provider.dismissConnectionResult} returnFocus={connectionOpener.current} />}
  </div>;
}
