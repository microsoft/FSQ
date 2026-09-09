import { useEffect, useMemo, useRef, useState } from 'react';
import type { ControlPlaneClient } from '../../api/controlPlaneClient';
import type { AzureConfigPayload, ConfigResponse } from '../../api/types';
import { AzureConfigForm } from './components/AzureConfigForm';
import { ConnectionResultDialog } from './components/ConnectionResultDialog';
import { ProviderDialog } from './components/ProviderDialog';
import { useProviderConfig } from './hooks/useProviderConfig';

interface ConfigPageProps {
  client?: ControlPlaneClient;
  onDirtyChange?: (dirty: boolean) => void;
}

const emptyAzure: AzureConfigPayload = { baseUrl: '', modelName: '', apiKey: '' };

function persistedAzure(config: ConfigResponse | null): AzureConfigPayload | null {
  if (!config?.configured || config.provider.type !== 'azure_openai') return null;
  return { baseUrl: config.provider.baseUrl, modelName: config.provider.modelName, apiKey: config.provider.apiKey };
}

function normalized(value: AzureConfigPayload): AzureConfigPayload {
  return { baseUrl: value.baseUrl.trim(), modelName: value.modelName.trim(), apiKey: value.apiKey.trim() };
}

function sameAzure(left: AzureConfigPayload, right: AzureConfigPayload): boolean {
  const a = normalized(left);
  const b = normalized(right);
  return a.baseUrl === b.baseUrl && a.modelName === b.modelName && a.apiKey === b.apiKey;
}

export function ConfigPage({ client, onDirtyChange }: ConfigPageProps) {
  const provider = useProviderConfig(client);
  const connectionOpener = useRef<HTMLElement | null>(null);
  const testConnection = () => {
    connectionOpener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    void provider.testSavedConnection();
  };
  const [azureDraft, setAzureDraft] = useState<AzureConfigPayload | null>(null);
  const [providerDialogOpen, setProviderDialogOpen] = useState(false);
  const loadedAzure = useMemo(() => persistedAzure(provider.config.data), [provider.config.data]);
  const loadedProvider = provider.config.data?.configured ? provider.config.data.provider : null;
  const dirty = azureDraft !== null && (loadedAzure ? !sameAzure(azureDraft, loadedAzure) : Object.values(normalized(azureDraft)).some(Boolean));
  const replacementDraft = azureDraft !== null && loadedProvider !== null && loadedProvider.type !== 'azure_openai';
  const deviceBusy = provider.deviceFlowPending !== null;

  useEffect(() => {
    if (loadedAzure) setAzureDraft(loadedAzure);
  }, [loadedAzure]);
  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange]);
  useEffect(() => {
    if (provider.deviceFlow?.status !== 'success' || !provider.config.data?.configured || provider.config.data.provider.type !== 'github_copilot') return;
    setAzureDraft(null);
    setProviderDialogOpen(false);
    void provider.clearDeviceFlow();
  }, [provider.config.data, provider.deviceFlow?.status, provider.clearDeviceFlow]);

  const discardDraft = () => setAzureDraft(loadedAzure ? { ...loadedAzure } : null);
  const confirmDiscard = () => !dirty || window.confirm('Discard unsaved Azure changes?');
  const openProviderDialog = () => {
    if (!confirmDiscard()) return;
    discardDraft();
    void provider.clearDeviceFlow();
    setProviderDialogOpen(true);
  };
  const cancelAzure = () => {
    if (!confirmDiscard()) return;
    discardDraft();
  };
  const selectAzure = () => {
    setAzureDraft(loadedAzure ? { ...loadedAzure } : { ...emptyAzure });
    setProviderDialogOpen(false);
    void provider.clearDeviceFlow();
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

  if (provider.config.state === 'loading' && !provider.config.data) return <div className="config-page"><p className="config-loading" role="status">Loading Provider configuration...</p></div>;
  if (provider.config.error && !provider.config.data) {
    const unavailable = provider.config.error.code === 'config_unavailable';
    return <div className="config-page"><section className="config-state" aria-labelledby="config-error-title">
      <p className="config-eyebrow">{unavailable ? 'Local access required' : 'Configuration unavailable'}</p>
      <h1 id="config-error-title">{provider.config.error.message}</h1>
      <p>{provider.config.error.action}</p>
      {!unavailable && <button className="button" type="button" onClick={() => void provider.reload()}>Retry</button>}
    </section></div>;
  }

  const configured = provider.config.data?.configured === true;
  const canTest = configured && !dirty && !replacementDraft && !provider.savePending && !deviceBusy && !provider.testPending;
  return <div className="config-page">
    {provider.config.error && <div className="config-error" role="alert"><strong>{provider.config.error.message}</strong><span>{provider.config.error.action}</span></div>}
    {azureDraft ? <AzureConfigForm
      draft={azureDraft} configured={loadedProvider?.type === 'azure_openai'} dirty={dirty} savePending={provider.savePending}
      saveError={provider.saveError} testPending={provider.testPending} canTest={canTest} onChange={setAzureDraft}
      onSave={() => void provider.saveAzure(azureDraft)} onCancel={cancelAzure} onChangeProvider={openProviderDialog}
      onTest={testConnection}
    /> : loadedProvider?.type === 'github_copilot' ? <section className="config-provider" aria-labelledby="github-provider-title">
      <div className="config-section-heading">
        <div><p className="config-eyebrow">Active provider</p><h1 id="github-provider-title">GitHub Copilot GPT authenticated</h1></div>
        <button className="button" type="button" onClick={openProviderDialog}>Change provider</button>
      </div>
      <dl className="provider-details"><div><dt>Provider</dt><dd>GitHub Copilot GPT</dd></div><div><dt>Model</dt><dd className="mono">{loadedProvider.modelName}</dd></div><div><dt>Status</dt><dd><span className="config-status-dot" />Authenticated</dd></div></dl>
      <div className="config-test-actions"><div><strong>Connection check</strong><span>Send a fixed minimal request using only the saved configuration.</span></div><button className="button" type="button" disabled={!canTest} onClick={testConnection}>{provider.testPending ? 'Testing...' : 'Test connection'}</button></div>
    </section> : <section className="config-state" aria-labelledby="empty-config-title">
      <p className="config-eyebrow">Model provider</p><h1 id="empty-config-title">No Provider configured</h1>
      <p>Add the one model provider FSQ will use for the next complete task.</p>
      <button className="button button--primary" type="button" onClick={openProviderDialog}>Add configuration</button>
    </section>}
    {providerDialogOpen && <ProviderDialog
      deviceFlow={provider.deviceFlow} deviceFlowPending={provider.deviceFlowPending} deviceFlowError={provider.deviceFlowError}
      onSelectAzure={selectAzure} onStartGithub={provider.startGithub} onRetryModels={provider.retryGithubModels}
      onSaveModel={provider.saveGithubModel} onCancelAuthentication={cancelAuthentication}
      onClose={() => void closeProviderDialog()}
    />}
    {provider.connectionResult && <ConnectionResultDialog result={provider.connectionResult} onClose={provider.dismissConnectionResult} returnFocus={connectionOpener.current} />}
  </div>;
}
