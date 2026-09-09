import { act, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PrerequisiteList } from './PrerequisiteList';

it('shows failures, distinguishes blocked checks, and collapses passed checks', async () => {
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:copy}});
  render(<PrerequisiteList items={[
    {identifier:'appium_cli',status:'unavailable',message:'CLI missing',action:'Install Appium',commands:['npm install -g appium']},
    {identifier:'appium_mac2_driver',status:'not_applicable',message:'Waiting for Appium',commands:[]},
    {identifier:'xcode_installation',status:'ready',message:'Full Xcode is installed',commands:[]},
  ]} />);
  expect(screen.getByText('CLI missing')).toBeVisible();
  expect(screen.getByText('Waiting for Appium')).toBeVisible();
  expect(screen.getByText('Full Xcode is installed')).not.toBeVisible();
  await userEvent.click(screen.getByRole('button',{name:'Copy command: npm install -g appium'}));
  expect(copy).toHaveBeenCalledWith('npm install -g appium');
  const commandRow = screen.getByText('npm install -g appium').closest('.prerequisite-command')!;
  expect(within(commandRow as HTMLElement).getByRole('status')).toHaveTextContent('Copied!');
  expect(within(commandRow as HTMLElement).getByRole('button')).toHaveAttribute('data-copy-state', 'copied');
});

it('keeps manual copying available on clipboard failure',async()=>{
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:vi.fn().mockRejectedValue(new Error('denied'))}});
  render(<PrerequisiteList items={[{identifier:'appium_cli',status:'unavailable',message:'Missing',commands:['npm install -g appium']}]}/>);
  await userEvent.click(screen.getByRole('button',{name:'Copy command: npm install -g appium'}));
  expect(screen.getByRole('status')).toHaveTextContent('Copy failed');
  expect(screen.getByText('npm install -g appium')).toBeVisible();
});

it('shows local pending feedback and restores the icon after two seconds', async () => {
  vi.useFakeTimers();
  let finish!: () => void;
  const pending = new Promise<void>((resolve) => { finish = resolve; });
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockReturnValue(pending) } });
  const view = render(<PrerequisiteList items={[{ identifier: 'appium_cli', status: 'unavailable', message: 'Missing', commands: ['npm install -g appium'] }]} />);
  try {
    const button = screen.getByRole('button', { name: 'Copy command: npm install -g appium' });
    fireEvent.click(button);
    expect(button).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('Copying…');
    await act(async () => { finish(); await pending; });
    expect(button).toBeEnabled();
    expect(screen.getByRole('status')).toHaveTextContent('Copied!');
    act(() => { vi.advanceTimersByTime(2000); });
    expect(button).toHaveAttribute('data-copy-state', 'idle');
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
  } finally { view.unmount(); vi.useRealTimers(); }
});

it('keeps feedback scoped to each command when clipboard requests resolve out of order', async () => {
  const resolve: (() => void)[] = [];
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn(() => new Promise<void>(done => resolve.push(done))) } });
  const view = render(<PrerequisiteList items={[{ identifier: 'mac2', status: 'unavailable', message: 'Missing', commands: ['appium driver install mac2', 'appium driver doctor mac2'] }]} />);
  const buttons = screen.getAllByRole('button');
  fireEvent.click(buttons[0]);
  fireEvent.click(buttons[1]);
  await act(async () => resolve[1]());
  expect(buttons[0]).toHaveAttribute('data-copy-state', 'copying');
  expect(buttons[1]).toHaveAttribute('data-copy-state', 'copied');
  view.unmount();
  await act(async () => resolve[0]());
});
