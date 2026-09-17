import { Eye, EyeOff, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import type { ApiErrorBody, KimiRegion } from '../../../api/types';
import type { OpenAIModelsState } from '../hooks/useProviderConfig';

export interface KimiDraftPayload {
  region: KimiRegion | '';
  modelName: string;
  apiKey: string;
}

interface KimiConfigFormProps {
  draft: KimiDraftPayload;
  configured: boolean;
  dirty: boolean;
  models: OpenAIModelsState;
  savePending: boolean;
  blocked: boolean;
  saveError: ApiErrorBody | null;
  canTest: boolean;
  testPending: boolean;
  onChange: (draft: KimiDraftPayload) => void;
  onLoadModels: () => void;
  onSave: () => void;
  onCancel: () => void;
  onChangeProvider: () => void;
  onTest: () => void;
}

export function KimiConfigForm(props: KimiConfigFormProps) {
  const [keyVisible, setKeyVisible] = useState(false);
  const loading = props.models.state === 'loading';
  const models = props.models.data?.models ?? [];
  const hasModels = props.models.state === 'ready' && models.length > 0;
  const offered = hasModels && models.some(model => model.id === props.draft.modelName);
  const locked = props.savePending || props.blocked;
  const canSave = props.dirty && offered && Boolean(props.draft.region) && Boolean(props.draft.apiKey.trim()) && !locked;

  return <section className="config-editor" aria-labelledby="kimi-config-title">
    <div className="config-section-heading">
      <div><p className="config-eyebrow">{props.configured ? 'Active provider' : 'New provider'}</p><h1 id="kimi-config-title">Kimi configuration</h1></div>
      <button className="button" type="button" disabled={locked} onClick={() => { setKeyVisible(false); props.onChangeProvider(); }}>Change provider</button>
    </div>
    <form className="config-form" aria-busy={props.savePending} onSubmit={event => { event.preventDefault(); if (canSave) { setKeyVisible(false); props.onSave(); } }}>
      <div className="config-field">
        <label htmlFor="kimi-region">Region</label>
        <select id="kimi-region" required value={props.draft.region} disabled={locked} onChange={event => props.onChange({ ...props.draft, region: event.target.value as KimiRegion | '', modelName: '' })}>
          <option value="">Select a region</option>
          <option value="cn">China</option>
          <option value="global">Global</option>
        </select>
        <small>Use the region where this Kimi API key was created.</small>
      </div>
      <div className="config-field">
        <label htmlFor="kimi-api-key">API key</label>
        <span className="config-secret-input">
          <input id="kimi-api-key" required autoComplete="off" type={keyVisible ? 'text' : 'password'} value={props.draft.apiKey} disabled={locked} onChange={event => { setKeyVisible(false); props.onChange({ ...props.draft, modelName: '', apiKey: event.target.value }); }} />
          <button className="config-icon-button" type="button" disabled={locked} aria-label={`${keyVisible ? 'Hide' : 'Show'} API key`} title={`${keyVisible ? 'Hide' : 'Show'} API key`} onClick={() => setKeyVisible(visible => !visible)}>{keyVisible ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}</button>
        </span>
      </div>
      <div className="config-model-actions">
        <button className="button" type="button" disabled={locked || loading || !props.draft.region || !props.draft.apiKey.trim()} onClick={props.onLoadModels}>
          <RefreshCw size={16} aria-hidden="true" />{loading ? 'Loading models...' : props.models.state === 'error' || (props.models.state === 'ready' && models.length === 0) ? 'Retry models' : 'Load models'}
        </button>
        {loading && <span role="status">Loading available models...</span>}
      </div>
      {props.models.error && <div className="config-error" role="alert"><strong>{props.models.error.message}</strong><span>{props.models.error.action}</span></div>}
      {props.models.state === 'ready' && models.length === 0 && <div className="config-error" role="status"><strong>No eligible models are available.</strong><span>Retry model discovery or use another provider.</span></div>}
      <div className="config-field">
        <div className="config-model-heading">
          {hasModels ? <label htmlFor="kimi-model">Model</label> : <span>Model</span>}
          <small id="kimi-model-rule">Stable K3+ only; no prerelease, latest, or dated models.</small>
        </div>
        {hasModels ? <select id="kimi-model" aria-describedby="kimi-model-rule" required value={props.draft.modelName} disabled={locked} onChange={event => props.onChange({ ...props.draft, modelName: event.target.value })}>
          <option value="">Select a model</option>
          {models.map(model => <option key={model.id} value={model.id}>{model.name}</option>)}
        </select> : props.draft.modelName && <p className="config-model-value mono">{props.draft.modelName}</p>}
      </div>
      {props.saveError && <div className="config-error" role="alert"><strong>{props.saveError.message}</strong><span>{props.saveError.action}</span></div>}
      {props.dirty && !offered && !loading && <p className="field-help">Load models for this region and API key, then select a model before saving.</p>}
      {props.savePending && <p role="status">Saving configuration...</p>}
      <div className="config-form-actions">
        <button className="button" type="button" disabled={locked} onClick={() => { setKeyVisible(false); props.onCancel(); }}>Cancel</button>
        <button className="button button--primary" type="submit" disabled={!canSave}>{props.savePending ? 'Saving...' : 'Save changes'}</button>
      </div>
    </form>
    {props.configured && <div className="config-test-actions"><div><strong>Connection check</strong><span>{props.dirty ? 'Unsaved changes' : 'Saved configuration'}</span></div><button className="button" type="button" disabled={!props.canTest} onClick={props.onTest}>{props.testPending ? 'Testing...' : 'Test connection'}</button></div>}
  </section>;
}
