import { ChevronDown, ChevronUp } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import type { TimelineEvent } from '../../../api/types';

const LONG_MESSAGE_LENGTH = 140;
const FOLLOW_THRESHOLD = 32;
const DEFAULT_MESSAGE_WIDTH = 280;
const MIN_MESSAGE_WIDTH = 180;
const MAX_MESSAGE_WIDTH = 720;

interface LogStyle extends CSSProperties {
  '--log-message-width': string;
}

function LogMessage({ event }: { event: TimelineEvent }) {
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const messageRef = useRef<HTMLSpanElement>(null);
  const message = event.message ?? '—';
  const messageId = `log-message-${event.sequence}`;
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
  return <div className="event-message-wrap">
    <span ref={messageRef} id={messageId} className={!expanded ? 'event-message event-message--clamped' : 'event-message'}>{message}</span>
    {overflowing && <button className="message-disclosure" type="button" title={expanded ? 'Collapse message' : 'Expand message'} aria-label={expanded ? 'Collapse message' : 'Expand message'} aria-expanded={expanded} aria-controls={messageId} onClick={() => setExpanded((value) => !value)}>{expanded ? <ChevronUp aria-hidden="true"/> : <ChevronDown aria-hidden="true"/>}</button>}
  </div>;
}

function EventDetails({ event }: { event: TimelineEvent }) {
  return <details className="log-event-details">
    <summary>Details</summary>
    <pre className="log-event-json">{JSON.stringify(event, null, 2)}</pre>
  </details>;
}

export function RunLogsView({ events, active }: { events: TimelineEvent[]; active: boolean }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const previousLastSequence = useRef(0);
  const [following, setFollowing] = useState(true);
  const [messageWidth, setMessageWidth] = useState(DEFAULT_MESSAGE_WIDTH);
  const lastSequence = events.at(-1)?.sequence ?? 0;
  useEffect(() => {
    const previous = previousLastSequence.current;
    previousLastSequence.current = lastSequence;
    if (!active || !lastSequence || lastSequence <= previous) return;
    if (following) {
      scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight, behavior: 'auto' });
    }
  }, [events, following, lastSequence, active]);
  if (!events.length) return <div className="evidence-message"><strong>Logs not yet available</strong><p>Safe structured events will appear as the run progresses.</p></div>;
  const onScroll = () => {
    const element = scrollRef.current;
    if (!element) return;
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight <= FOLLOW_THRESHOLD;
    setFollowing(atBottom);
  };
  const onMessageResizeStart = (event: React.PointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = messageWidth;
    const onMove = (moveEvent: PointerEvent) => {
      const next = Math.min(MAX_MESSAGE_WIDTH, Math.max(MIN_MESSAGE_WIDTH, startWidth + moveEvent.clientX - startX));
      setMessageWidth(next);
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp, { once: true });
  };
  const tableStyle: LogStyle = { '--log-message-width': `${messageWidth}px` };
  return <div className="logs-region">
    <div className="logs-table-wrap" ref={scrollRef} onScroll={onScroll} data-following={following} tabIndex={-1} aria-label="Structured run logs history"><table className="logs-table" style={tableStyle}><caption className="visually-hidden">Structured run logs</caption><thead><tr><th>Time</th><th>Level</th><th>Phase</th><th>Tool</th><th>Status</th><th><span className="log-message-heading"><span>Message</span><button className="log-column-resizer" type="button" aria-label="Resize message column" onPointerDown={onMessageResizeStart} /></span></th><th>Event</th></tr></thead><tbody>
      {events.map((event) => <tr key={event.sequence}><td><time dateTime={event.time ?? undefined}>{event.time ? new Date(event.time).toLocaleTimeString() : '—'}</time></td><td>{event.level ?? 'info'}</td><td>{event.phase ?? '—'}</td><td>{event.tool ?? event.label ?? '—'}</td><td>{event.status ?? '—'}</td><td><LogMessage event={event} /></td><td><EventDetails event={event} /></td></tr>)}
    </tbody></table></div>
  </div>;
}
