import type {
  BootstrapResponse,
  EnvironmentHealth,
  HomeResponse,
  NavigationItem,
  PlanningCycle,
  PlanningTask,
  TaskStatus
} from "../api/types";
import { Icon } from "./Icon";
import { PlanningTaskCard, taskOrder } from "./PlanningTaskCard";

interface DashboardProps {
  bootstrap: BootstrapResponse;
  home: HomeResponse;
  busyTaskId: number | null;
  health: EnvironmentHealth | null;
  healthBusy: boolean;
  onCheckHealth: () => Promise<void>;
  onTaskStatus: (taskId: number, status: TaskStatus) => Promise<void>;
  onSubmitApproval: (taskId: number) => Promise<void>;
}

export function Dashboard({
  bootstrap,
  home,
  busyTaskId,
  health,
  healthBusy,
  onCheckHealth,
  onTaskStatus,
  onSubmitApproval
}: DashboardProps) {
  const user = bootstrap.user!;
  const primaryCycle = home.cycles.find((cycle) => !["COMPLETED", "CANCELLED"].includes(cycle.status)) ?? home.cycles[0] ?? null;
  const tasks = [...home.tasks].filter((task) => task.status !== "CANCELLED").sort(taskOrder);
  const isAdmin = user.persona === "SERVICE_ADMINISTRATOR";
  const isManager = user.persona === "POWER_USER";
  const isViewer = user.persona === "VIEWER";
  const visibleActivity = isViewer
    ? home.recent_activity.filter((item) => item.name.toLowerCase().includes("report"))
    : home.recent_activity;

  return (
    <div className={`dashboard persona-${user.persona.toLowerCase()}`}>
      <section className="page-intro">
        <div>
          <span className="eyebrow">My day · {user.persona_label}</span>
          <h1>{greeting()}, {firstName(user.display_name)}</h1>
          <p>{personaMessage(user.persona, home.summary.action_required, tasks, primaryCycle)}</p>
        </div>
        <div className="live-badge"><span /> Personalized from live assignments</div>
      </section>

      <SummaryStrip persona={user.persona} home={home} tasks={tasks} cycle={primaryCycle} />

      {isAdmin && (
        <EnvironmentPanel
          bootstrap={bootstrap}
          health={health}
          busy={healthBusy}
          onCheck={onCheckHealth}
        />
      )}

      {isViewer ? (
        <ViewerWorkspace cycle={primaryCycle} navigation={bootstrap.navigation} />
      ) : (
        <div className="dashboard-grid">
          <section className="panel panel--priorities" id="priorities">
            <PanelHeading
              eyebrow={isManager ? "Requires your review" : "Your priorities"}
              title={isManager ? "Manage by exception" : "What needs your attention"}
              description={isManager
                ? "Submitted, blocked, and high-priority work appears first."
                : "Tasks are ordered by urgency, due date, and dependency readiness."}
            />
            {tasks.length ? (
              <div className="task-list">
                {tasks.slice(0, 7).map((task) => (
                  <PlanningTaskCard key={task.task_id} task={task} busy={busyTaskId === task.task_id} onStatus={onTaskStatus} onSubmitApproval={onSubmitApproval} />
                ))}
              </div>
            ) : (
              <EmptyState icon="check" title="You are all caught up" description="New work will appear here when it is assigned to you or one of your roles." />
            )}
          </section>

          <aside className="side-stack">
            <section className="panel panel--next">
              <PanelHeading eyebrow="Due next" title="Current Planning cycle" />
              {primaryCycle ? <CycleCard cycle={primaryCycle} /> : (
                <EmptyState icon="calendar" title="No active cycle" description="A Planning administrator can open the next business cycle after preparation is complete." />
              )}
            </section>
            {isManager && <EntityProgress tasks={tasks} />}
          </aside>
        </div>
      )}

      <QuickActions persona={user.persona} navigation={bootstrap.navigation} />

      <section className="panel activity-panel">
        <PanelHeading
          eyebrow={isViewer ? "Approved results" : "Recent activity"}
          title={isViewer ? "Latest reporting activity" : "What happened recently"}
          description={isViewer
            ? "Only reporting activity available to your role is shown here."
            : "A concise audit view of Planning automation visible to your role."}
        />
        {visibleActivity.length ? (
          <div className="activity-table" role="table" aria-label="Recent Planning activity">
            <div className="activity-row activity-row--head" role="row"><span>Activity</span><span>Started</span><span>Triggered by</span><span>Status</span></div>
            {visibleActivity.map((item) => (
              <div className="activity-row" role="row" key={item.execution_id}>
                <span><strong>{item.name}</strong><small>{item.trigger_source.toLowerCase()}</small></span>
                <span>{formatDateTime(item.started_at)}</span>
                <span>{item.initiated_by}</span>
                <span><StatusBadge status={item.status} /></span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={isViewer ? "reports" : "activity"}
            title={isViewer ? "No published report activity yet" : "No recent activity"}
            description={isViewer ? "Open Reports to review the business outputs available to you." : "Completed and running automations will appear here automatically."}
            compact
          />
        )}
      </section>
    </div>
  );
}

function SummaryStrip({ persona, home, tasks, cycle }: { persona: string; home: HomeResponse; tasks: PlanningTask[]; cycle: PlanningCycle | null }) {
  const remaining = tasks.filter((task) => !["COMPLETED", "CANCELLED"].includes(task.status)).length;
  const waiting = tasks.filter((task) => task.readiness === "WAITING").length;
  const failed = home.recent_activity.filter((item) => item.status === "FAILED").length;
  const reviews = tasks.filter((task) => /REVIEW|APPROVAL|SUBMIT/.test(`${task.task_type} ${task.action_type}`)).length;
  const activeCycles = home.cycles.filter((item) => !["COMPLETED", "CANCELLED"].includes(item.status)).length;
  const values = persona === "SERVICE_ADMINISTRATOR"
    ? [["Attention required", home.summary.action_required, "danger", "alert"], ["Failed recent runs", failed, "warning", "activity"], ["Due today", home.summary.due_today, "brand", "clock"], ["Active cycles", activeCycles, "success", "calendar"]]
    : persona === "POWER_USER"
      ? [["Awaiting review", reviews, "danger", "tasks"], ["Due today", home.summary.due_today, "warning", "clock"], ["Tasks in scope", remaining, "brand", "tasks"], ["Cycle progress", cycle ? `${cycle.progress_percent}%` : "—", "success", "calendar"]]
      : persona === "VIEWER"
        ? [["Active cycles", activeCycles, "brand", "calendar"], ["Cycle progress", cycle ? `${cycle.progress_percent}%` : "—", "success", "activity"], ["Published reports", home.recent_activity.filter((item) => item.name.toLowerCase().includes("report")).length, "warning", "reports"], ["Completed cycles", home.cycles.filter((item) => item.status === "COMPLETED").length, "success", "check"]]
        : [["Tasks remaining", remaining, "danger", "tasks"], ["Due today", home.summary.due_today, "warning", "clock"], ["Waiting on others", waiting, "brand", "activity"], ["Completed", home.summary.completed, "success", "check"]];
  return <section className="attention-strip" aria-label="Planning summary">{values.map(([label, value, tone, icon]) => <SummaryMetric key={String(label)} label={String(label)} value={value as number | string} tone={String(tone)} icon={icon as IconName} />)}</section>;
}

type IconName = "activity" | "alert" | "automation" | "calendar" | "check" | "clock" | "data" | "reports" | "settings" | "tasks" | "users" | "assistant";

function SummaryMetric({ label, value, tone, icon }: { label: string; value: number | string; tone: string; icon: IconName }) {
  return <article className={`summary-metric summary-metric--${tone}`}><span className="summary-icon"><Icon name={icon} /></span><span><strong>{value}</strong><small>{label}</small></span></article>;
}

function EnvironmentPanel({ bootstrap, health, busy, onCheck }: { bootstrap: BootstrapResponse; health: EnvironmentHealth | null; busy: boolean; onCheck: () => Promise<void> }) {
  const available = health?.status === "ok";
  return (
    <section className="environment-panel" aria-label="Environment health">
      <div className={`environment-state ${health ? available ? "is-healthy" : "is-unavailable" : "is-unchecked"}`}>
        <span className="environment-state__icon"><Icon name={available ? "check" : health ? "alert" : "activity"} /></span>
        <div><small>Environment health</small><strong>{busy ? "Checking Oracle…" : available ? "Connected" : health ? "Connection needs attention" : "Ready to verify"}</strong></div>
      </div>
      <div className="environment-detail"><small>Application</small><strong>{bootstrap.environment?.application_name || "Not configured"}</strong></div>
      <div className="environment-detail"><small>Deployment</small><strong>{bootstrap.environment?.deployment_mode || "Unknown"}</strong></div>
      <div className="environment-detail"><small>Last check</small><strong>{health ? "Just now" : "Not checked"}</strong></div>
      <button className="button button--quiet" type="button" disabled={busy} onClick={onCheck}>{busy ? <span className="spinner spinner--dark" /> : <Icon name="refresh" />} {health ? "Check again" : "Check connection"}</button>
      {health?.details && <p className="environment-error">{health.details}</p>}
    </section>
  );
}

function ViewerWorkspace({ cycle, navigation }: { cycle: PlanningCycle | null; navigation: NavigationItem[] }) {
  const reports = navigation.find((item) => item.code === "reports");
  return (
    <div className="viewer-grid">
      <section className="panel viewer-cycle">
        <PanelHeading eyebrow="Planning outlook" title="Current approved business context" description="Operational controls are intentionally hidden from this read-only experience." />
        {cycle ? <CycleCard cycle={cycle} /> : <EmptyState icon="calendar" title="No active Planning cycle" description="Approved reporting remains available from the Reports workspace." />}
      </section>
      <section className="panel viewer-report-card">
        <span className="viewer-report-card__icon"><Icon name="reports" /></span>
        <span className="eyebrow">Management reporting</span>
        <h2>Review approved results</h2>
        <p>Open the report library for Forecast, Budget, variance, and management outputs authorized for your account.</p>
        {reports ? <a className="button button--primary" href="#reports">Open Reports <Icon name="arrow" /></a> : <span className="muted">No report workspace is assigned to this role.</span>}
      </section>
    </div>
  );
}

function QuickActions({ persona, navigation }: { persona: string; navigation: NavigationItem[] }) {
  const preferred: Record<string, string[]> = {
    SERVICE_ADMINISTRATOR: ["jobs", "operations", "data-review", "cycles"],
    POWER_USER: ["approvals", "data-review", "reports", "jobs"],
    USER: ["tasks", "data-review", "reports", "assistant"],
    VIEWER: ["reports", "assistant"]
  };
  const available = new Map(navigation.map((item) => [item.code, item]));
  const actions = (preferred[persona] ?? []).map((code) => available.get(code)).filter((item): item is NavigationItem => Boolean(item));
  if (!actions.length) return null;
  return (
    <section className="panel quick-actions-panel">
      <PanelHeading eyebrow="From here" title="What you can do now" description="Only actions permitted for your account are shown." />
      <div className="quick-action-grid">
        {actions.map((item) => <a className="quick-action" href={["reports", "assistant", "data-review"].includes(item.code) ? `#${item.code}` : item.path} key={item.code}><span><Icon name={quickActionIcon(item.code)} /></span><div><strong>{item.label}</strong><small>{quickActionDescription(item.code)}</small></div><Icon name="arrow" /></a>)}
      </div>
    </section>
  );
}

function EntityProgress({ tasks }: { tasks: PlanningTask[] }) {
  const entities = new Map<string, { total: number; complete: number }>();
  tasks.filter((task) => task.entity).forEach((task) => {
    const value = entities.get(task.entity!) ?? { total: 0, complete: 0 };
    value.total += 1;
    if (task.status === "COMPLETED") value.complete += 1;
    entities.set(task.entity!, value);
  });
  if (!entities.size) return null;
  return (
    <section className="panel entity-panel">
      <PanelHeading eyebrow="Entity progress" title="Teams in your scope" />
      <div className="entity-list">{[...entities].slice(0, 6).map(([name, value]) => {
        const percent = Math.round((value.complete / value.total) * 100);
        return <div className="entity-row" key={name}><div><strong>{name}</strong><small>{value.complete} of {value.total} tasks complete</small></div><span>{percent}%</span><div className="progress-track"><i style={{ width: `${percent}%` }} /></div></div>;
      })}</div>
    </section>
  );
}

function PanelHeading({ eyebrow, title, description }: { eyebrow: string; title: string; description?: string }) {
  return <header className="panel-heading"><span className="eyebrow">{eyebrow}</span><h2>{title}</h2>{description && <p>{description}</p>}</header>;
}

function CycleCard({ cycle }: { cycle: PlanningCycle }) {
  return (
    <div className="cycle-card">
      <div className="cycle-card__title"><div><span className="cycle-type">{cycle.cycle_type}</span><h3>{cycle.name}</h3><p>{[cycle.scenario, cycle.year].filter(Boolean).join(" · ")}</p></div><strong>{cycle.progress_percent}%</strong></div>
      <div className="progress-track"><span style={{ width: `${cycle.progress_percent}%` }} /></div>
      <div className="cycle-dates"><span><small>Started</small>{formatDate(cycle.start_date)}</span><span><small>Due</small>{formatDate(cycle.due_date)}</span></div>
      <div className="stage-list">{cycle.stages.map((stage) => {
        const current = stage.stage_id === cycle.current_stage?.stage_id;
        const complete = ["COMPLETED", "SKIPPED"].includes(stage.status);
        return <div className={`stage${current ? " is-current" : ""}${complete ? " is-complete" : ""}`} key={stage.stage_id}><span>{complete ? <Icon name="check" /> : stage.sequence}</span><div><strong>{stage.name}</strong><small>{current ? "Current stage" : complete ? "Completed" : "Upcoming"}</small></div></div>;
      })}</div>
      {cycle.current_stage && <div className="next-callout"><Icon name="sparkle" /><span><small>Focus now</small><strong>{cycle.current_stage.name}</strong></span></div>}
    </div>
  );
}

function EmptyState({ icon, title, description, compact = false }: { icon: "check" | "calendar" | "activity" | "reports"; title: string; description: string; compact?: boolean }) {
  return <div className={`empty-state${compact ? " empty-state--compact" : ""}`}><span><Icon name={icon} /></span><div><strong>{title}</strong><p>{description}</p></div></div>;
}

function StatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  return <span className={`status-badge status-${normalized}`}>{normalized.replaceAll("_", " ")}</span>;
}

function quickActionIcon(code: string): IconName {
  const icons: Record<string, IconName> = { operations: "automation", "data-review": "data", "access-control": "users", cycles: "calendar", approvals: "check", tasks: "tasks", reports: "reports", jobs: "activity", assistant: "assistant" };
  return icons[code] ?? "tasks";
}

function quickActionDescription(code: string) {
  const descriptions: Record<string, string> = {
    operations: "Run governed Oracle jobs and Pipelines",
    "data-review": "Inspect and reconcile Planning data",
    cycles: "Open and govern the Planning calendar",
    approvals: "Review submitted Planning work",
    tasks: "Continue your assigned Planning work",
    "access-control": "Manage platform users and roles",
    reports: "Generate and download business outputs",
    jobs: "Review status, evidence, and failures",
    assistant: "Ask for guidance or prepare an action"
  };
  return descriptions[code] ?? "Open this authorized workspace";
}

function greeting() { const hour = new Date().getHours(); return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening"; }
function firstName(name: string) { return name.trim().split(/\s+/)[0] || name; }
function formatDate(value: string) { return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" }).format(new Date(`${value}T00:00:00`)); }
function formatDateTime(value: string) { return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }).format(new Date(value)); }
function personaMessage(persona: string, actions: number, tasks: PlanningTask[], cycle: PlanningCycle | null) {
  if (persona === "SERVICE_ADMINISTRATOR") return actions ? `${actions} operational item${actions === 1 ? "" : "s"} need attention before the Planning lifecycle can move forward.` : "The environment and assigned operational work are currently under control.";
  if (persona === "POWER_USER") return actions ? `${actions} Planning item${actions === 1 ? " is" : "s are"} ready for review or action.` : "Your review queue and Planning responsibilities are up to date.";
  if (persona === "VIEWER") return cycle ? `Review the ${cycle.name} business context and the latest approved reporting available to you.` : "Review approved Planning results and management reports available to your account.";
  const remaining = tasks.filter((task) => !["COMPLETED", "CANCELLED"].includes(task.status)).length;
  return remaining ? `You have ${remaining} assigned task${remaining === 1 ? "" : "s"} remaining. Start with the first ready item below.` : "Your assigned Planning work is complete for now.";
}
