import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { controlPlaneClient, toApiError } from '../../../api/controlPlaneClient';
import type { PlatformId, StepArtifact, StepArtifactsResponse } from '../../../api/types';
import { changedSegments, diffLines } from '../replay/uiDiff';
import { formatUiTreeContent, isStructuredXmlTree } from '../replay/uiTreeFormat';

interface Props {
  requestId: string;
  stepId: string;
  kind: 'screen' | 'ui-tree';
  platform: PlatformId | null;
  targetLabel?: string;
}

function preferred(artifacts: StepArtifact[], phase: 'before' | 'after') {
  return artifacts.find((artifact) => artifact.phase === phase) ?? null;
}

function InlineLine({ before, after, side }: { before: string; after: string; side: 'before' | 'after' }) {
  const segments = changedSegments(before, after)[side];
  return <>{segments[0]}{segments[1] && <mark>{segments[1]}</mark>}{segments[2]}</>;
}

function UiTreePre({ content }: { content: string }) {
  const formatted = formatUiTreeContent(content);
  const structured = isStructuredXmlTree(content);
  return <pre aria-label={structured ? 'Structured XML UI Tree' : undefined}>{formatted}</pre>;
}

interface StepEvidenceState {
  status: 'loading' | 'available' | 'empty' | 'error';
  requestId: string;
  stepId: string;
  payload: StepArtifactsResponse | null;
  message: string;
}

export function StepEvidenceView({ requestId, stepId, kind, platform, targetLabel = 'selected target' }: Props) {
  const [cache] = useState(() => new Map<string, StepArtifactsResponse>());
  const [state, setState] = useState<StepEvidenceState>({ status: 'loading', requestId, stepId, payload: null, message: '' });
  const cacheKey = `${requestId}:${stepId}`;
  useEffect(() => {
    const cached = cache.get(cacheKey);
    if (cached) {
      setState({ status: cached.available ? 'available' : 'empty', requestId, stepId, payload: cached, message: '' });
      return;
    }
    const controller = new AbortController();
    setState((current) => ({
      status: 'loading',
      requestId,
      stepId,
      payload: current.requestId === requestId ? current.payload : null,
      message: '',
    }));
    void controlPlaneClient.stepArtifacts(requestId, stepId, controller.signal).then((data) => {
      if (controller.signal.aborted) return;
      cache.set(cacheKey, data);
      setState({ status: data.available ? 'available' : 'empty', requestId, stepId, payload: data, message: '' });
    }).catch((error) => {
      if (!controller.signal.aborted) setState({ status: 'error', requestId, stepId, payload: null, message: toApiError(error).message });
    });
    return () => controller.abort();
  }, [cache, cacheKey, requestId, stepId]);
  const artifacts = useMemo(() => state.payload?.artifacts ?? [], [state.payload]);
  const pending = state.status === 'loading' && Boolean(state.payload);
  const wrap = (content: ReactNode) => <div className="step-evidence-shell">{content}{pending && <div className="step-evidence-pending" role="status">Updating selected Action evidence…</div>}</div>;
  if (state.status === 'loading' && !state.payload) return <div className="evidence-message" role="status"><strong>Preparing selected Action evidence</strong><p>Fetching persisted artifacts…</p></div>;
  if (state.status === 'error') return <div className="evidence-message evidence-message--error" role="alert"><strong>Action evidence failed to load</strong><p>{state.message}</p></div>;
  const expectedKind = kind === 'screen' ? 'screenshot' : 'ui_snapshot';
  const matching = artifacts.filter((artifact) => artifact.kind === expectedKind);
  if (state.status === 'empty' || !matching.length) return <div className="evidence-message" role="status"><strong>{kind === 'screen' ? 'No screenshots for this Action' : 'No UI Tree for this Action'}</strong><p>The run remains valid; this Action did not capture that evidence kind.</p></div>;
  const before = preferred(matching, 'before');
  const after = preferred(matching, 'after');
  if (kind === 'screen') {
    const shown = [before, after].filter((item): item is StepArtifact => Boolean(item?.contentBase64));
    if (!shown.length) return <div className="evidence-message" role="alert"><strong>Action screenshots unavailable</strong><p>{matching.map((item) => item.error).filter(Boolean).join(' ')}</p></div>;
    return wrap(<div className={`step-screenshot-comparison${platform === 'android' ? ' step-screenshot-comparison--android' : ''}`}>
      {shown.map((artifact) => <figure key={artifact.phase} className="step-screenshot-card"><figcaption>{artifact.phase === 'before' ? 'Before' : 'After'}</figcaption><img src={`data:${artifact.mimeType};base64,${artifact.contentBase64}`} alt={(platform ?? "Platform") + " " + artifact.phase + " screenshot for " + targetLabel + ", selected Action " + (state.payload?.stepId ?? state.stepId)} /></figure>)}
    </div>);
  }
  if (!before?.content || !after?.content) {
    const only = before?.content ? before : after;
    return only?.content ? wrap(<div className="ui-snapshot"><div className="evidence-meta">{only.phase === 'before' ? 'Before' : 'After'} · selected Action</div><UiTreePre content={only.content} /></div>) : <div className="evidence-message" role="alert"><strong>Action UI Tree unavailable</strong><p>{matching.map((item) => item.error).filter(Boolean).join(' ')}</p></div>;
  }
  const rows = diffLines(formatUiTreeContent(before.content), formatUiTreeContent(after.content));
  return wrap(<div className="ui-diff" aria-label="Before and After UI Tree diff">
    <div className="ui-diff-header"><strong>Before</strong><strong>After</strong></div>
    <div className="ui-diff-body">
      <div className="ui-diff-pane">{rows.map((row, index) => <div key={index} className={`ui-diff-row ui-diff-row--${row.kind}`}><span>{row.beforeNumber ?? ''}</span><code>{row.kind === 'changed' ? <InlineLine before={row.before} after={row.after} side="before" /> : row.before}</code></div>)}</div>
      <div className="ui-diff-pane">{rows.map((row, index) => <div key={index} className={`ui-diff-row ui-diff-row--${row.kind}`}><span>{row.afterNumber ?? ''}</span><code>{row.kind === 'changed' ? <InlineLine before={row.before} after={row.after} side="after" /> : row.after}</code></div>)}</div>
    </div>
  </div>);
}
