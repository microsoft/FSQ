import { useEffect, useRef, useState } from 'react';
import { Check, Copy, TriangleAlert, Minus } from 'lucide-react';
import type { PrerequisiteRecord } from '../../../api/types';

const labels: Record<string,string> = {adb_cli:'ADB CLI',uiautomator2_runtime:'uiautomator2 dependency',adb_server:'Existing ADB server',device_connection:'Device connection and authorization',device_selection:'Device selection',application_identifier:'Application ID',application_installation:'Application installation',xcode_installation:'Full Xcode',xcode_developer_directory:'Xcode developer directory',appium_cli:'Appium CLI',appium_mac2_driver:'Appium Mac2 driver',appium_endpoint:'Appium endpoint',application_path:'Application path',bundle_identifier:'Bundle identifier'};

function CommandCopyButton({ command }: { command: string }) {
  const [state, setState] = useState<'idle' | 'copying' | 'copied' | 'failed'>('idle');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef(false);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (timer.current !== null) clearTimeout(timer.current);
    };
  }, []);

  const copy = async () => {
    if (pending.current) return;
    pending.current = true;
    if (timer.current !== null) clearTimeout(timer.current);
    setState('copying');
    try {
      await navigator.clipboard.writeText(command);
      if (!mounted.current) return;
      setState('copied');
      timer.current = setTimeout(() => setState('idle'), 2000);
    } catch {
      if (mounted.current) setState('failed');
    } finally {
      pending.current = false;
    }
  };

  const feedback = state === 'copying' ? 'Copying…' : state === 'copied' ? 'Copied!' : state === 'failed' ? 'Copy failed — select the command text to copy manually.' : '';
  return <span className="prerequisite-copy-control">
    <button type="button" aria-label={'Copy command: ' + command} title={state === 'copied' ? 'Copied!' : 'Copy command'}
      data-copy-state={state} disabled={state === 'copying'} onClick={() => void copy()}>
      {state === 'copied' ? <Check aria-hidden="true" /> : state === 'failed' ? <TriangleAlert aria-hidden="true" /> : <Copy aria-hidden="true" />}
    </button>
    <span className="prerequisite-copy-feedback" data-copy-state={state} role="status" aria-live="polite" aria-atomic="true">{feedback}</span>
  </span>;
}

export function PrerequisiteList({items}: {items: readonly PrerequisiteRecord[]}) {
  const passed = items.filter(item=>item.status==='ready');
  const remaining = items.filter(item=>item.status!=='ready');
  const rows = (values:readonly PrerequisiteRecord[]) => <ul className="prerequisite-list">{values.map(item=><li key={item.identifier} className={'prerequisite-row prerequisite-row--'+item.status}>
    <div className="prerequisite-heading"><strong>{labels[item.identifier] || item.identifier.replaceAll('_',' ')}</strong><span>{item.status==='ready'?<Check aria-hidden="true"/>:item.status==='not_applicable'?<Minus aria-hidden="true"/>:<TriangleAlert aria-hidden="true"/>}{item.status==='not_applicable'?'Not applicable':item.status==='ready'?'Ready':item.status==='error'?'Check failed':'Needs attention'}</span></div>
    <p>{item.message}</p>{item.status!=='ready' && item.action && <p>{item.action}</p>}
    {item.commands.map(command=><div className="prerequisite-command" key={command}><code>{command}</code><CommandCopyButton command={command} /></div>)}
  </li>)}</ul>;
  return <div>{rows(remaining)}{passed.length>0 && <details className="prerequisite-passed"><summary>Show passed checks ({passed.length})</summary>{rows(passed)}</details>}</div>;
}
