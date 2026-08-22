import { useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../api/client";
import type {
  JobActivitySummary,
  JobsActivityResponse,
  ProcessSchedule,
  ProcessScheduleInput,
  ScheduleCatalogResponse,
  ScheduleContextMode,
  ScheduleFrequency,
  SchedulePreviewResponse,
  ScheduleProcessOption
} from "../api/types";
import { Icon } from "./Icon";
import { ConfirmationDialog as ConfirmDialog, FeedbackBanner, WorkspaceLoading } from "./Feedback";

interface SchedulingWorkspaceProps {
  csrfToken: string;
  onOpenExecution: (executionId: string) => Promise<void>;
}

interface EditorState extends ProcessScheduleInput {
  scheduleId: number | null;
  phase: "CONFIGURE" | "REVIEW";
  preview: SchedulePreviewResponse | null;
}

type Confirmation = {
  title: string;
  description: string;
  warning: string;
  actionLabel: string;
  tone: "primary" | "danger";
  action: () => Promise<void>;
};

const TIMEZONES = [
  "Asia/Kolkata",
  "Asia/Calcutta",
  "UTC",
  "Asia/Dubai",
  "Asia/Singapore",
  "Europe/London",
  "Europe/Paris",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "Australia/Sydney"
];

const FREQUENCIES: { value: ScheduleFrequency; label: string; help: string }[] = [
  { value: "ONE_TIME", label: "One time", help: "Run once at the selected local date and time." },
  { value: "DAILY", label: "Daily", help: "Run every day at the selected local time." },
  { value: "WEEKLY", label: "Weekly", help: "Run every week on the selected weekday and time." },
  { value: "MONTHLY", label: "Monthly", help: "Run every month on the selected day and time." }
];

export function SchedulingWorkspace({ csrfToken, onOpenExecution }: SchedulingWorkspaceProps) {
  const [catalog, setCatalog] = useState<ScheduleCatalogResponse | null>(null);
  const [schedules, setSchedules] = useState<ProcessSchedule[]>([]);
  const [history, setHistory] = useState<JobsActivityResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"ALL" | "ACTIVE" | "PAUSED" | "ATTENTION">("ALL");
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function load() {
    setError(null);
    const [nextCatalog, nextSchedules, nextHistory] = await Promise.all([
      api.scheduleCatalog(),
      api.schedules(),
      api.jobsActivity().catch(() => null)
    ]);
    setCatalog(nextCatalog);
    setSchedules(nextSchedules.schedules);
    setHistory(nextHistory);
  }

  useEffect(() => {
    load().catch((reason: unknown) => setError(message(reason))).finally(() => setLoading(false));
  }, []);

  const processNames = useMemo(() => new Map(
    (catalog?.processes ?? []).map((process) => [process.code, process.name])
  ), [catalog]);
  const visibleSchedules = schedules.filter((schedule) => {
    const needle = query.trim().toLowerCase();
    const matchesQuery = !needle || [schedule.name, schedule.process_code, processNames.get(schedule.process_code) ?? ""]
      .some((value) => value.toLowerCase().includes(needle));
    const attention = schedule.last_outcome === "FAILED" || schedule.last_outcome === "SKIPPED";
    const matchesStatus = statusFilter === "ALL"
      || (statusFilter === "ACTIVE" && schedule.enabled)
      || (statusFilter === "PAUSED" && !schedule.enabled)
      || (statusFilter === "ATTENTION" && attention);
    return matchesQuery && matchesStatus;
  });
  const activeCount = schedules.filter((schedule) => schedule.enabled).length;
  const attentionCount = schedules.filter((schedule) => !schedule.enabled || ["FAILED", "SKIPPED"].includes(schedule.last_outcome)).length;
  const nextSchedule = schedules
    .filter((schedule) => schedule.enabled && schedule.next_run_at)
    .sort((left, right) => String(left.next_run_at).localeCompare(String(right.next_run_at)))[0];
  const scheduledHistory = (history?.jobs ?? []).filter((job) => job.trigger_source === "SCHEDULED").slice(0, 8);

  function openCreate() {
    const process = catalog?.processes[0];
    if (!process) return;
    const contextMode = defaultContext(process);
    const frequency: ScheduleFrequency = "MONTHLY";
    setEditor({
      scheduleId: null,
      name: automaticName(process.name, frequency),
      process_code: process.code,
      frequency,
      timezone: browserTimezone(),
      first_run_local: futureLocalDateTime(),
      context_mode: contextMode,
      preset_id: contextMode === "RUN_PRESET" ? readyPresets(process)[0]?.preset_id ?? null : null,
      enabled: true,
      phase: "CONFIGURE",
      preview: null
    });
  }

  function openEdit(schedule: ProcessSchedule) {
    setEditor({
      scheduleId: schedule.schedule_id,
      name: schedule.name,
      process_code: schedule.process_code,
      frequency: schedule.frequency,
      timezone: schedule.timezone,
      first_run_local: schedule.first_run_local.slice(0, 16),
      context_mode: schedule.context_mode,
      preset_id: schedule.preset_id,
      enabled: schedule.enabled,
      phase: "CONFIGURE",
      preview: null
    });
  }

  async function reviewSchedule() {
    if (!editor) return;
    setBusy(true);
    setError(null);
    try {
      const preview = await api.previewSchedule(schedulePayload(editor), csrfToken);
      setEditor({ ...editor, phase: "REVIEW", preview });
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function saveSchedule() {
    if (!editor?.preview) return;
    setBusy(true);
    setError(null);
    try {
      const response = editor.scheduleId === null
        ? await api.createSchedule(schedulePayload(editor), csrfToken)
        : await api.updateSchedule(editor.scheduleId, schedulePayload(editor), csrfToken);
      setEditor(null);
      setNotice(response.message);
      await load();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function performConfirmedAction() {
    if (!confirmation) return;
    setBusy(true);
    setError(null);
    try {
      await confirmation.action();
      setConfirmation(null);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  function confirmToggle(schedule: ProcessSchedule) {
    const enabling = !schedule.enabled;
    setConfirmation({
      title: `${enabling ? "Resume" : "Pause"} ${schedule.name}?`,
      description: enabling
        ? "The scheduler will revalidate the Process and calculate its next occurrence."
        : "No new occurrences will be submitted until this schedule is resumed.",
      warning: "Existing execution history and any job already submitted to Oracle are not changed.",
      actionLabel: enabling ? "Resume schedule" : "Pause schedule",
      tone: "primary",
      action: async () => {
        const response = await api.setScheduleEnabled(schedule.schedule_id, enabling, csrfToken);
        setNotice(response.message);
        await load();
      }
    });
  }

  function confirmRun(schedule: ProcessSchedule) {
    setConfirmation({
      title: `Run ${schedule.name} now?`,
      description: `The approved Process ${processNames.get(schedule.process_code) ?? schedule.process_code} will use this schedule's saved unattended context.`,
      warning: "The next planned occurrence will not change. Live preflight and duplicate-run protection still apply.",
      actionLabel: "Run Process now",
      tone: "primary",
      action: async () => {
        const response = await api.runScheduleNow(schedule.schedule_id, csrfToken);
        setNotice(response.message);
        await load();
        if (response.execution_id) await onOpenExecution(response.execution_id);
      }
    });
  }

  function confirmDelete(schedule: ProcessSchedule) {
    setConfirmation({
      title: `Delete ${schedule.name}?`,
      description: "The recurrence will stop and disappear from the active schedule list.",
      warning: "Completed Process executions and audit records are retained.",
      actionLabel: "Delete schedule",
      tone: "danger",
      action: async () => {
        const response = await api.deleteSchedule(schedule.schedule_id, csrfToken);
        setNotice(response.message);
        await load();
      }
    });
  }

  if (loading) return <WorkspaceLoading label="Schedules" message="Preparing approved Processes and durable recurrences…" />;

  return <section className="schedule-workspace">
    <header className="page-intro schedule-react-intro">
      <div><span className="eyebrow">Unattended automation</span><h1>Schedules</h1><p>Run approved Oracle Pipeline processes at the right business cadence without keeping a browser open.</p></div>
      <button className="button button--primary" onClick={openCreate} disabled={!catalog?.processes.length}><Icon name="calendar" /> Create schedule</button>
    </header>

    {notice && <FeedbackBanner tone="success" message={notice} onDismiss={() => setNotice(null)} />}
    {error && <FeedbackBanner tone="error" message={error} onDismiss={() => setError(null)} />}

    <div className="schedule-metrics" aria-label="Schedule summary">
      <ScheduleMetric icon="calendar" label="Active schedules" value={activeCount} tone="brand" />
      <ScheduleMetric icon="clock" label="Next execution" value={nextSchedule?.next_run_at ? formatDateTime(nextSchedule.next_run_at) : "Not scheduled"} detail={nextSchedule?.name} tone="success" />
      <ScheduleMetric icon="alert" label="Attention required" value={attentionCount} detail="Paused or unsuccessful" tone={attentionCount ? "warning" : "neutral"} />
    </div>

    <aside className="schedule-policy">
      <span><Icon name="automation" /></span>
      <div><strong>Oracle Pipeline remains the technical owner</strong><p>Each occurrence revalidates the current Process, Pipeline inputs, and Inbox file strategy before it enters the durable execution queue. Local uploads are intentionally excluded from unattended execution.</p></div>
      <span className="live-badge"><i /> Durable queue</span>
    </aside>

    <section className="panel schedule-list-panel">
      <div className="panel-heading schedule-list-heading"><div><span className="eyebrow">Automation calendar</span><h2>Configured schedules</h2><p>Pause, test, or adjust a recurrence without changing its approved Process.</p></div><span className="result-count">{visibleSchedules.length} shown</span></div>
      <div className="schedule-filters">
        <label className="search-field"><Icon name="search" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search schedule or Process" /></label>
        <label><span>Status</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as typeof statusFilter)}><option value="ALL">All schedules</option><option value="ACTIVE">Active</option><option value="PAUSED">Paused</option><option value="ATTENTION">Needs attention</option></select></label>
      </div>
      {visibleSchedules.length ? <div className="schedule-card-list">{visibleSchedules.map((schedule) => <ScheduleCard
        key={schedule.schedule_id}
        schedule={schedule}
        processName={processNames.get(schedule.process_code) ?? schedule.process_code}
        onEdit={() => openEdit(schedule)}
        onToggle={() => confirmToggle(schedule)}
        onRun={() => confirmRun(schedule)}
        onDelete={() => confirmDelete(schedule)}
        onOpenExecution={schedule.last_execution_id ? () => onOpenExecution(schedule.last_execution_id!) : undefined}
      />)}</div> : <div className="work-empty"><span><Icon name="calendar" /></span><h2>{schedules.length ? "No schedules match these filters" : "No schedules configured"}</h2><p>{schedules.length ? "Clear the search or choose another status." : "Create the first recurrence for an approved Planning Process."}</p>{!schedules.length && catalog?.processes.length ? <button className="button button--primary" onClick={openCreate}>Create schedule</button> : null}</div>}
    </section>

    <section className="panel schedule-history-panel">
      <div className="panel-heading schedule-list-heading"><div><span className="eyebrow">Execution history</span><h2>Recent scheduled runs</h2><p>These occurrences were submitted automatically by the scheduler. Open Jobs &amp; Activity for complete step evidence.</p></div>{scheduledHistory.length ? <button className="button button--quiet" onClick={() => { window.location.hash = "#jobs"; }}>View all jobs</button> : null}</div>
      {scheduledHistory.length ? <div className="schedule-history-list">{scheduledHistory.map((job) => <ScheduleHistoryRow key={job.execution_id} job={job} onOpen={() => onOpenExecution(job.execution_id)} />)}</div> : <div className="schedule-history-empty"><Icon name="clock" /><div><strong>No automatic occurrence has run yet</strong><p>History will appear here after the scheduler submits its first Process.</p></div></div>}
    </section>

    {editor && catalog && <ScheduleEditor
      editor={editor}
      processes={catalog.processes}
      busy={busy}
      error={error}
      onChange={setEditor}
      onReview={reviewSchedule}
      onSave={saveSchedule}
      onClose={() => { setEditor(null); setError(null); }}
    />}
    {confirmation && <ConfirmDialog title={confirmation.title} description={confirmation.description} warning={confirmation.warning} confirmLabel={confirmation.actionLabel} tone={confirmation.tone} busy={busy} error={error} onConfirm={performConfirmedAction} onClose={() => { setConfirmation(null); setError(null); }} />}
  </section>;
}

function ScheduleMetric({ icon, label, value, detail, tone }: { icon: "calendar" | "clock" | "alert"; label: string; value: number | string; detail?: string; tone: string }) {
  return <article className={`schedule-metric schedule-metric--${tone}`}><span><Icon name={icon} /></span><div><small>{label}</small><strong>{value}</strong>{detail && <p>{detail}</p>}</div></article>;
}

function ScheduleCard({ schedule, processName, onEdit, onToggle, onRun, onDelete, onOpenExecution }: { schedule: ProcessSchedule; processName: string; onEdit: () => void; onToggle: () => void; onRun: () => void; onDelete: () => void; onOpenExecution?: () => void }) {
  const attention = ["FAILED", "SKIPPED"].includes(schedule.last_outcome);
  return <article className={`schedule-card${!schedule.enabled ? " is-paused" : ""}${attention ? " needs-attention" : ""}`}>
    <div className="schedule-card-state"><span className={`schedule-state-dot ${schedule.enabled ? "is-active" : "is-paused"}`} /><strong>{schedule.enabled ? "Active" : "Paused"}</strong></div>
    <div className="schedule-card-main"><div className="schedule-card-title"><div><h3>{schedule.name}</h3><p>{processName}</p></div><span className="schedule-cadence"><Icon name="clock" />{cadenceLabel(schedule)}</span></div>
      <div className="schedule-card-facts"><span><small>Next run</small><strong>{schedule.next_run_at ? formatDateTime(schedule.next_run_at) : "No future occurrence"}</strong></span><span><small>Run context</small><strong>{schedule.context_label}</strong></span><span><small>Last result</small><strong className={`schedule-outcome schedule-outcome--${schedule.last_outcome.toLowerCase()}`}>{friendly(schedule.last_outcome)}</strong></span></div>
      {schedule.last_error && <p className="schedule-card-error"><Icon name="alert" />{schedule.last_error}</p>}
    </div>
    <div className="schedule-card-actions"><button className="button button--primary" onClick={onRun}>Run now</button>{onOpenExecution && <button className="button button--quiet" onClick={onOpenExecution}>Last job</button>}<details><summary aria-label={`More actions for ${schedule.name}`}>•••</summary><div><button onClick={onEdit}>Edit schedule</button><button onClick={onToggle}>{schedule.enabled ? "Pause schedule" : "Resume schedule"}</button><button className="is-danger" onClick={onDelete}>Delete schedule</button></div></details></div>
  </article>;
}

function ScheduleHistoryRow({ job, onOpen }: { job: JobActivitySummary; onOpen: () => void }) {
  return <article><span className={`schedule-history-icon is-${job.status.toLowerCase()}`}><Icon name={job.status === "FAILED" ? "alert" : job.status === "SUCCESS" ? "check" : "clock"} /></span><div><strong>{job.name}</strong><small>{formatDateTime(job.started_at)} · {friendly(job.status)}</small></div><button className="button button--quiet" onClick={onOpen}>View details</button></article>;
}

function ScheduleEditor({ editor, processes, busy, error, onChange, onReview, onSave, onClose }: { editor: EditorState; processes: ScheduleProcessOption[]; busy: boolean; error: string | null; onChange: (value: EditorState) => void; onReview: () => Promise<void>; onSave: () => Promise<void>; onClose: () => void }) {
  const process = processes.find((item) => item.code === editor.process_code) ?? processes[0];
  const presets = readyPresets(process);
  const selectedPreset = presets.find((preset) => preset.preset_id === editor.preset_id);
  const timezoneOptions = TIMEZONES.includes(editor.timezone) ? TIMEZONES : [editor.timezone, ...TIMEZONES];

  function changeProcess(code: string) {
    const nextProcess = processes.find((item) => item.code === code)!;
    const mode = defaultContext(nextProcess);
    onChange({ ...editor, process_code: code, name: automaticName(nextProcess.name, editor.frequency), context_mode: mode, preset_id: mode === "RUN_PRESET" ? readyPresets(nextProcess)[0]?.preset_id ?? null : null, phase: "CONFIGURE", preview: null });
  }

  function changeFrequency(frequency: ScheduleFrequency) {
    const currentAutomatic = automaticName(process.name, editor.frequency);
    onChange({ ...editor, frequency, name: editor.name === currentAutomatic ? automaticName(process.name, frequency) : editor.name, phase: "CONFIGURE", preview: null });
  }

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  return <div className="modal-backdrop schedule-modal" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}><section className="schedule-editor" role="dialog" aria-modal="true" aria-labelledby="schedule-editor-title">
    <header><div><span className="eyebrow">{editor.scheduleId === null ? "New automation" : "Edit automation"}</span><h2 id="schedule-editor-title">{editor.phase === "CONFIGURE" ? "Configure the schedule" : "Review before saving"}</h2><p>{editor.phase === "CONFIGURE" ? "Choose the approved Process, reusable context, and business cadence." : "Live validation passed. Confirm what will happen before activating the recurrence."}</p></div><button aria-label="Close schedule editor" disabled={busy} onClick={onClose}><Icon name="close" /></button></header>
    <div className="schedule-editor-progress"><span className={editor.phase === "CONFIGURE" ? "is-current" : "is-complete"}><i>{editor.phase === "REVIEW" ? <Icon name="check" /> : "1"}</i><strong>Configure</strong></span><span className={editor.phase === "REVIEW" ? "is-current" : ""}><i>2</i><strong>Review</strong></span></div>
    {editor.phase === "CONFIGURE" ? <div className="schedule-editor-body">
      <section><div className="schedule-section-heading"><span>1</span><div><h3>What should run?</h3><p>Only approved Planning Processes can be scheduled.</p></div></div><div className="schedule-form-grid"><label className="field"><span>Approved Process *</span><select aria-label="Approved Process" value={editor.process_code} onChange={(event) => changeProcess(event.target.value)}>{processes.map((item) => <option value={item.code} key={item.code}>{item.name}</option>)}</select><small>The Oracle Pipeline inside this Process remains the technical lifecycle.</small></label><label className="field"><span>Schedule name *</span><input aria-label="Schedule name" value={editor.name} maxLength={120} onChange={(event) => onChange({ ...editor, name: event.target.value, preview: null })} /><small>A suggested name is provided; change it only if useful.</small></label></div></section>
      <section><div className="schedule-section-heading"><span>2</span><div><h3>Which saved context should it use?</h3><p>Unattended runs cannot pause for keyboard input or a local file.</p></div></div><div className="schedule-form-grid"><label className="field"><span>Context strategy *</span><select aria-label="Context strategy" value={editor.context_mode} onChange={(event) => { const mode = event.target.value as ScheduleContextMode; onChange({ ...editor, context_mode: mode, preset_id: mode === "RUN_PRESET" ? presets[0]?.preset_id ?? null : null, preview: null }); }}><option value="PIPELINE_DEFAULTS" disabled={!process.supports_pipeline_defaults}>Use Oracle Pipeline defaults</option><option value="RUN_PRESET" disabled={!presets.length}>Use a saved run preset</option></select><small>{editor.context_mode === "PIPELINE_DEFAULTS" ? "Oracle supplies the runtime values configured on the Pipeline." : "The selected preset supplies reusable year, period, variables, and Inbox references."}</small></label>{editor.context_mode === "RUN_PRESET" && <label className="field"><span>Run preset *</span><select aria-label="Run preset" value={editor.preset_id ?? ""} onChange={(event) => onChange({ ...editor, preset_id: Number(event.target.value), preview: null })}>{presets.map((preset) => <option value={preset.preset_id} key={preset.preset_id}>{preset.name}</option>)}</select><small>{selectedPreset ? presetSummary(selectedPreset) : "Select an unattended-ready preset."}</small></label>}</div><FileStrategy process={process} mode={editor.context_mode} presetId={editor.preset_id} /></section>
      <section><div className="schedule-section-heading"><span>3</span><div><h3>When should it run?</h3><p>The first occurrence anchors the local time, weekday, or day of month.</p></div></div><div className="schedule-form-grid schedule-form-grid--three"><label className="field"><span>Frequency *</span><select aria-label="Frequency" value={editor.frequency} onChange={(event) => changeFrequency(event.target.value as ScheduleFrequency)}>{FREQUENCIES.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select><small>{FREQUENCIES.find((item) => item.value === editor.frequency)?.help}</small></label><label className="field"><span>First run *</span><input aria-label="First run" type="datetime-local" step="60" value={editor.first_run_local} onChange={(event) => onChange({ ...editor, first_run_local: event.target.value, preview: null })} /><small>Choose the date and time in the selected timezone.</small></label><label className="field"><span>Timezone *</span><select aria-label="Timezone" value={editor.timezone} onChange={(event) => onChange({ ...editor, timezone: event.target.value, preview: null })}>{timezoneOptions.map((timezone) => <option key={timezone}>{timezone}</option>)}</select><small>Daylight-saving changes are handled automatically.</small></label></div></section>
      <label className="schedule-enable-choice"><input type="checkbox" checked={editor.enabled} onChange={(event) => onChange({ ...editor, enabled: event.target.checked, preview: null })} /><span /><div><strong>Enable after saving</strong><small>Turn this off to save the recurrence in a paused state.</small></div></label>
      {error && <div className="schedule-form-error" role="alert"><Icon name="alert" />{error}</div>}
    </div> : <ScheduleReview editor={editor} process={process} preset={selectedPreset} />}
    <footer>{editor.phase === "CONFIGURE" ? <><button className="button button--quiet" disabled={busy} onClick={onClose}>Cancel</button><button className="button button--primary" disabled={busy || !validEditor(editor, process)} onClick={onReview}>{busy ? <><span className="spinner" /> Validating Process</> : <>Review schedule <Icon name="arrow" /></>}</button></> : <><button className="button button--quiet" disabled={busy} onClick={() => onChange({ ...editor, phase: "CONFIGURE", preview: null })}>Back to edit</button><button className="button button--primary" disabled={busy || !editor.preview} onClick={onSave}>{busy ? <><span className="spinner" /> Saving</> : editor.scheduleId === null ? "Create schedule" : "Save changes"}</button></>}</footer>
  </section></div>;
}

function FileStrategy({ process, mode, presetId }: { process: ScheduleProcessOption; mode: ScheduleContextMode; presetId: number | null }) {
  const preset = process.presets.find((item) => item.preset_id === presetId);
  if (mode === "PIPELINE_DEFAULTS") return <aside className="schedule-file-strategy"><Icon name="data" /><div><strong>Configured-file strategy</strong><p>No local file will be uploaded at run time. Oracle Pipeline stages must use their configured file or an existing Oracle Inbox file.</p></div></aside>;
  return <aside className="schedule-file-strategy"><Icon name="data" /><div><strong>{preset?.inbox_files.length ? "Saved Inbox-file strategy" : "No local upload required"}</strong><p>{preset?.inbox_files.length ? `${preset.inbox_files.length} Oracle Inbox reference${preset.inbox_files.length === 1 ? " is" : "s are"} already saved in this preset.` : "This preset passed the unattended-ready check and does not request a local file."}</p></div></aside>;
}

function ScheduleReview({ editor, process, preset }: { editor: EditorState; process: ScheduleProcessOption; preset: ReturnType<typeof readyPresets>[number] | undefined }) {
  return <div className="schedule-editor-body schedule-review"><div className="schedule-review-success"><Icon name="check" /><div><strong>Live validation passed</strong><p>{editor.preview?.message}</p></div></div><div className="schedule-review-grid"><ReviewFact label="Schedule" value={editor.name} /><ReviewFact label="Approved Process" value={process.name} /><ReviewFact label="Cadence" value={FREQUENCIES.find((item) => item.value === editor.frequency)?.label ?? editor.frequency} /><ReviewFact label="Next run" value={editor.preview ? formatDateTime(editor.preview.next_run_at) : "—"} /><ReviewFact label="Timezone" value={editor.timezone} /><ReviewFact label="Run context" value={editor.context_mode === "PIPELINE_DEFAULTS" ? "Oracle Pipeline defaults" : preset?.name ?? "Saved run preset"} /></div><FileStrategy process={process} mode={editor.context_mode} presetId={editor.preset_id} /><aside className="schedule-review-warning"><Icon name="alert" /><div><strong>Every occurrence validates again</strong><p>If the Process, Oracle Pipeline, or required Inbox file later becomes unavailable, the occurrence is recorded as failed instead of running with incomplete inputs.</p></div></aside></div>;
}

function ReviewFact({ label, value }: { label: string; value: string }) { return <span><small>{label}</small><strong>{value}</strong></span>; }

function schedulePayload(editor: EditorState): ProcessScheduleInput {
  return { name: editor.name.trim(), process_code: editor.process_code, frequency: editor.frequency, timezone: editor.timezone, first_run_local: editor.first_run_local, context_mode: editor.context_mode, preset_id: editor.context_mode === "RUN_PRESET" ? editor.preset_id : null, enabled: editor.enabled };
}

function validEditor(editor: EditorState, process: ScheduleProcessOption) { return Boolean(editor.name.trim() && editor.process_code && editor.timezone && editor.first_run_local && (editor.context_mode === "PIPELINE_DEFAULTS" ? process.supports_pipeline_defaults : editor.preset_id)); }
function readyPresets(process: ScheduleProcessOption) { return process.presets.filter((preset) => preset.one_click_ready); }
function defaultContext(process: ScheduleProcessOption): ScheduleContextMode { return process.supports_pipeline_defaults ? "PIPELINE_DEFAULTS" : "RUN_PRESET"; }
function automaticName(processName: string, frequency: ScheduleFrequency) { return `${processName} · ${FREQUENCIES.find((item) => item.value === frequency)?.label ?? "Schedule"}`; }
function browserTimezone() { const detected = Intl.DateTimeFormat().resolvedOptions().timeZone; return TIMEZONES.includes(detected) ? detected : "Asia/Kolkata"; }
function futureLocalDateTime() { const date = new Date(Date.now() + 30 * 60 * 1000); date.setMinutes(Math.ceil(date.getMinutes() / 15) * 15, 0, 0); const offset = date.getTimezoneOffset(); return new Date(date.getTime() - offset * 60_000).toISOString().slice(0, 16); }
function presetSummary(preset: ReturnType<typeof readyPresets>[number]) { const period = [preset.start_period, preset.end_period].filter(Boolean).join(" to "); return [preset.year, period, preset.inbox_files.length ? `${preset.inbox_files.length} Inbox file${preset.inbox_files.length === 1 ? "" : "s"}` : "No file upload"].filter(Boolean).join(" · "); }
function cadenceLabel(schedule: ProcessSchedule) { const date = new Date(schedule.first_run_local); const time = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(date); if (schedule.frequency === "WEEKLY") return `Weekly · ${new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(date)} · ${time}`; if (schedule.frequency === "MONTHLY") return `Monthly · day ${date.getDate()} · ${time}`; if (schedule.frequency === "DAILY") return `Daily · ${time}`; return `One time · ${formatDateTime(schedule.first_run_local)}`; }
function formatDateTime(value: string) { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
function friendly(value: string) { return value.toLowerCase().replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase()); }
function message(reason: unknown) { if (reason instanceof ApiError || reason instanceof Error) return reason.message; return "The request could not be completed."; }
