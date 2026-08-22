import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api/client";
import type { DataIntegrationDefinition, DataIntegrationRunInput, OperationSummary, OracleArtifactRegistration } from "../api/types";
import { ExecutionStep, ResultStep } from "./OperationRunner";
import { Icon } from "./Icon";
import { FeedbackBanner } from "./Feedback";
import { OracleFilePicker } from "./OracleFilePicker";
import { useOperationMonitor } from "./useOperationMonitor";

type RunnerStep = "SETUP" | "REVIEW" | "RUNNING" | "RESULT";
type FileSource = "configured" | "upload" | "inbox";
type PeriodInputMode = "mapped" | "planning" | "exact";

interface ReviewedIntegration {
  payload: Omit<DataIntegrationRunInput, "upload_token" | "inbox_file">;
  source: FileSource;
  file: File | null;
  inboxFile: string | null;
}

interface DataIntegrationRunnerProps {
  operation: OperationSummary;
  csrfToken: string;
  canManageCatalog: boolean;
  planningTaskId?: number | null;
  onBack: () => void;
}

const IMPORT_MODES = ["Replace", "Append", "Map and Validate", "No Import"];
const EXPORT_MODES = ["Merge", "Replace", "Accumulate", "Subtract", "No Export", "Check"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const PLANNING_YEARS = Array.from({ length: 21 }, (_, index) => `FY${String(20 + index).padStart(2, "0")}`);

export function DataIntegrationRunner({ operation, csrfToken, canManageCatalog, planningTaskId = null, onBack }: DataIntegrationRunnerProps) {
  const [step, setStep] = useState<RunnerStep>("SETUP");
  const [integrations, setIntegrations] = useState<DataIntegrationDefinition[]>([]);
  const [artifacts, setArtifacts] = useState<OracleArtifactRegistration[]>([]);
  const [integrationName, setIntegrationName] = useState("");
  const [sessionPendingIntegration, setSessionPendingIntegration] = useState<DataIntegrationDefinition | null>(null);
  const [registerName, setRegisterName] = useState("");
  const [periodInputMode, setPeriodInputMode] = useState<PeriodInputMode>("mapped");
  const [planningYear, setPlanningYear] = useState("");
  const [startMonth, setStartMonth] = useState("");
  const [endMonth, setEndMonth] = useState("");
  const [startPeriod, setStartPeriod] = useState("");
  const [endPeriod, setEndPeriod] = useState("");
  const [importMode, setImportMode] = useState("Replace");
  const [exportMode, setExportMode] = useState("Merge");
  const [fileSource, setFileSource] = useState<FileSource>("upload");
  const [file, setFile] = useState<File | null>(null);
  const [inboxFile, setInboxFile] = useState("");
  const [reviewed, setReviewed] = useState<ReviewedIntegration | null>(null);
  const [approved, setApproved] = useState(false);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [registering, setRegistering] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [catalogNotice, setCatalogNotice] = useState<string | null>(null);
  const [oracleAvailable, setOracleAvailable] = useState<boolean | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { execution, monitorError } = useOperationMonitor(executionId);

  useEffect(() => {
    let active = true;
    api.dataIntegrationCatalog()
      .then((catalog) => {
        if (!active) return;
        setIntegrations(catalog.integrations);
        setArtifacts(catalog.artifacts ?? []);
        if (canManageCatalog) void synchronizeCatalog();
      })
      .catch((reason: unknown) => active && setError(errorMessage(reason)))
      .finally(() => active && setLoadingCatalog(false));
    return () => { active = false; };
  }, []);

  const verifiedNames = new Set(
    artifacts.filter((item) => item.is_verified && item.is_active).map((item) => item.oracle_identifier.toLowerCase())
  );
  const selectableIntegrations = artifacts.length
    ? integrations.filter((item) => verifiedNames.has(item.name.toLowerCase()))
    : integrations;

  async function reloadCatalog() {
    const catalog = await api.dataIntegrationCatalog();
    setIntegrations(catalog.integrations);
    setArtifacts(catalog.artifacts ?? []);
    const selectable = new Set(
      (catalog.artifacts?.length
        ? catalog.artifacts.filter((item) => item.is_verified && item.is_active).map((item) => item.oracle_identifier)
        : catalog.integrations.map((item) => item.name)
      ).map((item) => item.toLowerCase())
    );
    if (
      integrationName
      && !selectable.has(integrationName.toLowerCase())
      && sessionPendingIntegration?.name.toLowerCase() !== integrationName.toLowerCase()
    ) {
      setIntegrationName("");
    }
  }

  async function registerIntegration() {
    const name = registerName.trim();
    if (!name) {
      setError("Enter the exact Data Integration name shown in Oracle.");
      return;
    }
    setRegistering(true);
    setError(null);
    setCatalogNotice(null);
    try {
      const result = await api.registerDataIntegration(name, csrfToken);
      setSessionPendingIntegration({
        name: result.integration.oracle_identifier,
        description: "Pending this governed verification run",
      });
      await reloadCatalog();
      setIntegrationName(result.integration.oracle_identifier);
      setRegisterName("");
      setCatalogNotice(result.message);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setRegistering(false);
    }
  }

  async function synchronizeCatalog() {
    setSyncing(true);
    setError(null);
    setCatalogNotice(null);
    try {
      const result = await api.synchronizeOracleCatalog(csrfToken);
      setOracleAvailable(result.sync.oracle_available);
      await reloadCatalog();
      setCatalogNotice(result.sync.message);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSyncing(false);
    }
  }

  useEffect(() => {
    if (execution?.terminal) setStep("RESULT");
  }, [execution]);

  function review(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const generatedPeriods = periodInputMode === "exact"
      ? { start: startPeriod.trim(), end: endPeriod.trim() }
      : buildPeriodRange(planningYear, startMonth, endMonth, periodInputMode);
    const normalizedStart = generatedPeriods.start;
    const normalizedEnd = generatedPeriods.end;
    const normalizedInbox = inboxFile.trim();
    if (!integrationName) {
      setError("Select a Data Integration before continuing.");
      return;
    }
    if (!normalizedStart || !normalizedEnd) {
      setError(periodInputMode === "exact"
        ? "Enter both exact Oracle period names."
        : "Select the Planning year, start month, and end month.");
      return;
    }
    if (fileSource === "upload" && !file) {
      setError("Select a local .csv, .txt, or .zip data file.");
      return;
    }
    if (file && ![".csv", ".txt", ".zip", ".dat"].some((extension) => file.name.toLowerCase().endsWith(extension))) {
      setError("Data Integration uploads support .csv, .txt, .zip, or .dat files.");
      return;
    }
    if (fileSource === "inbox" && !normalizedInbox) {
      setError("Enter an existing Oracle Inbox file reference.");
      return;
    }
    setReviewed({
      payload: {
        integration_name: integrationName,
        start_period: normalizedStart,
        end_period: normalizedEnd,
        import_mode: importMode,
        export_mode: exportMode
      },
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
      const accepted = await api.startDataIntegration(
        {
          ...reviewed.payload,
          upload_token: uploadToken,
          inbox_file: reviewed.inboxFile,
          ...(reviewed.source === "configured" ? { use_configured_file: true } : {}),
          ...(planningTaskId ? { planning_task_id: planningTaskId } : {})
        },
        csrfToken
      );
      setExecutionId(accepted.execution_id);
      setStep("RUNNING");
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setStarting(false);
    }
  }

  function runAgain() {
    void reloadCatalog();
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
      <div className="panel-heading"><span className="eyebrow">Step 1</span><h2>Prepare the data load</h2><p>Choose an approved integration, define the period range and provide the exact source file.</p></div>
      <IntegrationCatalogHealth artifacts={artifacts} canManage={canManageCatalog} syncing={syncing} notice={catalogNotice} onSync={() => void synchronizeCatalog()} />
      <div className="integration-input-grid">
        <label className="runner-field integration-field--wide"><span>Data Integration *</span><select value={integrationName} disabled={loadingCatalog || oracleAvailable === false || (!selectableIntegrations.length && !sessionPendingIntegration)} onChange={(event) => setIntegrationName(event.target.value)}><option value="">{loadingCatalog ? "Loading verified integrations..." : "Select a verified Data Integration"}</option>{selectableIntegrations.length > 0 && <optgroup label="Verified in Oracle">{selectableIntegrations.map((item) => <option value={item.name} key={item.name}>{item.description ? `${item.name} - ${item.description}` : item.name}</option>)}</optgroup>}{sessionPendingIntegration && !verifiedNames.has(sessionPendingIntegration.name.toLowerCase()) && <optgroup label="New exact-name verification"><option value={sessionPendingIntegration.name}>{sessionPendingIntegration.name} — verify with this run</option></optgroup>}</select><small>{loadingCatalog ? "Reading the approved environment catalog." : oracleAvailable === false ? "Oracle is unavailable; execution is temporarily disabled." : `${selectableIntegrations.length} Oracle-verified integration${selectableIntegrations.length === 1 ? "" : "s"} available. Unavailable registrations are hidden.`}</small></label>
        <label className="runner-field"><span>Period naming *</span><select aria-label="Period naming" value={periodInputMode} onChange={(event) => setPeriodInputMode(event.target.value as PeriodInputMode)}><option value="mapped">Mapped periods (Jan-27)</option><option value="planning">Planning members (Jan#FY27)</option><option value="exact">Advanced: exact Oracle names</option></select><small>Use the format configured in Oracle Data Integration.</small></label>
        {periodInputMode !== "exact" && <>
          <label className="runner-field"><span>Planning year *</span><select aria-label="Planning year" value={planningYear} onChange={(event) => setPlanningYear(event.target.value)}><option value="">Select year</option>{PLANNING_YEARS.map((year) => <option key={year}>{year}</option>)}</select><small>Used to build the Oracle period values.</small></label>
          <label className="runner-field"><span>Start month *</span><select aria-label="Start month" value={startMonth} onChange={(event) => setStartMonth(event.target.value)}><option value="">Select month</option>{MONTHS.map((month) => <option key={month}>{month}</option>)}</select></label>
          <label className="runner-field"><span>End month *</span><select aria-label="End month" value={endMonth} onChange={(event) => setEndMonth(event.target.value)}><option value="">Select month</option>{MONTHS.map((month) => <option key={month}>{month}</option>)}</select><small>May match the start month.</small></label>
        </>}
        {periodInputMode === "exact" && <>
          <label className="runner-field"><span>Start period *</span><input value={startPeriod} onChange={(event) => setStartPeriod(event.target.value)} placeholder="Jan-27" /><small>Exact Data Integration period mapping name.</small></label>
          <label className="runner-field"><span>End period *</span><input value={endPeriod} onChange={(event) => setEndPeriod(event.target.value)} placeholder="Mar-27" /><small>Do not include curly braces.</small></label>
        </>}
        <label className="runner-field"><span>Import mode *</span><select value={importMode} onChange={(event) => setImportMode(event.target.value)}>{IMPORT_MODES.map((mode) => <option key={mode}>{mode}</option>)}</select></label>
        <label className="runner-field"><span>Export mode *</span><select value={exportMode} onChange={(event) => setExportMode(event.target.value)}>{EXPORT_MODES.map((mode) => <option key={mode}>{mode}</option>)}</select></label>
      </div>

      {canManageCatalog && <details className="pipeline-register">
        <summary>Data Integration not listed?</summary>
        <div className="pipeline-register__body">
          <label className="runner-field"><span>Exact Oracle Data Integration name</span><input value={registerName} onChange={(event) => setRegisterName(event.target.value)} placeholder="Revenue_Load" maxLength={250} /><small>Oracle has no safe read-only lookup for a standalone Integration. Registration stays pending until its first governed run is accepted.</small></label>
          <button type="button" className="button button--quiet" disabled={registering} onClick={() => void registerIntegration()}>{registering ? "Registering..." : "Register exact name"}</button>
        </div>
      </details>}

      <section className="integration-file-section"><header><h3>Source file</h3><p>Use the integration's configured file, upload a replacement, or choose a compatible file already in Oracle.</p></header>
        <div className="file-source-choice" role="radiogroup" aria-label="File source">
          <label className={fileSource === "configured" ? "is-selected" : ""}><input type="radio" name="file-source" checked={fileSource === "configured"} onChange={() => { setFileSource("configured"); setFile(null); setInboxFile(""); }} /><Icon name="settings" /><span><strong>Use configured file</strong><small>Run with the filename already saved in the selected integration.</small></span></label>
          <label className={fileSource === "upload" ? "is-selected" : ""}><input type="radio" name="file-source" checked={fileSource === "upload"} onChange={() => { setFileSource("upload"); setInboxFile(""); }} /><Icon name="data" /><span><strong>Upload local file</strong><small>Replace the matching Oracle Inbox file before this run.</small></span></label>
          <label className={fileSource === "inbox" ? "is-selected" : ""}><input type="radio" name="file-source" checked={fileSource === "inbox"} onChange={() => { setFileSource("inbox"); setFile(null); }} /><Icon name="automation" /><span><strong>Choose from Oracle Inbox</strong><small>Select a compatible live file without typing its reference.</small></span></label>
        </div>
        {fileSource === "upload" && <label className={`integration-upload${file ? " has-file" : ""}`}><input type="file" accept=".csv,.txt,.zip,.dat" aria-label="Local data file" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><Icon name={file ? "check" : "data"} /><span><strong>{file ? file.name : "Choose a data file"}</strong><small>{file ? formatFileSize(file.size) : "CSV, TXT, ZIP, or DAT"}</small></span><em>{file ? "Change file" : "Browse"}</em></label>}
        {fileSource === "inbox" && <OracleFilePicker purpose="data-integration" value={inboxFile} onChange={setInboxFile} label="Existing Oracle integration file" />}
        {fileSource === "configured" && <div className="configured-file"><Icon name="check" /><span><strong>Saved Oracle integration configuration</strong><small>No runtime filename override will be supplied.</small></span></div>}
      </section>
      <footer className="runner-actions"><button type="button" className="button button--quiet" onClick={onBack}>Cancel</button><button className="button button--primary" disabled={loadingCatalog || oracleAvailable === false || (!selectableIntegrations.length && !sessionPendingIntegration)}>Review execution <Icon name="arrow" /></button></footer>
    </form><IntegrationGuidance /></div>}

    {step === "REVIEW" && reviewed && <section className="panel runner-review"><div className="panel-heading"><span className="eyebrow">Step 2</span><h2>Review the Data Integration</h2><p>Nothing has been uploaded or submitted yet. Confirm the file, period range, and Oracle modes.</p></div>
      <div className="review-target"><span className="service-card__icon"><Icon name="data" /></span><div><small>Data Integration</small><strong>{reviewed.payload.integration_name}</strong></div></div>
      <dl className="review-summary integration-review-summary"><div><dt>Period range</dt><dd>{reviewed.payload.start_period} to {reviewed.payload.end_period}</dd></div><div><dt>Import mode</dt><dd>{reviewed.payload.import_mode}</dd></div><div><dt>Export mode</dt><dd>{reviewed.payload.export_mode}</dd></div><div><dt>Source file</dt><dd>{reviewed.file?.name || reviewed.inboxFile || "Configured in Oracle integration"}</dd></div></dl>
      {reviewed.file && <div className="runner-warning integration-replace-notice"><Icon name="alert" /><div><strong>Existing filenames are replaced safely</strong><p>If {reviewed.file.name} already exists in the Oracle Inbox, the framework replaces it before starting this load.</p></div></div>}
      <label className="runner-approval"><input type="checkbox" checked={approved} onChange={(event) => setApproved(event.target.checked)} /><span><strong>I reviewed the integration, periods, modes, and file</strong><small>The approved data load will be submitted to the connected Oracle environment.</small></span></label>
      <footer className="runner-actions"><button className="button button--quiet" disabled={starting} onClick={() => { setError(null); setStep("SETUP"); }}>Back to inputs</button><button className="button button--primary" disabled={!approved || starting} onClick={() => void start()}>{starting ? <><span className="spinner" /> Uploading and starting...</> : <>Start Data Integration <Icon name="arrow" /></>}</button></footer>
    </section>}

    {step === "RUNNING" && <ExecutionStep execution={execution} operation={operation} />}
    {step === "RESULT" && execution && <ResultStep execution={execution} onAgain={runAgain} onBack={onBack} />}
  </section>;
}

function IntegrationGuidance() {
  return <aside className="panel runner-guidance"><span className="eyebrow">File guidance</span><h2>Use the correct Oracle location</h2><ol><li><span>1</span><div><strong>Local upload</strong><p>The current file is staged securely and then uploaded to Applications Inbox.</p></div></li><li><span>2</span><div><strong>Existing file</strong><p>The entered Oracle reference is preserved exactly for execution.</p></div></li><li><span>3</span><div><strong>Oracle configuration</strong><p>Import format, mappings, target cube, and multi-column behavior remain owned by the selected integration.</p></div></li></ol></aside>;
}

function IntegrationCatalogHealth({ artifacts, canManage, syncing, notice, onSync }: {
  artifacts: OracleArtifactRegistration[];
  canManage: boolean;
  syncing: boolean;
  notice: string | null;
  onSync: () => void;
}) {
  const verified = artifacts.filter((item) => item.is_verified).length;
  const pending = artifacts.filter((item) => item.is_active && item.status === "PENDING").length;
  const unavailable = artifacts.filter((item) => item.status === "MISSING" || item.status === "INACTIVE");
  return <section className="catalog-health" aria-label="Data Integration catalog status">
    <div className="catalog-health__summary">
      <span className="definition-badge"><Icon name="check" /> {verified} verified</span>
      {pending > 0 && <span className="catalog-health__pending">{pending} pending first run</span>}
      {unavailable.length > 0 && <span className="catalog-health__attention"><Icon name="alert" /> {unavailable.length} unavailable</span>}
      {canManage && <button type="button" className="button button--quiet" disabled={syncing} onClick={onSync}>{syncing ? <><span className="spinner" /> Syncing Oracle...</> : <><Icon name="refresh" /> Sync Pipelines &amp; Integrations</>}</button>}
    </div>
    {notice && <p className="catalog-health__notice">{notice}</p>}
    {canManage && unavailable.length > 0 && <details><summary>Review unavailable registrations</summary><ul>{unavailable.map((item) => <li key={item.artifact_id}><span><strong>{item.display_name}</strong><small>{item.last_error || "Not available in the connected application"}</small></span><em className={`catalog-status catalog-status--${item.status.toLowerCase()}`}>{item.status.toLowerCase()}</em></li>)}</ul></details>}
  </section>;
}

function RunnerSteps({ current }: { current: RunnerStep }) {
  const steps: [RunnerStep, string, string][] = [["SETUP", "Prepare", "Periods and file"], ["REVIEW", "Review", "Confirm scope"], ["RUNNING", "Run", "Monitor Oracle"], ["RESULT", "Result", "Review outcome"]];
  const currentIndex = steps.findIndex(([value]) => value === current);
  return <ol className="runner-steps" aria-label="Operation progress">{steps.map(([value, label, description], index) => <li className={index === currentIndex ? "is-current" : index < currentIndex ? "is-complete" : ""} key={value}><span>{index < currentIndex ? <Icon name="check" /> : index + 1}</span><div><strong>{label}</strong><small>{description}</small></div></li>)}</ol>;
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function buildPeriodRange(
  year: string,
  startMonth: string,
  endMonth: string,
  mode: Exclude<PeriodInputMode, "exact">
) {
  const yearSuffix = year.trim().toUpperCase().replace(/^FY/, "");
  if (
    !/^\d{2}$/.test(yearSuffix)
    || !MONTHS.includes(startMonth)
    || !MONTHS.includes(endMonth)
  ) {
    return { start: "", end: "" };
  }
  if (mode === "planning") {
    return {
      start: `${startMonth}#FY${yearSuffix}`,
      end: `${endMonth}#FY${yearSuffix}`,
    };
  }
  return {
    start: `${startMonth}-${yearSuffix}`,
    end: `${endMonth}-${yearSuffix}`,
  };
}

function errorMessage(reason: unknown) {
  return reason instanceof Error ? reason.message : "The Data Integration could not be completed.";
}
