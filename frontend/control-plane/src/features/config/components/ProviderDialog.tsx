import { Copy } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { ApiErrorBody, GitHubDeviceFlowResponse } from '../../../api/types';
import type { DeviceFlowPending } from '../hooks/useProviderConfig';
import { DialogFrame } from './DialogFrame';

interface ProviderDialogProps {
  deviceFlow: GitHubDeviceFlowResponse | null;
  deviceFlowPending: DeviceFlowPending;
  deviceFlowError: ApiErrorBody | null;
  onSelectAzure: () => void;
  onStartGithub: () => Promise<unknown>;
  onRetryModels: () => Promise<unknown>;
  onSaveModel: (modelName: string) => Promise<unknown>;
  onCancelAuthentication: () => Promise<void>;
  onClose: () => void;
}

export function ProviderDialog(props: ProviderDialogProps) {
  const [step, setStep] = useState<'choice' | 'github'>('choice');
  const [modelName, setModelName] = useState('');
  const [copied, setCopied] = useState(false);
  const modelRef = useRef<HTMLSelectElement>(null);
  const verificationRef = useRef<HTMLAnchorElement>(null);
  const flow = props.deviceFlow;

  useEffect(() => {
    if (flow?.status === 'waiting') {
      verificationRef.current?.focus();
    } else if (flow?.status === 'ready') {
      setModelName('');
      modelRef.current?.focus();
    }
  }, [flow?.authRequestId, flow?.status]);

  const copyCode = async () => {
    if (flow?.status !== 'waiting') return;
    await navigator.clipboard?.writeText(flow.userCode);
    setCopied(true);
  };

  const startGithub = () => {
    setStep('github');
    setModelName('');
    void props.onStartGithub();
  };

  if (step === 'choice') return <DialogFrame title="Choose model provider" onClose={props.onClose}>
    <p className="config-dialog-intro">One provider is active at a time. A replacement takes effect only after it is saved.</p>
    <div className="provider-options">
      <button type="button" className="provider-option" data-dialog-initial onClick={props.onSelectAzure}>
        <strong>Azure GPT</strong><span>Use an Azure OpenAI-compatible endpoint and API key.</span>
      </button>
      <button type="button" className="provider-option" onClick={startGithub}>
        <strong>GitHub Copilot GPT</strong><span>Authenticate this computer through GitHub device flow.</span>
      </button>
    </div>
    <div className="config-dialog-actions"><button className="button" type="button" onClick={props.onClose}>Cancel</button></div>
  </DialogFrame>;

  if (props.deviceFlowPending === 'starting' && !flow) return <DialogFrame title="Connect GitHub Copilot GPT" onClose={props.onClose}>
    <p className="config-status" role="status">Requesting a GitHub device code...</p>
    <div className="config-dialog-actions"><button className="button" type="button" onClick={props.onClose}>Cancel</button></div>
  </DialogFrame>;

  if (flow?.status === 'waiting') return <DialogFrame title="Connect GitHub Copilot GPT" onClose={props.onClose}>
    <>
      <p className="config-status" role="status">Waiting for authorization in GitHub.</p>
      <a ref={verificationRef} className="verification-link" data-dialog-initial href={flow.verificationUri} target="_blank" rel="noreferrer">Open GitHub verification</a>
      <div className="device-code-block">
        <span>User code</span><strong className="mono">{flow.userCode}</strong>
        <button className="config-icon-button" type="button" aria-label="Copy user code" title="Copy user code" onClick={() => void copyCode()}><Copy aria-hidden="true"/></button>
      </div>
      {copied && <p className="field-help" role="status">Code copied.</p>}
      <p className="field-help">Expires <time dateTime={flow.expiresAt}>{new Date(flow.expiresAt).toLocaleString()}</time></p>
    </>
    <div className="config-dialog-actions">
      <button className="button" type="button" disabled={props.deviceFlowPending === 'cancelling'} onClick={() => void props.onCancelAuthentication()}>
        Cancel authentication
      </button>
    </div>
  </DialogFrame>;

  if (flow?.status === 'loading_models') return <DialogFrame title="Choose GitHub Copilot model" onClose={props.onClose}>
    <p className="config-status" role="status">Loading available models...</p>
    <div className="config-dialog-actions"><button className="button" type="button" onClick={() => void props.onCancelAuthentication()}>Cancel</button></div>
  </DialogFrame>;

  if (flow?.status === 'ready') return <DialogFrame title="Choose GitHub Copilot model" onClose={props.onClose}>
    {flow.models.length ? <form onSubmit={(event) => { event.preventDefault(); void props.onSaveModel(modelName); }}>
      <div className="config-field">
        <label htmlFor="github-model-name">Model</label>
        <select id="github-model-name" ref={modelRef} data-dialog-initial required value={modelName} onChange={(event) => setModelName(event.target.value)}>
          <option value="">Select a model</option>
          {flow.models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
        </select>
        <small>General-purpose GPT 5 or later models available to this Copilot account.</small>
      </div>
      {props.deviceFlowError && <div className="config-error" role="alert"><strong>{props.deviceFlowError.message}</strong><span>{props.deviceFlowError.action}</span></div>}
      <div className="config-dialog-actions">
        <button className="button" type="button" onClick={() => void props.onCancelAuthentication()}>Cancel</button>
        <button className="button button--primary" type="submit" disabled={!modelName || props.deviceFlowPending === 'saving'}>{props.deviceFlowPending === 'saving' ? 'Saving...' : 'Save'}</button>
      </div>
    </form> : <>
      <div className="config-error" role="alert"><strong>No eligible models are available.</strong><span>Retry model discovery or use another provider.</span></div>
      <div className="config-dialog-actions">
        <button className="button" type="button" onClick={() => void props.onCancelAuthentication()}>Cancel</button>
        <button className="button button--primary" type="button" disabled={props.deviceFlowPending === 'retrying_models'} onClick={() => void props.onRetryModels()}>Retry models</button>
      </div>
    </>}
  </DialogFrame>;

  if (flow?.status === 'model_error') return <DialogFrame title="Choose GitHub Copilot model" onClose={props.onClose}>
    <div className="config-error" role="alert"><strong>{flow.message}</strong><span>Authorization is still valid. Retry model discovery.</span></div>
    {props.deviceFlowError && <div className="config-error" role="alert"><strong>{props.deviceFlowError.message}</strong><span>{props.deviceFlowError.action}</span></div>}
    <div className="config-dialog-actions">
      <button className="button" type="button" onClick={() => void props.onCancelAuthentication()}>Cancel</button>
      <button className="button button--primary" type="button" disabled={props.deviceFlowPending === 'retrying_models'} onClick={() => void props.onRetryModels()}>Retry models</button>
    </div>
  </DialogFrame>;

  if (flow && (flow.status === 'failed' || flow.status === 'expired')) return <DialogFrame title="Connect GitHub Copilot GPT" onClose={props.onClose}>
    <div className="config-error" role="alert"><strong>{flow.message ?? 'GitHub authentication failed.'}</strong><span>Request a new device code and try again.</span></div>
    <div className="config-dialog-actions">
      <button className="button" type="button" onClick={() => void props.onCancelAuthentication()}>Cancel</button>
      <button className="button button--primary" type="button" onClick={() => void props.onStartGithub()}>Retry</button>
    </div>
  </DialogFrame>;

  return <DialogFrame title="Connect GitHub Copilot GPT" onClose={props.onClose}>
    {props.deviceFlowError
      ? <div className="config-error" role="alert"><strong>{props.deviceFlowError.message}</strong><span>{props.deviceFlowError.action}</span></div>
      : <p className="config-status" role="status">GitHub authorization ended.</p>}
    <div className="config-dialog-actions">
      <button className="button" type="button" onClick={props.onClose}>Cancel</button>
      <button className="button button--primary" type="button" onClick={() => void props.onStartGithub()}>Retry</button>
    </div>
  </DialogFrame>;
}