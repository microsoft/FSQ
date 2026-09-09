import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ControlPlaneApiError, type ControlPlaneClient } from '../../api/controlPlaneClient';
import type { ConfigResponse, GitHubDeviceFlowResponse } from '../../api/types';
import { ConfigPage } from './ConfigPage';

const unconfigured: ConfigResponse = { configured: false, provider: null };
const azure: ConfigResponse = {
  configured: true,
  provider: { type: 'azure_openai', baseUrl: 'https://example.test/openai/v1/', modelName: 'gpt-5.4', apiKey: 'saved-key' },
};
const github: ConfigResponse = {
  configured: true,
  provider: { type: 'github_copilot', modelName: 'gpt-5.5', authenticated: true },
};
const waiting: GitHubDeviceFlowResponse = {
  authRequestId: 'auth-1', verificationUri: 'https://github.com/login/device', userCode: 'ABCD-EFGH',
  expiresAt: '2030-01-01T00:00:00Z', pollIntervalSeconds: 10, status: 'waiting',
};
const ready: GitHubDeviceFlowResponse = {
  authRequestId: 'auth-1', expiresAt: '2030-01-01T00:10:00Z', status: 'ready',
  models: [{ id: 'gpt-5', name: 'GPT 5' }, { id: 'gpt-5.5', name: 'GPT 5.5' }],
};
const modelError: GitHubDeviceFlowResponse = {
  authRequestId: 'auth-1', expiresAt: '2030-01-01T00:10:00Z', status: 'model_error', message: 'Model discovery failed.',
};

function client(config: ConfigResponse = unconfigured, overrides: Partial<ControlPlaneClient> = {}) {
  return {
    config: vi.fn().mockResolvedValue(config),
    saveAzureConfig: vi.fn().mockResolvedValue(azure),
    startGithubDeviceFlow: vi.fn().mockResolvedValue(waiting),
    githubDeviceFlow: vi.fn().mockResolvedValue(waiting),
    retryGithubModels: vi.fn().mockResolvedValue(ready),
    saveGithubModel: vi.fn().mockResolvedValue(github),
    cancelGithubDeviceFlow: vi.fn().mockResolvedValue({ ...waiting, status: 'cancelled' }),
    testConnection: vi.fn().mockResolvedValue({ success: true, provider: 'github_copilot', modelName: 'gpt-5.5', durationMs: 125 }),
    ...overrides,
  } as unknown as ControlPlaneClient;
}

const openai: ConfigResponse = { configured: true, provider: { type: 'openai', modelName: 'gpt-5', apiKey: 'candidate-key' } };
const openaiModels = { models: [{ id: 'gpt-5', name: 'gpt-5' }, { id: 'gpt-5.4', name: 'gpt-5.4' }] };

it('configures Gemini using explicit discovery and tests the saved provider', async () => {
  const saved: ConfigResponse = { configured: true, provider: { type: 'google_gemini', modelName: 'gemini-3.8-flash', apiKey: 'google-key' } };
  const api = client(unconfigured, {
    googleGeminiModels: vi.fn().mockResolvedValue({ models: [{ id: 'gemini-3.8-flash', name: 'Gemini Flash' }] }),
    saveGoogleGeminiConfig: vi.fn().mockResolvedValue(saved),
    testConnection: vi.fn().mockResolvedValue({ success: true, provider: 'google_gemini', modelName: 'gemini-3.8-flash', durationMs: 12 }),
  });
  const user = userEvent.setup();
  render(<ConfigPage client={api} />);
  await user.click(await screen.findByRole('button', { name: 'Add configuration' }));
  await user.click(screen.getByRole('button', { name: /Google Gemini/ }));
  expect(screen.getByRole('heading', { name: 'Google Gemini configuration' })).toBeVisible();
  expect(screen.queryByLabelText('Base URL')).not.toBeInTheDocument();
  await user.type(screen.getByLabelText('API key'), 'google-key');
  await user.click(screen.getByRole('button', { name: 'Load models' }));
  const select = await screen.findByRole('combobox', { name: 'Model' });
  expect(select).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
  await user.selectOptions(select, 'gemini-3.8-flash');
  await user.click(screen.getByRole('button', { name: 'Save changes' }));
  expect(api.saveGoogleGeminiConfig).toHaveBeenCalledWith({ apiKey: 'google-key', modelName: 'gemini-3.8-flash' }, expect.any(AbortSignal));
  const test = await screen.findByRole('button', { name: 'Test connection' });
  await user.click(test);
  expect(await screen.findByRole('dialog')).toHaveTextContent('Google Gemini');
  await user.click(screen.getByRole('button', { name: 'Done' }));
  await waitFor(() => expect(test).toHaveFocus());
});

it('does not borrow an OpenAI key when changing to Gemini and invalidates discovered models', async () => {
  const api = client(openai, { googleGeminiModels: vi.fn().mockResolvedValue({ models: [{ id: 'gemini-3.8-flash', name: 'Flash' }] }) });
  const user = userEvent.setup();
  render(<ConfigPage client={api} />);
  await user.click(await screen.findByRole('button', { name: 'Change provider' }));
  await user.click(screen.getByRole('button', { name: /Google Gemini/ }));
  expect(screen.getByLabelText('API key')).toHaveValue('');
  await user.type(screen.getByLabelText('API key'), 'google-key');
  await user.click(screen.getByRole('button', { name: 'Load models' }));
  await user.selectOptions(await screen.findByRole('combobox', { name: 'Model' }), 'gemini-3.8-flash');
  await user.type(screen.getByLabelText('API key'), '-changed');
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
});

it('preserves Gemini drafts after a delayed unknown save and hydrates a later successful save', async () => {
  const saved: ConfigResponse = { configured: true, provider: { type: 'google_gemini', modelName: 'gemini-3.8-flash', apiKey: 'old-key' } };
  const updated: ConfigResponse = { configured: true, provider: { type: 'google_gemini', modelName: 'gemini-3.7-flash', apiKey: 'new-key' } };
  let fail!: (error: Error) => void;
  let readback!: (value: ConfigResponse) => void;
  const api = client(saved, {
    config: vi.fn().mockResolvedValueOnce(saved).mockReturnValueOnce(new Promise(resolve => { readback = resolve; })),
    googleGeminiModels: vi.fn().mockResolvedValue({ models: [{ id: 'gemini-3.7-flash', name: 'Flash' }] }),
    saveGoogleGeminiConfig: vi.fn().mockReturnValueOnce(new Promise((_resolve, reject) => { fail = reject; })).mockResolvedValueOnce(updated),
  });
  const user = userEvent.setup();
  render(<ConfigPage client={api} />);
  const key = await screen.findByLabelText('API key');
  await user.clear(key);
  await user.type(key, ' new-key ');
  await user.click(screen.getByRole('button', { name: 'Load models' }));
  await user.selectOptions(await screen.findByRole('combobox', { name: 'Model' }), 'gemini-3.7-flash');
  await user.click(screen.getByRole('button', { name: 'Save changes' }));
  expect(screen.getByRole('button', { name: 'Change provider' })).toBeDisabled();
  fail(new Error('lost response'));
  await screen.findByText('Reading current configuration...');
  expect(key).toBeDisabled();
  readback(saved);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled());
  expect(key).toHaveValue(' new-key ');
  expect(api.saveGoogleGeminiConfig).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(key).toHaveValue('new-key'));
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Test connection' })).toBeEnabled();
});

it('keeps Gemini discovery errors unsaveable without a partial list', async () => {
  const saved: ConfigResponse = { configured: true, provider: { type: 'google_gemini', modelName: 'gemini-3.8-flash', apiKey: 'key' } };
  const api = client(saved, { googleGeminiModels: vi.fn().mockRejectedValue(new ControlPlaneApiError(502, { code: 'provider_response_invalid', message: 'Discovery is incomplete.', action: 'Retry models.' })) });
  const user = userEvent.setup();
  render(<ConfigPage client={api} />);
  await user.click(await screen.findByRole('button', { name: 'Load models' }));
  expect(await screen.findByText('Discovery is incomplete.')).toBeVisible();
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
});

it('configures OpenAI through explicit model discovery and tests only saved configuration', async () => {
  const api = client(unconfigured, { openaiModels: vi.fn().mockResolvedValue(openaiModels), saveOpenAIConfig: vi.fn().mockResolvedValue(openai), testConnection: vi.fn().mockResolvedValue({ success: true, provider: 'openai', modelName: 'gpt-5', durationMs: 35 }) });
  const user = userEvent.setup();
  render(<ConfigPage client={api} />);
  await user.click(await screen.findByRole('button', { name: 'Add configuration' }));
  const choice = screen.getByRole('button', { name: /^OpenAI / });
  expect(choice).toHaveFocus();
  await user.click(choice);
  expect(screen.queryByLabelText('Base URL')).not.toBeInTheDocument();
  await user.type(screen.getByLabelText('API key'), 'candidate-key');
  await user.click(screen.getByRole('button', { name: 'Load models' }));
  const model = await screen.findByRole('combobox', { name: 'Model' });
  expect(model).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
  await user.selectOptions(model, 'gpt-5');
  await user.click(screen.getByRole('button', { name: 'Save changes' }));
  expect(api.saveOpenAIConfig).toHaveBeenCalledWith({ modelName: 'gpt-5', apiKey: 'candidate-key' }, expect.any(AbortSignal));
  const test = await screen.findByRole('button', { name: 'Test connection' });
  await user.click(test);
  expect(await screen.findByRole('dialog')).toHaveTextContent('OpenAI');
  await user.click(screen.getByRole('button', { name: 'Done' }));
  await waitFor(() => expect(test).toHaveFocus());
});

it('invalidates OpenAI model selection on key edits and supports empty/error discovery retry', async () => {
  const api = client(openai, { openaiModels: vi.fn().mockResolvedValueOnce(openaiModels).mockResolvedValueOnce({ models: [] }).mockRejectedValueOnce(new ControlPlaneApiError(401, { code: 'provider_authorization_failed', message: 'Key rejected.', action: 'Replace key.' })).mockResolvedValueOnce(openaiModels) });
  const user = userEvent.setup();
  render(<ConfigPage client={api} />);
  await user.click(await screen.findByRole('button', { name: 'Load models' }));
  expect(await screen.findByRole('combobox', { name: 'Model' })).toHaveValue('gpt-5');
  await user.type(screen.getByLabelText('API key'), '-new');
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Test connection' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Load models' }));
  expect(await screen.findByText('No eligible models are available.')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Retry models' }));
  expect(await screen.findByText('Key rejected.')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Retry models' }));
  expect(await screen.findByRole('combobox', { name: 'Model' })).toHaveValue('');
});

it('masks a revealed OpenAI key again when cancelling the saved form', async () => {
  const user = userEvent.setup();
  render(<ConfigPage client={client(openai)} />);
  await user.click(await screen.findByRole('button', { name: 'Show API key' }));
  expect(screen.getByLabelText('API key')).toHaveAttribute('type', 'text');
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(screen.getByLabelText('API key')).toHaveAttribute('type', 'password');
});

it('locks OpenAI saving and preserves the candidate after an unknown result is reconciled', async () => {
  let fail!: (reason: Error) => void;
  const pending = new Promise<ConfigResponse>((_resolve, reject) => { fail = reject; });
  const api = client(openai, { config: vi.fn().mockResolvedValue(openai), openaiModels: vi.fn().mockResolvedValue(openaiModels), saveOpenAIConfig: vi.fn().mockReturnValueOnce(pending).mockRejectedValueOnce(new ControlPlaneApiError(400, { code: 'provider_model_not_offered', message: 'Selected model is no longer offered.', action: 'Reload models.' })) });
  const busy = vi.fn();
  const user = userEvent.setup();
  render(<ConfigPage client={api} onSavePendingChange={busy} />);
  await user.click(await screen.findByRole('button', { name: 'Load models' }));
  await user.selectOptions(await screen.findByRole('combobox', { name: 'Model' }), 'gpt-5.4');
  await user.click(screen.getByRole('button', { name: 'Save changes' }));
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Change provider' })).toBeDisabled();
  expect(busy).toHaveBeenLastCalledWith(true);
  fail(new Error('connection lost'));
  expect(await screen.findByText('The save result could not be confirmed.')).toBeVisible();
  expect(screen.getByRole('combobox', { name: 'Model' })).toHaveValue('gpt-5.4');
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled();
  expect(api.saveOpenAIConfig).toHaveBeenCalledTimes(1);
  expect(busy).toHaveBeenLastCalledWith(false);
  await user.click(screen.getByRole('button', { name: 'Save changes' }));
  expect(await screen.findByText('Selected model is no longer offered.')).toBeVisible();
  expect(screen.getByRole('combobox', { name: 'Model' })).toHaveValue('gpt-5.4');
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled();
});

it('blocks Azure edits during failed reconciliation and applies a later successful normalized Azure save', async () => {
  const normalizedAzure: ConfigResponse = { configured: true, provider: { type: 'azure_openai', baseUrl: 'https://demo.openai.azure.com/openai/v1/', modelName: 'deployment', apiKey: 'azure-key' } };
  const api = client(openai, {
    config: vi.fn().mockResolvedValueOnce(openai).mockResolvedValueOnce(openai).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(openai),
    openaiModels: vi.fn().mockResolvedValue(openaiModels),
    saveOpenAIConfig: vi.fn().mockRejectedValue(new Error('lost response')),
    saveAzureConfig: vi.fn().mockResolvedValue(normalizedAzure),
  });
  const dirty = vi.fn();
  const user = userEvent.setup();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  render(<ConfigPage client={api} onDirtyChange={dirty} />);
  await user.click(await screen.findByRole('button', { name: 'Load models' }));
  await user.selectOptions(await screen.findByRole('combobox', { name: 'Model' }), 'gpt-5.4');
  await user.click(screen.getByRole('button', { name: 'Save changes' }));
  await screen.findByText('The save result could not be confirmed.');
  await user.click(screen.getByRole('button', { name: 'Change provider' }));
  await user.click(screen.getByRole('button', { name: /Azure OpenAI/ }));
  await user.type(screen.getByLabelText('Base URL'), 'https://demo.openai.azure.com');
  await user.type(screen.getByLabelText('Model name'), 'deployment');
  await user.type(screen.getByLabelText('API key'), 'azure-key');
  await user.click(screen.getByRole('button', { name: 'Reload configuration' }));
  await screen.findByText('Current configuration is unavailable. Changes remain blocked.');
  expect(screen.getByLabelText('API key')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Change provider' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Save changes' }));
  expect(api.saveAzureConfig).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Reload configuration' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled());
  await user.click(screen.getByRole('button', { name: 'Save changes' }));
  await waitFor(() => expect(screen.getByLabelText('Base URL')).toHaveValue('https://demo.openai.azure.com/openai/v1/'));
  expect(screen.getByLabelText('API key')).toHaveAttribute('type', 'password');
  expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Test connection' })).toBeEnabled();
  expect(dirty).toHaveBeenLastCalledWith(false);
});

afterEach(() => vi.restoreAllMocks());

it('adds Azure configuration, masks its populated key, and saves the complete draft', async () => {
  const api = client();
  const user = userEvent.setup();
  render(<ConfigPage client={api} />);
  await user.click(await screen.findByRole('button', { name: 'Add configuration' }));
  await user.click(screen.getByRole('button', { name: /Azure OpenAI/ }));

  const key = screen.getByLabelText('API key');
  expect(key).toHaveAttribute('type', 'password');
  await user.type(screen.getByLabelText('Base URL'), 'https://example.test');
  await user.type(screen.getByLabelText('Model name'), 'gpt-5.4');
  await user.type(key, 'saved-key');
  await user.click(screen.getByRole('button', { name: 'Show API key' }));
  expect(key).toHaveAttribute('type', 'text');
  await user.click(screen.getByRole('button', { name: 'Save changes' }));

  expect(api.saveAzureConfig).toHaveBeenCalledWith({
    baseUrl: 'https://example.test', modelName: 'gpt-5.4', apiKey: 'saved-key',
  }, expect.any(AbortSignal));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Test connection' })).toBeEnabled());
});

it('traps provider-dialog focus and restores it to the invoking action on Escape', async () => {
  const user = userEvent.setup();
  render(<ConfigPage client={client()} />);
  const add = await screen.findByRole('button', { name: 'Add configuration' });
  await user.click(add);
  const openaiChoice = screen.getByRole('button', { name: /^OpenAI / });
  const cancel = screen.getByRole('button', { name: 'Cancel' });
  expect(openaiChoice).toHaveFocus();

  await user.keyboard('{Shift>}{Tab}{/Shift}');
  expect(cancel).toHaveFocus();
  await user.keyboard('{Escape}');

  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  await waitFor(() => expect(add).toHaveFocus());
});

it('keeps a dirty Azure draft until discard is confirmed and disables saved-only testing', async () => {
  const api = client(azure);
  const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
  const user = userEvent.setup();
  render(<ConfigPage client={api} />);
  const model = await screen.findByLabelText('Model name');
  await user.clear(model);
  await user.type(model, 'draft-model');
  expect(screen.getByRole('button', { name: 'Test connection' })).toBeDisabled();

  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(model).toHaveValue('draft-model');
  await user.click(screen.getByRole('button', { name: 'Cancel' }));

  expect(confirm).toHaveBeenCalledTimes(2);
  expect(model).toHaveValue('gpt-5.4');
  expect(screen.getByRole('button', { name: 'Test connection' })).toBeEnabled();
});

it('requests GitHub authentication immediately and cancels the waiting flow', async () => {
  const api = client();
  const user = userEvent.setup();
  render(<ConfigPage client={api} />);
  await user.click(await screen.findByRole('button', { name: 'Add configuration' }));
  await user.click(screen.getByRole('button', { name: /GitHub Copilot GPT/ }));

  expect(api.startGithubDeviceFlow).toHaveBeenCalledWith(expect.any(AbortSignal));
  expect(await screen.findByText('ABCD-EFGH')).toBeVisible();
  const verification = screen.getByRole('link', { name: 'Open GitHub verification' });
  expect(verification).toHaveAttribute('target', '_blank');
  await waitFor(() => expect(verification).toHaveFocus());
  await user.click(screen.getByRole('button', { name: 'Cancel authentication' }));

  await waitFor(() => expect(api.cancelGithubDeviceFlow).toHaveBeenCalled());
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
});

it('requires an explicit discovered model selection before saving GitHub', async () => {
  const api = client(unconfigured, { startGithubDeviceFlow: vi.fn().mockResolvedValue(ready) });
  const user = userEvent.setup();
  render(<ConfigPage client={api} />);
  await user.click(await screen.findByRole('button', { name: 'Add configuration' }));
  await user.click(screen.getByRole('button', { name: /GitHub Copilot GPT/ }));

  const model = await screen.findByRole('combobox', { name: 'Model' });
  expect(model).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  await user.selectOptions(model, 'gpt-5.5');
  await user.click(screen.getByRole('button', { name: 'Save' }));

  expect(api.saveGithubModel).toHaveBeenCalledWith('auth-1', 'gpt-5.5', expect.any(AbortSignal));
  expect(await screen.findByText('GitHub Copilot GPT authenticated')).toBeVisible();
});

it('retries model discovery without starting another authorization', async () => {
  const api = client(unconfigured, {
    startGithubDeviceFlow: vi.fn().mockResolvedValue(modelError),
    retryGithubModels: vi.fn().mockResolvedValue(ready),
  });
  const user = userEvent.setup();
  render(<ConfigPage client={api} />);
  await user.click(await screen.findByRole('button', { name: 'Add configuration' }));
  await user.click(screen.getByRole('button', { name: /GitHub Copilot GPT/ }));
  await user.click(await screen.findByRole('button', { name: 'Retry models' }));

  expect(api.startGithubDeviceFlow).toHaveBeenCalledTimes(1);
  expect(api.retryGithubModels).toHaveBeenCalledWith('auth-1', expect.any(AbortSignal));
  expect(await screen.findByRole('combobox', { name: 'Model' })).toHaveValue('');
});

it('preserves the selected model when GitHub save fails', async () => {
  const api = client(unconfigured, {
    startGithubDeviceFlow: vi.fn().mockResolvedValue(ready),
    saveGithubModel: vi.fn().mockRejectedValue(new Error('save unavailable')),
  });
  const user = userEvent.setup();
  render(<ConfigPage client={api} />);
  await user.click(await screen.findByRole('button', { name: 'Add configuration' }));
  await user.click(screen.getByRole('button', { name: /GitHub Copilot GPT/ }));
  const model = await screen.findByRole('combobox', { name: 'Model' });
  await user.selectOptions(model, 'gpt-5.5');
  await user.click(screen.getByRole('button', { name: 'Save' }));

  expect(await screen.findByText('save unavailable')).toBeVisible();
  expect(model).toHaveValue('gpt-5.5');
  expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
});

it('shows loopback unavailability without exposing editable controls', async () => {
  const error = new ControlPlaneApiError(403, {
    code: 'config_unavailable', message: 'Provider configuration is available only on a loopback server.',
    action: 'Restart Control Plane with a loopback bind host.',
  });
  render(<ConfigPage client={client(unconfigured, { config: vi.fn().mockRejectedValue(error) })} />);

  expect(await screen.findByText(error.message)).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Add configuration' })).not.toBeInTheDocument();
});

it('reports a saved GitHub connection result without changing the page', async () => {
  const user = userEvent.setup();
  render(<ConfigPage client={client(github)} />);
  expect(await screen.findByText('GitHub Copilot GPT authenticated')).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Test connection' }));

  const dialog = await screen.findByRole('dialog', { name: 'Connection successful' });
  expect(dialog).toHaveTextContent('gpt-5.5');
  expect(dialog).toHaveTextContent('125 ms');
  await user.click(screen.getByRole('button', { name: 'Done' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it.each([azure, github])('returns focus to the disabled-while-pending connection opener', async config => {
  let finish!: (value: {success:true; provider:string; modelName:string; durationMs:number}) => void;
  const pending = new Promise<{success:true; provider:string; modelName:string; durationMs:number}>(resolve => { finish=resolve; });
  render(<ConfigPage client={client(config,{testConnection:vi.fn().mockReturnValue(pending)})}/>);
  const opener=await screen.findByRole('button',{name:'Test connection'});
  await userEvent.click(opener);
  expect(opener).toBeDisabled();
  document.body.focus();
  finish({success:true,provider:'github_copilot',modelName:'gpt-5.5',durationMs:125});
  await userEvent.click(await screen.findByRole('button',{name:'Done'}));
  await waitFor(()=>expect(opener).toHaveFocus());
});
