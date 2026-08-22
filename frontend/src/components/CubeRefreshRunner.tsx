import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { CubeRefreshRunInput, OperationSummary } from "../api/types";
import { ExecutionStep, ResultStep } from "./OperationRunner";
import { Icon } from "./Icon";
import { FeedbackBanner } from "./Feedback";
import { useOperationMonitor } from "./useOperationMonitor";

type RunnerStep = "SETUP" | "REVIEW" | "RUNNING" | "RESULT";

interface CubeRefreshRunnerProps {
  operation: OperationSummary;
  csrfToken: string;
  planningTaskId?: number | null;
  onBack: () => void;
}

export function CubeRefreshRunner({
  operation,
  csrfToken,
  planningTaskId = null,
  onBack
}: CubeRefreshRunnerProps) {
  const [step, setStep] = useState<RunnerStep>("SETUP");
  const [jobs, setJobs] = useState<string[]>([]);
  const [jobName, setJobName] = useState("");
  const [reviewedJob, setReviewedJob] = useState<string | null>(null);
  const [approved, setApproved] = useState(false);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [catalogWarning, setCatalogWarning] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { execution, monitorError } = useOperationMonitor(executionId);

  useEffect(() => {
    let active = true;
    api.cubeRefreshCatalog()
      .then((catalog) => {
        if (!active) return;
        setJobs(catalog.jobs);
        if (!catalog.jobs.length) {
          setCatalogWarning("Oracle did not expose the saved Refresh Database job names. Generic API labels are hidden because they may not exist as jobs. Enter the exact name shown in Oracle Planning.");
        }
      })
      .catch(() => {
        if (active) setCatalogWarning("Live job suggestions are unavailable. Enter the exact saved Refresh Database job name to continue.");
      })
      .finally(() => active && setLoadingCatalog(false));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (execution?.terminal) setStep("RESULT");
  }, [execution]);

  function review(event: FormEvent) {
    event.preventDefault();
    const normalizedJob = jobName.trim();
    setError(null);
    if (!normalizedJob) {
      setError("Select or enter the exact saved Cube Refresh job name.");
      return;
    }
    setReviewedJob(normalizedJob);
    setApproved(false);
    setStep("REVIEW");
  }

  async function start() {
    if (!reviewedJob || !approved) return;
    setStarting(true);
    setError(null);
    try {
      const payload: CubeRefreshRunInput = {
        job_name: reviewedJob,
        ...(planningTaskId ? { planning_task_id: planningTaskId } : {})
      };
      const accepted = await api.startCubeRefresh(payload, csrfToken);
      setExecutionId(accepted.execution_id);
      setStep("RUNNING");
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setStarting(false);
    }
  }

  function runAgain() {
    setReviewedJob(null);
    setApproved(false);
    setExecutionId(null);
    setError(null);
    setStep("SETUP");
  }

  return <section className="operation-runner">
    <header className="runner-header">
      <button className="button button--quiet" onClick={onBack}><Icon name="arrow" /> Back to Operations</button>
      <div><span className="eyebrow">{operation.category}</span><h1>{operation.display_name}</h1><p>{operation.description}</p></div>
      <span className="risk-badge">{planningTaskId ? "Assigned task" : operation.risk_level}</span>
    </header>

    <RunnerSteps current={step} />
    {(error || monitorError) && <FeedbackBanner tone="error" title="Action required" message={error || monitorError} />}

    {step === "SETUP" && <div className="runner-layout"><form className="panel runner-form cube-refresh-form" onSubmit={review}>
      <div className="panel-heading"><span className="eyebrow">Step 1</span><h2>Select the saved refresh job</h2><p>Choose a live suggestion or enter the exact name displayed under Oracle Planning’s Refresh Database jobs.</p></div>
      <label className="runner-field"><span>Saved Cube Refresh job *</span><input list="cube-refresh-jobs" aria-label="Saved Cube Refresh job" value={jobName} disabled={loadingCatalog} onChange={(event) => setJobName(event.target.value)} placeholder={loadingCatalog ? "Loading saved jobs..." : "Select or enter the exact saved job name"} autoComplete="off" /><datalist id="cube-refresh-jobs">{jobs.map((job) => <option value={job} key={job} />)}</datalist><small>{loadingCatalog ? "Reading live Oracle job definitions." : jobs.length ? `${jobs.length} live suggestion${jobs.length === 1 ? "" : "s"}. Exact names with spaces and underscores are accepted.` : "Enter the exact job name from Jobs > Refresh Database."}</small></label>
      {catalogWarning && <div className="runner-warning cube-refresh-discovery-note"><Icon name="alert" /><div><strong>Job discovery is limited</strong><p>{catalogWarning}</p></div></div>}
      <aside className="cube-refresh-impact"><Icon name="refresh" /><div><strong>The saved Oracle job owns the refresh configuration</strong><p>This platform executes the selected job without changing its cube selections or Oracle job definition.</p></div></aside>
      <footer className="runner-actions"><button type="button" className="button button--quiet" onClick={onBack}>Cancel</button><button className="button button--primary" disabled={loadingCatalog}>Review Cube Refresh <Icon name="arrow" /></button></footer>
    </form><RefreshGuidance /></div>}

    {step === "REVIEW" && reviewedJob && <section className="panel runner-review">
      <div className="panel-heading"><span className="eyebrow">Step 2</span><h2>Review the Cube Refresh</h2><p>No Oracle job has started. Confirm the exact saved name and application-wide impact before execution.</p></div>
      <div className="review-target"><span className="service-card__icon"><Icon name="refresh" /></span><div><small>Saved Refresh Database job</small><strong>{reviewedJob}</strong></div></div>
      <dl className="review-summary"><div><dt>Planning application</dt><dd>Connected environment</dd></div><div><dt>Configuration source</dt><dd>Saved Oracle job</dd></div><div><dt>Execution</dt><dd>Run once and monitor</dd></div></dl>
      <div className="runner-warning"><Icon name="alert" /><div><strong>A Cube Refresh can affect active users</strong><p>It synchronizes Planning metadata with the underlying cube and may temporarily affect application availability or performance.</p></div></div>
      <label className="runner-approval"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} /><span><strong>I confirmed the exact saved job and the application is ready</strong><small>The platform will start this Oracle job immediately and monitor it to a terminal status.</small></span></label>
      <footer className="runner-actions"><button className="button button--quiet" disabled={starting} onClick={() => { setError(null); setStep("SETUP"); }}>Back to job selection</button><button className="button button--primary" disabled={!approved || starting} onClick={() => void start()}>{starting ? <><span className="spinner" /> Starting refresh...</> : <>Start Cube Refresh <Icon name="arrow" /></>}</button></footer>
    </section>}

    {step === "RUNNING" && <ExecutionStep execution={execution} operation={operation} />}
    {step === "RESULT" && execution && <ResultStep execution={execution} onAgain={runAgain} onBack={onBack} />}
  </section>;
}

function RefreshGuidance() {
  return <aside className="panel runner-guidance"><span className="eyebrow">Before you run</span><h2>Refresh deliberately</h2><ol><li><span>1</span><div><strong>Confirm metadata readiness</strong><p>Run after approved structural changes that require cube synchronization.</p></div></li><li><span>2</span><div><strong>Consider active users</strong><p>Schedule carefully because the refresh may affect Planning availability and performance.</p></div></li><li><span>3</span><div><strong>Monitor completion</strong><p>The platform waits for Oracle’s terminal status and records any returned failure details.</p></div></li></ol></aside>;
}

function RunnerSteps({ current }: { current: RunnerStep }) {
  const steps: [RunnerStep, string, string][] = [["SETUP", "Prepare", "Select saved job"], ["REVIEW", "Review", "Confirm impact"], ["RUNNING", "Refresh", "Monitor Oracle"], ["RESULT", "Result", "Review outcome"]];
  const currentIndex = steps.findIndex(([value]) => value === current);
  return <ol className="runner-steps" aria-label="Operation progress">{steps.map(([value, label, description], index) => <li className={index === currentIndex ? "is-current" : index < currentIndex ? "is-complete" : ""} key={value}><span>{index < currentIndex ? <Icon name="check" /> : index + 1}</span><div><strong>{label}</strong><small>{description}</small></div></li>)}</ol>;
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "The Cube Refresh could not be completed.";
}
