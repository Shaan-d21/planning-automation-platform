import { useState, type ReactNode } from "react";

import type { BootstrapResponse } from "../api/types";
import { Icon } from "./Icon";

interface AppShellProps {
  bootstrap: BootstrapResponse;
  activeView: "home" | "tasks" | "cycles" | "approvals" | "notifications" | "access" | "jobs" | "operations" | "schedules" | "data-review" | "reports" | "assistant";
  children: ReactNode;
  busy: boolean;
  unreadNotifications: number;
  onLogout: () => Promise<void>;
  onRefresh: () => Promise<void>;
}

export function AppShell({
  bootstrap,
  activeView,
  children,
  busy,
  unreadNotifications,
  onLogout,
  onRefresh
}: AppShellProps) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const user = bootstrap.user!;
  const groups = bootstrap.navigation.reduce<Record<string, typeof bootstrap.navigation>>(
    (result, item) => {
      (result[item.group] ??= []).push(item);
      return result;
    },
    {}
  );

  return (
    <div className={`app-shell${activeView === "assistant" ? " app-shell--assistant" : ""}`}>
      <button
        className="mobile-menu"
        aria-label="Open navigation"
        aria-expanded={mobileOpen}
        onClick={() => setMobileOpen(true)}
      >
        <Icon name="menu" />
      </button>
      {mobileOpen && <button className="scrim" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />}
      <aside className={`sidebar${mobileOpen ? " is-open" : ""}`}>
        <div className="sidebar-brand">
          <img src="/static/images/bisp-logo.png" alt="BISP Solutions" />
          <div><strong>EPM Automation</strong><small>Planning workspace</small></div>
          <button className="sidebar-close" onClick={() => setMobileOpen(false)} aria-label="Close navigation"><Icon name="close" /></button>
        </div>
        <nav aria-label="Primary navigation">
          {Object.entries(groups).map(([group, items]) => (
            <div className="nav-group" key={group}>
              <span>{group}</span>
              {items.map((item) => (
                <a
                  className={`nav-link${isActiveNavigation(item.code, activeView) ? " is-active" : ""}`}
                  href={navigationHref(item.code, item.path)}
                  key={item.code}
                  onClick={() => setMobileOpen(false)}
                >
                  <span className="nav-icon"><Icon name={navigationIcon(item.code)} /></span>
                  {item.label}
                </a>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="environment-dot" />
          <div><strong>{bootstrap.environment?.application_name}</strong><small>{bootstrap.environment?.deployment_mode} environment</small></div>
        </div>
      </aside>

      <div className={`workspace${activeView === "assistant" ? " workspace--assistant" : ""}`}>
        <header className="topbar">
          <div className="topbar-context">
            <span>Oracle EPM</span><Icon name="chevron" /><strong>{viewLabel(activeView)}</strong>
          </div>
          <div className="topbar-actions">
            <a className="icon-button notification-button" aria-label={`${unreadNotifications} unread notifications`} href="#notifications"><Icon name="bell" />{unreadNotifications > 0 && <span>{unreadNotifications > 9 ? "9+" : unreadNotifications}</span>}</a>
            <button className="icon-button" aria-label={`Refresh ${viewLabel(activeView)}`} onClick={onRefresh} disabled={busy}><Icon name="refresh" /></button>
            <div className="user-summary">
              <span className="avatar">{initials(user.display_name)}</span>
              <span><strong>{user.display_name}</strong><small>{user.persona_label}</small></span>
            </div>
            <button className="signout-button" onClick={onLogout} disabled={busy}><Icon name="signout" /> Sign out</button>
          </div>
        </header>
        <main className="page" id={activeView}>{children}</main>
      </div>
    </div>
  );
}

function initials(name: string) {
  return name.split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function navigationIcon(code: string) {
  const icons = {
    home: "home",
    tasks: "tasks",
    approvals: "check",
    notifications: "bell",
    "data-review": "data",
    operations: "automation",
    schedules: "clock",
    reports: "reports",
    assistant: "assistant",
    cycles: "calendar",
    "access-control": "users",
    jobs: "activity"
  } as const;
  return icons[code as keyof typeof icons] ?? "chevron";
}

function isActiveNavigation(code: string, view: AppShellProps["activeView"]) {
  return code === view || (code === "access-control" && view === "access");
}

function navigationHref(code: string, fallback: string) {
  const reactViews: Record<string, string> = {
    home: "home",
    tasks: "tasks",
    cycles: "cycles",
    approvals: "approvals",
    notifications: "notifications",
    "access-control": "access",
    jobs: "jobs",
    operations: "operations",
    schedules: "schedules",
    "data-review": "data-review",
    reports: "reports",
    assistant: "assistant"
  };
  if (reactViews[code]) return `#${reactViews[code]}`;
  return fallback;
}

function viewLabel(view: AppShellProps["activeView"]) {
  const labels = { home: "Home", tasks: "My Work", cycles: "Planning Cycles", approvals: "Approvals", notifications: "Notifications", access: "Access Control", jobs: "Jobs & Activity", operations: "Operations", schedules: "Schedules", "data-review": "Data Explorer", reports: "Data Explorer", assistant: "EPM Assistant" };
  return labels[view];
}
