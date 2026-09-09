import type { ConnectionResult } from '../hooks/useProviderConfig';
import { DialogFrame } from './DialogFrame';

export function ConnectionResultDialog({ result, onClose, returnFocus }: { result: ConnectionResult; onClose: () => void; returnFocus?: HTMLElement | null }) {
  const title = result.success ? 'Connection successful' : 'Connection failed';
  return <DialogFrame title={title} onClose={onClose} returnFocus={returnFocus}>
    {result.success ? <dl className="connection-result">
      <div><dt>Provider</dt><dd>{{ openai: 'OpenAI', azure_openai: 'Azure OpenAI', google_gemini: 'Google Gemini', github_copilot: 'GitHub Copilot' }[result.data.provider]}</dd></div>
      <div><dt>Model</dt><dd className="mono">{result.data.modelName}</dd></div>
      <div><dt>Duration</dt><dd>{result.data.durationMs} ms</dd></div>
    </dl> : <div className="config-error" role="alert"><strong>{result.error.message}</strong><span>{result.error.action}</span></div>}
    <div className="config-dialog-actions"><button className="button button--primary" data-dialog-initial type="button" onClick={onClose}>Done</button></div>
  </DialogFrame>;
}
