import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PreflightStatus } from './PreflightStatus';
import type { ReadinessResponse } from '../../../api/types';

const ok={status:'ready' as const,message:'Ready',action:''};
const mac:ReadinessResponse={workspaceName:'test',platformId:'macos',workspace:ok,platform:ok,provider:ok,target:ok,strict:ok,prerequisites:[],commands:{caseCreate:ok,caseTest:ok},checkedAt:'2026-09-07T00:00:00Z'};

it('renders Android stopped-server guidance with copy only and packaged limits',async()=>{
  const bad={status:'unavailable' as const,message:'Existing ADB server unavailable',action:'Start manually'};
  const data:ReadinessResponse={...mac,platformId:'android',targetId:null,commands:{caseCreate:bad,caseTest:bad},prerequisites:[{identifier:'adb_server',code:'android.adb_server_unavailable',status:'unavailable',message:bad.message,action:bad.action,commands:['adb start-server']}]};
  render(<PreflightStatus android mode="explore" loading={false} diagnostics={data}/>);
  expect(screen.getByRole('heading',{name:'Android environment'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Copy command: adb start-server'})).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Start server'})).not.toBeInTheDocument();
  await userEvent.click(screen.getByText('Android installation and troubleshooting',{selector:'summary'}));
  expect(screen.getByText(/Ordinary ADB commands/)).toBeVisible();
});

it('distinguishes a failed diagnostic request from a missing installation and permits retry',async()=>{
  const recheck=vi.fn();
  render(<PreflightStatus macos mode="explore" loading={false} diagnostics={null} onRecheck={recheck}/>);
  expect(screen.getByRole('status')).toHaveTextContent('Environment check unavailable');
  expect(screen.queryByText('Appium CLI')).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button',{name:'Recheck environment'}));
  expect(recheck).toHaveBeenCalledTimes(1);
});

it('renders the packaged setup guide on disclosure without a network request',async()=>{
  render(<PreflightStatus macos mode="explore" loading={false} diagnostics={mac}/>);
  const summary=screen.getByText('macOS installation and troubleshooting');
  expect(summary.parentElement).not.toHaveAttribute('open');
  await userEvent.click(summary);
  expect(summary.parentElement).toHaveAttribute('open');
  expect(screen.getByText(/Install full Xcode from the Mac App Store/)).toBeVisible();
});
