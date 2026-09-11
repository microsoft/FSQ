import type { ReportFact } from '../../api/types';

export function SnapshotDiffView({ diff, mode = 'before_after', baselineRunId, currentRunId }: { diff: ReportFact | null; mode?: 'before_after'|'baseline_current'; baselineRunId?: string; currentRunId?: string }) {
  const baseline = mode === 'baseline_current';
  const labels = baseline ? ['Baseline · ' + (baselineRunId ?? 'unknown Run'), 'Current · ' + (currentRunId ?? 'unknown Run')] : ['Before','After'];
  const rows = Array.isArray(diff?.rows) ? diff.rows as ReportFact[] : [];
  const content = (row: ReportFact, side: 'before' | 'after') => {
    const segments = row[side + '_segments'];
    return Array.isArray(segments) ? segments.map((segment: ReportFact, index) => segment.changed ? <mark key={index}>{String(segment.text ?? '')}</mark> : <span key={index}>{String(segment.text ?? '')}</span>) : String(row[side] ?? '');
  };
  return <div className="ui-diff" aria-label={baseline ? 'Baseline and Current UI snapshot diff' : 'Before and After UI Tree diff'}><div className="ui-diff-header"><strong>{labels[0]}</strong><strong>{labels[1]}</strong></div>{!rows.length ? <p className="evidence-message">{String(diff?.reason ?? diff?.status ?? 'Snapshot comparison is unavailable.')}</p> : <div className="ui-diff-body">{(['before', 'after'] as const).map(side => <div className="ui-diff-pane" key={side}>{rows.map((row, index) => <div key={index} className={'ui-diff-row ui-diff-row--' + String(row.kind ?? 'context')}><span>{String(row[side + '_number'] ?? '')}</span><code>{content(row, side)}</code></div>)}</div>)}</div>}</div>;
}
