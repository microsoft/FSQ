import { useEffect, useMemo, useState, type RefObject } from 'react';
import type { CaseRecord, ReadinessResponse, RequestResource, RunMode } from '../../../api/types';
import { PreflightStatus } from './PreflightStatus';
import { ChevronDown, ChevronRight, FileCode, Play } from 'lucide-react';

interface CaseTreeDirectory {
  kind: 'directory';
  name: string;
  path: string;
  children: CaseTreeNode[];
}

interface CaseTreeFile {
  kind: 'file';
  name: string;
  path: string;
  case: CaseRecord;
}

type CaseTreeNode = CaseTreeDirectory | CaseTreeFile;

interface OperationComposerProps {
  mode: RunMode;
  goal: string;
  casePath: string;
  cases: CaseRecord[];
  casesState: RequestResource<unknown>['state'];
  readiness: ReadinessResponse | null;
  discoveryLoading: boolean;
  canStart: boolean;
  errorMessage?: string;
  errorAction?: string;
  primaryInputRef: RefObject<HTMLElement | null>;
  onModeChange: (mode: RunMode) => void;
  onGoalChange: (goal: string) => void;
  onCaseChange: (path: string) => void;
  onStart: () => void;
  macos?: boolean;
  android?: boolean;
  starting?: boolean;
  blockedReason?: string;
  setupHint?: string;
  onRecheck?: () => void;
  onRepair?: () => void;
}

function insertCasePath(nodes: CaseTreeNode[], item: CaseRecord) {
  const parts = item.path.split('/').filter(Boolean);
  let current = nodes;
  let currentPath = '';
  for (const [index, part] of parts.entries()) {
    currentPath = currentPath ? `${currentPath}/${part}` : part;
    if (index === parts.length - 1) {
      current.push({ kind: 'file', name: part, path: item.path, case: item });
      return;
    }
    let directory = current.find((node): node is CaseTreeDirectory => node.kind === 'directory' && node.name === part);
    if (!directory) {
      directory = { kind: 'directory', name: part, path: currentPath, children: [] };
      current.push(directory);
    }
    current = directory.children;
  }
}

function sortCaseTree(nodes: CaseTreeNode[]) {
  nodes.sort((left, right) => left.kind === right.kind ? left.name.localeCompare(right.name) : left.kind === 'directory' ? -1 : 1);
  for (const node of nodes) if (node.kind === 'directory') sortCaseTree(node.children);
}

function buildCaseTree(cases: CaseRecord[]) {
  const root: CaseTreeNode[] = [];
  for (const item of cases) insertCasePath(root, item);
  sortCaseTree(root);
  return root;
}

function CaseTree({ nodes, selectedPath, expanded, onToggle, onSelect }: { nodes: CaseTreeNode[]; selectedPath: string; expanded: Set<string>; onToggle: (path: string) => void; onSelect: (path: string) => void }) {
  return <ul className="case-tree-list" role="group">
    {nodes.map((node) => node.kind === 'directory'
      ? <li key={node.path} className="case-tree-item case-tree-item--directory" role="none">
        <button className="case-tree-row" type="button" role="treeitem" aria-expanded={expanded.has(node.path)} aria-label={`${expanded.has(node.path) ? 'Collapse' : 'Expand'} ${node.path}`} onClick={() => onToggle(node.path)}><span aria-hidden="true">{expanded.has(node.path) ? <ChevronDown/> : <ChevronRight/>}</span><strong>{node.name}</strong></button>
        {expanded.has(node.path) && <CaseTree nodes={node.children} selectedPath={selectedPath} expanded={expanded} onToggle={onToggle} onSelect={onSelect} />}
      </li>
      : <li key={node.path} className="case-tree-item" role="none"><button className={`case-tree-row case-tree-row--file${selectedPath === node.path ? ' case-tree-row--selected' : ''}`} type="button" role="treeitem" aria-selected={selectedPath === node.path} onClick={() => onSelect(node.path)}><FileCode aria-hidden="true"/><span>{node.name}</span></button></li>)}
  </ul>;
}

export function OperationComposer(props: OperationComposerProps) {
  const selectedCase = props.cases.find((item) => item.path === props.casePath);
  const selectableCases = useMemo(() => props.cases.filter((item) => item.selectable), [props.cases]);
  const caseTree = useMemo(() => buildCaseTree(selectableCases), [selectableCases]);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [treeOpen, setTreeOpen] = useState(false);
  useEffect(() => setExpanded(new Set()), [selectableCases]);
  const toggleDirectory = (path: string) => setExpanded((value) => {
    const next = new Set(value);
    if (next.has(path)) next.delete(path); else next.add(path);
    return next;
  });
  const selectCase = (path: string) => {
    props.onCaseChange(path);
    setTreeOpen(false);
  };
  return <div className="operation-composer">
    <div className="composer-editor"><h2>Test setup</h2>
    <fieldset className="composer-inputs" disabled={props.starting}><div className="mode-switch" role="radiogroup" aria-label="Operation mode" onKeyDown={event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const next=event.key==='Home'?'explore':event.key==='End'?'strict':props.mode==='explore'?'strict':'explore';props.onModeChange(next);event.currentTarget.querySelector<HTMLButtonElement>('[aria-label="'+(next==='explore'?'Explore':'Strict Replay')+'"]')?.focus();}}>
      <button type="button" role="radio" aria-checked={props.mode === 'explore'} className={props.mode === 'explore' ? 'active' : ''} tabIndex={props.mode==='explore'?0:-1} aria-label="Explore" onClick={() => props.onModeChange('explore')}><strong>Explore</strong><small>Create a Case from a goal</small></button>
      <button type="button" role="radio" aria-checked={props.mode === 'strict'} className={props.mode === 'strict' ? 'active' : ''} tabIndex={props.mode==='strict'?0:-1} aria-label="Strict Replay" onClick={() => props.onModeChange('strict')}><strong>Strict Replay</strong><small>Run an existing Case</small></button>
    </div>
    {props.mode === 'explore' ? <div className="source-pane">
      <label className="field-label" htmlFor="explore-goal">What should FSQ prove?</label>
      <textarea ref={props.primaryInputRef as RefObject<HTMLTextAreaElement>} id="explore-goal" rows={8} value={props.goal} onChange={(event) => props.onGoalChange(event.target.value)} placeholder="Describe the outcome FSQ should verify…" />
      <p className="field-help">FSQ uses the configured model to plan, operate, capture evidence, and verify this goal.</p>
    </div> : <div className="source-pane">
      <span className="field-label" id="strict-case-label">Validated case</span>
      <div className="case-selector">
        <button ref={props.primaryInputRef as RefObject<HTMLButtonElement>} className="case-selector-trigger" type="button" aria-labelledby="strict-case-label strict-case-selection" aria-expanded={treeOpen} aria-controls="strict-case-tree" disabled={props.casesState === 'loading' || Boolean(props.setupHint)} onKeyDown={event=>{if(event.key==='Escape')setTreeOpen(false)}} onClick={() => setTreeOpen((value) => !value)}>
          <span id="strict-case-selection">{props.casesState === 'loading' ? 'Discovering cases…' : selectedCase ? selectedCase.path : 'Select a yaml'}</span><ChevronDown aria-hidden="true"/>
        </button>
        {treeOpen && <div id="strict-case-tree" className="case-tree" role="tree" aria-labelledby="strict-case-label" aria-busy={props.casesState === 'loading'}>
          {selectableCases.length ? <CaseTree nodes={caseTree} selectedPath={props.casePath} expanded={expanded} onToggle={toggleDirectory} onSelect={selectCase} /> : <p className="case-tree-empty">No validated cases available</p>}
        </div>}
      </div>
      {selectedCase && <dl className="case-summary">
        <div><dt>Platform</dt><dd>{selectedCase.platform ?? 'Unknown'}</dd></div>
        <div><dt>Commands</dt><dd>{selectedCase.commandCount}</dd></div><div><dt>Validation</dt><dd>{selectedCase.validationStatus}</dd></div>
        <div><dt>AI assertion</dt><dd>{selectedCase.requiresAiAssertion ? 'Provider required' : 'Not required'}</dd></div>
      </dl>}
      {props.cases.length > selectableCases.length && <p className="field-help">{props.cases.length - selectableCases.length} invalid or platform-mismatched case(s) are unavailable.</p>}
    </div>}
    </fieldset>
    {props.errorMessage && <div className="inline-error" role="alert"><strong>{props.errorMessage}</strong>{props.errorAction && <span>{props.errorAction}</span>}</div>}
    <div className="composer-start"><p id="start-blocked-reason" className="field-help">{props.setupHint || (!props.canStart ? props.blockedReason || "Provide the required source and check the environment." : "Ready to start on the selected target.")}</p>
    <button className="button button--primary start-button" type="button" disabled={!props.canStart} aria-describedby="start-blocked-reason" onClick={props.onStart}><Play aria-hidden="true"/>{props.starting && (props.macos||props.android)?'Checking environment…':props.mode === 'explore' ? 'Start exploration' : 'Start strict replay'}</button>
    </div></div><aside className="composer-environment" aria-label="Environment checks">{props.setupHint ? <div className="preflight-setup"><h2>Environment</h2><span className="status-badge">Not checked</span><p>{props.setupHint}</p></div> : <PreflightStatus mode={props.mode} workspace={props.readiness?.workspace} provider={props.readiness?.provider} target={props.readiness?.target} strict={props.readiness?.strict} requiresProvider={selectedCase?.requiresAiAssertion} loading={props.discoveryLoading} diagnostics={props.readiness} macos={props.macos} android={props.android} onRecheck={props.onRecheck} onRepair={props.onRepair} locked={props.starting}/>}</aside>
  </div>;
}
