import { useMemo, useState, type FormEvent } from "react";

import type {
  AccessControlResponse,
  IdentitySyncPreview,
  IdentityMappingCatalogResponse,
  IdentityProvisioningPreview,
  PlatformRoleCode,
  PlatformUser,
  PlatformUserCreateInput,
  PlatformUserEditInput
} from "../api/types";
import { Icon } from "./Icon";

interface AccessControlWorkspaceProps {
  data: AccessControlResponse;
  busyUserId: number | "create" | null;
  onCreate: (payload: PlatformUserCreateInput) => Promise<void>;
  onUpdate: (userId: number, payload: PlatformUserEditInput) => Promise<void>;
  onResetPassword: (userId: number, password: string) => Promise<void>;
  identitySyncBusy: boolean;
  identitySyncPreview: IdentitySyncPreview | null;
  onPreviewIdentitySync: () => Promise<void>;
  onApplyIdentitySync: () => Promise<void>;
  onCancelIdentitySync: () => void;
  identityMappings: IdentityMappingCatalogResponse | null;
  identityProvisioningPreview: IdentityProvisioningPreview | null;
  onSetIdentityMapping: (entitlementId: number, roleCode: PlatformRoleCode | null) => Promise<void>;
  onPreviewProvisioning: () => Promise<void>;
  onApplyProvisioning: () => Promise<void>;
  onCancelProvisioning: () => void;
}

type EditorState = {
  userId: number | null;
  username: string;
  displayName: string;
  email: string;
  password: string;
  roleCode: PlatformRoleCode;
  active: boolean;
};

const EMPTY_EDITOR: EditorState = {
  userId: null,
  username: "",
  displayName: "",
  email: "",
  password: "",
  roleCode: "USER",
  active: true
};

export function AccessControlWorkspace({
  data,
  busyUserId,
  onCreate,
  onUpdate,
  onResetPassword,
  identitySyncBusy,
  identitySyncPreview,
  onPreviewIdentitySync,
  onApplyIdentitySync,
  onCancelIdentitySync,
  identityMappings,
  identityProvisioningPreview,
  onSetIdentityMapping,
  onPreviewProvisioning,
  onApplyProvisioning,
  onCancelProvisioning
}: AccessControlWorkspaceProps) {
  const [query, setQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState<PlatformRoleCode | "ALL">("ALL");
  const [statusFilter, setStatusFilter] = useState<"ALL" | "ACTIVE" | "INACTIVE">("ALL");
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [passwordUser, setPasswordUser] = useState<PlatformUser | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [mappingQuery, setMappingQuery] = useState("");
  const [mappingType, setMappingType] = useState<"ALL" | "APPLICATION_ROLE" | "GRANULAR_ROLE" | "GROUP">("ALL");

  const roleByCode = useMemo(
    () => new Map(data.roles.map((role) => [role.code, role])),
    [data.roles]
  );
  const users = useMemo(() => data.users.filter((user) => {
    const needle = query.trim().toLowerCase();
    const matchesText = !needle || [user.display_name, user.username, user.email ?? ""]
      .some((value) => value.toLowerCase().includes(needle));
    const matchesRole = roleFilter === "ALL" || user.role_code === roleFilter;
    const matchesStatus = statusFilter === "ALL" || user.active === (statusFilter === "ACTIVE");
    return matchesText && matchesRole && matchesStatus;
  }), [data.users, query, roleFilter, statusFilter]);

  const activeUsers = data.users.filter((user) => user.active).length;
  const administrators = data.users.filter(
    (user) => user.active && user.role_code === "SERVICE_ADMINISTRATOR"
  ).length;
  const visibleEntitlements = useMemo(() => (identityMappings?.entitlements ?? []).filter((entitlement) => {
    const needle = mappingQuery.trim().toLowerCase();
    return entitlement.active
      && (mappingType === "ALL" || entitlement.entitlement_type === mappingType)
      && (!needle || entitlement.display_name.toLowerCase().includes(needle));
  }).slice(0, 100), [identityMappings, mappingQuery, mappingType]);
  const mappedEntitlements = identityMappings?.entitlements.filter(
    (entitlement) => entitlement.mapping_enabled
  ).length ?? data.identity_sync.mapped_entitlements;
  const synchronized = data.identity_sync.synced_identities > 0;

  function editUser(user: PlatformUser) {
    setEditor({
      userId: user.user_id,
      username: user.username,
      displayName: user.display_name,
      email: user.email ?? "",
      password: "",
      roleCode: user.role_code,
      active: user.active
    });
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!editor) return;
    if (editor.userId === null) {
      await onCreate({
        username: editor.username.trim(),
        display_name: editor.displayName.trim(),
        email: editor.email.trim() || null,
        password: editor.password,
        role_code: editor.roleCode
      });
    } else {
      await onUpdate(editor.userId, {
        display_name: editor.displayName.trim(),
        email: editor.email.trim() || null,
        role_code: editor.roleCode,
        active: editor.active
      });
    }
    setEditor(null);
  }

  async function savePassword(event: FormEvent) {
    event.preventDefault();
    if (!passwordUser) return;
    await onResetPassword(passwordUser.user_id, newPassword);
    setPasswordUser(null);
    setNewPassword("");
  }

  return <section className="access-workspace">
    <header className="page-intro access-intro">
      <div>
        <span className="eyebrow">Administration</span>
        <h1>Access Control</h1>
        <p>Manage who can sign in and what each person can do in the automation platform.</p>
      </div>
      <button className="button button--primary" onClick={() => setEditor({ ...EMPTY_EDITOR })}>
        <Icon name="users" /> Add user
      </button>
    </header>

    <div className="access-summary" aria-label="Access summary">
      <article><span><Icon name="users" /></span><div><strong>{activeUsers}</strong><small>Active users</small></div></article>
      <article><span><Icon name="settings" /></span><div><strong>{administrators}</strong><small>Service Administrators</small></div></article>
      <article><span><Icon name="alert" /></span><div><strong>{data.users.length - activeUsers}</strong><small>Inactive accounts</small></div></article>
      <article><span><Icon name="check" /></span><div><strong>4</strong><small>Standard roles</small></div></article>
    </div>

    <aside className="security-boundary">
      <span><Icon name="data" /></span>
      <div><strong>Platform role controls features—not Oracle data access.</strong><p>Forms, members, entities, and data remain protected by security configured in Oracle EPM Planning.</p></div>
    </aside>

    <section className="panel identity-access-explainer" aria-labelledby="identity-access-title">
      <div>
        <span className="eyebrow">How sign-in works</span>
        <h2 id="identity-access-title">Oracle first, platform accounts second</h2>
        <p>Oracle is the primary sign-in when federation is configured. Existing local platform users remain available through the secondary sign-in option.</p>
      </div>
      <div className="identity-access-modes">
        <article className="is-primary"><span><Icon name="users" /></span><div><strong>Primary · Oracle identity</strong><small>Oracle verifies the password and MFA. This platform never receives the Oracle password.</small></div><em>{data.identity_sync.sso_enabled ? "Ready to test" : "Setup required"}</em></article>
        <article><span><Icon name="settings" /></span><div><strong>Secondary · Platform account</strong><small>Existing platform usernames and passwords continue to work independently.</small></div><em>Available</em></article>
      </div>
    </section>

    <section className="panel identity-sync-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Oracle identity directory</span>
          <h2>Synchronize people and access context</h2>
          <p>Inspect live Cloud EPM users, application roles, granular roles, and group memberships before retaining them here.</p>
        </div>
        <span className={`status-pill ${data.identity_sync.available ? "status-ready" : "status-warning"}`}>
          {data.identity_sync.available ? "Cloud ready" : "Unavailable"}
        </span>
      </div>
      <div className="identity-sync-layout">
        <div className="identity-sync-source">
          <span className="identity-sync-mark"><Icon name="users" /></span>
          <div><strong>{data.identity_sync.provider_name}</strong><p>{data.identity_sync.message}</p><small>{data.identity_sync.active_identities} active of {data.identity_sync.synced_identities} retained identities</small></div>
        </div>
        <div className="identity-sync-guardrails">
          <span><Icon name="check" /> Read-only Oracle inspection</span>
          <span><Icon name="check" /> Preview before persistence</span>
          <span><Icon name="check" /> No platform role changes</span>
        </div>
        <button className="button button--primary" disabled={!data.identity_sync.available || identitySyncBusy} onClick={() => void onPreviewIdentitySync()}>
          {identitySyncBusy ? "Inspecting Oracle…" : "Preview Oracle access"}
        </button>
      </div>
      <div className="identity-setup-steps" aria-label="Oracle account setup steps">
        <article className={synchronized ? "is-complete" : "is-current"}><b>1</b><span><strong>Synchronize Oracle access</strong><small>{synchronized ? `${data.identity_sync.active_identities} active people retained` : "Start with Preview Oracle access"}</small></span></article>
        <article className={mappedEntitlements > 0 ? "is-complete" : synchronized ? "is-current" : ""}><b>2</b><span><strong>Map access to platform roles</strong><small>{mappedEntitlements ? `${mappedEntitlements} Oracle ${mappedEntitlements === 1 ? "entitlement" : "entitlements"} mapped` : "Choose only the roles or groups you need"}</small></span></article>
        <article className={mappedEntitlements > 0 ? "is-current" : ""}><b>3</b><span><strong>Preview and approve accounts</strong><small>One combined review across every saved mapping</small></span></article>
      </div>
      <p className="identity-sync-footnote"><strong>No account is created by synchronization or mapping.</strong> Changes happen only after the final account preview is approved.</p>
      {identityMappings && <details className="identity-mapping-disclosure" open={mappedEntitlements === 0}>
        <summary><span><strong>Step 2 · Map Oracle access to platform roles</strong><small>{mappedEntitlements} mappings configured · each mapping affects everyone who has that Oracle role or group</small></span><Icon name="chevron" /></summary>
        <div className="identity-mapping-content">
          <aside className="dialog-warning"><Icon name="alert" /><span><strong>This is not a per-person role selector.</strong> Mapping an Oracle entitlement assigns the chosen platform role to every synchronized person who has it. If a person matches several mappings, the highest platform role wins.</span></aside>
          <div className="identity-mapping-toolbar">
            <label className="search-field"><Icon name="search" /><input value={mappingQuery} onChange={(event) => setMappingQuery(event.target.value)} placeholder="Search Oracle roles or groups" /></label>
            <label><span>Entitlement type</span><select value={mappingType} onChange={(event) => setMappingType(event.target.value as typeof mappingType)}><option value="ALL">All types</option><option value="APPLICATION_ROLE">Application roles</option><option value="GRANULAR_ROLE">Granular roles</option><option value="GROUP">Groups</option></select></label>
          </div>
          {visibleEntitlements.length ? <div className="identity-mapping-list">{visibleEntitlements.map((entitlement) => <article key={entitlement.entitlement_id}>
            <div><span className="entitlement-type">{entitlement.entitlement_type.replaceAll("_", " ")}</span><strong>{entitlement.display_name}</strong><small>{entitlement.assigned_identity_count} Oracle {entitlement.assigned_identity_count === 1 ? "person has" : "people have"} this access</small>{entitlement.mapped_role && <p className="identity-mapping-impact">All {entitlement.assigned_identity_count} will derive <b>{roleByCode.get(entitlement.mapped_role)?.name ?? entitlement.mapped_role}</b> unless a higher mapped role also applies.</p>}</div>
            <label><span>Role for all {entitlement.assigned_identity_count}</span><select aria-label={`Platform role for ${entitlement.display_name}`} disabled={identitySyncBusy} value={entitlement.mapped_role ?? ""} onChange={(event) => void onSetIdentityMapping(entitlement.entitlement_id, (event.target.value || null) as PlatformRoleCode | null)}><option value="">Do not provision from this access</option>{data.roles.map((role) => <option key={role.code} value={role.code}>{role.name}</option>)}</select></label>
          </article>)}</div> : <div className="work-empty work-empty--compact"><span><Icon name="search" /></span><h3>No entitlements match</h3><p>Change the search or entitlement type.</p></div>}
          {(identityMappings.entitlements.length > 100 && visibleEntitlements.length === 100) && <p className="identity-preview-limit">Showing the first 100 matches. Refine the search to find another entitlement.</p>}
          <div className="identity-provision-action"><div><strong>Step 3 · Review the combined result</strong><p>The preview includes every synchronized person affected by all {mappedEntitlements} saved {mappedEntitlements === 1 ? "mapping" : "mappings"}. Nothing changes until you approve it.</p></div><button className="button button--primary" disabled={identitySyncBusy || mappedEntitlements === 0} onClick={() => void onPreviewProvisioning()}>{identitySyncBusy ? "Preparing…" : "Preview all mapped accounts"}</button></div>
        </div>
      </details>}
    </section>

    <section className="panel access-role-panel">
      <div className="panel-heading"><div><span className="eyebrow">Role model</span><h2>Four roles, one clear responsibility</h2></div><span className="status-pill status-ready">Standardized</span></div>
      <div className="access-role-grid">
        {data.roles.map((role) => <article key={role.code}>
          <span className={`role-mark role-mark--${role.code.toLowerCase().replaceAll("_", "-")}`}><Icon name={role.code === "SERVICE_ADMINISTRATOR" ? "settings" : role.code === "VIEWER" ? "reports" : "users"} /></span>
          <div><h3>{role.name}</h3><p>{role.description}</p><small>{role.permissions.length} platform capabilities</small></div>
        </article>)}
      </div>
    </section>

    <section className="panel access-user-panel">
      <div className="panel-heading"><div><span className="eyebrow">People</span><h2>Platform users</h2><p>Accounts are retained when inactive so audit history remains complete.</p></div><span className="result-count">{users.length} shown</span></div>
      <div className="access-filters">
        <label className="search-field"><Icon name="search" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search name, username, or email" /></label>
        <label><span>Role</span><select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value as PlatformRoleCode | "ALL")}><option value="ALL">All roles</option>{data.roles.map((role) => <option value={role.code} key={role.code}>{role.name}</option>)}</select></label>
        <label><span>Status</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as typeof statusFilter)}><option value="ALL">All accounts</option><option value="ACTIVE">Active</option><option value="INACTIVE">Inactive</option></select></label>
      </div>
      {users.length ? <div className="access-table-wrap"><table className="access-table"><thead><tr><th>User</th><th>Role</th><th>Status</th><th>Last sign in</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>
        {users.map((user) => <tr key={user.user_id}>
          <td><div className="access-person"><span className="avatar">{initials(user.display_name)}</span><div><strong>{user.display_name}{user.user_id === data.current_user_id && <em>You</em>}</strong><span>{user.username}{user.email ? ` · ${user.email}` : ""}</span></div></div></td>
          <td><span className="role-badge">{roleByCode.get(user.role_code)?.name ?? user.role_code}</span></td>
          <td><span className={`account-status account-status--${user.active ? "active" : "inactive"}`}><i />{user.active ? "Active" : "Inactive"}</span></td>
          <td>{user.last_login_at ? formatDate(user.last_login_at) : <span className="muted-value">Never</span>}</td>
          <td><div className="access-row-actions"><button className="button button--quiet" onClick={() => editUser(user)}>Edit</button><button className="icon-button" aria-label={`Reset password for ${user.display_name}`} onClick={() => { setPasswordUser(user); setNewPassword(""); }}><Icon name="settings" /></button></div></td>
        </tr>)}
      </tbody></table></div> : <div className="work-empty work-empty--compact"><span><Icon name="search" /></span><h3>No users match these filters</h3><p>Clear the search or choose a different role or status.</p></div>}
    </section>

    {editor && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setEditor(null); }}><section className="access-dialog" role="dialog" aria-modal="true" aria-labelledby="access-dialog-title">
      <header><div><span className="eyebrow">{editor.userId === null ? "New account" : "Edit account"}</span><h2 id="access-dialog-title">{editor.userId === null ? "Add a platform user" : `Manage ${editor.displayName}`}</h2><p>Assign one primary role. Oracle Planning security continues to control the data this person can see.</p></div><button aria-label="Close" onClick={() => setEditor(null)}><Icon name="close" /></button></header>
      <form onSubmit={save}>
        <div className="access-dialog-grid">
          <label className="field"><span>Display name *</span><input required maxLength={120} value={editor.displayName} onChange={(event) => setEditor({ ...editor, displayName: event.target.value })} /></label>
          <label className="field"><span>Username *</span><input required minLength={3} maxLength={80} disabled={editor.userId !== null} value={editor.username} onChange={(event) => setEditor({ ...editor, username: event.target.value })} /><small>{editor.userId === null ? "Used to sign in to this platform." : "Usernames cannot be changed after creation."}</small></label>
          <label className="field field--wide"><span>Email</span><input type="email" maxLength={254} value={editor.email} onChange={(event) => setEditor({ ...editor, email: event.target.value })} /></label>
          {editor.userId === null && <label className="field field--wide"><span>Temporary password *</span><input required type="password" minLength={12} maxLength={256} autoComplete="new-password" value={editor.password} onChange={(event) => setEditor({ ...editor, password: event.target.value })} /><small>Use at least 12 characters. The password is hashed and never displayed again.</small></label>}
        </div>
        <fieldset className="role-choice"><legend>Primary role *</legend>{data.roles.map((role) => <label className={editor.roleCode === role.code ? "is-selected" : ""} key={role.code}><input type="radio" name="role" checked={editor.roleCode === role.code} onChange={() => setEditor({ ...editor, roleCode: role.code })} /><span><strong>{role.name}</strong><small>{role.description}</small></span></label>)}</fieldset>
        {editor.userId !== null && <label className={`account-toggle${editor.userId === data.current_user_id ? " is-disabled" : ""}`}><input type="checkbox" checked={editor.active} disabled={editor.userId === data.current_user_id} onChange={(event) => setEditor({ ...editor, active: event.target.checked })} /><span><strong>Account active</strong><small>{editor.userId === data.current_user_id ? "You cannot deactivate your current session." : "Inactive users cannot sign in; their audit history is retained."}</small></span></label>}
        <footer><button type="button" className="button button--quiet" onClick={() => setEditor(null)}>Cancel</button><button className="button button--primary" disabled={busyUserId !== null}>{busyUserId !== null ? "Saving…" : editor.userId === null ? "Add user" : "Save changes"}</button></footer>
      </form>
    </section></div>}

    {passwordUser && <div className="modal-backdrop" role="presentation"><section className="access-dialog access-dialog--compact" role="dialog" aria-modal="true" aria-labelledby="password-dialog-title">
      <header><div><span className="eyebrow">Credential reset</span><h2 id="password-dialog-title">Set a new password</h2><p>Replace the platform password for {passwordUser.display_name}. Oracle credentials are not affected.</p></div><button aria-label="Close" onClick={() => setPasswordUser(null)}><Icon name="close" /></button></header>
      <form onSubmit={savePassword}><label className="field"><span>New password *</span><input required type="password" minLength={12} maxLength={256} autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} /><small>Minimum 12 characters.</small></label><aside className="dialog-warning"><Icon name="alert" /><span>The current password stops working immediately after you save.</span></aside><footer><button type="button" className="button button--quiet" onClick={() => setPasswordUser(null)}>Cancel</button><button className="button button--primary" disabled={busyUserId !== null}>{busyUserId !== null ? "Updating…" : "Update password"}</button></footer></form>
    </section></div>}

    {identitySyncPreview && <IdentitySyncPreviewDialog preview={identitySyncPreview} busy={identitySyncBusy} onCancel={onCancelIdentitySync} onApply={onApplyIdentitySync} />}
    {identityProvisioningPreview && <IdentityProvisioningPreviewDialog preview={identityProvisioningPreview} busy={identitySyncBusy} roles={roleByCode} onCancel={onCancelProvisioning} onApply={onApplyProvisioning} />}
  </section>;
}

function IdentitySyncPreviewDialog({ preview, busy, onCancel, onApply }: { preview: IdentitySyncPreview; busy: boolean; onCancel: () => void; onApply: () => Promise<void> }) {
  const changes = preview.identities.filter((identity) => identity.action !== "UNCHANGED");
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (!busy && event.target === event.currentTarget) onCancel(); }}>
    <section className="access-dialog identity-sync-dialog" role="dialog" aria-modal="true" aria-labelledby="identity-sync-title">
      <header><div><span className="eyebrow">Read-only preview</span><h2 id="identity-sync-title">Review Oracle access changes</h2><p>Oracle was inspected at {formatDate(preview.retrieved_at)}. Synchronizing retains this directory evidence but does not create platform accounts or assign platform roles.</p></div><button aria-label="Close" disabled={busy} onClick={onCancel}><Icon name="close" /></button></header>
      <div className="identity-preview-summary">
        <article><strong>{preview.summary.total_seen}</strong><small>Oracle users</small></article>
        <article><strong>{preview.summary.additions}</strong><small>New</small></article>
        <article><strong>{preview.summary.updates}</strong><small>Changed</small></article>
        <article><strong>{preview.summary.deactivations}</strong><small>To deactivate</small></article>
        <article><strong>{preview.summary.unchanged}</strong><small>Unchanged</small></article>
      </div>
      {!preview.complete && <aside className="dialog-warning"><Icon name="alert" /><span>This is a partial snapshot. Missing users will not be deactivated.</span></aside>}
      {preview.warnings.map((warning) => <aside className="dialog-warning" key={warning}><Icon name="alert" /><span>{warning}</span></aside>)}
      <div className="identity-preview-table-wrap">
        {changes.length ? <table className="access-table identity-preview-table"><thead><tr><th>Person</th><th>Change</th><th>Oracle roles</th><th>Groups</th></tr></thead><tbody>{changes.slice(0, 100).map((identity) => <tr key={identity.subject}>
          <td><div className="access-person"><span className="avatar">{initials(identity.display_name)}</span><div><strong>{identity.display_name}</strong><span>{identity.username}</span></div></div></td>
          <td><span className={`identity-change identity-change--${identity.action.toLowerCase()}`}>{identity.action === "DEACTIVATE" ? "Deactivate" : identity.action === "ADD" ? "New" : "Update"}</span></td>
          <td><strong>{identity.application_roles.join(", ") || "—"}</strong>{identity.granular_roles.length ? <small>{identity.granular_roles.length} granular roles</small> : null}</td>
          <td>{identity.groups.length ? `${identity.groups.slice(0, 3).join(", ")}${identity.groups.length > 3 ? ` +${identity.groups.length - 3}` : ""}` : "—"}</td>
        </tr>)}</tbody></table> : <div className="work-empty work-empty--compact"><span><Icon name="check" /></span><h3>Everything is already synchronized</h3><p>No external identity changes were found.</p></div>}
        {changes.length > 100 && <p className="identity-preview-limit">Showing the first 100 of {changes.length} changes.</p>}
      </div>
      <footer><button type="button" className="button button--quiet" disabled={busy} onClick={onCancel}>Cancel</button><button type="button" className="button button--primary" disabled={busy} onClick={() => void onApply()}>{busy ? "Verifying Oracle…" : changes.length ? "Synchronize reviewed access" : "Confirm synchronized state"}</button></footer>
    </section>
  </div>;
}

function IdentityProvisioningPreviewDialog({ preview, busy, roles, onCancel, onApply }: { preview: IdentityProvisioningPreview; busy: boolean; roles: Map<PlatformRoleCode, AccessControlResponse["roles"][number]>; onCancel: () => void; onApply: () => Promise<void> }) {
  const reviewed = preview.entries.filter((entry) => entry.action !== "UNCHANGED");
  const actionable = preview.summary.creates + preview.summary.updates + preview.summary.deactivations;
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (!busy && event.target === event.currentTarget) onCancel(); }}>
    <section className="access-dialog identity-sync-dialog" role="dialog" aria-modal="true" aria-labelledby="identity-provision-title">
      <header><div><span className="eyebrow">Governed account provisioning</span><h2 id="identity-provision-title">Review all mapped account changes</h2><p>This is one combined preview across every saved Oracle-to-platform mapping. It is not a preview of only the last role you selected.</p></div><button aria-label="Close" disabled={busy} onClick={onCancel}><Icon name="close" /></button></header>
      <div className="identity-preview-summary identity-preview-summary--six">
        <article><strong>{preview.summary.creates}</strong><small>Create</small></article><article><strong>{preview.summary.updates}</strong><small>Update</small></article><article><strong>{preview.summary.deactivations}</strong><small>Deactivate</small></article><article><strong>{preview.summary.conflicts}</strong><small>Conflicts</small></article><article><strong>{preview.summary.unmapped}</strong><small>Unmapped</small></article><article><strong>{preview.summary.unchanged}</strong><small>Unchanged</small></article>
      </div>
      {preview.summary.conflicts > 0 && <aside className="dialog-warning"><Icon name="alert" /><span>{preview.summary.conflicts} conflicting local {preview.summary.conflicts === 1 ? "account is" : "accounts are"} protected and will be skipped. Existing credentials are never overwritten.</span></aside>}
      {reviewed.length > 0 && <aside className="identity-preview-explainer"><Icon name="users" /><span><strong>Why can several people receive the same role?</strong> The table shows the Oracle access that matched each person. If six people share an entitlement and that entitlement maps to Power User, all six derive Power User.</span></aside>}
      <div className="identity-preview-table-wrap">
        {reviewed.length ? <table className="access-table identity-preview-table"><thead><tr><th>Person</th><th>Action</th><th>Platform role from Oracle access</th><th>Reason</th></tr></thead><tbody>{reviewed.slice(0, 100).map((entry) => <tr key={entry.external_identity_id}>
          <td><div className="access-person"><span className="avatar">{initials(entry.display_name)}</span><div><strong>{entry.display_name}</strong><span>{entry.username}</span></div></div></td>
          <td><span className={`identity-change identity-change--${entry.action.toLowerCase().replaceAll("_", "-")}`}>{entry.action.replaceAll("_", " ")}</span></td>
          <td><strong>{entry.target_role ? roles.get(entry.target_role)?.name ?? entry.target_role : "No role"}</strong>{entry.matched_entitlements.length ? <small>Matched: {entry.matched_entitlements.slice(0, 2).join(", ")}{entry.matched_entitlements.length > 2 ? ` +${entry.matched_entitlements.length - 2}` : ""}</small> : null}</td>
          <td>{entry.explanation}</td>
        </tr>)}</tbody></table> : <div className="work-empty work-empty--compact"><span><Icon name="check" /></span><h3>Accounts already match</h3><p>No provisioning changes are required.</p></div>}
      </div>
      <footer><button type="button" className="button button--quiet" disabled={busy} onClick={onCancel}>Cancel</button><button type="button" className="button button--primary" disabled={busy || actionable === 0} onClick={() => void onApply()}>{busy ? "Verifying state…" : `Approve ${actionable} account ${actionable === 1 ? "change" : "changes"}`}</button></footer>
    </section>
  </div>;
}

function initials(name: string) {
  return name.split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
