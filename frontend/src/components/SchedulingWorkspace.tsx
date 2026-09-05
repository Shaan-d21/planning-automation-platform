import { useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../api/client";
import type {
  AutomationSchedule,
  AutomationScheduleRunEvidence,
  AutomationScheduleRunsResponse,
  AutomationScheduleRunStatus,
  AutomationScheduleInput,
  AutomationTargetType,
  AutomationInputPolicy,
  AutomationMisfirePolicy,
  PipelineCatalogResponse,
  PipelineOperationPreview,
  ScheduleFrequency,
  SchedulePreviewResponse
} from "../api/types";
import { Icon } from "./Icon";
import { ConfirmationDialog, FeedbackBanner, WorkspaceLoading } from "./Feedback";

interface Props {
  csrfToken: string;
  onOpenExecution: (executionId: string) => Promise<void>;
}

interface Editor extends AutomationScheduleInput {
  scheduleId: number | null;
  phase: "CONFIGURE" | "REVIEW";
  pipeline: PipelineOperationPreview | null;
  recurrence: SchedulePreviewResponse | null;
}

const FREQUENCIES: { value: ScheduleFrequency; label: string; help: string }[] = [
  { value: "ONE_TIME", label: "One time", help: "Run once at this date and time." },
  { value: "DAILY", label: "Daily", help: "Run every day at this local time." },
  { value: "WEEKLY", label: "Weekly", help: "Run each week on this weekday." },
  { value: "MONTHLY", label: "Monthly", help: "Run each month on this day." }
];

const TIMEZONES = [
  "UTC", "Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Europe/London",
  "Europe/Paris", "America/New_York", "America/Chicago",
  "America/Los_Angeles", "Australia/Sydney"
];

export function SchedulingWorkspace({ csrfToken, onOpenExecution }: Props) {
  const [catalog, setCatalog] = useState<PipelineCatalogResponse | null>(null);
  const [schedules, setSchedules] = useState<AutomationSchedule[]>([]);
  const [history, setHistory] = useState<AutomationScheduleRunsResponse | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [query, setQuery] = useState("");
  const [historyStatus, setHistoryStatus] = useState<"ALL" | AutomationScheduleRunStatus>("ALL");
  const [historySchedule, setHistorySchedule] = useState("ALL");
  const [historyFrom, setHistoryFrom] = useState("");
  const [historyTo, setHistoryTo] = useState("");
  const [historyLoading, setHistoryLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<{ title: string; description: string; warning: string; confirmLabel: string; action: () => Promise<void> } | null>(null);

  async function load() {
    const [nextCatalog, nextSchedules, nextHistory] = await Promise.all([
      api.pipelineCatalog(),
      api.schedules(),
      api.scheduleRuns()
    ]);
    setCatalog(nextCatalog);
    setSchedules(nextSchedules.schedules);
    setHistory(nextHistory);
  }

  async function loadHistory() {
    setHistoryLoading(true);
    setError(null);
    const query = new URLSearchParams();
    if (historyStatus !== "ALL") query.set("status", historyStatus);
    if (historySchedule !== "ALL") query.set("schedule_id", historySchedule);
    if (historyFrom) query.set("scheduled_from", `${historyFrom}T00:00:00Z`);
    if (historyTo) query.set("scheduled_to", `${historyTo}T23:59:59Z`);
    try {
      setHistory(await api.scheduleRuns(query.toString()));
    } catch (reason) {
      setError(message(reason));
    } finally {
      setHistoryLoading(false);
    }
  }

  useEffect(() => {
    load().catch((reason) => setError(message(reason))).finally(() => setLoading(false));
  }, []);

  const pipelineNames = useMemo(() => new Map(
    (catalog?.pipelines ?? []).map((pipeline) => [pipeline.code, pipeline.name])
  ), [catalog]);
  const visible = schedules.filter((schedule) => {
    const needle = query.trim().toLowerCase();
    return !needle || [schedule.name, schedule.target_key, pipelineNames.get(schedule.target_key) ?? ""]
      .some((value) => value.toLowerCase().includes(needle));
  });
  const next = schedules.filter((item) => item.enabled && item.next_run_at)
    .sort((left, right) => String(left.next_run_at).localeCompare(String(right.next_run_at)))[0];

  function newEditor(): Editor | null {
    const pipeline = catalog?.pipelines[0];
    const targetType: AutomationTargetType = pipeline
      ? "ORACLE_PIPELINE"
      : "RTP_REGISTRY_SYNC";
    return {
      scheduleId: null,
      name: pipeline
        ? `${pipeline.name} · Monthly`
        : "Business Rule prompts · Weekly sync",
      target_type: targetType,
      target_key: pipeline?.code ?? "BISP_CalcManager_RTP",
      frequency: "MONTHLY",
      timezone: browserTimezone(),
      first_run_local: futureLocalDateTime(),
      input_policy: pipeline ? "ORACLE_DEFAULTS" : "FIXED",
      variables: {},
      inbox_files: {},
      misfire_policy: "RUN_ONCE",
      enabled: true,
      phase: "CONFIGURE",
      pipeline: null,
      recurrence: null
    };
  }

  function edit(schedule: AutomationSchedule) {
    setError(null);
    setEditor({
      scheduleId: schedule.schedule_id,
      name: schedule.name,
      target_type: schedule.target_type,
      target_key: schedule.target_key,
      frequency: schedule.frequency,
      timezone: schedule.timezone,
      first_run_local: schedule.first_run_local.slice(0, 16),
      input_policy: schedule.input_policy,
      variables: schedule.variables,
      inbox_files: schedule.inbox_files,
      misfire_policy: schedule.misfire_policy,
      enabled: schedule.enabled,
      phase: "CONFIGURE",
      pipeline: null,
      recurrence: null
    });
  }

  async function review() {
    if (!editor) return;
    setBusy(true);
    setError(null);
    let inspected: Editor | null = null;
    try {
      if (editor.target_type === "ORACLE_PIPELINE") {
        const pipeline = await api.pipelinePreflight(editor.target_key);
        inspected = { ...normalizeInputs(editor, pipeline.preview), pipeline: pipeline.preview };
        setEditor(inspected);
        if (editor.input_policy === "FIXED" && editor.pipeline === null) {
          return;
        }
      } else {
        inspected = {
          ...editor,
          pipeline: null,
          variables: {},
          inbox_files: {},
          input_policy: "FIXED"
        };
      }
      const recurrence = await api.previewSchedule(payload(inspected), csrfToken);
      setEditor({ ...inspected, recurrence, phase: "REVIEW" });
    } catch (reason) {
      if (inspected) setEditor(inspected);
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!editor?.recurrence) return;
    setBusy(true);
    setError(null);
    try {
      const result = editor.scheduleId === null
        ? await api.createSchedule(payload(editor), csrfToken)
        : await api.updateSchedule(editor.scheduleId, payload(editor), csrfToken);
      setEditor(null);
      setNotice(result.message);
      await load();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function confirmed() {
    if (!confirmation) return;
    setBusy(true);
    setError(null);
    try {
      await confirmation.action();
      setConfirmation(null);
      await load();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <WorkspaceLoading label="Schedules" message="Loading unattended automations..." />;

  return <section className="workspace-page scheduling-workspace">
    <div className="schedule-react-intro">
      <div><span className="eyebrow">Unattended Oracle automation</span><h1>Schedules</h1><p>Run Oracle Pipelines or keep the Business Rule runtime-prompt registry current on a governed recurrence.</p></div>
      <button className="button button--primary" onClick={() => { setError(null); setEditor(newEditor()); }}><Icon name="calendar" /> Create schedule</button>
    </div>

    {notice && <FeedbackBanner tone="success" title="Schedule updated" message={notice} onDismiss={() => setNotice(null)} />}
    {error && !editor && <FeedbackBanner tone="error" title="Scheduling needs attention" message={error} onDismiss={() => setError(null)} />}

    <div className="schedule-metrics">
      <ScheduleMetric icon="calendar" label="Active schedules" value={schedules.filter((item) => item.enabled).length} />
      <ScheduleMetric icon="clock" label="Next automatic run" value={next?.next_run_at ? formatDateTime(next.next_run_at) : "Not scheduled"} />
      <ScheduleMetric icon="alert" label="Needs attention" value={schedules.filter((item) => ["FAILED", "SKIPPED"].includes(item.last_outcome)).length} />
    </div>

    <aside className="schedule-policy"><span><Icon name="data" /></span><div><strong>Oracle remains the source of truth</strong><p>Pipeline stages stay configured in Oracle. RTP synchronization generates a fresh Calculation Manager snapshot through Oracle REST, imports it, and removes the temporary snapshot automatically.</p></div><span className="live-badge"><i /> Live validation</span></aside>

    <section className="panel">
      <div className="panel-heading schedule-list-heading"><div><span className="eyebrow">Automation calendar</span><h2>Automation schedules</h2><p>Pause, edit, or archive a recurrence. Every enabled target is revalidated against Oracle before it runs.</p></div><span className="result-count">{visible.length} shown</span></div>
      <div className="schedule-filters"><label className="search-field"><Icon name="search" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search schedule, Pipeline, or source file" /></label></div>
      {visible.length ? <div className="schedule-card-list">{visible.map((schedule) => <ScheduleCard key={schedule.schedule_id} schedule={schedule} targetName={pipelineNames.get(schedule.target_key) ?? schedule.target_key} onEdit={() => edit(schedule)} onToggle={() => setConfirmation({ title: schedule.enabled ? "Pause this schedule?" : "Resume this schedule?", description: schedule.enabled ? "Future occurrences will stop until it is resumed." : "The Oracle target will be validated before the recurrence is reactivated.", warning: "Existing execution history is retained.", confirmLabel: schedule.enabled ? "Pause schedule" : "Resume schedule", action: async () => { const result = await api.setScheduleEnabled(schedule.schedule_id, !schedule.enabled, csrfToken); setNotice(result.message); } })} onDelete={() => setConfirmation({ title: "Archive this schedule?", description: "It will disappear from the active calendar and will not run again.", warning: "Execution history is retained for audit purposes.", confirmLabel: "Archive schedule", action: async () => { const result = await api.deleteSchedule(schedule.schedule_id, csrfToken); setNotice(result.message); } })} onOpenExecution={schedule.last_execution_id ? () => onOpenExecution(schedule.last_execution_id!) : undefined} />)}</div> : <div className="work-empty"><span><Icon name="calendar" /></span><h2>No schedules yet</h2><p>Create a recurrence for an approved Oracle Pipeline or RTP registry source.</p></div>}
    </section>

    <section className="panel schedule-history-panel">
      <div className="panel-heading schedule-list-heading"><div><span className="eyebrow">Scheduler evidence</span><h2>Occurrence history</h2><p>Review every automatic handoff. Submitted entries link to the complete Oracle execution evidence.</p></div><span className="result-count">{history?.summary.total ?? 0} found</span></div>
      <div className="schedule-history-summary"><span><small>Submitted</small><strong>{history?.summary.submitted ?? 0}</strong></span><span><small>Completed</small><strong>{history?.summary.completed ?? 0}</strong></span><span><small>Failed</small><strong>{history?.summary.failed ?? 0}</strong></span><span><small>Skipped</small><strong>{history?.summary.skipped ?? 0}</strong></span><span><small>Claimed</small><strong>{history?.summary.claimed ?? 0}</strong></span></div>
      <div className="schedule-history-filters"><label><span>Schedule</span><select value={historySchedule} onChange={(event) => setHistorySchedule(event.target.value)}><option value="ALL">All schedules</option>{schedules.map((item) => <option key={item.schedule_id} value={item.schedule_id}>{item.name}</option>)}</select></label><label><span>Outcome</span><select value={historyStatus} onChange={(event) => setHistoryStatus(event.target.value as typeof historyStatus)}><option value="ALL">All outcomes</option><option value="SUBMITTED">Submitted</option><option value="COMPLETED">Completed</option><option value="FAILED">Failed</option><option value="SKIPPED">Skipped</option><option value="CLAIMED">Claimed</option></select></label><label><span>From</span><input type="date" value={historyFrom} onChange={(event) => setHistoryFrom(event.target.value)} /></label><label><span>To</span><input type="date" value={historyTo} onChange={(event) => setHistoryTo(event.target.value)} /></label><button className="button button--quiet" disabled={historyLoading} onClick={loadHistory}>{historyLoading ? "Loading..." : "Apply filters"}</button></div>
      {history?.runs.length ? <div className="schedule-history-list">{history.runs.map((run) => <ScheduleRunRow key={run.run_id} run={run} onOpenExecution={run.execution_id ? () => onOpenExecution(run.execution_id!) : undefined} />)}</div> : <div className="schedule-history-empty"><Icon name="clock" /><div><strong>No occurrences match these filters</strong><p>Evidence appears after the scheduler claims its first due automation.</p></div></div>}
    </section>

    {editor && catalog && <ScheduleEditor editor={editor} pipelines={catalog.pipelines} busy={busy} error={error} onChange={setEditor} onReview={review} onSave={save} onClose={() => { setEditor(null); setError(null); }} />}
    {confirmation && <ConfirmationDialog title={confirmation.title} description={confirmation.description} warning={confirmation.warning} confirmLabel={confirmation.confirmLabel} tone="danger" busy={busy} error={error} onConfirm={confirmed} onClose={() => { setConfirmation(null); setError(null); }} />}
  </section>;
}

function ScheduleEditor({ editor, pipelines, busy, error, onChange, onReview, onSave, onClose }: { editor: Editor; pipelines: PipelineCatalogResponse["pipelines"]; busy: boolean; error: string | null; onChange: (editor: Editor) => void; onReview: () => Promise<void>; onSave: () => Promise<void>; onClose: () => void }) {
  const preview = editor.pipeline;
  const fixed = editor.input_policy === "FIXED";
  const pipelineTarget = editor.target_type === "ORACLE_PIPELINE";
  function resetDiscovery(change: Partial<Editor>) { onChange({ ...editor, ...change, phase: "CONFIGURE", pipeline: null, recurrence: null }); }
  function updateConfiguration(change: Partial<Editor>) { onChange({ ...editor, ...change, phase: "CONFIGURE", recurrence: null }); }
  function changeTargetType(targetType: AutomationTargetType) {
    const pipeline = pipelines[0];
    resetDiscovery({
      target_type: targetType,
      target_key: targetType === "ORACLE_PIPELINE" ? pipeline?.code ?? "" : "BISP_CalcManager_RTP",
      input_policy: targetType === "ORACLE_PIPELINE" ? "ORACLE_DEFAULTS" : "FIXED",
      variables: {},
      inbox_files: {},
      name: editor.scheduleId === null
        ? targetType === "ORACLE_PIPELINE"
          ? `${pipeline?.name ?? "Oracle Pipeline"} · Monthly`
          : "Business Rule prompts · Weekly sync"
        : editor.name
    });
  }
  return <div className="modal-backdrop schedule-modal" role="presentation"><section className="schedule-editor" role="dialog" aria-modal="true" aria-label="Configure the schedule">
    <header><div><span className="eyebrow">{editor.scheduleId === null ? "New automation" : "Edit automation"}</span><h2>{editor.phase === "CONFIGURE" ? "Schedule Oracle automation" : "Review before saving"}</h2><p>{editor.phase === "CONFIGURE" ? "Choose one approved target and when it should run unattended." : "Oracle and the recurrence have both passed live validation."}</p></div><button aria-label="Close schedule editor" disabled={busy} onClick={onClose}><Icon name="close" /></button></header>
    <div className="schedule-editor-progress"><span className={editor.phase === "CONFIGURE" ? "is-current" : "is-complete"}><i>{editor.phase === "REVIEW" ? <Icon name="check" /> : "1"}</i><strong>Configure</strong></span><span className={editor.phase === "REVIEW" ? "is-current" : ""}><i>2</i><strong>Review</strong></span></div>
    {editor.phase === "CONFIGURE" ? <div className="schedule-editor-body">
      <section><Heading number="1" title="What should run?" help="Choose a governed automation type, then select its live Oracle target." /><div className="schedule-form-grid"><label className="field"><span>Automation type *</span><select aria-label="Automation type" value={editor.target_type} onChange={(event) => changeTargetType(event.target.value as AutomationTargetType)}><option value="ORACLE_PIPELINE" disabled={!pipelines.length}>Oracle Pipeline</option><option value="RTP_REGISTRY_SYNC">Business Rule RTP registry sync</option></select><small>No credentials or local computer paths are stored.</small></label>{pipelineTarget ? <label className="field"><span>Oracle Pipeline *</span><select aria-label="Oracle Pipeline" value={editor.target_key} onChange={(event) => { const selected = pipelines.find((item) => item.code === event.target.value); resetDiscovery({ target_key: event.target.value, name: editor.scheduleId === null ? `${selected?.name ?? event.target.value} · Monthly` : editor.name, variables: {}, inbox_files: {} }); }}>{pipelines.map((item) => <option value={item.code} key={item.code}>{item.name} ({item.code})</option>)}</select><small>Stages and job configuration stay in Oracle.</small></label> : <label className="field"><span>Generated snapshot prefix *</span><input aria-label="Generated snapshot prefix" value={editor.target_key} onChange={(event) => resetDiscovery({ target_key: event.target.value })} /><small>A unique temporary snapshot is generated through Oracle REST for each run.</small></label>}<label className="field"><span>Schedule name *</span><input aria-label="Schedule name" value={editor.name} onChange={(event) => updateConfiguration({ name: event.target.value })} /><small>Use a business-friendly name for the calendar.</small></label></div></section>
      <section><Heading number="2" title={pipelineTarget ? "How should inputs be supplied?" : "How will synchronization work?"} help={pipelineTarget ? "Unattended runs cannot pause for local uploads or keyboard input." : "Oracle generates a fresh Calculation Manager snapshot that is parsed and atomically published to the RTP registry."} /><div className="schedule-form-grid">{pipelineTarget && <label className="field"><span>Input strategy *</span><select aria-label="Input strategy" value={editor.input_policy} onChange={(event) => resetDiscovery({ input_policy: event.target.value as AutomationInputPolicy, variables: {}, inbox_files: {} })}><option value="ORACLE_DEFAULTS">Use Oracle Pipeline defaults</option><option value="FIXED">Use fixed unattended values</option></select><small>{fixed ? "Values are stored without credentials and validated on every run." : "Oracle must already provide every required value and file reference."}</small></label>}<label className="field"><span>Missed occurrence *</span><select aria-label="Missed occurrence" value={editor.misfire_policy} onChange={(event) => updateConfiguration({ misfire_policy: event.target.value as AutomationMisfirePolicy })}><option value="RUN_ONCE">Run once after service recovery</option><option value="SKIP">Skip missed occurrence</option></select><small>Controls what happens after platform downtime.</small></label></div>
        {pipelineTarget && preview && fixed && <PipelineInputs editor={editor} preview={preview} onChange={updateConfiguration} />}
        {pipelineTarget && !preview && <aside className="schedule-file-strategy"><Icon name="data" /><div><strong>Live inputs are discovered during review</strong><p>The platform will inspect the current Pipeline variables, stages, and file requirements before anything is saved.</p></div></aside>}
        {!pipelineTarget && <aside className="schedule-file-strategy"><Icon name="data" /><div><strong>No manual export or upload</strong><p>The platform exports the complete Calculation Manager category, waits for Oracle, downloads the snapshot, synchronizes RTP definitions, and removes the temporary package.</p></div></aside>}
      </section>
      <section><Heading number="3" title="When should it run?" help="The first occurrence anchors the local time, weekday, or day of month." /><div className="schedule-form-grid schedule-form-grid--three"><label className="field"><span>Frequency *</span><select aria-label="Frequency" value={editor.frequency} onChange={(event) => updateConfiguration({ frequency: event.target.value as ScheduleFrequency })}>{FREQUENCIES.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select><small>{FREQUENCIES.find((item) => item.value === editor.frequency)?.help}</small></label><label className="field"><span>First run *</span><input aria-label="First run" type="datetime-local" value={editor.first_run_local} onChange={(event) => updateConfiguration({ first_run_local: event.target.value })} /><small>Date and time in the selected timezone.</small></label><label className="field"><span>Timezone *</span><select aria-label="Timezone" value={editor.timezone} onChange={(event) => updateConfiguration({ timezone: event.target.value })}>{timezoneOptions(editor.timezone).map((item) => <option key={item}>{item}</option>)}</select><small>Daylight-saving changes are handled automatically.</small></label></div></section>
      <label className="schedule-enable-choice"><input type="checkbox" checked={editor.enabled} onChange={(event) => updateConfiguration({ enabled: event.target.checked })} /><span /><div><strong>Enable after saving</strong><small>Turn off to save this recurrence in a paused state.</small></div></label>
      {error && <div className="schedule-form-error" role="alert"><Icon name="alert" />{error}</div>}
    </div> : <ScheduleReview editor={editor} />}
    <footer>{editor.phase === "CONFIGURE" ? <><button className="button button--quiet" disabled={busy} onClick={onClose}>Cancel</button><button className="button button--primary" disabled={busy || !editor.name.trim() || !editor.target_key || !editor.first_run_local} onClick={onReview}>{busy ? <><span className="spinner" /> Checking Oracle</> : <>{pipelineTarget && fixed && !editor.pipeline ? "Load Pipeline inputs" : "Validate & review"} <Icon name="arrow" /></>}</button></> : <><button className="button button--quiet" disabled={busy} onClick={() => onChange({ ...editor, phase: "CONFIGURE", recurrence: null })}>Back to edit</button><button className="button button--primary" disabled={busy || !editor.recurrence} onClick={onSave}>{busy ? "Saving..." : editor.scheduleId === null ? "Create schedule" : "Save changes"}</button></>}</footer>
  </section></div>;
}

function PipelineInputs({ editor, preview, onChange }: { editor: Editor; preview: PipelineOperationPreview; onChange: (change: Partial<Editor>) => void }) {
  return <div className="schedule-pipeline-inputs"><div className="schedule-form-grid">{preview.variables.map((variable) => <label className="field" key={variable.name}><span>{variable.display_name}{variable.required ? " *" : ""}</span><input value={editor.variables[variable.name] ?? variable.default_value ?? ""} onChange={(event) => onChange({ variables: { ...editor.variables, [variable.name]: event.target.value } })} /><small>Oracle variable: {variable.name}</small></label>)}</div><div className="schedule-form-grid">{preview.file_requirements.map((file) => <label className="field" key={file.key}><span>{file.display_name}{file.required ? " *" : ""}</span><input placeholder={file.configured_reference ?? "Existing Oracle Inbox file name"} value={editor.inbox_files[file.key] ?? file.configured_reference ?? ""} onChange={(event) => onChange({ inbox_files: { ...editor.inbox_files, [file.key]: event.target.value } })} /><small>Existing Oracle Inbox reference only; no local path.</small></label>)}</div></div>;
}

function ScheduleReview({ editor }: { editor: Editor }) {
  const pipelineTarget = editor.target_type === "ORACLE_PIPELINE";
  return <div className="schedule-editor-body schedule-review"><div className="schedule-review-success"><Icon name="check" /><div><strong>Live validation passed</strong><p>{editor.recurrence?.message}</p></div></div><div className="schedule-review-grid">{pipelineTarget ? <><Fact label="Pipeline" value={`${editor.pipeline?.display_name ?? editor.target_key} (${editor.target_key})`} /><Fact label="Stages" value={editor.pipeline?.stages.map((stage) => stage.display_name).join(" → ") || "No stages returned"} /><Fact label="Input strategy" value={editor.input_policy === "FIXED" ? "Fixed unattended values" : "Oracle Pipeline defaults"} /><Fact label="Variables and files" value={`${Object.keys(editor.variables).length} variable(s), ${Object.keys(editor.inbox_files).length} file(s)`} /></> : <><Fact label="Automation" value="Business Rule RTP registry sync" /><Fact label="Snapshot prefix" value={editor.target_key} /><Fact label="Behavior" value="Export, download, parse, publish, and clean up" /></>}<Fact label="Next run" value={editor.recurrence ? formatDateTime(editor.recurrence.next_run_at) : "—"} /></div><aside className="schedule-review-warning"><Icon name="alert" /><div><strong>Every occurrence is validated again</strong><p>{pipelineTarget ? "If Oracle changes the Pipeline or a required Inbox file becomes unavailable, the run fails instead of submitting incomplete work." : "If Oracle cannot export Calculation Manager or the generated snapshot contains no supported RTP definitions, the existing registry remains unchanged and the occurrence is recorded as failed."}</p></div></aside></div>;
}

function ScheduleCard({ schedule, targetName, onEdit, onToggle, onDelete, onOpenExecution }: { schedule: AutomationSchedule; targetName: string; onEdit: () => void; onToggle: () => void; onDelete: () => void; onOpenExecution?: () => void }) {
  const pipelineTarget = schedule.target_type === "ORACLE_PIPELINE";
  return <article className={`schedule-card${schedule.enabled ? "" : " is-paused"}`}><div className="schedule-card-state"><span className={`schedule-state-dot ${schedule.enabled ? "is-active" : "is-paused"}`} /><strong>{schedule.enabled ? "Active" : "Paused"}</strong></div><div className="schedule-card-main"><div className="schedule-card-title"><div><h3>{schedule.name}</h3><p>{pipelineTarget ? "Oracle Pipeline" : "RTP registry sync"} · {targetName}</p></div><span className="schedule-cadence"><Icon name="clock" />{cadence(schedule)}</span></div><div className="schedule-card-facts"><span><small>Next run</small><strong>{schedule.next_run_at ? formatDateTime(schedule.next_run_at) : "No future occurrence"}</strong></span><span><small>{pipelineTarget ? "Inputs" : "Source"}</small><strong>{pipelineTarget ? schedule.input_policy === "FIXED" ? "Fixed unattended values" : "Oracle defaults" : "Fresh Oracle export"}</strong></span><span><small>Last outcome</small><strong>{friendly(schedule.last_outcome)}</strong></span></div>{schedule.last_error && <p className="schedule-card-error"><Icon name="alert" />{schedule.last_error}</p>}</div><div className="schedule-card-actions">{onOpenExecution && <button className="button button--quiet" onClick={onOpenExecution}>Last job</button>}<details><summary aria-label={`More actions for ${schedule.name}`}>•••</summary><div><button onClick={onEdit}>Edit schedule</button><button onClick={onToggle}>{schedule.enabled ? "Pause schedule" : "Resume schedule"}</button><button className="is-danger" onClick={onDelete}>Archive schedule</button></div></details></div></article>;
}

function ScheduleRunRow({ run, onOpenExecution }: { run: AutomationScheduleRunEvidence; onOpenExecution?: () => void }) {
  const icon = run.status === "FAILED" ? "alert" : ["SUBMITTED", "COMPLETED"].includes(run.status) ? "check" : "clock";
  return <article><span className={`schedule-history-icon is-${run.status.toLowerCase()}`}><Icon name={icon} /></span><div><strong>{run.schedule_name}</strong><small>{run.target_key} · Scheduled {formatDateTime(run.scheduled_for)} · {friendly(run.status)}</small>{run.error_message && <p className="schedule-history-error">{run.error_message}</p>}</div>{onOpenExecution && <button className="button button--quiet" onClick={onOpenExecution}>View execution</button>}</article>;
}

function Heading({ number, title, help }: { number: string; title: string; help: string }) { return <div className="schedule-section-heading"><span>{number}</span><div><h3>{title}</h3><p>{help}</p></div></div>; }
function Fact({ label, value }: { label: string; value: string }) { return <span><small>{label}</small><strong>{value}</strong></span>; }
function ScheduleMetric({ icon, label, value }: { icon: "calendar" | "clock" | "alert"; label: string; value: string | number }) { return <article className="schedule-metric schedule-metric--brand"><span><Icon name={icon} /></span><div><small>{label}</small><strong>{value}</strong></div></article>; }
function payload(editor: Editor): AutomationScheduleInput { const pipelineTarget = editor.target_type === "ORACLE_PIPELINE"; return { name: editor.name.trim(), target_type: editor.target_type, target_key: editor.target_key, frequency: editor.frequency, timezone: editor.timezone, first_run_local: editor.first_run_local, input_policy: pipelineTarget ? editor.input_policy : "FIXED", variables: pipelineTarget && editor.input_policy === "FIXED" ? clean(editor.variables) : {}, inbox_files: pipelineTarget && editor.input_policy === "FIXED" ? clean(editor.inbox_files) : {}, misfire_policy: editor.misfire_policy, enabled: editor.enabled }; }
function normalizeInputs(editor: Editor, preview: PipelineOperationPreview): Editor { if (editor.input_policy !== "FIXED") return { ...editor, variables: {}, inbox_files: {} }; return { ...editor, variables: Object.fromEntries(preview.variables.map((item) => [item.name, editor.variables[item.name] ?? item.default_value ?? ""]).filter(([, value]) => value)), inbox_files: Object.fromEntries(preview.file_requirements.map((item) => [item.key, editor.inbox_files[item.key] ?? item.configured_reference ?? ""]).filter(([, value]) => value)) }; }
function clean(values: Record<string, string>) { return Object.fromEntries(Object.entries(values).map(([key, value]) => [key.trim(), value.trim()]).filter(([key, value]) => key && value)); }
function timezoneOptions(current: string) { return TIMEZONES.includes(current) ? TIMEZONES : [current, ...TIMEZONES]; }
function browserTimezone() { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; }
function futureLocalDateTime() { const date = new Date(Date.now() + 60 * 60 * 1000); date.setMinutes(Math.ceil(date.getMinutes() / 15) * 15, 0, 0); const offset = date.getTimezoneOffset(); return new Date(date.getTime() - offset * 60_000).toISOString().slice(0, 16); }
function cadence(schedule: AutomationSchedule) { const anchor = new Date(schedule.first_run_local); const time = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(anchor); if (schedule.frequency === "WEEKLY") return `Weekly · ${new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(anchor)} · ${time}`; if (schedule.frequency === "MONTHLY") return `Monthly · day ${anchor.getDate()} · ${time}`; if (schedule.frequency === "DAILY") return `Daily · ${time}`; return `One time · ${formatDateTime(schedule.first_run_local)}`; }
function formatDateTime(value: string) { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
function friendly(value: string) { return value.toLowerCase().replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase()); }
function message(reason: unknown) { if (reason instanceof ApiError || reason instanceof Error) return reason.message; return "The request could not be completed."; }
