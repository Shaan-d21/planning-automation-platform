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
    oracle_credentials_enabled: boolean;
    provider_name: string;
    login_url: string | null;
    local_recovery_enabled: boolean;
  };
  onLogin: (username: string, password: string) => Promise<void>;
  onOracleLogin: (username: string, password: string) => Promise<void>;
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
  onOracleLogin,
  onBootstrap
}: LoginPageProps) {
  const [localUsername, setLocalUsername] = useState("");
  const [localPassword, setLocalPassword] = useState("");
  const [oracleUsername, setOracleUsername] = useState("");
  const [oraclePassword, setOraclePassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");

  async function submitLocal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onLogin(localUsername, localPassword);
  }

  async function submitOracle(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onOracleLogin(oracleUsername, oraclePassword);
  }

  async function submitBootstrap(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await onBootstrap({
      username: localUsername,
      display_name: displayName,
      email: email.trim() || null,
      password: localPassword,
      password_confirmation: passwordConfirmation
    });
  }

  const oracleSignInAvailable = identityAuthentication.federated_enabled
    || identityAuthentication.oracle_credentials_enabled;
  const platformSignInAvailable = identityAuthentication.local_recovery_enabled;

  const oracleSignIn = <>
    {identityAuthentication.federated_enabled && identityAuthentication.login_url && <div className="federated-login">
      <a className="button button--quiet button--wide federated-login__button" href={identityAuthentication.login_url}>
        <Icon name="users" /> Continue with Oracle SSO
      </a>
      <p>Oracle opens its secure sign-in page. Your Oracle password is never sent to this platform.</p>
    </div>}
    {identityAuthentication.federated_enabled && identityAuthentication.oracle_credentials_enabled && <div className="login-divider"><span>or use Oracle EPM credentials</span></div>}
    {identityAuthentication.oracle_credentials_enabled && <form className="login-form login-form--oracle" onSubmit={submitOracle}>
      <label><span>Oracle EPM username</span><input autoComplete="username" autoFocus={!platformSignInAvailable && !identityAuthentication.federated_enabled} value={oracleUsername} onChange={(event) => setOracleUsername(event.target.value)} required /></label>
      <label><span>Oracle EPM password</span><input type="password" autoComplete="current-password" value={oraclePassword} onChange={(event) => setOraclePassword(event.target.value)} required /></label>
      <button className="button button--quiet button--wide" disabled={busy}>
        {busy ? <span className="spinner spinner--blue" /> : null}
        {busy ? "Verifying with Oracle…" : "Sign in with Oracle EPM"}
      </button>
      <p className="login-form-note">Your credentials are validated directly against Oracle EPM and are not stored by this platform.</p>
    </form>}
  </>;

  return (
    <main className="login-page">
      <section className="login-story" aria-label="Product introduction">
        <div className="login-story__glow login-story__glow--one" />
        <div className="login-story__glow login-story__glow--two" />
        <div className="brand brand--inverse">
          <span className="brand-logo"><img src="/static/images/bisp-logo.png" alt="" /></span>
          <span><strong>{company}</strong><small>EPM AI Assistant</small></span>
        </div>
        <div className="login-story__content">
          <div className="login-story__copy">
            <span className="eyebrow eyebrow--light"><Icon name="sparkle" /> Governed EPM intelligence</span>
            <h1>Your Planning cycle. <span>Guided by AI.</span></h1>
            <p>Ask in plain language, work with live Oracle EPM context, and move from recommendation to governed execution—all in one workspace.</p>
            <div className="login-benefits">
              <div><Icon name="assistant" /><span><strong>Ask naturally</strong><small>Describe the outcome, not the navigation</small></span></div>
              <div><Icon name="data" /><span><strong>Use live context</strong><small>Grounded in your Planning environment</small></span></div>
              <div><Icon name="check" /><span><strong>Stay in control</strong><small>Review and approve before anything runs</small></span></div>
            </div>
          </div>

          <div className="login-ai-map" aria-hidden="true">
            <span className="login-ai-ring login-ai-ring--outer" />
            <span className="login-ai-ring login-ai-ring--inner" />
            <span className="login-ai-path login-ai-path--one" />
            <span className="login-ai-path login-ai-path--two" />
            <span className="login-ai-path login-ai-path--three" />

            <div className="login-ai-core">
              <span className="login-ai-core__pulse" />
              <span className="login-ai-core__icon"><Icon name="assistant" /></span>
              <strong>EPM Assistant</strong>
              <small>Understanding your request</small>
            </div>

            <div className="login-ai-node login-ai-node--data"><Icon name="data" /><span>Live data</span></div>
            <div className="login-ai-node login-ai-node--tasks"><Icon name="tasks" /><span>Cycle tasks</span></div>
            <div className="login-ai-node login-ai-node--automation"><Icon name="automation" /><span>Oracle operations</span></div>
            <div className="login-ai-node login-ai-node--governance"><Icon name="check" /><span>Approval</span></div>

            <div className="login-ai-status"><Icon name="sparkle" /><span><small>AI recommendation</small><strong>Governed action ready</strong></span></div>
          </div>
        </div>
        <p className="login-story__footer"><span /> Secure by design · Built by BISP Solutions</p>
      </section>

      <section className="login-panel">
        <div className="login-card">
          <div className="login-card__intro">
            <span className="login-card__assistant"><Icon name="assistant" /></span>
            <div>
              <span className="eyebrow">Secure AI workspace</span>
              <h2>Welcome back</h2>
              <p className="muted">Sign in to continue to {productName}.</p>
            </div>
          </div>

          {requiresBootstrap ? (
            <div className="setup-state">
              <Icon name="alert" />
              <div>
                <strong>Initial setup is required</strong>
                <p>Create the first Platform Administrator before signing in.</p>
                {error && <div className="inline-error login-error" role="alert">{error}</div>}
                <form className="login-form" onSubmit={submitBootstrap}>
                  <label><span>Display name</span><input autoComplete="name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></label>
                  <label><span>Username</span><input autoComplete="username" value={localUsername} onChange={(event) => setLocalUsername(event.target.value)} required minLength={3} /></label>
                  <label><span>Email <small>(optional)</small></span><input type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label>
                  <label><span>Password</span><input type="password" autoComplete="new-password" value={localPassword} onChange={(event) => setLocalPassword(event.target.value)} required minLength={12} /></label>
                  <label><span>Confirm password</span><input type="password" autoComplete="new-password" value={passwordConfirmation} onChange={(event) => setPasswordConfirmation(event.target.value)} required minLength={12} /></label>
                  <button className="button button--primary button--wide" disabled={busy || localPassword !== passwordConfirmation}>
                    {busy ? <span className="spinner" /> : null}
                    {busy ? "Creating administrator…" : "Create Platform Administrator"}
                  </button>
                </form>
              </div>
            </div>
          ) : <>
            {error && <div className="inline-error login-error" role="alert">{error}</div>}
            {platformSignInAvailable && <form className="login-form login-form--primary" onSubmit={submitLocal}>
              <label><span>Username</span><input autoComplete="username" autoFocus value={localUsername} onChange={(event) => setLocalUsername(event.target.value)} required /></label>
              <label><span>Password</span><input type="password" autoComplete="current-password" value={localPassword} onChange={(event) => setLocalPassword(event.target.value)} required /></label>
              <button className="button button--primary button--wide" disabled={busy}>
                {busy ? <span className="spinner" /> : null}
                {busy ? "Signing in…" : "Sign in"}
              </button>
              <p className="login-form-note">Use the platform account provided by your administrator.</p>
            </form>}
            {platformSignInAvailable && oracleSignInAvailable && <div className="login-divider"><span>Other sign-in options</span></div>}
            {platformSignInAvailable && oracleSignInAvailable && <details className="alternate-login">
              <summary><span><Icon name="users" /> Use Oracle sign-in</span><Icon name="chevron" /></summary>
              <div className="alternate-login__body">{oracleSignIn}</div>
            </details>}
            {!platformSignInAvailable && oracleSignInAvailable && <div className="alternate-login__body alternate-login__body--only">{oracleSignIn}</div>}
          </>}
        </div>
      </section>
    </main>
  );
}
