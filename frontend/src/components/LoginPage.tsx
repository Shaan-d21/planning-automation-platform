import { useState, type FormEvent } from "react";

import type { InitialAdministratorInput } from "../api/types";
import { Icon } from "./Icon";

interface LoginPageProps {
  productName: string;
  company: string;
  busy: boolean;
  error: string | null;
  requiresBootstrap: boolean;
  identityAuthentication: {
    federated_enabled: boolean;
    provider_name: string;
    login_url: string | null;
    local_recovery_enabled: boolean;
  };
  onLogin: (username: string, password: string) => Promise<void>;
  onBootstrap: (input: InitialAdministratorInput) => Promise<void>;
}

export function LoginPage({
  productName,
  company,
  busy,
  error,
  requiresBootstrap,
  identityAuthentication,
  onLogin,
  onBootstrap
}: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [showLocalLogin, setShowLocalLogin] = useState(
    !identityAuthentication.federated_enabled
  );

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onLogin(username, password);
  }

  async function submitBootstrap(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onBootstrap({
      username,
      display_name: displayName,
      email: email.trim() || null,
      password,
      password_confirmation: passwordConfirmation
    });
  }

  return (
    <main className="login-page">
      <section className="login-story" aria-label="Product introduction">
        <div className="brand brand--inverse">
          <span className="brand-logo"><img src="/static/images/bisp-logo.png" alt="" /></span>
          <span><strong>{company}</strong><small>EPM Automation</small></span>
        </div>
        <div className="login-story__content">
          <span className="eyebrow eyebrow--light">Planning, orchestrated</span>
          <h1>Move every Planning cycle forward with clarity.</h1>
          <p>One governed workspace for Oracle EPM operations, business tasks, cycle progress, and management by exception.</p>
          <div className="login-benefits">
            <div><Icon name="tasks" /><span><strong>Know what is next</strong><small>Role-aware priorities and dependencies</small></span></div>
            <div><Icon name="activity" /><span><strong>See the whole cycle</strong><small>From actual load through reporting</small></span></div>
            <div><Icon name="check" /><span><strong>Act with confidence</strong><small>Governed execution and durable audit</small></span></div>
          </div>
        </div>
        <p className="login-story__footer">Built by BISP Solutions</p>
      </section>

      <section className="login-panel">
        <div className="login-card">
          <span className="eyebrow">Secure platform access</span>
          <h2>Welcome back</h2>
          <p className="muted">Sign in to continue to {productName}.</p>

          {requiresBootstrap ? (
            <div className="setup-state">
              <Icon name="alert" />
              <div>
                <strong>Initial setup is required</strong>
                <p>Create the first Platform Administrator before signing in.</p>
                {error && <div className="inline-error login-error" role="alert">{error}</div>}
                <form className="login-form" onSubmit={submitBootstrap}>
                  <label><span>Display name</span><input autoComplete="name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></label>
                  <label><span>Username</span><input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required minLength={3} /></label>
                  <label><span>Email <small>(optional)</small></span><input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label>
                  <label><span>Password</span><input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} required minLength={12} /></label>
                  <label><span>Confirm password</span><input type="password" autoComplete="new-password" value={passwordConfirmation} onChange={(event) => setPasswordConfirmation(event.target.value)} required minLength={12} /></label>
                  <button className="button button--primary button--wide" disabled={busy || password !== passwordConfirmation}>
                    {busy ? <span className="spinner" /> : null}
                    {busy ? "Creating administrator…" : "Create Platform Administrator"}
                  </button>
                </form>
              </div>
            </div>
          ) : <>
            {identityAuthentication.federated_enabled && identityAuthentication.login_url && <div className="federated-login">
              <a className="button button--primary button--wide federated-login__button" href={identityAuthentication.login_url}>
                <Icon name="users" /> Sign in with Oracle
              </a>
              <p>Oracle opens its secure sign-in page. Your Oracle password is never sent to this platform.</p>
            </div>}
            {error && <div className="inline-error login-error" role="alert">{error}</div>}
            {identityAuthentication.federated_enabled && identityAuthentication.local_recovery_enabled && <div className="login-divider"><span>Secondary sign-in</span></div>}
            {identityAuthentication.federated_enabled && identityAuthentication.local_recovery_enabled && !showLocalLogin && <button type="button" className="button button--quiet button--wide local-login-toggle" onClick={() => setShowLocalLogin(true)}>Use an existing platform account</button>}
            {showLocalLogin && <form className="login-form" onSubmit={submit}>
              <label><span>Username</span><input autoComplete="username" autoFocus={!identityAuthentication.federated_enabled} value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
              <label><span>Password</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
              <button className="button button--primary button--wide" disabled={busy}>
                {busy ? <span className="spinner" /> : null}
                {busy ? "Signing in…" : identityAuthentication.federated_enabled ? "Sign in with local account" : "Sign in securely"}
              </button>
            </form>}
          </>}
          {!requiresBootstrap && <p className="login-help">The secondary form accepts only an existing platform username and password. Never enter Oracle credentials there.</p>}
        </div>
      </section>
    </main>
  );
}
