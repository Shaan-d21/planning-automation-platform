import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { EnvironmentConfigurationResponse } from "../api/types";
import { Icon } from "./Icon";

interface EnvironmentSetupDialogProps {
  csrfToken: string;
  onClose: () => void;
}

export function EnvironmentSetupDialog({
  csrfToken,
  onClose
}: EnvironmentSetupDialogProps) {
  const [configuration, setConfiguration] = useState<EnvironmentConfigurationResponse | null>(null);
  const [selection, setSelection] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void api.environmentConfiguration()
      .then((result) => {
        if (!active) return;
        setConfiguration(result);
        setSelection(result.selected_application ?? result.active_application ?? "");
      })
      .catch((reason: unknown) => {
        if (active) setError(message(reason));
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => { active = false; };
  }, []);

  const discover = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.discoverEnvironmentApplications(csrfToken);
      setConfiguration(result);
      if (!selection && result.applications.length === 1) {
        setSelection(result.applications[0].name);
      }
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!selection) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.selectEnvironmentApplication(selection, csrfToken);
      setConfiguration(result);
      setSelection(result.selected_application ?? selection);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (!busy && event.target === event.currentTarget) onClose();
    }}>
      <section className="access-dialog environment-setup-dialog" role="dialog" aria-modal="true" aria-labelledby="environment-setup-title">
        <header>
          <div>
            <span className="eyebrow">Environment setup</span>
            <h2 id="environment-setup-title">Choose the Planning application</h2>
            <p>Applications are retrieved from Oracle with the configured automation identity. No credentials are stored in this configuration.</p>
          </div>
          <button type="button" aria-label="Close environment setup" disabled={busy} onClick={onClose}><Icon name="close" /></button>
        </header>

        <div className="environment-setup-body">
          <section className="environment-setup-connection">
            <span><small>Oracle environment</small><strong>{configuration?.base_url ?? "Reading configuration…"}</strong></span>
            <span><small>Deployment</small><strong>{configuration?.deployment_mode ?? "—"}</strong></span>
            <button type="button" className="button button--quiet" disabled={busy} onClick={() => void discover()}>{busy ? <span className="spinner spinner--dark" /> : <Icon name="refresh" />} Refresh from Oracle</button>
          </section>

          {error && <aside className="environment-setup-feedback is-error"><Icon name="alert" /><span><strong>Application discovery could not complete</strong><small>{error}</small></span></aside>}
          {configuration?.last_discovery_error && !error && <aside className="environment-setup-feedback is-warning"><Icon name="alert" /><span><strong>Last discovery needs attention</strong><small>{configuration.last_discovery_error}</small></span></aside>}
          {configuration?.restart_required && <aside className="environment-setup-feedback is-warning"><Icon name="alert" /><span><strong>Restart required before running operations</strong><small>{configuration.message}</small></span></aside>}

          <section className="environment-application-list" aria-label="Discovered Planning applications">
            <header><div><strong>Available applications</strong><small>{configuration?.applications.length ?? 0} returned by Oracle</small></div>{configuration?.last_discovered_at && <time>Updated {formatDateTime(configuration.last_discovered_at)}</time>}</header>
            {busy && !configuration ? <div className="environment-setup-empty"><span className="spinner spinner--dark" /><strong>Reading saved environment configuration…</strong></div> : configuration?.applications.length ? configuration.applications.map((application) => (
              <label className={`environment-application${selection === application.name ? " is-selected" : ""}`} key={application.name}>
                <input type="radio" name="planning-application" value={application.name} checked={selection === application.name} disabled={busy} onChange={() => setSelection(application.name)} />
                <span className="environment-application__icon"><Icon name="data" /></span>
                <span><strong>{application.name}</strong><small>{[application.product_type, application.application_type].filter(Boolean).join(" · ") || "Oracle Planning application"}</small></span>
                {configuration.active_application === application.name && <em>Active</em>}
              </label>
            )) : <div className="environment-setup-empty"><Icon name="data" /><strong>No applications discovered yet</strong><small>Select Refresh from Oracle. Application discovery requires the configured Oracle account to have the supported administrative access.</small></div>}
          </section>
        </div>

        <footer>
          <button type="button" className="button button--quiet" disabled={busy} onClick={onClose}>Close</button>
          <button type="button" className="button button--primary" disabled={busy || !selection || selection === configuration?.selected_application} onClick={() => void save()}>{busy ? "Saving…" : "Save application"}</button>
        </footer>
      </section>
    </div>
  );
}

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : "The request could not be completed.";
}

function formatDateTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}
