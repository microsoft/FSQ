import type {
  HistoryResponse, RunReportResponse, ReportExportResponse, ReportFormat,
  ApiErrorBody,
  AzureConfigPayload,
  OpenAIConfigPayload,
  GoogleGeminiConfigPayload,
  OpenAIModelsResponse,
  BootstrapResponse,
  CasesResponse,
  ConfigResponse,
  ConnectionTestResponse,
  GitHubDeviceFlowResponse,
  PlatformId,
  ReadinessResponse,
  ReplayFramesResponse,
  ReplayVideoResponse,
  SaveYamlResponse,
  SaveYamlPayload,
  RunSnapshot,
  StartRunPayload,
  StartRunResponse,
  TargetsResponse,
  StepArtifactsResponse,
  UiSnapshotResponse,
  WorkspaceDetail,
  WorkspaceEntriesResponse,
  WorkspaceFileResponse,
  WorkspaceListResponse,
  WorkspaceParentDirectoryPickerResponse,
  CreateWorkspacePayload,
  AddWorkspacePlatformPayload,
  UpdateWorkspacePlatformPayload,
  WorkspacePlatformDetail,
  WorkspacePlatformMutationResponse,
} from './types';

import { historyValid, publicReportValid, exportValid, diff as reportDiffValid } from './reportValidation';

const API_BASE = '/api/control-plane';

function validateHistory(value: unknown): HistoryResponse {
  if (!historyValid(value)) invalidResponse('history', 'Invalid persisted Run fields.');
  return value;
}
function validateRunReport(value: unknown): RunReportResponse {
  if (!record(value) || !string(value.workspace) || !string(value.run_id) || !platform(value.platform) || !publicReportValid(value.report) || !arrayOf(value.warnings,string)) invalidResponse('report', 'Invalid report fields.');
  if (value.report.run.run_id !== value.run_id || value.report.run.platform !== value.platform) invalidResponse('report', 'Mismatched report identity.');
  return value as unknown as RunReportResponse;
}
function validateReportExport(value: unknown): ReportExportResponse {
  if (!exportValid(value)) invalidResponse('export', 'Invalid report download mapping.');
  return value;
}
const platforms = new Set<PlatformId>(['android', 'web', 'windows', 'macos']);
const modes = new Set(['explore', 'strict']);
const statuses = new Set(['preparing', 'running', 'finalizing', 'success', 'failed', 'inconclusive', 'cancelled', 'error']);
const readinessStatuses = new Set(['ready', 'unavailable', 'error']);
const deviceFlowStatuses = new Set(['waiting', 'loading_models', 'ready', 'model_error', 'success', 'failed', 'expired', 'cancelled']);
const providerTypes = new Set(['openai', 'azure_openai', 'google_gemini', 'github_copilot']);

export class ControlPlaneApiError extends Error {
  readonly status: number;
  readonly body: ApiErrorBody;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.name = 'ControlPlaneApiError';
    this.status = status;
    this.body = body;
  }
}

async function responseError(response: Response): Promise<ControlPlaneApiError> {
  const candidate = await response.json().catch(() => null) as Partial<ApiErrorBody> | null;
  return new ControlPlaneApiError(response.status, {
    code: candidate?.code ?? 'request_failed',
    message: candidate?.message ?? `Request failed (${response.status}).`,
    action: candidate?.action ?? 'Retry the request.',
    details: candidate?.details,
  });
}

function invalidResponse(path: string, detail: string): never {
  throw new ControlPlaneApiError(200, {
    code: 'invalid_response',
    message: 'The Control Plane server returned an invalid response.',
    action: 'Restart or update the local Control Plane server and retry.',
    details: { path, detail },
  });
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function string(value: unknown): value is string { return typeof value === 'string'; }
function nullableString(value: unknown): value is string | null { return value === null || string(value); }
function bool(value: unknown): value is boolean { return typeof value === 'boolean'; }
function finiteNumber(value: unknown): value is number { return typeof value === 'number' && Number.isFinite(value); }
function nonNegativeInteger(value: unknown): value is number { return finiteNumber(value) && Number.isInteger(value) && value >= 0; }
function platform(value: unknown): value is PlatformId { return string(value) && platforms.has(value as PlatformId); }
function arrayOf(value: unknown, predicate: (item: unknown) => boolean): boolean { return Array.isArray(value) && value.every(predicate); }
function hasOnlyKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const allowed = new Set(keys);
  return Object.keys(value).every((key) => allowed.has(key)) && keys.every((key) => key in value);
}
function hasNoUnknownKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const allowed = new Set(keys);
  return Object.keys(value).every((key) => allowed.has(key));
}

function activeTask(value: unknown): boolean {
  return record(value) && string(value.requestId) && nullableString(value.runId) && string(value.workspaceName) && platform(value.platform)
    && string(value.targetId) && string(value.mode) && modes.has(value.mode) && string(value.status) && statuses.has(value.status);
}
function readinessRecord(value: unknown): boolean {
  return record(value) && string(value.status) && readinessStatuses.has(value.status) && string(value.message) && string(value.action);
}
function timelineEvent(value: unknown): boolean {
  if (!record(value) || !nonNegativeInteger(value.sequence)) return false;
  return (value.time === undefined || nullableString(value.time)) && ['phase', 'stepId', 'label', 'status', 'message', 'level', 'toolCallId'].every((key) => value[key] === undefined || string(value[key]))
    && (value.tool === undefined || nullableString(value.tool))
    && (value.durationMs === undefined || value.durationMs === null || finiteNumber(value.durationMs));
}

export function validateRunSnapshot(value: unknown, path = 'run snapshot'): RunSnapshot {
  if (!activeTask(value) || !record(value)) invalidResponse(path, 'Invalid task identity or discriminants.');
  const source = value.source;
  const activeStep = value.activeStep;
  const validSource = record(source)
    && (source.goal === undefined || string(source.goal))
    && (source.casePath === undefined || string(source.casePath))
    && (source.caseContent === undefined || string(source.caseContent))
    && (source.caseSteps === undefined || arrayOf(source.caseSteps, strictCaseStep))
    && (value.mode === 'explore' ? string(source.goal) && source.casePath === undefined : string(source.casePath) && source.goal === undefined);
  const validActiveStep = activeStep === null || (record(activeStep) && string(activeStep.stepId) && (activeStep.label === undefined || string(activeStep.label)));
  if (!validSource || !string(value.startedAt) || !nullableString(value.completedAt) || !bool(value.cancelRequested)
    || !arrayOf(value.events, timelineEvent) || !validActiveStep || !(value.result === null || record(value.result))
    || !string(value.summary) || !nonNegativeInteger(value.screenshotRevision) || !nonNegativeInteger(value.uiSnapshotRevision)
    || !bool(value.evidenceAvailable) || !bool(value.reportAvailable) || !bool(value.terminal)
    || (value.suggestedCaseName !== undefined && !nullableString(value.suggestedCaseName))
    || (value.recordingDraft !== undefined && value.recordingDraft !== null && !bool(value.recordingDraft))) {
    invalidResponse(path, 'Invalid run snapshot fields.');
  }
  return value as unknown as RunSnapshot;
}

function strictCaseStep(value: unknown): boolean {
  if (!record(value) || !string(value.stepId) || !nonNegativeInteger(value.index) || !string(value.authoredActionName) || !string(value.actionName) || !string(value.kind)) return false;
  return ['sourceStepId','stepExecutionId','skipReason','blockedByStep','failureCategory','message','status','aggregateStatus'].every(key => value[key] == null || string(value[key]))
    && (value.evidenceErrors == null || arrayOf(value.evidenceErrors, record))
    && ['attemptIndex','maxAttempts','durationMs'].every(key => value[key] == null || nonNegativeInteger(value[key]))
    && (value.invocationPath == null || arrayOf(value.invocationPath,string))
    && (value.attempts == null || arrayOf(value.attempts, item => record(item) && string(item.stepExecutionId) && string(item.status) && (item.attemptIndex == null || nonNegativeInteger(item.attemptIndex))));
}

function validateBootstrap(value: unknown): BootstrapResponse {
  if (!record(value) || !string(value.apiVersion)
    || !arrayOf(value.platforms, (item) => record(item) && platform(item.id) && string(item.label))
    || !bool(value.busy) || !(value.activeTask === null || activeTask(value.activeTask))) invalidResponse('bootstrap', 'Invalid bootstrap fields.');
  return value as unknown as BootstrapResponse;
}
function validateReadiness(value: unknown): ReadinessResponse {
  if (!record(value) || !string(value.workspaceName) || !platform(value.platformId)
    || !readinessRecord(value.workspace) || !readinessRecord(value.platform) || !readinessRecord(value.provider)
    || !readinessRecord(value.target) || !readinessRecord(value.strict)) invalidResponse('readiness', 'Invalid readiness fields.');
  if ((value.platformId === 'macos' || value.platformId === 'android') && (!arrayOf(value.prerequisites, item => record(item) && string(item.identifier)
    && ['ready','unavailable','error','not_applicable'].includes(String(item.status)) && string(item.message)
    && (item.action == null || string(item.action)) && arrayOf(item.commands, item => string(item) && item.length <= 2000))
    || !record(value.commands) || !readinessRecord(value.commands.caseCreate) || !readinessRecord(value.commands.caseTest)
    || !string(value.checkedAt) || !Number.isFinite(Date.parse(value.checkedAt)))) invalidResponse('readiness', 'Invalid environment diagnostics.');
  if (value.platformId === 'android' && !(value.targetId === null || string(value.targetId))) invalidResponse('readiness', 'Invalid Android device binding.');
  return value as unknown as ReadinessResponse;
}
function validateTargets(value: unknown): TargetsResponse {
  const target = (item: unknown) => record(item) && string(item.id) && string(item.label) && string(item.description)
    && string(item.status) && bool(item.selectable) && bool(item.isDefault) && record(item.metadata);
  if (!record(value) || !platform(value.platform) || !string(value.targetLabel) || !arrayOf(value.targets, target)) invalidResponse('targets', 'Invalid target fields.');
  return value as unknown as TargetsResponse;
}
function validateCases(value: unknown): CasesResponse {
  const caseRecord = (item: unknown) => record(item) && string(item.path) && string(item.id) && string(item.name)
    && (item.platform === null || platform(item.platform)) && nonNegativeInteger(item.commandCount) && bool(item.requiresAiAssertion)
    && string(item.validationStatus) && bool(item.selectable) && arrayOf(item.diagnostics, string);
  if (!record(value) || !platform(value.platform) || !arrayOf(value.cases, caseRecord) || !bool(value.truncated)) invalidResponse('cases', 'Invalid case fields.');
  return value as unknown as CasesResponse;
}
function validateStart(value: unknown): StartRunResponse {
  if (!record(value) || !string(value.requestId) || !value.requestId) invalidResponse('start run', 'Missing requestId.');
  return value as unknown as StartRunResponse;
}
function validateUiSnapshot(value: unknown): UiSnapshotResponse {
  if (!record(value) || !nonNegativeInteger(value.revision) || !nullableString(value.timestamp) || !nullableString(value.stepId)
    || !string(value.mimeType) || !string(value.format) || !string(value.content)) invalidResponse('ui snapshot', 'Invalid UI snapshot fields.');
  return value as unknown as UiSnapshotResponse;
}
function validateStepArtifacts(value: unknown): StepArtifactsResponse {
  const artifact = (item: unknown) => record(item) && ['screenshot', 'ui_snapshot'].includes(String(item.kind))
    && string(item.phase) && nullableString(item.timestamp) && string(item.mimeType)
    && (item.format === undefined || string(item.format)) && (item.contentBase64 === undefined || string(item.contentBase64))
    && (item.content === undefined || string(item.content)) && (item.error === undefined || string(item.error))
    && (item.sizeBytes === undefined || nonNegativeInteger(item.sizeBytes))
    && ['artifactId','captureReason','normalizedContent','availability','unavailableReason'].every(key => item[key] == null || string(item[key]))
    && (item.stepExecutionId == null || string(item.stepExecutionId)) && (item.captureOccurrence == null || nonNegativeInteger(item.captureOccurrence))
    && ['redacted','transformed','displayTransformed'].every(key => item[key] == null || bool(item[key]))
    && ['coverage','compaction'].every(key => item[key] == null || record(item[key]))
    && (item.attemptIndex == null || nonNegativeInteger(item.attemptIndex)) && (item.truncated == null || bool(item.truncated))
    && (string(item.error) || (item.kind === 'screenshot' ? string(item.contentBase64) : string(item.content)));
  if (!record(value) || !bool(value.available) || !string(value.stepId) || !arrayOf(value.artifacts, artifact) || !nullableString(value.message)) invalidResponse('step artifacts', 'Invalid step artifact fields.');
  if (value.comparison !== undefined && value.comparison !== null && !reportDiffValid(value.comparison)) invalidResponse('step artifacts', 'Invalid shared snapshot comparison.');
  return value as unknown as StepArtifactsResponse;
}
function validateReplayFrames(value: unknown): ReplayFramesResponse {
  const frame = (item: unknown) => record(item) && nonNegativeInteger(item.index) && (item.timestamp === null || finiteNumber(item.timestamp)) && string(item.mimeType)
    && (item.contentBase64 === undefined || string(item.contentBase64)) && (item.error === undefined || string(item.error))
    && (item.sizeBytes === undefined || nonNegativeInteger(item.sizeBytes)) && (string(item.contentBase64) || string(item.error));
  if (!record(value) || !bool(value.available) || !arrayOf(value.frames, frame) || !nullableString(value.message)) invalidResponse('replay frames', 'Invalid replay frame fields.');
  return value as unknown as ReplayFramesResponse;
}
function validateReplayVideo(value: unknown): ReplayVideoResponse {
  if (!record(value) || !bool(value.available) || !nullableString(value.videoUrl)
    || (value.mimeType !== undefined && !string(value.mimeType)) || (value.sizeBytes !== undefined && !nonNegativeInteger(value.sizeBytes))) invalidResponse('replay video', 'Invalid replay video fields.');
  return value as unknown as ReplayVideoResponse;
}
function validateSaveYaml(value: unknown): SaveYamlResponse {
  if (!record(value) || !hasOnlyKeys(value, ['savedPath', 'message', 'outcome', 'draft']) || !string(value.savedPath) || !value.savedPath || !string(value.message) || !value.message
    || (value.outcome !== undefined && value.outcome !== 'created' && value.outcome !== 'unchanged') || (value.draft !== undefined && !bool(value.draft))) {
    invalidResponse('save yaml', 'Invalid Save yaml response fields.');
  }
  return value as unknown as SaveYamlResponse;
}
function validateConfig(value: unknown): ConfigResponse {
  if (!record(value) || !hasOnlyKeys(value, ['configured', 'provider']) || !bool(value.configured)) invalidResponse('config', 'Invalid configured state.');
  if (!value.configured) {
    if (value.provider !== null) invalidResponse('config', 'Unconfigured state must have a null provider.');
    return value as unknown as ConfigResponse;
  }
  const provider = value.provider;
  if (!record(provider) || !string(provider.type) || !providerTypes.has(provider.type) || !string(provider.modelName) || !provider.modelName) {
    invalidResponse('config', 'Invalid Provider identity.');
  }
  if (provider.type === 'openai' || provider.type === 'google_gemini') {
    if (!hasOnlyKeys(provider, ['type', 'modelName', 'apiKey']) || !string(provider.apiKey) || !provider.apiKey.trim()) invalidResponse('config', 'Invalid API-key Provider fields.');
  } else if (provider.type === 'azure_openai') {
    if (!hasOnlyKeys(provider, ['type', 'modelName', 'baseUrl', 'apiKey']) || !string(provider.baseUrl) || !provider.baseUrl
      || !string(provider.apiKey) || !provider.apiKey) invalidResponse('config', 'Invalid Azure Provider fields.');
  } else if (!hasOnlyKeys(provider, ['type', 'modelName', 'authenticated']) || provider.authenticated !== true) {
    invalidResponse('config', 'Invalid GitHub Provider fields.');
  }
  return value as unknown as ConfigResponse;
}
function validateOpenAIModels(value: unknown): OpenAIModelsResponse {
  const model = (item: unknown) => record(item) && hasOnlyKeys(item, ['id', 'name']) && string(item.id) && Boolean(item.id.trim()) && string(item.name) && Boolean(item.name.trim());
  if (!record(value) || !hasOnlyKeys(value, ['models']) || !arrayOf(value.models, model)) invalidResponse('Provider models', 'Invalid model-list fields.');
  return value as unknown as OpenAIModelsResponse;
}
function validateDeviceFlow(value: unknown): GitHubDeviceFlowResponse {
  if (!record(value) || !string(value.authRequestId) || !value.authRequestId || !string(value.status) || !deviceFlowStatuses.has(value.status)
    || !(value.message === undefined || string(value.message))) invalidResponse('GitHub device flow', 'Invalid device-flow identity.');
  const optionalMessage = ['message'];
  if (value.status === 'waiting') {
    if (!hasNoUnknownKeys(value, ['authRequestId', 'status', 'verificationUri', 'userCode', 'expiresAt', 'pollIntervalSeconds', ...optionalMessage])
      || !string(value.verificationUri) || !value.verificationUri || !string(value.userCode) || !value.userCode
      || !string(value.expiresAt) || !value.expiresAt || !finiteNumber(value.pollIntervalSeconds) || value.pollIntervalSeconds <= 0) {
      invalidResponse('GitHub device flow', 'Invalid waiting fields.');
    }
  } else if (value.status === 'loading_models') {
    if (!hasNoUnknownKeys(value, ['authRequestId', 'status', 'expiresAt', 'pollIntervalSeconds', ...optionalMessage])
      || !string(value.expiresAt) || !value.expiresAt || !finiteNumber(value.pollIntervalSeconds) || value.pollIntervalSeconds <= 0) {
      invalidResponse('GitHub device flow', 'Invalid model-loading fields.');
    }
  } else if (value.status === 'ready') {
    const model = (item: unknown) => record(item) && hasOnlyKeys(item, ['id', 'name']) && string(item.id) && Boolean(item.id) && string(item.name) && Boolean(item.name);
    if (!hasNoUnknownKeys(value, ['authRequestId', 'status', 'expiresAt', 'models', ...optionalMessage])
      || !string(value.expiresAt) || !value.expiresAt || !arrayOf(value.models, model)) {
      invalidResponse('GitHub device flow', 'Invalid ready model fields.');
    }
  } else if (value.status === 'model_error') {
    if (!hasOnlyKeys(value, ['authRequestId', 'status', 'expiresAt', 'message']) || !string(value.expiresAt) || !value.expiresAt || !string(value.message) || !value.message) {
      invalidResponse('GitHub device flow', 'Invalid model-error fields.');
    }
  } else if (!hasNoUnknownKeys(value, ['authRequestId', 'status', ...optionalMessage])) {
    invalidResponse('GitHub device flow', 'Invalid terminal fields.');
  }
  return value as unknown as GitHubDeviceFlowResponse;
}
function validateConnectionTest(value: unknown): ConnectionTestResponse {
  if (!record(value) || !hasOnlyKeys(value, ['success', 'provider', 'modelName', 'durationMs']) || value.success !== true || !string(value.provider) || !providerTypes.has(value.provider)
    || !string(value.modelName) || !value.modelName || !finiteNumber(value.durationMs) || value.durationMs < 0) {
    invalidResponse('connection test', 'Invalid connection-test fields.');
  }
  return value as unknown as ConnectionTestResponse;
}

function workspaceTarget(value: unknown, platformId: PlatformId): boolean {
  if (!record(value)) return false;
  if (platformId === 'android') return hasOnlyKeys(value, ['appId']) && string(value.appId) && Boolean(value.appId);
  if (platformId === 'web') return hasOnlyKeys(value, ['browserChannel', 'browserExecutablePath'])
    && string(value.browserChannel) && ['chromium', 'chrome', 'chrome-beta', 'chrome-dev', 'chrome-canary', 'msedge', 'msedge-beta', 'msedge-dev', 'msedge-canary'].includes(value.browserChannel)
    && string(value.browserExecutablePath) && Boolean(value.browserExecutablePath);
  if (platformId === 'windows') {
    return hasOnlyKeys(value, ['appPath', 'launchArgs']) || hasOnlyKeys(value, ['appPath', 'windowTitleRe', 'launchArgs'])
      ? string(value.appPath) && Boolean(value.appPath) && string(value.launchArgs)
        && (value.windowTitleRe === undefined || string(value.windowTitleRe))
      : false;
  }
  const keys = Object.keys(value);
  return keys.every((key) => key === 'bundleId' || key === 'appPath')
    && keys.length > 0
    && (value.bundleId === undefined || string(value.bundleId))
    && (value.appPath === undefined || string(value.appPath))
    && Boolean(value.bundleId || value.appPath);
}

function workspacePlatformStatus(value: unknown, summary: boolean): boolean {
  if (!record(value) || !platform(value.platform) || !string(value.configPath) || !string(value.status) || !string(value.message)) return false;
  if (value.status === 'unavailable') {
    return hasOnlyKeys(value, ['platform', 'configPath', 'status', 'message', 'action','diagnosticAvailable','repairAvailable']) && string(value.action)
      && (value.diagnosticAvailable === undefined || (value.platform === 'macos' && bool(value.diagnosticAvailable)))
      && (value.repairAvailable === undefined || (value.platform === 'macos' && bool(value.repairAvailable)));
  }
  if (value.status !== 'available') return false;
  if (!summary) return hasOnlyKeys(value, ['platform', 'configPath', 'status', 'message']);
  return hasOnlyKeys(value, ['platform', 'configPath', 'status', 'message', 'target', 'env', 'revision'])
    && workspaceTarget(value.target, value.platform)
    && arrayOf(value.env, (item) => record(item) && hasOnlyKeys(item, ['name', 'configured']) && string(item.name) && item.configured === true)
    && string(value.revision) && value.revision.startsWith('sha256:');
}

function workspaceStatus(value: unknown, summary: boolean): value is WorkspaceDetail {
  if (!record(value) || !string(value.name) || !string(value.rootPath) || !string(value.status)
    || !['available', 'partial', 'unavailable'].includes(value.status) || !string(value.message)
    || !arrayOf(value.platforms, (item) => workspacePlatformStatus(item, summary))) return false;
  const keys = value.action === undefined
    ? ['name', 'rootPath', 'status', 'message', 'platforms']
    : ['name', 'rootPath', 'status', 'message', 'action', 'platforms'];
  return hasOnlyKeys(value, keys) && (value.action === undefined || string(value.action));
}

function validateWorkspaceList(value: unknown): WorkspaceListResponse {
  if (!record(value) || !arrayOf(value.workspaces, (item) => workspaceStatus(item, false))) invalidResponse('workspaces', 'Invalid workspace registry fields.');
  return value as unknown as WorkspaceListResponse;
}

function validateWorkspaceDetail(value: unknown): WorkspaceDetail {
  if (!workspaceStatus(value, true)) invalidResponse('workspace detail', 'Invalid workspace summary fields.');
  return value;
}

function validateWorkspaceParentDirectoryPicker(value: unknown): WorkspaceParentDirectoryPickerResponse {
  if (!record(value) || !string(value.status)) {
    invalidResponse('workspace parent directory picker', 'Invalid folder selection response.');
  }
  if (value.status === 'cancelled' && hasOnlyKeys(value, ['status'])) {
    return value as { status: 'cancelled' };
  }
  if (value.status === 'selected' && hasOnlyKeys(value, ['status', 'selectedPath', 'isEmpty']) && string(value.selectedPath) && value.selectedPath && typeof value.isEmpty === 'boolean') {
    return value as { status: 'selected'; selectedPath: string; isEmpty: boolean };
  }
  invalidResponse('workspace parent directory picker', 'Invalid folder selection response.');
}

function validateWorkspacePlatformDetail(value: unknown): WorkspacePlatformDetail {
  if (!record(value) || !hasOnlyKeys(value, ['name', 'rootPath', 'configPath', 'platform', 'target', 'env', 'revision'])
    || !string(value.name) || !string(value.rootPath) || !string(value.configPath) || !platform(value.platform)
    || !workspaceTarget(value.target, value.platform)
    || !record(value.env) || !Object.entries(value.env).every(([name, secret]) => Boolean(name) && string(secret))
    || !string(value.revision) || !value.revision.startsWith('sha256:')) {
    invalidResponse('workspace platform detail', 'Invalid workspace platform configuration fields.');
  }
  return value as unknown as WorkspacePlatformDetail;
}

function validateWorkspacePlatformMutation(value: unknown): WorkspacePlatformMutationResponse {
  if (!record(value) || !hasOnlyKeys(value, ['workspace', 'platform']) || !workspaceStatus(value.workspace, true)) {
    invalidResponse('workspace platform mutation', 'Invalid workspace platform mutation fields.');
  }
  return { workspace: value.workspace, platform: validateWorkspacePlatformDetail(value.platform) };
}

function validateWorkspaceEntries(value: unknown): WorkspaceEntriesResponse {
  const entry = (item: unknown) => record(item) && string(item.path) && string(item.name)
    && (item.kind === 'directory' || item.kind === 'file')
    && (item.size === null || nonNegativeInteger(item.size)) && string(item.modifiedTime);
  if (!record(value) || !string(value.path) || !arrayOf(value.entries, entry) || !bool(value.truncated)) {
    invalidResponse('workspace entries', 'Invalid workspace directory fields.');
  }
  return value as unknown as WorkspaceEntriesResponse;
}

function validateWorkspaceFile(value: unknown): WorkspaceFileResponse {
  if (!record(value) || !string(value.path) || !string(value.name) || !string(value.mediaType)
    || (value.presentation !== 'markdown' && value.presentation !== 'code')
    || !nonNegativeInteger(value.size) || !nonNegativeInteger(value.lineCount)
    || !string(value.modifiedTime) || !string(value.content)) {
    invalidResponse('workspace file', 'Invalid workspace file fields.');
  }
  return value as unknown as WorkspaceFileResponse;
}

async function jsonRequest<T>(path: string, validate: (value: unknown) => T, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });
  if (!response.ok) throw await responseError(response);
  const value: unknown = await response.json().catch(() => invalidResponse(path, 'Response body is not JSON.'));
  return validate(value);
}

export function toApiError(error: unknown): ApiErrorBody {
  if (error instanceof ControlPlaneApiError) return error.body;
  if (error instanceof DOMException && error.name === 'AbortError') {
    return { code: 'aborted', message: 'Request cancelled.', action: 'Retry if needed.' };
  }
  return {
    code: 'network_error',
    message: error instanceof Error ? error.message : 'The Control Plane request failed.',
    action: 'Check the local server and retry.',
  };
}

export const controlPlaneClient = {
  history: (workspaceName: string, filters: Record<string, string> = {}, signal?: AbortSignal) => {
    const query = new URLSearchParams({ workspace: workspaceName });
    for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value);
    return jsonRequest(`/history?${query}`, validateHistory, { signal }).then(value => { if (value.workspace !== workspaceName) invalidResponse('history', 'Mismatched Workspace.'); return value; });
  },
  runReport: (workspaceName: string, platformId: PlatformId, runId: string, baselineRunId?: string, signal?: AbortSignal, relatedRunIds: string[] = []) => {
    const query = new URLSearchParams({ workspace: workspaceName });
    if (baselineRunId) query.set('baselineRunId', baselineRunId);
    relatedRunIds.forEach(id => query.append('relatedRunId', id));
    return jsonRequest(`/history/${encodeURIComponent(platformId)}/${encodeURIComponent(runId)}?${query}`, validateRunReport, { signal }).then(value => { if (value.workspace !== workspaceName || value.platform !== platformId || value.run_id !== runId) invalidResponse('report', 'Mismatched requested Run.'); return value; });
  },
  exportReport: (workspaceName: string, platformId: PlatformId, runId: string, format: ReportFormat, baselineRunId?: string, signal?: AbortSignal, relatedRunIds: string[] = []) => jsonRequest(`/history/${encodeURIComponent(platformId)}/${encodeURIComponent(runId)}/exports`, validateReportExport, { method: 'POST', body: JSON.stringify({ workspaceName, format, ...(baselineRunId ? { baselineRunId } : {}), ...(relatedRunIds.length ? { relatedRunIds } : {}) }), signal }).then(value => {
    if (value.run_id !== runId || value.platform !== platformId || value.format !== format) invalidResponse('export', 'Mismatched export identity.');
    for (const file of value.files) {
      const expected = `${API_BASE}/history/${encodeURIComponent(platformId)}/${encodeURIComponent(runId)}/exports/${encodeURIComponent(value.export_id)}/files/${encodeURIComponent(file.file_id)}`;
      const url = new URL(file.download_url, window.location.origin);
      if (!file.download_url.startsWith('/') || url.origin !== window.location.origin || url.pathname !== expected || url.searchParams.getAll('workspace').length !== 1 || url.searchParams.get('workspace') !== workspaceName || [...url.searchParams.keys()].some(key => key !== 'workspace') || url.hash) invalidResponse('export', 'Download mapping escapes requested scope.');
    }
    return value;
  }),
  historyArtifactUrl: (workspaceName: string, platformId: PlatformId, runId: string, artifactId: string) => `${API_BASE}/history/${encodeURIComponent(platformId)}/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}?workspace=${encodeURIComponent(workspaceName)}`,
  historyArtifactImage: async (workspaceName: string, platformId: PlatformId, runId: string, artifactId: string, signal?: AbortSignal) => {
    const response = await fetch(controlPlaneClient.historyArtifactUrl(workspaceName, platformId, runId, artifactId), { signal });
    if (!response.ok) throw await responseError(response);
    const mime = response.headers.get('Content-Type')?.split(';')[0];
    if (!mime || !['image/png','image/jpeg','image/webp'].includes(mime)) invalidResponse('artifact image', 'Unsupported image content type.');
    const length = Number(response.headers.get('Content-Length'));
    if (!Number.isFinite(length) || length <= 0 || length > 16 * 1024 * 1024) invalidResponse('artifact image', 'Image exceeds the display limit.');
    const blob = await response.blob();
    if (!blob.size || blob.size > 16 * 1024 * 1024) invalidResponse('artifact image', 'Image exceeds the display limit.');
    return blob;
  },
  historyArtifactText: async (workspaceName: string, platformId: PlatformId, runId: string, artifactId: string, signal?: AbortSignal) => {
    const response = await fetch(controlPlaneClient.historyArtifactUrl(workspaceName, platformId, runId, artifactId), { signal });
    if (!response.ok) throw await responseError(response);
    const length = Number(response.headers.get('Content-Length'));
    if (!Number.isFinite(length) || length > 512 * 1024) throw new Error('This artifact exceeds the 512 KiB display limit. Download the original artifact to inspect it.');
    return response.text();
  },
  bootstrap: (signal?: AbortSignal) => jsonRequest('/bootstrap', validateBootstrap, { signal }),
  readiness: (workspaceName: string, platform: PlatformId, signal?: AbortSignal, targetId?: string | null) =>
    platform === 'android' ? jsonRequest('/readiness', validateReadiness, { method: 'POST', body: JSON.stringify({workspaceName, platform, targetId: targetId || null}), signal })
      : jsonRequest(`/readiness?workspace=${encodeURIComponent(workspaceName)}&platform=${encodeURIComponent(platform)}`, validateReadiness, { signal }),
  targets: (workspaceName: string, platform: PlatformId, signal?: AbortSignal) =>
    jsonRequest(`/targets?workspace=${encodeURIComponent(workspaceName)}&platform=${encodeURIComponent(platform)}`, validateTargets, { signal }),
  cases: (workspaceName: string, platform: PlatformId, signal?: AbortSignal) =>
    jsonRequest(`/cases?workspace=${encodeURIComponent(workspaceName)}&platform=${encodeURIComponent(platform)}`, validateCases, { signal }),
  startRun: (payload: StartRunPayload) => jsonRequest('/runs', validateStart, { method: 'POST', body: JSON.stringify(payload) }),
  cancelRun: (requestId: string) => jsonRequest(`/runs/${encodeURIComponent(requestId)}/cancel`, (value) => validateRunSnapshot(value, 'cancel run'), { method: 'POST' }),
  runSnapshot: (requestId: string, signal?: AbortSignal) => jsonRequest(`/runs/${encodeURIComponent(requestId)}`, validateRunSnapshot, { signal }),
  streamUrl: (requestId: string, afterSequence: number) =>
    `${API_BASE}/runs/${encodeURIComponent(requestId)}/stream?afterSequence=${afterSequence}`,
  screenUrl: (requestId: string, revision: number) =>
    `${API_BASE}/runs/${encodeURIComponent(requestId)}/screen?revision=${revision}`,
  screen: async (requestId: string, revision: number, signal?: AbortSignal) => {
    const response = await fetch(`${API_BASE}/runs/${encodeURIComponent(requestId)}/screen?revision=${revision}`, { signal });
    if (!response.ok) throw await responseError(response);
    const mimeType = response.headers.get('Content-Type');
    if (!mimeType?.startsWith('image/')) invalidResponse('screen', 'Screenshot content type is not an image.');
    const blob = await response.blob();
    if (!blob.size) invalidResponse('screen', 'Screenshot body is empty.');
    return blob;
  },
  uiSnapshot: (requestId: string, signal?: AbortSignal) =>
    jsonRequest(`/runs/${encodeURIComponent(requestId)}/ui-snapshot`, validateUiSnapshot, { signal }),
  stepArtifacts: (requestId: string, stepId: string, signal?: AbortSignal) =>
    jsonRequest(`/runs/${encodeURIComponent(requestId)}/step-artifacts/${encodeURIComponent(stepId)}`, validateStepArtifacts, { signal }),
  replayFrames: (requestId: string, signal?: AbortSignal) =>
    jsonRequest(`/runs/${encodeURIComponent(requestId)}/replay`, validateReplayFrames, { signal }),
  replayVideo: (requestId: string, signal?: AbortSignal) =>
    jsonRequest(`/runs/${encodeURIComponent(requestId)}/replay-video`, validateReplayVideo, { signal }),
  uploadReplayVideo: (requestId: string, mimeType: string, videoBase64: string, signal?: AbortSignal) =>
    jsonRequest(`/runs/${encodeURIComponent(requestId)}/replay-video`, validateReplayVideo, { method: 'POST', body: JSON.stringify({ mimeType, videoBase64 }), signal }),
  saveYaml: (requestId: string, payload: SaveYamlPayload, signal?: AbortSignal) =>
    jsonRequest(`/runs/${encodeURIComponent(requestId)}/save-yaml`, validateSaveYaml, { method: 'POST', body: JSON.stringify(payload), signal }),
  config: (signal?: AbortSignal) => jsonRequest('/config', validateConfig, { signal }),
  openaiModels: (apiKey: string, signal?: AbortSignal) =>
    jsonRequest('/config/openai/models', validateOpenAIModels, { method: 'POST', body: JSON.stringify({ apiKey }), signal }),
  googleGeminiModels: (apiKey: string, signal?: AbortSignal) =>
    jsonRequest('/config/google-gemini/models', validateOpenAIModels, { method: 'POST', body: JSON.stringify({ apiKey }), signal }),
  saveGoogleGeminiConfig: (payload: GoogleGeminiConfigPayload, signal?: AbortSignal) =>
    jsonRequest('/config/google-gemini', validateConfig, { method: 'PUT', body: JSON.stringify(payload), signal }),
  saveOpenAIConfig: (payload: OpenAIConfigPayload, signal?: AbortSignal) =>
    jsonRequest('/config/openai', validateConfig, { method: 'PUT', body: JSON.stringify(payload), signal }),
  saveAzureConfig: (payload: AzureConfigPayload, signal?: AbortSignal) =>
    jsonRequest('/config/azure', validateConfig, { method: 'PUT', body: JSON.stringify(payload), signal }),
  startGithubDeviceFlow: (signal?: AbortSignal) =>
    jsonRequest('/config/github/device-flow', validateDeviceFlow, { method: 'POST', signal }),
  githubDeviceFlow: (authRequestId: string, signal?: AbortSignal) =>
    jsonRequest(`/config/github/device-flow/${encodeURIComponent(authRequestId)}`, validateDeviceFlow, { signal }),
  retryGithubModels: (authRequestId: string, signal?: AbortSignal) =>
    jsonRequest(`/config/github/device-flow/${encodeURIComponent(authRequestId)}/models`, validateDeviceFlow, { method: 'POST', signal }),
  saveGithubModel: (authRequestId: string, modelName: string, signal?: AbortSignal) =>
    jsonRequest(`/config/github/device-flow/${encodeURIComponent(authRequestId)}`, validateConfig, { method: 'PUT', body: JSON.stringify({ modelName }), signal }),
  cancelGithubDeviceFlow: (authRequestId: string, signal?: AbortSignal) =>
    jsonRequest(`/config/github/device-flow/${encodeURIComponent(authRequestId)}`, validateDeviceFlow, { method: 'DELETE', signal }),
  testConnection: (signal?: AbortSignal) =>
    jsonRequest('/config/test-connection', validateConnectionTest, { method: 'POST', signal }),
  workspaces: (signal?: AbortSignal) => jsonRequest('/workspaces', validateWorkspaceList, { signal }),
  workspace: (name: string, signal?: AbortSignal) =>
    jsonRequest(`/workspaces/${encodeURIComponent(name)}`, validateWorkspaceDetail, { signal }),
  workspacePlatform: (name: string, platformId: PlatformId, signal?: AbortSignal) =>
    jsonRequest(`/workspaces/${encodeURIComponent(name)}/platforms/${encodeURIComponent(platformId)}`, validateWorkspacePlatformDetail, { signal }),
  pickWorkspaceParentDirectory: () =>
    jsonRequest('/workspaces/pick-parent-directory', validateWorkspaceParentDirectoryPicker, { method: 'POST', body: '{}' }),
  createWorkspace: (payload: CreateWorkspacePayload, signal?: AbortSignal) =>
    jsonRequest('/workspaces', validateWorkspaceDetail, { method: 'POST', body: JSON.stringify(payload), signal }),
  addWorkspacePlatform: (name: string, payload: AddWorkspacePlatformPayload, signal?: AbortSignal) =>
    jsonRequest(`/workspaces/${encodeURIComponent(name)}/platforms`, validateWorkspacePlatformMutation, { method: 'POST', body: JSON.stringify(payload), signal }),
  updateWorkspacePlatform: (name: string, platformId: PlatformId, payload: UpdateWorkspacePlatformPayload, signal?: AbortSignal) =>
    jsonRequest(`/workspaces/${encodeURIComponent(name)}/platforms/${encodeURIComponent(platformId)}`, validateWorkspacePlatformMutation, { method: 'PUT', body: JSON.stringify(payload), signal }),
  workspaceEntries: (name: string, path: string, signal?: AbortSignal) =>
    jsonRequest(`/workspaces/${encodeURIComponent(name)}/entries?path=${encodeURIComponent(path)}`, validateWorkspaceEntries, { signal }),
  workspaceFile: (name: string, path: string, signal?: AbortSignal) =>
    jsonRequest(`/workspaces/${encodeURIComponent(name)}/file?path=${encodeURIComponent(path)}`, validateWorkspaceFile, { signal }),
};

export type ControlPlaneClient = typeof controlPlaneClient;
