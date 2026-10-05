import { useMemo, useState } from "react";

import type {
  TaskManagerSnapshotResponse,
  TaskManagerSyncInput,
  TaskManagerTask
} from "../api/types";
import { Icon } from "./Icon";

interface Props {
  data: TaskManagerSnapshotResponse;
  busy: boolean;
  onSynchronize: (input: TaskManagerSyncInput) => Promise<void>;
}

interface ParameterRow {
  id: number;
  name: string;
  value: string;
}

export function TaskManagerWorkspace({ data, busy, onSynchronize }: Props) {
  const [search, setSearch] = useState("");
  const [schedule, setSchedule] = useState("ALL");
  const [status, setStatus] = useState("ALL");
  const [reportGroup, setReportGroup] = useState(
    data.configuration?.report_group ?? ""
  );
  const [reportName, setReportName] = useState(
    data.configuration?.report_name ?? ""
  );
  const [parameters, setParameters] = useState<ParameterRow[]>(() => {
    const configured = Object.entries(data.configuration?.parameters ?? {});
    return configured.length
      ? configured.map(([name, value], index) => ({ id: index + 1, name, value }))
      : [{ id: 1, name: "", value: "" }];
  });

  const schedules = useMemo(
    () => unique(data.tasks.map((task) => task.schedule_name)),
    [data.tasks]
  );
  const statuses = useMemo(
    () => unique(data.tasks.map((task) => task.status)),
    [data.tasks]
  );
  const filtered = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return data.tasks.filter((task) => {
      if (schedule !== "ALL" && task.schedule_name !== schedule) return false;
      if (status !== "ALL" && task.status !== status) return false;
      if (!query) return true;
      return [
        task.name,
        task.external_id,
        task.schedule_name,
        task.period_name,
        task.owner,
        task.assignee,
        task.organization
      ].some((value) => value?.toLocaleLowerCase().includes(query));
    });
  }, [data.tasks, schedule, search, status]);
  const grouped = useMemo(() => groupBySchedule(filtered), [filtered]);
  const actionRequired = data.tasks.filter((task) =>
    ["OPEN", "NOT STARTED", "IN PROGRESS", "LATE", "OVERDUE"].includes(
      (task.status ?? "").toLocaleUpperCase()
    )
  ).length;
  const completed = data.tasks.filter((task) =>
    ["COMPLETED", "CLOSED", "APPROVED"].includes(
      (task.status ?? "").toLocaleUpperCase()
    )
  ).length;

  async function synchronize() {
    const mapped = Object.fromEntries(
      parameters
        .map((item) => [item.name.trim(), item.value.trim()] as const)
        .filter(([name, value]) => name && value)
    );
    await onSynchronize({
      report_group: reportGroup.trim(),
      report_name: reportName.trim(),
      parameters: mapped
    });
  }

  return (
    <main className="page task-manager-page">
      <header className="page-intro">
        <div>
          <span className="eyebrow">Oracle Planning · Task Manager</span>
          <h1>Task Manager</h1>
          <p>
            Review the latest synchronized Oracle schedules and tasks in one
            structured workspace. This is separate from platform Planning Cycles.
          </p>
        </div>
        <SyncState data={data} />
      </header>

      <section className="attention-strip task-manager-metrics" aria-label="Task summary">
        <Metric icon="tasks" value={data.summary.task_count} label="Synchronized tasks" />
        <Metric icon="calendar" value={data.summary.schedule_count} label="Schedules" />
        <Metric icon="alert" value={actionRequired} label="Action required" tone="warning" />
        <Metric icon="check" value={completed} label="Completed" tone="success" />
      </section>

      {data.can_sync && (
        <section className="panel task-manager-sync-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">Synchronization source</span>
              <h2>Oracle Task Manager report</h2>
              <p>
                Enter the exact report group and report name configured in Oracle.
                The generated CSV is parsed in memory and is never stored by this platform.
              </p>
            </div>
            <span className="task-manager-memory-badge">No source file retained</span>
          </div>
          {data.sync?.last_sync_status === "FAILED" && data.sync.last_error && (
            <div className="feedback-banner feedback-banner--error">
              <Icon name="alert" />
              <span><strong>Last synchronization failed.</strong> {data.sync.last_error}</span>
            </div>
          )}
          <div className="task-manager-config-grid">
            <label>
              <span>Report group</span>
              <input
                value={reportGroup}
                onChange={(event) => setReportGroup(event.target.value)}
                placeholder="Exact Oracle report group"
              />
            </label>
            <label>
              <span>Report name</span>
              <input
                value={reportName}
                onChange={(event) => setReportName(event.target.value)}
                placeholder="Exact Task Manager report name"
              />
            </label>
          </div>
          <div className="task-manager-parameters">
            <div className="task-manager-parameters__heading">
              <div>
                <strong>Report parameters</strong>
                <small>Add every parameter required by the Oracle report.</small>
              </div>
              <button
                className="button button--quiet"
                type="button"
                onClick={() => setParameters((current) => [
                  ...current,
                  { id: Math.max(0, ...current.map((item) => item.id)) + 1, name: "", value: "" }
                ])}
              >
                Add parameter
              </button>
            </div>
            {parameters.map((parameter) => (
              <div className="task-manager-parameter-row" key={parameter.id}>
                <input
                  aria-label="Parameter name"
                  placeholder="Parameter name"
                  value={parameter.name}
                  onChange={(event) => setParameters((current) => current.map((item) =>
                    item.id === parameter.id ? { ...item, name: event.target.value } : item
                  ))}
                />
                <input
                  aria-label="Parameter value"
                  placeholder="Parameter value"
                  value={parameter.value}
                  onChange={(event) => setParameters((current) => current.map((item) =>
                    item.id === parameter.id ? { ...item, value: event.target.value } : item
                  ))}
                />
                <button
                  className="icon-button"
                  type="button"
                  aria-label="Remove parameter"
                  onClick={() => setParameters((current) =>
                    current.length === 1
                      ? [{ ...current[0], name: "", value: "" }]
                      : current.filter((item) => item.id !== parameter.id)
                  )}
                >
                  <Icon name="close" />
                </button>
              </div>
            ))}
          </div>
          <div className="task-manager-sync-actions">
            <p>Synchronization replaces only this application’s Task Manager snapshot.</p>
            <button
              className="button button--primary"
              type="button"
              disabled={busy || !reportGroup.trim() || !reportName.trim()}
              onClick={() => void synchronize()}
            >
              <Icon name="refresh" />
              {busy ? "Synchronizing…" : "Synchronize from Oracle"}
            </button>
          </div>
        </section>
      )}

      <section className="panel task-manager-list-panel">
        <div className="panel-heading task-manager-list-heading">
          <div>
            <span className="eyebrow">Current snapshot</span>
            <h2>Schedules and tasks</h2>
            <p>{filtered.length} of {data.tasks.length} tasks shown.</p>
          </div>
          <div className="task-manager-filters">
            <label className="task-manager-search">
              <Icon name="search" />
              <input
                aria-label="Search tasks"
                placeholder="Search task, owner, period…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </label>
            <select aria-label="Filter by schedule" value={schedule} onChange={(event) => setSchedule(event.target.value)}>
              <option value="ALL">All schedules</option>
              {schedules.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
            <select aria-label="Filter by status" value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="ALL">All statuses</option>
              {statuses.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </div>
        </div>
        {!data.tasks.length ? (
          <div className="empty-state">
            <span><Icon name="tasks" /></span>
            <div>
              <strong>No Task Manager snapshot yet</strong>
              <p>
                {data.can_sync
                  ? "Configure an Oracle Task Manager report above, then synchronize it."
                  : "A Service Administrator must configure and synchronize the Oracle report."}
              </p>
            </div>
          </div>
        ) : !filtered.length ? (
          <div className="empty-state empty-state--compact">
            <span><Icon name="search" /></span>
            <div><strong>No matching tasks</strong><p>Change the search or filters.</p></div>
          </div>
        ) : (
          <div className="task-manager-schedules">
            {Array.from(grouped.entries()).map(([name, tasks]) => (
              <ScheduleGroup key={name} name={name} tasks={tasks} />
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

function SyncState({ data }: { data: TaskManagerSnapshotResponse }) {
  const syncedAt = data.sync?.last_synced_at;
  return (
    <div className={`task-manager-sync-state ${data.sync?.last_sync_status === "FAILED" ? "is-error" : ""}`}>
      <span><Icon name={data.sync?.last_sync_status === "FAILED" ? "alert" : "refresh"} /></span>
      <div>
        <small>Last synchronized</small>
        <strong>{syncedAt ? formatDateTime(syncedAt) : "Not synchronized"}</strong>
      </div>
    </div>
  );
}

function Metric({ icon, value, label, tone = "brand" }: { icon: "tasks" | "calendar" | "alert" | "check"; value: number; label: string; tone?: string }) {
  return (
    <article className={`summary-metric summary-metric--${tone}`}>
      <span className="summary-icon"><Icon name={icon} /></span>
      <span><strong>{value}</strong><small>{label}</small></span>
    </article>
  );
}

function ScheduleGroup({ name, tasks }: { name: string; tasks: TaskManagerTask[] }) {
  const completed = tasks.filter((task) =>
    ["COMPLETED", "CLOSED", "APPROVED"].includes((task.status ?? "").toLocaleUpperCase())
  ).length;
  return (
    <section className="task-manager-schedule">
      <header>
        <div><span><Icon name="calendar" /></span><div><h3>{name}</h3><p>{tasks.length} tasks</p></div></div>
        <strong>{completed}/{tasks.length} completed</strong>
      </header>
      <div className="task-manager-table-wrap">
        <table className="task-manager-table">
          <thead><tr><th>Task</th><th>Period</th><th>Owner / assignee</th><th>Due</th><th>Status</th></tr></thead>
          <tbody>
            {tasks.map((task) => (
              <tr key={task.source_key}>
                <td>
                  <details>
                    <summary><strong>{task.name}</strong><small>{task.task_type ?? task.external_id ?? "Task"}</small></summary>
                    <TaskDetails task={task} />
                  </details>
                </td>
                <td>{task.period_name ?? "—"}</td>
                <td><strong>{task.assignee ?? task.owner ?? "—"}</strong>{task.approver && <small>Approver: {task.approver}</small>}</td>
                <td>{task.due_at ? formatDateTime(task.due_at) : "—"}</td>
                <td><span className={`task-manager-status ${statusClass(task.status)}`}>{task.status ?? "Unspecified"}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TaskDetails({ task }: { task: TaskManagerTask }) {
  const details = [
    ["Description", task.description],
    ["Organization", task.organization],
    ["Parent task", task.parent_task],
    ["Dependency", task.dependency],
    ["Start", task.start_at ? formatDateTime(task.start_at) : null],
    ["Completed", task.completed_at ? formatDateTime(task.completed_at) : null],
    ["Priority", task.priority]
  ].filter((item): item is string[] => Boolean(item[1]));
  if (!details.length) return <p className="task-manager-no-details">No additional details were supplied.</p>;
  return <dl className="task-manager-details">{details.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

function groupBySchedule(tasks: TaskManagerTask[]) {
  const groups = new Map<string, TaskManagerTask[]>();
  tasks.forEach((task) => {
    const name = task.schedule_name || "Unassigned schedule";
    groups.set(name, [...(groups.get(name) ?? []), task]);
  });
  return groups;
}

function unique(values: Array<string | null>) {
  return Array.from(new Set(values.filter((value): value is string => Boolean(value)))).sort();
}

function formatDateTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function statusClass(value: string | null) {
  const status = (value ?? "").toLocaleLowerCase();
  if (status.includes("complete") || status.includes("closed") || status.includes("approved")) return "is-success";
  if (status.includes("late") || status.includes("overdue") || status.includes("error")) return "is-danger";
  if (status.includes("progress") || status.includes("open")) return "is-running";
  return "is-neutral";
}
