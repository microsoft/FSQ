import { Check, Ellipsis, TriangleAlert } from 'lucide-react';
import type { ReadinessRecord, ReadinessResponse, RunMode } from '../../../api/types';
import { PrerequisiteList } from './PrerequisiteList';
import ReactMarkdown from 'react-markdown';
import prerequisiteGuide from '../../../../../../docs/platform-prerequisites.md?raw';
import androidGuide from '../../../../../../docs/android-prerequisites.md?raw';

interface PreflightStatusProps {
  mode: RunMode;
  workspace?: ReadinessRecord;
  provider?: ReadinessRecord;
  target?: ReadinessRecord;
  strict?: ReadinessRecord;
  requiresProvider?: boolean;
  loading: boolean;
  diagnostics?: ReadinessResponse | null;
  macos?: boolean;
  android?: boolean;
  onRecheck?: () => void;
  onRepair?: () => void;
  locked?: boolean;
}

function PreflightItem({ label, record, loading }: { label: string; record?: ReadinessRecord; loading: boolean }) {
  const status = loading ? 'loading' : record?.status ?? 'unavailable';
  const detail = loading ? 'Checking readiness…' : record?.message ?? 'Readiness is unavailable.';
  return <li className={`preflight-item preflight-item--${status}`}>
    <span className="preflight-icon" aria-hidden="true">{status === 'ready' ? <Check/> : status === 'loading' ? <Ellipsis/> : <TriangleAlert/>}</span>
    <span><strong>{label}</strong><small>{detail}</small>{record?.action && status !== 'ready' && <small className="preflight-action">{record.action}</small>}</span>
  </li>;
}

export function PreflightStatus({ mode, workspace, provider, target, strict, requiresProvider, loading, diagnostics, macos, android, onRecheck, onRepair, locked }: PreflightStatusProps) {
  if (macos || android) {
    const label = android ? 'Android' : 'macOS';
    const verdict = mode==='explore'?diagnostics?.commands?.caseCreate:diagnostics?.commands?.caseTest;
    const ready = verdict?.status==='ready' && (!requiresProvider || provider?.status==='ready');
    const message = requiresProvider && provider?.status!=='ready'?provider?.message:verdict?.message;
    return <section className="preflight preflight--macos" aria-labelledby="preflight-title" aria-busy={loading}>
      <div className="preflight-heading"><h3 id="preflight-title">{label} environment</h3><button type="button" className="button" disabled={loading||locked||!onRecheck} onClick={onRecheck}>{loading?'Checking…':'Recheck environment'}</button></div>
      <div className={'preflight-summary '+(ready&&!loading?'preflight-summary--ready':'')} role="status"><strong>{loading?`Checking ${label} environment…`:!diagnostics?'Environment check unavailable':ready?`${label} is ready`:`${label} needs attention`}</strong><p>{loading?'Start is unavailable until checks complete.':!diagnostics?'Recheck the environment to obtain current results.':ready?'Preflight checks passed. Readiness is checked again before starting.':message || 'Review the failed checks below.'}</p></div>
      {!loading && diagnostics?.prerequisites && <PrerequisiteList key={diagnostics.checkedAt} items={diagnostics.prerequisites}/>}
      {!loading && diagnostics && <ul><PreflightItem label="Workspace" record={workspace} loading={false}/><PreflightItem label={mode==='explore'||requiresProvider?'Provider':'Strict runner'} record={mode==='explore'||requiresProvider?provider:strict} loading={false}/></ul>}
      {onRepair && <button className="button prerequisite-repair" type="button" disabled={locked||loading} onClick={onRepair}>Edit target configuration</button>}
      <details className="prerequisite-guide"><summary>{label} installation and troubleshooting</summary><ReactMarkdown>{android ? androidGuide : prerequisiteGuide.split('## macOS')[1] || prerequisiteGuide}</ReactMarkdown></details>
      {diagnostics?.checkedAt && !loading && <p className="field-help">Checked at {new Date(diagnostics.checkedAt).toLocaleTimeString()}</p>}
      <p className="field-help">{android ? 'Checks do not start ADB, initialize device automation or grant permissions. Ready does not guarantee Case success.' : 'Checks do not grant macOS Accessibility or Automation permissions.'}</p>
    </section>;
  }
  return <section className="preflight" aria-labelledby="preflight-title">
    <h3 id="preflight-title">Preflight</h3>
    <ul>
      <PreflightItem label="Target" record={target} loading={loading} />
      <PreflightItem label="Workspace" record={workspace} loading={loading} />
      <PreflightItem label={mode === 'explore' || requiresProvider ? 'Provider' : 'Strict runner'} record={mode === 'explore' || requiresProvider ? provider : strict} loading={loading} />
    </ul>
  </section>;
}
