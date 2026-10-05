import { useState, type ReactNode } from "react";

import type { BootstrapResponse } from "../api/types";
import { Icon } from "./Icon";

interface AppShellProps {
  bootstrap: BootstrapResponse;
  activeView: "home" | "tasks" | "cycles" | "approvals" | "notifications" | "access" | "system-administration" | "jobs" | "operations" | "schedules" | "data-review" | "reports" | "assistant" | "fccs-overview" | "fccs-dimensions" | "fccs-jobs" | "fccs-journals";
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
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const user = bootstrap.user!;
  const isHome = activeView === "home";
  const platformHref = platformEntryHref(bootstrap);
  const groups = bootstrap.navigation.reduce<Record<string, typeof bootstrap.navigation>>(
    (result, item) => {
      (result[item.group] ??= []).push(item);
      return result;
    },
    {}
  );

  return (
    <div className={`app-shell${isHome ? " app-shell--home" : ""}${sidebarCollapsed && !isHome ? " app-shell--sidebar-collapsed" : ""}${activeView === "assistant" ? " app-shell--assistant" : ""}`}>
      {!isHome && <>
        <button
          className="mobile-menu"
          aria-label="Open navigation"
          aria-expanded={mobileOpen}
          onClick={() => setMobileOpen(true)}
        >
          <Icon name="menu" />
        </button>
        {mobileOpen && <button className="scrim" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />}
        <aside className={`sidebar${mobileOpen ? " is-open" : ""}${sidebarCollapsed ? " is-collapsed" : ""}`}>
          <div className="sidebar-brand">
            <img src="/static/images/bisp-logo.png" alt="BISP Solutions" />
            <div><strong>EPM AI Assistant</strong><small>{bootstrap.environment?.business_process === "FCCS" ? "Close workspace" : "Planning workspace"}</small></div>
            <button
              className="sidebar-toggle"
              type="button"
              aria-label={sidebarCollapsed ? "Expand navigation" : "Collapse navigation"}
              aria-expanded={!sidebarCollapsed}
              title={sidebarCollapsed ? "Expand navigation" : "Collapse navigation"}
              onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
            ><Icon name="chevron" /></button>
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
                    title={sidebarCollapsed ? item.label : undefined}
                    onClick={() => setMobileOpen(false)}
                  >
                    <span className="nav-icon"><Icon name={navigationIcon(item.code)} /></span>
                    <span className="nav-label">{item.label}</span>
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
      </>}

      <div className={`workspace${isHome ? " workspace--home" : ""}${activeView === "assistant" ? " workspace--assistant" : ""}`}>
        <header className="topbar">
          <div className="topbar-context">
            <span>Oracle EPM</span><Icon name="chevron" /><strong>{viewLabel(activeView)}</strong>
          </div>
          <div className="topbar-actions">
            {isHome && platformHref && <a className="topbar-enter-platform" href={platformHref}>Enter platform <Icon name="arrow" /></a>}
            <a className="icon-button notification-button" aria-label={`${unreadNotifications} unread notifications`} href="#notifications"><Icon name="bell" />{unreadNotifications > 0 && <span>{unreadNotifications > 9 ? "9+" : unreadNotifications}</span>}</a>
            <button className="icon-button" aria-label={`Refresh ${viewLabel(activeView)}`} onClick={onRefresh} disabled={busy}><Icon name="refresh" /></button>
            <div className="user-summary">
              <span className="avatar">{initials(user.display_name)}</span>
              <span><strong>{user.display_name}</strong><small>{user.persona_label}</small></span>
            </div>
            <button className="signout-button" onClick={onLogout} disabled={busy}><Icon name="signout" /> Sign out</button>
          </div>
        </header>
        <main className={`page${activeView === "home" ? " page--home" : ""}`} id={activeView}>{children}</main>
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
    jobs: "activity",
    "system-administration": "users",
    "fccs-overview": "home",
    "fccs-dimensions": "data",
    "fccs-jobs": "activity",
    "fccs-journals": "reports"
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
    "system-administration": "system-administration",
    jobs: "jobs",
    operations: "operations",
    schedules: "schedules",
    "data-review": "data-review",
    reports: "reports",
    assistant: "assistant",
    "fccs-overview": "fccs-overview",
    "fccs-dimensions": "fccs-dimensions",
    "fccs-jobs": "fccs-jobs",
    "fccs-journals": "fccs-journals"
  };
  if (reactViews[code]) return `#${reactViews[code]}`;
  return fallback;
}

function platformEntryHref(bootstrap: BootstrapResponse) {
  const preferredCodes = ["fccs-overview", "tasks", "assistant", "operations", "data-review", "jobs", "schedules"];
  const destination = preferredCodes
    .map((code) => bootstrap.navigation.find((item) => item.code === code))
    .find(Boolean) ?? bootstrap.navigation.find((item) => item.code !== "home");
  return destination ? navigationHref(destination.code, destination.path) : null;
}

function viewLabel(view: AppShellProps["activeView"]) {
  const labels = { home: "Home", tasks: "My Work", cycles: "Planning Cycles", approvals: "Approvals", notifications: "Notifications", access: "Access Control", "system-administration": "System Administration", jobs: "Jobs & Activity", operations: "Operations", schedules: "Schedules", "data-review": "Data Explorer", reports: "Data Explorer", assistant: "EPM Assistant", "fccs-overview": "Close Overview", "fccs-dimensions": "Dimensions", "fccs-jobs": "Oracle Jobs", "fccs-journals": "Journals" };
  return labels[view];
}
