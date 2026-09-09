import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { controlPlaneClient } from '../../api/controlPlaneClient';
import type { WorkspaceEntriesResponse, WorkspaceFileResponse } from '../../api/types';
import { WorkspaceBrowser } from './WorkspaceBrowser';

vi.mock('../../api/controlPlaneClient', () => ({
  controlPlaneClient: { workspaceEntries: vi.fn(), workspaceFile: vi.fn() },
  toApiError: vi.fn((error) => error),
}));

const entries = (path: string, names: string[]): WorkspaceEntriesResponse => ({
  path,
  entries: names.map((name) => ({
    path: path ? `${path}/${name}` : name,
    name,
    kind: name.includes('.') ? 'file' : 'directory',
    size: name.includes('.') ? 10 : null,
    modifiedTime: '2030-01-01T00:00:00Z',
  })),
  truncated: false,
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => { resolve = accept; });
  return { promise, resolve };
}

const file = (name: string, content: string): WorkspaceFileResponse => ({
  path: `cases/${name}`,
  name,
  mediaType: 'text/yaml',
  presentation: 'code',
  size: content.length,
  lineCount: 1,
  modifiedTime: '2030-01-01T00:00:00Z',
  content,
});

afterEach(() => vi.clearAllMocks());

it('renders a distinct successful empty tree state', async () => {
  vi.mocked(controlPlaneClient.workspaceEntries).mockResolvedValue(entries('', []));

  render(<WorkspaceBrowser workspaceName="alpha" />);

  expect(screen.getByRole('region', { name: 'Workspace files for alpha' })).toBeVisible();
  expect(screen.getByRole('region', { name: 'Workspace file tree' })).toBeVisible();
  expect(screen.getByRole('region', { name: 'Workspace file content' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Files' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Files' })).toBeVisible();
  expect(await screen.findByText('No cases or knowledge files')).toBeVisible();
  expect(screen.queryByText('Loading workspace files…')).not.toBeInTheDocument();
});

it('aborts an older root retry and ignores its stale response', async () => {
  const firstRetry = deferred<WorkspaceEntriesResponse>();
  const latestRetry = deferred<WorkspaceEntriesResponse>();
  vi.mocked(controlPlaneClient.workspaceEntries)
    .mockRejectedValueOnce({ message: 'Root failed', action: 'Retry.' })
    .mockReturnValueOnce(firstRetry.promise)
    .mockReturnValueOnce(latestRetry.promise);

  render(<WorkspaceBrowser workspaceName="alpha" />);
  const retry = await screen.findByRole('button', { name: 'Retry workspace files' });
  act(() => {
    retry.click();
    retry.click();
  });

  const firstRetrySignal = vi.mocked(controlPlaneClient.workspaceEntries).mock.calls[1][2];
  const latestRetrySignal = vi.mocked(controlPlaneClient.workspaceEntries).mock.calls[2][2];
  expect(firstRetrySignal?.aborted).toBe(true);
  expect(latestRetrySignal?.aborted).toBe(false);

  latestRetry.resolve(entries('', ['latest.md']));
  expect(await screen.findByRole('button', { name: 'latest.md' })).toBeVisible();
  firstRetry.resolve(entries('', ['stale.md']));
  await act(async () => Promise.resolve());
  expect(screen.queryByRole('button', { name: 'stale.md' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'latest.md' })).toBeVisible();
});

it('shows the real workspace path and a non-interactive Code presentation for text files', async () => {
  vi.mocked(controlPlaneClient.workspaceEntries)
    .mockResolvedValueOnce(entries('', ['cases']))
    .mockResolvedValueOnce(entries('cases', ['scenario.yaml']));
  vi.mocked(controlPlaneClient.workspaceFile).mockResolvedValue(file('scenario.yaml', 'name: scenario\r\nurl: "https://example.test"'));

  const user = userEvent.setup();
  render(<WorkspaceBrowser workspaceName="alpha" />);
  const cases = await screen.findByRole('button', { name: 'cases' });
  expect(cases).toHaveAttribute('aria-expanded', 'false');
  expect(cases.querySelector('.cp-tree-type-icon--folder svg')).toBeInTheDocument();
  await user.click(cases);
  expect(cases).toHaveAttribute('aria-expanded', 'true');
  const yamlFile = await screen.findByRole('button', { name: 'scenario.yaml' });
  expect(yamlFile.querySelector('.cp-tree-type-icon--yaml svg')).toBeInTheDocument();
  await user.click(yamlFile);

  const breadcrumb = await screen.findByLabelText('File path');
  expect(breadcrumb).toHaveTextContent('alpha / cases / scenario.yaml');
  expect(screen.queryByText('Code')).not.toBeInTheDocument();
  expect(screen.queryByRole('tab', { name: 'Code' })).not.toBeInTheDocument();
  expect(screen.getByText(/1 lines · \d+ B/)).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Replay Case' })).not.toBeInTheDocument();
  expect(screen.getByRole('region', { name: 'Read-only YAML source' })).toBeVisible();
  expect(screen.getByText('name')).toHaveClass('cp-yaml-key');
  expect(screen.getByText('"https://example.test"')).toHaveClass('cp-yaml-string');
});

it('offers recording and extracts an eligible FSQ replay context', async () => {
  vi.mocked(controlPlaneClient.workspaceEntries)
    .mockResolvedValueOnce(entries('', ['cases']))
    .mockResolvedValueOnce(entries('cases', ['web']))
    .mockResolvedValueOnce(entries('cases/web', ['flows']))
    .mockResolvedValueOnce(entries('cases/web/flows', ['login.fsq.yaml']));
  vi.mocked(controlPlaneClient.workspaceFile).mockResolvedValue({
    ...file('login.fsq.yaml', 'name: login\ncommands: []'),
    path: 'cases/web/flows/login.fsq.yaml',
    lineCount: 2,
    size: 24,
  });
  const onRecordCase = vi.fn();
  const onReplayCase = vi.fn();
  const user = userEvent.setup();

  render(<WorkspaceBrowser workspaceName="alpha" onReplayCase={onReplayCase} />);
  expect(screen.queryByRole('button', { name: 'Record new case' })).not.toBeInTheDocument();

  await user.click(await screen.findByRole('button', { name: 'cases' }));
  await user.click(await screen.findByRole('button', { name: 'web' }));
  await user.click(await screen.findByRole('button', { name: 'flows' }));
  await user.click(await screen.findByRole('button', { name: 'login.fsq.yaml' }));

  const replayCase = await screen.findByRole('button', { name: 'Replay Case' });
  expect(screen.getByText(/2 lines · 24 B/)).toBeVisible();
  await user.click(replayCase);
  expect(onReplayCase).toHaveBeenCalledWith('web', 'flows/login.fsq.yaml');
});

it('extracts a top-level FSQ replay case path', async () => {
  vi.mocked(controlPlaneClient.workspaceEntries)
    .mockResolvedValueOnce(entries('', ['cases']))
    .mockResolvedValueOnce(entries('cases', ['android']))
    .mockResolvedValueOnce(entries('cases/android', ['smoke.fsq.yaml']));
  vi.mocked(controlPlaneClient.workspaceFile).mockResolvedValue({
    ...file('smoke.fsq.yaml', 'name: smoke\ncommands: []'),
    path: 'cases/android/smoke.fsq.yaml',
  });
  const onReplayCase = vi.fn();
  const user = userEvent.setup();

  render(<WorkspaceBrowser workspaceName="alpha" onReplayCase={onReplayCase} />);
  await user.click(await screen.findByRole('button', { name: 'cases' }));
  await user.click(await screen.findByRole('button', { name: 'android' }));
  await user.click(await screen.findByRole('button', { name: 'smoke.fsq.yaml' }));
  await user.click(await screen.findByRole('button', { name: 'Replay Case' }));

  expect(onReplayCase).toHaveBeenCalledWith('android', 'smoke.fsq.yaml');
});

it('ignores stale root and nested responses after the selected workspace changes', async () => {
  const alphaRoot = deferred<WorkspaceEntriesResponse>();
  const alphaCases = deferred<WorkspaceEntriesResponse>();
  const betaCases = deferred<WorkspaceEntriesResponse>();
  vi.mocked(controlPlaneClient.workspaceEntries).mockImplementation((workspace, path) => {
    if (workspace === 'alpha' && path === '') return alphaRoot.promise;
    if (workspace === 'alpha') return alphaCases.promise;
    if (path === '') return Promise.resolve(entries('', ['cases']));
    return betaCases.promise;
  });

  const user = userEvent.setup();
  const view = render(<WorkspaceBrowser workspaceName="alpha" />);
  view.rerender(<WorkspaceBrowser workspaceName="beta" />);
  await user.click(await screen.findByRole('button', { name: 'cases' }));
  expect(screen.getByText('Loading…')).toBeVisible();

  alphaRoot.resolve(entries('', ['alpha-only.md']));
  alphaCases.resolve(entries('cases', ['alpha-case.yaml']));
  await Promise.resolve();

  expect(screen.queryByText('alpha-only.md')).not.toBeInTheDocument();
  expect(screen.getByText('Loading…')).toBeVisible();

  betaCases.resolve(entries('cases', ['beta-case.yaml']));
  expect(await screen.findByRole('button', { name: 'beta-case.yaml' })).toBeVisible();
});

it('suppresses raw HTML in Markdown preview and exposes it only in the code tab', async () => {
  vi.mocked(controlPlaneClient.workspaceEntries)
    .mockResolvedValueOnce(entries('', ['knowledge']))
    .mockResolvedValueOnce(entries('knowledge', ['project.md']));
  vi.mocked(controlPlaneClient.workspaceFile).mockResolvedValue({
    path: 'knowledge/project.md',
    name: 'project.md',
    mediaType: 'text/markdown',
    presentation: 'markdown',
    size: 45,
    lineCount: 3,
    modifiedTime: '2030-01-01T00:00:00Z',
    content: '# Safe heading\n<script>alert("unsafe")</script>',
  } satisfies WorkspaceFileResponse);

  const user = userEvent.setup();
  const { container } = render(<WorkspaceBrowser workspaceName="alpha" />);
  await user.click(await screen.findByRole('button', { name: 'knowledge' }));
  const markdownFile = await screen.findByRole('button', { name: 'project.md' });
  expect(markdownFile.querySelector('.cp-tree-type-icon--file svg')).toBeInTheDocument();
  await user.click(markdownFile);

  expect(await screen.findByRole('heading', { name: 'Safe heading' })).toBeVisible();
  const previewTab = screen.getByRole('tab', { name: 'Preview' });
  const codeTab = screen.getByRole('tab', { name: 'Code' });
  const panel = screen.getByRole('tabpanel');
  expect(previewTab).toHaveAttribute('aria-controls', panel.id);
  expect(panel).toHaveAttribute('aria-labelledby', previewTab.id);
  expect(container.querySelector('.cp-markdown script')).toBeNull();
  expect(screen.queryByText('alert("unsafe")')).not.toBeInTheDocument();

  await user.click(codeTab);
  expect(screen.getByText(/<script>alert\("unsafe"\)<\/script>/)).toBeVisible();
  expect(panel).toHaveAttribute('aria-labelledby', codeTab.id);
  expect(container.querySelector('.cp-markdown-source')).toBeVisible();
  expect(container.querySelector('.cp-yaml-source')).toBeNull();
});

it('keeps non-YAML text in escaped plain source without YAML line numbers', async () => {
  vi.mocked(controlPlaneClient.workspaceEntries)
    .mockResolvedValueOnce(entries('', ['cases']))
    .mockResolvedValueOnce(entries('cases', ['notes.txt', 'settings.yml']));
  vi.mocked(controlPlaneClient.workspaceFile).mockResolvedValue(file('notes.txt', '<safe>plain text</safe>'));

  const user = userEvent.setup();
  const { container } = render(<WorkspaceBrowser workspaceName="alpha" />);
  await user.click(await screen.findByRole('button', { name: 'cases' }));
  expect(screen.getByRole('button', { name: 'settings.yml' }).querySelector('.cp-tree-type-icon--yaml svg')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'notes.txt' }));

  expect(await screen.findByText('<safe>plain text</safe>')).toBeVisible();
  expect(container.querySelector('.cp-plain-source')).toBeVisible();
  expect(container.querySelector('.cp-source-line-number')).toBeNull();
  expect(container.querySelector('.cp-yaml-source')).toBeNull();
});

it('keeps the latest file selection when an earlier request finishes last', async () => {
  const first = deferred<WorkspaceFileResponse>();
  const second = deferred<WorkspaceFileResponse>();
  vi.mocked(controlPlaneClient.workspaceEntries)
    .mockResolvedValueOnce(entries('', ['cases']))
    .mockResolvedValueOnce(entries('cases', ['first.yaml', 'second.yaml']));
  vi.mocked(controlPlaneClient.workspaceFile)
    .mockImplementationOnce(() => first.promise)
    .mockImplementationOnce(() => second.promise);

  const user = userEvent.setup();
  render(<WorkspaceBrowser workspaceName="alpha" />);
  await user.click(await screen.findByRole('button', { name: 'cases' }));
  await user.click(await screen.findByRole('button', { name: 'first.yaml' }));
  await user.click(screen.getByRole('button', { name: 'second.yaml' }));
  await act(async () => second.resolve(file('second.yaml', 'second: selected')));
  const source = await screen.findByRole('region', { name: 'Read-only YAML source' });
  expect(source).toHaveTextContent('second: selected');

  await act(async () => first.resolve(file('first.yaml', 'first: stale')));
  expect(source).toHaveTextContent('second: selected');
  expect(source).not.toHaveTextContent('first: stale');
});