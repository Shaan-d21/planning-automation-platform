import type { PlanningTask, TaskStatus } from "../api/types";
import { Icon } from "./Icon";

interface PlanningTaskCardProps {
  task: PlanningTask;
  busy: boolean;
  onStatus: (taskId: number, status: TaskStatus) => Promise<void>;
  onSubmitApproval: (taskId: number) => Promise<void>;
}

export function PlanningTaskCard({ task, busy, onStatus, onSubmitApproval }: PlanningTaskCardProps) {
  const next = nextStatus(task);
  const executable = isExecutable(task);
  const latestExecution = task.execution_attempts?.[0];
  const modernOperation = modernOperationCode(task.action_type);
  const baseRoute = task.action_type === "REVIEW_APPROVAL" ? "#approvals" : typeof task.action_config.route === "string"
    ? task.action_config.route
    : defaultRoute(task.action_type);
  const route = task.action_type === "OPEN_REPORT"
    ? "#reports"
    : modernOperation
    ? modernOperationRoute(modernOperation, task.task_id)
    : baseRoute && task.action_type === "OPEN_DATA_REVIEW"
    ? `/?planning_task_id=${task.task_id}#data-review`
    : baseRoute && executable
    ? withTaskContext(baseRoute, task.task_id)
    : baseRoute;
  const executionActive = latestExecution && ["QUEUED", "RUNNING"].includes(latestExecution.status);
  return (
    <article className={`task-card priority-${task.priority.toLowerCase()}`}>
      <div className="task-state"><span className={`state-dot state-${task.readiness.toLowerCase()}`} /><span>{readinessLabel(task)}</span></div>
      <div className="task-content">
        <div className="task-title-row"><h3>{task.title}</h3><span className={`priority priority--${task.priority.toLowerCase()}`}>{task.priority.toLowerCase()}</span></div>
        <p>{task.description || `Complete this ${task.task_type.toLowerCase().replaceAll("_", " ")} task.`}</p>
        <div className="task-meta"><span>{task.cycle_name}</span><span>{task.stage_name}</span>{task.entity && <span>{task.entity}</span>}{task.period && <span>{task.period}</span>}{task.due_at && <span className={isOverdue(task) ? "is-overdue" : ""}><Icon name="clock" /> {formatDue(task.due_at)}</span>}</div>
        {latestExecution && <div className={`task-execution task-execution--${latestExecution.status.toLowerCase()}`}><span><Icon name={latestExecution.status === "FAILED" ? "alert" : latestExecution.status === "SUCCESS" ? "check" : "clock"} /> Attempt {latestExecution.attempt_number}: {executionLabel(latestExecution.status)}</span>{latestExecution.error_message && <small>{latestExecution.error_message}</small>}</div>}
        {task.latest_validation && <div className={`task-validation-summary task-validation-summary--${task.latest_validation.status.toLowerCase()}`}><span><Icon name={task.latest_validation.status === "PASS" ? "check" : "alert"} /> Latest validation: {task.latest_validation.status.toLowerCase()}</span><small>{task.latest_validation.checked_cells.toLocaleString()} cells checked · {task.latest_validation.exception_count.toLocaleString()} exceptions</small></div>}
      </div>
      <div className="task-actions">
        {latestExecution && <a className="button button--quiet" href={latestExecution.run_url}>View run</a>}
        {route && (!executable || (!executionActive && !["COMPLETED", "CANCELLED"].includes(task.status))) && <a className={executable ? "button button--primary" : "button button--quiet"} href={route}>{executable ? (latestExecution?.status === "FAILED" ? "Retry operation" : "Run operation") : "Open"} <Icon name="arrow" /></a>}
        {task.action_type === "SUBMIT_APPROVAL" ? <button className="button button--primary" disabled={busy || task.readiness !== "READY"} onClick={() => onSubmitApproval(task.task_id)}>{busy ? <span className="spinner" /> : <Icon name="arrow" />} Submit for review</button> : !executable && task.action_type !== "REVIEW_APPROVAL" && <button className="button button--primary" disabled={!next || busy || task.readiness === "WAITING"} onClick={() => next && onStatus(task.task_id, next)}>{busy ? <span className="spinner" /> : null}{actionLabel(task)}</button>}
      </div>
    </article>
  );
}

export function taskOrder(left: PlanningTask, right: PlanningTask) {
  const readiness = { READY: 0, BLOCKED: 1, WAITING: 2, COMPLETED: 3, CANCELLED: 4 };
  const priority = { CRITICAL: 0, HIGH: 1, NORMAL: 2, LOW: 3 };
  return readiness[left.readiness] - readiness[right.readiness]
    || priority[left.priority] - priority[right.priority]
    || String(left.due_at ?? "9999").localeCompare(String(right.due_at ?? "9999"));
}

function nextStatus(task: PlanningTask): TaskStatus | null {
  if (["SUBMIT_APPROVAL", "REVIEW_APPROVAL"].includes(task.action_type) || isExecutable(task)) return null;
  if (task.readiness === "WAITING" || ["COMPLETED", "CANCELLED"].includes(task.status)) return null;
  if (task.status === "IN_PROGRESS") return "COMPLETED";
  if (task.status === "BLOCKED") return "IN_PROGRESS";
  return "IN_PROGRESS";
}

function isExecutable(task: PlanningTask) {
  return [
    "RUN_PIPELINE",
    "RUN_BUSINESS_RULE",
    "RUN_DATA_INTEGRATION",
    "RUN_DATA_MAP",
    "RUN_DATA_IMPORT",
    "RUN_METADATA_IMPORT",
    "RUN_CUBE_REFRESH",
    "UPDATE_SUBSTITUTION_VARIABLE"
  ].includes(task.action_type);
}

function withTaskContext(route: string, taskId: number) {
  const url = new URL(route, window.location.origin);
  url.searchParams.set("planning_task_id", String(taskId));
  return `${url.pathname}${url.search}${url.hash}`;
}

function modernOperationRoute(operation: string, taskId: number) {
  const query = new URLSearchParams({
    operation,
    planning_task_id: String(taskId)
  });
  return `/?${query.toString()}#operations`;
}

function modernOperationCode(actionType: string) {
  return ({
    RUN_PIPELINE: "pipelines",
    RUN_BUSINESS_RULE: "business-rules",
    RUN_DATA_INTEGRATION: "data-integrations",
    RUN_DATA_MAP: "data-maps",
    RUN_DATA_IMPORT: "data-import",
    RUN_METADATA_IMPORT: "metadata-import",
    RUN_CUBE_REFRESH: "cube-refresh",
    UPDATE_SUBSTITUTION_VARIABLE: "substitution-variables"
  } as Record<string, string>)[actionType] ?? null;
}

function executionLabel(status: string) {
  return ({ QUEUED: "queued", RUNNING: "running in Oracle", SUCCESS: "completed successfully", FAILED: "failed - ready to retry" } as Record<string, string>)[status] ?? status.toLowerCase();
}

function actionLabel(task: PlanningTask) {
  if (task.readiness === "WAITING") return "Waiting";
  if (task.status === "COMPLETED") return "Completed";
  if (task.status === "CANCELLED") return "Cancelled";
  if (task.status === "IN_PROGRESS") return "Mark complete";
  if (task.status === "BLOCKED") return "Resume";
  return "Start task";
}

function readinessLabel(task: PlanningTask) {
  if (task.readiness === "WAITING") return `${task.incomplete_dependency_ids.length} prerequisite${task.incomplete_dependency_ids.length === 1 ? "" : "s"} pending`;
  return task.readiness.toLowerCase().replaceAll("_", " ");
}

function defaultRoute(actionType: string) {
  const routes: Record<string, string> = {
    OPEN_DATA_REVIEW: "/app/data-review",
    OPEN_REPORT: "#reports",
    OPEN_PROCESS: "/app/control-panel",
    RUN_PIPELINE: "/app/operations/pipelines",
    RUN_BUSINESS_RULE: "/app/operations/business-rules",
    RUN_DATA_INTEGRATION: "/app/operations/data-integrations",
    RUN_DATA_MAP: "/app/operations/data-maps",
    RUN_DATA_IMPORT: "/app/operations/data-import",
    RUN_METADATA_IMPORT: "/app/operations/metadata-import",
    RUN_CUBE_REFRESH: "/app/operations/cube-refresh",
    UPDATE_SUBSTITUTION_VARIABLE: "/app/operations/substitution-variables"
  };
  return routes[actionType] ?? null;
}

function formatDue(value: string) {
  return new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(
    Math.ceil((new Date(value).getTime() - Date.now()) / 86_400_000),
    "day"
  );
}

function isOverdue(task: PlanningTask) {
  return Boolean(task.due_at && new Date(task.due_at).getTime() < Date.now() && !["COMPLETED", "CANCELLED"].includes(task.status));
}
