import { useState } from "react";

import type {
  PlanningCycleAdministrationResponse,
  PlanningCycleCreateInput,
  PlanningCycleTaskInput
} from "../api/types";
import { Icon } from "./Icon";

interface PlanningCycleAdministrationProps {
  data: PlanningCycleAdministrationResponse;
  busy: boolean;
  onCreate: (payload: PlanningCycleCreateInput) => Promise<void>;
}

interface StageEditor {
  id: string;
  code: string;
  name: string;
  tasks: TaskEditor[];
}

interface TaskEditor {
  id: string;
  key: string;
  title: string;
  description: string;
  priority: PlanningCycleTaskInput["priority"];
  assignee: string;
  dueAt: string;
  actionType: string;
  dependsOn: string;
  dataReview: DataReviewEditor;
}

interface DataReviewEditor {
  cube: string;
  pov: string;
  rows: string;
  columns: string;
  validationType: "QUALITY" | "COMPARISON";
  checkMissing: boolean;
  checkZero: boolean;
  targetCube: string;
  tolerance: string;
}

interface CycleContext {
  name: string;
  code: string;
  cycleType: string;
  scenario: string;
  year: string;
  actualThrough: string;
  forecastStart: string;
  startDate: string;
  dueDate: string;
}

const ACTIONS = [
  ["OPEN_DATA_REVIEW", "Review Planning data"],
  ["OPEN_REPORT", "Generate or review a report"],
  ["RUN_PIPELINE", "Run an Oracle Pipeline"],
  ["RUN_BUSINESS_RULE", "Run a business rule"],
  ["RUN_DATA_INTEGRATION", "Run a Data Integration"],
  ["RUN_DATA_MAP", "Push data with a Data Map"],
  ["RUN_DATA_IMPORT", "Run a Planning data import"],
  ["RUN_METADATA_IMPORT", "Run a metadata import"],
  ["RUN_CUBE_REFRESH", "Refresh the Planning cube"],
  ["UPDATE_SUBSTITUTION_VARIABLE", "Update substitution variables"],
  ["SUBMIT_APPROVAL", "Submit completed work for approval"],
  ["REVIEW_APPROVAL", "Review and decide a Planning submission"],
  ["OPEN_MY_WORK", "Complete a business checklist task"]
] as const;

export function PlanningCycleAdministration({ data, busy, onCreate }: PlanningCycleAdministrationProps) {
  const [editing, setEditing] = useState(false);
  const [step, setStep] = useState(1);
  const [context, setContext] = useState<CycleContext>(() => initialContext());
  const [stages, setStages] = useState<StageEditor[]>(() => forecastStarter(data));
  const [validation, setValidation] = useState<string | null>(null);
  const activeCycles = data.cycles.filter((cycle) => !["COMPLETED", "CANCELLED"].includes(cycle.status));

  function beginCycle() {
    setContext(initialContext());
    setStages(forecastStarter(data));
    setValidation(null);
    setStep(1);
    setEditing(true);
  }

  function next() {
    const issue = validateStep(step, context, stages);
    if (issue) {
      setValidation(issue);
      return;
    }
    setValidation(null);
    setStep((current) => Math.min(3, current + 1));
  }

  async function create() {
    const issue = validateStep(3, context, stages);
    if (issue) {
      setValidation(issue);
      return;
    }
    await onCreate(toPayload(context, stages));
    setEditing(false);
    setStep(1);
  }

  return (
    <section className="cycle-admin">
      <header className="page-intro cycle-admin-intro">
        <div>
          <span className="eyebrow">Planning governance</span>
          <h1>{editing ? "Open a Planning cycle" : "Planning Cycles"}</h1>
          <p>{editing
            ? "Define the business calendar and responsibilities. Oracle Pipelines remain configured in Oracle; this workspace organizes who must do what and when."
            : "Open each forecast or budget cycle once, assign accountable owners, and monitor progress from one place."}</p>
        </div>
        {!editing && <button className="button button--primary" onClick={beginCycle}><span aria-hidden="true">+</span> Open new cycle</button>}
      </header>

      {editing ? (
        <CycleEditor
          context={context}
          data={data}
          stages={stages}
          step={step}
          validation={validation}
          busy={busy}
          onContext={setContext}
          onStages={setStages}
          onBack={() => { setValidation(null); setStep((current) => Math.max(1, current - 1)); }}
          onCancel={() => setEditing(false)}
          onNext={next}
          onCreate={create}
        />
      ) : (
        <CyclePortfolio cycles={data.cycles} activeCount={activeCycles.length} onCreate={beginCycle} />
      )}
    </section>
  );
}

function CyclePortfolio({ cycles, activeCount, onCreate }: { cycles: PlanningCycleAdministrationResponse["cycles"]; activeCount: number; onCreate: () => void }) {
  const completed = cycles.filter((cycle) => cycle.status === "COMPLETED").length;
  const dueSoon = cycles.filter((cycle) => !["COMPLETED", "CANCELLED"].includes(cycle.status) && daysUntil(cycle.due_date) <= 7).length;
  return <>
    <div className="attention-strip">
      <CycleMetric label="Active cycles" value={activeCount} tone="brand" icon="calendar" />
      <CycleMetric label="Due within 7 days" value={dueSoon} tone="warning" icon="clock" />
      <CycleMetric label="Completed cycles" value={completed} tone="success" icon="check" />
      <CycleMetric label="Visible cycles" value={cycles.length} tone="neutral" icon="activity" />
    </div>
    <section className="panel cycle-portfolio">
      <div className="work-list-heading"><div><span className="eyebrow">Cycle portfolio</span><h2>Business calendar</h2></div><span className="work-scope-badge">Operational cycles, not Oracle Pipeline definitions</span></div>
      {cycles.length ? <div className="cycle-admin-list">{cycles.map((cycle) => <article className="cycle-admin-card" key={cycle.cycle_id}>
        <div className="cycle-admin-card__status"><span className={`state-dot state-${cycle.status === "COMPLETED" ? "completed" : "ready"}`} /><strong>{cycle.status.toLowerCase().replaceAll("_", " ")}</strong></div>
        <div className="cycle-admin-card__body"><span>{cycle.cycle_type.replaceAll("_", " ")}</span><h3>{cycle.name}</h3><p>{[cycle.scenario, cycle.year].filter(Boolean).join(" · ")} · {formatDate(cycle.start_date)}–{formatDate(cycle.due_date)}</p></div>
        <div className="cycle-admin-card__progress"><strong>{cycle.progress_percent}%</strong><div className="progress-track"><span style={{ width: `${cycle.progress_percent}%` }} /></div><small>{cycle.current_stage ? `Current: ${cycle.current_stage.name}` : "All stages complete"}</small></div>
        <a className="button button--quiet" href={`#tasks`}>View work <Icon name="arrow" /></a>
      </article>)}</div> : <div className="work-empty work-empty--compact"><span><Icon name="calendar" /></span><h2>No Planning cycles yet</h2><p>Open the first cycle to give planners a clear calendar, responsibilities, and dependency-aware work queue.</p><button className="button button--primary" onClick={onCreate}>Open first cycle</button></div>}
    </section>
  </>;
}

function CycleMetric({ label, value, tone, icon }: { label: string; value: number; tone: string; icon: "calendar" | "clock" | "check" | "activity" }) {
  return <article className={`summary-metric summary-metric--${tone}`}><span className="summary-icon"><Icon name={icon} /></span><span><strong>{value}</strong><small>{label}</small></span></article>;
}

interface CycleEditorProps extends PlanningCycleAdministrationProps {
  context: CycleContext;
  stages: StageEditor[];
  step: number;
  validation: string | null;
  onContext: (value: CycleContext) => void;
  onStages: (value: StageEditor[]) => void;
  onBack: () => void;
  onCancel: () => void;
  onNext: () => void;
  onCreate: () => Promise<void>;
}

function CycleEditor(props: CycleEditorProps) {
  const labels = ["Business context", "Stages & owners", "Review & open"];
  return <>
    <ol className="cycle-wizard-steps" aria-label="Cycle setup progress">{labels.map((label, index) => <li className={props.step === index + 1 ? "is-current" : props.step > index + 1 ? "is-complete" : ""} key={label}><span>{props.step > index + 1 ? <Icon name="check" /> : index + 1}</span><div><strong>{label}</strong><small>{index === 0 ? "What and when" : index === 1 ? "Accountability" : "Final check"}</small></div></li>)}</ol>
    <section className="panel cycle-editor-panel">
      {props.step === 1 && <ContextStep context={props.context} onChange={props.onContext} />}
      {props.step === 2 && <WorkflowStep data={props.data} stages={props.stages} onChange={props.onStages} />}
      {props.step === 3 && <ReviewStep context={props.context} data={props.data} stages={props.stages} />}
      {props.validation && <div className="inline-error cycle-validation" role="alert"><Icon name="alert" />{props.validation}</div>}
      <footer className="cycle-editor-actions">
        <button className="button button--quiet" onClick={props.step === 1 ? props.onCancel : props.onBack}>{props.step === 1 ? "Cancel" : "Back"}</button>
        {props.step < 3
          ? <button className="button button--primary" onClick={props.onNext}>Continue <Icon name="arrow" /></button>
          : <button className="button button--primary" disabled={props.busy} onClick={props.onCreate}>{props.busy ? <span className="spinner" /> : <Icon name="check" />} Open cycle</button>}
      </footer>
    </section>
  </>;
}

function ContextStep({ context, onChange }: { context: CycleContext; onChange: (value: CycleContext) => void }) {
  const update = (key: keyof CycleContext, value: string) => onChange({ ...context, [key]: value });
  return <div className="cycle-step-content">
    <div className="cycle-section-heading"><span className="eyebrow">Step 1</span><h2>Set the business calendar</h2><p>The Planning year is required and is retained as cycle context. Scenario is optional when Oracle category mappings already own it.</p></div>
    <div className="cycle-form-grid">
      <label className="field field--wide"><span>Cycle name *</span><input value={context.name} onChange={(event) => { const name = event.target.value; onChange({ ...context, name, code: codeFrom(name) }); }} placeholder="March Forecast FY27" /><small>Use a name that business users will recognize.</small></label>
      <label className="field"><span>Planning year *</span><input value={context.year} onChange={(event) => update("year", event.target.value)} placeholder="FY27" /></label>
      <label className="field"><span>Cycle type *</span><select value={context.cycleType} onChange={(event) => update("cycleType", event.target.value)}><option value="FORECAST">Forecast</option><option value="BUDGET">Budget</option><option value="PLAN">Strategic plan</option><option value="CLOSE">Period close</option></select></label>
      <label className="field"><span>Start date *</span><input type="date" value={context.startDate} onChange={(event) => update("startDate", event.target.value)} /></label>
      <label className="field"><span>Due date *</span><input type="date" value={context.dueDate} onChange={(event) => update("dueDate", event.target.value)} /></label>
      <label className="field"><span>Actual through</span><input value={context.actualThrough} onChange={(event) => update("actualThrough", event.target.value)} placeholder="Feb" /></label>
      <label className="field"><span>Forecast starts</span><input value={context.forecastStart} onChange={(event) => update("forecastStart", event.target.value)} placeholder="Mar" /></label>
      <label className="field"><span>Scenario</span><input value={context.scenario} onChange={(event) => update("scenario", event.target.value)} placeholder="Optional" /><small>Leave blank when Oracle mappings determine scenario.</small></label>
      <label className="field field--wide field--technical"><span>Cycle code *</span><input value={context.code} onChange={(event) => update("code", codeFrom(event.target.value))} placeholder="MAR_FORECAST_FY27" /><small>Stable internal reference generated from the name; edit only if needed.</small></label>
    </div>
  </div>;
}

function WorkflowStep({ data, stages, onChange }: { data: PlanningCycleAdministrationResponse; stages: StageEditor[]; onChange: (value: StageEditor[]) => void }) {
  function updateStage(stageId: string, patch: Partial<StageEditor>) { onChange(stages.map((stage) => stage.id === stageId ? { ...stage, ...patch } : stage)); }
  function updateTask(stageId: string, taskId: string, patch: Partial<TaskEditor>) {
    const current = stages.flatMap((stage) => stage.tasks).find((task) => task.id === taskId);
    const nextKey = patch.key;
    onChange(stages.map((stage) => ({
      ...stage,
      tasks: stage.tasks.map((task) => {
        if (task.id === taskId) return { ...task, ...patch };
        return nextKey && current && task.dependsOn === current.key ? { ...task, dependsOn: nextKey } : task;
      })
    })));
  }
  function removeStage(stageId: string) {
    const removed = new Set(stages.find((stage) => stage.id === stageId)?.tasks.map((task) => task.key) ?? []);
    onChange(stages.filter((stage) => stage.id !== stageId).map((stage) => ({ ...stage, tasks: stage.tasks.map((task) => removed.has(task.dependsOn) ? { ...task, dependsOn: "" } : task) })));
  }
  function removeTask(stageId: string, taskId: string) {
    const removedKey = stages.find((stage) => stage.id === stageId)?.tasks.find((task) => task.id === taskId)?.key;
    onChange(stages.map((stage) => ({ ...stage, tasks: stage.tasks.filter((task) => task.id !== taskId).map((task) => task.dependsOn === removedKey ? { ...task, dependsOn: "" } : task) })));
  }
  const allTasks = stages.flatMap((stage) => stage.tasks.map((task) => ({ ...task, stageId: stage.id })));
  return <div className="cycle-step-content">
    <div className="cycle-section-heading cycle-section-heading--actions"><div><span className="eyebrow">Step 2</span><h2>Define stages and accountable owners</h2><p>A stage is a business checkpoint. Responsibilities become visible in My Work and wait automatically for their selected prerequisite.</p></div><button className="button button--quiet" onClick={() => onChange([...stages, newStage(stages.length + 1, data)])}>+ Add stage</button></div>
    <div className="stage-editor-list">{stages.map((stage, stageIndex) => <article className="stage-editor" key={stage.id}>
      <header><span>{stageIndex + 1}</span><div><input aria-label={`Stage ${stageIndex + 1} name`} value={stage.name} onChange={(event) => updateStage(stage.id, { name: event.target.value, code: stage.code || codeFrom(event.target.value) })} /><small>Business stage</small></div>{stages.length > 1 && <button aria-label={`Remove ${stage.name || `stage ${stageIndex + 1}`}`} onClick={() => removeStage(stage.id)}><Icon name="close" /></button>}</header>
      <div className="stage-editor-code"><label><span>Stage code</span><input value={stage.code} onChange={(event) => updateStage(stage.id, { code: codeFrom(event.target.value) })} /></label><button className="button button--quiet" onClick={() => updateStage(stage.id, { tasks: [...stage.tasks, newTask(stage, data)] })}>+ Add responsibility</button></div>
      <div className="task-editor-list">{stage.tasks.map((task, taskIndex) => <div className="task-editor" key={task.id}>
        <div className="task-editor-heading"><strong>Responsibility {taskIndex + 1}</strong>{stage.tasks.length > 1 && <button aria-label={`Remove ${task.title || "responsibility"}`} onClick={() => removeTask(stage.id, task.id)}><Icon name="close" /></button>}</div>
        <div className="task-editor-grid">
          <label className="field field--wide"><span>What must be completed? *</span><input value={task.title} onChange={(event) => updateTask(stage.id, task.id, { title: event.target.value, key: task.key || codeFrom(event.target.value) })} placeholder="Review forecast assumptions" /></label>
          <label className="field field--wide"><span>Helpful instructions</span><textarea value={task.description} onChange={(event) => updateTask(stage.id, task.id, { description: event.target.value })} placeholder="Explain the expected outcome, not technical API steps." /></label>
          <label className="field"><span>Owner *</span><select value={task.assignee} onChange={(event) => updateTask(stage.id, task.id, { assignee: event.target.value })}><option value="">Select a person or role</option><optgroup label="Business roles">{data.roles.map((role) => <option value={`role:${role.code}`} key={role.code}>{role.name}</option>)}</optgroup><optgroup label="Named users">{data.users.map((user) => <option value={`user:${user.username}`} key={user.user_id}>{user.display_name} ({user.username})</option>)}</optgroup></select></label>
          <label className="field"><span>Due date and time</span><input type="datetime-local" value={task.dueAt} onChange={(event) => updateTask(stage.id, task.id, { dueAt: event.target.value })} /></label>
          <label className="field"><span>Priority</span><select value={task.priority} onChange={(event) => updateTask(stage.id, task.id, { priority: event.target.value as TaskEditor["priority"] })}><option>NORMAL</option><option>HIGH</option><option>CRITICAL</option><option>LOW</option></select></label>
          <label className="field"><span>Where should the user work?</span><select value={task.actionType} onChange={(event) => {
            const actionType = event.target.value;
            updateTask(stage.id, task.id, {
              actionType,
              ...(actionType === "UPDATE_SUBSTITUTION_VARIABLE" ? { assignee: preferredRole(data, "SERVICE_ADMINISTRATOR") } : {})
            });
          }}>{ACTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select>{task.actionType === "UPDATE_SUBSTITUTION_VARIABLE" && <small>This governed application setting is assigned to the Service Administrator.</small>}</label>
          <label className="field"><span>Wait for</span><select value={task.dependsOn} onChange={(event) => updateTask(stage.id, task.id, { dependsOn: event.target.value })}><option value="">No prerequisite</option>{allTasks.filter((candidate) => candidate.id !== task.id).map((candidate) => <option value={candidate.key} key={candidate.id}>{candidate.title || "Untitled responsibility"}</option>)}</select><small>This task stays waiting until the selected work is complete.</small></label>
          <label className="field field--technical"><span>Task key</span><input value={task.key} onChange={(event) => updateTask(stage.id, task.id, { key: codeFrom(event.target.value) })} /></label>
        </div>
        {task.actionType === "OPEN_DATA_REVIEW" && <DataReviewTaskSetup value={task.dataReview} onChange={(dataReview) => updateTask(stage.id, task.id, { dataReview })} />}
      </div>)}</div>
    </article>)}</div>
  </div>;
}

function DataReviewTaskSetup({ value, onChange }: { value: DataReviewEditor; onChange: (value: DataReviewEditor) => void }) {
  const update = <K extends keyof DataReviewEditor>(key: K, next: DataReviewEditor[K]) => onChange({ ...value, [key]: next });
  return <details className="data-review-task-setup"><summary><span><Icon name="data" /><strong>Prefill Data Review for the planner</strong></span><small>Optional, but recommended</small></summary><div className="data-review-task-setup__body"><p>Define this once so the planner opens the exact cube intersection and validation method without rebuilding it.</p><div className="task-editor-grid"><label className="field"><span>Source cube</span><input value={value.cube} onChange={(event) => update("cube", event.target.value)} placeholder="Plan1" /></label><label className="field"><span>Validation method</span><select value={value.validationType} onChange={(event) => update("validationType", event.target.value as DataReviewEditor["validationType"])}><option value="QUALITY">Data quality checks</option><option value="COMPARISON">Compare source and target</option></select></label><label className="field field--wide"><span>POV members</span><textarea value={value.pov} onChange={(event) => update("pov", event.target.value)} placeholder={"Scenario=Forecast\nVersion=Working\nYear=FY27"} /><small>One Dimension=Member per line.</small></label><label className="field field--wide"><span>Rows *</span><textarea value={value.rows} onChange={(event) => update("rows", event.target.value)} placeholder="Account=Revenue|Gross Profit" /><small>Use | between multiple members.</small></label><label className="field field--wide"><span>Columns *</span><textarea value={value.columns} onChange={(event) => update("columns", event.target.value)} placeholder="Period=Jan|Feb|Mar" /><small>Additional axis dimensions can use another line.</small></label>{value.validationType === "QUALITY" ? <><label className="validation-rule"><input type="checkbox" checked={value.checkMissing} onChange={(event) => update("checkMissing", event.target.checked)} /><span><strong>Fail missing values</strong></span></label><label className="validation-rule"><input type="checkbox" checked={value.checkZero} onChange={(event) => update("checkZero", event.target.checked)} /><span><strong>Warn on zero values</strong></span></label></> : <><label className="field"><span>Target cube *</span><input value={value.targetCube} onChange={(event) => update("targetCube", event.target.value)} placeholder="Reporting" /></label><label className="field"><span>Allowed difference</span><input type="number" min="0" step="any" value={value.tolerance} onChange={(event) => update("tolerance", event.target.value)} /></label></>}</div></div></details>;
}

function ReviewStep({ context, data, stages }: { context: CycleContext; data: PlanningCycleAdministrationResponse; stages: StageEditor[] }) {
  const tasks = stages.flatMap((stage) => stage.tasks.map((task) => ({ ...task, stageName: stage.name })));
  const ownerName = (value: string) => {
    const [kind, code] = value.split(":", 2);
    return kind === "role" ? data.roles.find((role) => role.code === code)?.name : data.users.find((user) => user.username === code)?.display_name;
  };
  return <div className="cycle-step-content">
    <div className="cycle-section-heading"><span className="eyebrow">Step 3</span><h2>Review before opening</h2><p>This is the final check. Opening the cycle immediately publishes these responsibilities to the assigned users’ My Work queues.</p></div>
    <div className="cycle-review-summary"><article><small>Cycle</small><strong>{context.name}</strong><span>{context.cycleType.toLowerCase()} · {context.year}</span></article><article><small>Calendar</small><strong>{formatDate(context.startDate)}–{formatDate(context.dueDate)}</strong><span>{context.forecastStart ? `Forecast starts ${context.forecastStart}` : "No forecast start specified"}</span></article><article><small>Workflow</small><strong>{stages.length} stages · {tasks.length} responsibilities</strong><span>Business accountability only</span></article></div>
    <ol className="cycle-review-stages">{stages.map((stage, index) => <li key={stage.id}><span>{index + 1}</span><div><h3>{stage.name}</h3>{stage.tasks.map((task) => <div className="cycle-review-task" key={task.id}><Icon name="tasks" /><span><strong>{task.title}</strong><small>{ownerName(task.assignee) || "Owner not selected"} · {task.priority.toLowerCase()}{task.dependsOn ? ` · waits for ${tasks.find((item) => item.key === task.dependsOn)?.title}` : ""}</small></span></div>)}</div></li>)}</ol>
    <div className="cycle-open-notice"><Icon name="alert" /><div><strong>Opening is immediate</strong><p>The cycle starts in Open status. No Oracle job or Pipeline runs from this action; assigned users simply receive their governed business work.</p></div></div>
  </div>;
}

function forecastStarter(data: PlanningCycleAdministrationResponse): StageEditor[] {
  const planner = preferredRole(data, "USER");
  const consultant = preferredRole(data, "SERVICE_ADMINISTRATOR");
  const operator = preferredRole(data, "POWER_USER");
  const input = starterStage("PLANNING_INPUT", "Planning Input", "COMPLETE_PLAN", "Complete planning input", planner, "OPEN_DATA_REVIEW", "RECONCILE_ACTUALS");
  input.tasks.push({ id: uid(), key: "SUBMIT_PLAN", title: "Submit plan for approval", description: "Confirm validation is complete, then submit the Planning unit for manager review.", priority: "HIGH", assignee: planner, dueAt: "", actionType: "SUBMIT_APPROVAL", dependsOn: "COMPLETE_PLAN", dataReview: emptyDataReview() });
  return [
    starterStage("ACTUALS", "Actuals & Reconciliation", "RECONCILE_ACTUALS", "Reconcile actual data", consultant, "OPEN_DATA_REVIEW"),
    input,
    starterStage("REVIEW", "Review & Approval", "REVIEW_PLAN", "Review submitted plan", operator, "REVIEW_APPROVAL", "SUBMIT_PLAN"),
    starterStage("PUBLISH", "Publish & Reporting", "PUBLISH_RESULTS", "Publish approved results", consultant, "OPEN_REPORT", "REVIEW_PLAN")
  ];
}

function starterStage(code: string, name: string, key: string, title: string, assignee: string, actionType: string, dependsOn = ""): StageEditor {
  const id = uid();
  return { id, code, name, tasks: [{ id: uid(), key, title, description: "", priority: "NORMAL", assignee, dueAt: "", actionType, dependsOn, dataReview: emptyDataReview() }] };
}

function newStage(sequence: number, data: PlanningCycleAdministrationResponse): StageEditor {
  const id = uid();
  return { id, code: `STAGE_${sequence}`, name: `Stage ${sequence}`, tasks: [newTask({ id, code: `STAGE_${sequence}`, name: `Stage ${sequence}`, tasks: [] }, data)] };
}

function newTask(stage: StageEditor, data: PlanningCycleAdministrationResponse): TaskEditor {
  return { id: uid(), key: `${stage.code}_TASK_${stage.tasks.length + 1}`, title: "", description: "", priority: "NORMAL", assignee: preferredRole(data, "USER"), dueAt: "", actionType: "OPEN_MY_WORK", dependsOn: "", dataReview: emptyDataReview() };
}

function preferredRole(data: PlanningCycleAdministrationResponse, code: string) {
  const role = data.roles.find((item) => item.code === code) ?? data.roles[0];
  return role ? `role:${role.code}` : "";
}

function initialContext(): CycleContext {
  const startDate = dateValue(new Date());
  const due = new Date();
  due.setDate(due.getDate() + 10);
  return { name: "", code: "", cycleType: "FORECAST", scenario: "", year: "", actualThrough: "", forecastStart: "", startDate, dueDate: dateValue(due) };
}

function validateStep(step: number, context: CycleContext, stages: StageEditor[]) {
  if (step === 1) {
    if (!context.name.trim() || !context.code.trim() || !context.year.trim() || !context.startDate || !context.dueDate) return "Enter the cycle name, Planning year, start date, and due date before continuing.";
    if (context.dueDate < context.startDate) return "The cycle due date cannot be before its start date.";
  }
  if (step >= 2) {
    if (!stages.length) return "Add at least one business stage.";
    if (stages.some((stage) => !stage.name.trim() || !stage.code.trim())) return "Every stage requires a name.";
    if (new Set(stages.map((stage) => stage.code)).size !== stages.length) return "Each business stage must have a unique stage code.";
    if (stages.some((stage) => !stage.tasks.length)) return "Every stage requires at least one responsibility.";
    const tasks = stages.flatMap((stage) => stage.tasks);
    if (tasks.some((task) => !task.title.trim() || !task.key.trim() || !task.assignee)) return "Every responsibility requires a clear title and an owner.";
    const keys = tasks.map((task) => task.key);
    if (new Set(keys).size !== keys.length) return "Each responsibility must have a unique task key.";
    if (hasDependencyCycle(tasks)) return "Responsibilities cannot form a circular dependency. Change one of the ‘Wait for’ selections.";
    const configuredReviews = tasks.filter((task) => task.actionType === "OPEN_DATA_REVIEW" && hasReviewConfiguration(task.dataReview));
    if (configuredReviews.some((task) => !task.dataReview.cube.trim() || !task.dataReview.rows.trim() || !task.dataReview.columns.trim())) return "A prefilled Data Review requires a source cube, rows, and columns.";
    if (configuredReviews.some((task) => task.dataReview.validationType === "COMPARISON" && !task.dataReview.targetCube.trim())) return "A comparison Data Review requires a target cube.";
  }
  return null;
}

function toPayload(context: CycleContext, stages: StageEditor[]): PlanningCycleCreateInput {
  return {
    code: context.code,
    name: context.name.trim(),
    cycle_type: context.cycleType,
    process_code: null,
    scenario: nullable(context.scenario),
    year: context.year.trim(),
    actual_through_period: nullable(context.actualThrough),
    forecast_start_period: nullable(context.forecastStart),
    start_date: context.startDate,
    due_date: context.dueDate,
    stages: stages.map((stage, index) => ({ code: stage.code, name: stage.name.trim(), sequence: index + 1, start_date: null, due_date: null })),
    tasks: stages.flatMap((stage) => stage.tasks.map((task) => {
      const [assigneeType, assigneeValue] = task.assignee.split(":", 2);
      return {
        key: task.key,
        stage_code: stage.code,
        title: task.title.trim(),
        description: task.description.trim(),
        task_type: "BUSINESS_TASK",
        priority: task.priority,
        assigned_username: assigneeType === "user" ? assigneeValue : null,
        assigned_role_code: assigneeType === "role" ? assigneeValue : null,
        entity: null,
        scenario: nullable(context.scenario),
        period: nullable(context.forecastStart),
        due_at: task.dueAt ? new Date(task.dueAt).toISOString() : null,
        action_type: task.actionType,
        action_config: task.actionType === "OPEN_MY_WORK" ? { route: "#tasks" } : task.actionType === "OPEN_DATA_REVIEW" && hasReviewConfiguration(task.dataReview) ? { data_review: dataReviewConfiguration(task.dataReview) } : {},
        depends_on: task.dependsOn ? [task.dependsOn] : []
      };
    }))
  };
}

function emptyDataReview(): DataReviewEditor {
  return { cube: "", pov: "", rows: "", columns: "", validationType: "QUALITY", checkMissing: true, checkZero: false, targetCube: "", tolerance: "0" };
}

function hasReviewConfiguration(value: DataReviewEditor) {
  return Boolean(value.cube.trim() || value.pov.trim() || value.rows.trim() || value.columns.trim() || value.targetCube.trim());
}

function dataReviewConfiguration(value: DataReviewEditor) {
  return {
    slice: {
      cube: value.cube.trim(),
      pov: Object.fromEntries(axisLines(value.pov, true).map((item) => [item.dimension, item.members[0]])),
      rows: axisLines(value.rows),
      columns: axisLines(value.columns)
    },
    validation_type: value.validationType,
    rules: value.validationType === "QUALITY" ? { check_missing: value.checkMissing, check_zero: value.checkZero, minimum: null, maximum: null, max_issues: 500 } : undefined,
    target_cube: value.validationType === "COMPARISON" ? value.targetCube.trim() : undefined,
    tolerance: value.validationType === "COMPARISON" ? Number(value.tolerance || 0) : undefined
  };
}

function axisLines(value: string, single = false) {
  return value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
    const separator = line.indexOf("=");
    const dimension = (separator < 0 ? line : line.slice(0, separator)).trim();
    const rawMembers = separator < 0 ? "" : line.slice(separator + 1);
    const members = rawMembers.split("|").map((member) => member.trim()).filter(Boolean);
    return { dimension, members: single ? members.slice(0, 1) : members };
  }).filter((item) => item.dimension && item.members.length);
}

function nullable(value: string) { return value.trim() || null; }
function codeFrom(value: string) { return value.toUpperCase().trim().replace(/[^A-Z0-9]+/g, "_").replace(/^_+|_+$/g, ""); }
function uid() { return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`; }
function dateValue(value: Date) { return value.toISOString().slice(0, 10); }
function formatDate(value: string) { return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(new Date(`${value}T00:00:00`)); }
function daysUntil(value: string) { return Math.ceil((new Date(`${value}T23:59:59`).getTime() - Date.now()) / 86_400_000); }
function hasDependencyCycle(tasks: TaskEditor[]) {
  const graph = new Map(tasks.map((task) => [task.key, task.dependsOn]));
  return tasks.some((task) => {
    const visited = new Set<string>();
    let current = task.key;
    while (current) {
      if (visited.has(current)) return true;
      visited.add(current);
      current = graph.get(current) ?? "";
    }
    return false;
  });
}
