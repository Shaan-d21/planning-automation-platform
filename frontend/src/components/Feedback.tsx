import type { ReactNode } from "react";

import { Icon } from "./Icon";

type FeedbackTone = "success" | "error" | "warning" | "info";

interface FeedbackBannerProps {
  tone: FeedbackTone;
  title?: string;
  message: ReactNode;
  onDismiss?: () => void;
  className?: string;
}

export function FeedbackBanner({ tone, title, message, onDismiss, className = "" }: FeedbackBannerProps) {
  const role = tone === "error" ? "alert" : "status";
  const icon = tone === "success" ? "check" : tone === "info" ? "assistant" : "alert";
  return <div className={`feedback-banner feedback-banner--${tone}${className ? ` ${className}` : ""}`} role={role}>
    <Icon name={icon} />
    <div>{title && <strong>{title}</strong>}<span>{message}</span></div>
    {onDismiss && <button type="button" aria-label="Dismiss message" onClick={onDismiss}><Icon name="close" /></button>}
  </div>;
}

export function ToastMessage({ tone, message, onDismiss }: { tone: "success" | "error"; message: string; onDismiss?: () => void }) {
  return <div className={`toast toast--${tone}`} role={tone === "error" ? "alert" : "status"}>
    <Icon name={tone === "success" ? "check" : "alert"} />
    <span>{message}</span>
    {onDismiss && <button type="button" onClick={onDismiss} aria-label="Dismiss message"><Icon name="close" /></button>}
  </div>;
}

export function FullPageLoading() {
  return <main className="loading-screen"><div className="loading-brand"><span className="brand-mark">B</span><strong>BISP EPM Automation</strong></div><div className="skeleton skeleton--title" /><div className="skeleton-grid"><span /><span /><span /><span /></div><p>Preparing your Planning workspace…</p></main>;
}

export function WorkspaceLoading({ label = "My work", message = "Organizing your assignments and Planning cycle…" }: { label?: string; message?: string }) {
  return <section className="workspace-loading" aria-live="polite" aria-busy="true"><span className="eyebrow">{label}</span><div className="skeleton skeleton--title" /><div className="skeleton-grid"><span /><span /><span /><span /></div><p>{message}</p></section>;
}

export function UnavailableState({ error, onRetry }: { error: string; onRetry: () => Promise<void> }) {
  return <main className="unavailable"><span><Icon name="alert" /></span><h1>We could not open your workspace</h1><p>{error}</p><button className="button button--primary" onClick={() => void onRetry()}>Try again</button></main>;
}

interface ConfirmationDialogProps {
  title: string;
  description: string;
  warning?: string;
  confirmLabel: string;
  tone?: "primary" | "danger";
  busy?: boolean;
  error?: string | null;
  onConfirm: () => void | Promise<void>;
  onClose: () => void;
}

export function ConfirmationDialog({ title, description, warning, confirmLabel, tone = "primary", busy = false, error, onConfirm, onClose }: ConfirmationDialogProps) {
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
    <section className="access-dialog access-dialog--compact confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="confirmation-dialog-title">
      <header><div><span className="eyebrow">Confirm action</span><h2 id="confirmation-dialog-title">{title}</h2><p>{description}</p></div><button type="button" aria-label="Close confirmation" disabled={busy} onClick={onClose}><Icon name="close" /></button></header>
      <div className="confirmation-dialog__body">
        {warning && <aside className="dialog-warning"><Icon name="alert" /><span>{warning}</span></aside>}
        {error && <FeedbackBanner tone="error" message={error} />}
      </div>
      <footer><button type="button" className="button button--quiet" disabled={busy} onClick={onClose}>Cancel</button><button type="button" className={`button ${tone === "danger" ? "button--danger" : "button--primary"}`} disabled={busy} onClick={() => void onConfirm()}>{busy ? <><span className="spinner" /> Working</> : confirmLabel}</button></footer>
    </section>
  </div>;
}
