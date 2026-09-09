import type { ReactNode } from 'react';
export interface ContentTab<T extends string> { id: T; label: ReactNode; tabId: string; panelId: string; disabled?: boolean }
interface Props<T extends string> { label: string; value: T; items: readonly ContentTab<T>[]; onChange: (value: T) => void; className?: string }
export function ContentTabs<T extends string>({ label, value, items, onChange, className = '' }: Props<T>) {
  const enabled = items.filter(item => !item.disabled);
  return <div className={'content-tabs ' + className} role="tablist" aria-label={label}>
    {items.map(item => <button key={item.id} id={item.tabId} type="button" role="tab" aria-selected={value === item.id} aria-controls={item.panelId} disabled={item.disabled} tabIndex={value === item.id ? 0 : -1} onClick={() => onChange(item.id)} onKeyDown={event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || !enabled.length) return;
      event.preventDefault();
      const index = enabled.findIndex(tab => tab.id === item.id);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? enabled.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + enabled.length) % enabled.length;
      onChange(enabled[next].id);
      document.getElementById(enabled[next].tabId)?.focus();
    }}>{item.label}</button>)}
  </div>;
}
