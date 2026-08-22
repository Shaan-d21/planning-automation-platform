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
    return (!needle || [job.name, job.initiated_by, job.execution_id]
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
    <td><div className="job-actor"><strong>{job.initiated_by}</strong><small>{friendlySource(job.trigger_source)}</small></div></td>
    <td><span className="job-progress">{job.completed_steps} / {job.total_steps || "–"}</span></td>
    <td><button className="button button--quiet" disabled={busy} onClick={() => onInspect(job.execution_id)}>{busy ? <span className="spinner" /> : null} View details</button></td>
  </tr>;
}

function JobDetailDialog({ job, csrfToken, allowRecovery, onRecoveryStarted, onClose }: { job: JobActivityDetail; csrfToken: string; allowRecovery: boolean; onRecoveryStarted: (executionId: string) => Promise<void>; onClose: () => void }) {
  const [recoveryPlan, setRecoveryPlan] = useState<StandaloneFlowRecoveryPlan | null>(null);
  const [reviewingRecovery, setReviewingRecovery] = useState(false);
  const [recoveryError, setRecoveryError] = useState<string | null>(null);
  const canRecover = allowRecovery && job.status === "FAILED" && job.name.startsWith("Standalone Flow - ");

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

  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="job-dialog" role="dialog" aria-modal="true" aria-labelledby="job-dialog-title">
    <header><div><span className="eyebrow">Execution evidence</span><h2 id="job-dialog-title">{job.name}</h2><div className="job-dialog-meta"><JobStatusBadge status={job.status} /><span>{formatDateTime(job.started_at)}</span><span>{formatDuration(job.duration_seconds, job.status)}</span><span>{job.initiated_by} · {friendlySource(job.trigger_source)}</span></div></div><button aria-label="Close job details" onClick={onClose}><Icon name="close" /></button></header>
    {job.error_message && <aside className="job-error"><Icon name="alert" /><div><strong>Execution failed</strong><p>{job.error_message}</p></div></aside>}
    <div className="job-dialog-body">
      {recoveryError && <FeedbackBanner tone="error" title="Recovery review is unavailable" message={recoveryError} />}
      <div className="job-identity"><span><small>Execution ID</small><strong>{job.execution_id}</strong></span><span><small>Steps completed</small><strong>{job.completed_steps} of {job.total_steps}</strong></span></div>
      {job.record_statistics && <OracleLoadStatistics statistics={job.record_statistics} />}
      <section className="job-step-section"><div className="panel-heading"><div><span className="eyebrow">Timeline</span><h3>Execution steps</h3></div></div>
        {job.steps.length ? <ol className="job-step-list">{job.steps.map((step) => <li className={`job-step job-step--${step.status.toLowerCase()}`} key={step.sequence}><span className="job-step-number">{step.status === "SUCCESS" ? <Icon name="check" /> : step.status === "FAILED" ? <Icon name="alert" /> : step.sequence}</span><div className="job-step-content"><header><div><strong>{step.name}</strong><small>Step {step.sequence} · {formatDuration(step.duration_seconds, step.status === "RUNNING" ? "RUNNING" : "SUCCESS")}</small></div><span>{friendlyStatus(step.status)}</span></header>{step.error_message && <p className="step-error">{step.error_message}</p>}<StepDetails details={step.details} /></div></li>)}</ol> : <div className="work-empty work-empty--compact"><span><Icon name="activity" /></span><h3>No step evidence was recorded</h3><p>This execution contains only an overall status.</p></div>}
      </section>
    </div>
    <footer>{canRecover && <button className="button button--primary" disabled={reviewingRecovery} onClick={() => void reviewRecovery()}>{reviewingRecovery ? <><span className="spinner" /> Reviewing recovery…</> : "Review recovery"}</button>}<button className="button button--secondary" onClick={onClose}>Close</button></footer>
    {recoveryPlan && <StandaloneFlowRecoveryDialog plan={recoveryPlan} csrfToken={csrfToken} onClose={() => setRecoveryPlan(null)} onStarted={(accepted) => { setRecoveryPlan(null); void onRecoveryStarted(accepted.execution_id); }} />}
  </section></div>;
}

function StepDetails({ details }: { details: Record<string, unknown> }) {
  const entries = Object.entries(details).filter(([, value]) => value !== null && value !== "");
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
