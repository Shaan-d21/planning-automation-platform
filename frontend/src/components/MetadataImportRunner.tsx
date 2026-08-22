import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { MetadataImportRunInput, OperationSummary } from "../api/types";
import { ExecutionStep, ResultStep } from "./OperationRunner";
import { Icon } from "./Icon";
import { FeedbackBanner } from "./Feedback";
import { OracleFilePicker } from "./OracleFilePicker";
import { useOperationMonitor } from "./useOperationMonitor";

type RunnerStep = "SETUP" | "REVIEW" | "RUNNING" | "RESULT";
type FileSource = "configured" | "upload" | "inbox";

interface ReviewedMetadataImport {
  jobName: string;
  errorFileName: string | null;
  refreshJobName: string | null;
  source: FileSource;
  file: File | null;
  inboxFile: string | null;
}

interface MetadataImportRunnerProps {
  operation: OperationSummary;
  csrfToken: string;
  planningTaskId?: number | null;
  onBack: () => void;
}

const ALLOWED_EXTENSIONS = [".csv", ".zip"];

export function MetadataImportRunner({
  operation,
  csrfToken,
  planningTaskId = null,
  onBack
}: MetadataImportRunnerProps) {
  const [step, setStep] = useState<RunnerStep>("SETUP");
  const [jobs, setJobs] = useState<string[]>([]);
  const [refreshJobs, setRefreshJobs] = useState<string[]>([]);
  const [jobName, setJobName] = useState("");
  const [errorFileName, setErrorFileName] = useState("");
  const [refreshAfterImport, setRefreshAfterImport] = useState(false);
  const [refreshJobName, setRefreshJobName] = useState("");
  const [fileSource, setFileSource] = useState<FileSource>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [inboxFile, setInboxFile] = useState("");
  const [reviewed, setReviewed] = useState<ReviewedMetadataImport | null>(null);
  const [approved, setApproved] = useState(false);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { execution, monitorError } = useOperationMonitor(executionId);

  useEffect(() => {
    let active = true;
    api.metadataImportCatalog()
      .then((catalog) => {
        if (!active) return;
        setJobs(catalog.jobs);
        setRefreshJobs(catalog.refresh_jobs);
        if (!catalog.jobs.length) {
          setError("No saved Import Metadata jobs are visible to the connected Oracle user.");
        }
      })
      .catch((reason: unknown) => active && setError(errorMessage(reason)))
      .finally(() => active && setLoadingCatalog(false));
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (execution?.terminal) setStep("RESULT");
  }, [execution]);

  function review(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const normalizedInbox = inboxFile.trim();
    const normalizedErrorFile = errorFileName.trim();
    const normalizedRefreshJob = refreshJobName.trim();
    if (!jobName) {
      setError("Select a saved Import Metadata job.");
      return;
    }
    if (fileSource === "upload" && !file) {
      setError("Choose a local CSV or ZIP metadata file.");
      return;
    }
    if (file && !hasAllowedExtension(file.name)) {
      setError("Metadata Import supports CSV or ZIP files.");
      return;
    }
    if (fileSource === "inbox" && !normalizedInbox) {
      setError("Choose an existing Oracle Planning Inbox file.");
      return;
    }
    if (refreshAfterImport && !normalizedRefreshJob) {
      setError("Select or enter the saved Cube Refresh job to run after the import.");
      return;
    }
    setReviewed({
      jobName,
      errorFileName: normalizedErrorFile || null,
      refreshJobName: refreshAfterImport ? normalizedRefreshJob : null,
      source: fileSource,
      file: fileSource === "upload" ? file : null,
      inboxFile: fileSource === "inbox" ? normalizedInbox : null
    });
    setApproved(false);
    setStep("REVIEW");
  }

  async function start() {
    if (!reviewed || !approved) return;
    setStarting(true);
    setError(null);
    try {
      const uploadToken = reviewed.file
        ? (await api.uploadOperationFile(reviewed.file, csrfToken)).upload.token
        : null;
      const payload: MetadataImportRunInput = {
        job_name: reviewed.jobName,
        upload_token: uploadToken,
        inbox_file: reviewed.inboxFile,
        error_file_name: reviewed.errorFileName,
        refresh_job_name: reviewed.refreshJobName,
        ...(reviewed.source === "configured" ? { use_configured_file: true } : {}),
        ...(planningTaskId ? { planning_task_id: planningTaskId } : {})
      };
      const accepted = await api.startMetadataImport(payload, csrfToken);
      setExecutionId(accepted.execution_id);
      setStep("RUNNING");
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setStarting(false);
    }
  }

  function runAgain() {
    setReviewed(null);
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

    {step === "SETUP" && <div className="runner-layout"><form className="panel runner-form" onSubmit={review}>
      <div className="panel-heading"><span className="eyebrow">Step 1</span><h2>Prepare the metadata import</h2><p>Select the saved Oracle job, provide its source file, and decide whether a successful import should refresh the cube.</p></div>
      <div className="native-import-grid">
        <label className="runner-field native-import-job"><span>Import Metadata job *</span><select aria-label="Import Metadata job" value={jobName} disabled={loadingCatalog || !jobs.length} onChange={(event) => setJobName(event.target.value)}><option value="">{loadingCatalog ? "Loading saved jobs..." : "Select a saved Import Metadata job"}</option>{jobs.map((job) => <option key={job}>{job}</option>)}</select><small>{loadingCatalog ? "Reading live Oracle job definitions." : `${jobs.length} live job${jobs.length === 1 ? "" : "s"} available.`}</small></label>
        <label className="runner-field"><span>Error output filename</span><input aria-label="Metadata error output filename" value={errorFileName} onChange={(event) => setErrorFileName(event.target.value)} placeholder="Metadata_Errors.csv" maxLength={250} /><small>Optional. Oracle writes rejected records to this file when supported by the saved job.</small></label>
      </div>

      <section className="integration-file-section"><header><h3>Metadata source file</h3><p>Upload the current hierarchy file or choose a compatible file already present in the Oracle Planning Inbox.</p></header>
        <div className="file-source-choice" role="radiogroup" aria-label="Metadata file source">
          <label className={fileSource === "configured" ? "is-selected" : ""}><input type="radio" name="metadata-source" checked={fileSource === "configured"} onChange={() => { setFileSource("configured"); setFile(null); setInboxFile(""); }} /><Icon name="settings" /><span><strong>Use Oracle job files</strong><small>Use the dimension files already saved in the selected Metadata Import job.</small></span></label>
          <label className={fileSource === "upload" ? "is-selected" : ""}><input type="radio" name="metadata-source" checked={fileSource === "upload"} onChange={() => { setFileSource("upload"); setInboxFile(""); }} /><Icon name="data" /><span><strong>Upload local file</strong><small>Replace a matching Inbox filename before the job starts.</small></span></label>
          <label className={fileSource === "inbox" ? "is-selected" : ""}><input type="radio" name="metadata-source" checked={fileSource === "inbox"} onChange={() => { setFileSource("inbox"); setFile(null); }} /><Icon name="automation" /><span><strong>Choose from Oracle Inbox</strong><small>Select a compatible live file without typing its name.</small></span></label>
        </div>
        {fileSource === "upload" && <label className={`integration-upload${file ? " has-file" : ""}`}><input type="file" accept={ALLOWED_EXTENSIONS.join(",")} aria-label="Local metadata file" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><Icon name={file ? "check" : "data"} /><span><strong>{file?.name || "Choose a metadata file"}</strong><small>{file ? formatFileSize(file.size) : "CSV or ZIP"}</small></span><em>{file ? "Change file" : "Browse"}</em></label>}
        {fileSource === "inbox" && <OracleFilePicker purpose="metadata-import" value={inboxFile} onChange={setInboxFile} label="Existing Oracle metadata file" />}
        {fileSource === "configured" && <div className="configured-file"><Icon name="check" /><span><strong>Saved Oracle job configuration</strong><small>No runtime metadata file override will be supplied.</small></span></div>}
      </section>

      <section className="metadata-refresh-section">
        <label className="runner-approval metadata-refresh-choice"><input type="checkbox" checked={refreshAfterImport} onChange={(event) => setRefreshAfterImport(event.target.checked)} /><span><strong>Refresh the Planning cube after a successful import</strong><small>The refresh is skipped automatically if Metadata Import fails.</small></span></label>
        {refreshAfterImport && <label className="runner-field"><span>Saved Cube Refresh job *</span><input list="metadata-refresh-jobs" aria-label="Metadata Cube Refresh job" value={refreshJobName} onChange={(event) => setRefreshJobName(event.target.value)} placeholder="Select or enter the exact saved job name" /><datalist id="metadata-refresh-jobs">{refreshJobs.map((job) => <option value={job} key={job} />)}</datalist><small>{refreshJobs.length ? `${refreshJobs.length} live suggestion${refreshJobs.length === 1 ? "" : "s"}. Exact saved names are also accepted.` : "Oracle did not return suggestions; enter the exact saved Refresh Database job name."}</small></label>}
      </section>

      <aside className="native-import-note"><Icon name="settings" /><div><strong>The saved Oracle job owns the metadata configuration</strong><p>Dimensions, delimiters, member properties, mappings, validation behavior, and target cube remain configured in Oracle Planning.</p></div></aside>
      <footer className="runner-actions"><button type="button" className="button button--quiet" onClick={onBack}>Cancel</button><button className="button button--primary" disabled={loadingCatalog || !jobs.length}>Review Metadata Import <Icon name="arrow" /></button></footer>
    </form><MetadataGuidance /></div>}

    {step === "REVIEW" && reviewed && <section className="panel runner-review">
      <div className="panel-heading"><span className="eyebrow">Step 2</span><h2>Review the Metadata Import</h2><p>No file has been uploaded and no Oracle job has started. Confirm the hierarchy file and post-import action.</p></div>
      <div className="review-target"><span className="service-card__icon"><Icon name="tasks" /></span><div><small>Saved Import Metadata job</small><strong>{reviewed.jobName}</strong></div></div>
      <dl className="review-summary"><div><dt>File source</dt><dd>{fileSourceLabel(reviewed.source)}</dd></div><div><dt>Metadata file</dt><dd>{reviewed.file?.name || reviewed.inboxFile || "Configured in saved Oracle job"}</dd></div><div><dt>Error output</dt><dd>{reviewed.errorFileName || "Oracle default"}</dd></div><div><dt>After successful import</dt><dd>{reviewed.refreshJobName ? `Run ${reviewed.refreshJobName}` : "No cube refresh"}</dd></div></dl>
      {reviewed.file && <div className="runner-warning integration-replace-notice"><Icon name="alert" /><div><strong>A matching Inbox filename will be replaced</strong><p>If {reviewed.file.name} already exists, the platform replaces it before starting the selected Import Metadata job.</p></div></div>}
      {reviewed.refreshJobName && <div className="native-import-note"><Icon name="refresh" /><div><strong>Cube Refresh is conditional</strong><p>{reviewed.refreshJobName} starts only after Oracle reports a successful metadata import.</p></div></div>}
      <label className="runner-approval"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} /><span><strong>I reviewed the saved job, source file, and optional refresh</strong><small>The approved metadata changes will be submitted to the connected Oracle Planning application.</small></span></label>
      <footer className="runner-actions"><button className="button button--quiet" disabled={starting} onClick={() => { setError(null); setStep("SETUP"); }}>Back to inputs</button><button className="button button--primary" disabled={!approved || starting} onClick={() => void start()}>{starting ? <><span className="spinner" /> Uploading and starting...</> : <>Start Metadata Import <Icon name="arrow" /></>}</button></footer>
    </section>}

    {step === "RUNNING" && <ExecutionStep execution={execution} operation={operation} />}
    {step === "RESULT" && execution && <ResultStep execution={execution} onAgain={runAgain} onBack={onBack} />}
  </section>;
}

function MetadataGuidance() {
  return <aside className="panel runner-guidance"><span className="eyebrow">Before you run</span><h2>Protect the application structure</h2><ol><li><span>1</span><div><strong>Use the saved job</strong><p>The Oracle job determines which dimensions and properties are imported.</p></div></li><li><span>2</span><div><strong>Confirm the current file</strong><p>Use a compatible CSV or ZIP and review its filename before approval.</p></div></li><li><span>3</span><div><strong>Refresh only when needed</strong><p>Choose a saved refresh job when imported metadata must be synchronized with the cube.</p></div></li></ol></aside>;
}

function RunnerSteps({ current }: { current: RunnerStep }) {
  const steps: [RunnerStep, string, string][] = [["SETUP", "Prepare", "Job, file, and refresh"], ["REVIEW", "Review", "Confirm structural change"], ["RUNNING", "Import", "Monitor Oracle"], ["RESULT", "Result", "Review outputs"]];
  const currentIndex = steps.findIndex(([value]) => value === current);
  return <ol className="runner-steps" aria-label="Operation progress">{steps.map(([value, label, description], index) => <li className={index === currentIndex ? "is-current" : index < currentIndex ? "is-complete" : ""} key={value}><span>{index < currentIndex ? <Icon name="check" /> : index + 1}</span><div><strong>{label}</strong><small>{description}</small></div></li>)}</ol>;
}

function hasAllowedExtension(filename: string) {
  const normalized = filename.toLowerCase();
  return ALLOWED_EXTENSIONS.some((extension) => normalized.endsWith(extension));
}

function fileSourceLabel(source: FileSource) {
  if (source === "upload") return "Local upload";
  if (source === "inbox") return "Oracle Inbox";
  return "Saved Oracle job";
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "The Metadata Import could not be completed.";
}
