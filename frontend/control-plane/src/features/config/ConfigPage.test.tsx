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

afterEach(() => vi.restoreAllMocks());

it('adds Azure configuration, masks its populated key, and saves the complete draft', async () => {
  const api = client();
  const user = userEvent.setup();
  render(<ConfigPage client={api} />);
  await user.click(await screen.findByRole('button', { name: 'Add configuration' }));
  await user.click(screen.getByRole('button', { name: /Azure GPT/ }));

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
  const azureChoice = screen.getByRole('button', { name: /Azure GPT/ });
  const cancel = screen.getByRole('button', { name: 'Cancel' });
  expect(azureChoice).toHaveFocus();

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
