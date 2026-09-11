import { useEffect, useRef, useState } from 'react';
import { controlPlaneClient, toApiError } from '../../api/controlPlaneClient';
import type { ApiErrorBody, HistoryResponse, PlatformId, ReportFact, ReportFormat, ReportExportResponse, RunReportResponse, WorkspaceRegistryEntry } from '../../api/types';
import { runHash, type RunRoute } from './runRoute';
import './runs.css';
import { SnapshotDiffView } from './SnapshotDiffView';
import { CaptureQualifiers } from './CaptureQualifiers';

interface Props { route: RunRoute; workspaces: readonly WorkspaceRegistryEntry[]; registryStatus?: 'loading'|'ready'|'error'; registryError?: string; onRetryRegistry?: () => void; onNavigate: (route: RunRoute) => void }
const text = (value: unknown, fallback = 'Unavailable'): string => typeof value === 'string' || typeof value === 'number' ? String(value) : fallback;
const fact = (value: unknown): ReportFact => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as ReportFact : {};
const stepIdentity = (step: ReportFact) => text(step.step_execution_id ?? (step.attempted === false ? null : step.step_id), '');
const artifactIdentity = (artifact: ReportFact) => text(artifact.artifact_id, '');
const outcome = (value: ReportFact) => text(value.status ?? value.outcome, 'Unavailable');
const factRows = (value: unknown): ReportFact[] => Array.isArray(value) ? value.filter(item => item && typeof item === 'object') as ReportFact[] : [];
const eventLabel = (log: ReportFact) => [log.label, log.tool, log.event_kind, log.event_type].find(value => typeof value === 'string' && value.trim()) ?? 'Event';

function Metrics({ metrics }: { metrics: ReportFact }) {
  const entries = Object.entries(metrics).filter(([, value]) => value && typeof value === 'object' && 'value' in value);
  return entries.length ? <dl className="runs-metrics">{entries.map(([name, raw]) => { const value = fact(raw); return <div key={name}><dt>{name.replaceAll('_', ' ')}</dt><dd>{value.value == null ? text(value.unavailable_reason, 'Unmeasured') : text(value.value) + ' ' + text(value.unit, '')}</dd></div>; })}</dl> : <p>No measured metrics are available.</p>;
}
function Processing({ value }: { value: ReportFact }) {
  return Object.keys(value).length ? <>{Object.entries(value).map(([name, item]) => <span key={name}>{name}: {outcome(fact(item))}<br /></span>)}</> : <>Not requested</>;
}
function Logs({ logs, route, omission = {} }: { logs: ReportFact[]; route: RunRoute; omission?: ReportFact }) {
  if (Object.keys(omission).length) return <><div role="status" className="runs-notice"><p>Log display is limited: {text(omission.returned_count)} of {text(omission.original_count)} persisted entries, {text(omission.original_size_bytes)} original bytes. {text(omission.reason)}</p>{factRows(omission.full_artifacts).map((ref, index) => route.workspace && route.platform ? <a key={index} href={controlPlaneClient.historyArtifactUrl(route.workspace, route.platform, text(ref.run_id), text(ref.artifact_id))} download>Download complete log</a> : null)}</div>{logs.length ? <Logs logs={logs} route={route} /> : <p>No log entries for this execution are included in the limited display.</p>}</>;
  return logs.length ? <div className="runs-table-wrap"><table className="runs-table"><thead><tr><th>Time</th><th>Event</th><th>Message</th></tr></thead><tbody>{logs.map((log, index) => <tr key={index}><td>{text(log.time ?? log.timestamp, 'Unknown')}</td><td>{text(eventLabel(log), 'Event')}</td><td>{text(log.message, '')}<Details title="Event facts" value={log} />{log.truncated === true && <small>Log text is truncated.</small>}{fact(log.complete_artifact).artifact_id && route.workspace && route.platform ? <a href={controlPlaneClient.historyArtifactUrl(route.workspace, route.platform, text(fact(log.complete_artifact).run_id), text(fact(log.complete_artifact).artifact_id))} download>Download complete log</a> : null}</td></tr>)}</tbody></table></div> : <p>No persisted logs for this execution.</p>;
}

function Notice({ message, retry, error }: { message: string; retry?: () => void; error?: ApiErrorBody | null }) {
  return <div className="runs-notice" role="alert"><span>{message}</span>{error?.action && <p>{error.action}</p>}{typeof fact(error?.details).reason === 'string' && <p>{String(fact(error?.details).reason)}</p>}{retry && <button className="button" onClick={retry}>Retry</button>}</div>;
}
function Details({ title, value }: { title: string; value: unknown }) {
  const empty = value === null || value === undefined || typeof value === 'object' && Object.keys(value).length === 0;
  return <details className="runs-details"><summary>{title}</summary>{empty ? <p>Unavailable in the persisted evidence.</p> : <pre>{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre>}</details>;
}
function Warnings({ values }: { values: unknown[] }) {
  return values.length ? <div className="runs-notice" role="status">{values.map((value, index) => <p key={index}>{typeof value === 'string' ? value : JSON.stringify(value)}</p>)}</div> : null;
}
function ArtifactView({ artifact, workspace, platform, runId, onRetry }: { artifact: ReportFact; workspace: string; platform: PlatformId; runId: string; onRetry?: () => void }) {
  const id = artifactIdentity(artifact);
  const suppliedContent = artifact.normalized_content ?? artifact.content;
  const [content, setContent] = useState(typeof suppliedContent === 'string' ? suppliedContent : '');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const kind = text(artifact.kind, 'artifact');
  const artifactRun = text(artifact.run_id, runId);
  const url = controlPlaneClient.historyArtifactUrl(workspace, platform, artifactRun, id);
  const previewUrl = typeof artifact.previewUrl === 'string' ? artifact.previewUrl : url;
  const availability = text(artifact.availability ?? artifact.status, 'unknown');
  const displayOmitted = artifact.display_availability === 'omitted' || fact(artifact.display).status === 'omitted' || fact(artifact.display).availability === 'omitted';
  useEffect(() => {
    setError(''); setContent(typeof suppliedContent === 'string' ? suppliedContent : '');
    if (artifact.loadError || displayOmitted || kind === 'screenshot' || !id || typeof suppliedContent === 'string' || ['unavailable', 'missing', 'failed', 'omitted'].includes(availability)) return;
    const controller = new AbortController(); setLoading(true);
    void controlPlaneClient.historyArtifactText(workspace, platform, artifactRun, id, controller.signal).then(value => { if (!controller.signal.aborted) setContent(value); }).catch(error => { if (!controller.signal.aborted) setError(error instanceof Error ? error.message : 'Artifact unavailable.'); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [suppliedContent, id, kind, workspace, platform, artifactRun, availability, displayOmitted, artifact.loadError]);
  return <figure className="runs-artifact"><figcaption><strong>{kind === 'screenshot' ? 'Screenshot' : 'UI snapshot'} · {text(artifact.capture_reason ?? artifact.reason ?? artifact.phase, 'capture')}</strong><span>{availability}{artifact.attempt_index ? ' · attempt ' + text(artifact.attempt_index) : ''}</span></figcaption>
    {artifact.truncated === true && <Notice message="This capture is truncated. It does not represent complete UI state." />}
    {displayOmitted && <Notice message={'Display omitted: ' + text(fact(artifact.display).reason ?? fact(artifact.display).unavailable_reason, 'resource policy')} />}{artifact.unavailable_reason ? <Notice message={text(artifact.unavailable_reason)} /> : null}
    {kind === 'screenshot' && !displayOmitted && !artifact.loadError && !['unavailable', 'missing', 'failed', 'omitted'].includes(availability) ? <img src={previewUrl} alt={platform + ' application target context unavailable; ' + text(artifact.capture_reason ?? artifact.phase, 'Captured') + ' screenshot for ' + id + '; ' + availability + ' evidence'} onError={() => setError('Screenshot is unavailable.')} /> : content ? <pre>{content}</pre> : <p role="status">{loading ? 'Loading persisted snapshot…' : 'No readable content.'}</p>}
    <CaptureQualifiers artifactId={artifact.artifact_id} executionId={artifact.step_execution_id} occurrence={artifact.capture_occurrence} attempt={artifact.attempt_index} redacted={artifact.redacted} transformed={artifact.transformed} displayTransformed={artifact.display_transformed} truncated={artifact.truncated} coverage={fact(artifact.coverage)} compaction={fact(artifact.compaction)} />
    {Boolean(error || artifact.loadError) && <Notice message={error || text(artifact.loadError)} retry={onRetry} />}{id && <a href={url} download>Download artifact</a>}
  </figure>;
}

interface EvidenceSelectionProps { selected?: ReportFact; report: RunReportResponse['report']; route: RunRoute; refreshEpoch: number }
function ExecutionEvidence({ selected, report, route, refreshEpoch }: EvidenceSelectionProps) {
  const selectedId = selected ? stepIdentity(selected) : '';
  const [shown, setShown] = useState<{ step: ReportFact; artifacts: ReportFact[]; diff: ReportFact | null; logs: ReportFact[] } | null>(null);
  const [pending, setPending] = useState(false);
  const urls = useRef<string[]>([]);
  const [retryEpoch, setRetryEpoch] = useState(0);
  const input = JSON.stringify({ selected, artifacts: report.artifacts, comparison: report.comparison.before_after, logs: report.logs });
  useEffect(() => {
    if (!selected || !route.workspace || !route.platform || !route.run) { setShown(null); return; }
    const controller = new AbortController();
    const created: string[] = [];
    let committed = false;
    setPending(true);
    const artifacts = report.artifacts.filter(item => text(item.run_id, route.run) === route.run && text(item.step_execution_id ?? item.step_id, '') === selectedId);
    const load = async (artifact: ReportFact): Promise<ReportFact> => {
      if (artifact.display_availability === 'omitted' || fact(artifact.display).status === 'omitted' || fact(artifact.display).availability === 'omitted') return artifact;
      if (!['available','unknown'].includes(text(artifact.availability ?? artifact.status,'unknown'))) return artifact;
      if (artifact.kind !== 'screenshot') {
        if (typeof artifact.content === 'string' || typeof artifact.normalized_content === 'string') return artifact;
        try { return { ...artifact, content: await controlPlaneClient.historyArtifactText(route.workspace!, route.platform!, route.run!, artifactIdentity(artifact), controller.signal) }; }
        catch (error) { return { ...artifact, loadError: toApiError(error).message }; }
      }
      try {
        const blob = await controlPlaneClient.historyArtifactImage(route.workspace!, route.platform!, route.run!, artifactIdentity(artifact), controller.signal);
        const url = URL.createObjectURL(blob); created.push(url);
        await new Promise<void>((resolve, reject) => {
          const image = new Image();
          const timer = window.setTimeout(() => reject(new Error('Screenshot loading timed out.')), 10000);
          image.onload = () => { window.clearTimeout(timer); resolve(); };
          image.onerror = () => { window.clearTimeout(timer); reject(new Error('Screenshot cannot be decoded.')); };
          controller.signal.addEventListener('abort', () => { window.clearTimeout(timer); reject(new Error('Cancelled.')); }, { once: true });
          image.src = url;
        });
        return { ...artifact, previewUrl: url };
      } catch (error) { return { ...artifact, loadError: toApiError(error).message }; }
    };
    void Promise.all(artifacts.map(load)).then(loaded => {
      if (controller.signal.aborted) return;
      urls.current.forEach(url => URL.revokeObjectURL(url)); urls.current = created; committed = true;
      const diff = factRows(report.comparison.before_after).find(item => item.step_execution_id === selectedId) ?? null;
      setShown({ step: selected, artifacts: loaded, diff, logs: report.logs.filter(log => text(log.step_execution_id ?? log.step_id,'') === selectedId) });
      setPending(false);
    });
    return () => { controller.abort(); if (!committed) created.forEach(url => URL.revokeObjectURL(url)); };
  }, [selectedId, input, route.workspace, route.platform, route.run, refreshEpoch, retryEpoch]);
  useEffect(() => () => urls.current.forEach(url => URL.revokeObjectURL(url)), []);
  if (!selected) return <section className="runs-panel runs-evidence"><h2>Selected execution evidence</h2><Notice message="The selected execution is unavailable in this Run." /></section>;
  const step = shown?.step;
  return <section className="runs-panel runs-evidence"><h2>Selected execution evidence</h2>{pending && <p role="status">{step ? 'Showing evidence for ' + stepIdentity(step) + ' while loading selected execution ' + selectedId + '…' : 'Loading selected execution ' + selectedId + '…'}</p>}{step && shown && <><p><strong>{stepIdentity(step)}</strong> · {outcome(step)}</p>{step.error_message ? <Notice message={text(step.error_message)} /> : null}<Metrics metrics={fact(step.metrics)} /><Details title="Recorded step facts" value={step} />{Object.keys(fact(step.assertion)).length > 0 && <Details title="AI assertion verdict" value={step.assertion} />}{shown.artifacts.length ? <div className="runs-artifacts">{shown.artifacts.map((artifact,index) => <ArtifactView key={artifactIdentity(artifact) + index} artifact={artifact} workspace={route.workspace!} platform={route.platform!} runId={route.run!} onRetry={() => setRetryEpoch(value => value + 1)} />)}</div> : <p>No viewable artifacts for this execution.</p>}<h3>UI snapshot diff · Before / After</h3><SnapshotDiffView diff={shown.diff} /><h3>Structured logs</h3><Logs logs={shown.logs} route={route} omission={fact(fact(report.run.display_omissions).logs)} /></>}</section>;
}

export function RunsPage({ route, workspaces, registryStatus = 'ready', registryError, onRetryRegistry, onNavigate }: Props) {
  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [detail, setDetail] = useState<RunReportResponse | null>(null);
  const [reportFreshness, setReportFreshness] = useState<'unloaded'|'checking'|'current'|'stale'>('unloaded');
  const [loadedSelection, setLoadedSelection] = useState({ baseline: '', related: [] as string[] });
  const [error, setError] = useState<ApiErrorBody | null>(null);
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  const [filters, setFilters] = useState<Record<string, string>>({ limit: '20' });
  const [baselineDraft, setBaselineDraft] = useState('');
  const [baseline, setBaseline] = useState('');
  const [relatedDraft, setRelatedDraft] = useState('');
  const [related, setRelated] = useState<string[]>([]);
  const [format, setFormat] = useState<ReportFormat>('html');
  const [exportResult, setExportResult] = useState<ReportExportResponse | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<ApiErrorBody | null>(null);
  const exportGeneration = useRef(0);
  const exportController = useRef<AbortController | null>(null);
  const identity = [route.workspace, route.platform, route.run].join(':');
  const filtersKey = JSON.stringify(filters);
  useEffect(() => { exportGeneration.current += 1; setBaseline(''); setBaselineDraft(''); setRelated(current => current.length ? [] : current); setRelatedDraft(''); setExportResult(null); setExportError(null); setExporting(false); }, [identity]);
  useEffect(() => {
    setError(null); setHistory(null);
    if (!route.workspace || route.error) return;
    const controller = new AbortController(); setLoading(true); setReportFreshness('checking');
    const request = route.run && route.platform
      ? controlPlaneClient.runReport(route.workspace, route.platform, route.run, baseline || undefined, controller.signal, related).then(value => { if (!controller.signal.aborted) { setDetail(value); setLoadedSelection({ baseline, related }); setReportFreshness('current'); } })
      : controlPlaneClient.history(route.workspace, { ...JSON.parse(filtersKey), ...(route.platform ? { platform: route.platform } : {}) }, controller.signal).then(value => { if (!controller.signal.aborted) setHistory(value); });
    void request.catch(error => { if (!controller.signal.aborted) { setError(toApiError(error)); setReportFreshness('stale'); setExportResult(null); } }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [route.workspace, route.platform, route.run, route.error, filtersKey, baseline, related, revision]);
  useEffect(() => {
    exportGeneration.current += 1;
    exportController.current?.abort();
    setExportResult(null); setExportError(null); setExporting(false);
    return () => { exportGeneration.current += 1; exportController.current?.abort(); };
  }, [identity, baseline, related, format, revision, reportFreshness]);
  const exportReport = () => {
    if (!route.workspace || !route.platform || !route.run || reportFreshness !== 'current') return;
    const generation = ++exportGeneration.current;
    exportController.current?.abort();
    const controller = new AbortController();
    exportController.current = controller;
    setExporting(true); setExportError(null); setExportResult(null);
    void controlPlaneClient.exportReport(route.workspace, route.platform, route.run, format, baseline || undefined, controller.signal, related).then(value => { if (generation === exportGeneration.current) setExportResult(value); }).catch(error => { if (generation === exportGeneration.current) setExportError(toApiError(error)); }).finally(() => { if (generation === exportGeneration.current) setExporting(false); });
  };
  if (route.error) return <div className="runs-page"><h1>Runs</h1><Notice message={route.error} /><button className="button" onClick={() => onNavigate({})}>Choose Workspace</button></div>;
  if (!route.workspace && registryStatus === 'loading') return <div className="runs-page"><h1>Runs</h1><p role="status">Loading registered Workspaces…</p></div>;
  if (!route.workspace && registryStatus === 'error') return <div className="runs-page"><h1>Runs</h1><Notice message={registryError ?? 'Workspace registry unavailable.'} retry={onRetryRegistry} /></div>;
  if (!route.workspace) return <div className="runs-page"><h1>Runs</h1><p>Choose the registered Workspace whose persisted evidence you want to inspect.</p><div className="runs-workspaces">{workspaces.map(workspace => <button className="button" key={workspace.name} onClick={() => onNavigate({ workspace: workspace.name })}>{workspace.name}</button>)}</div>{!workspaces.length && <p>No registered Workspaces are available.</p>}</div>;
  const report = detail?.workspace === route.workspace && detail?.run_id === route.run && detail?.platform === route.platform ? detail.report : undefined;
  const selectionStale = !!report && (baseline !== loadedSelection.baseline || JSON.stringify(related) !== JSON.stringify(loadedSelection.related));
  const steps = report?.steps.filter(step => !step.container) ?? [];
  const selected = steps.find(step => stepIdentity(step) === route.step) ?? (!route.step ? steps.find(step => ['failed', 'error'].includes(outcome(step))) ?? steps[0] : undefined);
  const selectedId = selected ? stepIdentity(selected) : '';
  const gate = fact(report?.run.gate);
  const failure = fact(report?.execution.first_failure ?? report?.execution.primary_failure);
  const recoveredAttempt = report?.execution.outcome === 'success';
  const baselineComparison = fact(report?.comparison.baseline_current);
  const baselineSteps = Array.isArray(baselineComparison.steps) ? baselineComparison.steps as ReportFact[] : [];
  const baselineDiff = baselineSteps.find(item => text(fact(item.current).step_execution_id, '') === selectedId);
  const updateFilter = (key: string, value: string) => setFilters(current => ({ ...current, [key]: value }));
  return <div className="runs-page"><header className="runs-heading"><div><p className="runs-eyebrow">Persisted evidence · {route.workspace}{route.platform ? ' · ' + route.platform : ''}</p><h1>{route.run ?? 'Runs'}</h1></div><div className="runs-controls">{route.run && <button className="button" onClick={() => onNavigate({ workspace: route.workspace, platform: route.platform })}>All Runs</button>}<button className="button" onClick={() => setRevision(value => value + 1)}>Refresh</button></div></header>
    {!route.run && <form className="runs-filters" onSubmit={event => event.preventDefault()}><label>Platform<select aria-label="Platform" value={route.platform ?? ''} onChange={event => onNavigate({ workspace: route.workspace, platform: event.target.value as PlatformId || undefined })}><option value="">All platforms</option>{['web', 'android', 'windows', 'macos'].map(value => <option key={value}>{value}</option>)}</select></label><label>Status<select aria-label="Status" value={filters.status ?? ''} onChange={event => updateFilter('status', event.target.value)}><option value="">All statuses</option>{['success', 'failed', 'error', 'inconclusive', 'cancelled', 'interrupted', 'preparing', 'running', 'finalizing'].map(value => <option key={value}>{value}</option>)}</select></label><label>Mode<select aria-label="Mode" value={filters.mode ?? ''} onChange={event => updateFilter('mode', event.target.value)}><option value="">All modes</option><option>strict</option><option>explore</option></select></label><label>Case<input value={filters.case ?? ''} onChange={event => updateFilter('case', event.target.value)} /></label><label>Since<input placeholder="7d or 24h" aria-describedby="runs-since-help" value={filters.since ?? ''} onChange={event => updateFilter('since', event.target.value)} /></label><label>Limit<select aria-label="Limit" value={filters.limit} onChange={event => updateFilter('limit', event.target.value)}>{[20, 50, 100, 200].map(value => <option key={value}>{value}</option>)}</select></label><small id="runs-since-help">Positive duration, such as 7d or 24h.</small></form>}
    {loading && <p role="status">Loading persisted Run facts…</p>}{error && <Notice message={error.message} error={error} retry={() => setRevision(value => value + 1)} />}
    {history && <><p className="runs-counts">{history.returned_count} returned · {history.matched_count} matching Runs</p><Warnings values={history.warnings} />{history.truncated && <Notice message={'Showing ' + history.returned_count + ' of ' + history.matched_count + ' matching Runs. Narrow the filters or increase the limit.'} />}{!history.runs.length ? <p>{Object.entries(filters).some(([key,value]) => key !== 'limit' && !!value) ? 'No Runs match these filters. Adjust the filters to inspect other history.' : 'No Runs have been recorded in this Workspace scope.'}</p> : <div className="runs-table-wrap"><table className="runs-table"><thead><tr><th>Run / source</th><th>Platform</th><th>Mode</th><th>Execution</th><th>Started</th><th>Duration</th></tr></thead><tbody>{history.runs.map(run => <tr key={run.platform + ':' + run.run_id}><td><a href={runHash({ workspace: route.workspace, platform: run.platform, run: run.run_id })} onClick={event => { event.preventDefault(); onNavigate({ workspace: route.workspace, platform: run.platform, run: run.run_id }); }}>{run.run_id}</a><small>{text(run.source?.case_id ?? run.source?.goal_summary, 'Source unavailable')}</small>{run.warnings.length > 0 && <Warnings values={run.warnings} />}</td><td>{run.platform}</td><td>{run.mode ?? 'Unknown'}</td><td><span className={'runs-status runs-status--' + run.status}>{run.status ?? 'Unknown'}</span>{Boolean(run.result?.summary) && <small>{text(run.result?.summary)}</small>}{Boolean(run.result?.failed_step) && <small>{run.status === 'success' ? 'Recovered attempt: ' : 'Failed execution: '}{text(run.result?.failed_step)}</small>}{Boolean(run.evidence?.status) && <small>Evidence: {text(run.evidence?.status)}</small>}{run.liveness && <small>Owner: {run.liveness}</small>}{run.persisted_status && run.persisted_status !== run.status && <small>Persisted: {run.persisted_status}</small>}</td><td>{run.started_at ?? 'Unknown'}</td><td>{run.duration_ms == null ? 'Unmeasured' : run.duration_ms + ' ms'}</td></tr>)}</tbody></table></div>}</>}
    {report && detail && <>{reportFreshness !== 'current' && <Notice message={reportFreshness === 'stale' ? 'Previously loaded report is stale. Historical scope could not be revalidated; its last recorded outcome is shown for reference only. Export is disabled until access is restored.' : 'Revalidating historical scope. The previously loaded outcome is shown for reference until validation completes.'} />}{selectionStale && <Notice message={'Showing the previous report: ' + (loadedSelection.baseline ? 'baseline ' + loadedSelection.baseline : 'Before / After') + '. Requested comparison is ' + (loading ? 'loading.' : 'unavailable.')} />}<Warnings values={[...detail.warnings, ...report.warnings]} /><section className="runs-result" aria-labelledby="run-result-heading"><div className="runs-result-heading"><h2 id="run-result-heading">Result and failure</h2><span className={'runs-status runs-status--' + outcome(gate)}>Gate: {outcome(gate)}</span></div><dl className="runs-outcomes">{[['Execution', report.execution], ['Verification', report.verification], ['Evidence', report.evidence], ['Processing', report.processing]].map(([name, value]) => <div key={name as string}><dt>{name as string}</dt><dd>{name === 'Processing' ? <Processing value={value as ReportFact} /> : outcome(value as ReportFact)}</dd></div>)}</dl><p>{text(report.execution.summary ?? report.run.summary, 'Inspect recorded steps and evidence for the execution conclusion.')}</p>{Object.keys(failure).length > 0 && <div className="runs-failure"><strong>{recoveredAttempt ? 'Earlier failed attempt · ' : ''}{text(failure.failure_category ?? failure.category, 'Recorded failure')}</strong><p>{text(failure.error_message ?? failure.message, 'Inspect the linked execution for details.')}</p>{stepIdentity(failure) && <button className="button" onClick={() => onNavigate({ ...route, step: stepIdentity(failure) })}>Inspect failure evidence</button>}<small>{recoveredAttempt ? 'Recorded attempt; the final execution outcome is preserved above.' : 'Recorded execution fact'}</small></div>}{factRows(report.execution.interpretations).map((item,index) => <section key={index} className="runs-interpretation"><strong>{text(item.label, 'Interpretation')}</strong><small>{text(item.kind)} · {text(item.availability)}</small><p>{text(item.text ?? item.unavailable_reason,'No content available.')}</p>{factRows(item.references).map((ref,refIndex) => ref.artifact_id && route.workspace && route.platform ? <a key={refIndex} href={controlPlaneClient.historyArtifactUrl(route.workspace,route.platform,text(ref.run_id),text(ref.artifact_id))} download>Supporting artifact</a> : ref.step_execution_id ? <button key={refIndex} className="button" onClick={() => onNavigate({...route,step:text(ref.step_execution_id)})}>Supporting execution</button> : null)}</section>)}<Details title="Failure facts and gate reasons" value={report.execution.first_failure ?? report.execution.failure ?? report.execution.primary_failure ?? gate.reasons} /><Details title="Source and provenance" value={report.source} /></section>
    <div className="runs-workbench"><section className="runs-panel runs-timeline"><h2>Action timeline</h2>{!steps.length ? <p>No execution steps are available. This is not a passing test.</p> : <ol>{steps.map((step, index) => { const id = stepIdentity(step); return <li key={id || index}><button disabled={!id} aria-pressed={id === selectedId} onClick={() => onNavigate({ ...route, step: id })}><span><strong>{text(step.authored_action_name ?? step.action_name ?? step.capability_name, id || 'Unexecuted action ' + (index + 1))}</strong><small>{text(step.lifecycle_phase ?? step.phase, 'case')} · {text(step.kind ?? step.step_kind, 'action')}{step.attempt_index ? ' · attempt ' + text(step.attempt_index) : ''}</small></span><span className={'runs-status runs-status--' + outcome(step)}>{outcome(step)}</span></button></li>; })}</ol>}<Details title="Plans and helper activity" value={report.tool_calls.filter(call => !['common', 'platform', 'driver'].includes(text(call.tool_origin, '')))} /></section>
    <ExecutionEvidence selected={selected} report={report} route={route} refreshEpoch={revision} /></div>
    <section className="runs-panel"><h2>Comparison</h2><p>Before / After describes this execution. A baseline compares independent Runs; observed changes do not establish a regression verdict.</p><form className="runs-controls" onSubmit={event => { event.preventDefault(); setBaseline(baselineDraft.trim()); }}><label>Baseline Run ID<input value={baselineDraft} onChange={event => setBaselineDraft(event.target.value)} placeholder="Same Workspace and platform" /></label><button className="button" type="submit">Compare baseline</button>{baseline && <button className="button" type="button" onClick={() => { setBaseline(''); setBaselineDraft(''); }}>Show Before / After</button>}</form>{loadedSelection.baseline && <><p>{text(baselineComparison.status)}</p><SnapshotDiffView diff={baselineDiff ? fact(baselineDiff.snapshot) : null} mode="baseline_current" baselineRunId={loadedSelection.baseline} currentRunId={route.run} /></>}<Details title="Shared comparison facts" value={report.comparison} /></section>
    <section className="runs-panel"><h2>Strict replay and lineage</h2><p>Strict execution has no planning-agent loop. Authored AI assertions may still use a Provider. Screenshot video is visual playback, not deterministic execution.</p>{factRows(report.lineage.related_runs).map(related => <p key={text(fact(related.run).run_id ?? related.run_id)}><a href={runHash({ workspace: route.workspace, platform: route.platform, run: text(fact(related.run).run_id ?? related.run_id) })} onClick={event => { event.preventDefault(); onNavigate({ workspace: route.workspace, platform: route.platform, run: text(fact(related.run).run_id ?? related.run_id) }); }}>{text(fact(related.run).run_id ?? related.run_id)}</a> · {outcome(fact(related.execution))}</p>)}<form className="runs-controls" onSubmit={event => { event.preventDefault(); const ids = [...new Set(relatedDraft.split(',').map(value => value.trim()).filter(Boolean))]; if (ids.length > 8) { setError({code:'invalid_related',message:'Select at most 8 related Runs.',action:'Remove extra Run IDs.'}); return; } setRelated(ids); }}><label>Related Run IDs<input value={relatedDraft} onChange={event => setRelatedDraft(event.target.value)} placeholder="Comma-separated, up to 8" /></label><button className="button" type="submit">Load related Runs</button></form><Details title="Recorded relationships and strict results" value={report.lineage} /></section>
    <section className="runs-panel"><h2>Downloads and inventory</h2><p>Exports preserve the original outcome. Local evidence can contain application data; public sharing uses a separately reviewed export.</p><div className="runs-controls"><label>Format<select aria-label="Format" value={format} onChange={event => setFormat(event.target.value as ReportFormat)}><option value="html">Portable HTML</option><option value="json">JSON</option><option value="junit">JUnit</option><option value="bundle">Evidence bundle</option></select></label><button className="button button--primary" disabled={exporting || loading || selectionStale || reportFreshness !== 'current'} onClick={exportReport}>{exporting ? 'Exporting report…' : 'Export report'}</button></div>{exportError && <Notice message={exportError.message} error={exportError} />}{exportResult && <div role="status"><p>Report export complete.</p>{exportResult.files.map(file => <a className="runs-download" key={file.file_id} href={file.download_url} download>{file.name} · {file.size_bytes} bytes</a>)}<Warnings values={exportResult.warnings} /></div>}<Details title="Artifact inventory and integrity" value={report.artifacts} /><h3>Measured Run metrics</h3><Metrics metrics={report.metrics} /><Details title="Usage and metric provenance" value={report.metrics} /></section></>}
  </div>;
}
