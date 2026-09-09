export type PlatformId = 'android' | 'web' | 'windows' | 'macos';
export type RunMode = 'explore' | 'strict';
export type TaskStatus =
  | 'preparing'
  | 'running'
  | 'finalizing'
  | 'success'
  | 'failed'
  | 'inconclusive'
  | 'cancelled'
  | 'error';
export type EvidenceTab = 'screen' | 'ui-tree' | 'logs';
export type LoadState = 'idle' | 'loading' | 'ready' | 'error';

export interface ApiErrorBody {
  code: string;
  message: string;
  action: string;
  details?: unknown;
}

export interface PlatformOption { id: PlatformId; label: string }
export interface ActiveTaskSummary {
  requestId: string;
  runId: string | null;
  workspaceName: string;
  platform: PlatformId;
  targetId: string;
  mode: RunMode;
  status: TaskStatus;
}
export interface BootstrapResponse {
  apiVersion: string;
  platforms: PlatformOption[];
  busy: boolean;
  activeTask: ActiveTaskSummary | null;
}

export type ReadinessStatus = 'ready' | 'unavailable' | 'error';
export interface ReadinessRecord { status: ReadinessStatus; message: string; action: string }
export interface PrerequisiteRecord { identifier: string; code?: string | null; status: ReadinessStatus | 'not_applicable'; message: string; action?: string | null; commands: string[] }
export interface ReadinessResponse {
  workspaceName: string;
  platformId: PlatformId;
  workspace: ReadinessRecord;
  platform: ReadinessRecord;
  provider: ReadinessRecord;
  target: ReadinessRecord;
  strict: ReadinessRecord;
  prerequisites?: PrerequisiteRecord[];
  commands?: {caseCreate: ReadinessRecord; caseTest: ReadinessRecord};
  checkedAt?: string;
  targetId?: string | null;
}

export interface TargetRecord {
  id: string;
  label: string;
  description: string;
  status: string;
  selectable: boolean;
  isDefault: boolean;
  metadata: Record<string, unknown>;
}
export interface TargetsResponse { platform: PlatformId; targetLabel: string; targets: TargetRecord[] }

export interface CaseRecord {
  path: string;
  id: string;
  name: string;
  platform: PlatformId | null;
  commandCount: number;
  requiresAiAssertion: boolean;
  validationStatus: 'validated' | 'invalid' | string;
  selectable: boolean;
  diagnostics: string[];
}
export interface CasesResponse { platform: PlatformId; cases: CaseRecord[]; truncated: boolean }

export interface TimelineEvent {
  sequence: number;
  time?: string;
  phase?: string;
  stepId?: string;
  label?: string;
  tool?: string | null;
  status?: string;
  durationMs?: number | null;
  message?: string;
  level?: string;
  payload?: unknown;
  toolCallId?: string;
  toolArguments?: unknown;
  toolOutputPreview?: unknown;
}
export interface StrictCaseStep {
  stepId: string;
  index: number;
  authoredActionName: string;
  actionName: string;
  kind: string;
  status?: string;
  durationMs?: number | null;
  failureCategory?: string | null;
  message?: string | null;
}
export interface RunSnapshot extends ActiveTaskSummary {
  suggestedCaseName?: string | null;
  recordingDraft?: boolean | null;
  source: { goal?: string; casePath?: string; caseContent?: string; caseSteps?: StrictCaseStep[] };
  startedAt: string;
  completedAt: string | null;
  cancelRequested: boolean;
  events: TimelineEvent[];
  activeStep: { stepId: string; label?: string } | null;
  result: Record<string, unknown> | null;
  summary: string;
  screenshotRevision: number;
  uiSnapshotRevision: number;
  evidenceAvailable: boolean;
  reportAvailable: boolean;
  terminal: boolean;
}
export type StartRunPayload =
  | { mode: 'explore'; workspaceName: string; platform: PlatformId; targetId: string; goal: string }
  | { mode: 'strict'; workspaceName: string; platform: PlatformId; targetId: string; casePath: string };
export interface StartRunResponse { requestId: string }
export interface UiSnapshotResponse {
  revision: number;
  timestamp: string | null;
  stepId: string | null;
  mimeType: string;
  format: string;
  content: string;
}
export interface StepArtifact {
  kind: 'screenshot' | 'ui_snapshot';
  phase: string;
  timestamp: string | null;
  mimeType: string;
  format?: string;
  contentBase64?: string;
  content?: string;
  error?: string;
  sizeBytes?: number;
}
export interface StepArtifactsResponse {
  available: boolean;
  stepId: string;
  artifacts: StepArtifact[];
  message: string | null;
}
export interface ReplayFrame {
  index: number;
  timestamp: number | null;
  mimeType: string;
  contentBase64?: string;
  error?: string;
  sizeBytes?: number;
}
export interface ReplayFramesResponse { available: boolean; frames: ReplayFrame[]; message: string | null }
export interface ReplayVideoResponse {
  available: boolean;
  videoUrl: string | null;
  mimeType?: string;
  sizeBytes?: number;
}
export interface SaveYamlResponse {
  outcome?: 'created' | 'unchanged';
  draft?: boolean;
  savedPath: string;
  message: string;
}
export interface SaveYamlPayload { caseName: string }

export interface OpenAIConfigPayload { modelName: string; apiKey: string }
export interface OpenAIProviderConfig extends OpenAIConfigPayload { type: 'openai' }
export interface OpenAIModelsResponse { models: { id: string; name: string }[] }
export interface GoogleGeminiConfigPayload { modelName: string; apiKey: string }
export interface GoogleGeminiProviderConfig extends GoogleGeminiConfigPayload { type: 'google_gemini' }
export interface GoogleGeminiModelsResponse { models: { id: string; name: string }[] }
export interface AzureProviderConfig {
  type: 'azure_openai';
  modelName: string;
  baseUrl: string;
  apiKey: string;
}
export interface GitHubProviderConfig {
  type: 'github_copilot';
  modelName: string;
  authenticated: true;
}
export type ProviderConfig = OpenAIProviderConfig | AzureProviderConfig | GoogleGeminiProviderConfig | GitHubProviderConfig;
export type ConfigResponse =
  | { configured: false; provider: null }
  | { configured: true; provider: ProviderConfig };
export interface AzureConfigPayload { baseUrl: string; modelName: string; apiKey: string }
export interface GitHubCopilotModel { id: string; name: string }
export type GitHubDeviceFlowResponse =
  | { authRequestId: string; status: 'waiting'; verificationUri: string; userCode: string; expiresAt: string; pollIntervalSeconds: number; message?: string }
  | { authRequestId: string; status: 'loading_models'; expiresAt: string; pollIntervalSeconds: number; message?: string }
  | { authRequestId: string; status: 'ready'; expiresAt: string; models: GitHubCopilotModel[]; message?: string }
  | { authRequestId: string; status: 'model_error'; expiresAt: string; message: string }
  | { authRequestId: string; status: 'success' | 'failed' | 'expired' | 'cancelled'; message?: string };
export interface ConnectionTestResponse {
  success: true;
  provider: 'openai' | 'azure_openai' | 'google_gemini' | 'github_copilot';
  modelName: string;
  durationMs: number;
}

export interface AndroidWorkspaceTarget { appId: string }
export type WebBrowserChannel = 'chromium' | 'chrome' | 'chrome-beta' | 'chrome-dev' | 'chrome-canary' | 'msedge' | 'msedge-beta' | 'msedge-dev' | 'msedge-canary';
export interface WebWorkspaceTarget { browserChannel: WebBrowserChannel; browserExecutablePath: string }
export interface WebWorkspaceTargetInput { browserChannel: WebBrowserChannel; browserExecutablePath?: string }
export interface WindowsWorkspaceTarget { appPath: string; windowTitleRe?: string; launchArgs: string }
export interface MacOSWorkspaceTarget { bundleId?: string; appPath?: string }
export type WorkspaceTarget = AndroidWorkspaceTarget | WebWorkspaceTarget | WindowsWorkspaceTarget | MacOSWorkspaceTarget;
export type WorkspaceTargetInput = AndroidWorkspaceTarget | WebWorkspaceTargetInput | WindowsWorkspaceTarget | MacOSWorkspaceTarget;

interface WorkspaceStatusBase {
  name: string;
  rootPath: string;
  status: 'available' | 'partial' | 'unavailable';
  message: string;
  action?: string;
}
interface WorkspacePlatformStatusBase {
  platform: PlatformId;
  configPath: string;
  status: 'available' | 'unavailable';
  message: string;
  action?: string;
  diagnosticAvailable?: boolean;
  repairAvailable?: boolean;
}
export interface WorkspaceRegistryEntry extends WorkspaceStatusBase {
  platforms: WorkspacePlatformStatusBase[];
}
export interface WorkspaceListResponse { workspaces: WorkspaceRegistryEntry[] }

export interface WorkspacePlatformSummary extends WorkspacePlatformStatusBase {
  target?: WorkspaceTarget;
  env?: { name: string; configured: true }[];
  revision?: string;
}
export interface WorkspaceDetail extends WorkspaceStatusBase {
  platforms: WorkspacePlatformSummary[];
}
export interface WorkspacePlatformDetail {
  name: string;
  rootPath: string;
  configPath: string;
  platform: PlatformId;
  target: WorkspaceTarget;
  env: Record<string, string>;
  revision: string;
}
export interface CreateWorkspacePayload {
  name: string;
  selectedPath: string;
  platforms: { platform: PlatformId; target: WorkspaceTargetInput; env: Record<string, string> }[];
}
export type WorkspaceParentDirectoryPickerResponse =
  | { status: 'selected'; selectedPath: string; isEmpty: boolean }
  | { status: 'cancelled' };
export interface AddWorkspacePlatformPayload {
  platform: PlatformId;
  target: WorkspaceTargetInput;
  env: Record<string, string>;
}
export interface UpdateWorkspacePlatformPayload {
  target: WorkspaceTargetInput;
  env: Record<string, string>;
  expectedRevision: string;
}
export interface WorkspacePlatformMutationResponse {
  workspace: WorkspaceDetail;
  platform: WorkspacePlatformDetail;
}
export interface WorkspaceEntry {
  path: string;
  name: string;
  kind: 'directory' | 'file';
  size: number | null;
  modifiedTime: string;
}
export interface WorkspaceEntriesResponse {
  path: string;
  entries: WorkspaceEntry[];
  truncated: boolean;
}
export interface WorkspaceFileResponse {
  path: string;
  name: string;
  mediaType: string;
  presentation: 'markdown' | 'code';
  size: number;
  lineCount: number;
  modifiedTime: string;
  content: string;
}

export interface RequestResource<T> {
  state: LoadState;
  data: T | null;
  error: ApiErrorBody | null;
}
