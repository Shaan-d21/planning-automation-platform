import { useMemo, useState } from "react";

import type { PlanningCycle, PlanningTask, PlanningWorkResponse, TaskStatus } from "../api/types";
import { Icon } from "./Icon";
import { PlanningTaskCard, taskOrder } from "./PlanningTaskCard";

interface PlanningWorkspaceProps {
  work: PlanningWorkResponse;
  busyTaskId: number | null;
  onTaskStatus: (taskId: number, status: TaskStatus) => Promise<void>;
  onSubmitApproval: (taskId: number) => Promise<void>;
}

type WorkFilter = "ALL" | "READY" | "WAITING" | "IN_PROGRESS" | "BLOCKED" | "COMPLETED";

export function PlanningWorkspace({ work, busyTaskId, onTaskStatus, onSubmitApproval }: PlanningWorkspaceProps) {
  const [cycleId, setCycleId] = useState<string>("");
  const [filter, setFilter] = useState<WorkFilter>("ALL");
  const [search, setSearch] = useState("");
  const selectedCycle = work.cycles.find((cycle) => String(cycle.cycle_id) === cycleId)
    ?? work.cycles.find((cycle) => !["COMPLETED", "CANCELLED"].includes(cycle.status))
    ?? work.cycles[0]
    ?? null;
  const tasks = useMemo(() => {
    const query = search.trim().toLowerCase();
    return work.tasks
      .filter((task) => !cycleId || String(task.cycle_id) === cycleId)
      .filter((task) => matchesFilter(task, filter))
      .filter((task) => !query || [task.title, task.description, task.cycle_name, task.stage_name, task.entity, task.period].some((value) => String(value ?? "").toLowerCase().includes(query)))
      .sort(taskOrder);
  }, [cycleId, filter, search, work.tasks]);
  const scopedTasks = work.tasks.filter((task) => !cycleId || String(task.cycle_id) === cycleId);

  return (
    <div className="planning-workspace">
      <section className="page-intro work-intro">
        <div>
          <span className="eyebrow">My work</span>
          <h1>Move Planning forward</h1>
          <p>See every assignment in context, understand what is blocking it, and open the right governed action from one workspace.</p>
        </div>
        <a className="button button--quiet" href="#home"><Icon name="home" /> Back to My Day</a>
      </section>

      <WorkSummary tasks={scopedTasks} />

      <section className="panel work-filter-panel" aria-label="Task filters">
        <label className="work-search"><span>Search assignments</span><div><Icon name="search" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Task, entity, period, or stage" /></div></label>
        <label><span>Planning cycle</span><select value={cycleId} onChange={(event) => setCycleId(event.target.value)}><option value="">All visible cycles</option>{work.cycles.map((cycle) => <option key={cycle.cycle_id} value={cycle.cycle_id}>{cycle.name} · {cycle.year}</option>)}</select></label>
        <label><span>Work status</span><select value={filter} onChange={(event) => setFilter(event.target.value as WorkFilter)}><option value="ALL">All assignments</option><option value="READY">Ready to start</option><option value="IN_PROGRESS">In progress</option><option value="WAITING">Waiting on prerequisites</option><option value="BLOCKED">Blocked</option><option value="COMPLETED">Completed</option></select></label>
        <button className="button button--quiet clear-filter" type="button" disabled={!search && !cycleId && filter === "ALL"} onClick={() => { setSearch(""); setCycleId(""); setFilter("ALL"); }}><Icon name="refresh" /> Clear</button>
      </section>

      <div className="work-layout">
        <section className="panel work-list-panel">
          <header className="work-list-heading"><div><span className="eyebrow">Assignments</span><h2>{tasks.length} task{tasks.length === 1 ? "" : "s"} shown</h2></div><span className="work-scope-badge">Assigned to you or your role</span></header>
          {tasks.length ? <div className="task-list">{tasks.map((task) => <PlanningTaskCard key={task.task_id} task={task} busy={busyTaskId === task.task_id} onStatus={onTaskStatus} onSubmitApproval={onSubmitApproval} />)}</div> : <WorkEmpty />}
        </section>

        <aside className="work-aside">
          <CycleOverview cycle={selectedCycle} />
          <DependencyGuide tasks={scopedTasks} />
        </aside>
      </div>
    </div>
  );
}

function WorkSummary({ tasks }: { tasks: PlanningTask[] }) {
  const values = [
    ["Ready now", tasks.filter((task) => task.readiness === "READY" && !["COMPLETED", "CANCELLED"].includes(task.status)).length, "brand", "tasks"],
    ["In progress", tasks.filter((task) => task.status === "IN_PROGRESS").length, "warning", "activity"],
    ["Waiting", tasks.filter((task) => task.readiness === "WAITING").length, "neutral", "clock"],
    ["Completed", tasks.filter((task) => task.status === "COMPLETED").length, "success", "check"]
  ] as const;
  return <section className="attention-strip work-summary" aria-label="My work summary">{values.map(([label, value, tone, icon]) => <article className={`summary-metric summary-metric--${tone}`} key={label}><span className="summary-icon"><Icon name={icon} /></span><span><strong>{value}</strong><small>{label}</small></span></article>)}</section>;
}

function CycleOverview({ cycle }: { cycle: PlanningCycle | null }) {
  if (!cycle) return <section className="panel"><div className="work-empty work-empty--compact"><Icon name="calendar" /><h2>No Planning cycle</h2><p>Assignments will be organized here when an administrator opens a cycle.</p></div></section>;
  return (
    <section className="panel cycle-overview-panel">
      <span className="eyebrow">Cycle context</span>
      <div className="cycle-overview-title"><div><h2>{cycle.name}</h2><p>{[cycle.scenario, cycle.year].filter(Boolean).join(" · ")}</p></div><strong>{cycle.progress_percent}%</strong></div>
      <div className="progress-track"><span style={{ width: `${cycle.progress_percent}%` }} /></div>
      <div className="cycle-context-grid">
        <span><small>Actual through</small><strong>{cycle.actual_through_period || "Not specified"}</strong></span>
        <span><small>Forecast starts</small><strong>{cycle.forecast_start_period || "Not specified"}</strong></span>
        <span><small>Due date</small><strong>{formatDate(cycle.due_date)}</strong></span>
        <span><small>Current stage</small><strong>{cycle.current_stage?.name || "Complete"}</strong></span>
      </div>
      <ol className="cycle-step-list">{cycle.stages.map((stage) => {
        const complete = ["COMPLETED", "SKIPPED"].includes(stage.status);
        const current = stage.stage_id === cycle.current_stage?.stage_id;
        return <li className={`${complete ? "is-complete" : ""}${current ? " is-current" : ""}`} key={stage.stage_id}><span>{complete ? <Icon name="check" /> : stage.sequence}</span><div><strong>{stage.name}</strong><small>{current ? "Current stage" : complete ? "Completed" : "Upcoming"}</small></div></li>;
      })}</ol>
    </section>
  );
}

function DependencyGuide({ tasks }: { tasks: PlanningTask[] }) {
  const byId = new Map(tasks.map((task) => [task.task_id, task]));
  const waiting = tasks.filter((task) => task.incomplete_dependency_ids.length > 0 && !["COMPLETED", "CANCELLED"].includes(task.status));
  return (
    <section className="panel dependency-panel">
      <span className="eyebrow">Dependencies</span><h2>Why work may be waiting</h2>
      {waiting.length ? <div className="dependency-list">{waiting.slice(0, 5).map((task) => <article key={task.task_id}><Icon name="clock" /><div><strong>{task.title}</strong><p>Waiting for {task.incomplete_dependency_ids.map((id) => byId.get(id)?.title ?? `task ${id}`).join(", ")}.</p></div></article>)}</div> : <div className="dependency-clear"><Icon name="check" /><div><strong>No unresolved dependencies</strong><p>Every unfinished assignment in this scope can proceed according to its current status.</p></div></div>}
      <details><summary>How readiness works</summary><p>A task becomes ready only after all of its prerequisite tasks are completed. This prevents later Planning steps from starting before required validation or preparation is finished.</p></details>
    </section>
  );
}

function WorkEmpty() {
  return <div className="work-empty"><span><Icon name="search" /></span><h2>No assignments match</h2><p>Change the cycle, status, or search filter. Only work assigned directly to you or one of your platform roles is displayed.</p></div>;
}

function matchesFilter(task: PlanningTask, filter: WorkFilter) {
  if (filter === "ALL") return true;
  if (filter === "READY") return task.readiness === "READY" && !["COMPLETED", "CANCELLED"].includes(task.status);
  if (filter === "WAITING") return task.readiness === "WAITING";
  return task.status === filter;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", year: "numeric" }).format(new Date(`${value}T00:00:00`));
}
