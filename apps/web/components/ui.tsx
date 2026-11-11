'use client';

import { useEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { AlertTriangle, LoaderCircle, X } from 'lucide-react';
import { statusLabel } from '@/lib/format';

export function Button({ variant = 'secondary', size, className = '', children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' | 'ghost'; size?: 'small'; children: ReactNode }) {
  return <button {...props} className={`button ${variant === 'secondary' ? '' : variant} ${size || ''} ${className}`.trim()}>{children}</button>;
}
export function IconButton({ label, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; children: ReactNode }) {
  return <button {...props} className={`icon-button ${props.className || ''}`} aria-label={label} title={label}>{children}</button>;
}
export function PageHeader({ index, title, subtitle, children }: { index: string; title: string; subtitle: string; children?: ReactNode }) {
  return <header className="page-heading"><div><div className="eyebrow">{index} / LINEPILOT OPERATIONS SYSTEM</div><h1>{title}</h1><p className="subheading">{subtitle}</p></div>{children && <div className="heading-actions">{children}</div>}</header>;
}
export function Panel({ title, meta, action, children, className = '' }: { title: string; meta?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><div className="panel-header"><div><h3>{title}</h3>{meta && <div className="panel-meta">{meta}</div>}</div>{action}</div>{children}</section>;
}
export function Metric({ label, value, unit, context }: { label: string; value: string | number; unit?: string; context?: string }) {
  return <div className="metric"><div className="metric-label">{label}</div><div className="metric-value">{value}{unit && <span className="metric-unit">{unit}</span>}</div><div className="metric-context">{context || 'Selected schedule'}</div></div>;
}
export function Status({ value }: { value?: string }) {
  const css = ['completed', 'optimal', 'feasible', 'active'].includes((value || '').toLowerCase()) ? 'success' : ['failed', 'infeasible', 'error', 'cancelled', 'down'].includes((value || '').toLowerCase()) ? 'error' : 'warning';
  return <span className="status"><span className={`status-dot ${css}`} />{statusLabel(value)}</span>;
}
export function Empty({ title, text, action }: { title: string; text: string; action?: ReactNode }) {
  return <div className="empty-state"><AlertTriangle size={23} strokeWidth={1.5} /><h3>{title}</h3><p>{text}</p>{action}</div>;
}
export function ErrorNotice({ error, retry }: { error: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : typeof error === 'string' ? error : 'The request could not be completed.';
  return <div className="error-state" role="alert"><div><strong>Request failed</strong><p>{message}</p></div>{retry && <Button onClick={retry} size="small">Retry</Button>}</div>;
}
export function Loading({ label = 'Loading operational data' }: { label?: string }) {
  return <div className="panel-body" role="status"><div className="status"><LoaderCircle size={14} />{label}</div><div className="loading-line" style={{ marginTop: 14, width: '76%' }} /><div className="loading-line" style={{ marginTop: 8, width: '55%' }} /></div>;
}
export function Modal({ title, children, footer, onClose, wide = false }: { title: string; children: ReactNode; footer?: ReactNode; onClose: () => void; wide?: boolean }) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const previousFocusRef = useRef<HTMLElement | null | undefined>(undefined);
  if (previousFocusRef.current === undefined && typeof document !== 'undefined') {
    previousFocusRef.current = document.activeElement as HTMLElement | null;
  }
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const dialog = dialogRef.current;
    const focusable = () => Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])') || []);
    const initial = focusable()[0]; initial?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onCloseRef.current(); return; }
      if (event.key !== 'Tab') return;
      const targets = focusable(); if (!targets.length) { event.preventDefault(); return; }
      const first = targets[0], last = targets[targets.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handleKey, true);
    return () => { document.removeEventListener('keydown', handleKey, true); if (previousFocusRef.current?.isConnected) previousFocusRef.current.focus(); };
  }, []);
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><div ref={dialogRef} className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}><div className="modal-header"><h3>{title}</h3><IconButton label="Close dialog" onClick={onClose}><X /></IconButton></div><div className="modal-body">{children}</div>{footer && <div className="modal-footer">{footer}</div>}</div></div>;
}
