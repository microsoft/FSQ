import { useEffect, useId, useRef, type ReactNode } from 'react';

interface DialogFrameProps {
  title: string;
  children: ReactNode;
  onClose: () => void;
  returnFocus?: HTMLElement | null;
}

export function DialogFrame({ title, children, onClose, returnFocus }: DialogFrameProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const restoreTimer = useRef<number | null>(null);
  const onCloseRef = useRef(onClose);
  const explicitReturnFocus = useRef(returnFocus);
  explicitReturnFocus.current = returnFocus;
  onCloseRef.current = onClose;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (restoreTimer.current !== null) window.clearTimeout(restoreTimer.current);
    const active = document.activeElement;
    if (explicitReturnFocus.current) returnFocusRef.current = explicitReturnFocus.current;
    else if (active instanceof HTMLElement && !dialog?.contains(active)) returnFocusRef.current = active;
    const focusable = () => dialog?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])');
    const focusFirst = () => (dialog?.querySelector<HTMLElement>('[data-dialog-initial]:not([disabled])') ?? focusable()?.[0] ?? dialog)?.focus();
    focusFirst();
    const observer = new MutationObserver(() => {
      if (!dialog?.contains(document.activeElement) || document.activeElement?.matches('[disabled]')) focusFirst();
    });
    if (dialog) observer.observe(dialog, {childList:true, subtree:true, attributes:true, attributeFilter:['disabled']});
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      const controls = focusable();
      if (event.key !== 'Tab') return;
      if (!controls?.length) { event.preventDefault(); dialog?.focus(); return; }
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (!dialog?.contains(document.activeElement)) { event.preventDefault(); (event.shiftKey ? last : first).focus(); }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      observer.disconnect();
      const opener = returnFocusRef.current;
      restoreTimer.current = window.setTimeout(() => { if (opener?.isConnected) opener.focus(); }, 0);
    };
  }, []);

  return <div className="config-dialog-backdrop">
    <div ref={dialogRef} className="config-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
      <h2 id={titleId}>{title}</h2>
      {children}
    </div>
  </div>;
}
