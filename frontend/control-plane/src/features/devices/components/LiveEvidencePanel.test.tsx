import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ControlPlaneApiError, controlPlaneClient } from '../../../api/controlPlaneClient';
import type { RunSnapshot } from '../../../api/types';
import { LiveEvidencePanel } from './LiveEvidencePanel';

const snapshot: RunSnapshot = {
  requestId: 'request', runId: 'run', workspaceName: 'test', platform: 'web', targetId: 'chrome', mode: 'explore', status: 'running',
  source: { goal: 'Verify' }, startedAt: '', completedAt: null, cancelRequested: false,
  events: [{ sequence: 1, time: '2026-08-11T12:00:00Z', level: 'info', phase: 'execution', tool: 'clickOn', status: 'completed', message: 'Clicked safely' }],
  activeStep: null, result: null, summary: 'Running', screenshotRevision: 0, uiSnapshotRevision: 0,
  evidenceAvailable: false, reportAvailable: false, terminal: false,
};

it('provides keyboard-operable evidence tabs and structured logs', async () => {
  const onTabChange = vi.fn();
  const { rerender } = render(<LiveEvidencePanel tab="screen" snapshot={snapshot} platform="web" targetLabel="Chrome" onTabChange={onTabChange} />);
  const screenTab = screen.getByRole('tab', { name: 'Screen' });
  screenTab.focus();
  await userEvent.keyboard('{ArrowRight}');
  expect(onTabChange).toHaveBeenCalledWith('ui-tree');
  await userEvent.keyboard('{End}');
  expect(onTabChange).toHaveBeenCalledWith('logs');
  await userEvent.keyboard('{Home}');
  expect(onTabChange).toHaveBeenCalledWith('screen');
  rerender(<LiveEvidencePanel tab="logs" snapshot={snapshot} platform="web" targetLabel="Chrome" onTabChange={onTabChange} />);
  expect(screen.getByRole('table', { name: 'Structured run logs' })).toBeInTheDocument();
  expect(screen.getByText('Clicked safely')).toBeInTheDocument();
  expect(screen.queryByText(JSON.stringify(snapshot.events[0]))).not.toBeInTheDocument();
});

it('distinguishes screen loading, unavailable, failed, and available states', async () => {
  let resolveScreen!: (blob: Blob) => void;
  vi.spyOn(controlPlaneClient, 'screen').mockReturnValue(new Promise((resolve) => { resolveScreen = resolve; }));
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:screen'), revokeObjectURL: vi.fn() });
  const revised = { ...snapshot, screenshotRevision: 1 };
  const { rerender } = render(<LiveEvidencePanel tab="screen" snapshot={revised} platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  expect(screen.getByText('Loading screen')).toBeInTheDocument();
  resolveScreen(new Blob(['png'], { type: 'image/png' }));
  expect(await screen.findByRole('img', { name: /screenshot evidence/i })).toHaveAttribute('src', 'blob:screen');

  vi.mocked(controlPlaneClient.screen).mockRejectedValueOnce(new ControlPlaneApiError(404, { code: 'evidence_unavailable', message: 'missing', action: 'wait' }));
  rerender(<LiveEvidencePanel tab="screen" snapshot={{ ...revised, screenshotRevision: 2 }} platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  expect(await screen.findByText('Screen unavailable')).toBeInTheDocument();
  vi.mocked(controlPlaneClient.screen).mockRejectedValueOnce(new Error('broken'));
  rerender(<LiveEvidencePanel tab="screen" snapshot={{ ...revised, screenshotRevision: 3 }} platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Screen failed to load');
  vi.unstubAllGlobals();
});

it('distinguishes UI Tree loading, unavailable, oversized, failed, and available states', async () => {
  let resolveTree!: (value: Awaited<ReturnType<typeof controlPlaneClient.uiSnapshot>>) => void;
  vi.spyOn(controlPlaneClient, 'uiSnapshot').mockReturnValue(new Promise((resolve) => { resolveTree = resolve; }));
  const revised = { ...snapshot, uiSnapshotRevision: 1 };
  const { rerender } = render(<LiveEvidencePanel tab="ui-tree" snapshot={revised} platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  expect(screen.getByText('Loading UI Tree')).toBeInTheDocument();
  resolveTree({ revision: 1, timestamp: null, stepId: 'step-1', mimeType: 'application/json', format: 'json', content: '{"safe":true}' });
  expect(await screen.findByText('{"safe":true}')).toBeInTheDocument();

  vi.mocked(controlPlaneClient.uiSnapshot).mockRejectedValueOnce(new ControlPlaneApiError(404, { code: 'evidence_unavailable', message: 'missing', action: 'wait' }));
  rerender(<LiveEvidencePanel tab="ui-tree" snapshot={{ ...revised, uiSnapshotRevision: 2 }} platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  expect(await screen.findByText('UI Tree unavailable')).toBeInTheDocument();
  vi.mocked(controlPlaneClient.uiSnapshot).mockRejectedValueOnce(new ControlPlaneApiError(413, { code: 'evidence_too_large', message: 'large', action: 'inspect' }));
  rerender(<LiveEvidencePanel tab="ui-tree" snapshot={{ ...revised, uiSnapshotRevision: 3 }} platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  expect(await screen.findByText('UI Tree is too large to display')).toBeInTheDocument();
  vi.mocked(controlPlaneClient.uiSnapshot).mockRejectedValueOnce(new Error('broken'));
  rerender(<LiveEvidencePanel tab="ui-tree" snapshot={{ ...revised, uiSnapshotRevision: 4 }} platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('UI Tree failed to load');
});

it('renders XML UI Tree snapshots as readable structured trees', async () => {
  vi.spyOn(controlPlaneClient, 'uiSnapshot').mockResolvedValue({
    revision: 1,
    timestamp: null,
    stepId: 'step-1',
    mimeType: 'application/xml',
    format: 'xml',
    content: '<hierarchy><node text="Sign in" class="Button" bounds="[0,0][100,40]" /></hierarchy>',
  });
  render(<LiveEvidencePanel tab="ui-tree" snapshot={{ ...snapshot, uiSnapshotRevision: 1 }} platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);

  expect(await screen.findByLabelText('Structured XML UI Tree')).toHaveTextContent('node text="Sign in" class="Button" bounds="[0,0][100,40]"');
  expect(screen.queryByText(/<hierarchy>/)).not.toBeInTheDocument();
});

it('renders Android JSON-wrapped XML UI Tree snapshots as readable structured trees', async () => {
  vi.spyOn(controlPlaneClient, 'uiSnapshot').mockResolvedValue({
    revision: 1,
    timestamp: null,
    stepId: 'step-1',
    mimeType: 'application/json',
    format: 'json',
    content: JSON.stringify({ xml: '<hierarchy><node text="Continue" bounds="[1,2][3,4]" /></hierarchy>' }),
  });
  render(<LiveEvidencePanel tab="ui-tree" snapshot={{ ...snapshot, platform: 'android', uiSnapshotRevision: 1 }} platform="android" targetLabel="Device" onTabChange={vi.fn()} />);

  expect(await screen.findByLabelText('Structured XML UI Tree')).toHaveTextContent('node text="Continue" bounds="[1,2][3,4]"');
  expect(screen.queryByText(/"xml"/)).not.toBeInTheDocument();
});

it('shows an explicit not-yet-captured screen state', () => {
  render(<LiveEvidencePanel tab="screen" snapshot={snapshot} platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  expect(screen.getByText('Screen not yet captured')).toBeInTheDocument();
});

it('shows selected Action screenshot comparison and UI Tree diff', async () => {
  const terminal = { ...snapshot, terminal: true, status: 'success' as const, completedAt: '2026-08-11T12:00:10Z' };
  vi.spyOn(controlPlaneClient, 'stepArtifacts').mockResolvedValue({
    available: true,
    stepId: 'step-1',
    message: null,
    artifacts: [
      { kind: 'screenshot', phase: 'before', timestamp: null, mimeType: 'image/png', contentBase64: 'YmVmb3Jl' },
      { kind: 'screenshot', phase: 'after', timestamp: null, mimeType: 'image/png', contentBase64: 'YWZ0ZXI=' },
      { kind: 'ui_snapshot', phase: 'before', timestamp: null, mimeType: 'application/json', content: '{"value":"before"}' },
      { kind: 'ui_snapshot', phase: 'after', timestamp: null, mimeType: 'application/json', content: '{"value":"after"}' },
    ],
    comparison: { status: 'changed', rows: [{ kind: 'changed', before: 'before', after: 'after', before_number: 1, after_number: 1 }] },
  });
  const { container, rerender } = render(<LiveEvidencePanel tab="screen" snapshot={terminal} selectedStepId="step-1" platform="web" targetLabel="Chrome" onTabChange={vi.fn()} onClearStep={vi.fn()} />);
  expect(await screen.findByRole('img', { name: /web before screenshot for Chrome, selected Action/ })).toBeInTheDocument();
  expect(screen.getByRole('img', { name: /web after screenshot for Chrome, selected Action/ })).toBeInTheDocument();
  expect([...container.querySelectorAll('.step-screenshot-card figcaption')].map((caption) => caption.textContent)).toEqual(['Before', 'After']);
  rerender(<LiveEvidencePanel tab="ui-tree" snapshot={terminal} selectedStepId="step-1" platform="web" targetLabel="Chrome" onTabChange={vi.fn()} onClearStep={vi.fn()} />);
  expect(await screen.findByLabelText('Before and After UI Tree diff')).toHaveTextContent('before');
  expect(screen.getByLabelText('Before and After UI Tree diff')).toHaveTextContent('after');
});

it('keeps selected Action screenshots visible while the next Action evidence loads', async () => {
  let resolveStep2!: (value: Awaited<ReturnType<typeof controlPlaneClient.stepArtifacts>>) => void;
  vi.spyOn(controlPlaneClient, 'stepArtifacts').mockImplementation((_requestId, stepId) => stepId === 'step-1'
    ? Promise.resolve({
      available: true,
      stepId: 'step-1',
      message: null,
      artifacts: [{ kind: 'screenshot', phase: 'before', timestamp: null, mimeType: 'image/png', contentBase64: 'c3RlcC0x' }],
    })
    : new Promise((resolve) => { resolveStep2 = resolve; }));
  const terminal = { ...snapshot, terminal: true, status: 'success' as const, completedAt: '2026-08-11T12:00:10Z' };
  const { rerender } = render(<LiveEvidencePanel tab="screen" snapshot={terminal} selectedStepId="step-1" platform="web" targetLabel="Chrome" onTabChange={vi.fn()} onClearStep={vi.fn()} />);

  expect(await screen.findByRole('img', { name: /web before screenshot for Chrome, selected Action/ })).toHaveAttribute('src', 'data:image/png;base64,c3RlcC0x');
  rerender(<LiveEvidencePanel tab="screen" snapshot={terminal} selectedStepId="step-2" platform="web" targetLabel="Chrome" onTabChange={vi.fn()} onClearStep={vi.fn()} />);

  expect(screen.queryByText('Loading Action evidence')).not.toBeInTheDocument();
  expect(screen.queryByText('Reading persisted Before and After artifacts…')).not.toBeInTheDocument();
  expect(screen.getByRole('img', { name: /web before screenshot for Chrome, selected Action/ })).toHaveAttribute('src', 'data:image/png;base64,c3RlcC0x');
  expect(screen.getByRole('status')).toHaveTextContent('Updating selected Action evidence');
  expect(screen.getByRole('img')).toHaveAccessibleName('web before screenshot for Chrome, selected Action step-1');
  resolveStep2({
    available: true,
    stepId: 'step-2',
    message: null,
    artifacts: [{ kind: 'screenshot', phase: 'before', timestamp: null, mimeType: 'image/png', contentBase64: 'c3RlcC0y' }],
  });
  await waitFor(() => expect(screen.getByRole('img', { name: 'web before screenshot for Chrome, selected Action step-2' })).toHaveAttribute('src', 'data:image/png;base64,c3RlcC0y'));
});

it('uses backend-formatted XML UI Tree evidence and comparison rows', async () => {
  const terminal = { ...snapshot, terminal: true, status: 'success' as const, completedAt: '2026-08-11T12:00:10Z' };
  vi.spyOn(controlPlaneClient, 'stepArtifacts').mockResolvedValue({
    available: true,
    stepId: 'step-1',
    message: null,
    artifacts: [
      { kind: 'ui_snapshot', phase: 'before', timestamp: null, mimeType: 'application/xml', content: '<hierarchy><node text="Before" class="Text" /></hierarchy>', normalizedContent: 'node text="Before" class="Text"' },
      { kind: 'ui_snapshot', phase: 'after', timestamp: null, mimeType: 'application/xml', content: '<hierarchy><node text="After" class="Text" /></hierarchy>', normalizedContent: 'node text="After" class="Text"' },
    ],
    comparison: { status: 'changed', rows: [{ kind: 'changed', before: 'node text="Before" class="Text"', after: 'node text="After" class="Text"', before_number: 1, after_number: 1 }] },
  });
  render(<LiveEvidencePanel tab="ui-tree" snapshot={terminal} selectedStepId="step-1" platform="web" targetLabel="Chrome" onTabChange={vi.fn()} onClearStep={vi.fn()} />);

  expect(await screen.findByLabelText('Before and After UI Tree diff')).toHaveTextContent('node text="Before" class="Text"');
  expect(screen.getByLabelText('Before and After UI Tree diff')).toHaveTextContent('node text="After" class="Text"');
  expect(screen.queryByText(/<hierarchy>/)).not.toBeInTheDocument();
});

it.each([
  '<label role="status">Purchase failed</label>',
  '<label xml:space="preserve">  Purchase\n  failed  </label>',
  '{"xml":"<label>Purchase failed</label>","metadata":{"source":"server"}}',
  '<malformed>Purchase failed',
])('retains one-sided backend snapshot text verbatim: %s', async content => {
  vi.spyOn(controlPlaneClient, 'stepArtifacts').mockResolvedValue({ available: true, stepId: 'step-1', message: null, artifacts: [
    {kind: 'ui_snapshot', phase: 'before', timestamp: null, mimeType: 'application/xml', content: 'raw input', normalizedContent: content},
  ]});
  const {container} = render(<LiveEvidencePanel tab="ui-tree" snapshot={{...snapshot, terminal:true}} selectedStepId="step-1" platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  await waitFor(() => expect(container.querySelector('pre')?.textContent).toBe(content));
});

it.each([true, false])('retains failed and repeated screenshot identities with readable capture %s', async readable => {
  vi.spyOn(controlPlaneClient, 'stepArtifacts').mockResolvedValue({ available: true, stepId: 'step-1', message: null, artifacts: [
    ...(readable ? [{kind: 'screenshot' as const, phase: 'before' as const, timestamp:null, mimeType:'image/png', artifactId:'before-capture', contentBase64:'cG5n'}] : []),
    {kind:'screenshot',phase:'after',timestamp:null,mimeType:'image/png',artifactId:'after-failed',unavailableReason:'capture_timeout'},
    {kind:'screenshot',phase:'after',timestamp:null,mimeType:'image/png',artifactId:'retry-failed',unavailableReason:'session_unavailable',captureOccurrence:2},
  ]});
  render(<LiveEvidencePanel tab="screen" snapshot={{...snapshot,terminal:true}} selectedStepId="step-1" platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  expect(await screen.findByText('capture_timeout')).toBeVisible();
  expect(screen.getByText('session_unavailable')).toBeVisible();
  expect(screen.getByText(/after-failed/)).toBeVisible();
  expect(screen.getByText(/retry-failed/)).toBeVisible();
  expect(screen.queryAllByRole('img')).toHaveLength(readable ? 1 : 0);
});

it('uses the persisted replay video for a terminal run without Action selection', async () => {
  vi.spyOn(controlPlaneClient, 'replayVideo').mockResolvedValue({ available: true, videoUrl: '/stored.webm', mimeType: 'video/webm' });
  const terminal = { ...snapshot, terminal: true, status: 'success' as const, completedAt: '2026-08-11T12:00:10Z' };
  render(<LiveEvidencePanel tab="screen" snapshot={terminal} platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  expect(await screen.findByLabelText('Run replay video')).toHaveAttribute('src', '/stored.webm?generation=0');
});

it('shows one-sided selected Action evidence without an empty counterpart', async () => {
  vi.spyOn(controlPlaneClient, 'stepArtifacts').mockResolvedValue({
    available: true, stepId: 'step-1', message: null,
    artifacts: [{ kind: 'screenshot', phase: 'before', timestamp: null, mimeType: 'image/png', contentBase64: 'YmVmb3Jl' }],
  });
  const terminal = { ...snapshot, terminal: true, status: 'success' as const, completedAt: '2026-08-11T12:00:10Z' };
  render(<LiveEvidencePanel tab="screen" snapshot={terminal} selectedStepId="step-1" platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  expect(await screen.findByRole('img', { name: /web before screenshot for Chrome, selected Action/ })).toBeInTheDocument();
  expect(screen.queryByRole('img', { name: /web after screenshot for Chrome, selected Action/ })).not.toBeInTheDocument();
});

it('keeps replay generation failures scoped to Screen', async () => {
  vi.spyOn(controlPlaneClient, 'replayVideo').mockResolvedValue({ available: false, videoUrl: null });
  vi.spyOn(controlPlaneClient, 'replayFrames').mockResolvedValue({
    available: true, message: null, frames: [{ index: 1, timestamp: 1, mimeType: 'image/png', contentBase64: 'aW1hZ2U=' }],
  });
  vi.stubGlobal('MediaRecorder', undefined);
  const terminal = { ...snapshot, terminal: true, status: 'success' as const, completedAt: '2026-08-11T12:00:10Z' };
  render(<LiveEvidencePanel tab="screen" snapshot={terminal} platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Run replay failed');
  expect(screen.getByRole('alert')).toHaveTextContent('cannot generate replay video');
  vi.unstubAllGlobals();
});

it('regenerates once after a stored replay playback error', async () => {
  vi.spyOn(controlPlaneClient, 'replayVideo').mockResolvedValue({ available: true, videoUrl: '/broken.webm' });
  const frames = vi.spyOn(controlPlaneClient, 'replayFrames').mockResolvedValue({ available: false, frames: [], message: 'No frames' });
  const terminal = { ...snapshot, terminal: true, status: 'success' as const, completedAt: '2026-08-11T12:00:10Z' };
  render(<LiveEvidencePanel tab="screen" snapshot={terminal} platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  fireEvent.error(await screen.findByLabelText('Run replay video'));
  await waitFor(() => expect(frames).toHaveBeenCalledOnce());
  expect(await screen.findByText('Run replay unavailable')).toBeInTheDocument();
});

it('aborts in-flight replay requests when the evidence component unmounts', async () => {
  let capturedSignal: AbortSignal | undefined;
  vi.spyOn(controlPlaneClient, 'replayVideo').mockImplementation((_requestId, signal) => {
    capturedSignal = signal;
    return new Promise(() => undefined);
  });
  const terminal = { ...snapshot, terminal: true, status: 'success' as const, completedAt: '2026-08-11T12:00:10Z' };
  const { unmount } = render(<LiveEvidencePanel tab="screen" snapshot={terminal} platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  unmount();
  expect(capturedSignal?.aborted).toBe(true);
});

it('discloses long log messages and resumes paused log following', async () => {
  const longMessage = 'A safe structured agent message '.repeat(8);
  const withLongLog = { ...snapshot, events: [{ ...snapshot.events[0], stepId: 'step-1', durationMs: 42, payload: { tool_origin: 'agent_tool' }, toolCallId: 'call-1', toolArguments: { query: 'cats' }, toolOutputPreview: { status: 'ok' }, message: longMessage }] };
  const { container, rerender } = render(<LiveEvidencePanel tab="logs" snapshot={withLongLog} platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);
  const disclosure = await screen.findByRole('button', { name: 'Expand message' });
  expect(document.getElementById('log-message-1')).toHaveClass('event-message--clamped');
  await userEvent.click(disclosure);
  expect(disclosure).toHaveAccessibleName('Collapse message');
  expect(disclosure).toHaveAccessibleName('Collapse message');
  expect(document.getElementById('log-message-1')).not.toHaveClass('event-message--clamped');
  await userEvent.click(disclosure);
  expect(disclosure).toHaveAccessibleName('Expand message');
  expect(document.getElementById('log-message-1')).toHaveClass('event-message--clamped');

  await userEvent.click(screen.getByText('Details'));
  const eventJson = await screen.findByText(/"stepId": "step-1"/);
  expect(eventJson.closest('pre')).toHaveTextContent('"durationMs": 42');
  expect(eventJson.closest('pre')).toHaveTextContent('"sequence": 1');
  expect(eventJson.closest('pre')).toHaveTextContent('"payload"');
  expect(eventJson.closest('pre')).toHaveTextContent('"toolCallId": "call-1"');
  expect(eventJson.closest('pre')).toHaveTextContent('"toolArguments"');
  expect(eventJson.closest('pre')).toHaveTextContent('"toolOutputPreview"');

  const scrolling = container.querySelector('.logs-table-wrap') as HTMLDivElement;
  const logsTable = container.querySelector('.logs-table') as HTMLTableElement;
  const resizer = screen.getByRole('button', { name: 'Resize message column' });
  expect(logsTable.style.getPropertyValue('--log-message-width')).toBe('280px');
  expect(logsTable).toHaveClass('logs-table');
  fireEvent.pointerDown(resizer, { clientX: 0 });
  fireEvent.pointerMove(window, { clientX: 120 });
  fireEvent.pointerUp(window);
  expect(logsTable.style.getPropertyValue('--log-message-width')).toBe('400px');
  const details = container.querySelector('.log-event-json') as HTMLPreElement;
  expect(details).toHaveClass('log-event-json');
  const scrollTo = vi.fn();
  Object.defineProperties(scrolling, { scrollHeight: { configurable: true, value: 1000 }, clientHeight: { configurable: true, value: 200 }, scrollTop: { configurable: true, value: 0, writable: true }, scrollTo: { configurable: true, value: scrollTo } });
  fireEvent.scroll(scrolling);
  rerender(<LiveEvidencePanel tab="logs" snapshot={{ ...withLongLog, events: [...withLongLog.events, { sequence: 2, phase: 'execution', message: 'New row' }] }} platform="web" targetLabel="Chrome" onTabChange={vi.fn()} />);

  expect(screen.queryByRole('button', { name: /Jump to latest/ })).not.toBeInTheDocument();
  expect(scrolling.scrollTop).toBe(0);
  expect(scrollTo).not.toHaveBeenCalled();
});
