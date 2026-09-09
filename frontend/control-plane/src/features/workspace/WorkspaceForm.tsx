import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Eye, EyeOff, FolderOpen, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { ControlPlaneApiError, controlPlaneClient, toApiError } from '../../api/controlPlaneClient';
import type {
  ApiErrorBody,
  PlatformId,
  AddWorkspacePlatformPayload,
  WorkspaceDetail,
  WorkspacePlatformDetail,
  WorkspaceTargetInput,
  WebBrowserChannel,
} from '../../api/types';

interface WorkspaceFormProps {
  mode: 'create' | 'add' | 'edit';
  workspace?: WorkspaceDetail;
  detail?: WorkspacePlatformDetail;
  allowedPlatforms?: readonly PlatformId[];
  onCancel: () => void;
  onSaved: (workspace: WorkspaceDetail, platform?: WorkspacePlatformDetail) => void;
  onReloadLatest?: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  onPendingChange?: (pending: boolean) => void;
}

interface TargetDraft {
  appId: string;
  browserExecutablePath: string;
  browserChannel: WebBrowserChannel;
  appPath: string;
  windowTitleRe: string;
  launchArgs: string;
  bundleId: string;
}
interface EnvRow { id: number; name: string; value: string }
interface PlatformDraft { id: number; platform: PlatformId | ''; target: TargetDraft; envRows: EnvRow[] }

const allPlatforms: PlatformId[] = ['android', 'web', 'windows', 'macos'];
const platformLabels: Record<PlatformId, string> = { android: 'Android', web: 'Web', windows: 'Windows', macos: 'macOS' };
const webChannels: { value: WebBrowserChannel; label: string }[] = [
  { value: 'chromium', label: 'Chromium' }, { value: 'chrome', label: 'Google Chrome' }, { value: 'chrome-beta', label: 'Google Chrome Beta' },
  { value: 'chrome-dev', label: 'Google Chrome Dev' }, { value: 'chrome-canary', label: 'Google Chrome Canary' },
  { value: 'msedge', label: 'Microsoft Edge' }, { value: 'msedge-beta', label: 'Microsoft Edge Beta' },
  { value: 'msedge-dev', label: 'Microsoft Edge Dev' }, { value: 'msedge-canary', label: 'Microsoft Edge Canary' },
];
const emptyTarget = (): TargetDraft => ({ appId: '', browserChannel: 'chrome', browserExecutablePath: '', appPath: '', windowTitleRe: '', launchArgs: '', bundleId: '' });

function targetFromDetail(detail?: WorkspacePlatformDetail): TargetDraft {
  const draft = emptyTarget();
  if (!detail) return draft;
  const target = detail.target;
  if ('appId' in target) draft.appId = target.appId;
  if ('browserExecutablePath' in target) draft.browserExecutablePath = target.browserExecutablePath;
  if ('browserChannel' in target) draft.browserChannel = target.browserChannel;
  if ('appPath' in target) draft.appPath = target.appPath ?? '';
  if ('windowTitleRe' in target) draft.windowTitleRe = target.windowTitleRe ?? '';
  if ('launchArgs' in target) draft.launchArgs = target.launchArgs;
  if ('bundleId' in target) draft.bundleId = target.bundleId ?? '';
  return draft;
}

function targetPayload(platform: PlatformId, target: TargetDraft): WorkspaceTargetInput {
  if (platform === 'android') return { appId: target.appId.trim() };
  if (platform === 'web') return {
    browserChannel: target.browserChannel,
    ...(target.browserExecutablePath.trim() ? { browserExecutablePath: target.browserExecutablePath.trim() } : {}),
  };
  if (platform === 'windows') return {
    appPath: target.appPath.trim(),
    ...(target.windowTitleRe.trim() ? { windowTitleRe: target.windowTitleRe.trim() } : {}),
    launchArgs: target.launchArgs,
  };
  return {
    ...(target.bundleId.trim() ? { bundleId: target.bundleId.trim() } : {}),
    ...(target.appPath.trim() ? { appPath: target.appPath.trim() } : {}),
  };
}

function envRecord(rows: EnvRow[]): Record<string, string> {
  return Object.fromEntries(rows.map((row) => [row.name, row.value]));
}

export function WorkspaceForm({ mode, workspace, detail, allowedPlatforms = allPlatforms, onCancel, onSaved, onReloadLatest, onDirtyChange, onPendingChange }: WorkspaceFormProps) {
  const callbacks = useRef({onDirtyChange,onPendingChange});
  callbacks.current = {onDirtyChange,onPendingChange};
  const initialPlatform = detail?.platform ?? allowedPlatforms[0] ?? 'android';
  const initialRows = Object.entries(detail?.env ?? {}).map(([name, value], index) => ({ id: index + 1, name, value }));
  const [name, setName] = useState(detail?.name ?? workspace?.name ?? '');
  const [selectedPath, setSelectedPath] = useState('');
  const [selectedPathIsEmpty, setSelectedPathIsEmpty] = useState<boolean | null>(null);
  const [drafts, setDrafts] = useState<PlatformDraft[]>(mode === 'create' ? [] : [{ id: 1, platform: initialPlatform, target: targetFromDetail(detail), envRows: initialRows }]);
  const [revealed, setRevealed] = useState<Set<number>>(new Set());
  const [error, setError] = useState<ApiErrorBody | null>(null);
  const [fieldError, setFieldError] = useState('');
  const [pending, setPending] = useState(false);
  const [pickerPending, setPickerPending] = useState(false);
  const nextDraftId = useRef(2);
  const nextRowId = useRef(initialRows.length + 1);
  const firstField = useRef<HTMLInputElement>(null);
  const pickerButton = useRef<HTMLButtonElement>(null);
  const pickerRequest = useRef(0);
  const draftFields = useRef(new Map<string, HTMLElement>());
  const singleDraft = drafts[0];
  const initialValue = useMemo(() => JSON.stringify({ target: detail?.target, env: detail?.env }), [detail]);
  const currentValue = singleDraft?.platform
    ? JSON.stringify({ target: targetPayload(singleDraft.platform, singleDraft.target), env: envRecord(singleDraft.envRows) })
    : '';
  const dirty = mode === 'create'
    ? Boolean(name || selectedPath || drafts.length)
    : mode === 'add' ? Boolean(singleDraft && (Object.values(singleDraft.target).some(Boolean) || singleDraft.envRows.length > 0)) : currentValue !== initialValue;
  const finalPath = selectedPathIsEmpty === null ? '' : selectedPathIsEmpty ? selectedPath : `${selectedPath.replace(/[\\/]+$/, '')}/${name.trim()}`;

  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange]);
  useEffect(() => () => {
    pickerRequest.current += 1;
    callbacks.current.onDirtyChange?.(false);
    callbacks.current.onPendingChange?.(false);
  }, []);
  useEffect(() => onPendingChange?.(pending), [pending,onPendingChange]);

  const chooseParentFolder = async () => {
    const request = ++pickerRequest.current;
    setPickerPending(true);
    setError(null);
    try {
      const result = await controlPlaneClient.pickWorkspaceParentDirectory();
      if (request !== pickerRequest.current) return;
      if (result.status === 'selected') {
        setSelectedPath(result.selectedPath);
        setSelectedPathIsEmpty(result.isEmpty);
        setFieldError('');
      } else {
        pickerButton.current?.focus();
      }
    } catch (reason) {
      if (request === pickerRequest.current) setError(toApiError(reason));
    } finally {
      if (request === pickerRequest.current) setPickerPending(false);
    }
  };

  const updateDraft = (draftId: number, update: (draft: PlatformDraft) => PlatformDraft) => {
    setDrafts((current) => current.map((draft) => draft.id === draftId ? update(draft) : draft));
  };
  const updateTarget = (draftId: number, field: keyof TargetDraft, value: string) => updateDraft(draftId, (draft) => ({ ...draft, target: { ...draft.target, [field]: value } }));
  const changePlatform = (draftId: number, value: PlatformId | '') => {
    const previousRows = drafts.find((draft) => draft.id === draftId)?.envRows ?? [];
    updateDraft(draftId, (draft) => ({ ...draft, platform: value, target: emptyTarget(), envRows: [] }));
    setRevealed((current) => {
      const next = new Set(current);
      previousRows.forEach((row) => next.delete(row.id));
      return next;
    });
  };
  const addPlatformDraft = () => {
    if (drafts.length >= allowedPlatforms.length) return;
    setDrafts((current) => [...current, { id: nextDraftId.current++, platform: '', target: emptyTarget(), envRows: [] }]);
  };
  const removeDraft = (draftId: number) => {
    const removedRows = drafts.find((draft) => draft.id === draftId)?.envRows ?? [];
    setDrafts((current) => current.filter((draft) => draft.id !== draftId));
    setRevealed((current) => {
      const next = new Set(current);
      removedRows.forEach((row) => next.delete(row.id));
      return next;
    });
  };
  const addEnv = (draftId: number) => updateDraft(draftId, (draft) => ({ ...draft, envRows: [...draft.envRows, { id: nextRowId.current++, name: '', value: '' }] }));
  const updateEnv = (draftId: number, id: number, field: 'name' | 'value', value: string) => updateDraft(draftId, (draft) => ({ ...draft, envRows: draft.envRows.map((row) => row.id === id ? { ...row, [field]: value } : row) }));
  const deleteEnv = (draftId: number, id: number) => {
    updateDraft(draftId, (draft) => ({ ...draft, envRows: draft.envRows.filter((row) => row.id !== id) }));
    setRevealed((current) => { const next = new Set(current); next.delete(id); return next; });
  };

  const platformPayload = (draft: PlatformDraft): AddWorkspacePlatformPayload | null => {
    const { platform, target, envRows } = draft;
    const invalid = (message: string, field: string) => {
      setFieldError(message);
      draftFields.current.get(`${draft.id}:${field}`)?.focus();
      return null;
    };
    if (!platform) return invalid('Select a platform for every platform section.', 'platform');
    if (platform === 'android' && !target.appId.trim()) return invalid('Android App ID is required.', 'target');
    if (platform === 'windows' && !target.appPath.trim()) return invalid('Windows application path is required.', 'target');
    if (platform === 'macos' && !target.bundleId.trim() && !target.appPath.trim()) return invalid('macOS Bundle ID or application path is required.', 'target');
    const env: Record<string, string> = {};
    for (const row of envRows) {
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(row.name)) return invalid('Environment names must use letters, digits, and underscores and cannot start with a digit.', `env-name-${row.id}`);
      if (!row.value.trim()) return invalid(`Environment value for ${row.name} cannot be blank.`, `env-value-${row.id}`);
      if (row.name in env) return invalid(`Environment name ${row.name} is duplicated.`, `env-name-${row.id}`);
      env[row.name] = row.value;
    }
    return { platform, target: targetPayload(platform, target), env };
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (mode === 'create' && !name.trim()) { setFieldError('Enter a workspace name.'); firstField.current?.focus(); return; }
    if (mode === 'create' && !selectedPath.trim()) { setFieldError('Choose a folder.'); pickerButton.current?.focus(); return; }
    if (mode === 'create' && drafts.length === 0) { setFieldError('Add at least one platform.'); document.querySelector<HTMLElement>('.cp-add-platform')?.focus(); return; }
    const platformPayloads: AddWorkspacePlatformPayload[] = [];
    for (const draft of drafts) {
      const payload = platformPayload(draft);
      if (!payload) return;
      platformPayloads.push(payload);
    }
    setFieldError('');
    setPending(true);
    callbacks.current.onPendingChange?.(true);
    setError(null);
    try {
      if (mode === 'create') {
        const saved = await controlPlaneClient.createWorkspace({ name: name.trim(), selectedPath: selectedPath.trim(), platforms: platformPayloads });
        onSaved(saved);
      } else if (mode === 'add') {
        const saved = await controlPlaneClient.addWorkspacePlatform(workspace!.name, platformPayloads[0]);
        onSaved(saved.workspace, saved.platform);
      } else {
        const payload = platformPayloads[0];
        const saved = await controlPlaneClient.updateWorkspacePlatform(detail!.name, detail!.platform, { target: payload!.target, env: payload!.env, expectedRevision: detail!.revision });
        onSaved(saved.workspace, saved.platform);
      }
    } catch (reason) {
      setError(toApiError(reason));
      if (reason instanceof ControlPlaneApiError && reason.body.code !== 'workspace_conflict') firstField.current?.focus();
    } finally {
      setPending(false);
      callbacks.current.onPendingChange?.(false);
    }
  };

  const renderTarget = (draft: PlatformDraft) => {
    if (!draft.platform) return null;
    const fields = <div className={mode === 'add' ? 'cp-form-grid cp-add-target' : 'cp-form-grid'}>
    {draft.platform === 'android' && <label><span>App ID</span><input ref={(element) => { if (element) draftFields.current.set(`${draft.id}:target`, element); if (mode !== 'create') firstField.current = element; }} value={draft.target.appId} onChange={(event) => updateTarget(draft.id, 'appId', event.target.value)} placeholder="com.example.app" /></label>}
    {draft.platform === 'web' && <><label><span>Browser channel</span><select value={draft.target.browserChannel} onChange={(event) => updateTarget(draft.id, 'browserChannel', event.target.value as WebBrowserChannel)}>{webChannels.map((channel) => <option key={channel.value} value={channel.value}>{channel.label}</option>)}</select></label><label className="cp-field-wide"><span>Web path <small>Optional</small></span><input aria-label="Web path" ref={(element) => { if (element) draftFields.current.set(`${draft.id}:target`, element); if (mode !== 'create') firstField.current = element; }} value={draft.target.browserExecutablePath} onChange={(event) => updateTarget(draft.id, 'browserExecutablePath', event.target.value)} placeholder="Browser executable path" /><small>Leave blank to discover the selected installed browser channel.</small></label></>}
    {draft.platform === 'windows' && <><label className="cp-field-wide"><span>App path</span><input ref={(element) => { if (element) draftFields.current.set(`${draft.id}:target`, element); if (mode !== 'create') firstField.current = element; }} value={draft.target.appPath} onChange={(event) => updateTarget(draft.id, 'appPath', event.target.value)} /></label><label><span>Window title regex <small>Optional</small></span><input value={draft.target.windowTitleRe} onChange={(event) => updateTarget(draft.id, 'windowTitleRe', event.target.value)} /></label><label><span>Launch args <small>Optional</small></span><input value={draft.target.launchArgs} onChange={(event) => updateTarget(draft.id, 'launchArgs', event.target.value)} /></label></>}
    {draft.platform === 'macos' && <><label><span>Bundle ID</span><input ref={(element) => { if (element) draftFields.current.set(`${draft.id}:target`, element); if (mode !== 'create') firstField.current = element; }} value={draft.target.bundleId} onChange={(event) => updateTarget(draft.id, 'bundleId', event.target.value)} placeholder="com.example.App" /></label><label><span>App path</span><input value={draft.target.appPath} onChange={(event) => updateTarget(draft.id, 'appPath', event.target.value)} /></label></>}
    </div>;
    return mode === 'add' ? fields : <section className="cp-form-section"><div><h3>Target</h3><p>Identify the local application FSQ should operate.</p></div>{fields}</section>;
  };

  const renderEnvironment = (draft: PlatformDraft) => draft.platform ? <details className="cp-env-disclosure" open={mode === 'edit'}><summary>Environment <span>{draft.envRows.length ? `${draft.envRows.length} configured` : 'Optional'}</span></summary><div className="cp-env-content"><p>Values remain local and are masked by default.</p>{draft.envRows.map((row) => { const visibilityLabel = `${revealed.has(row.id) ? 'Hide' : 'Show'} value for ${row.name || 'environment row'}`; return <div className="cp-env-row" key={row.id}><label><span>Name</span><input ref={(element) => { if (element) draftFields.current.set(`${draft.id}:env-name-${row.id}`, element); }} value={row.name} onChange={(event) => updateEnv(draft.id, row.id, 'name', event.target.value)} placeholder="TEST_PASSWORD" autoComplete="off" /></label><label><span>Value</span><span className="cp-secret-input"><input ref={(element) => { if (element) draftFields.current.set(`${draft.id}:env-value-${row.id}`, element); }} type={revealed.has(row.id) ? 'text' : 'password'} value={row.value} onChange={(event) => updateEnv(draft.id, row.id, 'value', event.target.value)} autoComplete="new-password" /><button type="button" aria-label={visibilityLabel} title={visibilityLabel} onClick={() => setRevealed((current) => { const next = new Set(current); next.has(row.id) ? next.delete(row.id) : next.add(row.id); return next; })}>{revealed.has(row.id) ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}</button></span></label><button className="cp-icon-button cp-delete-env" type="button" aria-label={`Delete ${row.name || 'environment row'}`} onClick={() => deleteEnv(draft.id, row.id)}><Trash2 aria-hidden="true" /></button></div>; })}<button className="button" type="button" onClick={() => addEnv(draft.id)}><Plus aria-hidden="true" />Add environment value</button></div></details> : null;

  return <form className={`cp-workspace-form cp-workspace-form--${mode}`} onSubmit={submit}>
    <fieldset disabled={pending}>
      <legend>{mode === 'create' ? 'Create workspace' : mode === 'add' ? 'Add platform' : `Edit ${platformLabels[detail!.platform]}`}</legend>
      {mode === 'create' ? <div className="cp-form-grid cp-form-grid--identity">
        <label><span>Workspace name</span><input ref={firstField} value={name} onChange={(event) => setName(event.target.value)} autoComplete="off" /></label>
        <div className="cp-selected-folder"><label><span>Selected folder</span><input value={selectedPath} placeholder="No folder selected" readOnly /></label><button ref={pickerButton} className="button" type="button" onClick={chooseParentFolder} disabled={pickerPending} aria-busy={pickerPending}>{pickerPending ? 'Choosing...' : <><FolderOpen aria-hidden="true" />Choose folder</>}</button></div>
        <label><span>Final path</span><input value={finalPath} placeholder="Select a folder to preview the workspace path" readOnly /></label>
      </div> : mode === 'edit' ? <p className="cp-form-platform-label">Platform: {platformLabels[detail!.platform]}</p> : null}

      {mode === 'create' && <div className="cp-platform-builder">
        <button className="button cp-add-platform" type="button" onClick={addPlatformDraft} disabled={drafts.length >= allowedPlatforms.length}><Plus aria-hidden="true" />Add platform</button>
        <div className="cp-platform-list">{drafts.map((draft, index) => {
          const selectedElsewhere = new Set(drafts.filter((candidate) => candidate.id !== draft.id).map((candidate) => candidate.platform));
          return <section className="cp-platform-draft" key={draft.id} aria-labelledby={`platform-draft-${draft.id}`}>
            <header><h2 id={`platform-draft-${draft.id}`}>Platform {index + 1}</h2><button className="cp-icon-button cp-remove-draft" type="button" aria-label={`Remove platform ${index + 1}`} title="Remove platform" onClick={() => removeDraft(draft.id)}><Trash2 aria-hidden="true" /></button></header>
            <label className="cp-platform-select"><span>Platform</span><select ref={(element) => { if (element) draftFields.current.set(`${draft.id}:platform`, element); }} aria-label={`Platform ${index + 1}`} value={draft.platform} onChange={(event) => changePlatform(draft.id, event.target.value as PlatformId | '')}><option value="">Select platform</option>{allowedPlatforms.map((platform) => <option key={platform} value={platform} disabled={selectedElsewhere.has(platform)}>{platformLabels[platform]}</option>)}</select></label>
            {renderTarget(draft)}
            {renderEnvironment(draft)}
          </section>;
        })}</div>
      </div>}
      {mode === 'add' && singleDraft && <div className="cp-platform-draft-tools"><label><span>Platform</span><select ref={(element) => { if (element) draftFields.current.set(`${singleDraft.id}:platform`, element); }} aria-label="Platform" value={singleDraft.platform} onChange={(event) => changePlatform(singleDraft.id, event.target.value as PlatformId)}>{allowedPlatforms.map((platform) => <option key={platform} value={platform}>{platformLabels[platform]}</option>)}</select></label></div>}
      {mode !== 'create' && singleDraft && <>{renderTarget(singleDraft)}{renderEnvironment(singleDraft)}</>}

      {fieldError && <p className="cp-form-error" role="alert"><AlertCircle aria-hidden="true" />{fieldError}</p>}
      {error && <div className="cp-form-error cp-form-error--server" role="alert"><AlertCircle aria-hidden="true" /><span><strong>{error.message}</strong><small>{error.action}</small></span>{error.code.startsWith('directory_picker_') && <button className="button" type="button" onClick={chooseParentFolder} disabled={pickerPending}><RefreshCw aria-hidden="true" />Retry folder selection</button>}{error.code === 'workspace_conflict' && onReloadLatest && <button className="button" type="button" onClick={onReloadLatest}><RefreshCw aria-hidden="true" />Reload latest</button>}</div>}
      <div className="cp-form-actions"><button className="button" type="button" onClick={onCancel}>Cancel</button><button className="button button--primary" type="submit" disabled={pickerPending}>{pending ? 'Saving…' : mode === 'create' ? 'Create workspace' : mode === 'add' ? 'Add platform' : 'Save changes'}</button></div>
    </fieldset>
  </form>;
}
