import { Eye, EyeOff, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import type { ApiErrorBody, GoogleGeminiConfigPayload, GoogleGeminiModelsResponse } from '../../../api/types';

interface GoogleGeminiConfigFormProps {
  draft: GoogleGeminiConfigPayload;
  configured: boolean;
  dirty: boolean;
  models: { state: 'idle' | 'loading' | 'ready' | 'error'; data: GoogleGeminiModelsResponse | null; error: ApiErrorBody | null };
  savePending: boolean;
  blocked: boolean;
  saveError: ApiErrorBody | null;
  canTest: boolean;
  testPending: boolean;
  onChange: (draft: GoogleGeminiConfigPayload) => void;
  onLoadModels: () => void;
  onSave: () => void;
  onCancel: () => void;
  onChangeProvider: () => void;
  onTest: () => void;
}

export function GoogleGeminiConfigForm(props: GoogleGeminiConfigFormProps) {
  const [keyVisible, setKeyVisible] = useState(false);
  const loading = props.models.state === 'loading';
  const models = props.models.data?.models ?? [];
  const offered = props.models.state === 'ready' && models.some(model => model.id === props.draft.modelName);
  const locked = props.savePending || props.blocked;
  const canSave = props.dirty && offered && Boolean(props.draft.apiKey.trim()) && !locked;
  return <section className="config-editor" aria-labelledby="gemini-config-title">
    <div className="config-section-heading">
      <div><p className="config-eyebrow">{props.configured ? 'Active provider' : 'New provider'}</p><h1 id="gemini-config-title">Google Gemini configuration</h1></div>
      <button className="button" type="button" disabled={locked} onClick={() => { setKeyVisible(false); props.onChangeProvider(); }}>Change provider</button>
    </div>
    <form className="config-form" aria-busy={props.savePending} onSubmit={event => { event.preventDefault(); if (canSave) { setKeyVisible(false); props.onSave(); } }}>
      <div className="config-field">
        <label htmlFor="gemini-api-key">API key</label>
        <span className="config-secret-input">
          <input id="gemini-api-key" required autoComplete="off" type={keyVisible ? 'text' : 'password'} value={props.draft.apiKey} disabled={locked} onChange={event => { setKeyVisible(false); props.onChange({ modelName: '', apiKey: event.target.value }); }} />
          <button className="config-icon-button" type="button" disabled={locked} aria-label={`${keyVisible ? 'Hide' : 'Show'} API key`} title={`${keyVisible ? 'Hide' : 'Show'} API key`} onClick={() => setKeyVisible(visible => !visible)}>{keyVisible ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}</button>
        </span>
      </div>
      <div className="config-model-actions">
        <button className="button" type="button" disabled={locked || loading || !props.draft.apiKey.trim()} onClick={props.onLoadModels}>
          <RefreshCw size={16} aria-hidden="true" />{loading ? 'Loading models...' : props.models.state === 'error' || (props.models.state === 'ready' && models.length === 0) ? 'Retry models' : 'Load models'}
        </button>
        {loading && <span role="status">Loading available models...</span>}
      </div>
      {props.models.error && <div className="config-error" role="alert"><strong>{props.models.error.message}</strong><span>{props.models.error.action}</span></div>}
      {props.models.state === 'ready' && models.length === 0 && <div className="config-error" role="status"><strong>No eligible models are available.</strong><span>Retry model discovery or use another provider.</span></div>}
      {props.models.state === 'ready' && models.length > 0 ? <div className="config-field">
        <label htmlFor="gemini-model">Model</label>
        <select id="gemini-model" required value={props.draft.modelName} disabled={locked} onChange={event => props.onChange({ ...props.draft, modelName: event.target.value })}>
          <option value="">Select a model</option>
          {models.map(model => <option key={model.id} value={model.id}>{model.name}</option>)}
        </select>
      </div> : props.draft.modelName && <dl className="provider-details"><div><dt>Model</dt><dd>{props.draft.modelName}</dd></div></dl>}
      {props.saveError && <div className="config-error" role="alert"><strong>{props.saveError.message}</strong><span>{props.saveError.action}</span></div>}
      {props.dirty && !offered && !loading && <p className="field-help">A model selection is required before saving.</p>}
      {props.savePending && <p role="status">Saving configuration...</p>}
      <div className="config-form-actions">
        <button className="button" type="button" disabled={locked} onClick={() => { setKeyVisible(false); props.onCancel(); }}>Cancel</button>
        <button className="button button--primary" type="submit" disabled={!canSave}>{props.savePending ? 'Saving...' : 'Save changes'}</button>
      </div>
    </form>
    {props.configured && <div className="config-test-actions"><div><strong>Connection check</strong><span>{props.dirty ? 'Unsaved changes' : 'Saved configuration'}</span></div><button className="button" type="button" disabled={!props.canTest} onClick={props.onTest}>{props.testPending ? 'Testing...' : 'Test connection'}</button></div>}
  </section>;
}
