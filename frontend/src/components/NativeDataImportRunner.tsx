import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { DataImportRunInput, OperationSummary } from "../api/types";
import { ExecutionStep, ResultStep } from "./OperationRunner";
import { Icon } from "./Icon";
import { FeedbackBanner } from "./Feedback";
import { OracleFilePicker } from "./OracleFilePicker";
import { useOperationMonitor } from "./useOperationMonitor";

type RunnerStep = "SETUP" | "REVIEW" | "RUNNING" | "RESULT";
type FileSource = "configured" | "upload" | "inbox";

interface ReviewedImport {
  jobName: string;
  errorFileName: string | null;
  source: FileSource;
  file: File | null;
  inboxFile: string | null;
}

interface NativeDataImportRunnerProps {
  operation: OperationSummary;
  csrfToken: string;
  planningTaskId?: number | null;
  onBack: () => void;
}

const ALLOWED_EXTENSIONS = [".csv", ".txt", ".zip"];

export function NativeDataImportRunner({ operation, csrfToken, planningTaskId = null, onBack }: NativeDataImportRunnerProps) {
  const [step, setStep] = useState<RunnerStep>("SETUP");
  const [jobs, setJobs] = useState<string[]>([]);
  const [jobName, setJobName] = useState("");
  const [errorFileName, setErrorFileName] = useState("");
  const [fileSource, setFileSource] = useState<FileSource>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [inboxFile, setInboxFile] = useState("");
  const [reviewed, setReviewed] = useState<ReviewedImport | null>(null);
  const [approved, setApproved] = useState(false);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { execution, monitorError } = useOperationMonitor(executionId);

  useEffect(() => {
    let active = true;
    api.dataImportCatalog()
      .then((catalog) => {
        if (!active) return;
        setJobs(catalog.jobs);
        if (!catalog.jobs.length) setError("No saved Planning Import Data jobs are available to the connected user.");
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
    if (!jobName) {
      setError("Select a saved Planning Import Data job.");
      return;
    }
    if (fileSource === "upload" && !file) {
      setError("Choose a local CSV, TXT, or ZIP data file.");
      return;
    }
    if (file && !hasAllowedExtension(file.name)) {
      setError("Planning Data Import supports CSV, TXT, or ZIP files.");
      return;
    }
    if (fileSource === "inbox" && !normalizedInbox) {
      setError("Choose an existing Oracle Planning Inbox file.");
      return;
    }
    setReviewed({
      jobName,
      errorFileName: normalizedErrorFile || null,
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
      const payload: DataImportRunInput = {
        job_name: reviewed.jobName,
        upload_token: uploadToken,
        inbox_file: reviewed.inboxFile,
        error_file_name: reviewed.errorFileName,
        ...(reviewed.source === "configured" ? { use_configured_file: true } : {}),
        ...(planningTaskId ? { planning_task_id: planningTaskId } : {})
      };
      const accepted = await api.startDataImport(payload, csrfToken);
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
      <span className="risk-badge">{operation.risk_level}</span>
    </header>

    <RunnerSteps current={step} />
    {(error || monitorError) && <FeedbackBanner tone="error" title="Action required" message={error || monitorError} />}

    {step === "SETUP" && <div className="runner-layout"><form className="panel runner-form" onSubmit={review}>
      <div className="panel-heading"><span className="eyebrow">Step 1</span><h2>Prepare the Planning data import</h2><p>Select the saved Oracle job and provide the exact data file for this run.</p></div>
      <div className="native-import-grid">
        <label className="runner-field native-import-job"><span>Import Data job *</span><select aria-label="Import Data job" value={jobName} disabled={loadingCatalog || !jobs.length} onChange={(event) => setJobName(event.target.value)}><option value="">{loadingCatalog ? "Loading saved jobs..." : "Select a saved Import Data job"}</option>{jobs.map((job) => <option key={job}>{job}</option>)}</select><small>{loadingCatalog ? "Reading live Oracle job definitions." : `${jobs.length} live job${jobs.length === 1 ? "" : "s"} available.`}</small></label>
        <label className="runner-field"><span>Error output filename</span><input aria-label="Error output filename" value={errorFileName} onChange={(event) => setErrorFileName(event.target.value)} placeholder="Forecast_Errors.log" maxLength={250} /><small>Optional. Leave blank to use Oracle’s default behavior.</small></label>
      </div>

      <section className="integration-file-section"><header><h3>Data source file</h3><p>Upload the current file or reuse a file already present in the Oracle Planning Inbox.</p></header>
        <div className="file-source-choice" role="radiogroup" aria-label="Data file source">
          <label className={fileSource === "configured" ? "is-selected" : ""}><input type="radio" name="data-import-source" checked={fileSource === "configured"} onChange={() => { setFileSource("configured"); setFile(null); setInboxFile(""); }} /><Icon name="settings" /><span><strong>Use Oracle job file</strong><small>Run with the filename already saved in the selected Import Data job.</small></span></label>
          <label className={fileSource === "upload" ? "is-selected" : ""}><input type="radio" name="data-import-source" checked={fileSource === "upload"} onChange={() => { setFileSource("upload"); setInboxFile(""); }} /><Icon name="data" /><span><strong>Upload local file</strong><small>Replace a matching Inbox filename before the import starts.</small></span></label>
          <label className={fileSource === "inbox" ? "is-selected" : ""}><input type="radio" name="data-import-source" checked={fileSource === "inbox"} onChange={() => { setFileSource("inbox"); setFile(null); }} /><Icon name="automation" /><span><strong>Choose from Oracle Inbox</strong><small>Select a compatible live file without typing its name.</small></span></label>
        </div>
        {fileSource === "upload" && <label className={`integration-upload${file ? " has-file" : ""}`}><input type="file" accept={ALLOWED_EXTENSIONS.join(",")} aria-label="Local Planning data file" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><Icon name={file ? "check" : "data"} /><span><strong>{file?.name || "Choose a Planning data file"}</strong><small>{file ? formatFileSize(file.size) : "CSV, TXT, or ZIP"}</small></span><em>{file ? "Change file" : "Browse"}</em></label>}
        {fileSource === "inbox" && <OracleFilePicker purpose="data-import" value={inboxFile} onChange={setInboxFile} label="Existing Oracle Inbox file" />}
        {fileSource === "configured" && <div className="configured-file"><Icon name="check" /><span><strong>Saved Oracle job configuration</strong><small>No runtime filename override will be supplied.</small></span></div>}
      </section>

      <aside className="native-import-note"><Icon name="settings" /><div><strong>The selected Oracle job owns the file layout</strong><p>Cube, delimiter, dimension columns, period columns, mappings, and import behavior are not duplicated here. Delimited and multi-column numeric files both work when they match the saved job.</p></div></aside>
      <footer className="runner-actions"><button type="button" className="button button--quiet" onClick={onBack}>Cancel</button><button className="button button--primary" disabled={loadingCatalog || !jobs.length}>Review Data Import <Icon name="arrow" /></button></footer>
    </form><ImportGuidance /></div>}

    {step === "REVIEW" && reviewed && <section className="panel runner-review">
      <div className="panel-heading"><span className="eyebrow">Step 2</span><h2>Review the Planning Data Import</h2><p>No file has been uploaded and no Oracle job has started. Confirm every value first.</p></div>
      <div className="review-target"><span className="service-card__icon"><Icon name="data" /></span><div><small>Saved Import Data job</small><strong>{reviewed.jobName}</strong></div></div>
      <dl className="review-summary"><div><dt>File source</dt><dd>{fileSourceLabel(reviewed.source)}</dd></div><div><dt>Data file</dt><dd>{reviewed.file?.name || reviewed.inboxFile || "Configured in saved Oracle job"}</dd></div><div><dt>Error output</dt><dd>{reviewed.errorFileName || "Oracle default"}</dd></div></dl>
      {reviewed.file && <div className="runner-warning integration-replace-notice"><Icon name="alert" /><div><strong>A matching Inbox filename will be replaced</strong><p>If {reviewed.file.name} already exists, the platform replaces it before starting the selected Import Data job.</p></div></div>}
      <label className="runner-approval"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} /><span><strong>I reviewed the saved job and source file</strong><small>The approved file will be loaded into the connected Oracle Planning application.</small></span></label>
      <footer className="runner-actions"><button className="button button--quiet" disabled={starting} onClick={() => { setError(null); setStep("SETUP"); }}>Back to inputs</button><button className="button button--primary" disabled={!approved || starting} onClick={() => void start()}>{starting ? <><span className="spinner" /> Uploading and starting...</> : <>Start Planning Data Import <Icon name="arrow" /></>}</button></footer>
    </section>}

    {step === "RUNNING" && <ExecutionStep execution={execution} operation={operation} />}
    {step === "RESULT" && execution && <ResultStep execution={execution} onAgain={runAgain} onBack={onBack} />}
  </section>;
}

function ImportGuidance() {
  return <aside className="panel runner-guidance"><span className="eyebrow">Before you run</span><h2>Use a compatible file</h2><ol><li><span>1</span><div><strong>Choose the saved job</strong><p>The job determines the target cube and expected dimensional layout.</p></div></li><li><span>2</span><div><strong>Provide the current file</strong><p>Upload a new CSV, TXT, or ZIP file, or reuse an existing Inbox file.</p></div></li><li><span>3</span><div><strong>Review Oracle results</strong><p>The execution and any returned failure details remain available in Jobs &amp; Activity.</p></div></li></ol></aside>;
}

function RunnerSteps({ current }: { current: RunnerStep }) {
  const steps: [RunnerStep, string, string][] = [["SETUP", "Prepare", "Job and source file"], ["REVIEW", "Review", "Confirm import scope"], ["RUNNING", "Import", "Monitor Oracle"], ["RESULT", "Result", "Review outcome"]];
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
  return reason instanceof Error ? reason.message : "The Planning Data Import could not be completed.";
}
