import { createRef } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { RunSnapshot } from '../../../api/types';
import { RunTimeline } from './RunTimeline';

it('keeps truthful terminal timeline, separates terminal actions, and selects actions', async () => {
  const onSelectStep = vi.fn();
  const message = 'Navigation complete with enough detail to require the disclosure control. '.repeat(4);
  const goal = 'Verify the page and keep enough source text available for expansion. '.repeat(3).trim();
  const snapshot: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'web', targetId: 'chrome', mode: 'explore', status: 'success',
    source: { goal }, startedAt: '', completedAt: '', cancelRequested: false,
    events: [{ sequence: 1, time: '2026-08-14T10:15:30Z', label: 'navigateTo', status: 'completed', message }], activeStep: null,
    result: { status: 'success' }, summary: 'Goal verified.', screenshotRevision: 1, uiSnapshotRevision: 1,
    evidenceAvailable: true, reportAvailable: true, terminal: true,
  };
  render(<RunTimeline snapshot={{ ...snapshot, events: [{ ...snapshot.events[0], stepId: 'step-1' }, { sequence: 2, stepId: 'step-1', label: 'Screenshot captured', payload: { kind: 'screenshot' } }] }} connection="ended" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={onSelectStep} onCancel={vi.fn()} onNewRun={vi.fn()} />);
  expect(screen.getByText('navigateTo')).toBeInTheDocument();
  expect(screen.getByText('Run source · Explore').closest('.run-source-summary')).not.toHaveTextContent('success');
  expect(screen.queryByText(/Updates:/)).not.toBeInTheDocument();
  expect(screen.queryByRole('heading', { name: 'Run success' })).not.toBeInTheDocument();
  const sourceText = screen.getByText(goal);
  expect(sourceText.closest('.run-source-summary')).not.toHaveClass('run-source-summary--expanded');
  expect(screen.getByRole('button', { name: 'Expand run source' }).closest('.run-source-line')).toContainElement(sourceText);
  await userEvent.click(screen.getByRole('button', { name: 'Expand run source' }));
  expect(sourceText.closest('.run-source-summary')).toHaveClass('run-source-summary--expanded');
  await userEvent.click(screen.getByRole('button', { name: 'Collapse run source' }));
  expect(sourceText.closest('.run-source-summary')).not.toHaveClass('run-source-summary--expanded');
  expect(screen.getByRole('button', { name: 'Select action navigateTo' })).not.toHaveTextContent(/\d{1,2}:\d{2}/);
  expect(screen.getByRole('button', { name: 'Save yaml' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'New run' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Cancel/ })).not.toBeInTheDocument();
  const disclosure = await screen.findByRole('button', { name: 'Expand message' });
  await userEvent.click(disclosure);
  expect(onSelectStep).not.toHaveBeenCalled();
  await userEvent.click(document.getElementById('timeline-message-1') as HTMLElement);
  expect(onSelectStep).toHaveBeenCalledWith('step-1');
  expect(onSelectStep).toHaveBeenCalledOnce();
});

it('distinguishes terminal explore actions without screenshots from selectable action cards', async () => {
  const onSelectStep = vi.fn();
  const snapshot: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'web', targetId: 'chrome', mode: 'explore', status: 'failed',
    source: { goal: 'Verify' }, startedAt: '', completedAt: '', cancelRequested: false,
    events: [
      { sequence: 1, stepId: 'step-with-screen', label: 'clickOn', status: 'failed', message: 'Click failed.' },
      { sequence: 2, stepId: 'step-with-screen', label: 'Screenshot captured', payload: { artifact_refs: [{ kind: 'screenshot' }] } },
      { sequence: 3, stepId: 'step-without-screen', label: 'assertVisible', status: 'skipped', message: 'Action was not executed.' },
    ], activeStep: null,
    result: { status: 'failed' }, summary: 'Run failed.', screenshotRevision: 1, uiSnapshotRevision: 1,
    evidenceAvailable: true, reportAvailable: true, terminal: true,
  };

  render(<RunTimeline snapshot={snapshot} connection="ended" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={onSelectStep} onCancel={vi.fn()} onNewRun={vi.fn()} />);

  expect(screen.getByText('clickOn').closest('li')).toHaveClass('timeline-row--selectable');
  expect(screen.getByRole('button', { name: 'Select action clickOn' })).toBeInTheDocument();
  expect(screen.getByText('assertVisible').closest('li')).not.toHaveClass('timeline-row--selectable');
  expect(screen.queryByRole('button', { name: 'Select action assertVisible' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByText('assertVisible'));
  expect(onSelectStep).not.toHaveBeenCalled();
});

it('hides Save yaml for terminal strict replay runs and reports save status', async () => {
  const onSaveYaml = vi.fn();
  const explore: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'web', targetId: 'chrome', mode: 'explore', status: 'success',
    source: { goal: 'Verify' }, startedAt: '', completedAt: '', cancelRequested: false,
    events: [], activeStep: null, result: { status: 'success' }, summary: 'Goal verified.', screenshotRevision: 1, uiSnapshotRevision: 1,
    evidenceAvailable: true, reportAvailable: true, terminal: true,
  };
  const { rerender } = render(<RunTimeline snapshot={explore} connection="ended" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onSaveYaml={onSaveYaml} onNewRun={vi.fn()} saveYamlState={{ state: 'idle', data: null, error: null }} />);

  await userEvent.click(screen.getByRole('button', { name: 'Save yaml' }));
  expect(screen.getByRole('dialog', { name: 'Save YAML case' })).toBeInTheDocument();
  const nameInput = screen.getByLabelText('Case name');
  expect(nameInput).toHaveValue('run-1');
  await waitFor(() => expect(nameInput).toHaveFocus());
  expect(screen.getByText('.fsq.yaml')).toBeInTheDocument();
  expect(screen.getByText('cases/web/run-1.fsq.yaml')).toBeInTheDocument();
  await userEvent.tab({ shift: true });
  expect(screen.getByRole('button', { name: 'Save' })).toHaveFocus();
  await userEvent.tab();
  expect(nameInput).toHaveFocus();
  await userEvent.keyboard('{Escape}');
  expect(screen.queryByRole('dialog', { name: 'Save YAML case' })).not.toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save yaml' })).toHaveFocus());
  await userEvent.click(screen.getByRole('button', { name: 'Save yaml' }));
  await userEvent.clear(screen.getByLabelText('Case name'));
  await userEvent.type(screen.getByLabelText('Case name'), 'checkout-flow');
  expect(screen.getByText('cases/web/checkout-flow.fsq.yaml')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Save' }));
  expect(onSaveYaml).toHaveBeenCalledWith('checkout-flow');
  rerender(<RunTimeline snapshot={explore} connection="ended" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onSaveYaml={onSaveYaml} onNewRun={vi.fn()} saveYamlState={{ state: 'ready', data: { savedPath: 'run-1.fsq.yaml', message: 'Saved YAML to cases/web/run-1.fsq.yaml.' }, error: null }} />);
  expect(screen.getByRole('dialog', { name: 'YAML case saved' })).toHaveTextContent('Saved YAML to cases/web/run-1.fsq.yaml.');
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole('button', { name: 'OK' })).toHaveFocus());
  await userEvent.click(screen.getByRole('button', { name: 'OK' }));
  expect(screen.queryByRole('dialog', { name: 'YAML case saved' })).not.toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save yaml' })).toHaveFocus());

  rerender(<RunTimeline snapshot={{ ...explore, mode: 'strict', source: { casePath: 'case.fsq.yaml' } }} connection="ended" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onSaveYaml={onSaveYaml} onNewRun={vi.fn()} saveYamlState={{ state: 'idle', data: null, error: null }} />);
  expect(screen.queryByRole('button', { name: 'Save yaml' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'New run' })).toBeInTheDocument();
});

it('uses the stable suggested Case name without truncation', async () => {
  const longRunId = 'open-edge1-3-launch-the-browser-1-4-open-the-about-microsoft-f7f3c6be-2026-08-19_13-29-48-670629-29748f81';
  const explore: RunSnapshot = {
    requestId: 'request', runId: longRunId, suggestedCaseName: 'case-' + 'a'.repeat(64), recordingDraft: true, workspaceName: 'test', platform: 'windows', targetId: 'edge', mode: 'explore', status: 'success',
    source: { goal: 'Verify' }, startedAt: '', completedAt: '', cancelRequested: false,
    events: [], activeStep: null, result: { status: 'success' }, summary: 'Goal verified.', screenshotRevision: 1, uiSnapshotRevision: 1,
    evidenceAvailable: true, reportAvailable: true, terminal: true,
  };
  render(<RunTimeline snapshot={explore} connection="ended" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);

  await userEvent.click(screen.getByRole('button', { name: 'Save yaml' }));

  const expected = 'case-' + 'a'.repeat(64);
  expect(screen.getByLabelText('Case name')).toHaveValue(expected);
  expect(screen.getByText(`cases/windows/${expected}.fsq.yaml`)).toBeInTheDocument();
});

it('shows Save yaml failures in a result dialog without adding bottom text', async () => {
  const explore: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'web', targetId: 'chrome', mode: 'explore', status: 'success',
    source: { goal: 'Verify' }, startedAt: '', completedAt: '', cancelRequested: false,
    events: [], activeStep: null, result: { status: 'success' }, summary: 'Goal verified.', screenshotRevision: 1, uiSnapshotRevision: 1,
    evidenceAvailable: true, reportAvailable: true, terminal: true,
  };
  render(<RunTimeline snapshot={explore} connection="ended" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onSaveYaml={vi.fn()} onNewRun={vi.fn()} saveYamlState={{ state: 'error', data: null, error: { code: 'save_failed', message: 'Unable to save YAML.', action: 'Choose another name.' } }} />);

  expect(screen.getByRole('dialog', { name: 'Save YAML failed' })).toHaveTextContent('Unable to save YAML.');
  expect(screen.getByRole('dialog', { name: 'Save YAML failed' })).toHaveTextContent('Choose another name.');
  expect(screen.queryByRole('status')).not.toBeInTheDocument();
});

it('offers cancellation through finalizing and locks repeated cancellation', async () => {
  const onCancel = vi.fn();
  const active: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'web', targetId: 'chrome', mode: 'strict', status: 'finalizing',
    source: { casePath: 'flow.fsq.yaml' }, startedAt: '', completedAt: null, cancelRequested: false,
    events: [], activeStep: null, result: null, summary: 'Finalizing', screenshotRevision: 0, uiSnapshotRevision: 0,
    evidenceAvailable: false, reportAvailable: false, terminal: false,
  };
  const { rerender } = render(<RunTimeline snapshot={active} connection="live" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={onCancel} onNewRun={vi.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: 'Cancel run' }));
  expect(onCancel).toHaveBeenCalledOnce();
  rerender(<RunTimeline snapshot={{ ...active, cancelRequested: true }} connection="live" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={onCancel} onNewRun={vi.fn()} />);
  expect(screen.getByRole('button', { name: /Cancellation requested/ })).toBeDisabled();
});

it('shows strict replay YAML content in the run source summary', () => {
  const yaml = 'schemaVersion: fsq.ai-test/v1\nname: Sample\n---\n- waitMs:\n    duration_ms: 1\n';
  const active: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'android', targetId: 'device', mode: 'strict', status: 'running',
    source: { casePath: 'strict.yaml', caseContent: yaml }, startedAt: '', completedAt: null, cancelRequested: false,
    events: [], activeStep: null, result: null, summary: 'Running', screenshotRevision: 0, uiSnapshotRevision: 0,
    evidenceAvailable: false, reportAvailable: false, terminal: false,
  };
  render(<RunTimeline snapshot={active} connection="live" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);

  expect(screen.getByText('Run source · Strict Replay').closest('.run-source-summary')).toHaveTextContent('schemaVersion: fsq.ai-test/v1');
  expect(screen.getByText('Run source · Strict Replay').closest('.run-source-summary')).not.toHaveTextContent('strict.yaml');
});

it('shows strict authored actions with step results and selects evidence by step id', async () => {
  const onSelectStep = vi.fn();
  const snapshot: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'android', targetId: 'device', mode: 'strict', status: 'success',
    source: {
      casePath: 'recorded.codex.yaml',
      caseSteps: [
        { stepId: 'step-launch', index: 1, authoredActionName: 'launchApp', actionName: 'launch_app', kind: 'setup', status: 'passed', durationMs: 2309 },
        { stepId: 'step-tap', index: 2, authoredActionName: 'tapOn', actionName: 'tap_on', kind: 'action', status: 'failed', durationMs: 12700, message: 'Target was not found.' },
        { stepId: 'step-assert', index: 3, authoredActionName: 'assertVisible', actionName: 'assert_visible', kind: 'assertion', status: 'skipped', message: 'Action was not executed.' },
      ],
    },
    startedAt: '', completedAt: '', cancelRequested: false,
    events: [
      { sequence: 1, stepId: 'step-launch', label: 'launch_app', status: 'completed', message: 'step finish' },
      { sequence: 2, stepId: 'step-tap', label: 'tap_on', status: 'completed', message: 'step finish' },
      { sequence: 3, stepId: 'step-tap', label: 'Screenshot captured', payload: { kind: 'screenshot' } },
      { sequence: 4, stepId: 'step-assert', label: 'Screenshot captured', payload: { kind: 'screenshot' } },
    ],
    activeStep: null, result: { status: 'failed' }, summary: 'Strict replay failed.', screenshotRevision: 1, uiSnapshotRevision: 1,
    evidenceAvailable: true, reportAvailable: true, terminal: true,
  };

  render(<RunTimeline snapshot={snapshot} connection="ended" selectedStepId="step-assert" resultHeadingRef={createRef()} onSelectStep={onSelectStep} onCancel={vi.fn()} onNewRun={vi.fn()} />);

  expect(screen.getByRole('region', { name: 'Strict replay action results' })).toBeInTheDocument();
  expect(screen.getByText('launchApp').closest('li')).toHaveTextContent('passed');
  expect(screen.getByText('tapOn').closest('li')).toHaveTextContent('failed');
  expect(screen.getByText('tapOn').closest('li')).toHaveTextContent('12700ms');
  expect(screen.getByText('tapOn').closest('li')).toHaveTextContent('Target was not found.');
  expect(screen.getByText('assertVisible').closest('li')).toHaveClass('timeline-row--selected');
  expect(screen.getByText('assertVisible').closest('li')).toHaveTextContent('skipped');
  expect(screen.getByText('assertVisible').closest('li')).toHaveTextContent('Action was not executed.');
  expect(screen.getByText('assertVisible').closest('li')).toHaveTextContent('assert_visible');

  await userEvent.click(screen.getByRole('button', { name: 'Select action tapOn' }));
  expect(onSelectStep).toHaveBeenCalledWith('step-tap');
});

it('distinguishes terminal strict actions with screenshots from ordinary rows without evidence', async () => {
  const onSelectStep = vi.fn();
  const snapshot: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'android', targetId: 'device', mode: 'strict', status: 'failed',
    source: {
      casePath: 'recorded.codex.yaml',
      caseSteps: [
        { stepId: 'step-launch', index: 1, authoredActionName: 'launchApp', actionName: 'launch_app', kind: 'setup', status: 'failed', message: 'FileNotFoundError' },
        { stepId: 'step-assert', index: 2, authoredActionName: 'assertVisible', actionName: 'assert_visible', kind: 'assertion', status: 'skipped', message: 'Action was not executed.' },
      ],
    },
    startedAt: '', completedAt: '', cancelRequested: false,
    events: [{ sequence: 1, stepId: 'step-launch', label: 'Screenshot captured', payload: { kind: 'screenshot' } }],
    activeStep: null, result: { status: 'failed' }, summary: 'Strict replay failed.', screenshotRevision: 1, uiSnapshotRevision: 1,
    evidenceAvailable: true, reportAvailable: true, terminal: true,
  };

  render(<RunTimeline snapshot={snapshot} connection="ended" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={onSelectStep} onCancel={vi.fn()} onNewRun={vi.fn()} />);

  expect(screen.getByText('launchApp').closest('li')).toHaveClass('timeline-row--selectable');
  expect(screen.getByRole('button', { name: 'Select action launchApp' })).toBeInTheDocument();
  expect(screen.getByText('assertVisible').closest('li')).not.toHaveClass('timeline-row--selectable');
  expect(screen.queryByRole('button', { name: 'Select action assertVisible' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Select action launchApp' }));
  expect(onSelectStep).toHaveBeenCalledWith('step-launch');
});

it('discloses overflowing strict authored action messages', async () => {
  const longMessage = 'Strict action completed with a long safe backend message. '.repeat(8);
  const snapshot: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'android', targetId: 'device', mode: 'strict', status: 'success',
    source: { casePath: 'recorded.codex.yaml', caseSteps: [{ stepId: 'step-tap', index: 1, authoredActionName: 'tapOn', actionName: 'tap_on', kind: 'action', status: 'passed', message: longMessage }] },
    startedAt: '', completedAt: '', cancelRequested: false,
    events: [{ sequence: 1, stepId: 'step-tap', label: 'tap_on', status: 'passed', message: longMessage }],
    activeStep: null, result: { status: 'passed' }, summary: 'Strict replay passed.', screenshotRevision: 1, uiSnapshotRevision: 1,
    evidenceAvailable: true, reportAvailable: true, terminal: true,
  };

  render(<RunTimeline snapshot={snapshot} connection="ended" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);

  const buttons = await screen.findAllByRole('button', { name: 'Expand message' });
  const actionDisclosure = buttons.find((button) => button.getAttribute('aria-controls') === 'strict-action-message-step-tap');
  expect(actionDisclosure).toBeDefined();
  expect(actionDisclosure).toHaveAttribute('aria-expanded', 'false');
  await userEvent.click(actionDisclosure as HTMLButtonElement);
  expect(actionDisclosure).toHaveAttribute('aria-expanded', 'true');
  expect(actionDisclosure).toHaveAccessibleName('Collapse message');
});

it('keeps selectable strict message disclosure outside the action selection button', async () => {
  const onSelectStep = vi.fn();
  const longMessage = 'Strict action completed with a long safe backend message. '.repeat(8);
  const snapshot: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'android', targetId: 'device', mode: 'strict', status: 'failed',
    source: { casePath: 'recorded.codex.yaml', caseSteps: [{ stepId: 'step-tap', index: 1, authoredActionName: 'tapOn', actionName: 'tap_on', kind: 'action', status: 'failed', message: longMessage }] },
    startedAt: '', completedAt: '', cancelRequested: false,
    events: [{ sequence: 1, stepId: 'step-tap', label: 'Screenshot captured', payload: { kind: 'screenshot' } }],
    activeStep: null, result: { status: 'failed' }, summary: 'Strict replay failed.', screenshotRevision: 1, uiSnapshotRevision: 1,
    evidenceAvailable: true, reportAvailable: true, terminal: true,
  };

  const { container } = render(<RunTimeline snapshot={snapshot} connection="ended" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={onSelectStep} onCancel={vi.fn()} onNewRun={vi.fn()} />);

  const selectButton = screen.getByRole('button', { name: 'Select action tapOn' });
  const disclosure = await screen.findByRole('button', { name: 'Expand message' });
  expect(selectButton).not.toContainElement(disclosure);
  expect(container.querySelector('.timeline-action-select .message-disclosure')).toBeNull();
  await userEvent.click(disclosure);
  expect(onSelectStep).not.toHaveBeenCalled();
  await userEvent.click(selectButton);
  expect(onSelectStep).toHaveBeenCalledWith('step-tap');
});

it('keeps strict actions pending until matching step events arrive', () => {
  const snapshot: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'android', targetId: 'device', mode: 'strict', status: 'running',
    source: {
      casePath: 'recorded.codex.yaml',
      caseSteps: [
        { stepId: 'step-launch', index: 1, authoredActionName: 'launchApp', actionName: 'launch_app', kind: 'setup' },
        { stepId: 'step-tap', index: 2, authoredActionName: 'tapOn', actionName: 'tap_on', kind: 'action' },
      ],
    },
    startedAt: '', completedAt: null, cancelRequested: false,
    events: [], activeStep: { stepId: 'step-tap', label: 'tapOn' }, result: null, summary: 'Running', screenshotRevision: 0, uiSnapshotRevision: 0,
    evidenceAvailable: false, reportAvailable: false, terminal: false,
  };

  render(<RunTimeline snapshot={snapshot} connection="live" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);

  expect(screen.getByText('launchApp').closest('li')).toHaveTextContent('pending');
  expect(screen.getByText('tapOn').closest('li')).toHaveTextContent('running');
  expect(screen.queryByRole('button', { name: 'Select action tapOn' })).not.toBeInTheDocument();
});

it('shows only strict authored action rows in the operation body', () => {
  const strict: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'android', targetId: 'device', mode: 'strict', status: 'running',
    source: { casePath: 'recorded.codex.yaml', caseSteps: [
      { stepId: 'recorded-step-001', index: 1, authoredActionName: 'launchApp', actionName: 'launch_app', kind: 'setup', status: 'passed', message: 'phase finish' },
      { stepId: 'recorded-step-002', index: 2, authoredActionName: 'tapOn', actionName: 'tap_on', kind: 'action', status: 'failed', message: 'Target was not found.' },
      { stepId: 'recorded-step-003', index: 3, authoredActionName: 'assertVisible', actionName: 'assert_visible', kind: 'assertion', status: 'skipped' },
      { stepId: 'recorded-step-004', index: 4, authoredActionName: 'killApp', actionName: 'kill_app', kind: 'teardown' },
    ] }, startedAt: '', completedAt: null, cancelRequested: false,
    events: [
      { sequence: 1, label: 'recorded-step-001', stepId: 'recorded-step-001', status: 'running', message: 'step start' },
      { sequence: 2, label: 'recorded-step-001', stepId: 'recorded-step-001', status: 'passed', message: 'phase finish' },
      { sequence: 3, label: 'recorded-step-002', stepId: 'recorded-step-002', status: 'completed', message: 'harness call finish' },
      { sequence: 4, label: 'Run update', message: 'strict log without step id' },
    ], activeStep: { stepId: 'recorded-step-004', label: 'recorded-step-004' }, result: null, summary: 'Running', screenshotRevision: 0, uiSnapshotRevision: 0,
    evidenceAvailable: false, reportAvailable: false, terminal: false,
  };
  render(<RunTimeline snapshot={strict} connection="live" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);

  expect(screen.getByLabelText('Strict replay action results')).toBeInTheDocument();
  expect(screen.getByText('launchApp').closest('li')).toHaveTextContent('passed');
  expect(screen.getByText('tapOn').closest('li')).toHaveTextContent('failed');
  expect(screen.getByText('assertVisible').closest('li')).toHaveTextContent('skipped');
  expect(screen.getByText('killApp').closest('li')).toHaveClass('timeline-row--active');
  expect(screen.queryByLabelText('Run timeline history')).not.toBeInTheDocument();
  expect(screen.queryByText('strict log without step id')).not.toBeInTheDocument();
  expect(screen.getAllByText('phase finish')).toHaveLength(1);
  expect(screen.queryByText('harness call finish')).not.toBeInTheDocument();
});

it('renders a flat sequence-ordered event list and discloses long messages', async () => {
  const longMessage = 'A detailed safe planning message '.repeat(8);
  const active: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'web', targetId: 'chrome', mode: 'explore', status: 'running',
    source: { goal: 'Verify' }, startedAt: '', completedAt: null, cancelRequested: false,
    events: [
      { sequence: 4, phase: 'startup', label: 'Latest startup', status: 'running', message: longMessage },
      { sequence: 2, phase: 'startup', label: 'Second startup', status: 'completed', message: 'Ready' },
      { sequence: 1, phase: 'startup', label: 'First startup', status: 'completed', message: 'Ready' },
      { sequence: 3, time: '2026-08-14T10:15:30Z', phase: 'planning', label: 'Plan', message: 'Planned' },
    ], activeStep: null, result: null, summary: 'Running', screenshotRevision: 0, uiSnapshotRevision: 0,
    evidenceAvailable: false, reportAvailable: false, terminal: false,
  };
  const { container } = render(<RunTimeline snapshot={active} connection="live" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);

  expect(container.querySelector('.timeline-group')).not.toBeInTheDocument();
  expect(container.querySelector('.timeline-group-toggle')).not.toBeInTheDocument();
  expect(screen.getAllByRole('listitem').map((item) => item.querySelector('strong')?.textContent)).toEqual([
    'First startup',
    'Second startup',
    'Plan',
    'Latest startup',
  ]);
  expect(screen.getByText('Latest startup').closest('li')?.querySelector('.status-badge')).toHaveTextContent('running');
  expect(screen.getByText('Plan').closest('li')?.querySelector('.status-badge')).toBeNull();
  expect(screen.getByText('Plan').closest('li')).not.toHaveTextContent(/\d{1,2}:\d{2}/);
  expect(screen.getByText('Plan').closest('li')).not.toHaveClass('timeline-row--running');
  const disclosure = await screen.findByRole('button', { name: 'Expand message' });
  expect(disclosure).toHaveAccessibleName('Expand message');
  expect(disclosure).toHaveAttribute('aria-expanded', 'false');
  await userEvent.click(disclosure);
  expect(disclosure).toHaveAccessibleName('Collapse message');
  expect(disclosure).toHaveAttribute('aria-expanded', 'true');
  await userEvent.click(disclosure);
  expect(disclosure).toHaveAccessibleName('Expand message');
  expect(disclosure).toHaveAttribute('aria-expanded', 'false');
});

it('highlights only the active running action and clears active highlighting after terminal selection', async () => {
  const snapshot: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'web', targetId: 'chrome', mode: 'explore', status: 'running',
    source: { goal: 'Verify' }, startedAt: '', completedAt: null, cancelRequested: false,
    events: [
      { sequence: 1, label: 'First', stepId: 'step-1', status: 'completed' },
      { sequence: 2, label: 'Second', stepId: 'step-2', status: 'running' },
    ], activeStep: { stepId: 'step-2', label: 'Second' }, result: null, summary: 'Running', screenshotRevision: 0, uiSnapshotRevision: 0,
    evidenceAvailable: false, reportAvailable: false, terminal: false,
  };
  const { rerender } = render(<RunTimeline snapshot={snapshot} connection="live" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);

  expect(screen.getByText('Second').closest('li')).toHaveClass('timeline-row--active');
  expect(screen.getByText('First').closest('li')).not.toHaveClass('timeline-row--active');

  rerender(<RunTimeline snapshot={{ ...snapshot, terminal: true, status: 'success', completedAt: 'now', events: [...snapshot.events, { sequence: 3, label: 'Screenshot captured', stepId: 'step-2', payload: { kind: 'screenshot' } }] }} connection="ended" selectedStepId="step-2" resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);
  expect(screen.getByText('Second').closest('li')).not.toHaveClass('timeline-row--active');
  expect(screen.getByText('Second').closest('li')).toHaveClass('timeline-row--selected');
});

it('keeps explicit active-step priority over newer non-step progress', () => {
  const snapshot: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'web', targetId: 'chrome', mode: 'explore', status: 'running',
    source: { goal: 'Verify' }, startedAt: '', completedAt: null, cancelRequested: false,
    events: [
      { sequence: 28, label: 'assert_with_ai', stepId: 'step-assert', status: 'completed', message: 'Tool returned output.' },
      { sequence: 29, label: 'Agent message', message: '{"status":"success"}' },
      { sequence: 30, label: 'Verification started', status: 'running', message: 'Running evidence-based verifier agent.' },
      { sequence: 31, label: 'Agent updated', message: 'fsq-agent verifier' },
    ], activeStep: { stepId: 'step-assert', label: 'assert_with_ai' }, result: null, summary: 'Running', screenshotRevision: 0, uiSnapshotRevision: 0,
    evidenceAvailable: false, reportAvailable: false, terminal: false,
  };
  render(<RunTimeline snapshot={snapshot} connection="live" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);

  expect(screen.getByText('assert_with_ai').closest('li')).toHaveClass('timeline-row--active');
  expect(screen.getByText('Verification started').closest('li')).not.toHaveClass('timeline-row--active');
  expect(screen.getByText('Agent updated').closest('li')).not.toHaveClass('timeline-row--active');
});

it('falls back to the latest running row or latest row when active step cannot match', () => {
  const base: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'web', targetId: 'chrome', mode: 'explore', status: 'running',
    source: { goal: 'Verify' }, startedAt: '', completedAt: null, cancelRequested: false,
    events: [
      { sequence: 1, label: 'First', status: 'completed' },
      { sequence: 2, label: 'Second', status: 'running' },
      { sequence: 3, label: 'Third' },
    ], activeStep: { stepId: 'missing-step', label: 'Missing' }, result: null, summary: 'Running', screenshotRevision: 0, uiSnapshotRevision: 0,
    evidenceAvailable: false, reportAvailable: false, terminal: false,
  };
  const { rerender } = render(<RunTimeline snapshot={base} connection="live" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);

  expect(screen.getByText('Second').closest('li')).toHaveClass('timeline-row--active');
  expect(screen.getByText('Third').closest('li')).not.toHaveClass('timeline-row--active');

  rerender(<RunTimeline snapshot={{ ...base, events: base.events.map((event) => ({ ...event, status: event.status === 'running' ? undefined : event.status })) }} connection="live" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);
  expect(screen.getByText('Third').closest('li')).toHaveClass('timeline-row--active');
});

it('pauses timeline following and jumps to appended events', async () => {
  const active: RunSnapshot = {
    requestId: 'request', runId: 'run-1', suggestedCaseName: 'run-1', workspaceName: 'test', platform: 'web', targetId: 'chrome', mode: 'explore', status: 'running',
    source: { goal: 'Verify' }, startedAt: '', completedAt: null, cancelRequested: false,
    events: [{ sequence: 1, phase: 'run', label: 'Started', status: 'running' }], activeStep: null,
    result: null, summary: 'Running', screenshotRevision: 0, uiSnapshotRevision: 0, evidenceAvailable: false, reportAvailable: false, terminal: false,
  };
  const { container, rerender } = render(<RunTimeline snapshot={active} connection="live" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);
  const scrolling = container.querySelector('.timeline-scroll') as HTMLDivElement;
  const scrollTo = vi.fn();
  Object.defineProperties(scrolling, { scrollHeight: { configurable: true, value: 1000 }, clientHeight: { configurable: true, value: 200 }, scrollTop: { configurable: true, value: 0, writable: true }, scrollTo: { configurable: true, value: scrollTo } });
  fireEvent.scroll(scrolling);

  rerender(<RunTimeline snapshot={{ ...active, events: [...active.events, { sequence: 2, phase: 'run', label: 'Next', status: 'running' }] }} connection="live" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);
  rerender(<RunTimeline snapshot={{ ...active, events: [...active.events, { sequence: 2, phase: 'run', label: 'Next', status: 'running' }, { sequence: 3, phase: 'run', label: 'Another', status: 'running' }] }} connection="live" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);
  const jump = await screen.findByRole('button', { name: 'Jump to latest · 2 new' });
  rerender(<RunTimeline snapshot={{ ...active, terminal: true, status: 'success' }} connection="ended" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);
  expect(screen.queryByRole('button', { name: /Jump to latest/ })).not.toBeInTheDocument();
  rerender(<RunTimeline snapshot={{ ...active, events: [...active.events, { sequence: 2, phase: 'run', label: 'Next', status: 'running' }, { sequence: 3, phase: 'run', label: 'Another', status: 'running' }] }} connection="live" selectedStepId={null} resultHeadingRef={createRef()} onSelectStep={vi.fn()} onCancel={vi.fn()} onNewRun={vi.fn()} />);
  const activeJump = await screen.findByRole('button', { name: /Jump to latest/ });
  expect(scrolling.scrollTop).toBe(0);
  await userEvent.click(activeJump);
  expect(scrolling.scrollTop).toBe(scrolling.scrollHeight - scrolling.clientHeight);
  expect(scrollTo).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByRole('button', { name: /Jump to latest/ })).not.toBeInTheDocument());
  expect(scrolling).toHaveFocus();
});

it('does not scroll terminal timeline appends',()=>{
  const snapshot:RunSnapshot={requestId:'request',runId:'run',workspaceName:'test',platform:'web',targetId:'chrome',mode:'explore',status:'success',source:{goal:'Verify'},startedAt:'',completedAt:'now',cancelRequested:false,events:[{sequence:1,label:'First'}],activeStep:null,result:null,summary:'Done',screenshotRevision:0,uiSnapshotRevision:0,evidenceAvailable:false,reportAvailable:false,terminal:true};
  const p={connection:'ended',selectedStepId:null,resultHeadingRef:createRef<HTMLHeadingElement>(),onSelectStep:vi.fn(),onCancel:vi.fn(),onNewRun:vi.fn()};
  const {rerender}=render(<RunTimeline {...p} snapshot={snapshot}/>);
  const scroll=vi.fn();screen.getByLabelText('Run timeline history').scrollTo=scroll;
  rerender(<RunTimeline {...p} snapshot={{...snapshot,events:[...snapshot.events,{sequence:2,label:'Final event'}]}}/>);
  expect(scroll).not.toHaveBeenCalled();
});
