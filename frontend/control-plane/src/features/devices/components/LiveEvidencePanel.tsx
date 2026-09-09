import { ContentTabs } from '../../../shared/ContentTabs';
import type { EvidenceTab, PlatformId, RunSnapshot } from '../../../api/types';
import { RunLogsView } from './RunLogsView';
import { ReplayVideoView } from './ReplayVideoView';
import { ScreenView } from './ScreenView';
import { StepEvidenceView } from './StepEvidenceView';
import { UiSnapshotView } from './UiSnapshotView';

const tabs: { id: EvidenceTab; label: string }[] = [
  { id: 'screen', label: 'Screen' }, { id: 'ui-tree', label: 'UI Tree' }, { id: 'logs', label: 'Logs' },
];

interface LiveEvidencePanelProps {
  tab: EvidenceTab;
  snapshot: RunSnapshot | null;
  selectedStepId?: string | null;
  platform: PlatformId | null;
  targetLabel: string;
  onTabChange: (tab: EvidenceTab) => void;
  onClearStep?: () => void;
}

export function LiveEvidencePanel({ tab, snapshot, selectedStepId = null, platform, targetLabel, onTabChange, onClearStep = () => undefined }: LiveEvidencePanelProps) {
  return <section className="evidence-card" aria-labelledby="live-evidence-title">
    <header className="card-header evidence-header"><div><h2 id="live-evidence-title">Live evidence</h2><p>{platform ?? 'No platform'} · {selectedStepId ? `Action ${selectedStepId}` : targetLabel || 'No target selected'}</p></div>
      {selectedStepId && <button className="button evidence-replay-button" type="button" onClick={onClearStep}>Show run replay</button>}
      <ContentTabs label="Live evidence views" value={tab} onChange={onTabChange} className="evidence-tabs" items={tabs.map(item=>({id:item.id,label:item.label,tabId:'evidence-tab-'+item.id,panelId:'evidence-panel-'+item.id}))}/>
    </header>
    <div className="evidence-surface" id={`evidence-panel-${tab}`} role="tabpanel" aria-labelledby={`evidence-tab-${tab}`}>
      {tab === 'screen' && (snapshot?.terminal
        ? selectedStepId ? <StepEvidenceView requestId={snapshot.requestId} stepId={selectedStepId} kind="screen" platform={platform} targetLabel={targetLabel} /> : <ReplayVideoView requestId={snapshot.requestId} />
        : <ScreenView requestId={snapshot?.requestId ?? null} revision={snapshot?.screenshotRevision ?? 0} platform={platform} targetLabel={targetLabel} />)}
      {tab === 'ui-tree' && (snapshot?.terminal && selectedStepId
        ? <StepEvidenceView requestId={snapshot.requestId} stepId={selectedStepId} kind="ui-tree" platform={platform} targetLabel={targetLabel} />
        : <UiSnapshotView requestId={snapshot?.requestId ?? null} revision={snapshot?.uiSnapshotRevision ?? 0} />)}
      {tab === 'logs' && <RunLogsView events={snapshot?.events ?? []} active={Boolean(snapshot && !snapshot.terminal)} />}
    </div>
    <footer className="evidence-footer"><span>{snapshot?.evidenceAvailable ? 'Evidence captured' : 'Awaiting evidence'}</span><span>{snapshot?.runId ? `Run ${snapshot.runId}` : 'No run allocated'}</span></footer>
  </section>;
}
