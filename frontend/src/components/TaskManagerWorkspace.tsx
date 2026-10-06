import { useMemo, useState } from "react";

import type { TaskManagerSnapshotResponse, TaskManagerSyncInput, TaskManagerTask } from "../api/types";
import { Icon } from "./Icon";

interface Props {
  data: TaskManagerSnapshotResponse;
  busy: boolean;
  onSaveConfiguration: (input: TaskManagerSyncInput) => Promise<void>;
  onSynchronize: () => Promise<void>;
}

interface ParameterRow { id: number; name: string; value: string; }

export function TaskManagerWorkspace({ data, busy, onSaveConfiguration, onSynchronize }: Props) {
  const [search, setSearch] = useState("");
  const [schedule, setSchedule] = useState("ALL");
  const [status, setStatus] = useState("ALL");
  const [configurationOpen, setConfigurationOpen] = useState(false);
  const schedules = useMemo(() => unique(data.tasks.map((task) => task.schedule_name)), [data.tasks]);
  const statuses = useMemo(() => unique(data.tasks.map((task) => task.status)), [data.tasks]);
  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return data.tasks.filter((task) => {
      if (schedule !== "ALL" && task.schedule_name !== schedule) return false;
      if (status !== "ALL" && task.status !== status) return false;
      if (!query) return true;
      return [task.name, task.external_id, task.schedule_name, task.period_name, task.owner, task.assignee, task.organization]
        .some((value) => value?.toLocaleLowerCase().includes(query));
    });
  }, [data.tasks, schedule, search, status]);
  const grouped = useMemo(() => groupBySchedule(filtered), [filtered]);
  const actionRequired = data.tasks.filter((task) => ["OPEN", "NOT STARTED", "IN PROGRESS", "LATE", "OVERDUE"].includes((task.status ?? "").toLocaleUpperCase())).length;
  const completed = data.tasks.filter((task) => ["COMPLETED", "CLOSED", "APPROVED"].includes((task.status ?? "").toLocaleUpperCase())).length;

  return <main className="page task-manager-page">
    <header className="page-intro task-manager-page-header">
      <div><span className="eyebrow">Oracle Planning · Task Manager</span><h1>Task Manager</h1><p>Review the latest synchronized Oracle schedules and tasks in one structured workspace. This remains separate from Planning Cycles.</p></div>
      <div className="task-manager-header-actions">
        <SyncState data={data} />
        {data.can_sync && <div>
          <button className="button button--quiet" type="button" disabled={busy} onClick={() => setConfigurationOpen(true)}><Icon name="settings" />Configure source</button>
          <button className="button button--primary" type="button" disabled={busy || !data.configuration} onClick={() => void onSynchronize()}><Icon name="refresh" />{busy ? "Synchronizing…" : "Synchronize now"}</button>
        </div>}
      </div>
    </header>

    <section className="attention-strip task-manager-metrics" aria-label="Task summary">
      <Metric icon="tasks" value={data.summary.task_count} label="Synchronized tasks" />
      <Metric icon="calendar" value={data.summary.schedule_count} label="Schedules" />
      <Metric icon="alert" value={actionRequired} label="Action required" tone="warning" />
      <Metric icon="check" value={completed} label="Completed" tone="success" />
    </section>

    {data.can_sync && !data.configuration && <section className="task-manager-onboarding">
      <span><Icon name="settings" /></span><div><span className="eyebrow">One-time administrator setup</span><h2>Connect the Oracle Task Manager report</h2><p>Configure the report that contains the schedules and tasks you want shown here. After setup, future refreshes use one click.</p></div>
      <button className="button button--primary" type="button" onClick={() => setConfigurationOpen(true)}>Configure source<Icon name="arrow" /></button>
    </section>}

    {data.configuration && <section className="task-manager-source-summary" aria-label="Synchronization source">
      <span><Icon name="check" /></span><div><small>Oracle synchronization source</small><strong>{data.configuration.report_group} · {data.configuration.report_name}</strong></div><em>CSV processed in memory · source file not retained</em>
    </section>}
    {data.sync?.last_sync_status === "FAILED" && data.sync.last_error && <div className="feedback-banner feedback-banner--error task-manager-sync-error"><Icon name="alert" /><span><strong>Last synchronization failed.</strong> {data.sync.last_error}</span></div>}

    <section className="panel task-manager-list-panel">
      <div className="panel-heading task-manager-list-heading"><div><span className="eyebrow">Current snapshot</span><h2>Schedules and tasks</h2><p>{filtered.length} of {data.tasks.length} tasks shown.</p></div>
        <div className="task-manager-filters">
          <label className="task-manager-search"><Icon name="search" /><input aria-label="Search tasks" placeholder="Search task, owner, period…" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
          <select aria-label="Filter by schedule" value={schedule} onChange={(event) => setSchedule(event.target.value)}><option value="ALL">All schedules</option>{schedules.map((item) => <option key={item} value={item}>{item}</option>)}</select>
          <select aria-label="Filter by status" value={status} onChange={(event) => setStatus(event.target.value)}><option value="ALL">All statuses</option>{statuses.map((item) => <option key={item} value={item}>{item}</option>)}</select>
        </div>
      </div>
      {!data.tasks.length ? <div className="empty-state"><span><Icon name="tasks" /></span><div><strong>No synchronized Task Manager tasks yet</strong><p>{data.can_sync ? data.configuration ? "Select Synchronize now to retrieve the configured Oracle task report." : "Complete the one-time source setup, then synchronize from Oracle." : "A Service Administrator must configure and synchronize the Oracle task report."}</p></div></div>
        : !filtered.length ? <div className="empty-state empty-state--compact"><span><Icon name="search" /></span><div><strong>No matching tasks</strong><p>Change the search or filters.</p></div></div>
          : <div className="task-manager-schedules">{Array.from(grouped.entries()).map(([name, tasks]) => <ScheduleGroup key={name} name={name} tasks={tasks} />)}</div>}
    </section>

    {configurationOpen && <TaskManagerConfigurationDialog data={data} busy={busy} onClose={() => setConfigurationOpen(false)} onSaveConfiguration={onSaveConfiguration} onSynchronize={onSynchronize} />}
  </main>;
}

function TaskManagerConfigurationDialog({ data, busy, onClose, onSaveConfiguration, onSynchronize }: Props & { onClose: () => void }) {
  const [reportGroup, setReportGroup] = useState(data.configuration?.report_group ?? "");
  const [reportName, setReportName] = useState(data.configuration?.report_name ?? "");
  const [parameters, setParameters] = useState<ParameterRow[]>(() => {
    const configured = Object.entries(data.configuration?.parameters ?? {});
    return configured.length ? configured.map(([name, value], index) => ({ id: index + 1, name, value })) : [{ id: 1, name: "", value: "" }];
  });
  async function saveAndSynchronize() {
    const mapped = Object.fromEntries(parameters.map((item) => [item.name.trim(), item.value.trim()] as const).filter(([name, value]) => name && value));
    await onSaveConfiguration({ report_group: reportGroup.trim(), report_name: reportName.trim(), parameters: mapped });
    await onSynchronize();
    onClose();
  }
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (!busy && event.target === event.currentTarget) onClose(); }}>
    <section className="access-dialog task-manager-config-dialog" role="dialog" aria-modal="true" aria-labelledby="task-manager-config-title">
      <header><div><span className="eyebrow">Administrator setup</span><h2 id="task-manager-config-title">Configure Task Manager source</h2><p>Connect one Oracle report that represents the task list this workspace should display.</p></div><button type="button" aria-label="Close Task Manager configuration" disabled={busy} onClick={onClose}><Icon name="close" /></button></header>
      <div className="task-manager-config-dialog__body">
        <aside className="task-manager-config-guidance"><Icon name="tasks" /><span><strong>The report determines what is synchronized.</strong> Use an Oracle Task Manager report containing every schedule, task, status, owner, assignee, period, and date required by your team. The generated CSV is parsed in memory and never stored.</span></aside>
        <div className="task-manager-config-grid">
          <label><span>Report group *</span><input value={reportGroup} onChange={(event) => setReportGroup(event.target.value)} placeholder="Exact Oracle report group" /></label>
          <label><span>Report name *</span><input value={reportName} onChange={(event) => setReportName(event.target.value)} placeholder="Exact Task Manager report name" /></label>
        </div>
        <div className="task-manager-parameters">
          <div className="task-manager-parameters__heading"><div><strong>Report parameters</strong><small>Add every parameter required by this Oracle report.</small></div><button className="button button--quiet" type="button" onClick={() => setParameters((current) => [...current, { id: Math.max(0, ...current.map((item) => item.id)) + 1, name: "", value: "" }])}>Add parameter</button></div>
          {parameters.map((parameter) => <div className="task-manager-parameter-row" key={parameter.id}>
            <input aria-label="Parameter name" placeholder="Parameter name" value={parameter.name} onChange={(event) => setParameters((current) => current.map((item) => item.id === parameter.id ? { ...item, name: event.target.value } : item))} />
            <input aria-label="Parameter value" placeholder="Parameter value" value={parameter.value} onChange={(event) => setParameters((current) => current.map((item) => item.id === parameter.id ? { ...item, value: event.target.value } : item))} />
            <button className="icon-button" type="button" aria-label="Remove parameter" onClick={() => setParameters((current) => current.length === 1 ? [{ ...current[0], name: "", value: "" }] : current.filter((item) => item.id !== parameter.id))}><Icon name="close" /></button>
          </div>)}
        </div>
      </div>
      <footer><button className="button button--quiet" type="button" disabled={busy} onClick={onClose}>Cancel</button><button className="button button--primary" type="button" disabled={busy || !reportGroup.trim() || !reportName.trim()} onClick={() => void saveAndSynchronize()}><Icon name="refresh" />{busy ? "Working…" : "Save and synchronize"}</button></footer>
    </section>
  </div>;
}

function SyncState({ data }: { data: TaskManagerSnapshotResponse }) {
  const syncedAt = data.sync?.last_synced_at;
  return <div className={`task-manager-sync-state ${data.sync?.last_sync_status === "FAILED" ? "is-error" : ""}`}><span><Icon name={data.sync?.last_sync_status === "FAILED" ? "alert" : "refresh"} /></span><div><small>Last synchronized</small><strong>{syncedAt ? formatDateTime(syncedAt) : "Not synchronized"}</strong></div></div>;
}

function Metric({ icon, value, label, tone = "brand" }: { icon: "tasks" | "calendar" | "alert" | "check"; value: number; label: string; tone?: string }) {
  return <article className={`summary-metric summary-metric--${tone}`}><span className="summary-icon"><Icon name={icon} /></span><span><strong>{value}</strong><small>{label}</small></span></article>;
}

function ScheduleGroup({ name, tasks }: { name: string; tasks: TaskManagerTask[] }) {
  const completed = tasks.filter((task) => ["COMPLETED", "CLOSED", "APPROVED"].includes((task.status ?? "").toLocaleUpperCase())).length;
  return <section className="task-manager-schedule"><header><div><span><Icon name="calendar" /></span><div><h3>{name}</h3><p>{tasks.length} tasks</p></div></div><strong>{completed}/{tasks.length} completed</strong></header><div className="task-manager-table-wrap"><table className="task-manager-table"><thead><tr><th>Task</th><th>Period</th><th>Owner / assignee</th><th>Due</th><th>Status</th></tr></thead><tbody>{tasks.map((task) => <tr key={task.source_key}><td><details><summary><strong>{task.name}</strong><small>{task.task_type ?? task.external_id ?? "Task"}</small></summary><TaskDetails task={task} /></details></td><td>{task.period_name ?? "—"}</td><td><strong>{task.assignee ?? task.owner ?? "—"}</strong>{task.approver && <small>Approver: {task.approver}</small>}</td><td>{task.due_at ? formatDateTime(task.due_at) : "—"}</td><td><span className={`task-manager-status ${statusClass(task.status)}`}>{task.status ?? "Unspecified"}</span></td></tr>)}</tbody></table></div></section>;
}

function TaskDetails({ task }: { task: TaskManagerTask }) {
  const details = [["Description", task.description], ["Organization", task.organization], ["Parent task", task.parent_task], ["Dependency", task.dependency], ["Start", task.start_at ? formatDateTime(task.start_at) : null], ["Completed", task.completed_at ? formatDateTime(task.completed_at) : null], ["Priority", task.priority]].filter((item): item is string[] => Boolean(item[1]));
  if (!details.length) return <p className="task-manager-no-details">No additional details were supplied.</p>;
  return <dl className="task-manager-details">{details.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

function groupBySchedule(tasks: TaskManagerTask[]) { const groups = new Map<string, TaskManagerTask[]>(); tasks.forEach((task) => { const name = task.schedule_name || "Unassigned schedule"; groups.set(name, [...(groups.get(name) ?? []), task]); }); return groups; }
function unique(values: Array<string | null>) { return Array.from(new Set(values.filter((value): value is string => Boolean(value)))).sort(); }
function formatDateTime(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date); }
function statusClass(value: string | null) { const status = (value ?? "").toLocaleLowerCase(); if (status.includes("complete") || status.includes("closed") || status.includes("approved")) return "is-success"; if (status.includes("late") || status.includes("overdue") || status.includes("error")) return "is-danger"; if (status.includes("progress") || status.includes("open")) return "is-running"; return "is-neutral"; }
