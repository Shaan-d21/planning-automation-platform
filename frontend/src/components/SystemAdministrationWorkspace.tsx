import { useMemo, useState, type FormEvent } from "react";

import type { SystemApplicationRegistration, SystemSecurityResponse } from "../api/types";
import { ConfirmationDialog } from "./Feedback";
import { Icon } from "./Icon";

interface Props {
  data: SystemSecurityResponse;
  busySessionKey: string | null;
  onRevoke: (sessionKey: string) => Promise<void>;
  busyUserId: number | null;
  onAdministratorChange: (userId: number, enabled: boolean) => Promise<void>;
  busyApplication: number | "register" | null;
  onRegisterEnvironment: (baseUrl: string) => Promise<void>;
  onRemoveApplication: (applicationId: number) => Promise<void>;
}

export function SystemAdministrationWorkspace({ data, busySessionKey, onRevoke, busyUserId, onAdministratorChange, busyApplication, onRegisterEnvironment, onRemoveApplication }: Props) {
  const [showEnded, setShowEnded] = useState(false);
  const [environmentUrl, setEnvironmentUrl] = useState("");
  const [removingApplication, setRemovingApplication] = useState<SystemApplicationRegistration | null>(null);
  const sessions = useMemo(
    () => data.sessions.filter((session) => showEnded || session.active),
    [data.sessions, showEnded]
  );

  async function registerEnvironment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await onRegisterEnvironment(environmentUrl.trim());
      setEnvironmentUrl("");
    } catch {
      // The application shell displays the actionable API error.
    }
  }

  async function removeApplication() {
    if (!removingApplication) return;
    try {
      await onRemoveApplication(removingApplication.application_id);
      setRemovingApplication(null);
    } catch {
      // Keep the confirmation open while the application shell shows the error.
    }
  }

  return <section className="jobs-workspace system-admin-workspace">
    <header className="page-intro jobs-intro">
      <div><span className="eyebrow">Platform security</span><h1>System Administration</h1><p>Review sign-ins, detect shared credentials, revoke browser sessions, and manage registered Oracle applications. Service-account secrets remain server-managed for this release.</p></div>
      <span className="retention-badge"><Icon name="check" /> Persistent security audit</span>
    </header>

    <div className="jobs-summary" aria-label="Session security summary">
      <Metric label="Active sessions" value={data.summary.active_sessions} tone="brand" icon="users" />
      <Metric label="Concurrent accounts" value={data.summary.concurrent_accounts} tone="warning" icon="alert" />
      <Metric label="Active IP addresses" value={data.summary.unique_active_ips} tone="success" icon="activity" />
      <Metric label="Failed logins (24h)" value={data.summary.failed_logins_24h} tone="danger" icon="alert" />
    </div>

    <section className="panel jobs-panel">
      <div className="panel-heading"><div><span className="eyebrow">Oracle environments</span><h2>Registered applications</h2><p>Add another Oracle EPM URL using the server-configured service account. The URL is verified server-side and is not returned to the browser after registration.</p></div></div>
      <form className="environment-registration-form" onSubmit={(event) => void registerEnvironment(event)}>
        <label htmlFor="oracle-environment-url"><span>Oracle environment URL</span><input id="oracle-environment-url" type="url" autoComplete="off" placeholder="https://your-environment.oraclecloud.com" value={environmentUrl} onChange={(event) => setEnvironmentUrl(event.target.value)} required /></label>
        <button type="submit" className="button button--primary" disabled={!environmentUrl.trim() || busyApplication !== null}>{busyApplication === "register" ? "Verifying Oracle…" : "Verify and register"}</button>
      </form>
      <div className="jobs-table-wrap"><table className="jobs-table"><thead><tr><th>Application</th><th>Product</th><th>Environment</th><th>Verification</th><th><span className="sr-only">Action</span></th></tr></thead><tbody>
        {data.applications.map((application) => <tr key={application.application_id}><td><strong>{application.application_name}</strong>{application.current && <><br /><small>Current workspace</small></>}</td><td>{application.business_process}</td><td>{application.environment_label}{application.deployment_application ? <><br /><small>Server default</small></> : null}</td><td>{application.last_verified_at ? formatDate(application.last_verified_at) : "Registered"}</td><td>{application.removable ? <button type="button" className="button button--quiet" disabled={busyApplication !== null} onClick={() => setRemovingApplication(application)}>{busyApplication === application.application_id ? "Removing…" : "Remove"}</button> : <small>Protected</small>}</td></tr>)}
        {!data.applications.length && <tr><td colSpan={5}>No active Oracle applications are registered.</td></tr>}
      </tbody></table></div>
    </section>

    <section className="panel jobs-panel">
      <div className="panel-heading"><div><span className="eyebrow">Separation of duties</span><h2>System Administrators</h2><p>This role controls login auditing and sessions only. It does not grant Oracle EPM operation permissions.</p></div></div>
      <div className="jobs-table-wrap"><table className="jobs-table"><thead><tr><th>User</th><th>Account</th><th>System Administrator</th></tr></thead><tbody>
        {data.administrators.map((user) => <tr key={user.user_id}><td><strong>{user.display_name}</strong><br /><small>{user.username}</small></td><td>{user.active ? "Active" : "Inactive"}</td><td><label><input type="checkbox" checked={user.system_administrator} disabled={!user.active || busyUserId === user.user_id} onChange={(event) => void onAdministratorChange(user.user_id, event.target.checked)} /> {busyUserId === user.user_id ? "Saving…" : user.system_administrator ? "Granted" : "Not granted"}</label></td></tr>)}
      </tbody></table></div>
    </section>

    {data.concurrent_accounts.length > 0 && <section className="panel jobs-panel">
      <div className="panel-heading"><div><span className="eyebrow">Concurrent use</span><h2>Accounts needing review</h2><p>Multiple sessions or IP addresses are indicators for review, not automatic proof of misuse.</p></div></div>
      <div className="jobs-table-wrap"><table className="jobs-table"><thead><tr><th>User</th><th>Active sessions</th><th>IP addresses</th></tr></thead><tbody>
        {data.concurrent_accounts.map((account) => <tr key={account.user_id}><td><strong>{account.display_name}</strong><br /><small>{account.username}</small></td><td>{account.session_count}</td><td>{account.ip_addresses.join(", ") || "Unknown"}</td></tr>)}
      </tbody></table></div>
    </section>}

    <section className="panel jobs-panel">
      <div className="panel-heading system-admin-heading"><div><span className="eyebrow">Session control</span><h2>Login sessions</h2><p>The current session is marked and all revocations are retained in the audit trail.</p></div><label><input type="checkbox" checked={showEnded} onChange={(event) => setShowEnded(event.target.checked)} /> Show ended sessions</label></div>
      <div className="jobs-table-wrap"><table className="jobs-table"><thead><tr><th>User</th><th>Source</th><th>IP / country</th><th>Last seen</th><th>Status</th><th><span className="sr-only">Action</span></th></tr></thead><tbody>
        {sessions.map((session) => <tr key={session.session_key}><td><strong>{session.display_name}</strong><br /><small>{session.username}</small></td><td>{friendlyMethod(session.authentication_method)}<br /><small title={session.user_agent ?? undefined}>{shortAgent(session.user_agent)}</small></td><td>{session.current_ip ?? "Unknown"}{session.country_code ? ` · ${session.country_code}` : ""}</td><td>{formatDate(session.last_seen_at)}</td><td>{session.current ? "Current" : session.active ? "Active" : session.revoked_at ? "Revoked" : "Ended"}</td><td>{session.active && !session.current && <button className="button button--quiet" disabled={busySessionKey === session.session_key} onClick={() => void onRevoke(session.session_key)}>{busySessionKey === session.session_key ? "Revoking…" : "Revoke"}</button>}</td></tr>)}
      </tbody></table></div>
    </section>

    <section className="panel jobs-panel">
      <div className="panel-heading"><div><span className="eyebrow">Audit trail</span><h2>Login activity</h2><p>Recent successful and failed authentication and session events.</p></div></div>
      <div className="jobs-table-wrap"><table className="jobs-table"><thead><tr><th>Time</th><th>User</th><th>Event</th><th>IP address</th><th>Result</th></tr></thead><tbody>
        {data.events.map((event) => <tr key={event.event_id}><td>{formatDate(event.occurred_at)}</td><td>{event.username}</td><td>{friendlyEvent(event.event_type)}</td><td>{event.ip_address ?? "Unknown"}</td><td>{event.success ? "Success" : "Failed"}</td></tr>)}
      </tbody></table></div>
    </section>
    {removingApplication && <ConfirmationDialog title={`Remove ${removingApplication.application_name}?`} description="The application will disappear from active workspaces and user assignments. Oracle itself will not be changed." warning="Historical platform executions and audit evidence are retained. Registering the same environment again can restore the application." confirmLabel="Remove application" tone="danger" busy={busyApplication === removingApplication.application_id} onConfirm={removeApplication} onClose={() => { if (busyApplication === null) setRemovingApplication(null); }} />}
  </section>;
}

function Metric({ label, value, tone, icon }: { label: string; value: number; tone: string; icon: "users" | "alert" | "activity" }) {
  return <article className={`job-metric job-metric--${tone}`}><span><Icon name={icon} /></span><div><strong>{value}</strong><small>{label}</small></div></article>;
}

function formatDate(value: string) { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
function friendlyEvent(value: string) { return value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function friendlyMethod(value: string) { return value === "oracle_basic" ? "Oracle credentials" : value === "oracle_oidc" ? "Oracle SSO" : value === "bootstrap" ? "Initial setup" : "Platform login"; }
function shortAgent(value: string | null) { if (!value) return "Browser unavailable"; return value.length > 55 ? `${value.slice(0, 52)}…` : value; }
