import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { controlPlaneClient, toApiError } from '../../../api/controlPlaneClient';
import type { PlatformId, StepArtifact, StepArtifactsResponse } from '../../../api/types';
import { SnapshotDiffView } from '../../runs/SnapshotDiffView';
import { CaptureQualifiers } from '../../runs/CaptureQualifiers';

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

function UiTreePre({ content }: { content: string }) {
  return <pre>{content}</pre>;
}
function Qualifiers({ artifact }: { artifact: StepArtifact }) {
  return <CaptureQualifiers artifactId={artifact.artifactId} executionId={artifact.stepExecutionId} occurrence={artifact.captureOccurrence} attempt={artifact.attemptIndex} redacted={artifact.redacted} transformed={artifact.transformed} displayTransformed={artifact.displayTransformed} truncated={artifact.truncated} coverage={artifact.coverage} compaction={artifact.compaction} />;
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
    return wrap(<div className={`step-screenshot-comparison${platform === 'android' ? ' step-screenshot-comparison--android' : ''}`}>
      {matching.map((artifact, index) => <figure key={(artifact.artifactId ?? artifact.phase) + ':' + index} className="step-screenshot-card"><figcaption>{artifact.captureReason ?? (artifact.phase === 'before' ? 'Before' : 'After')}{artifact.attemptIndex ? ' · attempt ' + artifact.attemptIndex : ''}</figcaption>{artifact.contentBase64 ? <img src={`data:${artifact.mimeType};base64,${artifact.contentBase64}`} alt={(platform ?? "Platform") + " " + artifact.phase + " screenshot for " + targetLabel + ", selected Action " + (state.payload?.stepId ?? state.stepId)} /> : <p role="status">{artifact.error ?? artifact.unavailableReason ?? 'Screenshot unavailable.'}</p>}<Qualifiers artifact={artifact} /></figure>)}
    </div>);
  }
  if (!before?.content || !after?.content) {
    return wrap(<div className="ui-snapshot">{matching.map((artifact,index)=><section key={artifact.artifactId ?? index}><div className="evidence-meta">{artifact.captureReason ?? artifact.phase} · {artifact.artifactId ?? 'capture'} · selected Action</div><Qualifiers artifact={artifact}/>{artifact.truncated&&<p role="status">Snapshot content is truncated.</p>}{typeof artifact.content==='string'?<UiTreePre content={artifact.normalizedContent ?? artifact.content}/>:<p role="status">{artifact.error ?? artifact.unavailableReason ?? 'Snapshot unavailable.'}</p>}</section>)}</div>);
  }
  return wrap(<><SnapshotDiffView diff={state.payload?.comparison ?? null}/><details open><summary>All snapshot captures ({matching.length})</summary>{matching.map((artifact,index)=><section key={artifact.artifactId ?? index}><strong>{artifact.captureReason ?? artifact.phase}</strong><Qualifiers artifact={artifact}/>{artifact.content?<UiTreePre content={artifact.normalizedContent ?? artifact.content}/>:<p>{artifact.error ?? 'Unavailable'}</p>}</section>)}</details></>);
}
