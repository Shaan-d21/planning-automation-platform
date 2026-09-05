import { useEffect, useMemo, useState } from "react";

import type {
  JobActivityDetail,
  JobActivitySummary,
  JobsActivityResponse,
  JobStatus,
  StandaloneFlowRecoveryPlan
} from "../api/types";
import { api } from "../api/client";
import { Icon } from "./Icon";
import { OracleLoadStatistics } from "./OracleLoadStatistics";
import { StandaloneFlowRecoveryDialog } from "./EpmAssistantWorkspace";
import { FeedbackBanner } from "./Feedback";

interface JobsActivityWorkspaceProps {
  data: JobsActivityResponse;
  detail: JobActivityDetail | null;
  busyExecutionId: string | null;
  onInspect: (executionId: string) => Promise<void>;
  onCloseDetail: () => void;
  csrfToken: string;
  allowRecovery: boolean;
  onRecoveryStarted: (executionId: string) => Promise<void>;
}

export function JobsActivityWorkspace({
  data,
  detail,
  busyExecutionId,
  onInspect,
  onCloseDetail,
  csrfToken,
  allowRecovery,
  onRecoveryStarted
}: JobsActivityWorkspaceProps) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<JobStatus | "ALL">("ALL");
  const [source, setSource] = useState("ALL");
  const [page, setPage] = useState(1);
  const sources = [...new Set(data.jobs.map((job) => job.trigger_source))].sort();
  const jobs = useMemo(() => data.jobs.filter((job) => {
    const needle = query.trim().toLowerCase();
    return (!needle || [job.name, job.initiated_by, job.executed_by || "", job.execution_id]
      .some((value) => value.toLowerCase().includes(needle)))
      && (status === "ALL" || job.status === status)
      && (source === "ALL" || job.trigger_source === source);
  }), [data.jobs, query, source, status]);
  const pageSize = 15;
  const pageCount = Math.max(1, Math.ceil(jobs.length / pageSize));
  const currentPage = Math.min(page, pageCount);
  const visibleJobs = jobs.slice((currentPage - 1) * pageSize, currentPage * pageSize);

  useEffect(() => {
    if (!detail) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCloseDetail();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [detail, onCloseDetail]);

  return <section className="jobs-workspace">
    <header className="page-intro jobs-intro">
      <div><span className="eyebrow">Operations</span><h1>Jobs &amp; Activity</h1><p>Monitor every retained automation run, investigate failures, and review step-level execution evidence.</p></div>
      <span className="retention-badge"><Icon name="check" /> Durable audit history</span>
    </header>

    <div className="jobs-summary" aria-label="Job activity summary">
      <JobMetric label="Recent jobs" value={data.summary.total} tone="brand" icon="activity" />
      <JobMetric label="Active or queued" value={data.summary.running} tone="warning" icon="clock" />
      <JobMetric label="Failed" value={data.summary.failed} tone="danger" icon="alert" />
      <JobMetric label="Success rate" value={`${data.summary.success_rate}%`} tone="success" icon="check" />
    </div>

    {data.summary.failed > 0 && <aside className="jobs-attention"><span><Icon name="alert" /></span><div><strong>{data.summary.failed} failed job{data.summary.failed === 1 ? " needs" : "s need"} review</strong><p>Open the execution evidence below to identify the failed step and Oracle response.</p></div></aside>}

    <section className="panel jobs-panel">
      <div className="panel-heading"><div><span className="eyebrow">Execution register</span><h2>What happened recently</h2><p>Newest runs appear first. Use filters to focus on failures or a specific trigger source.</p></div><span className="result-count">{jobs.length} shown</span></div>
      <div className="jobs-filters">
        <label className="search-field"><Icon name="search" /><input value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder="Search job, person, or execution ID" /></label>
        <label><span>Status</span><select value={status} onChange={(event) => { setStatus(event.target.value as JobStatus | "ALL"); setPage(1); }}><option value="ALL">All statuses</option><option value="QUEUED">Queued</option><option value="FAILED">Failed</option><option value="RUNNING">Running</option><option value="SUCCESS">Successful</option></select></label>
        <label><span>Started from</span><select value={source} onChange={(event) => { setSource(event.target.value); setPage(1); }}><option value="ALL">All sources</option>{sources.map((item) => <option key={item} value={item}>{friendlySource(item)}</option>)}</select></label>
      </div>

      {jobs.length ? <><div className="jobs-table-wrap"><table className="jobs-table"><thead><tr><th>Job</th><th>Status</th><th>Started</th><th>Duration</th><th>Initiated by</th><th>Progress</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{visibleJobs.map((job) => <JobRow job={job} busy={busyExecutionId === job.execution_id} onInspect={onInspect} key={job.execution_id} />)}</tbody></table></div>{pageCount > 1 && <nav className="table-pagination" aria-label="Job history pages"><button className="button button--quiet" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)}>Previous</button><span>Page {currentPage} of {pageCount}</span><button className="button button--quiet" disabled={currentPage === pageCount} onClick={() => setPage(currentPage + 1)}>Next</button></nav>}</> : <div className="work-empty"><span><Icon name="search" /></span><h2>No jobs match these filters</h2><p>Clear the search or select a different status or trigger source.</p></div>}
    </section>

    {detail && <JobDetailDialog job={detail} csrfToken={csrfToken} allowRecovery={allowRecovery} onRecoveryStarted={onRecoveryStarted} onClose={onCloseDetail} />}
  </section>;
}

function JobMetric({ label, value, tone, icon }: { label: string; value: number | string; tone: string; icon: "activity" | "clock" | "alert" | "check" }) {
  return <article className={`job-metric job-metric--${tone}`}><span><Icon name={icon} /></span><div><strong>{value}</strong><small>{label}</small></div></article>;
}

function JobRow({ job, busy, onInspect }: { job: JobActivitySummary; busy: boolean; onInspect: (executionId: string) => Promise<void> }) {
  return <tr className={job.status === "FAILED" ? "is-failed" : ""}>
    <td><div className="job-name"><span className={`job-icon job-icon--${job.status.toLowerCase()}`}><Icon name={job.status === "FAILED" ? "alert" : job.status === "RUNNING" || job.status === "QUEUED" ? "clock" : "check"} /></span><div><strong>{job.name}</strong><small>{shortId(job.execution_id)}</small></div></div></td>
    <td><JobStatusBadge status={job.status} /></td>
    <td>{formatDateTime(job.started_at)}</td>
    <td>{formatDuration(job.duration_seconds, job.status)}</td>
    <td><div className="job-actor"><strong>{job.initiated_by}</strong><small>{friendlySource(job.trigger_source)}</small>{job.executed_by && <small>Oracle: {job.executed_by}</small>}</div></td>
    <td><span className="job-progress">{job.completed_steps} / {job.total_steps || "–"}</span></td>
    <td><button className="button button--quiet" disabled={busy} onClick={() => onInspect(job.execution_id)}>{busy ? <span className="spinner" /> : null} View details</button></td>
  </tr>;
}

function JobDetailDialog({ job, csrfToken, allowRecovery, onRecoveryStarted, onClose }: { job: JobActivityDetail; csrfToken: string; allowRecovery: boolean; onRecoveryStarted: (executionId: string) => Promise<void>; onClose: () => void }) {
  const [recoveryPlan, setRecoveryPlan] = useState<StandaloneFlowRecoveryPlan | null>(null);
  const [reviewingRecovery, setReviewingRecovery] = useState(false);
  const [recoveryError, setRecoveryError] = useState<string | null>(null);
  const [tab, setTab] = useState<"overview" | "results" | "rejections" | "messages" | "files">("overview");
  const canRecover = allowRecovery && job.status === "FAILED" && job.name.startsWith("Standalone Flow - ");
  const messages = job.oracle_messages || [];
  const rejectedRecords = job.rejected_records || [];
  const artifacts = job.artifacts || [];

  async function reviewRecovery() {
    setReviewingRecovery(true);
    setRecoveryError(null);
    try {
      const response = await api.standaloneFlowRecovery(job.execution_id);
      setRecoveryPlan(response.recovery);
    } catch (reason) {
      setRecoveryError(reason instanceof Error ? reason.message : "Recovery review is unavailable.");
    } finally {
      setReviewingRecovery(false);
    }
  }

  const tabs = [
    { id: "overview", label: "Overview", count: undefined },
    { id: "results", label: "Load results", count: job.record_statistics?.details.length },
    { id: "rejections", label: "Rejected records", count: job.record_statistics?.records_rejected || rejectedRecords.reduce((total, item) => total + item.preview_count, 0) },
    { id: "messages", label: "Oracle messages", count: messages.length },
    { id: "files", label: "Files", count: artifacts.length }
  ] as const;

  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="job-dialog" role="dialog" aria-modal="true" aria-labelledby="job-dialog-title">
    <header><div><span className="eyebrow">Execution evidence</span><h2 id="job-dialog-title">{job.name}</h2><div className="job-dialog-meta"><JobStatusBadge status={job.status} /><span>{formatDateTime(job.started_at)}</span><span>{formatDuration(job.duration_seconds, job.status)}</span><span>{job.initiated_by} · {friendlySource(job.trigger_source)}</span>{job.executed_by && <span>Oracle execution: {job.executed_by}</span>}</div></div><button aria-label="Close job details" onClick={onClose}><Icon name="close" /></button></header>
    <nav className="job-detail-tabs" aria-label="Job detail sections">{tabs.map((item) => <button key={item.id} className={tab === item.id ? "is-active" : ""} onClick={() => setTab(item.id)}><span>{item.label}</span>{typeof item.count === "number" && item.count > 0 && <strong>{item.count.toLocaleString()}</strong>}</button>)}</nav>
    <div className="job-dialog-body">
      {job.error_message && <aside className="job-error"><Icon name="alert" /><div><strong>Execution failed</strong><p>{job.error_message}</p></div></aside>}
      {recoveryError && <FeedbackBanner tone="error" title="Recovery review is unavailable" message={recoveryError} />}
      {tab === "overview" && <>
        <div className="job-identity"><span><small>Execution ID</small><strong>{job.execution_id}</strong></span><span><small>Steps completed</small><strong>{job.completed_steps} of {job.total_steps}</strong></span></div>
        {job.record_statistics && <div className="job-overview-results" aria-label="Oracle load summary"><span><small>Records read</small><strong>{job.record_statistics.records_read.toLocaleString()}</strong></span><span><small>Records processed</small><strong>{job.record_statistics.records_processed.toLocaleString()}</strong></span><span className={job.record_statistics.records_rejected ? "is-warning" : ""}><small>Records rejected</small><strong>{job.record_statistics.records_rejected.toLocaleString()}</strong></span><button onClick={() => setTab("results")}>View breakdown <Icon name="arrow" /></button></div>}
        {job.lineage && <LoadLineage lineage={job.lineage} />}
        {(job.notices || []).map((notice) => <aside className="job-evidence-notice" key={notice}><Icon name="alert" /><span>{notice}</span></aside>)}
        <section className="job-step-section"><div className="panel-heading"><div><span className="eyebrow">Timeline</span><h3>Execution steps</h3></div></div>
        {job.steps.length ? <ol className="job-step-list">{job.steps.map((step) => <li className={`job-step job-step--${step.status.toLowerCase()}`} key={step.sequence}><span className="job-step-number">{step.status === "SUCCESS" ? <Icon name="check" /> : step.status === "FAILED" ? <Icon name="alert" /> : step.sequence}</span><div className="job-step-content"><header><div><strong>{step.name}</strong><small>Step {step.sequence} · {formatDuration(step.duration_seconds, step.status === "RUNNING" ? "RUNNING" : "SUCCESS")}</small></div><span>{friendlyStatus(step.status)}</span></header>{step.error_message && <p className="step-error">{step.error_message}</p>}<StepDetails details={step.details} /></div></li>)}</ol> : <div className="work-empty work-empty--compact"><span><Icon name="activity" /></span><h3>No step evidence was recorded</h3><p>This execution contains only an overall status.</p></div>}
        </section>
      </>}
      {tab === "results" && (job.record_statistics ? <OracleLoadStatistics statistics={job.record_statistics} /> : <EvidenceEmpty title="No Oracle load counters" message="This operation or Planning version did not expose records read, processed, and rejected through Job Details." />)}
      {tab === "rejections" && <RejectedRecords sets={rejectedRecords} rejectedCount={job.record_statistics?.records_rejected || 0} />}
      {tab === "messages" && <OracleMessages messages={messages} />}
      {tab === "files" && <JobFiles artifacts={artifacts} />}
    </div>
    <footer>{canRecover && <button className="button button--primary" disabled={reviewingRecovery} onClick={() => void reviewRecovery()}>{reviewingRecovery ? <><span className="spinner" /> Reviewing recovery…</> : "Review recovery"}</button>}<button className="button button--secondary" onClick={onClose}>Close</button></footer>
    {recoveryPlan && <StandaloneFlowRecoveryDialog plan={recoveryPlan} csrfToken={csrfToken} onClose={() => setRecoveryPlan(null)} onStarted={(accepted) => { setRecoveryPlan(null); void onRecoveryStarted(accepted.execution_id); }} />}
  </section></div>;
}

function LoadLineage({ lineage }: { lineage: NonNullable<JobActivityDetail["lineage"]> }) {
  const source = lineage.source_kind === "local_upload" ? "Uploaded source file" : lineage.source_kind === "existing_inbox" ? "Existing Oracle Inbox file" : "File configured in saved Oracle job";
  return <section className="load-lineage" aria-label="Data movement path"><header><span className="eyebrow">Data movement</span><h3>Source to Planning</h3></header><div className="lineage-flow"><LineageNode label="Source" value={lineage.source_file || source} hint={source} /><Icon name="arrow" /><LineageNode label="Staging" value={lineage.staging_location || "Oracle repository"} hint={lineage.oracle_job_name || "Saved import job"} /><Icon name="arrow" /><LineageNode label="Destination" value={lineage.target_application || "Planning application"} hint={lineage.target_system || "Oracle Planning"} /></div>{lineage.origin_note && <p>{lineage.origin_note}</p>}</section>;
}

function LineageNode({ label, value, hint }: { label: string; value: string; hint: string }) {
  return <div><small>{label}</small><strong>{value}</strong><span>{hint}</span></div>;
}

function RejectedRecords({ sets, rejectedCount }: { sets: JobActivityDetail["rejected_records"]; rejectedCount: number }) {
  if (!sets.length) return <EvidenceEmpty title={rejectedCount ? `${rejectedCount.toLocaleString()} rejected record${rejectedCount === 1 ? "" : "s"}` : "No rejected records"} message={rejectedCount ? "Oracle reported rejections, but did not make a readable error artifact available for this run. Check Files and Oracle messages for retained evidence." : "Oracle did not report any rejected rows for this execution."} />;
  return <section className="rejection-section"><div className="evidence-section-heading"><div><span className="eyebrow">Oracle rejection files</span><h3>Rejected record preview</h3><p>These rows come from Oracle's generated error artifact, not from an estimate.</p></div></div>{sets.map((set, index) => <article className="rejection-set" key={`${set.file_name}-${index}`}><header><div><strong>{set.dimension_name || set.file_name}</strong><small>{set.file_name} · {set.preview_count.toLocaleString()} row{set.preview_count === 1 ? "" : "s"} shown</small></div>{set.truncated && <span>Preview limited</span>}</header><div><table><thead><tr>{set.columns.map((column, columnIndex) => <th key={`${column}-${columnIndex}`}>{column}</th>)}</tr></thead><tbody>{set.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((value, columnIndex) => <td key={columnIndex}>{value}</td>)}</tr>)}</tbody></table></div></article>)}</section>;
}

function OracleMessages({ messages }: { messages: JobActivityDetail["oracle_messages"] }) {
  if (!messages.length) return <EvidenceEmpty title="No detailed Oracle messages" message="Oracle did not expose INFO, WARN, or ERROR child-job messages for this execution." />;
  return <section className="oracle-message-list"><div className="evidence-section-heading"><div><span className="eyebrow">Job Console evidence</span><h3>Oracle messages</h3><p>Messages returned by the job and metadata child-job detail APIs.</p></div></div>{messages.map((message, index) => <article className={`oracle-message oracle-message--${message.message_type.toLowerCase()}`} key={`${message.child_job_id || "job"}-${index}`}><span>{message.message_type}</span><div><strong>{message.dimension_name || message.category || "Oracle Planning"}</strong><p>{message.message}</p>{message.category && message.dimension_name && <small>{message.category}</small>}</div></article>)}</section>;
}

function JobFiles({ artifacts }: { artifacts: JobActivityDetail["artifacts"] }) {
  if (!artifacts.length) return <EvidenceEmpty title="No retained files" message="This job did not generate a downloadable rejection or diagnostic artifact." />;
  return <section className="job-file-list"><div className="evidence-section-heading"><div><span className="eyebrow">Execution artifacts</span><h3>Files from Oracle</h3><p>Error archives and extracted rejection files are retained with this execution.</p></div></div>{artifacts.map((artifact) => <a href={artifact.download_url} className="job-file" key={artifact.artifact_id}><span><Icon name="reports" /></span><div><strong>{artifact.name}</strong><small>{friendlyStatus(artifact.kind)} · {formatBytes(artifact.size_bytes)}</small></div><em>Download</em><Icon name="arrow" /></a>)}</section>;
}

function EvidenceEmpty({ title, message }: { title: string; message: string }) {
  return <div className="job-evidence-empty"><span><Icon name="data" /></span><h3>{title}</h3><p>{message}</p></div>;
}

function StepDetails({ details }: { details: Record<string, unknown> }) {
  const structuredKeys = new Set(["record_statistics", "load_lineage", "oracle_messages", "rejected_records", "artifacts", "artifact_message", "evidence_message"]);
  const entries = Object.entries(details).filter(([key, value]) => !structuredKeys.has(key) && value !== null && value !== "");
  if (!entries.length) return null;
  return <details className="step-details"><summary>Technical evidence</summary><dl>{entries.map(([key, value]) => <div key={key}><dt>{friendlyKey(key)}</dt><dd>{displayValue(value)}</dd></div>)}</dl></details>;
}

function JobStatusBadge({ status }: { status: JobStatus }) {
  return <span className={`job-status job-status--${status.toLowerCase()}`}><i />{friendlyStatus(status)}</span>;
}

function friendlyStatus(value: string) { return value.toLowerCase().replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase()); }
function friendlySource(value: string) { return value === "AI_AGENT" ? "EPM Assistant" : friendlyStatus(value); }
function friendlyKey(value: string) { return friendlyStatus(value.replaceAll("-", "_")); }
function shortId(value: string) { return `ID ${value.slice(0, 8)}`; }
function displayValue(value: unknown) { return typeof value === "object" ? JSON.stringify(value) : String(value); }
function formatDateTime(value: string) { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
function formatDuration(seconds: number | null, status: string) {
  if (seconds === null) return status === "RUNNING" ? "In progress" : status === "QUEUED" ? "Waiting" : "–";
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes ? `${minutes}m ${remainder}s` : `${remainder}s`;
}
function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
