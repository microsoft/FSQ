import { createRef } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReadinessResponse } from '../../../api/types';
import { OperationComposer } from './OperationComposer';

const ready: ReadinessResponse = {
  workspaceName: 'test', platformId: 'web',
  workspace: { status: 'ready', message: 'Workspace ready', action: '' },
  platform: { status: 'ready', message: 'Platform ready', action: '' },
  provider: { status: 'ready', message: 'Provider ready', action: '' },
  target: { status: 'ready', message: 'Target ready', action: '' },
  strict: { status: 'ready', message: 'Strict ready', action: '' },
};

it('shows the macOS disabled reason and pending-start state',()=>{
  const props={mode:'explore' as const,goal:'Keep',casePath:'',cases:[],casesState:'ready' as const,readiness:null,discoveryLoading:false,canStart:false,primaryInputRef:createRef<HTMLElement>(),onModeChange:vi.fn(),onGoalChange:vi.fn(),onCaseChange:vi.fn(),onStart:vi.fn()};
  const {rerender}=render(<OperationComposer {...props} macos blockedReason="Appium is offline."/>);
  expect(screen.getByRole('button',{name:'Start exploration'})).toBeDisabled();
  expect(screen.getByText('Appium is offline.')).toBeVisible();
  rerender(<OperationComposer {...props} macos starting blockedReason="Checking before starting."/>);
  expect(screen.getByRole('button',{name:'Checking environment…'})).toBeDisabled();
  expect(screen.getByLabelText('What should FSQ prove?')).toBeDisabled();
});

it.each(['explore','strict'] as const)('shows Android disabled reason and pending Start for %s', mode=>{
  const props={mode,goal:'Keep',casePath:'',cases:[],casesState:'ready' as const,readiness:null,discoveryLoading:false,canStart:false,primaryInputRef:createRef<HTMLElement>(),onModeChange:vi.fn(),onGoalChange:vi.fn(),onCaseChange:vi.fn(),onStart:vi.fn()};
  const {rerender}=render(<OperationComposer {...props} android blockedReason="Select a validated Case."/>);
  const button=screen.getByRole('button',{name:mode==='explore'?'Start exploration':'Start strict replay'});
  expect(button).toBeDisabled();
  expect(button).toHaveAccessibleDescription('Select a validated Case.');
  rerender(<OperationComposer {...props} android starting blockedReason="Checking before starting."/>);
  expect(screen.getByRole('button',{name:'Checking environment…'})).toBeDisabled();
  expect(screen.getByRole('button',{name:'Checking environment…'})).toHaveAccessibleDescription('Checking before starting.');
});

it('shows strict case facts without claiming human review', async () => {
  const onModeChange = vi.fn();
  render(<OperationComposer mode="strict" goal="" casePath="flow.fsq.yaml" cases={[{
    path: 'flow.fsq.yaml', id: 'flow', name: 'Create flow', platform: 'web', commandCount: 6,
    requiresAiAssertion: true, validationStatus: 'validated', selectable: true, diagnostics: [],
  }]} casesState="ready" readiness={ready} discoveryLoading={false} canStart primaryInputRef={createRef()} onModeChange={onModeChange} onGoalChange={vi.fn()} onCaseChange={vi.fn()} onStart={vi.fn()} />);
  expect(screen.queryByText('Path')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Validated case flow.fsq.yaml' })).toBeInTheDocument();
  expect(screen.getByText('web')).toBeInTheDocument();
  expect(screen.getByText('6')).toBeInTheDocument();
  expect(screen.getByText('validated')).toBeInTheDocument();
  expect(screen.getByText('Provider required')).toBeInTheDocument();
  expect(screen.queryByText(/reviewed/i)).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('radio', { name: 'Explore' }));
  expect(onModeChange).toHaveBeenCalledWith('explore');
});

it('shows selectable strict cases in an expandable file tree', async () => {
  const onCaseChange = vi.fn();
  render(<OperationComposer mode="strict" goal="" casePath="recorded.codex2.yaml" cases={[
    { path: 'recorded.codex.yaml', id: 'one', name: 'Recorded one', platform: 'web', commandCount: 4, requiresAiAssertion: false, validationStatus: 'validated', selectable: true, diagnostics: [] },
    { path: 'recorded.codex2.yaml', id: 'two', name: 'Recorded two', platform: 'web', commandCount: 4, requiresAiAssertion: false, validationStatus: 'validated', selectable: true, diagnostics: [] },
    { path: 'F1/nested.yaml', id: 'nested', name: 'Nested', platform: 'web', commandCount: 2, requiresAiAssertion: false, validationStatus: 'validated', selectable: true, diagnostics: [] },
    { path: 'invalid.yaml', id: 'invalid', name: 'Invalid', platform: 'web', commandCount: 0, requiresAiAssertion: false, validationStatus: 'invalid', selectable: false, diagnostics: ['broken'] },
  ]} casesState="ready" readiness={ready} discoveryLoading={false} canStart primaryInputRef={createRef()} onModeChange={vi.fn()} onGoalChange={vi.fn()} onCaseChange={onCaseChange} onStart={vi.fn()} />);

  expect(screen.getByRole('button', { name: 'Validated case recorded.codex2.yaml' })).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('tree', { name: 'Validated case' })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Validated case recorded.codex2.yaml' }));
  expect(screen.getByRole('tree', { name: 'Validated case' })).toBeInTheDocument();
  expect(screen.getByRole('treeitem', { name: 'recorded.codex.yaml' })).toBeInTheDocument();
  expect(screen.getByRole('treeitem', { name: 'recorded.codex2.yaml' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('treeitem', { name: 'Expand F1' })).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('treeitem', { name: 'nested.yaml' })).not.toBeInTheDocument();
  expect(screen.queryByRole('treeitem', { name: 'invalid.yaml' })).not.toBeInTheDocument();
  expect(screen.getByText('1 invalid or platform-mismatched case(s) are unavailable.')).toBeInTheDocument();
  expect(screen.queryByText(/4 commands/)).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole('treeitem', { name: 'recorded.codex.yaml' }));
  expect(onCaseChange).toHaveBeenCalledWith('recorded.codex.yaml');
  expect(screen.queryByRole('tree', { name: 'Validated case' })).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: 'Validated case recorded.codex2.yaml' }));
  await userEvent.click(screen.getByRole('treeitem', { name: 'Expand F1' }));
  expect(screen.getByRole('treeitem', { name: 'Collapse F1' })).toHaveAttribute('aria-expanded', 'true');
  await userEvent.click(screen.getByRole('treeitem', { name: 'nested.yaml' }));
  expect(onCaseChange).toHaveBeenCalledWith('F1/nested.yaml');
  expect(screen.queryByRole('tree', { name: 'Validated case' })).not.toBeInTheDocument();
});

it('shows an empty Strict Replay source while readiness remains available', async () => {
  render(<OperationComposer mode="strict" goal="" casePath="" cases={[]} casesState="ready" readiness={ready} discoveryLoading={false} canStart={false} primaryInputRef={createRef()} onModeChange={vi.fn()} onGoalChange={vi.fn()} onCaseChange={vi.fn()} onStart={vi.fn()} />);

  await userEvent.click(screen.getByRole('button', { name: 'Validated case Select a yaml' }));
  expect(screen.getByText('No validated cases available')).toBeInTheDocument();
  expect(screen.getByText('Workspace ready')).toBeInTheDocument();
  expect(screen.getByText('Strict ready')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Start strict replay' })).toBeDisabled();
});
