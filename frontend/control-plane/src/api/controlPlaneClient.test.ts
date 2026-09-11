import { controlPlaneClient, ControlPlaneApiError, validateRunSnapshot } from './controlPlaneClient';

afterEach(() => vi.restoreAllMocks());

it('sends Android device binding in POST body, never in a URL',async()=>{
  const ok={status:'ready',message:'Ready',action:''};
  const payload={workspaceName:'mobile',platformId:'android',targetId:'device-2',workspace:ok,platform:ok,provider:ok,target:ok,strict:ok,prerequisites:[],commands:{caseCreate:ok,caseTest:ok},checkedAt:'2026-09-08T00:00:00Z'};
  const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify(payload),{status:200}));
  expect((await controlPlaneClient.readiness('mobile','android',undefined,'device-2')).targetId).toBe('device-2');
  expect(String(fetch.mock.calls[0][0])).not.toContain('device-2');
  expect(fetch.mock.calls[0][1]?.method).toBe('POST');
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({workspaceName:'mobile',platform:'android',targetId:'device-2'});
  fetch.mockResolvedValue(new Response(JSON.stringify({...payload,targetId:undefined}),{status:200}));
  await expect(controlPlaneClient.readiness('mobile','android')).rejects.toMatchObject({body:{code:'invalid_response'}});
});

it('validates macOS diagnostic records including commands and verdicts',async()=>{
  const ok={status:'ready',message:'Ready',action:''};
  const payload={workspaceName:'mobile',platformId:'macos',workspace:ok,platform:ok,provider:ok,target:ok,strict:ok,prerequisites:[{identifier:'appium_cli',status:'unavailable',message:'Missing',action:'Install',commands:['npm install -g appium']}],commands:{caseCreate:ok,caseTest:ok},checkedAt:'2026-09-07T00:00:00Z'};
  const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(new Response(JSON.stringify(payload),{status:200}));
  expect((await controlPlaneClient.readiness('mobile','macos')).prerequisites?.[0].commands).toEqual(['npm install -g appium']);
  fetch.mockResolvedValue(new Response(JSON.stringify({...payload,commands:undefined}),{status:200}));
  await expect(controlPlaneClient.readiness('mobile','macos')).rejects.toMatchObject({body:{code:'invalid_response'}});
});

it.each([
  ['history', () => controlPlaneClient.history('mobile')],
  ['report', () => controlPlaneClient.runReport('mobile', 'web', 'run-1')],
  ['report export', () => controlPlaneClient.exportReport('mobile', 'web', 'run-1', 'html')],
  ['bootstrap', () => controlPlaneClient.bootstrap()],
  ['readiness', () => controlPlaneClient.readiness('mobile', 'web')],
  ['targets', () => controlPlaneClient.targets('mobile', 'web')],
  ['cases', () => controlPlaneClient.cases('mobile', 'web')],
  ['start', () => controlPlaneClient.startRun({ mode: 'explore', workspaceName: 'mobile', platform: 'web', targetId: 'chrome', goal: 'Verify' })],
  ['cancel', () => controlPlaneClient.cancelRun('request-1')],
  ['snapshot', () => controlPlaneClient.runSnapshot('request-1')],
  ['ui snapshot', () => controlPlaneClient.uiSnapshot('request-1')],
  ['step artifacts', () => controlPlaneClient.stepArtifacts('request-1', 'step-1')],
  ['replay frames', () => controlPlaneClient.replayFrames('request-1')],
  ['replay video', () => controlPlaneClient.replayVideo('request-1')],
  ['replay upload', () => controlPlaneClient.uploadReplayVideo('request-1', 'video/webm', 'encoded')],
  ['config', () => controlPlaneClient.config()],
  ['Azure config save', () => controlPlaneClient.saveAzureConfig({ baseUrl: 'https://example.test', modelName: 'model', apiKey: 'key' })],
  ['GitHub device-flow start', () => controlPlaneClient.startGithubDeviceFlow()],
  ['GitHub device-flow status', () => controlPlaneClient.githubDeviceFlow('auth-1')],
  ['GitHub model retry', () => controlPlaneClient.retryGithubModels('auth-1')],
  ['GitHub model save', () => controlPlaneClient.saveGithubModel('auth-1', 'gpt-5')],
  ['GitHub device-flow cancellation', () => controlPlaneClient.cancelGithubDeviceFlow('auth-1')],
  ['connection test', () => controlPlaneClient.testConnection()],
  ['workspace registry', () => controlPlaneClient.workspaces()],
  ['workspace detail', () => controlPlaneClient.workspace('mobile')],
  ['workspace platform detail', () => controlPlaneClient.workspacePlatform('mobile', 'android')],
  ['workspace parent directory picker', () => controlPlaneClient.pickWorkspaceParentDirectory()],
  ['workspace create', () => controlPlaneClient.createWorkspace({ name: 'mobile', selectedPath: 'C:\\projects', platforms: [{ platform: 'android', target: { appId: 'com.example' }, env: {} }] })],
  ['workspace platform add', () => controlPlaneClient.addWorkspacePlatform('mobile', { platform: 'android', target: { appId: 'com.example' }, env: {} })],
  ['workspace platform update', () => controlPlaneClient.updateWorkspacePlatform('mobile', 'android', { target: { appId: 'com.example' }, env: {}, expectedRevision: 'sha256:old' })],
  ['workspace entries', () => controlPlaneClient.workspaceEntries('mobile', 'cases')],
  ['workspace file', () => controlPlaneClient.workspaceFile('mobile', 'knowledge/project.md')],
])('rejects a malformed successful %s response', async (_name, request) => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({}), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  }));

  await expect(request()).rejects.toMatchObject({
    status: 200,
    body: expect.objectContaining({ code: 'invalid_response' }),
  });
});

it('rejects environment values in the workspace registry projection', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
    workspaces: [{
      name: 'mobile', rootPath: 'C:\\projects\\mobile', status: 'available', message: 'Available.',
      platforms: [{
        platform: 'android', configPath: 'C:\\projects\\mobile\\.fsq\\config\\config.android.yaml',
        status: 'available', message: 'Available.', env: { SECRET: 'must-not-enter-navigation-state' },
      }],
    }],
  }), { status: 200, headers: { 'Content-Type': 'application/json' } }));

  await expect(controlPlaneClient.workspaces()).rejects.toMatchObject({
    body: expect.objectContaining({ code: 'invalid_response' }),
  });
});

it('encodes explicit workspace and platform identity in Devices discovery requests', async () => {
  const ready = { status: 'ready', message: 'Ready.', action: '' };
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
    workspaceName: 'mobile main', platformId: 'web', workspace: ready, platform: ready,
    provider: ready, target: ready, strict: ready,
  }), { status: 200, headers: { 'Content-Type': 'application/json' } }));

  await expect(controlPlaneClient.readiness('mobile main', 'web')).resolves.toMatchObject({
    workspaceName: 'mobile main', platformId: 'web',
  });
  expect(fetch).toHaveBeenCalledWith(
    '/api/control-plane/readiness?workspace=mobile%20main&platform=web',
    expect.objectContaining({ headers: expect.objectContaining({ 'Content-Type': 'application/json' }) }),
  );
});

it('encodes workspace names and file paths in client requests', async () => {
  const workspaceFile = {
    path: 'knowledge/project notes.md', name: 'project notes.md', mediaType: 'text/markdown', presentation: 'markdown',
    size: 4, lineCount: 1, modifiedTime: '2030-01-01T00:00:00Z', content: 'test',
  };
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(workspaceFile), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  }));

  await controlPlaneClient.workspaceFile('mobile main', 'knowledge/project notes.md');

  expect(fetch).toHaveBeenCalledWith(
    '/api/control-plane/workspaces/mobile%20main/file?path=knowledge%2Fproject%20notes.md',
    expect.objectContaining({ headers: expect.objectContaining({ 'Content-Type': 'application/json' }) }),
  );
});

it.each([
  ['GitHub device-flow start', () => controlPlaneClient.startGithubDeviceFlow(), '/api/control-plane/config/github/device-flow', 'POST'],
  ['GitHub model retry', () => controlPlaneClient.retryGithubModels('auth-1'), '/api/control-plane/config/github/device-flow/auth-1/models', 'POST'],
  ['GitHub device-flow cancellation', () => controlPlaneClient.cancelGithubDeviceFlow('auth-1'), '/api/control-plane/config/github/device-flow/auth-1', 'DELETE'],
  ['connection test', () => controlPlaneClient.testConnection(), '/api/control-plane/config/test-connection', 'POST'],
])('sends no request fields for %s', async (_name, request, expectedUrl, expectedMethod) => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
    savedPath: 'run-1.fsq.yaml',
    message: 'Saved YAML.',
  }), { status: 200, headers: { 'Content-Type': 'application/json' } }));

  await request().catch(() => undefined);

  expect(fetch).toHaveBeenCalledWith(
    expectedUrl,
    expect.objectContaining({ method: expectedMethod }),
  );
  expect(fetch.mock.calls[0][1]).not.toHaveProperty('body');
});

it('sends the confirmed Save yaml case name without a suffix', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
    savedPath: 'checkout-flow.fsq.yaml', outcome: 'created', draft: false,
    message: 'Saved YAML to cases/web/checkout-flow.fsq.yaml.',
  }), { status: 200, headers: { 'Content-Type': 'application/json' } }));

  await expect(controlPlaneClient.saveYaml('request-1', { caseName: 'checkout-flow' })).resolves.toEqual({
    savedPath: 'checkout-flow.fsq.yaml', outcome: 'created', draft: false,
    message: 'Saved YAML to cases/web/checkout-flow.fsq.yaml.',
  });
  expect(fetch).toHaveBeenCalledWith(
    '/api/control-plane/runs/request-1/save-yaml',
    expect.objectContaining({ method: 'POST', body: JSON.stringify({ caseName: 'checkout-flow' }) }),
  );
});

it('accepts selected and cancelled workspace directory responses', async () => {
  const fetch = vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'selected', selectedPath: 'C:\\projects', isEmpty: false }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'cancelled' }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }));

  await expect(controlPlaneClient.pickWorkspaceParentDirectory()).resolves.toEqual({ status: 'selected', selectedPath: 'C:\\projects', isEmpty: false });
  await expect(controlPlaneClient.pickWorkspaceParentDirectory()).resolves.toEqual({ status: 'cancelled' });
  expect(fetch).toHaveBeenNthCalledWith(1, '/api/control-plane/workspaces/pick-parent-directory', expect.objectContaining({ method: 'POST', body: '{}' }));
});

it('requires step artifacts to contain readable content or an item error', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
    available: true, stepId: 'step-1', message: null,
    artifacts: [{ kind: 'screenshot', phase: 'before', timestamp: null, mimeType: 'image/png' }],
  }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
  await expect(controlPlaneClient.stepArtifacts('request-1', 'step-1')).rejects.toMatchObject({ body: expect.objectContaining({ code: 'invalid_response' }) });
});

it('loads and saves the strict OpenAI configuration and model-list contracts', async () => {
  const config = { configured: true, provider: { type: 'openai', modelName: 'gpt-5', apiKey: 'local-key' } };
  const fetch = vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify({ models: [{ id: 'gpt-5', name: 'gpt-5' }] })))
    .mockResolvedValueOnce(new Response(JSON.stringify(config)))
    .mockResolvedValueOnce(new Response(JSON.stringify({ success: true, provider: 'openai', modelName: 'gpt-5', durationMs: 15 })));
  await expect(controlPlaneClient.openaiModels('local-key')).resolves.toEqual({ models: [{ id: 'gpt-5', name: 'gpt-5' }] });
  await expect(controlPlaneClient.saveOpenAIConfig({ modelName: 'gpt-5', apiKey: 'local-key' })).resolves.toEqual(config);
  await expect(controlPlaneClient.testConnection()).resolves.toMatchObject({ provider: 'openai' });
  expect(fetch).toHaveBeenNthCalledWith(1, '/api/control-plane/config/openai/models', expect.objectContaining({ method: 'POST', body: JSON.stringify({ apiKey: 'local-key' }) }));
  expect(fetch).toHaveBeenNthCalledWith(2, '/api/control-plane/config/openai', expect.objectContaining({ method: 'PUT', body: JSON.stringify({ modelName: 'gpt-5', apiKey: 'local-key' }) }));
});

it.each([{ models: null }, { models: [{ id: 'gpt-5', name: '' }] }, { models: [{ id: 'gpt-5', name: 'gpt-5', apiKey: 'unexpected' }] }])('rejects invalid OpenAI model-list responses', async payload => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(payload)));
  await expect(controlPlaneClient.openaiModels('local-key')).rejects.toMatchObject({ body: { code: 'invalid_response' } });
});

it('accepts empty OpenAI model discovery', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ models: [] })));
  await expect(controlPlaneClient.openaiModels('local-key')).resolves.toEqual({ models: [] });
});

it('validates Gemini config and discovery with exact fields', async () => {
  const config = { configured: true, provider: { type: 'google_gemini', modelName: 'gemini-3.8-flash', apiKey: 'local-key' } };
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify({ models: [{ id: 'gemini-3.8-flash', name: 'Flash' }] })))
    .mockResolvedValueOnce(new Response(JSON.stringify(config)))
    .mockResolvedValueOnce(new Response(JSON.stringify({ success: true, provider: 'google_gemini', modelName: 'gemini-3.8-flash', durationMs: 12, apiKey: 'must-not-leak' })));
  await expect(controlPlaneClient.googleGeminiModels('local-key')).resolves.toEqual({ models: [{ id: 'gemini-3.8-flash', name: 'Flash' }] });
  await expect(controlPlaneClient.saveGoogleGeminiConfig({ apiKey: 'local-key', modelName: 'gemini-3.8-flash' })).resolves.toEqual(config);
  await expect(controlPlaneClient.testConnection()).rejects.toMatchObject({ body: { code: 'invalid_response' } });
});

it('rejects an OpenAI configuration with a custom endpoint', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ configured: true, provider: { type: 'openai', modelName: 'gpt-5', apiKey: 'local-key', baseUrl: 'https://untrusted.example' } })));
  await expect(controlPlaneClient.config()).rejects.toMatchObject({ body: { code: 'invalid_response' } });
});

it('accepts Config responses without admitting GitHub token fields into the contract', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
    configured: true,
    provider: { type: 'github_copilot', modelName: 'gpt-5.5', authenticated: true },
  }), { status: 200, headers: { 'Content-Type': 'application/json' } }));

  await expect(controlPlaneClient.config()).resolves.toEqual({
    configured: true,
    provider: { type: 'github_copilot', modelName: 'gpt-5.5', authenticated: true },
  });
});

it('rejects a GitHub Config projection containing an unexpected token field', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
    configured: true,
    provider: { type: 'github_copilot', modelName: 'gpt-5.5', authenticated: true, accessToken: 'must-not-enter-state' },
  }), { status: 200, headers: { 'Content-Type': 'application/json' } }));

  await expect(controlPlaneClient.config()).rejects.toMatchObject({
    body: expect.objectContaining({ code: 'invalid_response' }),
  });
});

it('rejects malformed stream snapshots and non-image screen responses', async () => {
  expect(() => validateRunSnapshot({ requestId: 'request-1' }, 'run stream')).toThrowError(ControlPlaneApiError);
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('not an image', {
    status: 200,
    headers: { 'Content-Type': 'text/plain' },
  }));

  await expect(controlPlaneClient.screen('request-1', 1)).rejects.toMatchObject({
    body: expect.objectContaining({ code: 'invalid_response' }),
  });
});

it('rejects rendered history fields with invalid types', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ workspace: 'demo', platforms: ['web'], filters: {}, matched_count: 1, returned_count: 1, truncated: false, warnings: [], runs: [{ run_id: 'r1', platform: 'web', status: 'success', started_at: { unsafe: 'object' }, warnings: [] }] })));
  await expect(controlPlaneClient.history('demo')).rejects.toMatchObject({ body: { code: 'invalid_response' } });
});

it('rejects another Run or format in a successful export response', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ run_id: 'other', platform: 'android', export_id: 'e1', format: 'json', files: [{ file_id: 'report', name: 'report.json', mime_type: 'application/json', size_bytes: 3, download_url: '/api/control-plane/history/android/other/exports/e1/files/report?workspace=another' }], warnings: [] })));
  await expect(controlPlaneClient.exportReport('demo', 'web', 'r1', 'html')).rejects.toMatchObject({ body: { code: 'invalid_response' } });
});

it('accepts unknown historical status and completed tool transport facts', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ workspace: 'demo', platforms: ['web'], filters: {}, matched_count: 1, returned_count: 1, truncated: false, warnings: [], runs: [{ run_id: 'history', platform: 'web', status: null, started_at: null, mode: null, warnings: [] }] })));
  expect((await controlPlaneClient.history('demo')).runs[0].status).toBeNull();
  const report = { schema_version: 'fsq.report/v1', run: { run_id: 'r1', platform: 'web', gate: { status: 'incomplete', reasons: [] } }, source: {}, execution: { outcome: 'inconclusive' }, verification: { status: 'unknown' }, evidence: { status: 'unavailable' }, processing: {}, steps: [], artifacts: [], metrics: {}, lineage: {}, comparison: {}, warnings: [], logs: [], tool_calls: [{ status: 'completed', tool_name: 'read_file', tool_origin: 'agent_tool' }] };
  vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({ workspace: 'demo', run_id: 'r1', platform: 'web', report, warnings: [] })));
  expect((await controlPlaneClient.runReport('demo', 'web', 'r1')).report.tool_calls[0].status).toBe('completed');
});

it('accepts backend recording warning with nullable live timestamp', () => {
  const value={requestId:'r',runId:'run',workspaceName:'w',platform:'web',targetId:'chrome',mode:'explore',status:'failed',source:{goal:'g'},startedAt:'2026-09-08T00:00:00Z',completedAt:null,cancelRequested:false,events:[{sequence:1,time:null,phase:'finalizing',label:'Dynamic recording',status:'failed',message:'Recording unavailable'}],activeStep:null,result:null,summary:'failed',screenshotRevision:0,uiSnapshotRevision:0,evidenceAvailable:false,reportAvailable:false,terminal:true};
  expect(validateRunSnapshot(value).events[0].time).toBeNull();
});
