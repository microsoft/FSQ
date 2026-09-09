import { useState, type FormEvent } from 'react';
import type { ApiErrorBody, AzureConfigPayload } from '../../../api/types';

interface AzureConfigFormProps {
  draft: AzureConfigPayload;
  configured: boolean;
  dirty: boolean;
  savePending: boolean;
  blocked?: boolean;
  saveError: ApiErrorBody | null;
  testPending: boolean;
  canTest: boolean;
  onChange: (draft: AzureConfigPayload) => void;
  onSave: () => void;
  onCancel: () => void;
  onChangeProvider: () => void;
  onTest: () => void;
}

export function AzureConfigForm(props: AzureConfigFormProps) {
  const [keyVisible, setKeyVisible] = useState(false);
  const locked = props.savePending || props.blocked;
  const update = (field: keyof AzureConfigPayload, value: string) => props.onChange({ ...props.draft, [field]: value });
  const submit = (event: FormEvent) => { event.preventDefault(); if (!locked) { setKeyVisible(false); props.onSave(); } };

  return <section className="config-editor" aria-labelledby="azure-config-title">
    <div className="config-section-heading">
      <div><p className="config-eyebrow">{props.configured ? 'Active provider' : 'New provider'}</p><h1 id="azure-config-title">Azure OpenAI configuration</h1></div>
      <button className="button" type="button" disabled={locked} onClick={props.onChangeProvider}>Change provider</button>
    </div>
    <form className="config-form" aria-busy={props.savePending} onSubmit={submit}>
      <div className="config-field">
        <label htmlFor="azure-base-url">Base URL</label>
        <input id="azure-base-url" required type="url" value={props.draft.baseUrl} disabled={locked} onChange={(event) => update('baseUrl', event.target.value)} placeholder="https://example.openai.azure.com" />
        <small>Azure OpenAI resource endpoint. FSQ normalizes it to the compatible Responses API path.</small>
      </div>
      <div className="config-field">
        <label htmlFor="azure-model-name">Model name</label>
        <input id="azure-model-name" required value={props.draft.modelName} disabled={locked} onChange={(event) => update('modelName', event.target.value)} autoComplete="off" />
        <small>Use a GPT 5 or later deployment when available.</small>
      </div>
      <div className="config-field">
        <label htmlFor="azure-api-key">API key</label>
        <span className="config-secret-input">
          <input id="azure-api-key" required type={keyVisible ? 'text' : 'password'} value={props.draft.apiKey} disabled={locked} onChange={(event) => update('apiKey', event.target.value)} autoComplete="off" />
          <button className="config-icon-button" type="button" aria-label={`${keyVisible ? 'Hide' : 'Show'} API key`} title={`${keyVisible ? 'Hide' : 'Show'} API key`} disabled={locked} onClick={() => setKeyVisible((visible) => !visible)}>
            <span className={`config-eye${keyVisible ? ' config-eye--hidden' : ''}`} aria-hidden="true" />
          </button>
        </span>
        <small>Stored as plaintext in the local FSQ auth directory.</small>
      </div>
      {props.saveError && <div className="config-error" role="alert"><strong>{props.saveError.message}</strong><span>{props.saveError.action}</span></div>}
      <div className="config-form-actions">
        <button className="button" type="button" disabled={locked} onClick={props.onCancel}>Cancel</button>
        <button className="button button--primary" type="submit" disabled={locked || !props.dirty}>{props.savePending ? 'Saving...' : 'Save changes'}</button>
      </div>
    </form>
    {props.configured && <div className="config-test-actions">
      <div><strong>Connection check</strong><span>Send a fixed minimal request using only the saved configuration.</span></div>
      <button className="button" type="button" disabled={!props.canTest} onClick={props.onTest}>{props.testPending ? 'Testing...' : 'Test connection'}</button>
    </div>}
  </section>;
}
