import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { AlertCircle, ChevronDown, ChevronRight, FileCode, FileText, Folder, FolderOpen, Play, RefreshCw } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { ContentTabs } from '../../shared/ContentTabs';
import { controlPlaneClient, toApiError } from '../../api/controlPlaneClient';
import type { ApiErrorBody, PlatformId, WorkspaceEntriesResponse, WorkspaceEntry, WorkspaceFileResponse } from '../../api/types';

interface WorkspaceBrowserProps {
  workspaceName: string;
  onReplayCase?: (platform: PlatformId, casePath: string) => void;
}

function formatBytes(bytes: number | null): string {
  if (bytes === null) return 'Directory';
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
}

function isYamlFile(name: string): boolean {
  return /\.ya?ml$/i.test(name);
}

function replayContext(path: string): { platform: PlatformId; casePath: string } | null {
  const match = path.match(/^cases\/(android|web|windows|macos)\/(.+\.fsq\.yaml)$/);
  return match ? { platform: match[1] as PlatformId, casePath: match[2] } : null;
}

function renderYamlScalar(value: string): ReactNode {
  if (!value) return null;
  const className = /^https?:\/\/\S+$/i.test(value) || /^['"].*['"]$/.test(value) ? 'cp-yaml-string' : 'cp-yaml-value';
  return <span className={className}>{value}</span>;
}

function renderYamlLine(line: string): ReactNode {
  const [, indent = '', body = ''] = line.match(/^(\s*)(.*)$/) ?? [];
  const indentation = indent ? <span className="cp-source-indent">{indent}</span> : null;
  if (!body) return indentation;
  if (body === '---') return <>{indentation}{body}</>;

  const listMatch = body.match(/^-(\s+)(.*)$/);
  const listPrefix = listMatch ? <><span className="cp-yaml-list-marker">-</span>{listMatch[1]}</> : null;
  const content = listMatch ? listMatch[2] : body;
  const keyMatch = content.match(/^([^:]+):(\s*)(.*)$/);
  if (!keyMatch) return <>{indentation}{listPrefix}{renderYamlScalar(content)}</>;

  const [, key, spacing, value] = keyMatch;
  return <>{indentation}{listPrefix}<span className="cp-yaml-key">{key}</span>:{spacing}{renderYamlScalar(value)}</>;
}

function SourceViewer({ content, yaml }: { content: string; yaml: boolean }) {
  if (!yaml) return <pre className="cp-plain-source" aria-label="Read-only source"><code>{content}</code></pre>;

  const lines = content.split(/\r?\n/);
  return <section className="cp-source-viewer cp-yaml-source" aria-label="Read-only YAML source">
    <div className="cp-source-scroll">
      <ol className="cp-source-lines">
        {lines.map((line, index) => <li className="cp-source-line" key={index}>
          <span className="cp-source-line-number" aria-hidden="true" data-line-number={index + 1} />
          <span className="cp-source-line-code">{renderYamlLine(line)}</span>
        </li>)}
      </ol>
    </div>
  </section>;
}

function TreeEntry({ entry, depth, expanded, childrenByPath, loadingPaths, selectedPath, onDirectory, onFile }: {
  entry: WorkspaceEntry;
  depth: number;
  expanded: Set<string>;
  childrenByPath: Record<string, WorkspaceEntriesResponse>;
  loadingPaths: Set<string>;
  selectedPath: string | null;
  onDirectory: (path: string) => void;
  onFile: (path: string) => void;
}) {
  const isDirectory = entry.kind === 'directory';
  const isExpanded = expanded.has(entry.path);
  return <li>
    <button
      className="cp-tree-entry"
      style={{ paddingLeft: `${10 + depth * 18}px` }}
      type="button"
      aria-expanded={isDirectory ? isExpanded : undefined}
      aria-current={!isDirectory && selectedPath === entry.path ? 'true' : undefined}
      onClick={() => isDirectory ? onDirectory(entry.path) : onFile(entry.path)}
    >
      {isDirectory
        ? <><span className="cp-tree-chevron" aria-hidden="true">{isExpanded ? <ChevronDown/> : <ChevronRight/>}</span><span className="cp-tree-type-icon cp-tree-type-icon--folder" aria-hidden="true">{isExpanded ? <FolderOpen/> : <Folder/>}</span></>
        : <span className={`cp-tree-type-icon ${isYamlFile(entry.name) ? 'cp-tree-type-icon--yaml' : 'cp-tree-type-icon--file'}`} aria-hidden="true">{isYamlFile(entry.name) ? <FileCode/> : <FileText/>}</span>}
      <span>{entry.name}</span>
    </button>
    {isDirectory && isExpanded && <ul>
      {loadingPaths.has(entry.path) && <li className="cp-tree-state" style={{ paddingLeft: `${30 + depth * 18}px` }}>Loading…</li>}
      {!loadingPaths.has(entry.path) && childrenByPath[entry.path]?.entries.length === 0 && <li className="cp-tree-state" style={{ paddingLeft: `${30 + depth * 18}px` }}>Empty directory</li>}
      {childrenByPath[entry.path]?.entries.map((child) => <TreeEntry key={child.path} entry={child} depth={depth + 1} expanded={expanded} childrenByPath={childrenByPath} loadingPaths={loadingPaths} selectedPath={selectedPath} onDirectory={onDirectory} onFile={onFile} />)}
    </ul>}
  </li>;
}

export function WorkspaceBrowser({ workspaceName, onReplayCase }: WorkspaceBrowserProps) {
  const markdownPanelId = useId();
  const markdownPreviewTabId = useId();
  const markdownCodeTabId = useId();
  const [root, setRoot] = useState<WorkspaceEntriesResponse | null>(null);
  const [childrenByPath, setChildrenByPath] = useState<Record<string, WorkspaceEntriesResponse>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loadingPaths, setLoadingPaths] = useState<Set<string>>(new Set());
  const [treeError, setTreeError] = useState<ApiErrorBody | null>(null);
  const [file, setFile] = useState<WorkspaceFileResponse | null>(null);
  const [fileError, setFileError] = useState<ApiErrorBody | null>(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [fileTab, setFileTab] = useState<'preview' | 'code'>('preview');
  const [requestedFilePath, setRequestedFilePath] = useState<string | null>(null);
  const activeRequests = useRef<Set<AbortController>>(new Set());
  const activeRootRequest = useRef<AbortController | null>(null);
  const activeFileRequest = useRef<AbortController | null>(null);

  const trackedController = () => {
    const controller = new AbortController();
    activeRequests.current.add(controller);
    return controller;
  };

  const loadRoot = () => {
    activeRootRequest.current?.abort();
    setTreeError(null);
    setRoot(null);
    const controller = trackedController();
    activeRootRequest.current = controller;
    controlPlaneClient.workspaceEntries(workspaceName, '', controller.signal).then((response) => {
      if (!controller.signal.aborted && activeRootRequest.current === controller) setRoot(response);
    }).catch((error) => {
      if (controller.signal.aborted || activeRootRequest.current !== controller) return;
      setTreeError(toApiError(error));
    }).finally(() => {
      activeRequests.current.delete(controller);
      if (activeRootRequest.current === controller) activeRootRequest.current = null;
    });
    return controller;
  };

  useEffect(() => {
    setChildrenByPath({});
    setExpanded(new Set());
    setLoadingPaths(new Set());
    setFile(null);
    setFileError(null);
    setFileLoading(false);
    setRequestedFilePath(null);
    const controller = loadRoot();
    return () => {
      controller.abort();
      activeRequests.current.forEach((request) => request.abort());
      activeRequests.current.clear();
      activeRootRequest.current = null;
      activeFileRequest.current = null;
    };
  }, [workspaceName]);

  const onDirectory = (path: string) => {
    if (expanded.has(path)) {
      setExpanded((current) => { const next = new Set(current); next.delete(path); return next; });
      return;
    }
    setExpanded((current) => new Set(current).add(path));
    if (childrenByPath[path] || loadingPaths.has(path)) return;
    setLoadingPaths((current) => new Set(current).add(path));
    const controller = trackedController();
    controlPlaneClient.workspaceEntries(workspaceName, path, controller.signal).then((response) => {
      if (!controller.signal.aborted) setChildrenByPath((current) => ({ ...current, [path]: response }));
    }).catch((error) => { if (!controller.signal.aborted) setTreeError(toApiError(error)); }).finally(() => {
      activeRequests.current.delete(controller);
      if (!controller.signal.aborted) setLoadingPaths((current) => { const next = new Set(current); next.delete(path); return next; });
    });
  };

  const onFile = (path: string) => {
    activeFileRequest.current?.abort();
    setFileLoading(true);
    setFileError(null);
    setFile(null);
    setRequestedFilePath(path);
    const controller = trackedController();
    activeFileRequest.current = controller;
    controlPlaneClient.workspaceFile(workspaceName, path, controller.signal).then((response) => {
      if (controller.signal.aborted || activeFileRequest.current !== controller) return;
      setFile(response);
      setFileTab(response.presentation === 'markdown' ? 'preview' : 'code');
    }).catch((error) => { if (!controller.signal.aborted) setFileError(toApiError(error)); }).finally(() => {
      activeRequests.current.delete(controller);
      if (activeFileRequest.current === controller) {
        activeFileRequest.current = null;
        if (!controller.signal.aborted) setFileLoading(false);
      }
    });
  };

  const selectedReplayContext = file ? replayContext(file.path) : null;

  return <section className="cp-workspace-browser" aria-label={`Workspace files for ${workspaceName}`}>
    <div className="cp-browser-grid">
      <section className="cp-tree-pane" aria-label="Workspace file tree">
        <header className="cp-tree-header"><FolderOpen aria-hidden="true"/><h2>Files</h2></header>
        <div className="cp-tree-content">
          {treeError && <div className="cp-inline-error"><AlertCircle aria-hidden="true" /><span><strong>{treeError.message}</strong><small>{treeError.action}</small></span><button className="cp-icon-button" type="button" aria-label="Retry workspace files" onClick={loadRoot}><RefreshCw aria-hidden="true" /></button></div>}
          {!root && !treeError && <p className="cp-pane-state">Loading workspace files…</p>}
          {root?.entries.length === 0 && <p className="cp-pane-state">No cases or knowledge files</p>}
          {root && <ul className="cp-tree-root">{root.entries.map((entry) => <TreeEntry key={entry.path} entry={entry} depth={0} expanded={expanded} childrenByPath={childrenByPath} loadingPaths={loadingPaths} selectedPath={file?.path ?? null} onDirectory={onDirectory} onFile={onFile} />)}</ul>}
        </div>
      </section>
      <section className="cp-file-pane" aria-label="Workspace file content">
        {!file && !fileError && !fileLoading && <div className="cp-file-empty"><FileText aria-hidden="true" /><strong>Select a text file</strong><span>Preview workspace knowledge or inspect authored cases without exposing private configuration.</span></div>}
        {fileLoading && <p className="cp-pane-state">Loading file…</p>}
        {fileError && <div className="cp-file-empty cp-file-empty--error"><AlertCircle aria-hidden="true" /><strong>{fileError.message}</strong><span>{fileError.action}</span>{requestedFilePath && <button className="button" type="button" onClick={() => onFile(requestedFilePath)}><RefreshCw aria-hidden="true" />Retry file</button>}</div>}
        {file && <>
          <header className="cp-file-header">
            <div><div className="cp-file-breadcrumb" aria-label="File path"><span>{workspaceName} / {file.path.split('/').slice(0,-1).join(' / ')}</span><strong> / {file.name}</strong></div><small>Read only · {file.lineCount} lines · {formatBytes(file.size)}</small></div>
            {selectedReplayContext && <button className="button button--primary button--compact cp-replay-case" type="button" onClick={() => onReplayCase?.(selectedReplayContext.platform, selectedReplayContext.casePath)}><Play aria-hidden="true"/>Replay Case</button>}
          </header>
          <div className="cp-file-panel">
            {file.presentation==='markdown' && <ContentTabs label="Markdown presentation" className="cp-file-tabs" value={fileTab} onChange={setFileTab} items={[{id:'preview',label:'Preview',tabId:markdownPreviewTabId,panelId:markdownPanelId},{id:'code',label:'Code',tabId:markdownCodeTabId,panelId:markdownPanelId}]}/>}
            <div className="cp-file-content" id={file.presentation === 'markdown' ? markdownPanelId : undefined} role={file.presentation === 'markdown' ? 'tabpanel' : undefined} aria-labelledby={file.presentation === 'markdown' ? (fileTab === 'preview' ? markdownPreviewTabId : markdownCodeTabId) : undefined}>
              {file.presentation === 'markdown' && fileTab === 'preview'
                ? <article className="cp-markdown"><ReactMarkdown skipHtml>{file.content}</ReactMarkdown></article>
                : file.presentation === 'markdown'
                  ? <article className="cp-markdown cp-markdown-source"><pre><code>{file.content}</code></pre></article>
                  : <SourceViewer content={file.content} yaml={isYamlFile(file.name)} />}
            </div>
          </div>
        </>}
      </section>
    </div>
  </section>;
}