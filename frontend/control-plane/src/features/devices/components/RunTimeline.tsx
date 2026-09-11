import { createPortal } from 'react-dom';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react';
import type { RequestResource, RunSnapshot, SaveYamlResponse, StrictCaseStep, TimelineEvent } from '../../../api/types';

const cancellable = new Set(['preparing', 'running', 'finalizing']);
const LONG_MESSAGE_LENGTH = 140;
const FOLLOW_THRESHOLD = 32;

interface RunTimelineProps {
  actionContainer?: HTMLElement | null;
  snapshot: RunSnapshot | null;
  connection: string;
  selectedStepId: string | null;
  resultHeadingRef: RefObject<HTMLHeadingElement | null>;
  onSelectStep: (stepId: string | null) => void;
  onCancel: () => void;
  onSaveYaml?: (caseName: string) => void;
  onNewRun: () => void;
  saveYamlState?: RequestResource<SaveYamlResponse>;
}

const emptySaveYamlState: RequestResource<SaveYamlResponse> = { state: 'idle', data: null, error: null };
const FSQ_CASE_SUFFIX = '.fsq.yaml';

function formatTime(value?: string) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '' : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function ExpandableMessage({ message, messageId }: { message: string; messageId: string }) {
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const messageRef = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    const element = messageRef.current;
    if (!element) return;
    const measure = () => {
      if (expanded) return;
      setOverflowing(element.clientWidth > 0 ? element.scrollWidth > element.clientWidth : message.length > LONG_MESSAGE_LENGTH);
    };
    measure();
    const frame = requestAnimationFrame(measure);
    const observer = globalThis.ResizeObserver ? new ResizeObserver(measure) : null;
    observer?.observe(element);
    return () => { cancelAnimationFrame(frame); observer?.disconnect(); };
  }, [expanded, message]);
  if (!message) return null;
  return <span className="event-message-wrap">
    <small ref={messageRef} id={messageId} className={!expanded ? 'event-message event-message--clamped' : 'event-message'}>{message}</small>
    {overflowing && <button className="message-disclosure" type="button" title={expanded ? 'Collapse message' : 'Expand message'} aria-label={expanded ? 'Collapse message' : 'Expand message'} aria-expanded={expanded} aria-controls={messageId} onClick={(clickEvent) => { clickEvent.stopPropagation(); setExpanded((value) => !value); }}>{expanded ? <ChevronUp aria-hidden="true"/> : <ChevronDown aria-hidden="true"/>}</button>}
  </span>;
}

function EventMessage({ event }: { event: TimelineEvent }) {
  return <ExpandableMessage message={event.message ?? ''} messageId={`timeline-message-${event.sequence}`} />;
}

function latestStepEvent<T>(events: TimelineEvent[], select: (event: TimelineEvent) => T | null | undefined): T | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const value = select(events[index]);
    if (value !== null && value !== undefined) return value;
  }
  return null;
}

function strictStepStatus(step: StrictCaseStep, stepEvents: TimelineEvent[], snapshot: RunSnapshot) {
  if (step.status) return step.status;
  if (!snapshot.terminal && snapshot.activeStep?.stepId === step.stepId) return 'running';
  if (!snapshot.terminal && stepEvents.length) return 'running';
  return snapshot.terminal ? 'incomplete' : 'pending';
}

function payloadHasScreenshotArtifact(value: unknown, depth = 0): boolean {
  if (depth > 4 || value === null || value === undefined) return false;
  if (Array.isArray(value)) return value.some((item) => payloadHasScreenshotArtifact(item, depth + 1));
  if (typeof value !== 'object') return false;
  const record = value as Record<string, unknown>;
  if (record.kind === 'screenshot' || record.kind === 'ui_snapshot') return true;
  return Object.values(record).some((item) => payloadHasScreenshotArtifact(item, depth + 1));
}

function stepIdsWithScreenshotEvidence(events: TimelineEvent[]): Set<string> {
  const values = new Set<string>();
  for (const event of events) {
    if (event.stepId && payloadHasScreenshotArtifact(event.payload)) values.add(event.stepId);
  }
  return values;
}

function StrictActionSummary({ snapshot, events, selectedStepId, onSelectStep }: { snapshot: RunSnapshot; events: TimelineEvent[]; selectedStepId: string | null; onSelectStep: (stepId: string | null) => void }) {
  const steps = snapshot.source.caseSteps ?? [];
  if (snapshot.mode !== 'strict') return null;
  if (!steps.length) return <section className="strict-action-summary" aria-label="Strict replay action results"><p className="empty-state">No YAML steps are available.</p></section>;
  const eventsByStep = new Map<string, TimelineEvent[]>();
  for (const event of events) {
    if (!event.stepId) continue;
    const existing = eventsByStep.get(event.stepId) ?? [];
    existing.push(event);
    eventsByStep.set(event.stepId, existing);
  }
  const screenshotStepIds = stepIdsWithScreenshotEvidence(events);
  return <section className="strict-action-summary" aria-label="Strict replay action results">
    <ol className="strict-action-list">
      {steps.map((step) => {
        const stepEvents = eventsByStep.get(step.stepId) ?? [];
        const status = strictStepStatus(step, stepEvents, snapshot);
        const durationMs = typeof step.durationMs === 'number' ? step.durationMs : null;
        const message = step.message || step.skipReason || null;
        const hasScreenshotEvidence = screenshotStepIds.has(step.stepId);
        const selected = hasScreenshotEvidence && selectedStepId === step.stepId;
        const active = !snapshot.terminal && snapshot.activeStep?.stepId === step.stepId;
        const selectable = snapshot.terminal && hasScreenshotEvidence;
        const selectAction = () => onSelectStep(selected ? null : step.stepId);
        const summaryContent = <>
          <span className="timeline-index">{String(step.index).padStart(2, '0')}</span>
          <span className="timeline-event-title"><strong>{step.authoredActionName}</strong><small>{step.actionName} · {step.kind}</small></span>
          <span className="timeline-event-meta"><span className={`status-badge status-badge--${status}`}>{status}</span>{durationMs !== null && <small>{durationMs}ms</small>}</span>
        </>;
        return <li key={step.stepId} className={`strict-action-row timeline-row timeline-row--${status}${active ? ' timeline-row--active' : ''}${selectable ? ' timeline-row--selectable' : ''}${selected ? ' timeline-row--selected' : ''}`}>
          {selectable ? <button className="timeline-action-select" type="button" aria-label={`Select action ${step.authoredActionName}`} aria-pressed={selected} onClick={selectAction}>{summaryContent}</button> : summaryContent}
          {message && <span className="timeline-event-main"><ExpandableMessage message={message} messageId={`strict-action-message-${step.stepId}`} /></span>}
          {(step.blockedByStep || step.attemptIndex || step.invocationPath?.length) && <small className="timeline-event-main">{step.blockedByStep && `Blocked by ${step.blockedByStep}. `}{step.attemptIndex && `Attempt ${step.attemptIndex}${step.maxAttempts ? ` / ${step.maxAttempts}` : ''}. `}{step.invocationPath?.join(' / ')}</small>}
          {step.evidenceErrors && step.evidenceErrors.length > 0 && <details><summary>Evidence capture issues · aggregate {step.aggregateStatus ?? status}</summary><pre>{JSON.stringify(step.evidenceErrors, null, 2)}</pre></details>}
          {step.attempts && step.attempts.length > 1 && <details><summary>Recorded attempts ({step.attempts.length})</summary>{step.attempts.map((attempt, index) => { const id = String(attempt.stepExecutionId ?? ''); const eligible = snapshot.terminal && screenshotStepIds.has(id); return <button className="button" key={index} disabled={!eligible} aria-pressed={eligible && selectedStepId === id} onClick={() => { if (eligible) onSelectStep(id); }}>Attempt {String(attempt.attemptIndex)} · {String(attempt.status)}</button>; })}</details>}
        </li>;
      })}
    </ol>
  </section>;
}

function defaultCaseName(snapshot: RunSnapshot) {
  return snapshot.suggestedCaseName || '';
}

function invalidCaseName(caseName: string) {
  const trimmed = caseName.trim();
  if (!trimmed) return 'Enter a case name.';
  if (/\.fsq\.yaml$/i.test(trimmed)) return 'Do not include the .fsq.yaml suffix.';
  if (trimmed.startsWith('.') || trimmed.includes('..') || /[\\/:*?[\]\x00-\x1f]/.test(trimmed)) return 'Use a filename without paths, dots, or wildcard characters.';
  return null;
}

export function RunTimeline({ actionContainer, snapshot, connection, selectedStepId, onSelectStep, onCancel, onSaveYaml = () => undefined, onNewRun, saveYamlState = emptySaveYamlState }: RunTimelineProps) {
  const events = useMemo(() => [...(snapshot?.events ?? [])].sort((left, right) => left.sequence - right.sequence), [snapshot?.events]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const previousLastSequence = useRef(0);
  const [following, setFollowing] = useState(true);
  const [sourceExpanded, setSourceExpanded] = useState(false);
  const [sourceOverflowing, setSourceOverflowing] = useState(false);
  const sourceRef = useRef<HTMLElement>(null);
  const saveButtonRef = useRef<HTMLButtonElement>(null);
  const saveDialogRef = useRef<HTMLElement>(null);
  const saveNameInputRef = useRef<HTMLInputElement>(null);
  const saveResultDialogRef = useRef<HTMLElement>(null);
  const saveResultButtonRef = useRef<HTMLButtonElement>(null);
  const [unseen, setUnseen] = useState(0);
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saveResultOpen, setSaveResultOpen] = useState(false);
  const [caseName, setCaseName] = useState('');
  const lastSequence = snapshot?.events.at(-1)?.sequence ?? 0;
  const source = snapshot?.mode === 'explore' ? snapshot.source.goal : snapshot?.source.caseContent ?? snapshot?.source.casePath;
  const saveNameError = invalidCaseName(caseName);
  const savePathPreview = snapshot ? `cases/${snapshot.platform}/${caseName.trim() || defaultCaseName(snapshot)}${FSQ_CASE_SUFFIX}` : '';
  const openSaveDialog = () => {
    if (!snapshot) return;
    setCaseName(defaultCaseName(snapshot));
    setSaveDialogOpen(true);
  };
  const closeSaveDialog = () => {
    setSaveDialogOpen(false);
    window.setTimeout(() => saveButtonRef.current?.focus(), 0);
  };
  const confirmSaveYaml = () => {
    if (saveNameError) return;
    onSaveYaml(caseName.trim());
    closeSaveDialog();
  };
  const closeSaveResult = () => {
    setSaveResultOpen(false);
    window.setTimeout(() => saveButtonRef.current?.focus(), 0);
  };
  useEffect(() => {
    if (saveYamlState.state === 'ready' || saveYamlState.state === 'error') {
      setSaveResultOpen(true);
    } else if (saveYamlState.state === 'loading') {
      setSaveResultOpen(false);
    }
  }, [saveYamlState.state, saveYamlState.data, saveYamlState.error]);
  useEffect(() => {
    if (!saveDialogOpen) return;
    const frame = requestAnimationFrame(() => saveNameInputRef.current?.focus());
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeSaveDialog();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(saveDialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])') ?? [])
        .filter((element) => !element.hidden && element.getAttribute('aria-hidden') !== 'true');
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => { cancelAnimationFrame(frame); document.removeEventListener('keydown', onKeyDown); };
  }, [saveDialogOpen]);
  useEffect(() => {
    if (!saveResultOpen) return;
    const frame = requestAnimationFrame(() => saveResultButtonRef.current?.focus());
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeSaveResult();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(saveResultDialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])') ?? [])
        .filter((element) => !element.hidden && element.getAttribute('aria-hidden') !== 'true');
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => { cancelAnimationFrame(frame); document.removeEventListener('keydown', onKeyDown); };
  }, [saveResultOpen]);
  useEffect(() => {
    const previous = previousLastSequence.current;
    previousLastSequence.current = lastSequence;
    if (snapshot?.terminal || !lastSequence || lastSequence <= previous) return;
    if (following) {
      scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight, behavior: 'auto' });
      setUnseen(0);
    } else {
      setUnseen((value) => value + (snapshot?.events.filter((event) => event.sequence > previous).length ?? 0));
    }
  }, [following, lastSequence, snapshot?.events, snapshot?.terminal]);
  useLayoutEffect(() => {
    const element = sourceRef.current;
    if (!element) return;
    const measure = () => {
      if (sourceExpanded) return;
      setSourceOverflowing(element.clientWidth > 0 ? element.scrollWidth > element.clientWidth : Boolean(source && source.length > LONG_MESSAGE_LENGTH));
    };
    measure();
    const frame = requestAnimationFrame(measure);
    const observer = globalThis.ResizeObserver ? new ResizeObserver(measure) : null;
    observer?.observe(element);
    return () => { cancelAnimationFrame(frame); observer?.disconnect(); };
  }, [source, sourceExpanded]);
  if (!snapshot) return <div className="run-loading" role="status"><span className="spinner" aria-hidden="true" />Preparing run details…</div>;
  const screenshotStepIds = stepIdsWithScreenshotEvidence(events);
  const activeStepId = !snapshot.terminal ? snapshot.activeStep?.stepId : null;
  let latestActiveStepSequence: number | null = null;
  if (activeStepId) {
    for (const event of events) {
      if (event.stepId === activeStepId) latestActiveStepSequence = event.sequence;
    }
  }
  const activeStepMatched = Boolean(activeStepId && latestActiveStepSequence != null);
  let latestRunningEvent: TimelineEvent | null = null;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (events[index].status === 'running') { latestRunningEvent = events[index]; break; }
  }
  const activeFallbackSequence = !snapshot.terminal && !activeStepMatched ? (latestRunningEvent ?? events.at(-1))?.sequence : null;
  const onTimelineScroll = () => {
    const element = scrollRef.current;
    if (!element) return;
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight <= FOLLOW_THRESHOLD;
    setFollowing(atBottom);
    if (atBottom) setUnseen(0);
  };
  const jumpToLatest = () => {
    const element = scrollRef.current;
    if (!element) return;
    element.scrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight <= FOLLOW_THRESHOLD;
    setFollowing(atBottom);
    if (atBottom) setUnseen(0);
    element.focus();
  };
  const actions = <>
    {!snapshot.terminal && cancellable.has(snapshot.status) && <button className="button button--danger cancel-button" type="button" disabled={snapshot.cancelRequested} onClick={onCancel}>{snapshot.cancelRequested ? 'Cancellation requested…' : 'Cancel run'}</button>}
    {snapshot.terminal && <div className="terminal-actions" aria-label="Completed run actions">
      {snapshot.mode === 'explore' && <button ref={saveButtonRef} className="button" type="button" disabled={saveYamlState.state === 'loading'} onClick={openSaveDialog}>{saveYamlState.state === 'loading' ? 'Saving yaml…' : 'Save yaml'}</button>}
      <button className="button button--primary" type="button" onClick={onNewRun}>New run</button>
    </div>}
  </>;
  return <div className={`run-timeline${snapshot.mode === 'strict' ? ' run-timeline--strict' : ''}`}>
    <div className={`run-source-summary${sourceExpanded ? ' run-source-summary--expanded' : ''}`}><strong>Run source · {snapshot.mode === 'explore' ? 'Explore' : 'Strict Replay'}</strong><span className="run-source-line"><small ref={sourceRef}>{source ?? 'Source unavailable'}</small>{sourceOverflowing && <button className="message-disclosure run-source-disclosure" type="button" aria-label={sourceExpanded ? 'Collapse run source' : 'Expand run source'} aria-expanded={sourceExpanded} onClick={() => setSourceExpanded((value) => !value)}>{sourceExpanded ? <ChevronUp aria-hidden="true"/> : <ChevronDown aria-hidden="true"/>}</button>}</span></div>
    <StrictActionSummary snapshot={snapshot} events={events} selectedStepId={selectedStepId} onSelectStep={onSelectStep} />
    {snapshot.mode === 'explore' && <div className="timeline-history">
      <div className="timeline-scroll" ref={scrollRef} onScroll={onTimelineScroll} data-following={following} tabIndex={-1} aria-label="Run timeline history">
        {events.length ? <ol className="timeline-list">
          {events.map((event) => {
            const label = event.label || event.tool || event.phase || 'Run update';
            const selectable = snapshot.terminal && Boolean(event.stepId && screenshotStepIds.has(event.stepId));
            const selected = selectable && selectedStepId === event.stepId;
            const active = !snapshot.terminal && (activeStepMatched ? event.stepId === activeStepId : event.sequence === activeFallbackSequence);
            const selectAction = () => onSelectStep(selected ? null : event.stepId ?? null);
            const statusClass = event.status ? ` timeline-row--${event.status}` : '';
            const statusBadge = event.status ? <span className={`status-badge status-badge--${event.status}`}>{event.status}</span> : null;
            return <li key={event.sequence} className={`timeline-row${statusClass}${active ? ' timeline-row--active' : ''}${selectable ? ' timeline-row--selectable' : ''}${selected ? ' timeline-row--selected' : ''}`} onClick={selectable ? selectAction : undefined}>
              {selectable ? <button className="timeline-action-select" type="button" aria-label={`Select action ${label}`} aria-pressed={selected}>
                <span className="timeline-index">{String(event.sequence).padStart(2, '0')}</span>
                <span className="timeline-event-title"><strong>{label}</strong></span>
                {statusBadge && <span className="timeline-event-meta">{statusBadge}</span>}
              </button> : <><span className="timeline-index">{String(event.sequence).padStart(2, '0')}</span><span className="timeline-event-title"><strong>{label}</strong></span>{statusBadge && <span className="timeline-event-meta">{statusBadge}</span>}</>}
              <span className="timeline-event-main"><EventMessage event={event} /></span>
            </li>;
          })}
        </ol> : <p className="empty-state">No timeline events have been emitted yet.</p>}
      </div>
      {!snapshot.terminal && !following && <button className="jump-latest" type="button" onClick={jumpToLatest}>Jump to latest{unseen ? ` · ${unseen} new` : ''}</button>}
    </div>}
    {actionContainer ? createPortal(actions, actionContainer) : actions}
    {snapshot.terminal && snapshot.mode === 'explore' && saveDialogOpen && <div className="config-dialog-backdrop" role="presentation">
      <section ref={saveDialogRef} className="config-dialog save-yaml-dialog" role="dialog" aria-modal="true" aria-labelledby="save-yaml-title">
        <h2 id="save-yaml-title">Save YAML case</h2>{snapshot.recordingDraft && <p role="status">Draft recording: review this Case before use.</p>}
        <p className="config-dialog-intro">Confirm the case name before saving this generated recording.</p>
        <label className="save-yaml-name-field" htmlFor="save-yaml-case-name"><span>Case name</span><span className="save-yaml-name-input"><input ref={saveNameInputRef} id="save-yaml-case-name" aria-label="Case name" value={caseName} onChange={(event) => setCaseName(event.target.value)} aria-invalid={Boolean(saveNameError)} aria-describedby="save-yaml-path-preview save-yaml-name-error" /><strong>{FSQ_CASE_SUFFIX}</strong></span></label>
        {saveNameError && <p id="save-yaml-name-error" className="config-error" role="alert"><strong>{saveNameError}</strong></p>}
        <p id="save-yaml-path-preview" className="save-yaml-path-preview"><span>Save path</span><strong>{savePathPreview}</strong></p>
        <div className="config-dialog-actions"><button className="button" type="button" onClick={closeSaveDialog}>Cancel</button><button className="button button--primary" type="button" disabled={Boolean(saveNameError) || saveYamlState.state === 'loading'} onClick={confirmSaveYaml}>Save</button></div>
      </section>
    </div>}
    {snapshot.terminal && snapshot.mode === 'explore' && saveResultOpen && (saveYamlState.state === 'ready' || saveYamlState.state === 'error') && <div className="config-dialog-backdrop" role="presentation">
      <section ref={saveResultDialogRef} className="config-dialog save-yaml-dialog" role="dialog" aria-modal="true" aria-labelledby="save-yaml-result-title">
        <h2 id="save-yaml-result-title">{saveYamlState.state === 'ready' ? 'YAML case saved' : 'Save YAML failed'}</h2>
        {saveYamlState.state === 'ready' && saveYamlState.data && <p className="save-yaml-result-message">{saveYamlState.data.message}</p>}
        {saveYamlState.state === 'error' && saveYamlState.error && <div className="config-error" role="alert"><strong>{saveYamlState.error.message}</strong><span>{saveYamlState.error.action}</span></div>}
        <div className="config-dialog-actions"><button ref={saveResultButtonRef} className="button button--primary" type="button" onClick={closeSaveResult}>OK</button></div>
      </section>
    </div>}
  </div>;
}
