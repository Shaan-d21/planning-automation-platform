import { useState } from "react";

import type { NotificationsResponse, UserNotification } from "../api/types";
import { Icon } from "./Icon";

interface NotificationsWorkspaceProps {
  data: NotificationsResponse;
  busy: boolean;
  onRead: (notificationId: number) => Promise<void>;
  onReadAll: () => Promise<void>;
}

export function NotificationsWorkspace({ data, busy, onRead, onReadAll }: NotificationsWorkspaceProps) {
  const [unreadOnly, setUnreadOnly] = useState(false);
  const notifications = unreadOnly ? data.notifications.filter((item) => !item.read_at) : data.notifications;
  return <section className="notifications-workspace">
    <header className="page-intro">
      <div><span className="eyebrow">Your Planning inbox</span><h1>Notifications</h1><p>Important assignments, approval decisions, deadlines, and operational exceptions—kept separate from technical logs.</p></div>
      {data.unread_count > 0 && <button className="button button--quiet" disabled={busy} onClick={onReadAll}><Icon name="check" /> Mark all read</button>}
    </header>
    <section className="panel notification-panel">
      <div className="work-list-heading"><div><span className="eyebrow">Inbox</span><h2>{data.unread_count} unread</h2></div><label className="notification-toggle"><input type="checkbox" checked={unreadOnly} onChange={(event) => setUnreadOnly(event.target.checked)} /><span>Unread only</span></label></div>
      {notifications.length ? <div className="notification-list">{notifications.map((item) => <NotificationRow key={item.notification_id} item={item} busy={busy} onRead={onRead} />)}</div> : <div className="work-empty work-empty--compact"><span><Icon name="check" /></span><h2>{unreadOnly ? "No unread notifications" : "Your inbox is clear"}</h2><p>Only meaningful Planning events are shown here. Execution details remain in History.</p></div>}
    </section>
  </section>;
}

function NotificationRow({ item, busy, onRead }: { item: UserNotification; busy: boolean; onRead: (notificationId: number) => Promise<void> }) {
  const unread = !item.read_at;
  return <article className={`notification-row notification-row--${item.severity.toLowerCase()}${unread ? " is-unread" : ""}`}>
    <span className="notification-row__icon"><Icon name={severityIcon(item.severity)} /></span>
    <div className="notification-row__content"><div><h3>{item.title}</h3>{unread && <span>New</span>}</div><p>{item.message}</p><small>{relativeTime(item.created_at)}</small></div>
    <div className="notification-row__actions">{item.action_url && <a className="button button--quiet" href={item.action_url} onClick={() => unread && onRead(item.notification_id)}>Open</a>}{unread && <button className="notification-read" aria-label={`Mark ${item.title} as read`} disabled={busy} onClick={() => onRead(item.notification_id)}><Icon name="check" /></button>}</div>
  </article>;
}

function severityIcon(severity: UserNotification["severity"]): "activity" | "check" | "alert" {
  if (severity === "SUCCESS") return "check";
  if (severity === "WARNING" || severity === "ERROR") return "alert";
  return "activity";
}

function relativeTime(value: string) {
  const minutes = Math.round((new Date(value).getTime() - Date.now()) / 60_000);
  if (Math.abs(minutes) < 60) return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(hours, "hour");
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}
